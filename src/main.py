"""
Ana uygulama giriş noktası.
FastAPI server ve Telegram bot'u çalıştırır.

GÜVENLİK: İşlem açtırabilen endpoint'ler API anahtarı ister. Anahtar
yapılandırılmamışsa endpoint AÇIK KALMAZ, kapatılır (fail-closed).
"""

import asyncio
import hmac
import json
import logging
import math
import os
import re
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, Security, status
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
from src.strategies.scalper.tracker import ScalpTracker


# ---------------------------------------------------------------------------
# GÜVENLİK: erişim logu secret sızıntısı (2026-08-21)
# ---------------------------------------------------------------------------
# uvicorn'un `uvicorn.access` logger'ı tam istek satırını (?secret=... dahil)
# düz metin yazar (bkz. logs/supervisor.log — CLAUDE.md "secret içerir, dökme").
# `python -m uvicorn src.main:app` ile başlatıldığında bu modül import
# edilirken uvicorn kendi logging yapılandırmasını Config.__init__'te (import
# öncesinde ya da sonrasında olabilir) kurar; filtre LOGGER nesnesine
# eklendiği için handler'ların ne zaman bağlandığından bağımsız her zaman
# devreye girer. Modül kapsamında + idempotent: reload'da tekrar eklenmez.
_SECRET_QS_RE = re.compile(r"(secret=)[^&\s\"']+", re.IGNORECASE)


def _redact_secret_value(value):
    """Bir log alanındaki (msg ya da tek bir arg) `secret=...` değerini maskele."""
    if isinstance(value, str) and _SECRET_QS_RE.search(value):
        return _SECRET_QS_RE.sub(r"\1***", value)
    return value


