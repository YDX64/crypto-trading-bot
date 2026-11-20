"""D45 — loguru çağrılarının KENDİSİ istisna fırlatmamalı.

Tuzak: loguru çağrıya kwargs verilirse mesajı `str.format(**kwargs)` ile biçimlendirir.
stdlib alışkanlığıyla yazılan `logger.error(f"... {e}", exc_info=True)` ve
`logger.critical(f"... {e}", extra={"trade": True})` kalıplarında mesajda süslü parantez
varsa (Binance JSON hata gövdesi, dict repr, HTML) log çağrısı KeyError/IndexError/
ValueError fırlatır; `exc_info=True` ayrıca traceback EKLEMEZ. `except` içindeki bir log
çağrısı güvenlik eyleminden ÖNCE patlarsa eylem atlanır, döngü yakalayıcısında patlarsa
görev ölür. Mevcut testlerin çoğu `logger`'ı MagicMock ile değiştirir; sahte logger bu
tuzağı gizler, bu yüzden burada GERÇEK loguru çekirdeği kullanılır.

Çözüm `src/core/logger.py`'de merkezidir (çağrı yerleri DEĞİŞMEDİ). Bu dosya o davranışı
kilitler; ayrıntı ve kanıt: docs/DECISIONS.md D45.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from loguru import logger

import src.core.logger as core_logger
import src.services.orchestrator as orchestrator_module
from src.core.logger import _is_trade_record
from src.services.orchestrator import TradingOrchestrator

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_MARK = "_tradingbot_kwargs_guard"

# Gerçek hayattan benzer, süslü parantezli metinler. Her biri korumasız loguru'da
# kwargs ile ya istisna fırlatır ya da (çift parantez) mesajı sessizce BOZAR.
HOSTILE_MESSAGES = [
    'Binance [400] kod=-1021: {"code":-1021,"msg":"Timestamp outside recvWindow"}',
    "dict repr: {'a': 1, 'b': [1, 2]}",
    "boş {} parantez",
    "tek kapanış } parantezi",
    "tek açılış { parantezi",
    "{0} ve {name} yer tutucu gibi görünür",
    "{a.b} nitelik gibi görünür",
    "{:d} biçim belirteci gibi görünür",
    "<html><body>{{404}}</body></html>",
]


@pytest.fixture
def captured():
    """Gerçek loguru çekirdeğine takılan, sink hatasını YUTMAYAN (catch=False) yakalayıcı."""
    items = []
    handler_id = logger.add(
        items.append, level="DEBUG", format="{message}", catch=False, diagnose=False
    )
    yield items
    logger.remove(handler_id)


# ---------------------------------------------------------------------------
# Tuzağın kendisi: kwargs + süslü parantez
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", HOSTILE_MESSAGES)
def test_extra_kwarg_with_braces_is_logged_verbatim(captured, message):
    logger.critical(message, extra={"trade": True})

    assert [item.record["message"] for item in captured] == [message]


@pytest.mark.parametrize("message", HOSTILE_MESSAGES)
def test_exc_info_kwarg_with_braces_is_logged_verbatim_with_a_real_traceback(captured, message):
    try:
        1 / 0
    except ZeroDivisionError as exc:
        logger.error(f"izleme hatası: {exc} | {message}", exc_info=True)

    (item,) = captured
    assert item.record["message"] == f"izleme hatası: division by zero | {message}"
    assert item.record["exception"].type is ZeroDivisionError
    text = str(item)
    assert "Traceback (most recent call last)" in text
    assert "ZeroDivisionError: division by zero" in text


def test_exc_info_accepts_an_exception_instance_and_an_exc_info_tuple(captured):
    try:
        {}["k"]
    except KeyError as exc:
        logger.error('örnek {"a":1}', exc_info=exc)
        logger.error('demet {"a":1}', exc_info=sys.exc_info())

    assert [item.record["exception"].type for item in captured] == [KeyError, KeyError]


@pytest.mark.parametrize("value", [True, False, None])
def test_exc_info_without_an_active_exception_adds_no_exception_record(captured, value):
    logger.error('işlenen istisna yok {"a":1}', exc_info=value)

    (item,) = captured
    assert item.record["exception"] is None
    assert item.record["message"] == 'işlenen istisna yok {"a":1}'


def test_extra_mapping_is_merged_into_record_extra_like_bind(captured):
    logger.info("x", extra={"trade": True, "k": 1})

    extra = captured[0].record["extra"]
    assert extra["trade"] is True
    assert extra["k"] == 1
    assert "extra" not in extra


def test_trade_filter_routes_extra_form_bind_form_and_hostile_text(captured):
    routed = []
    handler_id = logger.add(
        lambda message: routed.append(message.record["message"]),
        filter=_is_trade_record,
        level="INFO",
        catch=False,
    )
    try:
        logger.info("A", extra={"trade": True})
        logger.critical('B {"code":-1}', extra={"trade": True})
        logger.bind(trade=True).warning("C")
        logger.info("D düz")
        logger.info("E", extra={"trade": False})
    finally:
        logger.remove(handler_id)

    assert routed == ["A", 'B {"code":-1}', "C"]


def test_template_message_with_genuine_kwargs_is_still_formatted(captured):
    """Gerçek yer tutucu kwarg'ı varsa mesaj şablondur: loguru anlamı korunur."""
    logger.info("sym={s}", s="BTC", extra={"trade": True})

    (item,) = captured
    assert item.record["message"] == "sym=BTC"
    assert item.record["extra"]["s"] == "BTC"
    assert item.record["extra"]["trade"] is True


