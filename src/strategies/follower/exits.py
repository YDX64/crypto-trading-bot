"""Takipçi çıkışları — TP1 → break-even, TP2/TP3 telemetri, kapanış defteri.

``ExitManager``'dan TÜRETİLİR: kapanış doğrulama merdiveni (income →
userTrades ledger → tahmini brüt), ``_confirmed_algo_fill`` fill kanıtı,
``_handle_closed`` tek-finalizer kilidi ve MAE/MFE takibi YENİDEN YAZILMAZ.

Scalper'dan FARKLAR (kullanıcı kararı — "çıkışı AlgoPro söyler"):
  * Chandelier trailing YOKTUR. ``_update_trailing`` hiç çağrılmaz; koşucu
    kavramı yoktur, üçüncü parça TP3'e kadar taşınır.
  * TP1 doğrulanınca SL ücret-farkında break-even'e çekilir (scalper ile AYNI
    ``fee_aware_breakeven_price`` seviyesi ve AYNI boşluksuz
    ``pm.replace_stop_loss`` deseni).
  * TP2/TP3 dolumları yalnız telemetridir (SL taşınmaz; TP3 zaten pozisyonu
    kapatır).
  * Cooldown her çıkışta başlar (yalnız kayıpta değil) — takipçi aynı sembole
    saniyeler içinde yeniden girmemeli.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Callable, Dict, List, Optional

from src.strategies.follower.executor import FOLLOWER_STRATEGY, FollowerPosition
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.executor import ScalpPosition
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Direction,
    ExitPlan,
    Regime,
    ScalpSignal,
    fee_aware_breakeven_price,
)
from src.models.position import PositionModel, PositionSide, PositionStatus
from src.trading.binance_client_improved import BinanceAPIError, ImprovedBinanceClient
from src.trading.position_manager import PositionManager, UnprotectedPositionError


async def _no_klines(*_args: Any, **_kwargs: Any) -> List[Any]:
    """Takipçide mum verisi ÇEKİLMEZ (trailing yok) — ExitManager sözleşmesi
    bir fetcher beklediği için boş liste döndüren açık bir yer tutucu."""
    return []


class FollowerExitManager(ExitManager):
    """AlgoPro takipçi pozisyonlarının çıkış yöneticisi."""

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
        exit_cooldown_cb: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(
            client, pm, tracker, cfg, _no_klines, loss_cooldown_cb=exit_cooldown_cb
        )

    # ------------------------------------------------------------------
    # Cooldown: takipçide HER çıkış cooldown başlatır
    # ------------------------------------------------------------------

    def _maybe_start_loss_cooldown(
        self,
        symbol: str,
        exit_reason: str,
        realized_pnl: float,
        loss_threshold: float = 0.0,
    ) -> None:
        """Kâr/zarar ayrımı YAPMADAN cooldown başlat (scalper'da yalnız kayıp).

        Gerekçe: takipçi 1 dakikalık sinyalleri izler; bir çıkışın hemen
        ardından gelen aynı yönlü sinyal (ör. TP3'ten 5 sn sonra yeni BUY)
        pratikte aynı hareketin devamıdır ve slot israfıdır.
        """
        if self._loss_cooldown_cb is None:
            return
        try:
            self._loss_cooldown_cb(symbol)
        except Exception as exc:
            self.logger.error(f"⚠️ {symbol}: takipçi cooldown'u başlatılamadı ({exc})")

    # ------------------------------------------------------------------
    # Tur adımı
    # ------------------------------------------------------------------

    async def _step_one(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            pos_info = await self.client.get_position_risk(symbol)
        except BinanceAPIError as exc:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgulanamadı (kod={exc.code}: {exc.msg}). "
                f"İzleme sürüyor — 'bilinmiyor' 'kapandı' sayılmaz."
            )
            return
        except Exception as exc:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgusunda beklenmeyen hata ({exc}). "
                f"İzleme sürüyor."
            )
            return

        # Eşzamanlı finalize: bu await sırasında başka bir yol (AlgoPro exit /
        # risk-olayı flatten) pozisyonu bitirmiş olabilir.
        if self._positions.get(symbol) is not sp:
            return

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0
        if amt == 0:
            await self._handle_closed(symbol, sp)
            return

        try:
            current_price = await self.client.get_current_price(symbol)
        except Exception as exc:
            current_price = None
            self.logger.debug(f"{symbol}: güncel fiyat alınamadı ({exc})")
        if current_price:
            self._update_mae_mfe(sp, current_price)
            sp.position.current_price = current_price

        if not sp.tp1_done:
            await self._check_tp1_breakeven(symbol, sp, amt)
        if sp.tp1_done and not sp.tp2_done:
            await self._check_tp_telemetry(symbol, sp, amt, index=2)
        if sp.tp2_done and not getattr(sp, "tp3_done", False):
            await self._check_tp_telemetry(symbol, sp, amt, index=3)

    async def _check_tp1_breakeven(
        self, symbol: str, sp: ScalpPosition, live_qty: float
    ) -> None:
        """TP1 GERÇEK fill ile kanıtlanınca SL'yi ücret-dahil BE'ye çek."""
        filled = sp.position.quantity
        expected = float(getattr(sp.plan, "tp1_quantity", 0.0) or 0.0)
        if filled <= 0 or expected <= 0:
            return
        # Miktar azalması yalnız pahalı sorguyu erteleyen bir İPUCUDUR;
        # fill kanıtı _confirmed_algo_fill'dir (scalper ile aynı ilke).
        if live_qty > filled - expected * 0.9:
            return
        if not await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=getattr(sp.plan, "tp1_algo_id", None),
            expected_quantity=expected,
            label="TP1",
        ):
            self.logger.warning(
                f"⚠️ {symbol}: miktar azaldı ancak TP1 algo fill'i doğrulanamadı; "
                f"salt miktar azalması break-even tetiklemeyecek"
            )
            return

        target = sp.plan.breakeven_price
        current_sl = sp.position.current_stoploss
        already_tighter = self._is_at_least_as_protective(
            sp.signal.direction, current_sl, target
        )
        ok = already_tighter or await self.pm.replace_stop_loss(sp.position, target)
        if not ok:
            self.logger.warning(
                f"⚠️ {symbol}: SL break-even'e taşınamadı, eski SL korunuyor. "
                f"Sonraki turda tekrar denenecek."
            )
            return
        sp.tp1_done = True
        if not already_tighter:
            sp.position.current_stoploss = target
        self.logger.info(
            f"✅ {symbol}: TP1 doğrulandı — ücret-dahil break-even aktif "
            f"(SL={sp.position.current_stoploss})",
            extra={"trade": True},
        )

    async def _check_tp_telemetry(
        self, symbol: str, sp: ScalpPosition, live_qty: float, *, index: int
    ) -> None:
        """TP2/TP3 dolumunu doğrula ve İŞARETLE (SL taşınmaz).

        Borsada dolum kanıtlanamıyorsa hiçbir durum değişmez — takipçinin
        çıkışı zaten AlgoPro'nun ``TPn HIT`` olayıyla çapraz doğrulanır
        (bkz. engine._handle_tp_event).
        """
        expected = float(
            getattr(sp.plan, "tp2_quantity" if index == 2 else "tp3_quantity", 0.0)
            or 0.0
        )
        algo_id = getattr(
            sp.plan, "tp2_algo_id" if index == 2 else "tp3_algo_id", None
        )
        filled = sp.position.quantity
        if filled <= 0 or expected <= 0:
            return
        consumed = float(getattr(sp.plan, "tp1_quantity", 0.0) or 0.0)
        if index == 3:
            consumed += float(getattr(sp.plan, "tp2_quantity", 0.0) or 0.0)
        if live_qty > filled - (consumed + expected * 0.9):
            return
        if not await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=algo_id,
            expected_quantity=expected,
            label=f"TP{index}",
        ):
            return
        if index == 2:
            sp.tp2_done = True
        else:
            setattr(sp, "tp3_done", True)
        self.logger.info(
            f"🎯 {symbol}: TP{index} gerçek fill ile doğrulandı (kalan={live_qty})",
            extra={"trade": True},
        )

    # ------------------------------------------------------------------
    # Restart kurtarma
    # ------------------------------------------------------------------

    async def _recover_one(self, trade) -> bool:
        """Açık takipçi işlemini borsadaki gerçek pozisyonla eşleştir.

        Scalper'ın ``_recover_one``'ı TP fiyatlarını cfg ROI'lerinden yeniden
        üretir; takipçide seviyeler AlgoPro'nundur ve DB'de saklanmaz — bu
        yüzden CANLI algo emirlerinden okunur. Okunamayan TP fiyatı 0.0 kalır
        (yalnız log/telemetri değeri vardır; BE ve kapanış defteri miktar ve
        algoId üzerinden çalışır).
        """
        symbol = trade.symbol
        try:
            pos_info = await self.client.get_position_risk(symbol, force_fresh=True)
        except Exception as exc:
            self.logger.error(
                f"⚠️ recover(): {symbol} pozisyon durumu sorgulanamadı ({exc}), "
                f"#{trade.id} bu turda atlanıyor"
            )
            return False

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0
        if amt <= 0:
            self.logger.warning(
                f"⚠️ recover(): {symbol} borsada açık pozisyon yok ama DB'de "
                f"#{trade.id} OPEN görünüyor — UNKNOWN ile kapatılıyor"
            )
            return await self._record_recovery_estimate(
                trade, notes="recovery=no_live_position"
            )

        direction = Direction(trade.direction)
        leverage = trade.leverage

        try:
            algo_orders = await self.client.get_open_algo_orders(symbol)
        except Exception as exc:
            self.logger.error(
                f"⚠️ recover(): {symbol} koşullu emirler okunamadı ({exc}); "
                f"koruma durumu belirsiz, readiness başarısız"
            )
            return False

        live_stop = self._live_stop_order(algo_orders, direction)
        live_sl_algo_id = live_stop[0] if live_stop is not None else None
        current_stop = live_stop[1] if live_stop is not None else None
        if current_stop is None:
            self.logger.critical(
                f"🚨 recover(): {symbol} borsada açık ama canlı STOP yok. "
                f"Korumasız pozisyon acil kapatılacak.",
                extra={"trade": True},
            )
            try:
                closed = await self.pm.emergency_close(symbol)
            except UnprotectedPositionError:
                raise
            except Exception as exc:
                raise UnprotectedPositionError(
                    f"{symbol}: restart kurtarmasında korumasız pozisyon "
                    f"kapatılamadı ({exc})"
                ) from exc
            if not closed:
                raise UnprotectedPositionError(
                    f"{symbol}: restart kurtarmasında canlı STOP yok ve acil "
                    f"kapatma başarısız"
                )
            return await self._record_recovery_estimate(
                trade, notes="recovery=missing_stop_emergency_close"
            )

        quantity = float(trade.quantity)
        third = quantity / 3.0
        tp_prices = self._live_tp_prices(algo_orders, direction, trade.entry_price)

        tp1_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=trade.tp1_algo_id,
            expected_quantity=third,
            label="TP1/recovery",
        )
        tp2_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=trade.tp2_algo_id,
            expected_quantity=third,
            label="TP2/recovery",
        )
        tp3_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=getattr(trade, "tp3_algo_id", None),
            expected_quantity=third,
            label="TP3/recovery",
        )

        entry_fee_rate, exit_fee_rate, fee_rate_source = (
            await self._resolve_commission_rates(symbol)
        )
        breakeven_price = fee_aware_breakeven_price(
            entry=float(trade.entry_price),
            direction=direction,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            buffer_pct=float(getattr(self.cfg, "scalper_breakeven_buffer_pct", 0.05)),
        )

        signal = ScalpSignal(
            strategy=trade.strategy or FOLLOWER_STRATEGY,
            symbol=symbol,
            direction=direction,
            entry_price=trade.entry_price,
            stop_price=current_stop,
            reason=trade.signal_reason or "algopro:recover",
            regime=Regime.UNKNOWN,
            atr_5m=0.0,
            leverage=leverage,
        )

        position = PositionModel(
            symbol=symbol,
            side=PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT,
            leverage=leverage,
            margin_type="ISOLATED",
            entry_price=trade.entry_price,
            current_price=trade.entry_price,
            # ORİJİNAL dolum: TP eşikleri buna göre hesaplanır (canlı kalan
            # miktar her turda borsadan okunur).
            quantity=quantity,
            position_size=quantity * trade.entry_price,
            initial_stoploss=current_stop,
            current_stoploss=current_stop,
            first_tp_price=tp_prices[0],
            first_tp_quantity=third,
            targets=str(list(tp_prices)),
            status=PositionStatus.OPEN,
            entry_order_id=str(getattr(trade, "entry_order_id", "") or ""),
            sl_order_id=live_sl_algo_id or trade.sl_algo_id,
            tp_order_id=trade.tp1_algo_id,
            highest_price=trade.entry_price,
            lowest_price=trade.entry_price,
            opened_at=trade.opened_at,
            notes=f"follower:{trade.strategy}:recovered",
        )

        plan = ExitPlan(
            tp1_price=tp_prices[0],
            tp1_quantity=third,
            tp2_price=tp_prices[1],
            tp2_quantity=third,
            runner_quantity=0.0,
            initial_stop=current_stop,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=0.0,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            fee_rate_source=fee_rate_source,
            breakeven_cost_pct=abs(breakeven_price - float(trade.entry_price))
            / float(trade.entry_price)
            * 100.0,
            runner_floor_price=tp_prices[0],
            tp1_algo_id=trade.tp1_algo_id,
            tp2_algo_id=trade.tp2_algo_id,
            tp3_price=tp_prices[2],
            tp3_quantity=quantity - 2 * third,
            tp3_algo_id=getattr(trade, "tp3_algo_id", None),
        )

        entry_candle_time = 0
        if trade.opened_at:
            opened_at = trade.opened_at
            if opened_at.tzinfo is None:
                # DB'deki zamanlar naive UTC'dir (datetime.utcnow); yerel saat
                # varsayımı restart sonrası saatlerce kayma demek olurdu.
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            entry_candle_time = int(opened_at.timestamp() * 1000)

        sp = FollowerPosition(
            trade_id=trade.id,
            signal=signal,
            position=position,
            plan=plan,
            entry_candle_time=entry_candle_time,
            tp1_done=tp1_done,
            tp2_done=tp2_done,
            tp3_done=tp3_done,
            meta={"recovered": True},
        )
        self.track(sp)
        self.logger.info(
            f"♻️ recover(): {symbol} #{trade.id} takipçi izlemesine geri alındı "
            f"(canlı_miktar={amt}, tp1={tp1_done}, tp2={tp2_done}, tp3={tp3_done})",
            extra={"trade": True},
        )
        return True

    @staticmethod
    def _live_tp_prices(
        algo_orders: List[Dict[str, Any]],
        direction: Direction,
        entry_price: float,
    ) -> tuple:
        """Canlı TAKE_PROFIT emirlerinin tetik fiyatlarını girişe uzaklığa göre sırala."""
        expected_side = "SELL" if direction == Direction.LONG else "BUY"
        triggers: List[float] = []
        for order in algo_orders or []:
            order_type = order.get("orderType") or order.get("type")
            if order_type not in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
                continue
            side = str(order.get("side") or "").upper()
            if side and side != expected_side:
                continue
            raw = order.get("triggerPrice") or order.get("stopPrice")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                triggers.append(value)
        triggers.sort(key=lambda price: abs(price - float(entry_price)))
        while len(triggers) < 3:
            triggers.append(0.0)
        return tuple(triggers[:3])
