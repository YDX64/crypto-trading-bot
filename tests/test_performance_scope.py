"""The primary scoreboard must match virtual-capital eligibility, not old wins."""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.main as main_module
from src.strategies.scalper.tracker import ScalpTracker


def _row(pnl, source="binance_income_net", strategy="C", roi=10.0):
    return SimpleNamespace(
        strategy=strategy, realized_pnl=pnl, roi_pct=roi,
        notes=f"pnl_source={source}" if source else None,
    )


def _scope(rows):
    return ScalpTracker.performance_scope(rows, start_trade_id=278, base_capital_usdt=1000.0)


def test_scope_conservative_eligibility_retains_losses_and_exposes_exclusions():
    rows = [
        _row(20.0), _row(-40.0, strategy="TV"),
        _row(-5.0, source="estimated_gross"),
        _row(80.0, source="estimated_gross"),
        _row(600.0, source=None), _row(-500.0, source=None),
    ]
    scope = _scope(rows)
    assert scope["enabled"] is True
    assert scope["kind"] == "virtual_capital_cohort"
    assert scope["eligible_realized_pnl"] == -25.0
    assert scope["capital_usdt"] == 975.0
    assert scope["verified_count"] == 2
    assert scope["negative_fallback_count"] == 1
    assert scope["excluded_positive_fallback"] == 1
    assert scope["excluded_legacy"] == 2
    assert scope["combined"]["trades"] == 3
    assert scope["combined"]["pnl_basis"] == "mixed"
    assert scope["combined"]["profit_factor"] == pytest.approx(20.0 / 45.0)
    assert scope["strategies"]["C"]["total_pnl"] == 15.0
    assert scope["strategies"]["TV"]["total_pnl"] == -40.0
    assert sum(value["total_pnl"] for value in scope["strategies"].values()) == scope["eligible_realized_pnl"]


@pytest.mark.parametrize("rows, expected_pnl, expected_pf", [
    ([], 0.0, 0.0), ([_row(10.0)], 10.0, None), ([_row(-10.0)], -10.0, 0.0),
])
def test_scope_empty_winner_only_and_loss_only_are_json_safe(rows, expected_pnl, expected_pf):
    scope = _scope(rows)
    assert scope["eligible_realized_pnl"] == expected_pnl
    assert scope["combined"]["profit_factor"] == expected_pf


def test_legacy_tracker_aggregate_still_reports_mathematical_infinity():
    assert math.isinf(ScalpTracker._stats_for_rows([_row(10.0)])["profit_factor"])


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", [None, "AP", "TV"])
async def test_api_retains_legacy_stats_and_queries_canonical_cohort(monkeypatch, strategy):
    monkeypatch.setattr(main_module.settings, "scalper_virtual_capital_usdt", 1000.0)
    monkeypatch.setattr(main_module.settings, "scalper_virtual_capital_start_trade_id", 278)
    historical = {"C": ScalpTracker._stats_for_rows([_row(501.0)])}
    monkeypatch.setattr(main_module, "scalper_engine", SimpleNamespace(
        tracker=SimpleNamespace(stats=AsyncMock(return_value=historical)),
    ))
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Result([_row(501.0)]), _Result([_row(-100.47262295)]),
    ]))

    payload = await main_module.scalper_stats(db=db, strategy=strategy)

    assert payload["combined"]["total_pnl"] == 501.0
    assert payload["strategies"]["C"]["total_pnl"] == 501.0
    scope = payload["performance_scope"]
    assert scope["eligible_realized_pnl"] == pytest.approx(-100.47262295)
    assert scope["capital_usdt"] == pytest.approx(899.52737705)
    assert scope["start_trade_id"] == 278
    query = db.execute.call_args_list[1].args[0].compile()
    assert "scalp_trades.id >=" in str(query)
    assert "scalp_trades.strategy !=" in str(query)
    assert "scalp_trades.status =" in str(query)
    assert query.params == {"status_1": "CLOSED", "id_1": 278, "strategy_1": "AP"}


@pytest.mark.asyncio
async def test_disabled_virtual_capital_keeps_all_history_without_extra_query(monkeypatch):
    monkeypatch.setattr(main_module.settings, "scalper_virtual_capital_usdt", 0.0)
    monkeypatch.setattr(main_module, "scalper_engine", None)
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result([])))
    payload = await main_module.scalper_stats(db=db)
    assert payload["performance_scope"] == {"enabled": False, "kind": "all_history"}
    db.execute.assert_awaited_once()
