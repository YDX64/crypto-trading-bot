from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.logger import app_logger
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


async def test_unprotected_error_with_brace_text_still_latches_and_keeps_its_type():
    """D45: gerçek loguru + süslü parantezli hata metni.

    `critical(..., extra={"trade": True})` log çağrısı patlarsa çağıran UnprotectedPositionError
    yerine KeyError görürdü (tür değişir, üst katmanın koruma kararı yanlış dala düşer).
    """
    orchestrator = object.__new__(TradingOrchestrator)
    orchestrator.position_manager = SimpleNamespace(
        is_position_still_open=AsyncMock(
            side_effect=UnprotectedPositionError(
                'stop reddedildi: {"code":-2021,"msg":"Order would immediately trigger."}'
            )
        )
    )
    orchestrator.logger = app_logger
    orchestrator._entry_halted = False
    orchestrator._entry_halt_reason = None
    position = SimpleNamespace(symbol="BTCUSDT")

    with pytest.raises(UnprotectedPositionError):
        await orchestrator._monitor_single_position(position, MagicMock())

    assert orchestrator._entry_halted is True
    assert "-2021" in orchestrator._entry_halt_reason
