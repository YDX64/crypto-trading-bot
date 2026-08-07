"""
Ana uygulama giriş noktası.
FastAPI server ve Telegram bot'u çalıştırır.

GÜVENLİK: İşlem açtırabilen endpoint'ler API anahtarı ister. Anahtar
yapılandırılmamışsa endpoint AÇIK KALMAZ, kapatılır (fail-closed).
"""

import asyncio
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Security, status
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.logger import app_logger
from src.core.database import init_db, close_db, get_db
from src.models.waiting_signal import WaitingSignalModel, WaitingStatus
from src.models.scalp_trade import ScalpTradeModel
from src.services.telegram_bot import TelegramBotService
from src.services.orchestrator import TradingOrchestrator
from src.strategies.scalper.engine import ScalperEngine


# Global instances — tek orchestrator, Telegram servisiyle PAYLAŞILIR
telegram_bot: Optional[TelegramBotService] = None
orchestrator: Optional[TradingOrchestrator] = None
scalper_engine: Optional[ScalperEngine] = None


# ---------------------------------------------------------------------------
# Kimlik doğrulama
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    """İşlem açtırabilen endpoint'ler için API anahtarı doğrula.

    Anahtar yapılandırılmamışsa endpoint kullanılamaz. "Anahtar yoksa kontrolü
    atla" davranışı, eksik kurulumu sessizce açık kapıya çevirirdi.
    """
    configured = settings.api_key
    if not configured:
        app_logger.error(
            "🔒 API_KEY yapılandırılmamış — korumalı endpoint reddedildi. "
            "Kullanmak için .env dosyasına API_KEY=<güçlü-rastgele-değer> ekleyin."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_KEY yapılandırılmamış. Bu endpoint devre dışı.",
        )

    if not api_key or not _constant_time_equals(api_key, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya eksik X-API-Key başlığı",
        )
    return api_key


