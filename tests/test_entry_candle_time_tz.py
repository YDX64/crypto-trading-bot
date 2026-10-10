"""`entry_candle_time` sunucu saat diliminden BAĞIMSIZ olmalı (maker dolum + taker yedek).

Hata: `int(datetime.utcnow().timestamp() * 1000)` NAIVE UTC'yi YEREL saat sayar.
Sunucu Europe/Stockholm (UTC+2 yaz) iken değer tam 2 saat geride kalıyordu →
chandelier penceresi girişten ~24 adet 5m mum önce başlıyor, yapı çıkışı
"olay girişten sonra mı" kapısı 2 saat gevşiyordu. Düzeltme: `time.time()`.

Gerçek `check_pending` / `try_open` yolu çalışır; mock yalnız borsa sınırındadır.
"""
import os
import time

import pytest

from src.strategies.scalper.executor import ScalpExecutor
from test_scalper_setups import (
    _ExecCfg,
    _FakeClient,
    _FakeClientMaker,
    _FakePm,
    _FakeTracker,
    _mk_exec_ctx,
    _mk_exec_signal,
    _mk_maker_cfg,
)

TOLERANCE_MS = 5_000


@pytest.fixture(params=["Europe/Stockholm", "America/New_York", "UTC"])
def tz(request):
    old = os.environ.get("TZ")
    os.environ["TZ"] = request.param
    time.tzset()
    yield request.param
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _capture_finalize(executor):
    captured = {}
    original = executor._finalize_position

    async def spy(*args, **kwargs):
        captured["entry_candle_time"] = kwargs.get("entry_candle_time")
        return await original(*args, **kwargs)

    executor._finalize_position = spy
    return captured


async def test_maker_fill_entry_candle_time_is_epoch_now_in_any_tz(tmp_path, tz):
    client = _FakeClientMaker(limit_order_id=555)
    pm = _FakePm(entry_price=100.0, filled_qty=1.0)
    executor = ScalpExecutor(
        client=client, pm=pm, tracker=_FakeTracker(),
        cfg=_mk_maker_cfg(scalper_pending_journal_path=str(tmp_path / "pending.json")),
    )
    assert await executor.try_open(_mk_exec_signal(100.0, 99.5), _mk_exec_ctx()) is None
    client.get_order_responses[555] = [{
        "orderId": 555, "status": "FILLED", "executedQty": "1", "avgPrice": "100",
    }]
    captured = _capture_finalize(executor)

    before = int(time.time() * 1000)
    opened = await executor.check_pending()
    after = int(time.time() * 1000)

    assert len(opened) == 1
    ect = captured["entry_candle_time"]
    assert before - TOLERANCE_MS <= ect <= after + TOLERANCE_MS, (
        f"TZ={tz}: entry_candle_time={ect} epoch-now={before} "
        f"(fark={(before - ect) / 3_600_000:.2f} sa)"
    )


async def test_taker_fallback_entry_candle_time_is_epoch_now_in_any_tz(tz):
    """Mum yokken (`ctx.candles_5m == []`) yedek dal da aynı hatayı taşıyordu."""
    cfg = _ExecCfg()
    executor = ScalpExecutor(
        client=_FakeClient(balance=10_000.0),
        pm=_FakePm(entry_price=100.0, filled_qty=1.0),
        tracker=_FakeTracker(), cfg=cfg,
    )
    captured = _capture_finalize(executor)

    before = int(time.time() * 1000)
    result = await executor.try_open(_mk_exec_signal(100.0, 99.5), _mk_exec_ctx())
    after = int(time.time() * 1000)

    assert result is not None
    ect = captured["entry_candle_time"]
    assert before - TOLERANCE_MS <= ect <= after + TOLERANCE_MS, (
        f"TZ={tz}: entry_candle_time={ect} epoch-now={before}"
    )
