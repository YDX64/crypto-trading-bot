"""D24/A2 — Benjamini-Hochberg FDR düzeltmesi testleri.

Doğruluk, literatürden BİLİNEN bir p-değeri vektörüyle sabitlenir
(Benjamini & Hochberg 1995, Tablo 1 — m=15 uyku/hipertansiyon örneği):
alpha=0.05 ile ilk DÖRT hipotez reddedilir.
"""

from __future__ import annotations

import json

import pytest

from src.strategies.scalper.multitest import (
    DEFAULT_ALPHA,
    benjamini_hochberg,
    fdr_report,
    main,
    render_report,
)

#: Benjamini & Hochberg (1995) makalesindeki 15 p-değeri.
_BH1995 = [
    0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344,
    0.0459, 0.3240, 0.4262, 0.5719, 0.6528, 0.7590, 1.0000,
]


class TestBenjaminiHochberg:
    def test_known_vector_rejects_first_four(self):
        rows = benjamini_hochberg(_BH1995, alpha=0.05)
        rejected = [r for r in rows if r["rejected"]]
        assert len(rejected) == 4
        assert sorted(r["rank"] for r in rejected) == [1, 2, 3, 4]
        # q = p * m / rank, monotonluk düzeltmesiyle
        assert rows[0]["q_value"] == pytest.approx(0.0001 * 15 / 1, abs=1e-6)
        assert rows[3]["q_value"] == pytest.approx(0.0095 * 15 / 4, abs=1e-6)

    def test_q_values_are_monotone_in_rank(self):
        rows = sorted(benjamini_hochberg(_BH1995), key=lambda r: r["rank"])
        qs = [r["q_value"] for r in rows]
        assert qs == sorted(qs)
        assert all(0.0 <= q <= 1.0 for q in qs)

    def test_input_order_is_preserved(self):
        shuffled = [0.5, 0.0001, 0.9, 0.0004]
        rows = benjamini_hochberg(shuffled)
        assert [r["index"] for r in rows] == [0, 1, 2, 3]
        assert [r["p_value"] for r in rows] == shuffled
        assert rows[1]["rank"] == 1
        assert rows[2]["rank"] == 4

    def test_single_test_leaves_p_unchanged(self):
        rows = benjamini_hochberg([0.03], alpha=0.05)
        assert rows[0]["q_value"] == pytest.approx(0.03)
        assert rows[0]["rejected"] is True

    def test_all_equal_p_values(self):
        rows = benjamini_hochberg([0.04] * 5, alpha=0.05)
        # q = 0.04 * 5 / 5 = 0.04 (monotonluk hepsini aynı yapar)
        assert all(r["q_value"] == pytest.approx(0.04) for r in rows)
        assert all(r["rejected"] for r in rows)

    def test_empty_input(self):
        assert benjamini_hochberg([]) == []

    def test_malformed_p_values_treated_as_one(self):
        rows = benjamini_hochberg([None, "abc", float("nan"), 1.5, -0.2])
        assert rows[0]["p_value"] == 1.0
        assert rows[1]["p_value"] == 1.0
        assert rows[2]["p_value"] == 1.0
        assert rows[3]["p_value"] == 1.0   # 1.5 -> kırpılır
        assert rows[4]["p_value"] == 0.0   # -0.2 -> kırpılır
        assert all(not r["rejected"] for r in rows[:4])

    def test_deterministic_across_calls(self):
        assert benjamini_hochberg(_BH1995) == benjamini_hochberg(_BH1995)


class TestFdrReport:
    def _rows(self):
        return [
            {"name": "E9a", "p_value": 0.001, "pnl": 120.0},
            {"name": "E9b", "p_value": 0.04},
            {"name": "E9c", "p_value": 0.30},
            {"name": "E9d", "p_value": 0.80},
        ]

    def test_shape_and_extra_fields_preserved(self):
        report = fdr_report(self._rows(), alpha=0.10)
        assert report["tests"] == 4
        assert report["alpha"] == 0.10
        assert report["rows"][0]["name"] == "E9a"
        assert report["rows"][0]["pnl"] == 120.0
        assert report["rows"][0]["rank"] == 1
        assert report["rejected"] >= 1
        assert "korelasyonlu" in report["caveat"]

    def test_rows_sorted_by_rank(self):
        report = fdr_report(self._rows())
        assert [r["rank"] for r in report["rows"]] == [1, 2, 3, 4]

    def test_effective_tests_column(self):
        report = fdr_report(self._rows(), alpha=0.10, effective_tests=2)
        assert report["effective_tests"] == 2
        assert report["effective"] is not None
        by_name = {r["name"]: r for r in report["effective"]}
        # m_eff (2) < m (4) → q daha küçük
        assert by_name["E9a"]["q_value_effective"] <= report["rows"][0]["q_value"]

    def test_no_effective_column_by_default(self):
        report = fdr_report(self._rows())
        assert report["effective"] is None
        assert report["effective_tests"] is None

    def test_missing_name_falls_back_to_index(self):
        report = fdr_report([{"p_value": 0.5}])
        assert report["rows"][0]["name"] == "#0"

    def test_empty_rows(self):
        report = fdr_report([])
        assert report["tests"] == 0
        assert report["rows"] == []

    def test_render_report_is_plain_text(self):
        text = render_report(fdr_report(self._rows(), effective_tests=2))
        assert "Benjamini-Hochberg" in text
        assert "E9a" in text
        assert "ÇEKİNCE" in text

    def test_render_empty_report(self):
        assert "Benjamini-Hochberg" in render_report(fdr_report([]))


class TestCli:
    def test_reads_json_file_and_prints_table(self, tmp_path, capsys):
        path = tmp_path / "tarama.json"
        path.write_text(json.dumps([
            {"name": "E2a", "p_value": 0.002},
            {"name": "E2b", "p_value": 0.4},
        ]), encoding="utf-8")
        assert main(["--json", str(path)]) == 0
        out = capsys.readouterr().out
        assert "E2a" in out and "E2b" in out

    def test_json_format_output(self, tmp_path, capsys):
        path = tmp_path / "t.json"
        path.write_text(json.dumps({"rows": [{"name": "x", "p_value": 0.01}]}),
                        encoding="utf-8")
        assert main(["--json", str(path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["tests"] == 1
        assert payload["alpha"] == DEFAULT_ALPHA
