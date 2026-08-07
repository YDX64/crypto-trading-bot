"""
Scalper strateji varyantları: A (trend kırılması), B (trend içi uç avcısı,
ana strateji), C (saf uç avcısı, trend filtresiz).

Kullanıcı felsefesi: "en dipten long, en tepeden short" — uç yakalama.
Her strateji SAF olmalı: yalnız StrategyContext okur, IO/saat/rastgelelik
yok. Sinyal yoksa None döner (bu normal akıştır, log spam'e sebep olmaz).

Bu modül src/strategies/scalper/{types,indicators}.py'ye ve (yalnız
get_enabled'ın bilinmeyen isim uyarısı için) src.core.logger.app_logger'a
bağımlıdır. Strateji sınıflarının evaluate() metodları hiçbir IO/log
çağrısı yapmaz — saflık bozulmaz.
"""

from __future__ import annotations

from src.core.logger import app_logger
from src.strategies.scalper.indicators import (
    bearish_divergence,
    bollinger,
    bullish_divergence,
    cmf,
    donchian,
    find_fvg,
    find_order_block,
    last_swing_high,
    last_swing_low,
    liquidity_sweep,
    market_structure,
    mfi_series,
    rsi_series,
    swing_points,
)
from src.strategies.scalper.types import (
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
    StrategyProtocol,
)


class StrategyA(StrategyProtocol):
    """A — Trend Kırılması (referans/karşılaştırma stratejisi).

    4h rejim yönünde 5m'de momentum kırılması: Donchian(20) kırılımı +
    hacim onayı. RANGE/UNKNOWN'da işlem yok.
    """

    name = "A"

    _DONCHIAN_PERIOD = 20
    _VOLUME_LOOKBACK = 20
    _VOLUME_MULT = 1.5

    def evaluate(self, ctx: StrategyContext) -> ScalpSignal | None:
        if ctx.regime not in (Regime.UP, Regime.DOWN):
            return None

        candles_5m = ctx.candles_5m
        n5 = len(candles_5m)
        # donchian(exclude_last=True, period=20) n>=21 ister; hacim
        # penceresi de aynı 20 mumluk (son hariç) aralığı kullanır.
        min_needed = self._DONCHIAN_PERIOD + 1
        if n5 < min_needed:
            return None

        atr_5m = ctx.atr_5m
        if atr_5m <= 0.0:
            return None

        dc_high, dc_low = donchian(candles_5m, self._DONCHIAN_PERIOD, exclude_last=True)
        last = candles_5m[-1]
        volume_window = candles_5m[-self._VOLUME_LOOKBACK - 1:-1]
        avg_volume = sum(c.volume for c in volume_window) / len(volume_window)
        if avg_volume <= 0.0:
            return None
        vol_ratio = last.volume / avg_volume

        if ctx.regime == Regime.UP:
            if last.close <= dc_high:
                return None
            if vol_ratio <= self._VOLUME_MULT:
                return None

            stop_price = last_swing_low(candles_5m)
            if stop_price is None:
                stop_price = dc_low if dc_low > 0.0 else None
            if stop_price is None or stop_price >= ctx.current_price:
                return None

            score = (last.close - dc_high) / atr_5m
            reason = (
                f"5m Donchian({self._DONCHIAN_PERIOD}) yukarı kırılım + "
                f"{vol_ratio:.1f}x hacim"
            )
            direction = Direction.LONG
        else:  # DOWN
            if last.close >= dc_low:
                return None
            if vol_ratio <= self._VOLUME_MULT:
                return None

            stop_price = last_swing_high(candles_5m)
            if stop_price is None:
                stop_price = dc_high if dc_high > 0.0 else None
            if stop_price is None or stop_price <= ctx.current_price:
                return None

            score = (dc_low - last.close) / atr_5m
            reason = (
                f"5m Donchian({self._DONCHIAN_PERIOD}) aşağı kırılım + "
                f"{vol_ratio:.1f}x hacim"
            )
            direction = Direction.SHORT

        return ScalpSignal(
            strategy=self.name,
            symbol=ctx.symbol,
            direction=direction,
            entry_price=ctx.current_price,
            stop_price=stop_price,
            reason=reason,
            regime=ctx.regime,
            atr_5m=atr_5m,
            score=score,
        )


