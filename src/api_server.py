"""
API Server for monitoring dashboard
Provides real-time data and control endpoints for the trading bot
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from typing import List, Dict, Any, Optional
import asyncio
import json
from datetime import datetime
from contextlib import asynccontextmanager

from src.core.logger import app_logger
from src.core.database import DatabaseManager
from src.core.config import settings
from src.trading.binance_client_improved import ImprovedBinanceClient


class ConnectionManager:
    """WebSocket connection manager"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        app_logger.info(f"WebSocket client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        app_logger.info(f"WebSocket client disconnected. Total: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        """Broadcast message to all connected clients"""
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass


# Global instances
manager = ConnectionManager()
bot_running = False
db_manager = None
binance_client = None
bot_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    global db_manager, binance_client

    # Startup
    app_logger.info("🚀 API Server başlatılıyor...")

    # Database bağlantısı
    db_manager = DatabaseManager()
    await db_manager.init_db()

    # Binance client
    binance_client = ImprovedBinanceClient()

    yield

    # Shutdown
    app_logger.info("🛑 API Server kapatılıyor...")
    if binance_client:
        await binance_client.close()


# FastAPI app
app = FastAPI(
    title="Trading Bot API",
    version="2.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Root endpoint - serve dashboard"""
    with open("monitoring_dashboard_enhanced.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await manager.connect(websocket)

    try:
        while True:
            # Send status updates every 2 seconds
            status_data = await get_status_data()
            await websocket.send_text(json.dumps(status_data))
            await asyncio.sleep(2)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        app_logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


@app.get("/api/status")
async def get_status():
    """Get current bot status"""
    return await get_status_data()


async def get_status_data() -> Dict[str, Any]:
    """Collect all status data"""
    try:
        # Account balance
        balance = 0.0
        if binance_client:
            balance = await binance_client.get_account_balance()

        # Get positions from database
        positions_data = []
        open_positions = 0
        total_pnl = 0.0

        if db_manager:
            async with db_manager.get_session() as session:
                # Get positions (simplified for this example)
                pass  # Database queries would go here

        # Calculate statistics
        win_rate = 0.0
        total_trades = 0

        # Prepare response
        return {
            "timestamp": datetime.now().isoformat(),
            "botStatus": "running" if bot_running else "stopped",
            "account": {
                "balance": balance,
                "totalProfit": total_pnl,
                "winRate": win_rate,
                "totalTrades": total_trades,
                "openPositions": open_positions
            },
            "positions": positions_data,
            "ai": {
                "activeModel": "deepseek",
                "lastAnalysis": "DeepSeek Reasoner aktif",
                "consensus": "3/3"
            },
            "charts": {
                "pnl": {
                    "labels": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"],
                    "data": [0, 50, 30, 80, 120, 100]
                },
                "signals": [5, 2, 1]  # Success, Fail, Pending
            }
        }
    except Exception as e:
        app_logger.error(f"Status data error: {e}")
        return {
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.post("/api/bot/start")
async def start_bot():
    """Start the trading bot"""
    global bot_running, bot_task

    if bot_running:
        return {"status": "already_running", "message": "Bot zaten çalışıyor"}

    try:
        # Import main bot module
        from src.main import main

        # Start bot in background
        bot_task = asyncio.create_task(main())
        bot_running = True

        # Broadcast status
        await manager.broadcast(json.dumps({
            "event": "bot_started",
            "timestamp": datetime.now().isoformat()
        }))

        app_logger.info("✅ Bot başlatıldı")
        return {"status": "started", "message": "Bot başarıyla başlatıldı"}

    except Exception as e:
        app_logger.error(f"Bot başlatma hatası: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/api/bot/stop")
async def stop_bot():
    """Stop the trading bot"""
    global bot_running, bot_task

    if not bot_running:
        return {"status": "not_running", "message": "Bot zaten durmuş"}

    try:
        if bot_task:
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass

        bot_running = False

        # Broadcast status
        await manager.broadcast(json.dumps({
            "event": "bot_stopped",
            "timestamp": datetime.now().isoformat()
        }))

        app_logger.info("⏹️ Bot durduruldu")
        return {"status": "stopped", "message": "Bot durduruldu"}

    except Exception as e:
        app_logger.error(f"Bot durdurma hatası: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/test/binance")
async def test_binance():
    """Test Binance connection"""
    try:
        if not binance_client:
            return {"status": "error", "message": "Binance client not initialized"}

        success = await binance_client.test_connection()
        if success:
            return {"status": "success", "message": "Binance bağlantısı başarılı"}
        else:
            return {"status": "error", "message": "Binance bağlantısı başarısız"}

    except Exception as e:
        app_logger.error(f"Binance test hatası: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/test/ai")
async def test_ai():
    """Test AI connection"""
    try:
        from src.analyzers.ai_analyzer import AIAnalyzer
        from openai import AsyncOpenAI

        # Test DeepSeek connection
        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )

        # Simple test request
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "user", "content": "Test connection"}
            ],
            max_tokens=10
        )

        if response:
            return {"status": "success", "message": "DeepSeek AI bağlantısı başarılı"}
        else:
            return {"status": "error", "message": "AI yanıt vermedi"}

    except Exception as e:
        app_logger.error(f"AI test hatası: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/positions")
async def get_positions():
    """Get all positions"""
    try:
        if not db_manager:
            return {"positions": []}

        # Database queries would go here
        return {"positions": []}

    except Exception as e:
        app_logger.error(f"Positions query error: {e}")
        return {"error": str(e)}


@app.get("/api/logs")
async def get_logs(limit: int = 100):
    """Get recent logs"""
    try:
        # Read from log file or memory buffer
        logs = []
        log_file = "logs/trading.log"

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                logs = lines[-limit:]  # Get last N lines
        except:
            pass

        return {"logs": logs}

    except Exception as e:
        app_logger.error(f"Logs query error: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )