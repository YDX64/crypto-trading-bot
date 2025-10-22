"""
Veritabanı bağlantı ve session yönetimi.
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base

from src.core.config import settings
from src.core.logger import app_logger


# SQLite için async URL'yi düzenle
database_url = settings.database_url
if database_url.startswith("sqlite"):
    database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://")

# Async engine oluştur
engine = create_async_engine(
    database_url,
    echo=settings.debug,
    future=True,
    pool_pre_ping=True,
)

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Base model
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Database session dependency.
    FastAPI endpoint'lerinde kullanılır.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            app_logger.error(f"Database session hatası: {e}")
            raise
        finally:
            await session.close()


async def init_db():
    """Veritabanını başlat ve tabloları oluştur"""
    # Import all models to ensure they're registered with Base
    from src.models.signal import SignalModel
    from src.models.position import PositionModel
    from src.models.waiting_signal import WaitingSignalModel, IndicatorSnapshot, WaitingModeConfig

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app_logger.info("Veritabanı tabloları oluşturuldu")


async def close_db():
    """Veritabanı bağlantılarını kapat"""
    await engine.dispose()
    app_logger.info("Veritabanı bağlantıları kapatıldı")