class StrategyB(StrategyProtocol):
    """B — Trend İçi Uç Avcısı (ana strateji).

    4h rejim yönünde 5m/15m geri çekilme ucunda giriş: RSI ucu + Bollinger
    bant taşması + yakın swing yapısı + dönüş mumu teyidi + 15m bağlam
    teyidi (üst zaman dilimi de soğumuş/ısınmış olmalı). RANGE/UNKNOWN'da
    işlem yok (C devralır).
    """

    name = "B"

    _RSI_PERIOD = 14
    _BB_PERIOD = 20
    _BB_STD = 2.0
    _BB_TOUCH_TOLERANCE = 0.001  # bandın %0.1 bitişiği de "değme" sayılır
    _SWING_LEFT = 3
    _SWING_RIGHT = 3
    _RECENT_SWING_WINDOW = 10
    _RSI_LONG_MAX = 35.0
    _RSI_SHORT_MIN = 65.0
    _CTX_RSI_LONG_MAX = 45.0   # 15m bağlamı: üst zaman dilimi de soğumuş
    _CTX_RSI_SHORT_MIN = 55.0  # 15m bağlamı: üst zaman dilimi de ısınmış
    _STOP_BUFFER = 0.001

    def evaluate(self, ctx: StrategyContext) -> ScalpSignal | None:
        if ctx.regime not in (Regime.UP, Regime.DOWN):
            return None

        candles_5m = ctx.candles_5m
        candles_15m = ctx.candles_15m
        n5 = len(candles_5m)
        # BB(20) + bir önceki mum (teyit için) + swing_points üretebilecek
        # kadar veri; 15m tarafı RSI(14)'ün gerçek değer üretmesi için
        # period'dan büyük olmalı.
        if n5 < self._BB_PERIOD + 1 or len(candles_15m) < self._RSI_PERIOD + 1:
            return None

        atr_5m = ctx.atr_5m
        if atr_5m <= 0.0:
            return None

        closes_5m = [c.close for c in candles_5m]
        closes_15m = [c.close for c in candles_15m]

        rsi5m = rsi_series(closes_5m, self._RSI_PERIOD)[-1]
        rsi15m = rsi_series(closes_15m, self._RSI_PERIOD)[-1]
        upper, _mid, lower = bollinger(closes_5m, self._BB_PERIOD, self._BB_STD)
        if upper <= 0.0 and lower <= 0.0:
            return None

        last = candles_5m[-1]
        prev = candles_5m[-2]
        highs_idx, lows_idx = swing_points(candles_5m, self._SWING_LEFT, self._SWING_RIGHT)

        if ctx.regime == Regime.UP:
            if rsi5m >= self._RSI_LONG_MAX:
                return None
            lower_touch = lower * (1.0 + self._BB_TOUCH_TOLERANCE)
            if not (last.close <= lower_touch or last.low < lower):
                return None
            if not lows_idx or lows_idx[-1] < n5 - self._RECENT_SWING_WINDOW:
                return None
            if not (last.close > last.open and last.close > prev.close):
                return None
            if rsi15m >= self._CTX_RSI_LONG_MAX:
                return None

            swing_low_price = candles_5m[lows_idx[-1]].low
            stop_price = swing_low_price * (1.0 - self._STOP_BUFFER)
            if stop_price >= ctx.current_price:
                return None

            depth = max(0.0, lower - min(last.low, last.close))
            score = (self._RSI_LONG_MAX - rsi5m) + depth / atr_5m
            reason = (
                f"UP rejimde geri çekilme dibi: RSI {rsi5m:.0f}, "
                f"BB alt bant, swing-low teyitli"
            )
            direction = Direction.LONG
        else:  # DOWN
            if rsi5m <= self._RSI_SHORT_MIN:
                return None
            upper_touch = upper * (1.0 - self._BB_TOUCH_TOLERANCE)
            if not (last.close >= upper_touch or last.high > upper):
                return None
            if not highs_idx or highs_idx[-1] < n5 - self._RECENT_SWING_WINDOW:
                return None
            if not (last.close < last.open and last.close < prev.close):
                return None
            if rsi15m <= self._CTX_RSI_SHORT_MIN:
                return None

            swing_high_price = candles_5m[highs_idx[-1]].high
            stop_price = swing_high_price * (1.0 + self._STOP_BUFFER)
            if stop_price <= ctx.current_price:
                return None

            depth = max(0.0, max(last.high, last.close) - upper)
            score = (rsi5m - self._RSI_SHORT_MIN) + depth / atr_5m
            reason = (
                f"DOWN rejimde geri çekilme tepesi: RSI {rsi5m:.0f}, "
                f"BB üst bant, swing-high teyitli"
            )
            direction = Direction.SHORT

        return ScalpSignal(
            strategy=self.name,
            symbol=ctx.symbol,
            direction=direction,
            entry_price=ctx.current_price,
            stop_price=stop_price,
            reason=reason,
            regime=ctx.regime,
            atr_5m=atr_5m,
            score=score,
        )