def _constant_time_equals(a: str, b: str) -> bool:
    """Zamanlama saldırısına kapalı karşılaştırma."""
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Yaşam döngüsü
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama yaşam döngüsü"""
    app_logger.info("=" * 80)
    app_logger.info("🚀 TRADING BOT BAŞLATILIYOR")
    app_logger.info("=" * 80)

    await init_db()
    app_logger.info("✅ Veritabanı hazır")

    global telegram_bot, orchestrator, scalper_engine

    # TEK orchestrator oluştur ve Telegram servisine PAYLAŞTIR.
    # İki ayrı örnek olursa sinyali işleyen örneğin izleme döngüsü çalışmaz.
    orchestrator = TradingOrchestrator()
    telegram_bot = TelegramBotService(orchestrator=orchestrator)

    # Orchestrator'ı önce başlat: açık pozisyonlar kurtarılsın ve izleme
    # döngüsü, ilk sinyal gelmeden önce ayakta olsun.
    await orchestrator.start()
    app_logger.info("✅ Trading Orchestrator başlatıldı")

    # Scalper motoru — orchestrator'dan bağımsız kendi istemci çiftiyle
    # çalışır (Telegram sinyal akışını asla bloklamaz).
    if settings.scalper_enabled:
        scalper_engine = ScalperEngine()
        await scalper_engine.start()
        app_logger.info("✅ Scalper motoru başlatıldı")

    asyncio.create_task(telegram_bot.start())
    app_logger.info("✅ Telegram bot başlatıldı")

    app_logger.info("=" * 80)
    app_logger.info(f"✅ TRADING BOT HAZIR - {settings.app_env.upper()}")
    app_logger.info(f"🌐 Ortam: {'TESTNET' if settings.is_testnet else '⚠️ MAINNET (GERÇEK PARA)'}")
    app_logger.info(f"📊 API Server: http://{settings.api_host}:{settings.api_port}")
    app_logger.info(f"⚡ Risk: %{settings.risk_percentage}")
    app_logger.info(f"🎯 İlk TP: %{settings.first_tp_percentage}")
    app_logger.info(f"🔄 Trailing: %{settings.trailing_stop_percentage}")
    app_logger.info(f"🔒 /signal endpoint: {'korumalı' if settings.api_key else 'DEVRE DIŞI (API_KEY yok)'}")
    app_logger.info("=" * 80)

    yield

    app_logger.info("🛑 Uygulama kapatılıyor...")
    if telegram_bot:
        await telegram_bot.stop()
    if scalper_engine:
        await scalper_engine.stop()
    if orchestrator:
        await orchestrator.close()
    await close_db()
    app_logger.info("✅ Uygulama kapatıldı")


app = FastAPI(
    title="VIP Trading Bot API",
    description="Otonom Kripto Trading Bot - Telegram Sinyalleri + AI Analiz + Trailing SL/TP",
    version="2.0.0",
    lifespan=lifespan,
)


static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ---------------------------------------------------------------------------
# Genel bilgi endpoint'leri
# ---------------------------------------------------------------------------

@app.get("/dashboard")
async def dashboard():
    """Trading Bot Dashboard"""
    path = os.path.join(static_dir, "dashboard.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="dashboard.html bulunamadı")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/")
async def root():
    """Ana sayfa"""
    return {
        "name": "VIP Trading Bot",
        "version": "2.0.0",
        "status": "running",
        "environment": settings.app_env,
        "network": "testnet" if settings.is_testnet else "mainnet",
        "features": [
            "Telegram Signal Integration",
            "AI-Powered Analysis (3x, fail-closed)",
            "Automatic Position Management",
            "Trailing Stop Loss",
            "Break-Even Protection",
            "Risk-Based Position Sizing",
            "Restart Position Recovery",
        ],
    }


@app.get("/health")
async def health_check():
    """Sağlık kontrolü — gerçek durumu yansıtır."""
    monitoring_alive = bool(
        orchestrator
        and orchestrator.monitoring_task
        and not orchestrator.monitoring_task.done()
    )
    healthy = bool(telegram_bot and orchestrator and monitoring_alive)

    if not settings.scalper_enabled:
        scalper_state = "disabled"
    else:
        scalper_state = "running" if (scalper_engine and scalper_engine.running) else "stopped"

    body = {
        "status": "healthy" if healthy else "degraded",
        "timestamp": _utcnow_iso(),
        "telegram_bot": "running" if telegram_bot else "stopped",
        "orchestrator": "running" if orchestrator else "stopped",
        "position_monitoring": "running" if monitoring_alive else "STOPPED",
        "tracked_positions": len(orchestrator.active_positions) if orchestrator else 0,
        "network": "testnet" if settings.is_testnet else "mainnet",
        "scalper": scalper_state,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=body)


@app.get("/api/status")
async def api_status():
    """Sistem durumu — Binance hataları gizlenmez."""
    account = {"balance": None, "btc_price": None, "open_positions": None}
    errors = []

    if orchestrator:
        client = orchestrator.binance
        try:
            account["balance"] = await client.get_account_balance()
        except Exception as e:
            errors.append(f"balance: {e}")
        try:
            account["btc_price"] = await client.get_current_price("BTCUSDT")
        except Exception as e:
            errors.append(f"price: {e}")
        try:
            account["open_positions"] = len(await client.get_all_positions())
        except Exception as e:
            errors.append(f"positions: {e}")
    else:
        errors.append("orchestrator başlatılmadı")

    return {
        "status": "running" if not errors else "degraded",
        "bot_active": telegram_bot is not None,
        "orchestrator_active": orchestrator is not None,
        "account": account,
        "errors": errors,
        "config": {
            "risk_percentage": settings.risk_percentage,
            "max_positions": settings.max_positions,
            "first_tp": settings.first_tp_percentage,
            "trailing_stop": settings.trailing_stop_percentage,
        },
        "timestamp": _utcnow_iso(),
    }


@app.get("/positions")
async def get_positions():
    """Bot tarafından izlenen açık pozisyonlar"""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator hazır değil")

    positions = []
    for symbol, position in orchestrator.active_positions.items():
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
            "opened_at": position.opened_at.isoformat() if position.opened_at else None,
        })

    return {"count": len(positions), "positions": positions}


@app.get("/stats")
async def get_stats():
    """İstatistikler"""
    return {
        "active_positions": len(orchestrator.active_positions) if orchestrator else 0,
        "max_positions": settings.max_positions,
        "risk_percentage": settings.risk_percentage,
        "first_tp_percentage": settings.first_tp_percentage,
        "trailing_stop_percentage": settings.trailing_stop_percentage,
        "check_interval": settings.check_interval_seconds,
        "margin_type": settings.margin_type,
        "timestamp": _utcnow_iso(),
    }


@app.get("/config")
async def get_config():
    """Konfigürasyon (gizli değer içermez)"""
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
        "signal_endpoint_protected": bool(settings.api_key),
        "waiting_mode_enabled": settings.waiting_mode_enabled,
        "waiting_mode_max_positions": settings.waiting_mode_max_positions,
        "waiting_mode_max_hours": settings.waiting_mode_max_hours,
        "waiting_mode_check_interval_minutes": settings.waiting_mode_check_interval_minutes,
        "waiting_mode_min_conditions": settings.waiting_mode_min_conditions,
        "waiting_mode_price_improvement": settings.waiting_mode_price_improvement,
    }


# ---------------------------------------------------------------------------
# KORUMALI: işlem açtırabilen endpoint'ler
# ---------------------------------------------------------------------------

class SignalRequest(BaseModel):
    message: str


@app.post("/signal", dependencies=[Depends(require_api_key)])
async def manual_signal(
    signal_data: SignalRequest,
    db: AsyncSession = Depends(get_db),
):
    """Manuel sinyal gönder — GERÇEK EMİR AÇAR, API anahtarı zorunludur."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator hazır değil")

    try:
        position = await orchestrator.process_signal(signal_data.message, db)
    except Exception as e:
        app_logger.error(f"Manuel sinyal hatası: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    if not position:
        return {"success": False, "message": "Sinyal reddedildi veya işlem açılamadı"}

    return {
        "success": True,
        "message": "Pozisyon açıldı",
        "position": {
            "symbol": position.symbol,
            "side": position.side.value,
            "entry_price": position.entry_price,
            "quantity": position.quantity,
            "leverage": position.leverage,
            "stop_loss": position.current_stoploss,
        },
    }


# ---------------------------------------------------------------------------
# Bekleme modu
# ---------------------------------------------------------------------------

@app.get("/waiting-mode/active")
async def get_active_waiting_signals(db: AsyncSession = Depends(get_db)):
    """Aktif bekleyen sinyaller"""
    result = await db.execute(
        select(WaitingSignalModel).where(WaitingSignalModel.status == WaitingStatus.WAITING)
    )
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
            "last_checked_at": ws.last_checked_at.isoformat() if ws.last_checked_at else None,
        }
        for ws in result.scalars().all()
    ]


