#!/usr/bin/env python3
"""
N8n-style Interactive Trading Dashboard with Real-time Logs
Ultra-fast WebSocket updates with visual signal flow
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
from collections import deque
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import our modules
try:
    from src.trading.binance_testnet_client import BinanceFuturesTestnetClient
    from src.core.config import settings
except ImportError as e:
    logger.error(f"Import error: {e}")
    try:
        from src.trading.binance_client_improved import BinanceClient
        BinanceFuturesTestnetClient = BinanceClient
    except:
        from src.trading.binance_client import BinanceClient
        BinanceFuturesTestnetClient = BinanceClient
    from src.core.config import settings

app = FastAPI(title="Interactive Trading Dashboard")

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
class SystemState:
    def __init__(self):
        self.websocket_clients: List[WebSocket] = []
        self.binance_client = None
        self.logs: deque = deque(maxlen=100)  # Keep last 100 logs
        self.signals: deque = deque(maxlen=50)  # Keep last 50 signals
        self.positions = {}
        self.account_info = {"balance": 0, "pnl": 0}
        self.bot_status = "offline"
        self.pipeline_status = {}
        self.metrics = {
            "signals_received": 0,
            "signals_processed": 0,
            "positions_opened": 0,
            "positions_closed": 0,
            "total_pnl": 0,
            "win_rate": 0
        }
        self.update_interval = 0.1  # 100ms updates for ultra-fast response

    async def add_log(self, level: str, message: str, module: str = "system"):
        """Add a log entry and broadcast to all clients"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "module": module,
            "message": message
        }
        self.logs.append(log_entry)
        await self.broadcast({
            "type": "log",
            "data": log_entry
        })

    async def add_signal(self, signal_data: dict):
        """Add a signal and update pipeline status"""
        signal = {
            "id": f"sig_{datetime.now().timestamp()}",
            "timestamp": datetime.now().isoformat(),
            "status": "received",
            "data": signal_data,
            "pipeline": {
                "received": True,
                "validated": False,
                "analyzed": False,
                "executed": False,
                "completed": False
            }
        }
        self.signals.append(signal)
        self.metrics["signals_received"] += 1

        await self.broadcast({
            "type": "signal",
            "data": signal
        })

        # Simulate signal processing pipeline
        asyncio.create_task(self.process_signal_pipeline(signal))

    async def process_signal_pipeline(self, signal: dict):
        """Process signal through pipeline with visual updates"""
        stages = ["validated", "analyzed", "executed", "completed"]

        for stage in stages:
            await asyncio.sleep(0.5)  # Simulate processing
            signal["pipeline"][stage] = True
            signal["status"] = stage

            # Update pipeline visualization
            self.pipeline_status = {
                "current_signal": signal["id"],
                "stage": stage,
                "progress": (stages.index(stage) + 1) / len(stages) * 100
            }

            await self.broadcast({
                "type": "pipeline_update",
                "data": {
                    "signal_id": signal["id"],
                    "stage": stage,
                    "pipeline": signal["pipeline"],
                    "status": self.pipeline_status
                }
            })

            # Add stage log
            await self.add_log("INFO", f"Signal {signal['id'][:8]} - Stage: {stage.upper()}", "pipeline")

        self.metrics["signals_processed"] += 1

    async def broadcast(self, message: dict):
        """Broadcast message to all connected WebSocket clients"""
        if self.websocket_clients:
            message_json = json.dumps(message)
            disconnected = []
            for ws in self.websocket_clients:
                try:
                    await ws.send_text(message_json)
                except:
                    disconnected.append(ws)

            # Remove disconnected clients
            for ws in disconnected:
                if ws in self.websocket_clients:
                    self.websocket_clients.remove(ws)

state = SystemState()

@app.on_event("startup")
async def startup_event():
    """Initialize system on startup"""
    try:
        state.binance_client = BinanceFuturesTestnetClient()
        state.bot_status = "initializing"
        await state.add_log("INFO", "System starting up...", "core")

        # Start background tasks
        asyncio.create_task(update_account_loop())
        asyncio.create_task(simulate_signals())  # For demo
        asyncio.create_task(metric_calculator())

        state.bot_status = "online"
        await state.add_log("SUCCESS", "System online and ready!", "core")
    except Exception as e:
        logger.error(f"Startup error: {e}")
        await state.add_log("ERROR", f"Startup failed: {str(e)}", "core")
        state.bot_status = "error"