class StrategyC(StrategyProtocol):
    """C — Saf Uç Avcısı (trend filtresiz).

    4h rejim ne olursa olsun (yalnız UNKNOWN hariç) çalışır: RSI ucu +
    Bollinger bant taşması + RSI diverjansı üst üste binince ters yönde
    girer. B'nin trend filtresinin değer katıp katmadığını ölçmek için var
    — counter-trend riski nedeniyle risk_multiplier sabit 0.5.
    """

    name = "C"

    _RSI_PERIOD = 14
    _BB_PERIOD = 20
    _BB_STD = 2.0
    _DIVERGENCE_LOOKBACK = 40
    _STOP_LOOKBACK = 40
    _STOP_BUFFER = 0.001
    _RSI_LONG_MAX = 25.0
    _RSI_SHORT_MIN = 75.0
    _RISK_MULTIPLIER = 0.5

    def evaluate(self, ctx: StrategyContext) -> ScalpSignal | None:
        if ctx.regime == Regime.UNKNOWN:
            return None

        candles_5m = ctx.candles_5m
        n5 = len(candles_5m)
        min_needed = max(self._BB_PERIOD, self._STOP_LOOKBACK) + 1
        if n5 < min_needed:
            return None

        atr_5m = ctx.atr_5m
        if atr_5m <= 0.0:
            return None

        closes_5m = [c.close for c in candles_5m]
        rsi_values = rsi_series(closes_5m, self._RSI_PERIOD)
        rsi_last = rsi_values[-1]
        upper, _mid, lower = bollinger(closes_5m, self._BB_PERIOD, self._BB_STD)
        if upper <= 0.0 and lower <= 0.0:
            return None

        last = candles_5m[-1]

        if (rsi_last < self._RSI_LONG_MAX
                and last.close < lower
                and bullish_divergence(candles_5m, rsi_values, self._DIVERGENCE_LOOKBACK)):
            lookback_window = candles_5m[-self._STOP_LOOKBACK:]
            stop_price = min(c.low for c in lookback_window) * (1.0 - self._STOP_BUFFER)
            if stop_price >= ctx.current_price:
                return None

            score = (self._RSI_LONG_MAX - rsi_last) + max(0.0, lower - last.close) / atr_5m
            return ScalpSignal(
                strategy=self.name,
                symbol=ctx.symbol,
                direction=Direction.LONG,
                entry_price=ctx.current_price,
                stop_price=stop_price,
                reason=f"trend filtresiz dip: RSI {rsi_last:.0f} + boğa diverjansı",
                regime=ctx.regime,
                atr_5m=atr_5m,
                score=score,
                risk_multiplier=self._RISK_MULTIPLIER,
            )

        if (rsi_last > self._RSI_SHORT_MIN
                and last.close > upper
                and bearish_divergence(candles_5m, rsi_values, self._DIVERGENCE_LOOKBACK)):
            lookback_window = candles_5m[-self._STOP_LOOKBACK:]
            stop_price = max(c.high for c in lookback_window) * (1.0 + self._STOP_BUFFER)
            if stop_price <= ctx.current_price:
                return None

            score = (rsi_last - self._RSI_SHORT_MIN) + max(0.0, last.close - upper) / atr_5m
            return ScalpSignal(
                strategy=self.name,
                symbol=ctx.symbol,
                direction=Direction.SHORT,
                entry_price=ctx.current_price,
                stop_price=stop_price,
                reason=f"trend filtresiz tepe: RSI {rsi_last:.0f} + ayı diverjansı",
                regime=ctx.regime,
                atr_5m=atr_5m,
                score=score,
                risk_multiplier=self._RISK_MULTIPLIER,
            )

        return None


