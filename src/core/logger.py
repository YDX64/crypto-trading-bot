"""
Loglama sistemi modülü.
Tüm uygulama logları bu modül üzerinden yönetilir.
"""

import sys
import io
import os
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


def setup_logger():
    """Logger'ı yapılandır"""
    
    # Mevcut handler'ları kaldır
    logger.remove()
    
    # UTF-8 stdout wrapper (emoji desteği için)
    utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    # Console output
    logger.add(
        utf8_stdout,
        level=settings.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        # Supervisor redirects stdout to a file/XML-RPC tail. ANSI control
        # codes there make operational parsers fail; retain colours only on a
        # real interactive terminal.
        colorize=bool(getattr(sys.stdout, "isatty", lambda: False)()),
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
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        enqueue=True,
    )
    
    # Trade logları
    logger.add(
        log_dir / "trades.log",
        rotation="50 MB",
        retention="90 days",
        level="INFO",
        filter=_is_trade_record,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        enqueue=True,
    )
    
    # Hata logları
    logger.add(
        log_dir / "errors.log",
        rotation="50 MB",
        retention="60 days",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}\n{exception}",
        backtrace=True,
        diagnose=True,
        enqueue=True,
    )
    
    if os.environ.get("TRADINGBOT_WATCHDOG_PROBE") != "1":
        logger.info("Logger başlatıldı - Ortam: {}", settings.app_env)
    
    return logger


# Global logger instance
app_logger = setup_logger()

