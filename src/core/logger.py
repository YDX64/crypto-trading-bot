"""
Loglama sistemi modülü.
Tüm uygulama logları bu modül üzerinden yönetilir.
"""

import functools
import inspect
import sys
import io
import os
import warnings
from collections.abc import Mapping
from pathlib import Path
from loguru import logger

from src.core.config import settings


def _is_trade_record(record) -> bool:
    """Accept both Loguru's bound-extra form and the legacy call-site form.

    Existing callers pass ``extra={"trade": True}`` to ``logger.info``;
    Loguru stores that as ``record["extra"]["extra"]``.  Newer callers may
    use ``logger.bind(trade=True)``.  Supporting both keeps the dedicated
    audit log complete while call sites are migrated gradually.
    """
    extra = record.get("extra", {})
    if extra.get("trade") is True:
        return True
    nested = extra.get("extra")
    return isinstance(nested, dict) and nested.get("trade") is True


# ---------------------------------------------------------------------------
# stdlib tarzı kwargs koruması (D45)
# ---------------------------------------------------------------------------
# loguru, çağrıya args/kwargs verilirse mesajı `str.format(*args, **kwargs)` ile
# biçimlendirir. Kod tabanındaki `logger.error(f"... {e}", exc_info=True)` ve
# `logger.critical(f"... {e}", extra={"trade": True})` kalıpları stdlib `logging`
# alışkanlığıdır: mesaj ZATEN biçimlenmiş bir f-string'dir ve istisna/API metni süslü
# parantez taşıyabilir (Binance JSON gövdesi, dict repr). O zaman log çağrısının KENDİSİ
# KeyError/IndexError/ValueError fırlatır: `except` içinde güvenlik eyleminden önce
# patlarsa eylem atlanır, döngü yakalayıcısında patlarsa görev ölür; çift parantez
# (`{{`) ise sessizce tek parantezle BOZULUR. `exc_info=True` loguru'da traceback
# eklemez, yok sayılır.
#
# Koruma loguru'nun tek çıkış noktasına (`Logger._log`: info/error/critical/log hepsi
# buradan geçer) takılır; çağrı yerleri DEĞİŞMEZ:
#   1. `exc_info=`  -> loguru `exception` seçeneği (gerçek traceback);
#   2. `extra={..}` -> kayda `bind(**extra)` gibi eklenir (`_is_trade_record` görür);
#   3. geriye gerçek şablon argümanı/kwarg'ı kalmazsa mesaj BİÇİMLENDİRİLMEZ;
#   4. yine de biçimlendirme patlarsa (f-string + konumsal argüman) mesaj aynen yazılır;
#      bir sink'in kendi hatası yutulmaz ve satır ikinci kez yazılmaz.
# Argümansız çağrılar ve gerçek yer tutuculu çağrılar (`"a {}", x`) eskisi gibi çalışır.

_GUARD_MARK = "_tradingbot_kwargs_guard"
_MISSING = object()
# loguru==0.7.2 (requirements.txt pin'i): imza kayarsa koruma takılmaz (aşağıya bak).
_LOG_PARAMS = ("self", "level", "from_decorator", "options", "message", "args", "kwargs")
# `options` demeti `Logger.__init__` sırasıyla dizilir (exception, depth, record, lazy, colors, raw, capture, patchers, extra).
_INIT_PARAMS = ("self", "core", "exception", "depth", "record", "lazy", "colors", "raw", "capture", "patchers", "extra")