async def update_account_loop():
    """Update account info in real-time"""
    while True:
        try:
            if state.binance_client:
                # Get account balance
                balance = await state.binance_client.get_balance()

                # Get open positions
                positions = await state.binance_client.get_position_risk()

                # Calculate total PNL
                total_pnl = sum(float(p.get('unRealizedProfit', 0)) for p in positions)

                state.account_info = {
                    "balance": balance,
                    "pnl": total_pnl,
                    "positions_count": len(positions),
                    "timestamp": datetime.now().isoformat()
                }

                state.positions = {p['symbol']: p for p in positions}

                # Broadcast update
                await state.broadcast({
                    "type": "account_update",
                    "data": state.account_info
                })

                await state.broadcast({
                    "type": "positions_update",
                    "data": list(state.positions.values())
                })

        except Exception as e:
            logger.error(f"Account update error: {e}")
            await state.add_log("ERROR", f"Account update failed: {str(e)}", "binance")

        await asyncio.sleep(state.update_interval)

async def simulate_signals():
    """Simulate incoming signals for demo"""
    signal_types = ["BUY", "SELL", "CLOSE"]
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

    while True:
        await asyncio.sleep(10)  # Generate signal every 10 seconds

        signal = {
            "type": signal_types[int(datetime.now().timestamp()) % 3],
            "symbol": symbols[int(datetime.now().timestamp()) % 3],
            "price": 50000 + (int(datetime.now().timestamp()) % 1000),
            "stop_loss": 49000,
            "take_profit": 51000,
            "confidence": 0.75 + (int(datetime.now().timestamp()) % 25) / 100
        }

        await state.add_signal(signal)

async def metric_calculator():
    """Calculate and update metrics"""
    while True:
        await asyncio.sleep(1)

        # Calculate win rate
        if state.metrics["positions_closed"] > 0:
            state.metrics["win_rate"] = (state.metrics["positions_closed"] - state.metrics["positions_opened"]) / state.metrics["positions_closed"] * 100

        await state.broadcast({
            "type": "metrics_update",
            "data": state.metrics
        })