def test_regular_loguru_formatting_is_untouched(captured):
    logger.info("a {} b {c}", 1, c=2)
    logger.info("sym={s}", s="BTC")
    logger.info("kwargs ve argümanı olmayan {braces} aynen kalır")

    messages = [item.record["message"] for item in captured]
    assert messages == ["a 1 b 2", "sym=BTC", "kwargs ve argümanı olmayan {braces} aynen kalır"]
    assert captured[1].record["extra"]["s"] == "BTC"


def test_message_with_braces_and_positional_args_is_logged_verbatim_instead_of_raising(captured):
    logger.error('liste {"a":1}', 5)

    assert [item.record["message"] for item in captured] == ['liste {"a":1}']


def test_a_non_formatting_failure_still_raises(captured):
    """Koruma biçimlendirme hatalarını yutar; sahte seviye gibi gerçek hatayı DEĞİL."""
    with pytest.raises(ValueError, match="does not exist"):
        logger.log("YOKBOYLESEVIYE", "x {}", 1)


def test_fallback_write_records_the_callers_exception_not_the_formatting_error(captured):
    """Yedek yazma `except` içinde koşsaydı `exception=True` biçimlendirme hatasını (KeyError) kaydederdi."""
    try:
        1 / 0
    except ZeroDivisionError:
        logger.exception('liste {"a":1}', 5)
        logger.error('liste {"a":1}', 5, exc_info=True)

    assert [item.record["exception"].type for item in captured] == [ZeroDivisionError, ZeroDivisionError]
    assert [item.record["message"] for item in captured] == ['liste {"a":1}'] * 2


def test_fallback_write_also_turns_off_the_record_and_colors_options(captured):
    """`opt(record=True)` ve `opt(colors=True)` mesajı biçimlendirmesi/ayrıştırması KAPALI yedek yazmada da sürerdi."""
    logger.opt(record=True).error('x {"a":1}', exc_info=True)
    logger.opt(colors=True).error("<html> {a}", extra={"trade": True})

    assert [item.record["message"] for item in captured] == ['x {"a":1}', "<html> {a}"]


def test_a_failing_sink_is_not_retried_so_the_record_is_not_written_twice(captured):
    """Bir sink (`catch=False`) patlarsa istisna yükselir; satır önceki sink'lere ikinci kez YAZILMAZ."""

    def broken_sink(message):
        raise ZeroDivisionError("sink arızası")

    handler_id = logger.add(broken_sink, level="DEBUG", catch=False)  # `captured`tan SONRA eklenir
    try:
        with pytest.raises(ZeroDivisionError):
            logger.error("önce yazılan {}", 1)  # args var: koruma yeniden denemeye aday olurdu
    finally:
        logger.remove(handler_id)

    assert [item.record["message"] for item in captured] == ["önce yazılan 1"]  # çift (biçimlenmemiş) kayıt yok


