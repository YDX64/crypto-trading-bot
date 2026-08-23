"""Takipçi girişleri — korumalı açılış (MARKET → SL → 3× TP).

Scalper'ın KANITLANMIŞ güvenlik disiplini AYNEN uygulanır (yeniden yazılmaz):
  * emir öncesi borsa filtresi doğrulaması (``validate_order``),
  * ``pm.place_stop_loss_or_close`` — SL kurulamazsa pozisyon ACİL KAPATILIR
    (``UnprotectedPositionError`` yukarı taşınır, motor global latch'i kurar),
  * TP başarısızlığı pozisyonu İPTAL ETTİRMEZ (SL zaten var),
  * borsanın döndürdüğü ``effectiveStopPrice`` ile kayıt hizalanır.

FARKLAR (kullanıcı kararı):
  * Giriş DAİMA MARKET — 1m sinyal gecikmesinde maker beklemek sinyali kaçırır.
  * Çıkış 3 EŞİT PARÇA (TP1/TP2/TP3, ``TAKE_PROFIT_MARKET`` reduce-only);
    yuvarlama artığı SON parçaya gider.
  * Boyutlama: marj = sermayenin %``FOLLOWER_MARGIN_PCT``'i, kaldıraç
    volatiliteye göre dinamik (bkz. ``plan.py``).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.logger import app_logger
from src.models.position import PositionModel, PositionSide, PositionStatus
from src.strategies.follower.brackets import LeverageBracketCache
from src.strategies.follower.plan import (
    build_plan,
    split_three_quantities,
    with_exchange_quantity,
)
from src.strategies.follower.types import (
    FollowerEvent,
    FollowerLevels,
    FollowerPlan,
    FollowerRejected,
)
from src.strategies.scalper.executor import ScalpPosition
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Direction,
    ExitPlan,
    Regime,
    ScalpSignal,
    fee_aware_breakeven_price,
)
from src.trading.binance_client_improved import BinanceAPIError, ImprovedBinanceClient
from src.trading.position_manager import PositionManager

# Deftere yazılan strateji etiketi — `ledger_report.py --strategy AP`.
FOLLOWER_STRATEGY = "AP"


@dataclass
class FollowerPosition(ScalpPosition):
    """``ScalpPosition`` + üçüncü TP durumu ve boyutlama meta verisi.

    ``ExitManager``'ın kapanış defteri (``_finalize_close``) aynı alanları
    okuduğu için ScalpPosition'dan TÜRETİLİR — kapanış doğrulama merdiveni
    (income → userTrades → tahmini) yeniden yazılmadan kullanılır.
    """

    tp3_done: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)


class FollowerExecutor:
    """AlgoPro sinyalinden korumalı bir pozisyon açar."""

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
        brackets: Optional[LeverageBracketCache] = None,
    ):
        self.client = client
        self.pm = pm
        self.tracker = tracker
        self.cfg = cfg
        self.logger = app_logger
        self.brackets = brackets or LeverageBracketCache(client, cfg)
        # Sembol → cooldown bitiş epoch'u. RAM'de tutulur: varsayılan pencere
        # 60 sn, süreç yeniden başlaması (~90 sn) zaten bundan uzundur.
        self._cooldowns: Dict[str, float] = {}
        self._reject_counters: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Cooldown ve teşhis
    # ------------------------------------------------------------------

    def _cooldown_seconds(self) -> float:
        try:
            value = float(getattr(self.cfg, "follower_cooldown_sec", 60.0) or 0.0)
        except (TypeError, ValueError):
            value = 60.0
        return max(0.0, value)

    def start_cooldown(self, symbol: str) -> None:
        """Çıkıştan sonra sembolü kısa süre yeni girişe kapat."""
        seconds = self._cooldown_seconds()
        if seconds <= 0:
            return
        key = str(symbol).upper()
        expires_at = time.time() + seconds
        if self._cooldowns.get(key, 0.0) >= expires_at:
            return
        self._cooldowns[key] = expires_at
        self.logger.info(f"🧊 {key}: takipçi cooldown {seconds:.0f} sn")

    def is_entry_blocked(self, symbol: str) -> bool:
        key = str(symbol).upper()
        expires_at = self._cooldowns.get(key)
        if expires_at is None:
            return False
        if expires_at <= time.time():
            self._cooldowns.pop(key, None)
            return False
        return True

    def cooldown_snapshot(self) -> List[Dict[str, Any]]:
        now = time.time()
        rows: List[Dict[str, Any]] = []
        for symbol, expires_at in sorted(self._cooldowns.items()):
            if expires_at <= now:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "remaining_seconds": round(expires_at - now, 1),
                    "expires_at": datetime.fromtimestamp(
                        expires_at, tz=timezone.utc
                    ).isoformat(),
                }
            )
        return rows

    def count_reject(self, reason: str) -> None:
        self._reject_counters[reason] = self._reject_counters.get(reason, 0) + 1

    def reject_snapshot(self) -> Dict[str, int]:
        return dict(self._reject_counters)

    # ------------------------------------------------------------------
    # Giriş
    # ------------------------------------------------------------------

    async def open_position(
        self,
        *,
        event: FollowerEvent,
        levels: FollowerLevels,
        equity_usdt: float,
    ) -> Optional[FollowerPosition]:
        """Planı kur, borsada aç, koru ve deftere yaz.

        ``FollowerRejected``: bir kapı reddetti (emir GÖNDERİLMEDİ).
        ``None``: emir yolu başarısız oldu (loglandı; koruma kurulamadıysa
        pozisyon PositionManager tarafından kapatılmıştır).
        """
        symbol = event.symbol
        direction = event.direction
        if direction is None:
            raise FollowerRejected("Giriş olayında yön yok", code="no_direction")

        brackets = await self.brackets.get(symbol)
        try:
            filters = await self.client.get_symbol_filters(symbol)
            step_size = float(filters.get("stepSize") or 0.0)
        except Exception as exc:
            raise FollowerRejected(
                f"Borsa filtreleri okunamadı ({exc})", code="filters"
            ) from exc

        plan = build_plan(
            symbol=symbol,
            direction=direction,
            levels=levels,
            equity_usdt=equity_usdt,
            brackets=brackets,
            cfg=self.cfg,
            step_size=step_size,
        )

        try:
            quantity = await self.client.quantize_quantity(symbol, plan.quantity)
            await self.client.validate_order(symbol, quantity, levels.entry)
        except BinanceAPIError as exc:
            raise FollowerRejected(
                f"Emir doğrulanamadı (kod={exc.code}: {exc.msg})", code="validate"
            ) from exc
        except Exception as exc:
            raise FollowerRejected(
                f"Boyutlama/doğrulama hatası ({exc})", code="validate"
            ) from exc

        plan = with_exchange_quantity(plan, quantity, step_size)
        if min(plan.tp_quantities[0], plan.tp_quantities[1]) <= 0:
            raise FollowerRejected(
                f"Pozisyon 3 parçaya bölünemiyor (miktar={quantity}, "
                f"stepSize={step_size}) — giriş yapılmadı",
                code="split",
            )

        # --- Margin type + leverage (emirden ÖNCE — hata zararsız) ---
        try:
            await self.client.set_margin_type(symbol, "ISOLATED")
            await self.client.set_leverage(symbol, plan.leverage)
        except BinanceAPIError as exc:
            raise FollowerRejected(
                f"Margin/leverage ayarlanamadı (kod={exc.code}: {exc.msg})",
                code="leverage",
            ) from exc
        except Exception as exc:
            raise FollowerRejected(
                f"Margin/leverage ayarında hata ({exc})", code="leverage"
            ) from exc

        side = "BUY" if direction == Direction.LONG else "SELL"
        sl_side = "SELL" if direction == Direction.LONG else "BUY"

        # --- BU NOKTADAN SONRA pozisyon GERÇEK olabilir ---
        try:
            entry_order = await self.client.open_market_order(
                symbol=symbol, side=side, quantity=plan.quantity
            )
        except BinanceAPIError as exc:
            self.logger.error(
                f"❌ {symbol}: market emri başarısız (kod={exc.code}: {exc.msg})"
            )
            self.count_reject("market_order")
            return None
        except Exception as exc:
            self.logger.error(f"❌ {symbol}: market emrinde beklenmeyen hata ({exc})")
            self.count_reject("market_order")
            return None

        try:
            entry_price, filled_qty = await self.pm.resolve_fill(symbol, entry_order)
        except Exception as exc:
            self.logger.critical(
                f"🚨 {symbol}: dolum bilgisi hiçbir kaynaktan okunamadı ({exc}). "
                f"Pozisyon açık olabilir — acil koruma/kapatma akışı devreye giriyor.",
                extra={"trade": True},
            )
            await self.pm.place_stop_loss_or_close(
                symbol=symbol, sl_side=sl_side, stop_price=levels.stop
            )
            return None

        if filled_qty <= 0:
            self.logger.error(f"❌ {symbol}: emir dolmadı (executedQty=0), pozisyon yok")
            return None

        self.logger.info(
            f"✅ Takipçi dolum: {symbol} {filled_qty} @ {entry_price} "
            f"(lev={plan.leverage}x, marj={plan.margin_usdt:.2f} USDT)"
        )
        return await self._finalize(
            event=event,
            plan=plan,
            direction=direction,
            sl_side=sl_side,
            entry_price=float(entry_price),
            filled_qty=float(filled_qty),
            entry_order_id=str(entry_order.get("orderId") or ""),
            step_size=step_size,
        )

    async def _finalize(
        self,
        *,
        event: FollowerEvent,
        plan: FollowerPlan,
        direction: Direction,
        sl_side: str,
        entry_price: float,
        filled_qty: float,
        entry_order_id: str,
        step_size: float,
    ) -> Optional[FollowerPosition]:
        symbol = plan.symbol
        levels = plan.levels

        # --- SL: kurulamazsa pm pozisyonu ZATEN acil kapattı ---
        sl_order = await self.pm.place_stop_loss_or_close(
            symbol=symbol,
            sl_side=sl_side,
            stop_price=levels.stop,
            reference_price=entry_price,
            max_distance_pct=float(
                getattr(self.cfg, "follower_max_sl_pct", 0.0) or 0.0
            )
            or None,
        )
        if sl_order is None:
            self.logger.error(
                f"❌ {symbol}: SL konulamadı — pozisyon PositionManager tarafından kapatıldı"
            )
            self.count_reject("initial_sl_failed")
            self.start_cooldown(symbol)
            await self._record_protection_failure(
                event=event,
                plan=plan,
                entry_price=entry_price,
                filled_qty=filled_qty,
                entry_order_id=entry_order_id,
            )
            return None

        stop_price = levels.stop
        effective_stop = self._coerce_price(sl_order.get("effectiveStopPrice"))
        if effective_stop is not None and effective_stop != stop_price:
            self.logger.warning(
                f"📌 {symbol}: kayıtlı stop borsadaki etkin tetik fiyatına hizalandı "
                f"{stop_price} -> {effective_stop}"
            )
            stop_price = effective_stop

        # --- 3 parça TP (reduce-only) — GERÇEK dolum miktarından bölünür ---
        parts = split_three_quantities(filled_qty, step_size)
        tp_prices = (levels.tp1, levels.tp2, levels.tp3)
        algo_ids: List[Optional[str]] = []
        for index, (price, qty) in enumerate(zip(tp_prices, parts), start=1):
            algo_ids.append(
                await self._place_tp_safely(symbol, sl_side, price, qty, f"TP{index}")
            )

        entry_fee_rate, exit_fee_rate, fee_rate_source = await self._resolve_fee_rates(
            symbol
        )
        breakeven_price = fee_aware_breakeven_price(
            entry=entry_price,
            direction=direction,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            buffer_pct=float(getattr(self.cfg, "scalper_breakeven_buffer_pct", 0.05)),
        )

        signal = ScalpSignal(
            strategy=FOLLOWER_STRATEGY,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            reason=f"algopro:{event.kind};tf={event.timeframe};{plan.ledger_note()}"[:480],
            regime=Regime.UNKNOWN,
            atr_5m=float(levels.atr_value or 0.0),
            leverage=plan.leverage,
        )

        position = PositionModel(
            symbol=symbol,
            side=PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT,
            leverage=plan.leverage,
            margin_type="ISOLATED",
            entry_price=entry_price,
            current_price=entry_price,
            quantity=filled_qty,
            position_size=filled_qty * entry_price,
            initial_stoploss=stop_price,
            current_stoploss=stop_price,
            first_tp_price=levels.tp1,
            first_tp_quantity=parts[0],
            targets=str([levels.tp1, levels.tp2, levels.tp3]),
            status=PositionStatus.OPEN,
            entry_order_id=entry_order_id,
            sl_order_id=self._extract_id(sl_order),
            tp_order_id=algo_ids[0],
            highest_price=entry_price,
            lowest_price=entry_price,
            opened_at=datetime.utcnow(),
            notes=f"follower:{FOLLOWER_STRATEGY}",
        )

        exit_plan = ExitPlan(
            tp1_price=levels.tp1,
            tp1_quantity=parts[0],
            tp2_price=levels.tp2,
            tp2_quantity=parts[1],
            runner_quantity=0.0,
            initial_stop=stop_price,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=0.0,  # takipçide trailing YOK — çıkış AlgoPro'nun
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            fee_rate_source=fee_rate_source,
            breakeven_cost_pct=abs(breakeven_price - entry_price) / entry_price * 100.0,
            runner_floor_price=levels.tp1,
            tp1_algo_id=algo_ids[0],
            tp2_algo_id=algo_ids[1],
            tp3_price=levels.tp3,
            tp3_quantity=parts[2],
            tp3_algo_id=algo_ids[2],
        )

        margin_usdt = (filled_qty * entry_price) / plan.leverage
        try:
            trade_id = await self.tracker.record_open(
                signal=signal,
                entry_price=entry_price,
                quantity=filled_qty,
                leverage=plan.leverage,
                margin_usdt=margin_usdt,
                sl_algo_id=self._extract_id(sl_order),
                tp1_algo_id=algo_ids[0],
                tp2_algo_id=algo_ids[1],
                tp3_algo_id=algo_ids[2],
                entry_order_id=entry_order_id,
            )
        except Exception as exc:
            self.logger.critical(
                f"🚨 {symbol}: takipçi işlem kaydı DB'ye yazılamadı ({exc}). Pozisyon "
                f"borsada AÇIK ve SL korumalı ama takip kaydı yok — recover() bulmalı.",
                extra={"trade": True},
            )
            return None

        self.logger.info(
            f"✅ Takipçi pozisyon açıldı: {symbol} {direction.value} {filled_qty} @ "
            f"{entry_price} (lev={plan.leverage}x, SL={stop_price} [%{plan.sl_pct:.3f} "
            f"= marjın %{plan.sl_roi_pct:.1f}'i], TP={levels.tp1}/{levels.tp2}/{levels.tp3})",
            extra={"trade": True},
        )

        return FollowerPosition(
            trade_id=trade_id,
            signal=signal,
            position=position,
            plan=exit_plan,
            entry_candle_time=int(time.time() * 1000),
            meta={
                "plan": plan.as_dict(),
                "event": event.as_dict(),
                "opened_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _record_protection_failure(
        self,
        *,
        event: FollowerEvent,
        plan: FollowerPlan,
        entry_price: float,
        filled_qty: float,
        entry_order_id: str,
    ) -> None:
        """İlk SL kurulamayıp acil kapatılan dolumu deftere DÜŞÜR.

        PnL doğrulanmadığı için ``pnl_source=estimated_gross`` (fallback) ve
        0.0 yazılır — "bilinmiyor" ASLA "kâr" sayılmaz; gerçek tutar Binance
        income'dan elle doğrulanır (bkz. docs/RUNBOOK.md "Entry-halt").
        """
        signal = ScalpSignal(
            strategy=FOLLOWER_STRATEGY,
            symbol=plan.symbol,
            direction=plan.direction,
            entry_price=entry_price,
            stop_price=plan.levels.stop,
            reason=f"algopro:{event.kind};{plan.ledger_note()}"[:480],
            regime=Regime.UNKNOWN,
            atr_5m=0.0,
            leverage=plan.leverage,
        )
        try:
            await self.tracker.record_failed_execution(
                signal=signal,
                entry_price=entry_price,
                exit_price=entry_price,
                quantity=filled_qty,
                leverage=plan.leverage,
                realized_pnl=0.0,
                pnl_source="estimated_gross",
                entry_order_id=entry_order_id,
                notes="follower_initial_sl_failed;exit_fill=unverified",
            )
        except Exception as exc:
            self.logger.error(
                f"⚠️ {plan.symbol}: koruma hatası kaydı yazılamadı ({exc})"
            )

    async def _place_tp_safely(
        self, symbol: str, side: str, price: float, quantity: float, label: str
    ) -> Optional[str]:
        """TP koymayı dene; başarısızlık pozisyonu İPTAL ETTİRMEZ (SL var)."""
        if quantity <= 0 or price <= 0:
            self.logger.warning(
                f"⚠️ {symbol}: {label} atlandı (miktar={quantity}, fiyat={price})"
            )
            return None
        try:
            order = await self.client.place_take_profit(
                symbol=symbol, side=side, stop_price=price, quantity=quantity
            )
            return self._extract_id(order)
        except BinanceAPIError as exc:
            self.logger.error(
                f"⚠️ {symbol}: {label} konulamadı (kod={exc.code}: {exc.msg}). "
                f"Pozisyon SL ile korunuyor."
            )
            return None
        except Exception as exc:
            self.logger.error(
                f"⚠️ {symbol}: {label} konulurken beklenmeyen hata ({exc}). "
                f"Pozisyon SL ile korunuyor."
            )
            return None

    async def _resolve_fee_rates(self, symbol: str) -> Tuple[float, float, str]:
        """Gerçek komisyon oranları; okunamazsa muhafazakâr config oranı.

        Takipçi girişi DAİMA taker'dır (MARKET); çıkış da MARKET/koşullu
        emirdir — bu yüzden config fallback'ında iki bacakta da taker/maker
        oranlarının YÜKSEĞİ kullanılır (scalper ile aynı ilke).
        """
        conservative = (
            max(
                float(getattr(self.cfg, "scalper_taker_fee_pct", 0.05) or 0.0),
                float(getattr(self.cfg, "scalper_maker_fee_pct", 0.02) or 0.0),
            )
            / 100.0
        )
        getter = getattr(self.client, "get_user_commission_rate", None)
        if getter is None:
            return conservative, conservative, "config_conservative"
        try:
            raw = await getter(symbol)
            taker = float((raw or {}).get("takerCommissionRate"))
            if not math.isfinite(taker) or taker < 0 or taker >= 1:
                raise ValueError(f"geçersiz commission response: {raw!r}")
            # İki bacak da taker: giriş MARKET, çıkış MARKET/koşullu emir.
            return taker, taker, "binance_user_commission"
        except Exception as exc:
            self.logger.warning(
                f"⚠️ {symbol}: gerçek komisyon okunamadı ({exc}); "
                f"muhafazakâr fallback={conservative:.8f}"
            )
        return conservative, conservative, "config_conservative"

    @staticmethod
    def _coerce_price(value: Any) -> Optional[float]:
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        return price if price > 0 else None

    @staticmethod
    def _extract_id(order: Any) -> Optional[str]:
        if not isinstance(order, dict):
            return None
        value = order.get("algoId") or order.get("orderId")
        return str(value) if value is not None else None
