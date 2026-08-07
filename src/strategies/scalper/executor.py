"""
Scalper pozisyon açma motoru.

PositionManager'ın bugün elden geçirilmiş güvenlik akışını (gerçek dolum
çözümü, SL konulamazsa acil kapatma) public sarmalayıcılar üzerinden AYNEN
yeniden kullanır; üstüne kendi risk bazlı boyutlama ve TP merdiveni mantığını
kurar. Hiçbir güvenlik deseni burada yeniden yazılmaz.

Akış (try_open):
  1. Bakiye sorgusu (None/<=0 → vazgeç)
  2. Stop mesafesi risk kapısı ([min_stop_pct, max_stop_pct])
  3. R:R kapısı (beklenen harman getiri / SL riski >= scalper_min_rr)
  4. Risk bazlı boyutlama + nominal tavan kırpma
  5. Yuvarlama + borsa filtresi doğrulaması
  6. Margin type + leverage (emirden ÖNCE — hata zararsız)
  7. Market emri (bu noktadan sonra pozisyon GERÇEK olabilir)
  8. Gerçek dolum çözümü (pm.resolve_fill)
  9. Stop-loss (pm.place_stop_loss_or_close — başarısızsa pozisyon zaten kapatıldı)
 10. TP1/TP2 merdiveni (başarısızlık pozisyonu iptal ettirmez — SL var)
 11. PositionModel + DB kaydı + tracker + ExitPlan
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.core.logger import app_logger
from src.models.position import PositionModel, PositionStatus, PositionSide
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Direction,
    ExitPlan,
    ScalpSignal,
    StrategyContext,
    price_at_roi,
)
from src.trading.binance_client_improved import (
    ImprovedBinanceClient,
    BinanceAPIError,
)
from src.trading.position_manager import PositionManager


@dataclass
class ScalpPosition:
    """Executor'ın kurduğu, exits.py ve engine.py tarafından taşınan canlı kayıt.

    mae_pct/mfe_pct exits.py tarafından her turda mark fiyatına göre güncellenir
    ve kapanışta tracker.record_close'a aktarılır (ROI yüzdesi cinsinden).
    """
    trade_id: int
    signal: ScalpSignal
    position: PositionModel          # pm akışının ürettiği kayıt DEĞİL — executor kurar
    plan: ExitPlan                   # types.ExitPlan
    entry_candle_time: int           # chandelier since hesabı için (ms)
    tp1_done: bool = False
    trailing_active: bool = False
    mae_pct: float = 0.0             # en kötü (olumsuz) ROI% ucu — negatif veya 0
    mfe_pct: float = 0.0             # en iyi (olumlu) ROI% ucu — pozitif veya 0


class ScalpExecutor:
    """Scalper sinyalinden güvenli, korumalı bir pozisyon açar."""

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
    ):
        self.client = client
        self.pm = pm
        self.tracker = tracker
        self.cfg = cfg
        self.logger = app_logger

    async def try_open(
        self, signal: ScalpSignal, ctx: StrategyContext
    ) -> Optional[ScalpPosition]:
        symbol = signal.symbol
        direction = signal.direction
        entry_hint = signal.entry_price
        stop_price = signal.stop_price

        # --- 1. Bakiye ---
        try:
            balance = await self.client.get_account_balance()
        except Exception as e:
            self.logger.error(f"❌ {symbol}: bakiye sorgusunda beklenmeyen hata ({e})")
            return None
        if balance is None or balance <= 0:
            self.logger.error(
                f"❌ {symbol}: bakiye bilinmiyor veya sıfır ({balance}), scalp girişi iptal"
            )
            return None

        # --- 2. Stop mesafesi risk kapısı ---
        if entry_hint <= 0:
            self.logger.error(f"❌ {symbol}: geçersiz giriş fiyatı ({entry_hint}), sinyal atlandı")
            return None
        stop_distance_pct = abs(entry_hint - stop_price) / entry_hint * 100.0
        if not (self.cfg.scalper_min_stop_pct <= stop_distance_pct <= self.cfg.scalper_max_stop_pct):
            self.logger.info(
                f"⏭️ {symbol}: stop mesafesi sınır dışı (%{stop_distance_pct:.3f}, izin verilen "
                f"[%{self.cfg.scalper_min_stop_pct}-%{self.cfg.scalper_max_stop_pct}]), sinyal atlandı"
            )
            return None

        # --- 3. R:R kapısı ---
        # Beklenen harman getiri (ROI%): tp1_roi*tp1_frac + tp2_roi*tp2_frac + tp1_roi*runner_frac
        # (runner'ın en az TP1 kadar taşıdığı varsayımı — muhafazakâr)
        # SL riski (ROI%): stop_distance_pct * kaldıraç
        # rr = beklenen_getiri / sl_riski ; rr < cfg.scalper_min_rr -> None
        # cfg.scalper_min_rr <= 0 ise kapı atlanır
        min_rr = self.cfg.scalper_min_rr
        if min_rr > 0:
            tp1_frac = self.cfg.scalper_tp1_fraction
            tp2_frac = self.cfg.scalper_tp2_fraction
            runner_frac = max(0.0, 1.0 - tp1_frac - tp2_frac)
            expected_roi = (
                self.cfg.scalper_tp1_roi * tp1_frac
                + self.cfg.scalper_tp2_roi * tp2_frac
                + self.cfg.scalper_tp1_roi * runner_frac
            )
            sl_risk_roi = stop_distance_pct * self.cfg.scalper_leverage
            if sl_risk_roi <= 0:
                self.logger.error(f"❌ {symbol}: SL riski hesaplanamadı (sl_risk_roi<=0), sinyal atlandı")
                return None
            rr = expected_roi / sl_risk_roi
            if rr < min_rr:
                self.logger.info(
                    f"⏭️ {symbol}: R:R yetersiz (rr={rr:.2f} < min={min_rr:.2f}, "
                    f"beklenen_getiri=%{expected_roi:.2f}, sl_riski=%{sl_risk_roi:.2f}), sinyal atlandı"
                )
                return None

        # --- 4. Risk bazlı boyutlama + nominal tavan ---
        price_distance = abs(entry_hint - stop_price)
        if price_distance <= 0:
            self.logger.error(f"❌ {symbol}: giriş/stop mesafesi sıfır, boyutlama yapılamıyor")
            return None

        risk_amount = balance * (self.cfg.scalper_risk_percentage / 100.0) * signal.risk_multiplier
        qty = risk_amount / price_distance

        nominal_cap = balance * self.cfg.scalper_leverage * 0.5
        nominal = qty * entry_hint
        if nominal > nominal_cap and entry_hint > 0:
            qty = nominal_cap / entry_hint
            self.logger.info(
                f"✂️ {symbol}: nominal değer kırpıldı ({nominal:.2f} -> {nominal_cap:.2f} USDT tavanı)"
            )

        # --- 5. Yuvarlama + borsa filtresi doğrulaması ---
        try:
            qty = await self.client.quantize_quantity(symbol, qty)
            await self.client.validate_order(symbol, qty, entry_hint)
        except BinanceAPIError as e:
            self.logger.error(f"❌ {symbol}: emir doğrulanamadı (kod={e.code}: {e.msg})")
            return None
        except Exception as e:
            self.logger.error(f"❌ {symbol}: boyutlama/doğrulama sırasında beklenmeyen hata ({e})")
            return None

        # --- 6. Margin type + leverage (emirden ÖNCE — burada hata zararsız) ---
        try:
            await self.client.set_margin_type(symbol, "ISOLATED")
            await self.client.set_leverage(symbol, self.cfg.scalper_leverage)
        except BinanceAPIError as e:
            self.logger.error(
                f"❌ {symbol}: margin/leverage ayarlanamadı (kod={e.code}: {e.msg}), pozisyon açılmadı"
            )
            return None
        except Exception as e:
            self.logger.error(f"❌ {symbol}: margin/leverage ayarında beklenmeyen hata ({e})")
            return None

        # --- 7. Market emri: BU NOKTADAN SONRA pozisyon GERÇEK olabilir ---
        side = "BUY" if direction == Direction.LONG else "SELL"
        try:
            entry_order = await self.client.open_market_order(symbol=symbol, side=side, quantity=qty)
        except BinanceAPIError as e:
            self.logger.error(f"❌ {symbol}: market order başarısız (kod={e.code}: {e.msg})")
            return None
        except Exception as e:
            self.logger.error(f"❌ {symbol}: market order sırasında beklenmeyen hata ({e})")
            return None

        # --- 8. Gerçek dolum çözümü ---
        sl_side = "SELL" if direction == Direction.LONG else "BUY"
        try:
            entry_price, filled_qty = await self.pm.resolve_fill(symbol, entry_order)
        except Exception as e:
            self.logger.critical(
                f"🚨 {symbol}: dolum bilgisi hiçbir kaynaktan okunamadı ({e}). Pozisyon açık "
                f"olabilir — PositionManager'ın acil kapatma akışı devreye sokuluyor."
            )
            await self.pm.place_stop_loss_or_close(symbol=symbol, sl_side=sl_side, stop_price=stop_price)
            return None

        if filled_qty <= 0:
            self.logger.error(f"❌ {symbol}: emir dolmadı (executedQty=0), pozisyon yok")
            return None

        self.logger.info(f"✅ Scalp dolum: {symbol} {filled_qty} @ {entry_price}")

        # --- 9. Stop-loss: BAŞARISIZ OLURSA pm zaten pozisyonu acil kapattı ---
        sl_order = await self.pm.place_stop_loss_or_close(
            symbol=symbol, sl_side=sl_side, stop_price=stop_price
        )
        if sl_order is None:
            self.logger.error(
                f"❌ {symbol}: SL konulamadı — pozisyon PositionManager tarafından kapatıldı"
            )
            return None
        sl_algo_id = self._extract_id(sl_order)

        # --- 10. TP merdiveni: başarısızlık pozisyonu tehlikeye atmaz (SL var) ---
        tp1_price = price_at_roi(entry_price, self.cfg.scalper_tp1_roi, self.cfg.scalper_leverage, direction)
        tp2_price = price_at_roi(entry_price, self.cfg.scalper_tp2_roi, self.cfg.scalper_leverage, direction)
        tp1_qty = filled_qty * self.cfg.scalper_tp1_fraction
        tp2_qty = filled_qty * self.cfg.scalper_tp2_fraction
        runner_qty = max(filled_qty - tp1_qty - tp2_qty, 0.0)

        tp1_algo_id = await self._place_tp_safely(symbol, sl_side, tp1_price, tp1_qty, "TP1")
        tp2_algo_id = await self._place_tp_safely(symbol, sl_side, tp2_price, tp2_qty, "TP2")

        # --- 11. Kayıt: PositionModel + DB + tracker + ExitPlan ---
        leverage = self.cfg.scalper_leverage
        margin_usdt = (filled_qty * entry_price) / leverage if leverage else filled_qty * entry_price

        position_side = PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT
        position = PositionModel(
            symbol=symbol,
            side=position_side,
            leverage=leverage,
            margin_type="ISOLATED",
            entry_price=entry_price,
            current_price=entry_price,
            quantity=filled_qty,
            position_size=filled_qty * entry_price,
            initial_stoploss=stop_price,
            current_stoploss=stop_price,
            first_tp_price=tp1_price,
            first_tp_quantity=tp1_qty,
            targets=str([tp1_price, tp2_price]),
            status=PositionStatus.OPEN,
            entry_order_id=str(entry_order.get("orderId") or ""),
            sl_order_id=sl_algo_id,
            tp_order_id=tp1_algo_id,
            highest_price=entry_price,
            lowest_price=entry_price,
            trailing_stop_distance=self.cfg.scalper_chandelier_atr_mult,
            trailing_profit_distance=self.cfg.scalper_tp1_roi,
            opened_at=datetime.utcnow(),
            notes=f"scalper:{signal.strategy}",
        )

        buffer_frac = self.cfg.scalper_breakeven_buffer_pct / 100.0
        breakeven_price = (
            entry_price * (1 + buffer_frac) if direction == Direction.LONG
            else entry_price * (1 - buffer_frac)
        )

        plan = ExitPlan(
            tp1_price=tp1_price,
            tp1_quantity=tp1_qty,
            tp2_price=tp2_price,
            tp2_quantity=tp2_qty,
            runner_quantity=runner_qty,
            initial_stop=stop_price,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=self.cfg.scalper_chandelier_atr_mult,
            tp1_algo_id=tp1_algo_id,
            tp2_algo_id=tp2_algo_id,
        )

        try:
            trade_id = await self.tracker.record_open(
                signal=signal,
                entry_price=entry_price,
                quantity=filled_qty,
                leverage=leverage,
                margin_usdt=margin_usdt,
                sl_algo_id=sl_algo_id,
                tp1_algo_id=tp1_algo_id,
                tp2_algo_id=tp2_algo_id,
            )
        except Exception as e:
            self.logger.critical(
                f"🚨 {symbol}: scalp işlem kaydı DB'ye yazılamadı ({e}). Pozisyon borsada AÇIK "
                f"ve SL korumalı ama takip kaydı yok — exits.recover() borsa taramasında bulmalı."
            )
            return None

        entry_candle_time = (
            ctx.candles_5m[-1].close_time if ctx.candles_5m
            else int(datetime.utcnow().timestamp() * 1000)
        )

        self.logger.info(
            f"✅ Scalp pozisyon açıldı: {signal.strategy}/{symbol} {direction.value} "
            f"{filled_qty} @ {entry_price} (SL={stop_price}, TP1={tp1_price}, TP2={tp2_price})",
            extra={"trade": True},
        )

        return ScalpPosition(
            trade_id=trade_id,
            signal=signal,
            position=position,
            plan=plan,
            entry_candle_time=entry_candle_time,
        )

    async def _place_tp_safely(
        self, symbol: str, side: str, price: float, quantity: float, label: str
    ) -> Optional[str]:
        """TP emrini koymayı dene; başarısızlık pozisyonu İPTAL ETTİRMEZ (SL zaten var)."""
        if quantity <= 0:
            self.logger.warning(f"⚠️ {symbol}: {label} miktarı sıfır, atlanıyor")
            return None
        try:
            order = await self.client.place_take_profit(
                symbol=symbol, side=side, stop_price=price, quantity=quantity
            )
            return self._extract_id(order)
        except BinanceAPIError as e:
            self.logger.error(
                f"⚠️ {symbol}: {label} konulamadı (kod={e.code}: {e.msg}). "
                f"Pozisyon SL ile korunuyor, {label} olmadan devam ediliyor."
            )
            return None
        except Exception as e:
            self.logger.error(
                f"⚠️ {symbol}: {label} konulurken beklenmeyen hata ({e}). "
                f"Pozisyon SL ile korunuyor, {label} olmadan devam ediliyor."
            )
            return None

    @staticmethod
    def _extract_id(order: dict) -> Optional[str]:
        value = order.get("algoId") or order.get("orderId")
        return str(value) if value is not None else None