# ---------------------------------------------------------------------------
# Çağıran satır: sarmalayıcı çerçeve eklemesine rağmen kayıt çağıranı göstermeli
# ---------------------------------------------------------------------------


def test_record_points_at_the_call_site_not_the_guard(captured):
    here = inspect.currentframe().f_lineno
    logger.info("düz")
    logger.error('kwargs {"a":1}', exc_info=True)
    logger.critical('şablon {"a":1}', 5)

    assert [item.record["line"] for item in captured] == [here + 1, here + 2, here + 3]
    for item in captured:
        assert item.record["function"] == "test_record_points_at_the_call_site_not_the_guard"
        assert item.record["name"] == __name__


# ---------------------------------------------------------------------------
# Kurulum: tek sefer, idempotent, imza kayarsa kapalı kalır
# ---------------------------------------------------------------------------


def test_guard_is_installed_and_installing_again_changes_nothing(captured):
    before = type(logger)._log
    assert getattr(before, GUARD_MARK, False) is True

    assert core_logger._install_kwargs_guard() is True
    assert type(logger)._log is before

    here = inspect.currentframe().f_lineno
    logger.error('tekrar {"a":1}', exc_info=True)
    assert captured[0].record["line"] == here + 1  # ikinci bir çerçeve eklenmedi


def test_guard_refuses_to_patch_when_the_loguru_internal_signature_changed():
    class Drifted:
        def _log(self, only_one_param):  # loguru bir gün imzayı değiştirirse
            return None

    with pytest.warns(RuntimeWarning):
        assert core_logger._install_kwargs_guard(Drifted) is False
    assert not hasattr(Drifted._log, GUARD_MARK)


def test_guard_refuses_to_patch_when_the_options_layout_may_have_changed():
    """`_log` imzası aynı kalsa bile `Logger.__init__` (seçenek demetinin dizilimi) kayarsa takılmaz."""

    class DriftedInit:
        def __init__(self, core, exception, depth, extra_first):  # loguru bir gün seçenekleri yeniden dizerse
            pass

        def _log(self, level, from_decorator, options, message, args, kwargs):
            return None

    with pytest.warns(RuntimeWarning):
        assert core_logger._install_kwargs_guard(DriftedInit) is False
    assert not hasattr(DriftedInit._log, GUARD_MARK)


def test_canary_pinned_loguru_still_formats_kwargs_so_the_guard_is_still_needed():
    """loguru bir gün kwargs'ı biçimlendirmeyi bırakırsa bu test düşer: koruma gözden geçirilmeli."""
    unguarded = type(logger)._log.__wrapped__
    options = (None, 0, False, False, False, False, True, [], {})  # opt() varsayılanları

    with pytest.raises(KeyError):
        unguarded(logger, "ERROR", False, options, 'x {"a":1}', (), {"exc_info": True})


# ---------------------------------------------------------------------------
# Gerçek sink'ler: trades.log yönlendirmesi, tek traceback, değişken değeri sızmaz
# ---------------------------------------------------------------------------

SUBPROCESS_SCRIPT = r'''
import src.core.logger  # sink'ler + koruma
from loguru import logger


def leak(api_secret):
    try:
        None + api_secret
    except TypeError as exc:
        logger.error(f"izleme hatası: {exc} {{'k': 1}}", exc_info=True)
        logger.critical('trade+istisna {"k": 2}', extra={"trade": True}, exc_info=True)


secret = "".join(["TOP", "SECRET", "-VALUE"])  # kaynakta düz metin olarak geçmez
leak(secret)
logger.critical('trade {"k": 1}', extra={"trade": True})
logger.info("düz satır")
logger.remove()  # enqueue=True kuyruklarını boşaltır
'''


TIMESTAMPED = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ")


