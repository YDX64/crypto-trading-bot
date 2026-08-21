"""
scripts/autoresearch.py icin birim testleri — AG YOK, hicbir gercek backtest
KOSULMAZ (yalnizca saf fonksiyonlar: env-file/aday ayristirma, JSON/metin
tablo ayristirma, P2 karar kurali, docs/EXPERIMENTS.md ekleme mantigi,
resumability).

`scripts/` bir paket degil (__init__.py yok); modul dosya yoluyla `sys.path`e
eklenip adiyla import edilir.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import autoresearch as ar  # noqa: E402  (sys.path eklemesinden sonra import)


# --------------------------------------------------------------------------
# parse_env_file
# --------------------------------------------------------------------------


def test_parse_env_file_basic(tmp_path: Path) -> None:
    f = tmp_path / "env.txt"
    f.write_text(
        "\n".join(
            [
                "# yorum satiri",
                "",
                "SCALPER_C_RSI_LONG_MAX=30",
                "SCALPER_SYMBOL_ALLOWLIST=BTCUSDT,ETHUSDT,SOLUSDT",
                'SCALPER_STOP_MODE="fixed_roi"',
                "SCALPER_TF_REGIME='15m'",
                "bicimsiz_satir_esittsiz",
                "  SCALPER_TP1_ROI = 10  ",
            ]
        ),
        encoding="utf-8",
    )
    env = ar.parse_env_file(str(f))
    assert env["SCALPER_C_RSI_LONG_MAX"] == "30"
    assert env["SCALPER_SYMBOL_ALLOWLIST"] == "BTCUSDT,ETHUSDT,SOLUSDT"
    assert env["SCALPER_STOP_MODE"] == "fixed_roi"  # cift tirnak temizlendi
    assert env["SCALPER_TF_REGIME"] == "15m"  # tek tirnak temizlendi
    assert "bicimsiz_satir_esittsiz" not in env
    # "=" etrafi bosluklu satirlarda anahtar/deger trim edilir
    assert env["SCALPER_TP1_ROI"] == "10"


def test_parse_env_file_empty(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_text("# sadece yorum\n\n", encoding="utf-8")
    assert ar.parse_env_file(str(f)) == {}


# --------------------------------------------------------------------------
# load_candidates
# --------------------------------------------------------------------------


def test_load_candidates_real_file() -> None:
    candidates = ar.load_candidates(ar.DEFAULT_CANDIDATES_PATH)
    assert len(candidates) >= 10
    ids = [c.id for c in candidates]
    assert len(ids) == len(set(ids)), "aday id'leri benzersiz olmali"
    for c in candidates:
        assert c.hypothesis, f"{c.id}: hipotez bos olamaz"
        assert c.overrides, f"{c.id}: overrides bos olamaz"
        for key in c.overrides:
            assert key.startswith("SCALPER_"), f"{c.id}: {key} SCALPER_ ile baslamali"


def test_load_candidates_duplicate_id_rejected(tmp_path: Path) -> None:
    f = tmp_path / "dup.json"
    f.write_text(
        json.dumps(
            [
                {"id": "X1", "hypothesis": "a", "overrides": {"SCALPER_A": "1"}},
                {"id": "X1", "hypothesis": "b", "overrides": {"SCALPER_B": "2"}},
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        ar.load_candidates(f)


# --------------------------------------------------------------------------
# extract_metrics_from_json
# --------------------------------------------------------------------------


def test_extract_metrics_from_json_valid() -> None:
    payload = {
        "overall": {
            "trades": 191, "wins": 168, "winrate": 88.0, "total_pnl": 2798.0,
            "avg_roi": 12.34, "profit_factor": 1.24, "max_consec_losses": 3,
            "max_drawdown": 3610.0, "avg_duration_min": 45.6, "avg_mae": 1.23, "avg_mfe": 4.56,
        }
    }
    m = ar.extract_metrics_from_json(payload)
    assert m == {
        "trades": 191, "winrate": 88.0, "total_pnl": 2798.0,
        "profit_factor": 1.24, "max_drawdown": 3610.0,
    }


def test_extract_metrics_from_json_missing_overall() -> None:
    assert ar.extract_metrics_from_json({"params": {}}) is None


def test_extract_metrics_from_json_malformed_types() -> None:
    payload = {"overall": {"trades": "bilinmiyor"}}
    assert ar.extract_metrics_from_json(payload) is None


# --------------------------------------------------------------------------
# parse_report_text — backtest.py::print_report bicimine sadik ornek
# --------------------------------------------------------------------------

# backtest.py::print_report cols = [("Strateji",8),("İşlem",6),("Kazanma%",9),
# ("Toplam PnL",12),("Ort ROI%",9),("P.Faktör",9),("Mks Ardş.Kyp",13),
# ("Mks DD",10),("Ort Süre(dk)",13),("Ort MAE%",9),("Ort MFE%",9)] — _row()
# her degeri bu genislige ljust'lar ve " | " ile birlestirir.
_SAMPLE_REPORT_TEXT = """
=====================================================================================================================
SCALPER BACKTEST RAPORU — Strateji Karşılaştırması
=====================================================================================================================
Strateji | İşlem  | Kazanma% | Toplam PnL   | Ort ROI% | P.Faktör  | Mks Ardş.Kyp | Mks DD     | Ort Süre(dk) | Ort MAE% | Ort MFE%
---------------------------------------------------------------------------------------------------------------------
C        | 191    | 88.0     | 2798.00      | 12.34    | 1.24      | 3            | 3610.00    | 45.6         | 1.23     | 4.56
---------------------------------------------------------------------------------------------------------------------
TOPLAM   | 191    | 88.0     | 2798.00      | 12.34    | 1.24      | 3            | 3610.00    | 45.6         | 1.23     | 4.56
=====================================================================================================================

