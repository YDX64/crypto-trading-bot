"""
Simple API Server for monitoring dashboard
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import asyncio
import json
from datetime import datetime
from typing import List

# Initialize FastAPI
app = FastAPI(title="Trading Bot API", version="2.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connections
active_connections: List[WebSocket] = []
bot_running = False


@app.get("/")
async def root():
    """Serve dashboard"""
    try:
        with open("monitoring_dashboard_enhanced.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except:
        return {"message": "Dashboard not found"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates"""
    await websocket.accept()
    active_connections.append(websocket)

    try:
        while True:
            # Send dummy data for now
            await websocket.send_text(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "status": "connected"
            }))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        active_connections.remove(websocket)


@app.get("/api/status")
async def get_status():
    """Get current status"""
    try:
        from src.trading.binance_client_improved import ImprovedBinanceClient

        client = ImprovedBinanceClient()
        balance = await client.get_account_balance()
        await client.close()

        return {
            "timestamp": datetime.now().isoformat(),
            "botStatus": "running" if bot_running else "stopped",
            "account": {
                "balance": balance,
                "totalProfit": 0,
                "winRate": 0,
                "totalTrades": 0,
                "openPositions": 0
            },
            "positions": [],
            "ai": {
                "activeModel": "deepseek",
                "lastAnalysis": "DeepSeek Reasoner v3.2 aktif",
                "consensus": "3/3"
            },
            "charts": {
                "pnl": {
                    "labels": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"],
                    "data": [0, 0, 0, 0, 0, 0]
                },
                "signals": [0, 0, 0]
            }
        }
    except Exception as e:
        return {
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.post("/api/bot/start")
async def start_bot():
    """Start the bot"""
    global bot_running
    bot_running = True
    return {"status": "started", "message": "Bot başlatıldı"}


@app.post("/api/bot/stop")
async def stop_bot():
    """Stop the bot"""
    global bot_running
    bot_running = False
    return {"status": "stopped", "message": "Bot durduruldu"}


@app.get("/api/test/binance")
async def test_binance():
    """Test Binance connection"""
    try:
        from src.trading.binance_client_improved import ImprovedBinanceClient

        client = ImprovedBinanceClient()
        success = await client.test_connection()
        await client.close()

        if success:
            return {"status": "success", "message": "Binance bağlantısı başarılı"}
        else:
            return {"status": "error", "message": "Binance bağlantısı başarısız"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/test/ai")
async def test_ai():
    """Test AI connection"""
    try:
        from openai import AsyncOpenAI
        from src.core.config import settings

        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )

        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": "Test"}],
            max_tokens=10
        )

        if response:
            return {"status": "success", "message": "DeepSeek AI bağlantısı başarılı"}
        else:
            return {"status": "error", "message": "AI yanıt vermedi"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    print("🌐 API Server başlatılıyor: http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")