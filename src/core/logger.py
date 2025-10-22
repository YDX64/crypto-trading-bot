"""
Loglama sistemi modülü.
Tüm uygulama logları bu modül üzerinden yönetilir.
"""

import sys
import io
from pathlib import Path
from loguru import logger

from src.core.config import settings


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
        colorize=True,
    )
    
    # Log dizinini oluştur
    log_dir = Path("logs")
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
        filter=lambda record: "trade" in record["extra"],
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
    
    logger.info("Logger başlatıldı - Ortam: {}", settings.app_env)
    
    return logger


# Global logger instance
app_logger = setup_logger()