def _stdlib_kwargs_to_options(options, kwargs):
    """`exc_info=` ve `extra=` kwarg'larını loguru seçeneklerine çevir.

    Döner: (options, kwargs). Tanınan kwarg'lar `kwargs`'tan ÇIKARILIR; mesaj yalnız
    çağıranın verdiği gerçek şablon argümanları/kwarg'ları kalırsa biçimlendirilir.
    """
    exception, depth, record, lazy, colors, raw, capture, patchers, extra = options
    if lazy:  # lazy=True'da kwargs çağrılabilirdir; anlamını bozma
        return options, kwargs
    kwargs = dict(kwargs)

    value = kwargs.get("exc_info", _MISSING)
    if value is None or isinstance(value, (bool, BaseException, tuple)):
        del kwargs["exc_info"]
        if value is True:
            # stdlib: işlenmekte olan istisna. Yoksa "NoneType: None" satırı basma.
            value = True if sys.exc_info()[0] is not None else None
        elif value is False or (isinstance(value, tuple) and (len(value) != 3 or value[0] is None)):
            value = None
        if value is not None:
            exception = value

    mapping = kwargs.get("extra", _MISSING)
    if mapping is None or isinstance(mapping, Mapping):
        del kwargs["extra"]
        extra = {**extra, **(mapping or {})}

    return (exception, depth, record, lazy, colors, raw, capture, patchers, extra), kwargs


def _raised_inside_a_sink(exc) -> bool:
    """İstisna loguru'nun yayın döngüsünde (bir sink'in `write`'ında) mı doğdu?

    Yayın başladıktan sonra patlayan satır önceki sink'lere ZATEN yazılmıştır; yeniden yazmak çift kayıt
    üretir. Yayından ÖNCE patlayanlar (biçimlendirme, seviye adı) hiçbir sink'e ulaşmamıştır.
    """
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") == "loguru._handler":
            return True
        tb = tb.tb_next
    return False


def _make_kwargs_guard(original):
    @functools.wraps(original)
    def _log(self, level, from_decorator, options, message, args, kwargs):
        # Bu sarmalayıcı yığına bir çerçeve ekler. loguru çağıran satırı `depth` ile
        # bulur; bir çerçeve daha atla, yoksa kayıt dosya/satır olarak BUNU gösterir.
        options = (options[0], options[1] + 1, *options[2:])
        if args or kwargs:  # biçimlendirme yalnız o zaman olur; tuzak da yalnız orada
            try:
                options, kwargs = _stdlib_kwargs_to_options(options, kwargs)
            except Exception:  # kendi hatamız log satırını yutmasın
                pass
        # Mesaj biçimlendirilir/ayrıştırılır mı? (`record`/`colors` seçenekleri argümansız da bunu yapar.)
        # loguru `kwargs`'a `record` ekleyerek onu DEĞİŞTİRİR: kararı çağırmadan ÖNCE ver.
        formats = bool(args or kwargs or options[2] or options[4])
        try:
            return original(self, level, from_decorator, options, message, args, kwargs)
        except Exception as exc:
            if not formats or _raised_inside_a_sink(exc):
                raise  # biçimlendirme yoktu ya da bir sink patladı (`catch=False`): gerçek hata, yutma
        # `message.format` patladı: aynı satırı biçimlendirmeden, aynen yaz. `except` DIŞINDA: `exception=True`
        # etkin istisnayı okur; içeride bu, biçimlendirme hatası olurdu ve çağıranın istisnası kaybolurdu.
        # `record`/`colors` seçenekleri de kapanır: ikisi de mesajı yine biçimlendirir/ayrıştırır.
        options = (*options[:2], False, options[3], False, *options[5:])
        return original(self, level, from_decorator, options, str(message), (), {})

    setattr(_log, _GUARD_MARK, True)
    return _log


def _install_kwargs_guard(logger_cls=type(logger)) -> bool:
    """Korumayı `logger_cls._log`'a BİR kez tak. Döner: koruma etkin mi.

    loguru'nun iç imzası kaymışsa (sürüm yükseltmesi) hiçbir şeye dokunulmaz ve
    uyarı verilir: yanlış imzayla sarmalamak bütün loglamayı bozardı.
    """
    current = logger_cls._log
    if getattr(current, _GUARD_MARK, False):
        return True
    try:
        params = tuple(inspect.signature(current).parameters)
        init_params = tuple(inspect.signature(logger_cls.__init__).parameters)
    except (TypeError, ValueError):
        params = init_params = ()
    if params != _LOG_PARAMS or init_params != _INIT_PARAMS:
        warnings.warn(
            f"loguru Logger._log/__init__ imzası beklenenden farklı ({params}, {init_params}): stdlib kwargs "
            "koruması KAPALI (docs/DECISIONS.md D45)",
            RuntimeWarning,
            stacklevel=2,
        )
        return False
    logger_cls._log = _make_kwargs_guard(current)
    return True


