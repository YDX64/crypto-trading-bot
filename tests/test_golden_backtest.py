"""
Altın (golden) backtest regresyon testi — src/strategies/scalper/backtest.py.

Amaç: backtest MOTORUNU (simulate_symbol → open_position → manage_position
zinciri) sabit bir mum verisi + sabit ayar kümesiyle koşup çıktının HER
zaman aynı kalacağını doğrulamak. Herhangi bir davranış değişikliği
(kasıtlı ya da kaza) buradaki sayıları oynatır — test bunu yakalar.

AĞ YOK: tests/fixtures/klines/ altındaki gzip JSON mum serileri
kline_cache ile (backtest.py'nin --cache-dir mekanizmasıyla BİREBİR aynı
kod yolu) diskten okunur. Ağa gerçekten hiç çıkılmadığından emin olmak
için KlineFetcher, çağrılırsa İSTİSNA fırlatan sahte bir sınıfla
değiştirilir (bkz. _NetworkForbiddenKlineFetcher) — önbellek eksik/yanlış
olsaydı bu test sessizce ağa düşmek yerine gürültülü biçimde patlar.

Fixture: BTCUSDT + ETHUSDT, pencere 2026-08-07→2026-08-10 (UTC, 3 gün,
[start,end)). Yalnız 5m (giriş) ve 15m (bağlam VE rejim — aşağıya bakın)
serileri gerekir: bu golden koşu scalper_tf_regime="15m" kullanır (canlı
config'in denendiği bir varyant), bu yüzden rejim serisi de "15m" anahtarı
altında context ile birleşir (backtest.gather_symbol_data'nın `out` sözlüğü
aralık ADIYLA anahtarlanır — iki rol aynı aralığı paylaşırsa aynı slotu
paylaşır, daha büyük warm-up kazanır). 1m HİÇ kullanılmaz: dolum
open_position()'da candles_5m[entry_idx].open ile yapılır, tik-bazlı değil
— bu harness için 1m veri gereksiz (fixture'ı küçük tutar).

Ayarlar: ne .env ne de process ortamı okunur — hem `cfg` (dataclass, aşağı
bakın) hem de src.core.config.settings TEKİL nesnesi (StrategyC.evaluate
bunu doğrudan okur, cfg'den DEĞİL — bkz. setups.py) monkeypatch ile
AÇIKÇA sabitlenir. Belirtilmeyen alanlar src/core/config.py'deki sınıf
varsayılanlarıyla aynıdır (yorumlarda işaretli).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pytest

import src.strategies.scalper.backtest as backtest_module
from src.strategies.scalper import setups as setups_module
from src.strategies.scalper.backtest import (
    BacktestTrade,
    _parse_utc_date,
    compute_stats,
    run_backtest,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "klines"
_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
_START = "2026-08-07"
_END = "2026-08-10"  # [start, end) UTC — 3 gün, resolve_backtest_window ile aynı


class _NetworkForbiddenKlineFetcher:
    """KlineFetcher yerine geçer: çağrılırsa (önbellek eksik/yanlış demektir)
    sessizce ağa düşmek yerine gürültülü biçimde patlar — bu test AĞSIZ
    çalışmalı, aksi hâlde hem yavaş hem deterministik olmaz."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def get_klines(self, symbol, interval, limit, end_time=None):
        raise AssertionError(
            f"Golden test AĞA düşmemeli (önbellek eksik/uyuşmuyor olabilir): "
            f"{symbol} {interval} limit={limit} end_time={end_time}"
        )

    async def close(self) -> None:
        return None


