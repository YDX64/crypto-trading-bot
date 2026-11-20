"""scripts/fill_funding_cache.py — ayrıştırma, sayfalama, durdurma, birleştirme.
Sahte taşıyıcı: AĞ YOK."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fill_funding_cache as ffc  # noqa: E402


def _item(t, r):
    return {"symbol": "BTCUSDT", "fundingTime": t, "fundingRate": str(r), "markPrice": "1"}


def test_parse_rows_converts_and_skips_bad():
    rows = ffc.parse_funding_rows([_item(10, "0.0001"), {"x": 1}, _item("7", "-0.0002")])
    assert rows == [
        {"funding_time": 10, "rate": 0.0001}, {"funding_time": 7, "rate": -0.0002}]
    assert ffc.parse_funding_rows({"code": -1}) == []


def test_pagination_advances_start_and_sleeps_between_calls():
    calls, sleeps = [], []
    page1 = [_item(1000 + i, 0.0001) for i in range(1000)]     # dolu sayfa
    page2 = [_item(5000, 0.0003), _item(5001, 0.0004)]          # kısa -> son

    def transport(params):
        calls.append(dict(params))
        return 200, (page1 if len(calls) == 1 else page2)

    rows = ffc.fetch_symbol(transport, "BTCUSDT", 0, 10_000, sleep=sleeps.append)
    assert len(calls) == 2
    assert calls[0]["startTime"] == 0 and calls[0]["limit"] == 1000
    assert calls[0]["endTime"] == 10_000 and calls[0]["symbol"] == "BTCUSDT"
    assert calls[1]["startTime"] == 1999 + 1          # son satır + 1 ms
    assert sleeps == [0.5, 0.5]
    assert len(rows) == 1002 and rows[-1]["rate"] == 0.0004
    assert [r["funding_time"] for r in rows] == sorted(r["funding_time"] for r in rows)


@pytest.mark.parametrize("status", [418, 429])
def test_stops_with_clear_message_on_ban_or_rate_limit(status):
    def transport(params):
        return status, {"code": -1003}

    with pytest.raises(ffc.FundingFetchError) as exc:
        ffc.fetch_symbol(transport, "BTCUSDT", 0, 10, sleep=lambda s: None)
    assert str(status) in str(exc.value) and "DURULDU" in str(exc.value)


def test_other_http_error_raises():
    with pytest.raises(ffc.FundingFetchError):
        ffc.fetch_symbol(lambda p: (500, "boom"), "BTCUSDT", 0, 10, sleep=lambda s: None)


def test_merge_keeps_existing_and_new_wins(tmp_path):
    ffc.merge_and_write(tmp_path, "btcusdt", [{"funding_time": 2, "rate": 0.1},
                                              {"funding_time": 1, "rate": 0.2}])
    path = ffc.merge_and_write(tmp_path, "BTCUSDT", [{"funding_time": 2, "rate": 0.5},
                                                     {"funding_time": 3, "rate": 0.3}])
    data = json.loads(path.read_text())
    assert data == [{"funding_time": 1, "rate": 0.2}, {"funding_time": 2, "rate": 0.5},
                    {"funding_time": 3, "rate": 0.3}]


def test_main_end_to_end_with_fake_transport_and_harness_can_read(tmp_path, capsys):
    from src.strategies.scalper.funding import load_funding_rows
    seen = []

    def transport(params):
        seen.append(params)
        return 200, [_item(28_800_000, "0.00005")]

    rc = ffc.main(
        ["--symbols", "btcusdt,ETHUSDT", "--start", "1970-01-01", "--end", "1970-01-03",
         "--cache-dir", str(tmp_path)], transport=transport, sleep=lambda s: None)
    assert rc == 0
    assert [p["symbol"] for p in seen] == ["BTCUSDT", "ETHUSDT"]
    assert seen[0]["startTime"] == 0 and seen[0]["endTime"] == 2 * 86_400_000 - 1
    assert load_funding_rows("BTCUSDT", tmp_path) == [(28_800_000, 0.00005)]


def test_main_returns_2_on_429_and_bad_dates(tmp_path, capsys):
    rc = ffc.main(["--symbols", "BTCUSDT", "--start", "2026-01-01", "--end", "2026-01-02",
                   "--cache-dir", str(tmp_path)],
                  transport=lambda p: (429, None), sleep=lambda s: None)
    assert rc == 2 and "429" in capsys.readouterr().err
    assert ffc.main(["--symbols", "X", "--start", "2026-02-01", "--end", "2026-01-01",
                     "--cache-dir", str(tmp_path)], transport=lambda p: (200, [])) == 2
    assert not list(tmp_path.glob("*.json"))
