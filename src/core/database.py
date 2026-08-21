"""
Veritabanı bağlantı ve session yönetimi.
"""

from typing import AsyncGenerator
from sqlalchemy import event
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


if database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        """Make the local trade journal resilient to concurrent access/crashes.

        SQLAlchemy exposes an adapted synchronous connection in this event even
        when the application uses aiosqlite.  These PRAGMAs therefore run once
        for every pooled connection before it is handed to application code.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

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


def _ensure_schema_migrations(sync_conn) -> None:
    """create_all mevcut tabloya yeni sütun EKLEMEZ; eksik sütunları tamamla."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(sync_conn)
    if "scalp_trades" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("scalp_trades")}
    if "entry_order_id" not in columns:
        sync_conn.execute(text("ALTER TABLE scalp_trades ADD COLUMN entry_order_id VARCHAR"))
        app_logger.info("🔧 scalp_trades.entry_order_id sütunu eklendi (migration)")


async def init_db():
    """Veritabanını başlat ve tabloları oluştur"""
    # Import all models to ensure they're registered with Base
    from src.models.signal import SignalModel
    from src.models.position import PositionModel
    from src.models.waiting_signal import WaitingSignalModel, IndicatorSnapshot, WaitingModeConfig
    from src.models.scalp_trade import ScalpTradeModel

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all zaten var olan tabloya yeni sütun eklemez; eski DB'lerde
        # eksik kalan sütunları burada tamamlıyoruz (idempotent).
        await conn.run_sync(_ensure_schema_migrations)
    app_logger.info("Veritabanı tabloları oluşturuldu")


async def close_db():
    """Veritabanı bağlantılarını kapat"""
    await engine.dispose()
    app_logger.info("Veritabanı bağlantıları kapatıldı")

