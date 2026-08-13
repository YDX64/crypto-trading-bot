from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.orchestrator import TradingOrchestrator
from src.trading.position_manager import UnprotectedPositionError


async def test_monitoring_unprotected_error_latches_all_new_telegram_entries():
    orchestrator = object.__new__(TradingOrchestrator)
    orchestrator.position_manager = SimpleNamespace(
        is_position_still_open=AsyncMock(
            side_effect=UnprotectedPositionError("stop replacement and flatten failed")
        )
    )
    orchestrator.logger = MagicMock()
    orchestrator._entry_halted = False
    orchestrator._entry_halt_reason = None
    position = SimpleNamespace(symbol="BTCUSDT")

    with pytest.raises(UnprotectedPositionError):
        await orchestrator._monitor_single_position(position, MagicMock())

    assert orchestrator._entry_halted is True
    assert "UnprotectedPositionError" in orchestrator._entry_halt_reason
    orchestrator.logger.critical.assert_called_once()
