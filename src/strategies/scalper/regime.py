"""
4 saatlik zaman diliminden piyasa rejimi tespiti.

Saf fonksiyon: yalnız candles_4h okur, IO/saat/rastgelelik yok. Sonuç
StrategyContext.regime alanını doldurmak için data/engine katmanınca
kullanılır; setups.py bu modülü DOĞRUDAN çağırmaz (rejim girdi olarak gelir).
"""

from __future__ import annotations

from src.strategies.scalper.indicators import ema
from src.strategies.scalper.types import Candle, Regime

_MIN_CANDLES = 60
_EMA_SHORT_PERIOD = 50
_EMA_LONG_PERIOD = 200


def detect_regime(candles_4h: list[Candle]) -> Regime:
    """4h mumlarından piyasa rejimini türetir.

    < 60 mum → UNKNOWN (yetersiz veri; hiçbir strateji işlem açmaz).

    ema50 = ema(closes, 50)[-1]. İdeal uzun-vade referansı EMA(200)'dür;
    ancak 60-200 mum arası veri varsa (henüz 200 mumluk geçmiş birikmemiş)
    uzun EMA periyodu olarak mevcut mum sayısının yarısı kullanılır —
    pragmatik ölçekleme: `min(200, len(candles_4h) // 2)`.

    UP:   ema50 > ema_uzun VE son kapanış > ema50
    DOWN: ema50 < ema_uzun VE son kapanış < ema50
    aksi → RANGE
    """
    n = len(candles_4h)
    if n < _MIN_CANDLES:
        return Regime.UNKNOWN

    closes = [c.close for c in candles_4h]
    long_period = min(_EMA_LONG_PERIOD, n // 2)

    ema_short = ema(closes, _EMA_SHORT_PERIOD)[-1]
    ema_long = ema(closes, long_period)[-1]
    last_close = closes[-1]

    if ema_short > ema_long and last_close > ema_short:
        return Regime.UP
    if ema_short < ema_long and last_close < ema_short:
        return Regime.DOWN
    return Regime.RANGE