@dataclass
class _GoldenCfg:
    """Bugünkü (2026-08-21) canlı .env'i yansıtan SABİT ayar kümesi —
    görev talimatındaki listeyle birebir. Listelenmeyen alanlar
    src/core/config.py sınıf varsayılanlarıdır (yorumlu).
    """

    # Boyutlama / kaldıraç
    scalper_risk_percentage: float = 2.0          # varsayılan
    scalper_leverage: int = 20                    # varsayılan (dyn lev aktifken taban)
    scalper_max_margin_pct: float = 10.0           # varsayılan

    # TP/SL
    scalper_tp1_roi: float = 10.0                  # görev: "tp1 10"
    scalper_tp1_fraction: float = 0.40             # varsayılan (görev tp1 fraction belirtmedi)
    scalper_tp2_roi: float = 25.0                  # görev: "tp2 25"
    scalper_tp2_fraction: float = 0.20             # görev: "tp2 fraction 0.20"
    scalper_min_stop_pct: float = 0.15             # varsayılan
    scalper_max_stop_pct: float = 3.0              # varsayılan
    scalper_breakeven_buffer_pct: float = 0.05     # varsayılan
    # DİKKAT — sınıf varsayılanı (1.2) DEĞİL, BİLİNÇLİ 0.0 (kapı kapalı):
    # open_position()'daki min_rr kapısı fixed_roi modunda sl_risk_roi'yi
    # HER ZAMAN tam olarak scalper_fixed_stop_roi_pct'e eşitler (kaldıraç
    # matematiksel olarak sadeleşir — bkz. apply_stop_policy: distance =
    # entry*(roi_pct/100/leverage), stop_distance_pct*leverage = roi_pct).
    # Bu yüzden expected_roi = tp1_roi*(1-tp2_frac) + tp2_roi*tp2_frac
    # = 10*0.8 + 25*0.2 = 13 SABİTTİR (tp1_fraction'dan bağımsız — runner
    # de tp1_roi varsayıyor) ve rr = 13/50 = 0.26 HER ZAMAN — piyasa
    # verisinden TAMAMEN bağımsız bir sabit. scalper_min_rr sınıf
    # varsayılanı 1.2 ile bu kombinasyon HİÇBİR C sinyalinin asla
    # geçmeyeceği anlamına gelir (ne backtest'te ne canlıda) — bu,
    # CLAUDE.md'nin belgelediği canlı işlem geçmişiyle (Başabaş kazanma
    # oranı ≈%85) ÇELİŞİR. Gerçek .env'de SCALPER_MIN_RR'nin ≤0.26
    # (muhtemelen 0/kapalı) olduğu varsayımıyla burada 0.0 kullanıldı —
    # BU BULGU raporlanmalı, gerçek .env ile doğrulanmalı.
    scalper_min_rr: float = 0.0

    # Chandelier trail
    scalper_chandelier_atr_mult: float = 3.5       # görev: "chandelier 3.5"
    scalper_chandelier_atr_period: int = 14        # varsayılan
    scalper_trail_relax_roi1_pct: float = 0.0      # varsayılan (0 = kademe kapalı)
    scalper_trail_relax_mult1: float = 5.0         # varsayılan
    scalper_trail_relax_roi2_pct: float = 150.0    # varsayılan
    scalper_trail_relax_mult2: float = 7.0         # varsayılan

    # Giriş / komisyon
    scalper_entry_mode: str = "taker"              # varsayılan
    scalper_taker_fee_pct: float = 0.05            # varsayılan
    scalper_maker_fee_pct: float = 0.02            # varsayılan
    scalper_maker_fill_timeout_candles: int = 3    # varsayılan

    # Rejim / zaman dilimleri
    scalper_regime_filter: bool = True             # görev: "regime filter on"
    scalper_tf_entry: str = "5m"                   # varsayılan
    scalper_tf_context: str = "15m"                # varsayılan
    scalper_tf_regime: str = "15m"                 # görev: "tf_regime 15m"

    # Stop politikası
    scalper_stop_mode: str = "fixed_roi"           # görev: "fixed_roi stop 50"
    scalper_fixed_stop_roi_pct: float = 50.0       # görev: "fixed_roi stop 50"
    scalper_stop_atr_floor_mult: float = 0.5       # görev: "stop atr floor 0.5"
    #   NOT: stop_mode="fixed_roi" iken apply_stop_policy() ATR tabanını hiç
    #   çağırmaz (yalnız "structural" modda devreye girer) — bu alan burada
    #   KAYIT amaçlı, golden sonuçları ETKİLEMEZ (setups.apply_stop_policy).

    # Dinamik kaldıraç
    scalper_dynamic_leverage: bool = True          # görev: "dyn lev 3-20"
    scalper_dyn_lev_stop_atr_mult: float = 3.0     # varsayılan
    scalper_dyn_lev_min: int = 3                   # görev: "dyn lev 3-20"
    scalper_dyn_lev_max: int = 20                  # görev: "dyn lev 3-20"

    # Kayıp sonrası soğuma
    scalper_loss_cooldown_minutes: float = 60.0    # görev: "loss cooldown 60"

    # Yalnız KAYIT amaçlı — backtest.simulate_symbol/manage_position bu
    # alanları OKUMAZ (yalnız canlı engine.py kullanır); golden sonuçları
    # ETKİLEMEZLER. Görev talimatındaki "mirror" listesiyle tutarlılık için
    # burada tutuluyorlar.
    scalper_max_hold_hours: float = 8.0            # görev: "max hold 8h" — İNERT (bkz. yukarı)
    scalper_symbol_allowlist: str = "BTCUSDT,ETHUSDT"  # görev: "symbol allowlist" — İNERT