@app.get("/waiting-mode/history")
async def get_waiting_history(db: AsyncSession = Depends(get_db)):
    """Bekleme modu geçmişi"""
    result = await db.execute(
        select(WaitingSignalModel).order_by(WaitingSignalModel.created_at.desc()).limit(20)
    )
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
            "executed_at": ws.executed_at.isoformat() if ws.executed_at else None,
        }
        for ws in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# Scalper motoru
# ---------------------------------------------------------------------------

_EMPTY_SCALPER_STATUS = {
    "enabled": False,
    "running": False,
    "scan_interval": settings.scalper_scan_interval_seconds,
    "universe": [],
    "regimes": {},
    "daily_pnl": 0.0,
    "daily_limit_pct": settings.scalper_daily_loss_limit_pct,
    "kill_switch_active": False,
    "signals_today": 0,
    "last_scan_at": None,
    "tracked": [],
}


@app.get("/scalper/status")
async def scalper_status():
    """Scalper motorunun anlık durumu (tarama evreni, rejimler, izlenen pozisyonlar)."""
    if not scalper_engine:
        return dict(_EMPTY_SCALPER_STATUS)
    return scalper_engine.snapshot()


@app.get("/scalper/stats")
async def scalper_stats(db: AsyncSession = Depends(get_db)):
    """Strateji bazlı ve toplam (combined) kapanmış scalp işlem istatistikleri."""
    strategies: dict = {}
    if scalper_engine:
        strategies = await scalper_engine.tracker.stats()

    result = await db.execute(
        select(ScalpTradeModel).where(ScalpTradeModel.status == "CLOSED")
    )
    rows = list(result.scalars().all())

    n = len(rows)
    wins = [r for r in rows if (r.realized_pnl or 0.0) > 0]
    losses = [r for r in rows if (r.realized_pnl or 0.0) < 0]
    total_pnl = sum(r.realized_pnl or 0.0 for r in rows)
    avg_roi = (sum(r.roi_pct or 0.0 for r in rows) / n) if n else 0.0
    gross_profit = sum(r.realized_pnl or 0.0 for r in wins)
    gross_loss = abs(sum(r.realized_pnl or 0.0 for r in losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    combined = {
        "trades": n,
        "wins": len(wins),
        "winrate": (len(wins) / n * 100.0) if n else 0.0,
        "total_pnl": total_pnl,
        "avg_roi": avg_roi,
        "profit_factor": profit_factor,
    }

    return {"strategies": strategies, "combined": combined}


@app.get("/scalper/trades")
async def scalper_trades(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Kapanmış scalp işlemleri, en yeni önce."""
    result = await db.execute(
        select(ScalpTradeModel)
        .where(ScalpTradeModel.status == "CLOSED")
        .order_by(ScalpTradeModel.closed_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": t.id,
            "strategy": t.strategy,
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "realized_pnl": t.realized_pnl,
            "roi_pct": t.roi_pct,
            "exit_reason": t.exit_reason,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            "signal_reason": t.signal_reason,
        }
        for t in result.scalars().all()
    ]


async def main():
    """Ana fonksiyon"""
    import uvicorn

    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=settings.debug,
    )
    server = uvicorn.Server(config)

    def signal_handler(sig, frame):
        app_logger.info(f"Signal {sig} alındı, kapatılıyor...")
        server.should_exit = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