def _one_line(fmt: str):
    """Kayıt başına TEK satır: istisna/traceback bu sink'e yazılmaz (D45).

    loguru metin biçimlerinin sonuna "\n{exception}" ekler; fonksiyon biçimleyicide eklemez. `bot.log`'u
    `scripts/restart_safe.sh`, `server_deploy.sh` ve `docker_run.sh` satır başındaki zaman damgasına
    bakarak süzer (418 ban kapısı): damgasız traceback satırları o süzgeçten HER ZAMAN geçerdi
    (kaynak satırında `banned`/`HTTP 418` geçen her traceback kapıyı kapatırdı).
    """
    line = fmt + "\n"
    return lambda record: line


def setup_logger():
    """Logger'ı yapılandır"""
    
    # Mevcut handler'ları kaldır
    logger.remove()

    # TÜM sink'lerde `diagnose=False`: loguru traceback'e değişken DEĞERLERİNİ basar
    # (ör. `api_secret = '...'`). `exc_info=True` artık gerçek traceback ürettiği için
    # değerler log dosyalarına sızardı (D45). Traceback YALNIZ errors.log'a gider;
    # konsol, bot.log ve trades.log kayıt başına tek satırlı kalır (`_one_line`).
    
    # UTF-8 stdout wrapper (emoji desteği için)
    utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    # Console output
    logger.add(
        utf8_stdout,
        level=settings.log_level,
        format=_one_line(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        ),
        # Supervisor redirects stdout to a file/XML-RPC tail. ANSI control
        # codes there make operational parsers fail; retain colours only on a
        # real interactive terminal.
        colorize=bool(getattr(sys.stdout, "isatty", lambda: False)()),
        diagnose=False,
    )
    
    # Log dizinini oluştur.
    # Testler prod denetim izini (bot.log/trades.log/errors.log) kirletmemeli:
    # TRADINGBOT_LOG_DIR verilirse loglar oraya yazılır, verilmezse davranış
    # eskisiyle birebir aynı kalır ("logs").
    log_dir = Path(os.environ.get("TRADINGBOT_LOG_DIR") or "logs")
    log_dir.mkdir(exist_ok=True)
    
    # Genel loglar
    logger.add(
        log_dir / "bot.log",
        rotation="100 MB",
        retention="30 days",
        level="DEBUG",
        format=_one_line("{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"),
        diagnose=False,
        enqueue=True,
    )
    
    # Trade logları
    logger.add(
        log_dir / "trades.log",
        rotation="50 MB",
        retention="90 days",
        level="INFO",
        filter=_is_trade_record,
        format=_one_line("{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}"),
        diagnose=False,
        enqueue=True,
    )
    
    # Hata logları
    logger.add(
        log_dir / "errors.log",
        rotation="50 MB",
        retention="60 days",
        level="ERROR",
        # `{exception}` YOK: loguru metin biçimlerinin sonuna "\n{exception}"'ı kendisi
        # ekler; açıkça yazmak traceback'i iki kez basardı (D45).
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        backtrace=True,
        diagnose=False,
        enqueue=True,
    )
    
    if os.environ.get("TRADINGBOT_WATCHDOG_PROBE") != "1":
        logger.info("Logger başlatıldı - Ortam: {}", settings.app_env)
    
    return logger


# Koruma log çağrısı yapılabilmeden ÖNCE takılır (setup_logger kendisi de log yazar).
_install_kwargs_guard()

# Global logger instance
app_logger = setup_logger()