@app.get("/")
async def get_dashboard():
    """Serve the N8n-style interactive dashboard"""
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Bot - Interactive Flow Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f0f1e 0%, #1a1a2e 100%);
            color: #e0e0e0;
            height: 100vh;
            overflow: hidden;
        }

        .container {
            display: grid;
            grid-template-columns: 300px 1fr 350px;
            grid-template-rows: 60px 1fr;
            height: 100vh;
            gap: 1px;
            background: rgba(255,255,255,0.05);
        }

        /* Header */
        .header {
            grid-column: 1 / -1;
            background: rgba(20, 20, 35, 0.9);
            backdrop-filter: blur(10px);
            display: flex;
            align-items: center;
            padding: 0 20px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }

        .header h1 {
            font-size: 20px;
            font-weight: 600;
            background: linear-gradient(90deg, #00d4ff, #0099ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .status-badge {
            margin-left: 20px;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            animation: pulse 2s infinite;
        }

        .status-online {
            background: rgba(0, 255, 100, 0.2);
            color: #00ff64;
            border: 1px solid rgba(0, 255, 100, 0.3);
        }

        .status-offline {
            background: rgba(255, 50, 50, 0.2);
            color: #ff3232;
            border: 1px solid rgba(255, 50, 50, 0.3);
        }

        /* Sidebar - Metrics */
        .sidebar {
            background: rgba(20, 20, 35, 0.7);
            padding: 20px;
            overflow-y: auto;
        }

        .metric-card {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            transition: all 0.3s;
        }

        .metric-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0, 150, 255, 0.2);
        }

        .metric-label {
            font-size: 11px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .metric-value {
            font-size: 24px;
            font-weight: bold;
            margin-top: 5px;
            color: #fff;
        }

        .metric-change {
            font-size: 12px;
            margin-top: 5px;
        }

        .positive {
            color: #00ff64;
        }

        .negative {
            color: #ff3232;
        }

        /* Main - Pipeline View */
        .main {
            padding: 20px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }

        .pipeline-container {
            flex: 1;
            background: rgba(20, 20, 35, 0.5);
            border-radius: 16px;
            padding: 20px;
            overflow-y: auto;
        }

        .pipeline-header {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 20px;
            color: #aaa;
            text-transform: uppercase;
        }

        .signal-flow {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }

        .signal-card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 15px;
            display: flex;
            align-items: center;
            gap: 15px;
            animation: slideIn 0.3s ease-out;
        }

        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateX(-20px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }

        .signal-icon {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
        }

        .signal-buy {
            background: rgba(0, 255, 100, 0.2);
        }

        .signal-sell {
            background: rgba(255, 50, 50, 0.2);
        }

        .signal-info {
            flex: 1;
        }

        .signal-symbol {
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 5px;
        }

        .signal-details {
            font-size: 12px;
            color: #888;
        }

        .pipeline-stages {
            display: flex;
            gap: 10px;
            margin-top: 10px;
        }

        .stage {
            padding: 4px 8px;
            border-radius: 15px;
            font-size: 10px;
            font-weight: 600;
            background: rgba(255, 255, 255, 0.05);
            color: #666;
            transition: all 0.3s;
        }

        .stage.active {
            background: rgba(0, 150, 255, 0.3);
            color: #00d4ff;
            animation: pulse 1s infinite;
        }

        .stage.completed {
            background: rgba(0, 255, 100, 0.2);
            color: #00ff64;
        }

        @keyframes pulse {
            0%, 100% {
                opacity: 1;
            }
            50% {
                opacity: 0.7;
            }
        }

        /* Right Panel - Logs */
        .logs-panel {
            background: rgba(15, 15, 25, 0.9);
            padding: 15px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }

        .logs-header {
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 15px;
            color: #aaa;
            text-transform: uppercase;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .log-filter {
            display: flex;
            gap: 5px;
        }

        .filter-btn {
            padding: 3px 8px;
            border-radius: 10px;
            font-size: 10px;
            background: rgba(255, 255, 255, 0.05);
            color: #888;
            cursor: pointer;
            border: 1px solid transparent;
            transition: all 0.2s;
        }

        .filter-btn.active {
            background: rgba(0, 150, 255, 0.2);
            color: #00d4ff;
            border-color: rgba(0, 150, 255, 0.3);
        }

        .logs-container {
            flex: 1;
            overflow-y: auto;
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 11px;
            line-height: 1.6;
        }

        .log-entry {
            padding: 5px;
            border-left: 2px solid transparent;
            margin-bottom: 2px;
            animation: fadeIn 0.3s;
        }

        @keyframes fadeIn {
            from {
                opacity: 0;
            }
            to {
                opacity: 1;
            }
        }

        .log-INFO {
            border-left-color: #00d4ff;
            color: #00d4ff;
        }

        .log-SUCCESS {
            border-left-color: #00ff64;
            color: #00ff64;
        }

        .log-WARNING {
            border-left-color: #ffaa00;
            color: #ffaa00;
        }

        .log-ERROR {
            border-left-color: #ff3232;
            color: #ff3232;
        }

        .log-timestamp {
            color: #666;
            margin-right: 10px;
        }

        .log-module {
            color: #888;
            margin-right: 10px;
        }

        /* Controls */
        .controls {
            position: fixed;
            bottom: 20px;
            right: 370px;
            display: flex;
            gap: 10px;
        }

        .control-btn {
            padding: 10px 20px;
            border-radius: 25px;
            background: linear-gradient(135deg, #0099ff, #00d4ff);
            color: white;
            border: none;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            box-shadow: 0 4px 15px rgba(0, 150, 255, 0.3);
        }

        .control-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0, 150, 255, 0.5);
        }

        .control-btn:active {
            transform: translateY(0);
        }

        /* Scrollbar styling */
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }

        ::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.05);
        }

        ::-webkit-scrollbar-thumb {
            background: rgba(0, 150, 255, 0.3);
            border-radius: 3px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: rgba(0, 150, 255, 0.5);
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <h1>🚀 Trading Bot Flow</h1>
            <div class="status-badge status-offline" id="status">CONNECTING...</div>
            <div style="margin-left: auto; display: flex; gap: 20px; align-items: center;">
                <div style="font-size: 12px;">
                    <span style="color: #888;">Balance:</span>
                    <span id="balance" style="font-weight: 600;">$0.00</span>
                </div>
                <div style="font-size: 12px;">
                    <span style="color: #888;">PNL:</span>
                    <span id="pnl" style="font-weight: 600;">$0.00</span>
                </div>
            </div>
        </div>

        <!-- Sidebar - Metrics -->
        <div class="sidebar">
            <div class="metric-card">
                <div class="metric-label">Signals Received</div>
                <div class="metric-value" id="signals-received">0</div>
                <div class="metric-change positive">↑ Real-time</div>
            </div>

            <div class="metric-card">
                <div class="metric-label">Signals Processed</div>
                <div class="metric-value" id="signals-processed">0</div>
                <div class="metric-change positive">Processing</div>
            </div>

            <div class="metric-card">
                <div class="metric-label">Open Positions</div>
                <div class="metric-value" id="positions-count">0</div>
                <div class="metric-change">Active</div>
            </div>

            <div class="metric-card">
                <div class="metric-label">Win Rate</div>
                <div class="metric-value" id="win-rate">0%</div>
                <div class="metric-change positive">Performance</div>
            </div>

            <div class="metric-card">
                <div class="metric-label">Total PNL</div>
                <div class="metric-value" id="total-pnl">$0</div>
                <div class="metric-change">Cumulative</div>
            </div>
        </div>

        <!-- Main - Pipeline View -->
        <div class="main">
            <div class="pipeline-container">
                <div class="pipeline-header">Signal Processing Pipeline</div>
                <div class="signal-flow" id="signal-flow">
                    <!-- Signals will be added here dynamically -->
                </div>
            </div>
        </div>

        <!-- Right Panel - Logs -->
        <div class="logs-panel">
            <div class="logs-header">
                <span>Live Logs</span>
                <div class="log-filter">
                    <div class="filter-btn active" onclick="filterLogs('ALL')">ALL</div>
                    <div class="filter-btn" onclick="filterLogs('INFO')">INFO</div>
                    <div class="filter-btn" onclick="filterLogs('ERROR')">ERROR</div>
                </div>
            </div>
            <div class="logs-container" id="logs">
                <!-- Logs will be added here -->
            </div>
        </div>
    </div>

    <!-- Controls -->
    <div class="controls">
        <button class="control-btn" onclick="startBot()">▶️ Start Bot</button>
        <button class="control-btn" onclick="stopBot()">⏹ Stop Bot</button>
        <button class="control-btn" onclick="clearLogs()">🗑 Clear</button>
    </div>

    <script>
        let ws = null;
        let logFilter = 'ALL';
        let signals = {};

        function connect() {
            ws = new WebSocket(`ws://${window.location.host}/ws`);

            ws.onopen = () => {
                console.log('WebSocket connected');
                updateStatus('ONLINE');
            };

            ws.onmessage = (event) => {
                const message = JSON.parse(event.data);
                handleMessage(message);
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected');
                updateStatus('OFFLINE');
                setTimeout(connect, 2000);
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        }

        function handleMessage(message) {
            switch(message.type) {
                case 'log':
                    addLog(message.data);
                    break;
                case 'signal':
                    addSignal(message.data);
                    break;
                case 'pipeline_update':
                    updatePipeline(message.data);
                    break;
                case 'account_update':
                    updateAccount(message.data);
                    break;
                case 'metrics_update':
                    updateMetrics(message.data);
                    break;
            }
        }

        function updateStatus(status) {
            const badge = document.getElementById('status');
            badge.textContent = status;
            badge.className = `status-badge status-${status.toLowerCase()}`;
        }

        function addLog(log) {
            const logsContainer = document.getElementById('logs');
            const entry = document.createElement('div');
            entry.className = `log-entry log-${log.level}`;
            entry.dataset.level = log.level;

            const timestamp = new Date(log.timestamp).toLocaleTimeString();
            entry.innerHTML = `
                <span class="log-timestamp">${timestamp}</span>
                <span class="log-module">[${log.module}]</span>
                <span>${log.message}</span>
            `;

            logsContainer.insertBefore(entry, logsContainer.firstChild);

            // Keep only last 100 logs
            while (logsContainer.children.length > 100) {
                logsContainer.removeChild(logsContainer.lastChild);
            }

            // Apply filter
            if (logFilter !== 'ALL' && log.level !== logFilter) {
                entry.style.display = 'none';
            }
        }

        function addSignal(signal) {
            signals[signal.id] = signal;
            const container = document.getElementById('signal-flow');

            const card = document.createElement('div');
            card.className = 'signal-card';
            card.id = `signal-${signal.id}`;

            const iconClass = signal.data.type === 'BUY' ? 'signal-buy' : 'signal-sell';

            card.innerHTML = `
                <div class="signal-icon ${iconClass}">
                    ${signal.data.type === 'BUY' ? '📈' : '📉'}
                </div>
                <div class="signal-info">
                    <div class="signal-symbol">${signal.data.symbol}</div>
                    <div class="signal-details">
                        ${signal.data.type} @ $${signal.data.price} |
                        SL: $${signal.data.stop_loss} |
                        TP: $${signal.data.take_profit} |
                        Confidence: ${(signal.data.confidence * 100).toFixed(0)}%
                    </div>
                    <div class="pipeline-stages">
                        <div class="stage ${signal.pipeline.received ? 'completed' : ''}" id="${signal.id}-received">Received</div>
                        <div class="stage ${signal.pipeline.validated ? 'completed' : ''}" id="${signal.id}-validated">Validated</div>
                        <div class="stage ${signal.pipeline.analyzed ? 'completed' : ''}" id="${signal.id}-analyzed">Analyzed</div>
                        <div class="stage ${signal.pipeline.executed ? 'completed' : ''}" id="${signal.id}-executed">Executed</div>
                        <div class="stage ${signal.pipeline.completed ? 'completed' : ''}" id="${signal.id}-completed">Completed</div>
                    </div>
                </div>
            `;

            container.insertBefore(card, container.firstChild);

            // Keep only last 10 signals visible
            while (container.children.length > 10) {
                container.removeChild(container.lastChild);
            }
        }

        function updatePipeline(data) {
            const stages = ['received', 'validated', 'analyzed', 'executed', 'completed'];

            stages.forEach(stage => {
                const element = document.getElementById(`${data.signal_id}-${stage}`);
                if (element) {
                    if (data.pipeline[stage]) {
                        element.classList.add('completed');
                        element.classList.remove('active');
                    } else if (data.stage === stage) {
                        element.classList.add('active');
                    }
                }
            });
        }

        function updateAccount(data) {
            document.getElementById('balance').textContent = `$${data.balance.toFixed(2)}`;
            document.getElementById('pnl').textContent = `$${data.pnl.toFixed(2)}`;
            document.getElementById('pnl').className = data.pnl >= 0 ? 'positive' : 'negative';

            if (data.positions_count !== undefined) {
                document.getElementById('positions-count').textContent = data.positions_count;
            }
        }

        function updateMetrics(data) {
            document.getElementById('signals-received').textContent = data.signals_received;
            document.getElementById('signals-processed').textContent = data.signals_processed;
            document.getElementById('win-rate').textContent = `${data.win_rate.toFixed(1)}%`;
            document.getElementById('total-pnl').textContent = `$${data.total_pnl.toFixed(2)}`;
        }

        function filterLogs(level) {
            logFilter = level;
            document.querySelectorAll('.filter-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.classList.add('active');

            document.querySelectorAll('.log-entry').forEach(entry => {
                if (level === 'ALL' || entry.dataset.level === level) {
                    entry.style.display = 'block';
                } else {
                    entry.style.display = 'none';
                }
            });
        }

        function clearLogs() {
            document.getElementById('logs').innerHTML = '';
        }

        async function startBot() {
            const response = await fetch('/api/bot/start', { method: 'POST' });
            const data = await response.json();
            console.log('Bot started:', data);
        }

        async function stopBot() {
            const response = await fetch('/api/bot/stop', { method: 'POST' });
            const data = await response.json();
            console.log('Bot stopped:', data);
        }

        // Connect on load
        connect();
    </script>
</body>
</html>
""")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await websocket.accept()
    state.websocket_clients.append(websocket)

    # Send initial state
    await websocket.send_json({
        "type": "initial_state",
        "data": {
            "status": state.bot_status,
            "account": state.account_info,
            "metrics": state.metrics
        }
    })

    try:
        while True:
            # Keep connection alive
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        state.websocket_clients.remove(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if websocket in state.websocket_clients:
            state.websocket_clients.remove(websocket)

@app.get("/api/status")
async def get_status():
    """Get current system status"""
    return JSONResponse({
        "status": state.bot_status,
        "account": state.account_info,
        "positions": list(state.positions.values()),
        "metrics": state.metrics,
        "logs_count": len(state.logs),
        "signals_count": len(state.signals)
    })

@app.post("/api/bot/start")
async def start_bot():
    """Start the trading bot"""
    state.bot_status = "online"
    await state.add_log("SUCCESS", "Bot started manually", "control")
    return {"status": "started"}

@app.post("/api/bot/stop")
async def stop_bot():
    """Stop the trading bot"""
    state.bot_status = "offline"
    await state.add_log("WARNING", "Bot stopped manually", "control")
    return {"status": "stopped"}

@app.post("/api/signal")
async def receive_signal(signal: dict):
    """Receive a new trading signal"""
    await state.add_signal(signal)
    return {"status": "received", "signal_id": signal.get("id")}

if __name__ == "__main__":
    print("🚀 Starting N8n-style Interactive Dashboard...")
    print("📊 Open http://localhost:8000 in your browser")
    print("⚡ Ultra-fast WebSocket updates (100ms)")
    print("📝 Real-time log streaming enabled")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")