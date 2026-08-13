"""
4 saatlik zaman diliminden piyasa rejimi tespiti.

Saf fonksiyon: yalnız candles_4h okur, IO/saat/rastgelelik yok. Sonuç
StrategyContext.regime alanını doldurmak için data/engine katmanınca
kullanılır; setups.py bu modülü DOĞRUDAN çağırmaz (rejim girdi olarak gelir).
"""

from __future__ import annotations

from src.strategies.scalper.indicators import ema
from src.strategies.scalper.types import Candle, Regime

_EMA_SHORT_PERIOD = 50
_EMA_LONG_PERIOD = 200
_MIN_CANDLES = _EMA_LONG_PERIOD


def detect_regime(candles_4h: list[Candle]) -> Regime:
    """4h mumlarından piyasa rejimini türetir.

    < 200 mum → UNKNOWN (EMA200 seed'i için yetersiz veri; hiçbir strateji
    işlem açmaz). Yeterli veride dönemler daima EMA50 ve EMA200'dür; veri
    miktarına göre dönem değiştirilmez. Böylece farklı tarih derinlikleri
    aynı rejim tanımını kullanır.

    UP:   ema50 > ema_uzun VE son kapanış > ema50
    DOWN: ema50 < ema_uzun VE son kapanış < ema50
    aksi → RANGE
    """
    n = len(candles_4h)
    if n < _MIN_CANDLES:
        return Regime.UNKNOWN

    closes = [c.close for c in candles_4h]
    ema_short = ema(closes, _EMA_SHORT_PERIOD)[-1]
    ema_long = ema(closes, _EMA_LONG_PERIOD)[-1]
    last_close = closes[-1]

    if ema_short > ema_long and last_close > ema_short:
        return Regime.UP
    if ema_short < ema_long and last_close < ema_short:
        return Regime.DOWN
    return Regime.RANGE
