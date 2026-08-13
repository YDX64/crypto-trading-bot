"""
ExitManager — scalper pozisyonları için TP dolum takibi, break-even geçişi ve
chandelier trailing döngüsü.

GÜVENLİK İLKESİ (bugünkü onarımlarla birebir): "bilinmiyor" ASLA "kapandı"
sayılmaz. Pozisyon durumu sorgulanamazsa izleme BIRAKILMAZ, o tur atlanır.
SL değişimleri PositionManager'ın boşluksuz deseniyle yapılır (önce yeni
reduceOnly SL, sonra eskisi iptal) — pm.replace_stop_loss sarmalayıcısı
üzerinden.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from src.core.logger import app_logger
from src.models.position import PositionModel, PositionStatus, PositionSide
from src.strategies.scalper.executor import ScalpPosition
from src.strategies.scalper.indicators import chandelier_stop
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Candle,
    Direction,
    ExitPlan,
    Regime,
    ScalpSignal,
    fee_aware_breakeven_price,
    price_at_roi,
)
from src.trading.binance_client_improved import (
    ImprovedBinanceClient,
    BinanceAPIError,
)
from src.trading.position_manager import PositionManager, UnprotectedPositionError

KlineFetch = Callable[[str, str, int], Awaitable[List[Candle]]]


class ExitManager:
    """Açık scalper pozisyonlarını izler ve çıkış merdivenini yönetir."""

    # Income history, gerçekleşen çıkıştan birkaç yüz milisaniye sonra
    # tamamlanabilir. Tüm bekleme kesin olarak sınırlıdır; testler bu tuple'ı
    # örnek üzerinde (0.0,) yaparak gerçek uyku olmadan çalışır.
    INCOME_RETRY_DELAYS = (0.0, 0.5, 1.0, 2.0)
    # Binance order updateTime milisaniye hassasiyetindedir. Aynı dolumun
    # komisyonunu kaçırmamak için yalnızca çok küçük bir güvenlik payı bırakılır.
    INCOME_ENTRY_LOOKBACK_SECONDS = 5
    NET_INCOME_TYPES = frozenset({"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"})

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
        kline_fetch: KlineFetch,
        loss_cooldown_cb: Optional[Callable[[str], None]] = None,
    ):
        self.client = client
        self.pm = pm
        self.tracker = tracker
        self.cfg = cfg
        self.kline_fetch = kline_fetch
        self.logger = app_logger
        self._positions: Dict[str, ScalpPosition] = {}
        # SL/negatif kapanışta executor'ın sembol cooldown'unu başlatır.
        # Opsiyonel: verilmezse (eski kurulum/testler) davranış değişmez.
        self._loss_cooldown_cb = loss_cooldown_cb

    def _maybe_start_loss_cooldown(
        self,
        symbol: str,
        exit_reason: str,
        realized_pnl: float,
        loss_threshold: float = 0.0,
    ) -> None:
        """Kayıplı kapanışta sembol cooldown'u tetikle; kapanış yolunu asla bozma.

        ``loss_threshold`` doğrulanmış NET PnL için 0'dır. PnL kaynağı BRÜT
        tahminse (estimated_gross) çağıran, tahmini gidiş-dönüş komisyonunu
        eşik olarak geçer: brüt küçük artı görünen ama net eksi olan kapanışlar
        da cooldown başlatır — backtest'in NET pnl<0 kuralıyla parite korunur.
        """
        if self._loss_cooldown_cb is None:
            return
        if exit_reason != "SL" and realized_pnl >= loss_threshold:
            return
        try:
            self._loss_cooldown_cb(symbol)
        except Exception as e:
            self.logger.error(f"⚠️ {symbol}: kayıp cooldown'u başlatılamadı ({e})")

    def _estimated_roundtrip_fee(
        self, entry_price: float, exit_price: float, quantity: float
    ) -> float:
        """Muhafazakâr (taker) iki bacak komisyon tahmini — brüt PnL eşiği için."""
        try:
            rate = max(
                float(getattr(self.cfg, "scalper_taker_fee_pct", 0.05) or 0.05),
                float(getattr(self.cfg, "scalper_maker_fee_pct", 0.02) or 0.02),
            ) / 100.0
        except (TypeError, ValueError):
            rate = 0.0005
        return (abs(entry_price) + abs(exit_price)) * abs(quantity) * rate

    def track(self, sp: ScalpPosition) -> None:
        """Pozisyonu izleme listesine ekle (sembol anahtarlı)."""
        self._positions[sp.position.symbol] = sp

    def tracked_symbols(self) -> Set[str]:
        return set(self._positions.keys())

    # ------------------------------------------------------------------
    # Ana döngü adımı
    # ------------------------------------------------------------------

    async def step(self) -> None:
        """Engine her turda çağırır: her izlenen sembol için bir adım işlet."""
        for symbol in list(self._positions.keys()):
            sp = self._positions.get(symbol)
            if sp is None:
                continue
            await self._step_one(symbol, sp)

    async def _step_one(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            pos_info = await self.client.get_position_risk(symbol)
        except BinanceAPIError as e:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgulanamadı (kod={e.code}: {e.msg}). "
                f"İzleme sürüyor — 'bilinmiyor' 'kapandı' sayılmaz."
            )
            return
        except Exception as e:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgusunda beklenmeyen hata ({e}). İzleme sürüyor."
            )
            return

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0

        if amt == 0:
            await self._handle_closed(symbol, sp)
            return

        # MAE/MFE güncelle (mark/güncel fiyat ile)
        try:
            current_price = await self.client.get_current_price(symbol)
        except Exception as e:
            current_price = None
            self.logger.debug(f"{symbol}: güncel fiyat alınamadı ({e}), MAE/MFE bu turda güncellenmiyor")
        if current_price:
            self._update_mae_mfe(sp, current_price)
            sp.position.current_price = current_price

        # TP1 dolum kontrolü → break-even
        if not sp.tp1_done:
            await self._check_tp1(symbol, sp, amt)

        # TP2 yalnız gerçek algo fill + gerçek futures trade satırlarıyla
        # doğrulanır; ardından runner tabanı sabit TP1 fiyatına yükseltilir.
        if not sp.tp2_done:
            await self._check_tp2(symbol, sp, amt)

        # Chandelier trailing
        if sp.trailing_active:
            await self._update_trailing(symbol, sp)

    async def _check_tp1(self, symbol: str, sp: ScalpPosition, live_qty: float) -> None:
        filled = sp.position.quantity
        if filled <= 0:
            return
        tp1_fraction = self.cfg.scalper_tp1_fraction
        threshold = filled * (1 - tp1_fraction * 0.9)

        if live_qty > threshold:
            return  # TP1 henüz dolmadı

        if not await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=sp.plan.tp1_algo_id,
            expected_quantity=sp.plan.tp1_quantity,
            label="TP1",
        ):
            self.logger.warning(
                f"⚠️ {symbol}: miktar azaldı ancak TP1 algo fill'i doğrulanamadı; "
                "salt quantity reduction break-even tetiklemeyecek"
            )
            return

        self.logger.info(
            f"🎯 {symbol}: TP1 gerçek fill ile doğrulandı (kalan={live_qty}) — "
            f"SL break-even'e taşınıyor"
        )
        current_sl = sp.position.current_stoploss
        target = sp.plan.breakeven_price
        already_tighter = self._is_at_least_as_protective(
            sp.signal.direction, current_sl, target
        )
        ok = already_tighter or await self.pm.replace_stop_loss(sp.position, target)
        if ok:
            sp.tp1_done = True
            sp.trailing_active = True
            if not already_tighter:
                sp.position.current_stoploss = target
            self.logger.info(
                f"✅ {symbol}: ücret-dahil break-even aktif, "
                f"SL={sp.position.current_stoploss}"
            )
        else:
            self.logger.warning(
                f"⚠️ {symbol}: SL break-even'e taşınamadı, eski SL korunuyor. "
                f"Sonraki turda tekrar denenecek."
            )

    async def _check_tp2(self, symbol: str, sp: ScalpPosition, live_qty: float) -> None:
        filled = sp.position.quantity
        expected = sp.plan.tp2_quantity
        if filled <= 0 or expected <= 0:
            return
        # Bu eşik yalnız pahalı signed sorguyu erteleyen bir ipucudur; fill
        # kanıtı değildir. TP1/manual reduction tek başına state değiştiremez.
        reduction_hint = (
            self.cfg.scalper_tp1_fraction * 0.9
            + self.cfg.scalper_tp2_fraction * 0.9
        )
        if live_qty > filled * (1 - reduction_hint):
            return
        if not await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=sp.plan.tp2_algo_id,
            expected_quantity=expected,
            label="TP2",
        ):
            return

        floor = sp.plan.runner_floor_price or sp.plan.tp1_price
        current_sl = sp.position.current_stoploss
        already_tighter = self._is_at_least_as_protective(
            sp.signal.direction, current_sl, floor
        )
        ok = already_tighter or await self.pm.replace_stop_loss(sp.position, floor)
        if not ok:
            self.logger.warning(
                f"⚠️ {symbol}: TP2 doğrulandı ama runner stopu TP1 tabanına "
                "yükseltilemedi; eski SL korunuyor, sonraki tur tekrar denenecek"
            )
            return

        sp.tp2_done = True
        sp.trailing_active = True
        if not already_tighter:
            sp.position.current_stoploss = floor
        self.logger.info(
            f"✅ {symbol}: TP2 gerçek fill doğrulandı; runner sabit tabanı "
            f"TP1={floor} (aktif SL={sp.position.current_stoploss})"
        )

    @staticmethod
    def _is_at_least_as_protective(
        direction: Direction,
        current_stop: Optional[float],
        candidate: float,
    ) -> bool:
        """True ise candidate'a geçmek stopu sıkılaştırmaz (gevşetme yasak)."""
        if current_stop is None or current_stop <= 0:
            return False
        if direction == Direction.LONG:
            return current_stop >= candidate
        return current_stop <= candidate

    async def _confirmed_algo_fill(
        self,
        *,
        symbol: str,
        algo_id: Optional[str],
        expected_quantity: float,
        label: str,
    ) -> bool:
        """Algo emrinin gerçek child order fill'ini account trades ile kanıtla."""
        if not algo_id or expected_quantity <= 0:
            return False
        algo_getter = getattr(self.client, "get_algo_order", None)
        trades_getter = getattr(self.client, "get_account_trades", None)
        if algo_getter is None or trades_getter is None:
            return False
        try:
            algo = await algo_getter(algo_id=int(algo_id))
            actual_order_id = (algo or {}).get("actualOrderId")
            if actual_order_id in (None, "", 0, "0"):
                return False
            numeric_actual_id = int(actual_order_id)
            try:
                algo_quantity = float(
                    (algo or {}).get("quantity")
                    or (algo or {}).get("origQty")
                    or expected_quantity
                )
            except (TypeError, ValueError):
                return False
            if not math.isfinite(algo_quantity) or algo_quantity <= 0:
                return False
            rows = await trades_getter(
                symbol,
                order_id=numeric_actual_id,
                limit=500,
            )
        except Exception as exc:
            self.logger.warning(
                f"⚠️ {symbol}: {label} gerçek fill sorgusu başarısız ({exc})"
            )
            return False
        if not isinstance(rows, list) or not rows:
            return False

        total_qty = 0.0
        for row in rows:
            if not isinstance(row, dict):
                return False
            try:
                if int(row.get("orderId")) != numeric_actual_id:
                    return False
                qty = float(row.get("qty") or row.get("quantity") or 0.0)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(qty) or qty <= 0:
                return False
            total_qty += qty

        tolerance = max(1e-12, algo_quantity * 1e-6)
        if total_qty + tolerance < algo_quantity:
            return False
        # Beklenen miktarı aşan child fill başka bir emre ait olamaz; algo
        # miktarı ile uyumsuzsa state'i yine fail-closed bırak.
        if total_qty > algo_quantity + tolerance:
            self.logger.warning(
                f"⚠️ {symbol}: {label} fill miktarı beklenenden büyük "
                f"({total_qty} > {algo_quantity}); doğrulanmadı"
            )
            return False
        return True

    async def _update_trailing(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            candles = await self.kline_fetch(
                symbol, str(getattr(self.cfg, "scalper_tf_entry", "5m") or "5m"), 200
            )
        except Exception as e:
            self.logger.warning(f"⚠️ {symbol}: trailing için mum verisi alınamadı ({e}), tur atlanıyor")
            return

        if not candles:
            return

        since_index = len(candles) - 1
        for i, c in enumerate(candles):
            if c.close_time > sp.entry_candle_time:
                since_index = i
                break

        direction = sp.signal.direction
        try:
            raw_stop = chandelier_stop(
                candles,
                direction=direction,
                atr_mult=self.cfg.scalper_chandelier_atr_mult,
                atr_period=self.cfg.scalper_chandelier_atr_period,
                since_index=since_index,
            )
        except Exception as e:
            self.logger.error(f"❌ {symbol}: chandelier hesaplanamadı ({e})")
            return

        if raw_stop == 0.0:
            # indicators.chandelier_stop yetersiz veride 0.0 döner — "hesaplanamadı"
            # anlamına gelir, gerçek fiyat DEĞİLDİR. Bu turda güncelleme yapılmaz.
            self.logger.debug(f"{symbol}: chandelier için yetersiz veri, trailing bu turda atlandı")
            return

        floor = self._active_runner_floor(sp)
        current_sl = sp.position.current_stoploss or floor
        if direction == Direction.LONG:
            new_stop = max(floor, raw_stop)
            should_update = new_stop > current_sl * 1.0005
        else:
            new_stop = min(floor, raw_stop)
            should_update = new_stop < current_sl * 0.9995

        if not should_update:
            return

        ok = await self.pm.replace_stop_loss(sp.position, new_stop)
        if ok:
            sp.position.current_stoploss = new_stop
            self.logger.info(f"📈 {symbol}: chandelier trailing SL güncellendi -> {new_stop}")
        else:
            self.logger.warning(f"⚠️ {symbol}: trailing SL güncellenemedi, eski SL korunuyor")

    @staticmethod
    def _active_runner_floor(sp: ScalpPosition) -> float:
        """BE tabanı; TP2 gerçek fill sonrası en az TP1 seviyesine yükselir."""
        breakeven = sp.plan.breakeven_price
        if not sp.tp2_done:
            return breakeven
        runner_floor = sp.plan.runner_floor_price or sp.plan.tp1_price
        if sp.signal.direction == Direction.LONG:
            return max(breakeven, runner_floor)
        return min(breakeven, runner_floor)

    async def _handle_closed(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            await self.client.cancel_all_open_orders(symbol)
        except Exception as e:
            self.logger.warning(f"⚠️ {symbol}: artık emirler temizlenemedi ({e})")

        exit_price: Optional[float] = None
        try:
            exit_price = await self.client.get_current_price(symbol)
        except Exception:
            pass
        if not exit_price:
            exit_price = sp.position.current_price or sp.position.entry_price

        direction = sp.signal.direction
        entry = sp.position.entry_price
        qty = sp.position.quantity
        estimated_gross = self._estimate_gross_pnl(direction, entry, exit_price, qty)
        income_net = await self._fetch_net_income(
            symbol=symbol,
            opened_at=sp.position.opened_at,
            entry_order_id=sp.position.entry_order_id,
        )
        if income_net is None:
            realized_pnl = estimated_gross
            pnl_source = "estimated_gross"
        else:
            realized_pnl = income_net
            pnl_source = "binance_income_net"

        exit_reason = self._infer_exit_reason(sp, exit_price)

        try:
            await self.tracker.record_close(
                trade_id=sp.trade_id,
                exit_price=exit_price,
                realized_pnl=realized_pnl,
                exit_reason=exit_reason,
                mae_pct=sp.mae_pct,
                mfe_pct=sp.mfe_pct,
                pnl_source=pnl_source,
            )
        except Exception as e:
            self.logger.error(f"❌ {symbol}: kapanış kaydı yazılamadı (#{sp.trade_id}): {e}")

        loss_threshold = (
            0.0
            if pnl_source == "binance_income_net"
            else self._estimated_roundtrip_fee(entry, exit_price, qty)
        )
        self._maybe_start_loss_cooldown(
            symbol, exit_reason, realized_pnl, loss_threshold
        )

        self.logger.info(
            f"🏁 Scalp pozisyon kapandı: {symbol} PNL={realized_pnl:.2f} "
            f"kaynak={pnl_source} neden={exit_reason}",
            extra={"trade": True},
        )
        self._positions.pop(symbol, None)

    @staticmethod
    def _estimate_gross_pnl(
        direction: Direction,
        entry_price: float,
        exit_price: float,
        quantity: float,
    ) -> float:
        if direction == Direction.LONG:
            return (exit_price - entry_price) * quantity
        return (entry_price - exit_price) * quantity

    async def _resolve_commission_rates(self, symbol: str) -> tuple[float, float, str]:
        """Recovery için executor ile aynı gerçek-fee/fallback sözleşmesi."""
        maker_cfg = max(
            0.0, float(getattr(self.cfg, "scalper_maker_fee_pct", 0.02))
        ) / 100.0
        taker_cfg = max(
            0.0, float(getattr(self.cfg, "scalper_taker_fee_pct", 0.05))
        ) / 100.0
        fallback = max(maker_cfg, taker_cfg)
        getter = getattr(self.client, "get_user_commission_rate", None)
        if getter is None:
            return fallback, fallback, "config_conservative"
        try:
            raw = await getter(symbol)
            maker = float((raw or {}).get("makerCommissionRate"))
            taker = float((raw or {}).get("takerCommissionRate"))
            if (
                not math.isfinite(maker)
                or not math.isfinite(taker)
                or maker < 0
                or taker < 0
                or maker >= 1
                or taker >= 1
            ):
                raise ValueError(f"geçersiz commission response: {raw!r}")
            entry = maker if getattr(self.cfg, "scalper_entry_mode", "taker") == "maker" else taker
            return entry, taker, "binance_user_commission"
        except Exception as exc:
            self.logger.warning(
                f"⚠️ {symbol}: recovery gerçek komisyonu okunamadı ({exc}); "
                f"muhafazakâr fallback={fallback:.8f}"
            )
            return fallback, fallback, "config_conservative"

    async def _fetch_net_income(
        self,
        symbol: str,
        opened_at: Optional[datetime],
        entry_order_id: Optional[str],
    ) -> Optional[float]:
        """Pozisyon penceresindeki signed Binance gelirlerini net PnL yap.

        ``None``, doğrulama yapılamadığını anlatır; sıfır ise borsadan
        doğrulanmış gerçek bir sonuçtur. Her başarılı yanıtta yalnız son
        snapshot kullanılır; retry yanıtları birbirine eklenmez ve dolayısıyla
        aynı gelir kaydı iki kez sayılmaz.
        """
        start_time_ms = await self._income_window_start_ms(
            symbol=symbol,
            opened_at=opened_at,
            entry_order_id=entry_order_id,
        )
        if start_time_ms is None:
            self.logger.warning(
                f"⚠️ {symbol}: kesin giriş-emir zamanı doğrulanamadı; aynı-sembol income "
                f"bulaşmasını önlemek için brüt tahmine düşülüyor"
            )
            return None

        latest_values: Optional[List[float]] = None
        latest_fingerprint: Optional[tuple] = None
        last_error: Optional[Exception] = None

        for delay in self.INCOME_RETRY_DELAYS:
            if delay:
                await asyncio.sleep(delay)
            end_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 1000
            try:
                rows = await self.client.get_income_history(
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                    symbol=symbol,
                    limit=1000,
                )
            except Exception as exc:
                last_error = exc
                continue

            if not isinstance(rows, list):
                last_error = TypeError(f"income history list bekleniyordu, {type(rows).__name__} geldi")
                continue

            parsed: List[tuple] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("symbol") and row.get("symbol") != symbol:
                    continue
                income_type = str(row.get("incomeType") or "")
                if income_type not in self.NET_INCOME_TYPES:
                    continue
                raw_row_time = row.get("time")
                if raw_row_time not in (None, ""):
                    try:
                        row_time_ms = int(raw_row_time)
                    except (TypeError, ValueError):
                        continue
                    if row_time_ms < start_time_ms or row_time_ms > end_time_ms:
                        continue
                try:
                    income = float(row.get("income"))
                except (TypeError, ValueError):
                    continue
                parsed.append(
                    (
                        str(row.get("tranId") or ""),
                        income_type,
                        str(row.get("time") or ""),
                        str(row.get("asset") or ""),
                        str(row.get("income")),
                        income,
                    )
                )

            if parsed:
                assets = {item[3] for item in parsed if item[3]}
                income_types = {item[1] for item in parsed}
                if len(assets) > 1:
                    last_error = ValueError(
                        f"birden çok income asset'i toplamak güvenli değil: {sorted(assets)}"
                    )
                    continue
                if "REALIZED_PNL" not in income_types:
                    # Yalnız giriş komisyonu görünmüş olabilir; closing income
                    # henüz gecikiyorsa bu snapshot doğrulanmış sayılmamalı.
                    continue
                # Fingerprint telemetry/debug için tutulur; toplama tek bir
                # response snapshot'ı üzerinde yapılır, retry'lar birleştirilmez.
                latest_fingerprint = tuple(sorted(item[:-1] for item in parsed))
                latest_values = [item[-1] for item in parsed]

        if latest_values is not None:
            net_pnl = sum(latest_values)
            self.logger.info(
                f"✅ {symbol}: Binance income net PNL doğrulandı: {net_pnl:.8f} "
                f"({len(latest_values)} kayıt, snapshot={len(latest_fingerprint or ())})"
            )
            return net_pnl

        if last_error:
            self.logger.warning(
                f"⚠️ {symbol}: Binance income doğrulanamadı ({last_error}); brüt tahmine düşülüyor"
            )
        else:
            self.logger.warning(
                f"⚠️ {symbol}: Binance income penceresi boş; brüt tahmine düşülüyor"
            )
        return None

    async def _income_window_start_ms(
        self,
        symbol: str,
        opened_at: Optional[datetime],
        entry_order_id: Optional[str],
    ) -> Optional[int]:
        """Giriş order'ının borsaca doğrulanmış zamanından pencereyi başlat.

        Yalnız DB ``opened_at`` değerinden geriye geniş bir pencere açmak,
        hızlı aynı-sembol yeniden girişlerinde önceki işlemin komisyon/PnL'ini
        yeni işleme yazabilir. Order zamanı doğrulanamıyorsa bu yüzden güvenli
        biçimde ``None`` döner ve çağıran tahmini kaynağa düşer.
        """
        if not entry_order_id:
            return None
        try:
            numeric_order_id = int(entry_order_id)
        except (TypeError, ValueError):
            return None
        try:
            order = await self.client.get_order(symbol, numeric_order_id)
        except Exception as exc:
            self.logger.warning(f"⚠️ {symbol}: giriş emri zamanı okunamadı ({exc})")
            return None
        if not isinstance(order, dict):
            return None
        if order.get("symbol") and order.get("symbol") != symbol:
            return None
        raw_time = order.get("updateTime") or order.get("time")
        try:
            order_time_ms = int(raw_time)
        except (TypeError, ValueError):
            return None
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if order_time_ms <= 0 or order_time_ms > now_ms + 60_000:
            return None

        if opened_at:
            opened_utc = opened_at
            if opened_utc.tzinfo is None:
                opened_utc = opened_utc.replace(tzinfo=timezone.utc)
            else:
                opened_utc = opened_utc.astimezone(timezone.utc)
            opened_ms = int(opened_utc.timestamp() * 1000)
            # Bir order günlerce beklemişse tek income çağrısındaki 1000 satır
            # sınırı güvenilir ilişkilendirme yapmaya yetmez.
            if abs(opened_ms - order_time_ms) > 24 * 60 * 60 * 1000:
                return None

        return order_time_ms - self.INCOME_ENTRY_LOOKBACK_SECONDS * 1000

    @staticmethod
    def _infer_exit_reason(sp: ScalpPosition, exit_price: float) -> str:
        """Kaba çıkarım: son fiyat SL'ye mi TP tarafına mı yakındı + tp1_done bilgisi."""
        if sp.trailing_active:
            # TP1 sonrası trailing aktifken kapanmışsa TRAIL veya son SL — TRAIL say
            return "TRAIL"
        sl_price = sp.plan.initial_stop
        tp_price = sp.plan.tp1_price
        dist_to_sl = abs(exit_price - sl_price)
        dist_to_tp = abs(exit_price - tp_price)
        return "TP_LADDER" if dist_to_tp < dist_to_sl else "SL"

    def _update_mae_mfe(self, sp: ScalpPosition, current_price: float) -> None:
        entry = sp.position.entry_price
        leverage = sp.position.leverage or 1
        if entry <= 0:
            return
        price_delta_pct = (current_price - entry) / entry * 100.0
        if sp.signal.direction == Direction.SHORT:
            price_delta_pct = -price_delta_pct
        roi_pct = price_delta_pct * leverage
        sp.mfe_pct = max(sp.mfe_pct, roi_pct)
        sp.mae_pct = min(sp.mae_pct, roi_pct)

    @staticmethod
    def _live_stop_trigger(
        algo_orders: List[Dict[str, Any]],
        direction: Direction,
    ) -> Optional[float]:
        """Açık algo emirlerinden pozisyon yönünü gerçekten koruyan STOP'u bul."""
        expected_side = "SELL" if direction == Direction.LONG else "BUY"
        for order in algo_orders or []:
            order_type = order.get("orderType") or order.get("type")
            if order_type not in ("STOP_MARKET", "STOP"):
                continue
            side = str(order.get("side") or "").upper()
            if side and side != expected_side:
                continue
            trigger = order.get("triggerPrice") or order.get("stopPrice")
            try:
                trigger_value = float(trigger)
            except (TypeError, ValueError):
                continue
            if trigger_value > 0:
                return trigger_value
        return None

    async def _record_recovery_estimate(self, trade: Any, notes: str) -> bool:
        """Belirsiz restart kapanışını açıkça tahmini brüt olarak kaydet."""
        try:
            exit_price = await self.client.get_current_price(trade.symbol)
        except Exception:
            exit_price = None
        exit_price = float(exit_price or trade.entry_price)
        estimated_gross = self._estimate_gross_pnl(
            Direction(trade.direction),
            float(trade.entry_price),
            exit_price,
            float(trade.quantity),
        )
        try:
            await self.tracker.record_close(
                trade_id=trade.id,
                exit_price=exit_price,
                realized_pnl=estimated_gross,
                exit_reason="UNKNOWN",
                pnl_source="estimated_gross",
                notes=notes,
            )
        except Exception as e:
            self.logger.error(
                f"❌ recover(): {trade.symbol} #{trade.id} UNKNOWN kapanışı yazılamadı ({e})"
            )
            return False
        self._maybe_start_loss_cooldown(
            trade.symbol,
            "UNKNOWN",
            estimated_gross,
            self._estimated_roundtrip_fee(
                float(trade.entry_price), exit_price, float(trade.quantity)
            ),
        )
        return True

    # ------------------------------------------------------------------
    # Restart kurtarma
    # ------------------------------------------------------------------

    async def recover(self) -> bool:
        """DB'de status=OPEN olan scalp işlemlerini borsadaki gerçek pozisyonlarla
        eşleştirip izlemeye geri al.

        Borsada karşılığı bulunmayan bir DB kaydı (manuel kapatma, dış müdahale,
        vb.) exit_reason=UNKNOWN ile kapatılır — "bilinmiyor" gerçeği maskelemez.
        Borsa durumu veya koruma emirleri kesin okunamazsa ``False`` dönülür;
        çağıran bunu readiness başarısızlığı olarak ele almalıdır.
        """
        try:
            open_trades = await self.tracker.open_trades()
        except Exception as e:
            self.logger.error(f"❌ recover(): açık scalp kayıtları okunamadı ({e})")
            return False

        if not open_trades:
            self.logger.info("ℹ️ recover(): DB'de açık scalp işlemi yok")
            return True

        recovered = True
        for trade in open_trades:
            try:
                recovered = await self._recover_one(trade) and recovered
            except UnprotectedPositionError:
                # Açık ve korumasız pozisyon kapatılamadı: bunu boolean içinde
                # gizlemek insan müdahalesini geciktirir; kritik hatayı yükselt.
                raise
            except Exception as e:
                recovered = False
                self.logger.error(
                    f"❌ recover(): {getattr(trade, 'symbol', '?')} "
                    f"#{getattr(trade, 'id', '?')} kurtarılamadı ({e})"
                )
        return recovered

    async def _recover_one(self, trade) -> bool:
        symbol = trade.symbol
        try:
            pos_info = await self.client.get_position_risk(symbol)
        except Exception as e:
            self.logger.error(
                f"⚠️ recover(): {symbol} pozisyon durumu sorgulanamadı ({e}), "
                f"#{trade.id} bu turda atlanıyor"
            )
            return False

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0

        if amt <= 0:
            self.logger.warning(
                f"⚠️ recover(): {symbol} borsada açık pozisyon yok ama DB'de #{trade.id} "
                f"OPEN görünüyor — UNKNOWN ile kapatılıyor"
            )
            return await self._record_recovery_estimate(
                trade,
                notes="recovery=no_live_position",
            )

        direction = Direction(trade.direction)
        leverage = trade.leverage

        try:
            algo_orders = await self.client.get_open_algo_orders(symbol)
        except Exception as e:
            self.logger.error(
                f"⚠️ recover(): {symbol} koşullu emirler okunamadı ({e}); "
                f"koruma durumu belirsiz, readiness başarısız"
            )
            return False

        current_stop = self._live_stop_trigger(algo_orders, direction)
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
            except Exception as e:
                raise UnprotectedPositionError(
                    f"{symbol}: restart kurtarmasında korumasız pozisyon kapatılamadı ({e})"
                ) from e
            if not closed:
                raise UnprotectedPositionError(
                    f"{symbol}: restart kurtarmasında canlı STOP yok ve acil kapatma başarısız"
                )
            return await self._record_recovery_estimate(
                trade,
                notes="recovery=missing_stop_emergency_close",
            )

        tp1_price = price_at_roi(trade.entry_price, self.cfg.scalper_tp1_roi, leverage, direction)
        tp2_price = price_at_roi(trade.entry_price, self.cfg.scalper_tp2_roi, leverage, direction)
        entry_fee_rate, exit_fee_rate, fee_rate_source = await self._resolve_commission_rates(
            symbol
        )
        breakeven_price = fee_aware_breakeven_price(
            entry=float(trade.entry_price),
            direction=direction,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            buffer_pct=float(getattr(self.cfg, "scalper_breakeven_buffer_pct", 0.05)),
        )
        breakeven_cost_pct = (
            abs(breakeven_price - float(trade.entry_price))
            / float(trade.entry_price)
            * 100.0
        )

        tp1_fraction = self.cfg.scalper_tp1_fraction
        tp2_fraction = self.cfg.scalper_tp2_fraction
        tp1_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=trade.tp1_algo_id,
            expected_quantity=float(trade.quantity) * tp1_fraction,
            label="TP1/recovery",
        )
        tp2_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=trade.tp2_algo_id,
            expected_quantity=float(trade.quantity) * tp2_fraction,
            label="TP2/recovery",
        )

        signal = ScalpSignal(
            strategy=trade.strategy,
            symbol=symbol,
            direction=direction,
            entry_price=trade.entry_price,
            stop_price=current_stop,
            reason=trade.signal_reason or "recover",
            regime=Regime.UNKNOWN,
            atr_5m=0.0,
        )

        position = PositionModel(
            symbol=symbol,
            side=PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT,
            leverage=leverage,
            margin_type="ISOLATED",
            entry_price=trade.entry_price,
            current_price=trade.entry_price,
            # quantity burada ORİJİNAL fill'dir; canlı kalan miktar her stop
            # replacement'ında borsadan okunur. Aksi halde restart sonrası TP
            # eşikleri küçülüp false positive üretir.
            quantity=trade.quantity,
            position_size=trade.quantity * trade.entry_price,
            initial_stoploss=current_stop,
            current_stoploss=current_stop,
            first_tp_price=tp1_price,
            first_tp_quantity=trade.quantity * tp1_fraction,
            targets=str([tp1_price, tp2_price]),
            status=PositionStatus.OPEN,
            entry_order_id="",
            sl_order_id=trade.sl_algo_id,
            tp_order_id=trade.tp1_algo_id,
            highest_price=trade.entry_price,
            lowest_price=trade.entry_price,
            trailing_stop_distance=self.cfg.scalper_chandelier_atr_mult,
            trailing_profit_distance=self.cfg.scalper_tp1_roi,
            opened_at=trade.opened_at,
            notes=f"scalper:{trade.strategy}:recovered",
        )

        plan = ExitPlan(
            tp1_price=tp1_price,
            tp1_quantity=trade.quantity * tp1_fraction,
            tp2_price=tp2_price,
            tp2_quantity=trade.quantity * tp2_fraction,
            runner_quantity=max(trade.quantity * (1 - tp1_fraction - tp2_fraction), 0.0),
            initial_stop=current_stop,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=self.cfg.scalper_chandelier_atr_mult,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            fee_rate_source=fee_rate_source,
            breakeven_cost_pct=breakeven_cost_pct,
            runner_floor_price=tp1_price,
            tp1_algo_id=trade.tp1_algo_id,
            tp2_algo_id=trade.tp2_algo_id,
        )

        entry_candle_time = 0
        if trade.opened_at:
            opened_at = trade.opened_at
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            entry_candle_time = int(opened_at.timestamp() * 1000)

        sp = ScalpPosition(
            trade_id=trade.id,
            signal=signal,
            position=position,
            plan=plan,
            entry_candle_time=entry_candle_time,
            tp1_done=tp1_done,
            tp2_done=tp2_done,
            trailing_active=tp1_done or tp2_done,
        )
        self.track(sp)
        self.logger.info(
            f"♻️ recover(): {symbol} #{trade.id} izlemeye geri alındı "
            f"(canlı_miktar={amt}, tp1_done={tp1_done}, tp2_done={tp2_done})",
            extra={"trade": True},
        )
        return True
