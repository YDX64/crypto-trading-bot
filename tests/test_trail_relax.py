"""Kademeli gevşeyen iz (resolve_trail_mult) birim testleri.

Sözleşme: kademe TEPE ROI'ye (high-water mark) bağlıdır ve tek yönlüdür;
roi1<=0 özelliği kapatır ve temel chandelier çarpanı aynen kullanılır.
Canlı exits._update_trailing ve backtest._update_trailing aynı fonksiyonu
çağırır — bu testler o ortak sözleşmeyi sabitler.
"""

from types import SimpleNamespace

from src.strategies.scalper.types import resolve_trail_mult


def _cfg(**over):
    base = dict(
        scalper_chandelier_atr_mult=3.5,
        scalper_trail_relax_roi1_pct=50.0,
        scalper_trail_relax_mult1=5.0,
        scalper_trail_relax_roi2_pct=150.0,
        scalper_trail_relax_mult2=7.0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_feature_off_returns_base_mult():
    cfg = _cfg(scalper_trail_relax_roi1_pct=0.0)
    assert resolve_trail_mult(cfg, 0.0) == 3.5
    assert resolve_trail_mult(cfg, 999.0) == 3.5  # kapalıyken tepe ROI önemsiz


def test_below_first_threshold_uses_base():
    cfg = _cfg()
    assert resolve_trail_mult(cfg, 0.0) == 3.5
    assert resolve_trail_mult(cfg, 49.9) == 3.5


def test_between_thresholds_uses_mult1():
    cfg = _cfg()
    assert resolve_trail_mult(cfg, 50.0) == 5.0
    assert resolve_trail_mult(cfg, 149.9) == 5.0


def test_above_second_threshold_uses_mult2():
    cfg = _cfg()
    assert resolve_trail_mult(cfg, 150.0) == 7.0
    assert resolve_trail_mult(cfg, 1000.0) == 7.0  # %1000 koşucusu geniş izde


def test_invalid_roi2_falls_back_to_mult1():
    # roi2 <= roi1 tutarsız yapılandırma: ikinci kademe yok sayılır
    cfg = _cfg(scalper_trail_relax_roi2_pct=40.0)
    assert resolve_trail_mult(cfg, 200.0) == 5.0


def test_missing_fields_on_fake_cfg_default_off():
    # Test fake'leri (SimpleNamespace) alanları hiç tanımlamayabilir:
    # getattr default'ları özelliği kapalı sayıp temel çarpanı döndürmeli.
    cfg = SimpleNamespace(scalper_chandelier_atr_mult=2.5)
    assert resolve_trail_mult(cfg, 500.0) == 2.5
