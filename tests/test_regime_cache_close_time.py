"""Madde 9a — rejim önbelleği `close_time` anahtarlı (zaman TTL'i DEĞİL).

Harness (`backtest.build_context`) rejimi her barda taze hesaplar; canlı motor
eskiden sembol başına 300 sn önbellekliyordu (15m rejim diliminde mumun üçte
biri kadar bayatlık). Şimdi önbellek, son KAPANMIŞ rejim mumunun `close_time`'ı
aynı kaldığı sürece geçerlidir ve mum değişince hemen yenilenir.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.strategies.scalper import engine as engine_module
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.types import Candle, Regime


def _candles(n: int, last_close_time: int, *, close: float = 100.0):
    out = []
    for i in range(n):
        out.append(
            Candle(
                open_time=1_000 + i, open=close, high=close, low=close,
                close=close, volume=1.0, close_time=1_500 + i,
            )
        )
    last = out[-1]
    out[-1] = Candle(
        open_time=last.open_time, open=close, high=close, low=close,
        close=close, volume=1.0, close_time=last_close_time,
    )
    return out


def _engine():
    engine = ScalperEngine.__new__(ScalperEngine)
    engine._regime_cache = {}
    return engine


def _count_detect(monkeypatch, results):
    calls = []

    def fake(candles):
        calls.append(candles[-1].close_time if candles else None)
        return results[min(len(calls) - 1, len(results) - 1)]

    monkeypatch.setattr(engine_module, "detect_regime", fake)
    return calls


def test_ayni_close_time_icin_yeniden_hesaplanmaz(monkeypatch):
    calls = _count_detect(monkeypatch, [Regime.UP, Regime.DOWN])
    engine = _engine()
    candles = _candles(5, 10_000)

    assert engine._get_cached_regime("BTCUSDT", candles) is Regime.UP
    # Aynı kapanmış mum → eski rejim (detect_regime saf: sonuç aynı olurdu).
    assert engine._get_cached_regime("BTCUSDT", list(candles)) is Regime.UP
    assert calls == [10_000]


def test_close_time_degisince_hemen_yenilenir(monkeypatch):
    """Eski 300 sn TTL'de yeni mum kapansa da rejim 5 dk'ya kadar eski kalırdı."""
    calls = _count_detect(monkeypatch, [Regime.UP, Regime.DOWN])
    engine = _engine()

    assert engine._get_cached_regime("BTCUSDT", _candles(5, 10_000)) is Regime.UP
    # Saniyeler sonra YENİ 15m mum kapandı (zaman TTL'i dolmadan).
    assert engine._get_cached_regime("BTCUSDT", _candles(5, 10_900)) is Regime.DOWN
    assert calls == [10_000, 10_900]
    # Aynı yeni mum için tekrar çağrı: önbellekten.
    assert engine._get_cached_regime("BTCUSDT", _candles(5, 10_900)) is Regime.DOWN
    assert calls == [10_000, 10_900]


def test_zaman_gecmesi_onbellegi_gecersiz_kilmaz(monkeypatch):
    """Anahtar zaman değil mumdur: çok zaman geçse de aynı mum → aynı rejim."""
    calls = _count_detect(monkeypatch, [Regime.RANGE, Regime.UP])
    engine = _engine()
    candles = _candles(5, 10_000)
    engine._get_cached_regime("BTCUSDT", candles)

    real_monotonic = engine_module.time.monotonic
    monkeypatch.setattr(
        engine_module.time, "monotonic", lambda: real_monotonic() + 10_000.0
    )
    assert engine._get_cached_regime("BTCUSDT", candles) is Regime.RANGE
    assert calls == [10_000]


def test_semboller_birbirinin_onbellegini_kullanmaz(monkeypatch):
    calls = _count_detect(monkeypatch, [Regime.UP, Regime.DOWN])
    engine = _engine()
    candles = _candles(5, 10_000)
    assert engine._get_cached_regime("BTCUSDT", candles) is Regime.UP
    assert engine._get_cached_regime("ETHUSDT", candles) is Regime.DOWN
    assert len(calls) == 2


def test_close_time_okunamazsa_onbellege_yazilmaz_her_cagri_taze(monkeypatch):
    _count_detect(monkeypatch, [Regime.UP, Regime.DOWN])
    engine = _engine()
    broken = [SimpleNamespace(close=100.0, close_time=None)]
    assert engine._get_cached_regime("BTCUSDT", broken) is Regime.UP
    assert engine._get_cached_regime("BTCUSDT", broken) is Regime.DOWN
    assert engine._regime_cache == {}
    assert engine._get_cached_regime("BTCUSDT", []) is Regime.DOWN


def test_gercek_detect_regime_ile_yeni_mum_rejimi_degistirir():
    """Sahte yok: gerçek EMA hesabı. Uzun düşüş → DOWN; son mum fırlayınca
    (yeni close_time) rejim önbellekten DEĞİL taze hesaplanır."""
    engine = _engine()
    closes = [200.0 - i * 0.5 for i in range(260)]  # sürekli düşüş

    def mk(values):
        return [
            Candle(open_time=i * 900_000, open=v, high=v, low=v, close=v,
                   volume=1.0, close_time=i * 900_000 + 899_999)
            for i, v in enumerate(values)
        ]

    down = mk(closes)
    assert engine._get_cached_regime("BTCUSDT", down) is Regime.DOWN
    # Yeni mum kapandı: fiyat EMA50'nin çok üstüne fırladı → artık DOWN değil.
    spiked = mk(closes + [500.0])
    assert engine._get_cached_regime("BTCUSDT", spiked) is not Regime.DOWN