def test_real_sinks_route_trade_line_and_write_one_traceback_without_variable_values(tmp_path):
    # `-c` DEĞİL, gerçek dosya: loguru değişken değerlerini kaynak satırını dosyadan
    # okuyarak ekler; `<string>` çerçevelerinde kaynak yoktur ve sızıntı görünmez olurdu.
    script = tmp_path / "emit_logs.py"
    script.write_text(SUBPROCESS_SCRIPT, encoding="utf-8")
    log_dir = tmp_path / "logs"
    env = {**os.environ, "TRADINGBOT_LOG_DIR": str(log_dir), "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "KeyError" not in proc.stderr and "ValueError" not in proc.stderr, proc.stderr[-1500:]

    bot = (log_dir / "bot.log").read_text(encoding="utf-8")
    trades = (log_dir / "trades.log").read_text(encoding="utf-8")
    errors = (log_dir / "errors.log").read_text(encoding="utf-8")

    assert "izleme hatası" in bot and "{'k': 1}" in bot
    assert 'trade {"k": 1}' in trades and 'trade+istisna {"k": 2}' in trades
    assert "izleme hatası" not in trades and "düz satır" not in trades
    # İki istisna kaydı (ERROR + CRITICAL) = iki traceback; çift `{exception}` olsaydı dört olurdu.
    assert errors.count("Traceback (most recent call last)") == 2
    assert "TOPSECRET-VALUE" not in bot + trades + errors + proc.stdout + proc.stderr

    # Traceback YALNIZ errors.log'ta. bot.log'u scripts/restart_safe.sh, server_deploy.sh ve docker_run.sh satır
    # başındaki zaman damgasıyla süzer (418 ban kapısı); damgasız traceback satırları süzgeçten hep geçerdi.
    assert "Traceback" not in bot + trades + proc.stdout
    for name, text in (("bot.log", bot), ("trades.log", trades), ("konsol", proc.stdout)):
        lines = [line for line in text.splitlines() if line.strip()]
        assert lines, name
        assert all(TIMESTAMPED.match(line) for line in lines), (name, [l for l in lines if not TIMESTAMPED.match(l)][:3])


# ---------------------------------------------------------------------------
# Orkestratör izleme döngüsü: parantezli hata döngüyü ÖLDÜRMEMELİ
# ---------------------------------------------------------------------------


async def test_monitoring_loop_survives_an_exception_whose_text_has_braces(monkeypatch, captured):
    orchestrator = object.__new__(TradingOrchestrator)
    orchestrator.logger = core_logger.app_logger  # sahte DEĞİL: tuzak gerçek loguru'da
    orchestrator._running = True
    orchestrator._recovery_ready = True  # D44 yeniden denemesi ilk satırda döner
    orchestrator._entry_halted = False
    orchestrator.active_positions = {}
    orchestrator.config = SimpleNamespace(check_interval_seconds=0)
    orchestrator._last_monitoring_success_monotonic = None
    orchestrator._last_monitoring_success_at = None
    if hasattr(TradingOrchestrator, "_retry_recovery_if_due"):
        orchestrator._retry_recovery_if_due = AsyncMock()  # bu testin konusu değil

    calls = {"n": 0}

    async def refresh_probe():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(
                'Binance [400] kod=-1021: {"code":-1021,"msg":"Timestamp outside recvWindow"}'
            )
        if calls["n"] >= 3:
            orchestrator._running = False

    orchestrator._refresh_exchange_readiness_if_due = refresh_probe

    sleeps = []
    real_sleep = asyncio.sleep

    async def fast_sleep(delay, *args, **kwargs):
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(orchestrator_module.asyncio, "sleep", fast_sleep)

    await asyncio.wait_for(orchestrator._monitoring_loop(), timeout=5)

    assert calls["n"] == 3  # hata sonrası döngü yaşadı ve ilerledi
    assert 10 in sleeps  # yakalayıcı dalı gerçekten çalıştı
    failures = [i for i in captured if "İzleme döngüsü hatası" in i.record["message"]]
    assert len(failures) == 1
    assert '{"code":-1021' in failures[0].record["message"]
    assert failures[0].record["exception"].type is RuntimeError