class StrategyD(StrategyProtocol):
    """D — Smart Money (para akışı + yapı kırılımı + likidite süpürmesi).

    Kamuya mal olmuş "akıllı para" (smart money) kavramlarını birleştirir:
    likidite süpürmesi (stop avı — dip/tepe ötesine kısa bir sarkma ile
    zayıf ellerin stop'larını temizleyip kapanışın tersine dönmesi), yapı
    kırılımı (BOS devam / CHoCH olası dönüş), fair value gap (3 mumlu
    fiyat dengesizliği) ve order block (kurumsal emir bloğu bölgesi) —
    bunlardan en az biri MFI/CMF para akışı teyidiyle birleşince giriş
    üretir. Rejime karşı işlem açmaz: DOWN'da LONG yok, UP'ta SHORT yok
    (güçlü akıntıya karşı yüzülmez); UNKNOWN'da hiç işlem yok.
    """

    name = "D"

    _SWING_LEFT = 3
    _SWING_RIGHT = 3
    _SWEEP_LOOKBACK = 40
    _FVG_LOOKBACK = 50
    _OB_LOOKBACK = 40
    _MFI_PERIOD = 14
    _MFI_OVERSOLD = 30.0
    _MFI_OVERBOUGHT = 70.0
    _CMF_PERIOD = 20
    _CMF_SHIFT = 4
    _STOP_MULT_LONG = 0.999
    _STOP_MULT_SHORT = 1.001
    _MIN_CANDLES = 30

    def evaluate(self, ctx: StrategyContext) -> ScalpSignal | None:
        if ctx.regime == Regime.UNKNOWN:
            return None

        candles_5m = ctx.candles_5m
        if len(candles_5m) < self._MIN_CANDLES:
            return None

        atr_5m = ctx.atr_5m
        if atr_5m <= 0.0:
            return None

        sweep = liquidity_sweep(candles_5m, self._SWING_LEFT, self._SWING_RIGHT,
                                 self._SWEEP_LOOKBACK)
        if sweep == "low":
            if ctx.regime == Regime.DOWN:
                return None
            return self._evaluate_long(ctx, candles_5m, atr_5m)
        if sweep == "high":
            if ctx.regime == Regime.UP:
                return None
            return self._evaluate_short(ctx, candles_5m, atr_5m)
        return None

    def _prior_cmf(self, candles_5m: list) -> float:
        """`_CMF_SHIFT` mum önceki CMF(period) penceresi — işaret dönüşü
        (negatiften pozitife / pozitiften negatife) tespiti için."""
        cutoff = len(candles_5m) - self._CMF_SHIFT
        if cutoff < self._CMF_PERIOD:
            return 0.0
        return cmf(candles_5m[:cutoff], self._CMF_PERIOD)

    def _evaluate_long(self, ctx: StrategyContext, candles_5m: list,
                        atr_5m: float) -> ScalpSignal | None:
        price = ctx.current_price

        ms = market_structure(candles_5m, self._SWING_LEFT, self._SWING_RIGHT)
        choch = ms["last_event"] == "CHOCH" and ms["trend"] == "bear"

        fvgs = find_fvg(candles_5m, self._FVG_LOOKBACK)
        in_fvg = any(g["direction"] == "bull" and not g["filled"]
                     and g["bottom"] <= price <= g["top"] for g in fvgs)

        ob = find_order_block(candles_5m, Direction.LONG, self._OB_LOOKBACK)
        in_ob = ob is not None and ob["low"] <= price <= ob["high"]

        if not (choch or in_fvg or in_ob):
            return None

        mfi_last = mfi_series(candles_5m, self._MFI_PERIOD)[-1]
        cmf_now = cmf(candles_5m, self._CMF_PERIOD)
        cmf_prev = self._prior_cmf(candles_5m)
        oversold = mfi_last < self._MFI_OVERSOLD
        cmf_flip = cmf_prev < 0.0 < cmf_now
        if not (oversold or cmf_flip):
            return None

        sweep_low = min(c.low for c in candles_5m[-3:])
        stop_price = sweep_low * self._STOP_MULT_LONG
        if stop_price >= price:
            return None

        confirm_labels = []
        if choch:
            confirm_labels.append("CHoCH")
        if in_fvg:
            confirm_labels.append("FVG")
        if in_ob:
            confirm_labels.append("order block")
        confirm_text = " + ".join(confirm_labels)

        score = max(0.0, self._MFI_OVERSOLD - mfi_last) + 1.0
        reason = f"SMC: likidite süpürmesi + {confirm_text} + MFI {mfi_last:.0f}"

        return ScalpSignal(
            strategy=self.name,
            symbol=ctx.symbol,
            direction=Direction.LONG,
            entry_price=price,
            stop_price=stop_price,
            reason=reason,
            regime=ctx.regime,
            atr_5m=atr_5m,
            score=score,
            risk_multiplier=1.0,
        )

    def _evaluate_short(self, ctx: StrategyContext, candles_5m: list,
                         atr_5m: float) -> ScalpSignal | None:
        price = ctx.current_price

        ms = market_structure(candles_5m, self._SWING_LEFT, self._SWING_RIGHT)
        choch = ms["last_event"] == "CHOCH" and ms["trend"] == "bull"

        fvgs = find_fvg(candles_5m, self._FVG_LOOKBACK)
        in_fvg = any(g["direction"] == "bear" and not g["filled"]
                     and g["bottom"] <= price <= g["top"] for g in fvgs)

        ob = find_order_block(candles_5m, Direction.SHORT, self._OB_LOOKBACK)
        in_ob = ob is not None and ob["low"] <= price <= ob["high"]

        if not (choch or in_fvg or in_ob):
            return None

        mfi_last = mfi_series(candles_5m, self._MFI_PERIOD)[-1]
        cmf_now = cmf(candles_5m, self._CMF_PERIOD)
        cmf_prev = self._prior_cmf(candles_5m)
        overbought = mfi_last > self._MFI_OVERBOUGHT
        cmf_flip = cmf_prev > 0.0 > cmf_now
        if not (overbought or cmf_flip):
            return None

        sweep_high = max(c.high for c in candles_5m[-3:])
        stop_price = sweep_high * self._STOP_MULT_SHORT
        if stop_price <= price:
            return None

        confirm_labels = []
        if choch:
            confirm_labels.append("CHoCH")
        if in_fvg:
            confirm_labels.append("FVG")
        if in_ob:
            confirm_labels.append("order block")
        confirm_text = " + ".join(confirm_labels)

        score = max(0.0, mfi_last - self._MFI_OVERBOUGHT) + 1.0
        reason = f"SMC: likidite süpürmesi + {confirm_text} + MFI {mfi_last:.0f}"

        return ScalpSignal(
            strategy=self.name,
            symbol=ctx.symbol,
            direction=Direction.SHORT,
            entry_price=price,
            stop_price=stop_price,
            reason=reason,
            regime=ctx.regime,
            atr_5m=atr_5m,
            score=score,
            risk_multiplier=1.0,
        )


ALL_STRATEGIES: list[StrategyProtocol] = [StrategyA(), StrategyB(), StrategyC(), StrategyD()]

_STRATEGY_BY_NAME: dict[str, StrategyProtocol] = {s.name: s for s in ALL_STRATEGIES}


def get_enabled(names_csv: str) -> list[StrategyProtocol]:
    """"A,B,C" gibi virgülle ayrılmış isimlerden etkin stratejileri filtreler.

    Boş/None girdi → boş liste (hiçbir strateji etkin değil). Bilinmeyen bir
    ad geçilirse WARNING loglanır ve o ad atlanır (akış bozulmaz). Sıra ve
    tekrar korunur: aynı ad iki kez verilirse strateji iki kez döner.
    """
    if not names_csv:
        return []

    enabled: list[StrategyProtocol] = []
    for raw_name in names_csv.split(","):
        name = raw_name.strip().upper()
        if not name:
            continue
        strategy = _STRATEGY_BY_NAME.get(name)
        if strategy is None:
            app_logger.warning(f"Bilinmeyen scalper stratejisi adı: '{name}' — atlanıyor")
            continue
        enabled.append(strategy)
    return enabled
