"""uvicorn erişim/hata logu secret redaksiyonu (2026-08-21).

Sözleşme: uvicorn.access logger'ı tam istek satırını (?secret=... dahil)
düz metin yazar (bkz. CLAUDE.md — "logs/supervisor.log secret içerir,
dökme"). `src.main` import edildiğinde `_SecretRedactionLogFilter`
`uvicorn.access` ve `uvicorn.error` logger'larına eklenir; bu, `python -m
uvicorn src.main:app` ile başlatıldığında da (app modülü uvicorn'un kendi
logging kurulumundan önce ya da sonra import edilsin) çalışır, çünkü filtre
Logger nesnesine bağlıdır — handler'ların ne zaman takıldığından bağımsızdır.
"""

import logging

import src.main as main_module
from src.main import _SecretRedactionLogFilter, _install_access_log_secret_redaction


def _make_uvicorn_access_record(path: str) -> logging.LogRecord:
    """uvicorn'un gerçek AccessFormatter şablonuyla birebir bir LogRecord."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "POST", path, "1.1", 200),
        exc_info=None,
    )


class TestSecretRedactionFilter:
    def test_secret_in_path_arg_is_redacted(self):
        record = _make_uvicorn_access_record(
            "/tv-signal?src=algopro&secret=sup3r-gizli-t0ken"
        )
        filt = _SecretRedactionLogFilter()

        assert filt.filter(record) is True  # kaydı asla düşürmez
        formatted = record.getMessage()

        assert "sup3r-gizli-t0ken" not in formatted
        assert "secret=***" in formatted
        # Komşu alanlar bozulmamalı.
        assert "127.0.0.1:12345" in formatted
        assert "src=algopro" in formatted
        assert "200" in formatted

    def test_no_secret_param_passes_through_unchanged(self):
        record = _make_uvicorn_access_record("/status")
        filt = _SecretRedactionLogFilter()
        filt.filter(record)
        assert record.getMessage() == '127.0.0.1:12345 - "POST /status HTTP/1.1" 200'

    def test_msg_itself_redacted_when_secret_embedded_directly(self):
        # uvicorn.error gibi args'sız düz mesajlar için de çalışmalı.
        record = logging.LogRecord(
            name="uvicorn.error",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="invalid request line: /tv-signal?secret=cok-gizli-123 boom",
            args=None,
            exc_info=None,
        )
        filt = _SecretRedactionLogFilter()
        filt.filter(record)
        assert "cok-gizli-123" not in record.getMessage()
        assert "secret=***" in record.getMessage()

    def test_case_insensitive_secret_key(self):
        record = _make_uvicorn_access_record("/tv-signal?SECRET=UPPERCASE-TOKEN")
        filt = _SecretRedactionLogFilter()
        filt.filter(record)
        assert "UPPERCASE-TOKEN" not in record.getMessage()

    def test_non_string_args_and_none_args_do_not_crash(self):
        # status_code int'tir; None args tamamen args'sız log çağrılarını
        # temsil eder (ör. logger.info("plain")).
        record = _make_uvicorn_access_record("/status")
        filt = _SecretRedactionLogFilter()
        filt.filter(record)  # int arg (status code) hatasız geçmeli

        record2 = logging.LogRecord(
            name="uvicorn.error",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="plain message, no args",
            args=None,
            exc_info=None,
        )
        assert filt.filter(record2) is True
        assert record2.getMessage() == "plain message, no args"


class TestFilterInstallation:
    def test_filter_installed_on_uvicorn_access_and_error_at_import_time(self):
        # src.main import edildiğinde modül kapsamında kurulmuş olmalı.
        for name in ("uvicorn.access", "uvicorn.error"):
            target = logging.getLogger(name)
            assert any(
                isinstance(f, _SecretRedactionLogFilter) for f in target.filters
            ), f"{name} üzerinde redaksiyon filtresi yok"

    def test_installation_is_idempotent(self):
        target = logging.getLogger("uvicorn.access")
        before = sum(
            1 for f in target.filters if isinstance(f, _SecretRedactionLogFilter)
        )
        assert before == 1  # tekrar tekrar import/reload'da yığılmamış olmalı

        _install_access_log_secret_redaction()
        _install_access_log_secret_redaction()

        after = sum(
            1 for f in target.filters if isinstance(f, _SecretRedactionLogFilter)
        )
        assert after == 1


class TestOwnAppLogsDoNotPrintQueryString:
    """Uygulamanın kendi logları request.url/query string basmamalı — bunu
    kaynak taraması ile doğrula (regresyon: birisi ileride 'debug' amaçlı
    request.url loglarsa bu test kırılır)."""

    def test_no_request_url_or_query_params_in_app_logger_calls(self):
        import inspect
        import re

        source = inspect.getsource(main_module)
        # app_logger.* çağrılarının hiçbiri request.url ya da query_params
        # okuyan bir f-string/format argümanı taşımamalı.
        for match in re.finditer(
            r"app_logger\.(?:info|warning|error|debug)\([^)]*\)", source, re.DOTALL
        ):
            call_text = match.group(0)
            assert "request.url" not in call_text
            assert "query_params" not in call_text