Kapılarda reddedilen/kaçan sinyal: 12 {'regime_gate': 12}
"""


def test_parse_report_text_toplam_row() -> None:
    m = ar.parse_report_text(_SAMPLE_REPORT_TEXT)
    assert m == {
        "trades": 191, "winrate": 88.0, "total_pnl": 2798.0,
        "profit_factor": 1.24, "max_drawdown": 3610.0,
    }


def test_parse_report_text_inf_profit_factor() -> None:
    text = "TOPLAM   | 5      | 100.0    | 500.00       | 8.00     | inf       | 0            | 0.00       | 10.0         | 0.50     | 2.00"
    m = ar.parse_report_text(text)
    assert m is not None
    assert m["profit_factor"] == float("inf")


def test_parse_report_text_no_toplam_row() -> None:
    text = "sadece rastgele metin\nTOPLAM_DEGIL | 1 | 2\n"
    assert ar.parse_report_text(text) is None


# --------------------------------------------------------------------------
# decide() — docs/DECISIONS.md #P2, tablo-guduml
# --------------------------------------------------------------------------


def _metrics(trades: int, pnl: float, pf: float) -> dict:
    return {"trades": trades, "winrate": 80.0, "total_pnl": pnl, "profit_factor": pf, "max_drawdown": 100.0}


_BASELINE = {
    "AYI": _metrics(200, -2042.0, 0.97),
    "YATAY": _metrics(150, -2289.0, 0.93),
    "BOGA": _metrics(100, 2798.0, 1.24),
}


def test_decide_candidate_via_pf_threshold() -> None:
    variant = {
        "AYI": _metrics(200, 886.0, 1.10),  # PF esigi tam sinirda
        "YATAY": _metrics(150, -2000.0, 0.95),  # onemsiz, gate1 zaten PF ile saglandi
        "BOGA": _metrics(100, 2798.0, 1.24),  # dusus yok
    }
    decision, reason, score = ar.decide(_BASELINE, variant)
    assert decision == "ADAY"
    assert "PF" in reason
    assert score == pytest.approx((886.0 - -2042.0) + (-2000.0 - -2289.0) + (2798.0 - 2798.0))


def test_decide_candidate_via_both_improve() -> None:
    variant = {
        "AYI": _metrics(200, -1000.0, 1.05),  # PF<1.1 ama iyilesti
        "YATAY": _metrics(150, -1500.0, 1.00),  # iyilesti
        "BOGA": _metrics(100, 2798.0, 1.24),
    }
    decision, reason, _ = ar.decide(_BASELINE, variant)
    assert decision == "ADAY"
    assert "birlikte" in reason


def test_decide_rejects_on_boga_drop_over_20pct() -> None:
    variant = {
        "AYI": _metrics(200, 886.0, 1.20),  # PF gate saglandi
        "YATAY": _metrics(150, -2000.0, 0.95),
        "BOGA": _metrics(100, 2798.0 * 0.75, 0.90),  # %25 dusus > %20 tavan
    }
    decision, reason, _ = ar.decide(_BASELINE, variant)
    assert decision == "REDDEDILDI"
    assert "BOGA" in reason


def test_decide_rejects_when_neither_gate_condition_holds() -> None:
    variant = {
        "AYI": _metrics(200, -3000.0, 0.80),  # PF<1.1 VE kotulesti
        "YATAY": _metrics(150, -2500.0, 0.85),  # kotulesti
        "BOGA": _metrics(100, 2798.0, 1.24),
    }
    decision, reason, _ = ar.decide(_BASELINE, variant)
    assert decision == "REDDEDILDI"
    assert "AYI/YATAY birlikte iyilesmedi" in reason


def test_decide_rejects_low_sample_even_if_metrics_would_pass() -> None:
    """Asiri filtreleme kapisi: PF cok yuksek olsa bile <60 islemli pencere
    varsa reddedilir (bkz. reddedilen E2ab: AYI 3.35/+1498 ama 31 islem)."""
    variant = {
        "AYI": _metrics(31, 1498.0, 3.35),  # ornek < 60
        "YATAY": _metrics(150, 5000.0, 2.0),
        "BOGA": _metrics(100, 5000.0, 2.0),
    }
    decision, reason, score = ar.decide(_BASELINE, variant)
    assert decision == "REDDEDILDI"
    assert "asiri filtreleme" in reason
    assert score == 0.0


def test_decide_boga_baseline_non_positive_edge_case() -> None:
    baseline = dict(_BASELINE)
    baseline["BOGA"] = _metrics(100, 0.0, 1.0)  # taban BOGA PnL = 0
    variant_ok = {
        "AYI": _metrics(200, 886.0, 1.20),
        "YATAY": _metrics(150, -2000.0, 0.95),
        "BOGA": _metrics(100, 0.0, 1.0),  # gerilemedi -> gecer
    }
    decision, _, _ = ar.decide(baseline, variant_ok)
    assert decision == "ADAY"

    variant_worse = {
        "AYI": _metrics(200, 886.0, 1.20),
        "YATAY": _metrics(150, -2000.0, 0.95),
        "BOGA": _metrics(100, -50.0, 0.80),  # gerilemis -> reddedilir
    }
    decision2, reason2, _ = ar.decide(baseline, variant_worse)
    assert decision2 == "REDDEDILDI"
    assert "BOGA" in reason2


def test_decide_missing_window_is_hata() -> None:
    variant = {"AYI": _metrics(200, 100.0, 1.5), "YATAY": _metrics(150, 100.0, 1.5)}
    decision, reason, score = ar.decide(_BASELINE, variant)
    assert decision == "HATA"
    assert "BOGA" in reason
    assert score == 0.0


# --------------------------------------------------------------------------
# upsert_experiments_section — saf metin manipulasyonu
# --------------------------------------------------------------------------


def test_upsert_creates_new_section_when_heading_absent() -> None:
    content = "# Deney defteri\n\nbaska icerik\n"
    rows = ["| E4a | AYI | 100 | 80.0 | +500.00 | 1.20 | 10.00 | - |"]
    updated = ar.upsert_experiments_section(content, "2026-08-22", rows)
    assert "## 2026-08-22 — Autoresearch (scripts/autoresearch.py)" in updated
    assert rows[0] in updated
    assert updated.index("baska icerik") < updated.index(rows[0])


def test_upsert_appends_rows_under_existing_heading_at_eof() -> None:
    content = (
        "# Deney defteri\n\n"
        "## 2026-08-22 — Autoresearch (scripts/autoresearch.py)\n"
        "Kaynak: `python3 scripts/autoresearch.py` — otomatik uretildi, elle duzenlemeyin.\n"
        "| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| E4a | AYI | 100 | 80.0 | +500.00 | 1.20 | 10.00 | - |\n"
    )
    new_rows = ["| E4a | YATAY | 90 | 78.0 | +300.00 | 1.10 | 20.00 | - |"]
    updated = ar.upsert_experiments_section(content, "2026-08-22", new_rows)
    # baslik/tablo basligi TEKRARLANMADI
    assert updated.count("## 2026-08-22 — Autoresearch") == 1
    assert updated.count("| Varyant | Pencere |") == 1
    assert new_rows[0] in updated
    assert "| E4a | AYI |" in updated  # eski satir korunuyor


def test_upsert_inserts_before_next_heading_not_after() -> None:
    content = (
        "# Deney defteri\n\n"
        "## 2026-08-22 — Autoresearch (scripts/autoresearch.py)\n"
        "| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| E4a | AYI | 100 | 80.0 | +500.00 | 1.20 | 10.00 | - |\n"
        "\n"
        "## Baska bir bolum (bundan sonra gelir)\n"
        "bu bolum degismemeli\n"
    )
    new_rows = ["| E4a | YATAY | 90 | 78.0 | +300.00 | 1.10 | 20.00 | - |"]
    updated = ar.upsert_experiments_section(content, "2026-08-22", new_rows)
    assert updated.index(new_rows[0]) < updated.index("## Baska bir bolum")
    assert "bu bolum degismemeli" in updated


def test_upsert_is_cumulative_across_two_calls() -> None:
    content = "# Deney defteri\n"
    r1 = ["| E4a | AYI | 100 | 80.0 | +500.00 | 1.20 | 10.00 | - |"]
    r2 = ["| E4a | YATAY | 90 | 78.0 | +300.00 | 1.10 | 20.00 | - |"]
    after1 = ar.upsert_experiments_section(content, "2026-08-22", r1)
    after2 = ar.upsert_experiments_section(after1, "2026-08-22", r2)
    assert after2.count("## 2026-08-22 — Autoresearch") == 1
    assert r1[0] in after2
    assert r2[0] in after2


# --------------------------------------------------------------------------
# should_skip_candidate — resumability
# --------------------------------------------------------------------------


def test_should_skip_candidate_absent() -> None:
    summary = {"candidates": {}}
    assert ar.should_skip_candidate(summary, "E4a") is False


def test_should_skip_candidate_ok_status() -> None:
    summary = {"candidates": {"E4a": {"status": "ok"}}}
    assert ar.should_skip_candidate(summary, "E4a") is True


def test_should_skip_candidate_hata_status_retries() -> None:
    summary = {"candidates": {"E4a": {"status": "hata"}}}
    assert ar.should_skip_candidate(summary, "E4a") is False


# --------------------------------------------------------------------------
# format_variant_rows — sanity
# --------------------------------------------------------------------------


def test_format_variant_rows_shape() -> None:
    windows_result = {
        "AYI": {"trades": 100, "winrate": 80.0, "total_pnl": 500.0, "profit_factor": 1.2, "max_drawdown": 10.0},
        "YATAY": {"trades": 90, "winrate": 78.0, "total_pnl": 300.0, "profit_factor": 1.1, "max_drawdown": 20.0},
        "BOGA": {"trades": 80, "winrate": 85.0, "total_pnl": 200.0, "profit_factor": 1.3, "max_drawdown": 5.0},
    }
    rows = ar.format_variant_rows("E4a", "test hipotezi", windows_result, "ADAY", "AYI PF=1.20", 42.0)
    # 3 pencere satiri + 1 karar satiri + 1 hipotez satiri
    assert len(rows) == 5
    assert all(r.startswith("| E4a |") for r in rows)
    assert any("KARAR" in r and "ADAY" in r for r in rows)