class _SecretRedactionLogFilter(logging.Filter):
    """`secret=<değer>`i `secret=***` yapan logging.Filter.

    LogRecord formatlanmadan ÖNCE (yani %-interpolasyonundan önce) hem
    `record.msg` hem `record.args` üzerinde çalışır — uvicorn erişim logu
    `'%s - "%s %s HTTP/%s" %d'` gibi bir şablonu ayrı args ile doldurur,
    secret query string'de olduğu için genelde args içindeki path elemanında
    bulunur.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_secret_value(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_secret_value(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _redact_secret_value(v) for k, v in record.args.items()}
        elif record.args is not None:
            record.args = _redact_secret_value(record.args)
        return True


def _install_access_log_secret_redaction() -> None:
    for _name in ("uvicorn.access", "uvicorn.error"):
        _target = logging.getLogger(_name)
        if not any(
            isinstance(_f, _SecretRedactionLogFilter) for _f in _target.filters
        ):
            _target.addFilter(_SecretRedactionLogFilter())


_install_access_log_secret_redaction()


# Global instances — tek orchestrator, Telegram servisiyle PAYLAŞILIR
telegram_bot: Optional[TelegramBotService] = None
orchestrator: Optional[TradingOrchestrator] = None
scalper_engine: Optional[ScalperEngine] = None
telegram_supervisor_task: Optional[asyncio.Task] = None


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


def _finite_or_none(value):
    """JSON'un temsil edemediği NaN/+Inf/-Inf sayılarını null'a çevir."""
    if value is None:
        return None
    try:
        return value if math.isfinite(float(value)) else None
    except (TypeError, ValueError):
        return value


# ---------------------------------------------------------------------------
# Yaşam döngüsü
# ---------------------------------------------------------------------------

async def _telegram_supervisor(service: TelegramBotService) -> None:
    """Keep Telegram retriable without taking the safety/scalper loops down."""

    backoff = 5.0
    while True:
        try:
            if not getattr(service, "is_running", False):
                await service.start()
                app_logger.info("✅ Telegram supervisor: servis hazır")
                backoff = 5.0

            await asyncio.sleep(10)
            if not getattr(service, "is_running", False):
                await service.stop()
                raise RuntimeError("Telegram polling/queue beklenmeden durdu")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            app_logger.error(
                f"⚠️ Telegram geçici olarak kullanılamıyor; scalper/safety çalışmaya "
                f"devam ediyor. {backoff:.0f}sn sonra tekrar denenecek: {e}"
            )
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama yaşam döngüsü"""
    global telegram_bot, orchestrator, scalper_engine, telegram_supervisor_task

    app_logger.info("=" * 80)
    app_logger.info("🚀 TRADING BOT BAŞLATILIYOR")
    app_logger.info("=" * 80)

    db_ready = False
    try:
        await init_db()
        db_ready = True
        app_logger.info("✅ Veritabanı hazır")

        # TEK orchestrator oluştur ve Telegram servisine PAYLAŞTIR.
        # İki ayrı örnek olursa sinyali işleyen örneğin izleme döngüsü çalışmaz.
        orchestrator = TradingOrchestrator()
        telegram_bot = TelegramBotService(orchestrator=orchestrator)

        # Scalper önce recover edilir. Böylece crash anında dolmuş bir maker
        # intent'i/OPEN scalp, genel orchestrator tarafından "yetim Telegram
        # pozisyonu" diye sahiplenilmeden önce journal + DB ile uzlaştırılır.
        if settings.scalper_enabled:
            scalper_engine = ScalperEngine()
            await scalper_engine.start()
            app_logger.info("✅ Scalper motoru görevleri başlatıldı")

        # Geriye kalan (scalper sahipliğinde olmayan) açık pozisyonları genel
        # orchestrator kurtarır ve izlemeye alır.
        await orchestrator.start()
        app_logger.info("✅ Trading Orchestrator başlatıldı")

        # Telegram ağ/409 hatası scalper safety döngülerini öldürmemeli.
        # Supervisor tam yaşam döngüsünü await eder ve bounded backoff ile
        # yeniden dener; health Telegram'ı ayrı bir bileşen olarak raporlar.
        try:
            await telegram_bot.start()
            app_logger.info("✅ Telegram bot ilk denemede hazır")
        except Exception as e:
            app_logger.error(
                f"⚠️ Telegram ilk başlatma başarısız; scalper/safety çalışıyor ve "
                f"supervisor yeniden deneyecek: {e}"
            )
        telegram_supervisor_task = asyncio.create_task(
            _telegram_supervisor(telegram_bot), name="telegram-supervisor"
        )

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
    finally:
        app_logger.info("🛑 Uygulama kapatılıyor...")
        if telegram_supervisor_task is not None:
            telegram_supervisor_task.cancel()
            await asyncio.gather(telegram_supervisor_task, return_exceptions=True)
            telegram_supervisor_task = None
        for label, closer in (
            ("Telegram bot", telegram_bot.stop if telegram_bot else None),
            ("Scalper motoru", scalper_engine.stop if scalper_engine else None),
            ("Trading Orchestrator", orchestrator.close if orchestrator else None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception as e:
                app_logger.error(f"❌ {label} kapatılırken hata: {e}", exc_info=True)
        if db_ready:
            try:
                await close_db()
            except Exception as e:
                app_logger.error(f"❌ Veritabanı kapatılırken hata: {e}", exc_info=True)

        telegram_bot = None
        scalper_engine = None
        orchestrator = None
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
    if orchestrator and hasattr(orchestrator, "health_snapshot"):
        orchestrator_health = orchestrator.health_snapshot()
    elif orchestrator:
        legacy_monitor_alive = bool(
            getattr(orchestrator, "monitoring_task", None)
            and not orchestrator.monitoring_task.done()
        )
        orchestrator_health = {
            "healthy": legacy_monitor_alive,
            "monitoring_task_alive": legacy_monitor_alive,
            "reason": "legacy_health_adapter",
        }
    else:
        orchestrator_health = {
            "healthy": False,
            "monitoring_task_alive": False,
            "reason": "orchestrator_not_created",
        }
    monitoring_alive = bool(orchestrator_health.get("monitoring_task_alive"))
    telegram_health = (
        telegram_bot.health_snapshot()
        if telegram_bot
        else {"healthy": False, "reason": "service_not_created"}
    )

    if not settings.scalper_enabled:
        scalper_state = "disabled"
        scalper_health = {"healthy": True, "enabled": False}
    else:
        scalper_health = (
            scalper_engine.health_snapshot()
            if scalper_engine
            else {"healthy": False, "running": False, "reason": "engine_not_created"}
        )
        if scalper_health.get("healthy"):
            scalper_state = "running"
        elif scalper_engine and scalper_engine.running:
            scalper_state = "degraded"
        else:
            scalper_state = "stopped"

    core_healthy = bool(
        orchestrator_health.get("healthy") and scalper_health.get("healthy")
    )
    fully_healthy = bool(core_healthy and telegram_health.get("healthy"))

    body = {
        "status": "healthy" if fully_healthy else "degraded",
        "core_healthy": core_healthy,
        "timestamp": _utcnow_iso(),
        "telegram_bot": "running" if telegram_health.get("healthy") else "stopped",
        "telegram_details": telegram_health,
        "orchestrator": "running" if orchestrator else "stopped",
        "orchestrator_details": orchestrator_health,
        "position_monitoring": "running" if monitoring_alive else "STOPPED",
        "tracked_positions": len(orchestrator.active_positions) if orchestrator else 0,
        "network": "testnet" if settings.is_testnet else "mainnet",
        "scalper": scalper_state,
        "scalper_details": scalper_health,
    }
    # Telegram has its own retry supervisor.  Its outage is reported as
    # degraded but does not provoke a process restart while the trading core
    # and protection loops remain healthy.
    return JSONResponse(status_code=200 if core_healthy else 503, content=body)


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
            # Gösterim amaçlı sayaç: force_fresh=False şart. Panel 5 sn'de bir
            # bu endpoint'i çağırıyor; taze zorlamak rate-limiter kuyruğunu
            # doyurup scan döngüsünü bayatlatıyordu (2026-08-18 degraded olayı).
            account["open_positions"] = len(
                await client.get_all_positions(force_fresh=False)
            )
        except Exception as e:
            errors.append(f"positions: {e}")
    else:
        errors.append("orchestrator başlatılmadı")

    return {
        "status": "running" if not errors else "degraded",
        "bot_active": bool(telegram_bot and telegram_bot.is_running),
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
        # Yalnız sayısal/politika alanları: anahtar, credential, endpoint veya
        # emir kimliği içermez. Dashboard bu blokla TESTNET çıkış davranışını
        # açıklayabilir; buradan hiçbir ayar değiştirilemez.
        "scalper_exit_policy": {
            "active_network": "testnet" if settings.is_testnet else "mainnet",
            "testnet_profile": settings.is_testnet,
            "entry_mode": getattr(settings, "scalper_entry_mode", None),
            "tp1_roi_pct": getattr(settings, "scalper_tp1_roi", None),
            "tp1_fraction": getattr(settings, "scalper_tp1_fraction", None),
            "tp2_roi_pct": getattr(settings, "scalper_tp2_roi", None),
            "tp2_fraction": getattr(settings, "scalper_tp2_fraction", None),
            "breakeven_buffer_pct": getattr(
                settings, "scalper_breakeven_buffer_pct", None
            ),
            "maker_fee_pct": getattr(settings, "scalper_maker_fee_pct", None),
            "taker_fee_pct": getattr(settings, "scalper_taker_fee_pct", None),
            "chandelier_atr_mult": getattr(
                settings, "scalper_chandelier_atr_mult", None
            ),
            "chandelier_atr_period": getattr(
                settings, "scalper_chandelier_atr_period", None
            ),
            "min_stop_pct": getattr(settings, "scalper_min_stop_pct", None),
            "max_stop_pct": getattr(settings, "scalper_max_stop_pct", None),
            "protection_failure_cooldown_minutes": getattr(
                settings, "scalper_protection_failure_cooldown_minutes", None
            ),
            "loss_cooldown_minutes": getattr(
                settings, "scalper_loss_cooldown_minutes", None
            ),
            "cooldown_state_path": getattr(
                settings, "scalper_cooldown_state_path", None
            ),
        },
        "scalper_risk_policy": {
            "stop_mode": getattr(settings, "scalper_stop_mode", None),
            "fixed_stop_roi_pct": getattr(
                settings, "scalper_fixed_stop_roi_pct", None
            ),
            "c_require_flow_confirm": getattr(
                settings, "scalper_c_require_flow_confirm", None
            ),
            "c_require_reversal_zone": getattr(
                settings, "scalper_c_require_reversal_zone", None
            ),
            "stop_atr_floor_mult": getattr(
                settings, "scalper_stop_atr_floor_mult", None
            ),
            "leverage": getattr(settings, "scalper_leverage", None),
            "dynamic_leverage": getattr(settings, "scalper_dynamic_leverage", None),
            "dyn_lev_stop_atr_mult": getattr(
                settings, "scalper_dyn_lev_stop_atr_mult", None
            ),
            "dyn_lev_bounds": [
                getattr(settings, "scalper_dyn_lev_min", None),
                getattr(settings, "scalper_dyn_lev_max", None),
            ],
            "risk_percentage": getattr(settings, "scalper_risk_percentage", None),
            "max_positions": getattr(settings, "scalper_max_positions", None),
            "max_margin_pct": getattr(settings, "scalper_max_margin_pct", None),
            "min_rr": getattr(settings, "scalper_min_rr", None),
            "daily_loss_limit_pct": getattr(
                settings, "scalper_daily_loss_limit_pct", None
            ),
            "virtual_capital_usdt": getattr(
                settings, "scalper_virtual_capital_usdt", 0.0
            ),
            "virtual_capital_start_trade_id": getattr(
                settings, "scalper_virtual_capital_start_trade_id", 0
            ),
        },
    }


# ---------------------------------------------------------------------------
# KORUMALI: işlem açtırabilen endpoint'ler
# ---------------------------------------------------------------------------

class SignalRequest(BaseModel):
    message: str


_tv_confluence_instance = None


def _tv_confluence():
    """Sağlama motoru — ayarlarla tembel kurulum (testlerde resetlenebilir)."""
    global _tv_confluence_instance
    if _tv_confluence_instance is None:
        from src.services.tv_confluence import TvConfluence

        _tv_confluence_instance = TvConfluence(
            required=int(getattr(settings, "tv_confluence_required", 1) or 1),
            window_seconds=float(
                getattr(settings, "tv_confluence_window_seconds", 180) or 180
            ),
        )
    return _tv_confluence_instance


_TV_SYMBOL_RE = re.compile(r"\b([A-Z0-9]{2,15}USDT)(?:\.P)?\b")
_TV_SECRET_RE = re.compile(r"secret[=:]\s*([^\s\"',}]+)")
_TV_LONG_WORDS = ("buy", "long", "bull")
_TV_SHORT_WORDS = ("sell", "short", "bear")


def _tv_source_allowlist() -> set:
    """?src= için izinli kaynak kümesi (küçük harf, boşluksuz)."""
    raw = getattr(settings, "tv_source_allowlist", "") or ""
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def resolve_tv_source(raw_src_param: Optional[str], raw_body: str):
    """`?src=` sorgu parametresini normalize et ve allowlist'e karşı doğrula.

    ?src= serbest metindir — bir yazım hatası (ör. "algpro") sessizce
    hayalet bir kaynak yaratır ve TvConfluence'ta hiçbir zaman farklı kaynak
    sayısını dolduramaz (bkz. config.py `tv_source_allowlist` yorumu).
    Bilinmeyen değer REDDEDİLMEZ (erişilebilirlik > katılık) — "tv" jenerik
    kaynağına eşlenir; çağıran taraf `source_raw_rejected=True` olduğunda
    WARNING loglar.

    `raw_src_param` boşsa (parametre hiç verilmemişse), AlgoPro'nun
    varsayılan mesaj biçimi ("... | TF: ... | Price: ...") parmak iziyle
    tanınır; aksi halde jenerik "tv".

    Dönüş: (source, source_raw_rejected).
    """
    source = str(raw_src_param or "").strip().lower()
    if not source:
        fallback = "algopro" if ("| TF:" in raw_body or "| Price:" in raw_body) else "tv"
        return fallback, False
    if source not in _tv_source_allowlist():
        return "tv", True
    return source, False


def resolve_tv_signal(raw: str, configured_secret: str, url_secret: str = ""):
    """TradingView alert gövdesini (JSON veya düz metin) çöz ve doğrula.

    TradingView webhook'u özel header GÖNDEREMEZ; secret bu yüzden gövdede
    VEYA URL query'sinde (?secret=...) taşınır ve sabit-zamanlı
    karşılaştırılır. URL varyantı özellikle LuxAlgo "Any alert() function
    call" modu içindir — o modda gövdeyi script doldurur, kullanıcı secret
    ekleyemez. Tolerans: LuxAlgo/AlgoPro varsayılan alert metinleri
    ("Bullish Confirmation" vb.) JSON yazmadan da çözülür — sembol
    {{ticker}} biçiminden (BTCUSDT.P → BTCUSDT), yön buy/long/bull ↔
    sell/short/bear kelimelerinden.

    Dönüş: (symbol, direction). Hata → HTTPException (403/422).
    """
    from src.strategies.scalper.types import Direction

    payload: dict = {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        pass

    provided_secret = str(payload.get("secret") or "")
    if not provided_secret:
        match = _TV_SECRET_RE.search(raw)
        provided_secret = match.group(1) if match else ""
    if not provided_secret:
        provided_secret = str(url_secret or "")
    if not hmac.compare_digest(provided_secret, configured_secret):
        raise HTTPException(status_code=403, detail="Geçersiz webhook secret")

    symbol = str(payload.get("symbol") or "").upper().strip()
    symbol = symbol.split(":")[-1]  # "BINANCE:BTCUSDT" → "BTCUSDT"
    if symbol.endswith(".P"):
        symbol = symbol[:-2]
    if not symbol:
        match = _TV_SYMBOL_RE.search(raw.upper())
        symbol = match.group(1) if match else ""
    if not symbol.endswith("USDT"):
        raise HTTPException(
            status_code=422,
            detail="Sembol çözülemedi — 'symbol' alanı veya metinde BTCUSDT gibi bir parite gerekli",
        )

    side_text = str(payload.get("side") or payload.get("action") or "").lower()
    # Secret metni yanlışlıkla yön kelimesi içerebilir — aramadan önce çıkar.
    scan_text = raw.lower().replace(provided_secret.lower(), "")
    for source in (side_text, scan_text):
        if not source:
            continue
        is_long = any(w in source for w in _TV_LONG_WORDS)
        is_short = any(w in source for w in _TV_SHORT_WORDS)
        if is_long and not is_short:
            return symbol, Direction.LONG
        if is_short and not is_long:
            return symbol, Direction.SHORT
        if is_long and is_short:
            raise HTTPException(
                status_code=422,
                detail="Yön belirsiz — mesajda hem buy/long/bull hem sell/short/bear var",
            )
    raise HTTPException(
        status_code=422,
        detail="Yön çözülemedi — 'side' alanı veya metinde buy/sell/long/short gerekli",
    )


@app.post("/tv-signal")
async def tradingview_webhook(request: Request):
    """TradingView webhook köprüsü — sinyal scalper'ın giriş hattına verilir.

    Dış sinyal yalnız YÖN + ZAMANLAMA sağlar; stop politikası, risk
    boyutlama, TP/BE/chandelier, cooldown ve kapasite kapıları scalper'ın
    kendi ayarlarıyla aynen uygulanır.
    """
    configured = (settings.tv_webhook_secret or "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="TV webhook devre dışı — .env'e TV_WEBHOOK_SECRET ekleyin",
        )
    raw = (await request.body()).decode("utf-8", errors="replace").strip()
    if not raw or len(raw) > 8192:
        raise HTTPException(status_code=422, detail="Geçersiz gövde")

    symbol, direction = resolve_tv_signal(
        raw, configured, url_secret=request.query_params.get("secret") or ""
    )

    if not scalper_engine:
        raise HTTPException(status_code=503, detail="Scalper hazır değil")

    # Kaynak etiketi: alarm URL'sindeki ?src=... öncelikli; yoksa AlgoPro'nun
    # varsayılan mesaj biçimi ("BUY on X | TF: 1 | Price: ...") parmak iziyle
    # tanınır; kalan her şey "tv". Sağlama FARKLI kaynak sayar. Bilinmeyen
    # ?src= REDDEDİLMEZ, "tv"ye eşlenir ve WARNING loglanır (bkz.
    # resolve_tv_source / config.py tv_source_allowlist yorumu).
    raw_src_param = request.query_params.get("src")
    source, source_raw_rejected = resolve_tv_source(raw_src_param, raw)
    if source_raw_rejected:
        app_logger.warning(
            f"TV webhook: allowlist dışı ?src='{str(raw_src_param)[:32]}' — "
            f"'tv' olarak eşleştirildi (yazım hatası ya da tanınmayan entegrasyon olabilir)"
        )

    source_fields = {"source": source}
    if source_raw_rejected:
        source_fields["source_raw_rejected"] = True

    required = max(1, int(getattr(settings, "tv_confluence_required", 1) or 1))
    if required > 1:
        verdict = _tv_confluence().vote(symbol, direction.value, source)
        if not verdict["triggered"]:
            return {
                "symbol": symbol,
                "direction": direction.value,
                "accepted": False,
                "confluence": verdict,
                **source_fields,
            }
        result = await scalper_engine.external_signal(symbol, direction)
        return {
            "symbol": symbol,
            "direction": direction.value,
            **result,
            "confluence": verdict,
            **source_fields,
        }

    result = await scalper_engine.external_signal(symbol, direction)
    return {"symbol": symbol, "direction": direction.value, **source_fields, **result}


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
    "safety_interval": settings.scalper_safety_interval_seconds,
    "health": {"healthy": False, "running": False, "reason": "engine_not_created"},
    "universe": [],
    "regimes": {},
    "daily_pnl": 0.0,
    "daily_pnl_source": "unavailable",
    "risk_ready": False,
    "risk_equity_usdt": None,
    "risk_equity_source": "unavailable",
    "daily_loss_threshold_usdt": None,
    "daily_limit_pct": settings.scalper_daily_loss_limit_pct,
    "kill_switch_active": False,
    "entry_halted": False,
    "entry_halt_reason": None,
    "entry_halted_at": None,
    "signals_today": 0,
    "last_scan_at": None,
    "tracked": [],
    "pending_entries": [],
    "cooldowns": [],
    "sizing": {},
    "sizing_equity_usdt": None,
    "virtual_capital_enabled": False,
    "virtual_capital_base_usdt": 0.0,
    "virtual_capital_current_usdt": None,
    "virtual_capital_start_trade_id": 0,
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
        # Starlette JSONResponse allow_nan=False kullanır. Kayıp olmayan
        # seride PF=+inf matematiksel olarak anlamlı olsa da JSON değildir;
        # API'de "henüz sonlu değil" anlamında null döndür.
        for strategy_stats in strategies.values():
            if isinstance(strategy_stats, dict):
                strategy_stats["profit_factor"] = _finite_or_none(
                    strategy_stats.get("profit_factor")
                )

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
        profit_factor = None if gross_profit > 0 else 0.0

    source_counts = {"verified": 0, "fallback": 0, "legacy": 0}
    for row in rows:
        source_counts[ScalpTracker._pnl_source(getattr(row, "notes", None))] += 1

    combined = {
        "trades": n,
        "wins": len(wins),
        "winrate": (len(wins) / n * 100.0) if n else 0.0,
        "total_pnl": total_pnl,
        "avg_roi": avg_roi,
        "profit_factor": _finite_or_none(profit_factor),
        "verified_trades": source_counts["verified"],
        "fallback_trades": source_counts["fallback"],
        "legacy_trades": source_counts["legacy"],
        "pnl_basis": ScalpTracker._pnl_basis(
            source_counts["verified"],
            source_counts["fallback"],
            source_counts["legacy"],
        ),
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
            "pnl_source": ScalpTracker._pnl_source(t.notes),
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