def _apply_golden_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """StrategyC.evaluate (ve passes_* kapıları) settings'i CFG'DEN DEĞİL,
    src.core.config'teki TEKİL `settings` nesnesinden doğrudan okur (bkz.
    setups.py başlık yorumu) — bu yüzden cfg'yi ayarlamak yetmez, aynı
    TEKİL nesne üzerinde monkeypatch şart (aksi halde makinedeki gerçek
    .env sızar ve test deterministik olmaktan çıkar)."""
    monkeypatch.setattr(setups_module.settings, "scalper_c_rsi_long_max", 30.0)   # görev: "C rsi 30/70"
    monkeypatch.setattr(setups_module.settings, "scalper_c_rsi_short_min", 70.0)  # görev: "C rsi 30/70"
    monkeypatch.setattr(setups_module.settings, "scalper_c_require_divergence", True)  # görev
    monkeypatch.setattr(setups_module.settings, "scalper_c_allowed_regimes", "UP,DOWN,RANGE")  # varsayılan
    monkeypatch.setattr(setups_module.settings, "scalper_use_equilibrium_filter", True)  # varsayılan
    monkeypatch.setattr(setups_module.settings, "scalper_c_require_flow_confirm", False)  # varsayılan
    monkeypatch.setattr(setups_module.settings, "scalper_c_require_reversal_zone", False)  # varsayılan


def _direction_exit_counts(trades: List[BacktestTrade]) -> Dict[str, Dict[str, int]]:
    directions: Dict[str, int] = {}
    exits: Dict[str, int] = {}
    for t in trades:
        directions[t.direction] = directions.get(t.direction, 0) + 1
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    return {"direction": directions, "exit_reason": exits}


async def _run_golden(monkeypatch: pytest.MonkeyPatch) -> tuple[List[BacktestTrade], Dict[str, int]]:
    _apply_golden_settings(monkeypatch)
    monkeypatch.setattr(backtest_module, "KlineFetcher", _NetworkForbiddenKlineFetcher)

    start_ms = _parse_utc_date(_START)
    end_ms = _parse_utc_date(_END)
    missed_counter: Dict[str, int] = {}

    trades = await run_backtest(
        days=3,
        symbols=_SYMBOLS,
        strategy_names="C",
        cfg=_GoldenCfg(),
        missed_counter=missed_counter,
        start_time_ms=start_ms,
        end_time_ms=end_ms,
        cache_dir=_FIXTURE_DIR,
        refresh=False,
        base_url="https://network-must-not-be-used.invalid",
    )
    return trades, missed_counter


class TestGoldenBacktest:
    """Sabit fixture + sabit ayar → sabit sonuç. Sayılar İLK koşuda
    üretilip buraya kopyalanmıştır (bkz. docs/EXPERIMENTS.md "Altın
    backtest" bölümü). BİLİNÇLİ bir motor/ayar değişikliği bu sayıları
    değiştirirse: yeni değerleri burada güncelle VE docs/EXPERIMENTS.md'ye
    ne/neden/kanıt olarak not düş (CLAUDE.md sözleşmesi)."""

    @pytest.mark.asyncio
    async def test_golden_trade_counts_and_pnl(self, monkeypatch):
        trades, missed_counter = await _run_golden(monkeypatch)

        # altın değerler — bilinçli değişiklikte güncelle, docs/EXPERIMENTS.md'ye not düş
        # (bu pencerede yalnız BTCUSDT işlem üretir; ETHUSDT'de RSI 30/70 +
        # zorunlu diverjans + BB taşması üçlüsü hiç üst üste binmez — bkz.
        # docs/EXPERIMENTS.md "Altın backtest" notu, bu bir hata DEĞİL)
        assert len(trades) == 2
        stats = compute_stats(trades)
        assert round(stats["total_pnl"], 2) == 26.77

        breakdown = _direction_exit_counts(trades)
        assert breakdown["direction"] == {"LONG": 2}
        assert breakdown["exit_reason"] == {"TRAIL": 2}

        assert missed_counter == {"regime_gate": 4}

    @pytest.mark.asyncio
    async def test_golden_is_deterministic_across_runs(self, monkeypatch):
        """Aynı fixture + aynı ayar iki farklı koşuda BİREBİR aynı sonucu
        vermeli — dict sıralaması / gizli zaman bağımlılığı yok demektir."""
        trades_a, missed_a = await _run_golden(monkeypatch)
        trades_b, missed_b = await _run_golden(monkeypatch)

        def _fingerprint(trades: List[BacktestTrade]) -> list:
            return [
                (
                    t.symbol, t.direction, round(t.entry_price, 8),
                    t.entry_time, round(t.exit_price, 8), t.exit_time,
                    t.exit_reason, round(t.pnl, 8), round(t.roi_pct, 8),
                )
                for t in trades
            ]

        assert _fingerprint(trades_a) == _fingerprint(trades_b)
        assert missed_a == missed_b

    @pytest.mark.asyncio
    async def test_golden_runs_offline_under_20_seconds(self, monkeypatch):
        started = time.monotonic()
        trades, _ = await _run_golden(monkeypatch)
        elapsed = time.monotonic() - started

        assert trades  # fixture boş dönerse ayar/pencere sessizce bozulmuş olabilir
        assert elapsed < 20.0, f"Golden backtest {elapsed:.1f}s sürdü (>20s, AĞSIZ olmalı)"
