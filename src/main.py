"""
Ana uygulama giriş noktası.
FastAPI server ve Telegram bot'u çalıştırır.
"""

import asyncio
import signal
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.logger import app_logger
from src.core.database import init_db, close_db, get_db
from src.services.telegram_bot import TelegramBotService
from src.services.orchestrator import TradingOrchestrator


# Global instances
telegram_bot = None
orchestrator = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama yaşam döngüsü"""
    # Startup
    app_logger.info("=" * 80)
    app_logger.info("🚀 TRADING BOT BAŞLATILIYOR")
    app_logger.info("=" * 80)
    
    # Database
    await init_db()
    app_logger.info("✅ Veritabanı hazır")
    
    # Telegram Bot
    global telegram_bot, orchestrator
    telegram_bot = TelegramBotService()

    # Trading Orchestrator
    orchestrator = TradingOrchestrator()

    # Bot'u arka planda başlat
    asyncio.create_task(telegram_bot.start())
    app_logger.info("✅ Telegram bot başlatıldı")

    # Orchestrator'ı başlat
    asyncio.create_task(orchestrator.start())
    app_logger.info("✅ Trading Orchestrator başlatıldı")
    
    app_logger.info("=" * 80)
    app_logger.info(f"✅ TRADING BOT HAZIR - {settings.app_env.upper()}")
    app_logger.info(f"📊 API Server: http://{settings.api_host}:{settings.api_port}")
    app_logger.info(f"🤖 Telegram Bot: Aktif")
    app_logger.info(f"💰 Hesap: {settings.account_balance} USDT")
    app_logger.info(f"⚡ Risk: %{settings.risk_percentage}")
    app_logger.info(f"🎯 İlk TP: %{settings.first_tp_percentage}")
    app_logger.info(f"🔄 Trailing: %{settings.trailing_stop_percentage}")
    app_logger.info("=" * 80)
    
    yield
    
    # Shutdown
    app_logger.info("🛑 Uygulama kapatılıyor...")

    if telegram_bot:
        await telegram_bot.stop()

    if orchestrator:
        await orchestrator.stop()

    await close_db()
    app_logger.info("✅ Uygulama kapatıldı")


# FastAPI App
app = FastAPI(
    title="VIP Trading Bot API",
    description="Otonom Kripto Trading Bot - Telegram Sinyalleri + AI Analiz + Trailing SL/TP",
    version="1.0.0",
    lifespan=lifespan
)


# Mount static files
import os
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/dashboard")
async def dashboard():
    """Professional Trading Bot Dashboard"""
    return HTMLResponse(content=open(f"{static_dir}/dashboard.html", "r").read())


@app.get("/")
async def root():
    """Ana sayfa"""
    return {
        "name": "VIP Trading Bot",
        "version": "1.0.0",
        "status": "running",
        "environment": settings.app_env,
        "features": [
            "Telegram Signal Integration",
            "AI-Powered Analysis (3x)",
            "Automatic Position Management",
            "Trailing Stop Loss",
            "Break-Even Protection",
            "10% Risk Per Trade",
            "25% First Take Profit",
        ]
    }


@app.get("/api/status")
async def api_status():
    """Sistem durumu"""
    global telegram_bot, orchestrator

    # Get Binance balance
    try:
        from src.trading.binance_testnet_client import BinanceFuturesTestnetClient
        client = BinanceFuturesTestnetClient()
        balance = await client.get_balance()
        btc_price = await client.get_ticker_price("BTCUSDT")
        positions = await client.get_position_risk()
        await client.close()
    except:
        balance = 0
        btc_price = 0
        positions = []

    return {
        "status": "running",
        "bot_active": telegram_bot is not None,
        "orchestrator_active": orchestrator is not None,
        "account": {
            "balance": balance,
            "btc_price": btc_price,
            "open_positions": len([p for p in positions if float(p.get("positionAmt", 0)) != 0])
        },
        "config": {
            "risk_percentage": settings.risk_percentage,
            "max_positions": settings.max_positions,
            "first_tp": settings.first_tp_percentage,
            "trailing_stop": settings.trailing_stop_percentage
        },
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    """Sağlık kontrolü"""
    return {
        "status": "healthy",
        "timestamp": "2024-01-01T00:00:00Z",
        "telegram_bot": "running" if telegram_bot else "stopped",
        "database": "connected"
    }


@app.get("/positions")
async def get_positions():
    """Açık pozisyonları listele"""
    if not telegram_bot or not telegram_bot.orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not ready")
    
    positions = []
    
    for symbol, position in telegram_bot.orchestrator.active_positions.items():
        positions.append({
            "symbol": symbol,
            "side": position.side.value,
            "entry_price": position.entry_price,
            "current_price": position.current_price,
            "quantity": position.quantity,
            "leverage": position.leverage,
            "current_stoploss": position.current_stoploss,
            "status": position.status.value,
            "is_break_even": position.is_break_even,
            "is_trailing": position.is_trailing,
            "unrealized_pnl": position.unrealized_pnl,
            "pnl_percentage": position.pnl_percentage,
            "opened_at": position.opened_at.isoformat()
        })
    
    return {
        "count": len(positions),
        "positions": positions
    }


from pydantic import BaseModel
from src.models.waiting_signal import WaitingSignalModel, WaitingStatus
from sqlalchemy import select

class SignalRequest(BaseModel):
    message: str

@app.post("/signal")
async def manual_signal(
    signal_data: SignalRequest,
    db: AsyncSession = Depends(get_db)
):
    """Manuel sinyal gönder"""
    if not telegram_bot or not telegram_bot.orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not ready")
    
    try:
        position = await telegram_bot.orchestrator.process_signal(signal_data.message, db)
        
        if position:
            return {
                "success": True,
                "message": "Position opened",
                "position": {
                    "symbol": position.symbol,
                    "side": position.side.value,
                    "entry_price": position.entry_price,
                    "quantity": position.quantity,
                    "leverage": position.leverage
                }
            }
        else:
            return {
                "success": False,
                "message": "Signal rejected or failed"
            }
    
    except Exception as e:
        app_logger.error(f"Manuel sinyal hatası: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """İstatistikler"""
    # TODO: Database'den istatistikleri çek
    
    active_count = 0
    if telegram_bot and telegram_bot.orchestrator:
        active_count = len(telegram_bot.orchestrator.active_positions)
    
    return {
        "active_positions": active_count,
        "account_balance": settings.account_balance,
        "risk_percentage": settings.risk_percentage,
        "first_tp_percentage": settings.first_tp_percentage,
        "trailing_stop_percentage": settings.trailing_stop_percentage,
        "check_interval": settings.check_interval_seconds,
        "margin_type": settings.margin_type,
    }


@app.get("/config")
async def get_config():
    """Konfigürasyon"""
    return {
        "risk_percentage": settings.risk_percentage,
        "first_tp_percentage": settings.first_tp_percentage,
        "trailing_stop_percentage": settings.trailing_stop_percentage,
        "trailing_profit_percentage": settings.trailing_profit_percentage,
        "check_interval_seconds": settings.check_interval_seconds,
        "margin_type": settings.margin_type,
        "max_leverage": settings.max_leverage,
        "environment": settings.app_env,
        "is_testnet": settings.is_testnet,
        # Waiting mode settings
        "waiting_mode_enabled": settings.waiting_mode_enabled,
        "waiting_mode_max_positions": settings.waiting_mode_max_positions,
        "waiting_mode_max_hours": settings.waiting_mode_max_hours,
        "waiting_mode_check_interval_minutes": settings.waiting_mode_check_interval_minutes,
        "waiting_mode_min_conditions": settings.waiting_mode_min_conditions,
        "waiting_mode_price_improvement": settings.waiting_mode_price_improvement
    }


@app.get("/waiting-mode/active")
async def get_active_waiting_signals(db: AsyncSession = Depends(get_db)):
    """Get all active waiting signals"""
    query = select(WaitingSignalModel).where(
        WaitingSignalModel.status == WaitingStatus.WAITING
    )
    result = await db.execute(query)
    waiting_signals = result.scalars().all()

    return [
        {
            "id": ws.id,
            "symbol": ws.symbol,
            "direction": ws.direction,
            "original_entry_min": ws.original_entry_min,
            "original_entry_max": ws.original_entry_max,
            "current_price": ws.current_price,
            "ai_verdict": ws.ai_verdict,
            "last_score": ws.last_score,
            "conditions_met_count": ws.conditions_met_count,
            "total_checks": ws.total_checks,
            "wait_time_hours": ws.wait_time_hours,
            "created_at": ws.created_at.isoformat() if ws.created_at else None,
            "last_checked_at": ws.last_checked_at.isoformat() if ws.last_checked_at else None
        }
        for ws in waiting_signals
    ]


@app.get("/waiting-mode/history")
async def get_waiting_history(db: AsyncSession = Depends(get_db)):
    """Get waiting signal history"""
    query = select(WaitingSignalModel).order_by(WaitingSignalModel.created_at.desc()).limit(20)
    result = await db.execute(query)
    waiting_signals = result.scalars().all()

    return [
        {
            "id": ws.id,
            "symbol": ws.symbol,
            "direction": ws.direction,
            "status": ws.status.value if ws.status else "UNKNOWN",
            "executed_price": ws.executed_price,
            "last_score": ws.last_score,
            "total_checks": ws.total_checks,
            "wait_time_hours": ws.wait_time_hours,
            "created_at": ws.created_at.isoformat() if ws.created_at else None,
            "executed_at": ws.executed_at.isoformat() if ws.executed_at else None
        }
        for ws in waiting_signals
    ]


async def main():
    """Ana fonksiyon"""
    import uvicorn
    
    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=settings.debug
    )
    
    server = uvicorn.Server(config)
    
    # Graceful shutdown handler
    def signal_handler(sig, frame):
        app_logger.info(f"Signal {sig} alındı, kapatılıyor...")
        server.should_exit = True
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())

