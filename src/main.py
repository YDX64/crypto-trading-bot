"""
Ana uygulama giriş noktası.
FastAPI server ve Telegram bot'u çalıştırır.

GÜVENLİK: İşlem açtırabilen endpoint'ler API anahtarı ister. Anahtar
yapılandırılmamışsa endpoint AÇIK KALMAZ, kapatılır (fail-closed).
"""

import asyncio
import json
import logging
import math
import os
import re
import signal
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Depends, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.logger import app_logger
from src.core.database import init_db, close_db, get_db
from src.core.account_lock import TradingAccountLock
from src.models.waiting_signal import WaitingSignalModel, WaitingStatus
from src.models.scalp_trade import ScalpTradeModel
from src.services.telegram_bot import TelegramBotService
from src.services.orchestrator import TradingOrchestrator
from src.services.follower_forwarder import maybe_forward_algopro_event
from src.services.tv_events import (
    DEFAULT_EVENT_SOURCES,
    EVENT_KINDS,
    STRUCTURE_KINDS,
    tv_events,
)
from src.strategies.scalper.data import MarketDataGuard
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper import intent as scalp_intent
from src.strategies.scalper import counterfactual_store
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import FOLLOWER_LEDGER_STRATEGY
from src.trading.symbol_reservations import symbol_reservations


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
# AlgoPro takipçi halkası (D20, BOT_MODE=follower). Scalper modunda DAİMA
# None kalır — bu dosyadaki her takipçi dalı o modda ölü koddur.
follower_engine = None
trading_account_lock: Optional[TradingAccountLock] = None


def _risk_engine():
    """`/risk-event` hangi motora gidecek? (halkaya göre)

    Scalper modunda BUGÜNKÜ davranış birebir: `scalper_engine`. Takipçi
    modunda aynı sözleşmeyi (halt/resume/flatten/status) uygulayan
    `FollowerEngine` döner — D10 semantiği iki halkada da aynıdır.
    """
    return scalper_engine or follower_engine


#: Gömülü takipçi KAPALIYKEN defterde açık kalmış AP satırları (D20b).
#: `/health` bunu ayrı bir alan olarak raporlar; hard fail YOKTUR (418/ban
#: kuralı: teşhis bir restart döngüsü doğurmamalı).
_orphaned_follower_trades: list = []


async def _check_disabled_follower_open_trades() -> list:
    """`FOLLOWER_EMBEDDED=false` iken DB'de OPEN AP satırı var mı? (D20b)

    NEDEN (doğrulayıcı bulgusu Y10): gömülü mod açıkken pozisyon açıp bayrağı
    kapatmak, o pozisyonu HİÇBİR motorun yönetmediği bir hâle sokar — takipçi
    hiç başlamaz, scalper da defter filtresi yüzünden AP satırını almaz.
    Borsada SL/TP emirleri durur ama TP1→BE, EXIT/flip ve kapanış defteri
    çalışmaz.

    KASTEN hard fail DEĞİL: startup'ı düşürmek ban/deploy döngüsü doğurur ve
    pozisyonu KAPATMAZ. CRITICAL log + `/health` alanı + RUNBOOK reçetesi.
    """
    global _orphaned_follower_trades
    _orphaned_follower_trades = []
    if settings.follower_active:
        return []
    try:
        rows = await ScalpTracker().open_trades(
            strategies=(FOLLOWER_LEDGER_STRATEGY,)
        )
    except Exception as exc:  # pragma: no cover - teşhis startup'ı düşürmez
        app_logger.warning(
            f"⚠️ Açık AP işlemleri kontrol edilemedi ({exc}); "
            f"gömülü takipçi kapalıyken yönetimsiz pozisyon olabilir"
        )
        return []
    if not rows:
        return []
    _orphaned_follower_trades = [
        {"id": r.id, "symbol": r.symbol, "direction": r.direction} for r in rows
    ]
    symbols = sorted({str(r.symbol) for r in rows})
    app_logger.bind(trade=True).critical(
        f"🚨 FOLLOWER_EMBEDDED KAPALI ama defterde AÇIK AlgoPro işlemi var: "
        f"{symbols}. Bu pozisyonları HİÇBİR motor yönetmiyor (TP1→BE, EXIT ve "
        f"kapanış defteri ÇALIŞMAZ; borsadaki SL/TP emirleri durur). Çözüm: "
        f"FOLLOWER_EMBEDDED=true ile yeniden başlatıp /risk-event flatten ile "
        f"kapatın, ya da pozisyonları elle kapatıp scalp_trades satırlarını "
        f"kapanmış olarak işaretleyin (docs/RUNBOOK.md 'Gömülü takipçiyi "
        f"kapatma')."
    )
    return _orphaned_follower_trades


def _foreign_tracked_symbols() -> set:
    """Takipçi DIŞINDAKİ motorların GERÇEKTEN yönettiği semboller (D20b).

    Gömülü takipçinin yetim denetimi bunu BİRİNCİ kaynak olarak kullanır:
    `symbol_reservations` bir NİYET kaydıdır ve scalper entry-halt'a
    düştüğünde DONAR (`_sync_scalper_reservations` ilk satırda döner), o
    yüzden tek başına ne yanlış-pozitifi ne yanlış-negatifi engeller.

    Hata hâlinde istisna YUTULMAZ: çağıran (`FollowerEngine._foreign_tracked_
    symbols`) onu yakalar, WARNING loglar ve o tur için boş küme kullanır —
    yani denetim rezervasyon kaydına düşer, SESSİZ kalmaz. Burada yakalamamak
    bilinçlidir: hatanın kaynağı çağıranın log satırında görünmelidir.
    """
    symbols: set = set()
    if scalper_engine is not None:
        symbols |= {str(s).upper() for s in scalper_engine.exits.tracked_symbols()}
        symbols |= {str(s).upper() for s in scalper_engine.executor.pending_symbols()}
        symbols |= {
            str(s).upper() for s in getattr(scalper_engine, "_opening_symbols", ())
        }
    if orchestrator is not None:
        symbols |= {
            str(s).upper() for s in getattr(orchestrator, "active_positions", {})
        }
    return symbols


def _risk_engines() -> list:
    """`/risk-event`'in uygulanacağı TÜM motorlar (D20b).

    Gömülü modda scalper ve takipçi AYNI süreçte ve AYNI Binance hesabında
    çalışır: `halt` yalnız birine uygulanırsa diğeri yeni pozisyon açmayı
    sürdürür, `flatten` yalnız birine uygulanırsa hesap FLAT DEĞİLDİR —
    ikisi de operatörün "durdur" komutunu YANLIŞ okutur. Sıra scalper
    önce (bugünkü yanıt alanları onun sonucundan türer), takipçi sonra.

    Gömülü mod kapalıyken liste tek elemanlıdır → davranış birebir aynı.
    """
    return [engine for engine in (scalper_engine, follower_engine) if engine]


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
    global follower_engine, trading_account_lock

    app_logger.info("=" * 80)
    app_logger.info("🚀 TRADING BOT BAŞLATILIYOR")
    app_logger.info("=" * 80)

    # D22: pano önbellekleri süreç-genelidir; yeniden kurulumda bayat
    # payload servis edilmesin (reload/test app'i).
    _reset_status_caches()

    db_ready = False
    try:
        # D28: DB ve süreç-içi sembol rezervasyonları iki AYRI süreci
        # koordine edemez. Emir gönderebilen her halka aynı Binance API
        # anahtarı için kernel kilidini DB/recovery'den ÖNCE alır.
        is_orderless_shadow = bool(
            getattr(settings, "scalper_shadow_mode", False)
        )
        if bool(getattr(settings, "trading_account_lock_enabled", True)) and not is_orderless_shadow:
            trading_account_lock = TradingAccountLock.acquire(
                api_key=settings.binance_api_key,
                lock_dir=settings.trading_account_lock_dir,
                app_env=settings.app_env,
                bot_mode=settings.bot_mode,
                network="testnet" if settings.is_testnet else "mainnet",
            )
            app_logger.info(
                "🔐 Binance hesabı tek-yönetici kilidi alındı "
                f"(pid={os.getpid()}, mode={settings.bot_mode})"
            )
        else:
            app_logger.warning(
                "👻 Binance hesap kilidi atlandı "
                "(uygulama-geneli emirsiz shadow veya "
                "TRADING_ACCOUNT_LOCK_ENABLED=false)"
            )
        await init_db()
        db_ready = True
        app_logger.info("✅ Veritabanı hazır")

        # --- AlgoPro takipçi halkası (D20): scanner/strateji/TV sağlaması
        #     ve Telegram VIP akışı YOKTUR; orchestrator da BAŞLATILMAZ
        #     (açık pozisyonları sahiplenip takipçiyle çakışırdı). ---
        if settings.is_follower_mode:
            from src.strategies.follower.engine import FollowerEngine

            follower_engine = FollowerEngine()
            await follower_engine.start()
            app_logger.info("=" * 80)
            app_logger.info(f"✅ ALGOPRO TAKİPÇİ HALKASI HAZIR - {settings.app_env.upper()}")
            app_logger.info(
                f"🌐 Ortam: {'TESTNET' if settings.is_testnet else '⚠️ MAINNET (GERÇEK PARA)'}"
            )
            app_logger.info(f"📊 API Server: http://{settings.api_host}:{settings.api_port}")
            app_logger.info(
                f"🔒 /follower/event: "
                f"{'korumalı' if (settings.follower_forward_secret or '').strip() else 'DEVRE DIŞI (secret yok)'}"
            )
            app_logger.info("=" * 80)
            yield
            return

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

            # TV olay kanalı yapılandırma sağlığı (D19a bulgu E): sunucu
            # `.env`'i TV_SOURCE_ALLOWLIST'i açıkça set etmişse ya da
            # pencere/kapı kaynakları boşsa kanal "kurulu görünüp ölü"
            # olabilir. Startup'ta WARNING; durum /scalper/status →
            # tv_events.allowlist_ok / gate_enabled alanlarındadır.
            tv_events.log_config_health()

        # Geriye kalan (scalper sahipliğinde olmayan) açık pozisyonları genel
        # orchestrator kurtarır ve izlemeye alır.
        #
        # ⚠️ GÖLGE MODU (D14) KAPISI — 2026-08-24'te ölçülerek eklendi.
        # `SCALPER_SHADOW_MODE=true` "emir gönderilmez" DEMEKTİR, ama bu söz
        # yalnız scalper motorunu kapsıyordu: orchestrator ayrı bir bileşen ve
        # `recover_open_positions()` borsadaki HER pozisyonu "yetim" sayıp
        # izlemeye alıyordu. Gölge halkası CANLI halkayla aynı Binance hesabına
        # bakınca (ölçüldü: /opt/tradingbot-shadow, 2026-08-24 10:44) canlının
        # 5 pozisyonunu sahiplendi → aynı pozisyonun İKİ yöneticisi, D20b
        # incelemesindeki kritik sınıfın aynısı. Gölgede orchestrator HİÇ
        # başlatılmaz; kurtarma da izleme de yapılmaz.
        if bool(getattr(settings, "scalper_shadow_mode", False)):
            app_logger.warning(
                "👻 GÖLGE MODU: Trading Orchestrator BAŞLATILMADI — "
                "borsadaki pozisyonlar sahiplenilmez (canlı halkanın işleri)"
            )
        else:
            await orchestrator.start()
            app_logger.info("✅ Trading Orchestrator başlatıldı")

        # --- GÖMÜLÜ TAKİPÇİ (D20b, kullanıcı kararı 2026-08-23) -----------
        # Scalper'ın YANINDA, AYNI süreçte/hesapta/panoda; boyutlaması SANAL
        # defterle (FOLLOWER_VIRTUAL_CAPITAL_USDT) yapılır.
        # `FOLLOWER_EMBEDDED=false` (varsayılan) → bu blok hiç çalışmaz ve
        # bugünkü davranış birebir korunur.
        #
        # SIRA ÖNEMLİ (düşmanca inceleme): takipçi EN SONDA başlar. `start()`
        # senkron bir yetim denetimi çalıştırır; orchestrator kendi açık
        # pozisyonlarını `start()` İÇİNDE rezerve ettiği için ondan ÖNCE
        # başlatmak Telegram'ın her pozisyonunu "sahipsiz" gösterirdi.
        if settings.scalper_enabled and settings.follower_embedded:
            from src.strategies.follower.engine import FollowerEngine

            follower_engine = FollowerEngine()
            # Yetim denetiminin BİRİNCİ kaynağı: diğer motorların GERÇEK
            # izleme listeleri (rezervasyon kaydı yalnız ikinci katmandır —
            # scalper entry-halt'ta rezervasyonlarını DONDURUR).
            follower_engine.foreign_tracked_cb = _foreign_tracked_symbols
            await follower_engine.start()
            app_logger.info(
                "✅ AlgoPro takipçisi GÖMÜLÜ modda başlatıldı "
                f"(sanal sermaye={settings.follower_virtual_capital_usdt:g} USDT, "
                f"evren={sorted(follower_engine.symbol_allowlist())})"
            )
            if not (settings.risk_event_secret or "").strip():
                # Ayrı halkada bu ZORUNLUDUR (Telegram yok). Gömülü modda
                # scalper'ın Telegram/supervisor yolları da vardır, o yüzden
                # fail-fast değil — ama sessiz de kalınmaz.
                app_logger.warning(
                    "⚠️ RISK_EVENT_SECRET boş: /risk-event 503 döner. "
                    "Gömülü takipçiyi uzaktan flatten etmenin tek yolu "
                    "odur (bkz. docs/RUNBOOK.md 'Gömülü takipçiyi açma')."
                )
            if str(getattr(settings, "follower_forward_url", "") or "").strip():
                # İki kurulum aynı anda ANLAMLI DEĞİLDİR: gömülü modda AlgoPro
                # gövdesi süreç içinde tüketilir ve HTTP köprüsü HİÇ çağrılmaz
                # — ayrı halka (tradingbot_ap) sessizce alarmsız kalır ve açık
                # pozisyonlarına EXIT/flip komutu ULAŞMAZ.
                app_logger.critical(
                    "🚨 FOLLOWER_EMBEDDED=true iken FOLLOWER_FORWARD_URL DOLU: "
                    "ayrı halka (tradingbot_ap) artık HİÇBİR AlgoPro olayı "
                    "ALMAZ. Önce ayrı halkayı düzleştirip durdurun, sonra "
                    "FOLLOWER_FORWARD_URL'i boşaltın "
                    "(docs/RUNBOOK.md 'Gömülü takipçiyi açma' adım 0).",
                    extra={"trade": True},
                )

        # Bayrak kapalıyken defterde açık AP satırı kaldıysa SESSİZ kalma.
        # Telegram supervisor'ından ÖNCE: `await`, supervisor task'ına sıra
        # verirdi ve lifespan başlatma sırası testleri kırılırdı.
        await _check_disabled_follower_open_trades()

        # Telegram ağ/409 hatası scalper safety döngülerini öldürmemeli.
        # Supervisor tam yaşam döngüsünü await eder ve bounded backoff ile
        # yeniden dener; health Telegram'ı ayrı bir bileşen olarak raporlar.
        if is_orderless_shadow:
            # D28: Telegram kuyruğu doğrudan orchestrator.process_signal'a
            # gider ve ScalperExecutor'ın shadow kapısından GEÇMEZ. Gerçek
            # shadow süreç, hesap kilidini güvenle paylaşabilmek için bütün
            # mutasyon yollarını kapatır; manuel `/signal` da aşağıda 503'tür.
            app_logger.warning(
                "👻 GÖLGE MODU: Telegram sinyal kuyruğu BAŞLATILMADI — "
                "uygulama-geneli hiçbir emir yolu çalışmaz"
            )
        else:
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
            # Scalper modunda DAİMA None → bu satır davranışı değiştirmez.
            ("AlgoPro takipçisi", follower_engine.stop if follower_engine else None),
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

        if trading_account_lock is not None:
            trading_account_lock.release()
            trading_account_lock = None
            app_logger.info("🔓 Binance hesabı tek-yönetici kilidi bırakıldı")

        telegram_bot = None
        scalper_engine = None
        orchestrator = None
        follower_engine = None
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
    # AlgoPro takipçi halkası (D20): orchestrator/Telegram/scalper YOKTUR;
    # sağlık yalnız takipçi motorundan okunur. Scalper modunda bu dal hiç
    # çalışmaz (bugünkü davranış birebir korunur).
    if settings.is_follower_mode:
        follower_health = (
            follower_engine.health_snapshot()
            if follower_engine
            else {"healthy": False, "running": False, "reason": "engine_not_created"}
        )
        core_healthy = bool(follower_health.get("healthy"))
        return JSONResponse(
            status_code=200 if core_healthy else 503,
            content={
                "status": "healthy" if core_healthy else "degraded",
                "core_healthy": core_healthy,
                "timestamp": _utcnow_iso(),
                "mode": "follower",
                "follower": "running" if core_healthy else "degraded",
                "follower_details": follower_health,
                "network": "testnet" if settings.is_testnet else "mainnet",
            },
        )

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
    # D20b: gömülü takipçi AYRI bir bileşen olarak raporlanır. `core_healthy`
    # KASTEN etkilenmez: takipçinin safety turu bayatlarsa çözüm süreci
    # yeniden başlatmak DEĞİL, takipçinin kendi fail-closed kilididir
    # (entry-halt) — scalper'ın soak'unu bir teşhis restart'ıyla kesmek
    # 2026-08-14 dersine aykırıdır. Durum panoda ve /follower/status'ta.
    if settings.follower_embedded:
        follower_health = (
            follower_engine.health_snapshot()
            if follower_engine
            else {"healthy": False, "running": False, "reason": "engine_not_created"}
        )
        body["follower"] = (
            "running" if follower_health.get("healthy") else "degraded"
        )
        body["follower_details"] = follower_health
    elif _orphaned_follower_trades:
        # D20b: bayrak KAPALI ama defterde açık AP satırı var → o pozisyonlar
        # YÖNETİMSİZ. `core_healthy` KASTEN etkilenmez (hard fail bir restart
        # döngüsü doğurur ve pozisyonu kapatmaz); operatör burada görür.
        body["follower"] = "disabled_with_open_trades"
        body["follower_details"] = {
            "healthy": False,
            "reason": "follower_disabled_but_open_ap_trades",
            "open_trades": list(_orphaned_follower_trades),
        }
    # Telegram has its own retry supervisor.  Its outage is reported as
    # degraded but does not provoke a process restart while the trading core
    # and protection loops remain healthy.
    return JSONResponse(status_code=200 if core_healthy else 503, content=body)


# --- Pano besleme önbellekleri (D22) ---------------------------------------
# Pano 5 sn'de bir yoklar; her tik yeni REST çağrısı demek DEĞİLDİR. Bu
# önbellekler SUNUCU tarafındadır (tarayıcı `cache: "no-store"` gönderir) ve
# tek amaçları Binance ağırlık bütçesini korumaktır: 2026-08-18'de panonun
# force-fresh çağrısı rate-limiter'ı doyurup tarama döngüsünü aç bırakmıştı.
# Motor YOKKEN `/scalper/status` önbelleklenmez: o yol hiç REST yapmaz,
# yalnız senkron anlık görüntü kurar (olay defteri her çağrıda taze olmalı).
# TTL, panonun yoklama aralığıyla AYNIDIR (5 sn): her tik EN FAZLA bir kez
# gerçek iş yapar ve gösterilen veri bir yoklama turundan daha eski olmaz.
# İki uç AYNI TTL'yi kullanır — farklı TTL'ler panoda birbirini tutmayan iki
# yaş üretiyordu.
_STATUS_CACHE_TTL = 5.0
_api_status_cache: Dict[str, Dict[str, Any]] = {}
_scalper_status_cache: Dict[str, Dict[str, Any]] = {}


def _reset_status_caches() -> None:
    """Pano önbelleklerini boşalt (lifespan başlangıcı, durum değiştiren uçlar).

    Durum DEĞİŞTİREN bir uç (risk-event halt/resume/flatten, TV olay defteri
    sıfırlama) çağrıldıktan sonra panonun 5 sn boyunca ESKİ tabloyu
    göstermesi, operatörün "komut çalıştı mı?" sorusuna yanlış cevap verir.
    """
    _api_status_cache.clear()
    _scalper_status_cache.clear()


def _status_cache_key(request: Optional[Request]) -> str:
    """Önbellek anahtarı = SIRALANMIŞ sorgu dizesi.

    Bugün bu iki uç query parametresi almıyor; anahtar yine de sorgudan
    türetilir ki ileride bir `?include_shadow=1` eklendiğinde YANLIŞ
    varyantın önbelleği servis edilmesin (sessiz veri sızıntısı).
    """
    if request is None:
        return ""
    try:
        return urlencode(sorted(request.query_params.multi_items()))
    except Exception:  # pragma: no cover - savunma
        return ""


def _cached_status(
    cache: Dict[str, Dict[str, Any]], key: str, *, engine: Any = None
) -> Optional[Any]:
    entry = cache.get(key)
    if not entry:
        return None
    if engine is not None and entry.get("engine") is not engine:
        return None
    if time.monotonic() - float(entry.get("at") or 0.0) >= _STATUS_CACHE_TTL:
        return None
    return entry.get("payload")


def _store_status(
    cache: Dict[str, Dict[str, Any]], key: str, payload: Any, *, engine: Any = None
) -> Any:
    # Anahtar başına tek kayıt; sınırsız büyümeyi engellemek için sorgu
    # varyantı sayısı makul bir tavana bağlanır (pano tek varyant kullanır).
    if len(cache) > 32:
        cache.clear()
    cache[key] = {"at": time.monotonic(), "payload": payload, "engine": engine}
    return payload


@app.get("/api/status")
async def api_status(request: Request = None):
    """Sistem durumu — Binance hataları gizlenmez.

    D22: yanıt SUNUCU tarafında 5 sn önbelleklenir ve borsa okumaları
    `priority="background"` ile yapılır. `force_fresh` bu yoldan ASLA
    istenmez (2026-08-18 pano-açlığı olayının kök nedeni buydu). Yanıttaki
    `as_of`, gövdenin KURULDUĞU andır (isteğin geldiği an değil) — pano
    "son güncelleme"yi buradan yazar, böylece önbellekten servis edilen bir
    tablo taze görünmez.
    """
    cache_key = _status_cache_key(request)
    cached = _cached_status(_api_status_cache, cache_key)
    if cached is not None:
        return cached

    account = {"balance": None, "btc_price": None, "open_positions": None}
    errors = []

    # Takipçi halkasında orchestrator YOKTUR; hesap özeti takipçi motorunun
    # istemcisinden okunur (deploy sağlık yoklaması bu uç noktayı kullanır).
    # Scalper modunda `orchestrator` her zaman doludur → davranış aynı.
    if orchestrator is None and follower_engine is not None:
        client = follower_engine.client
        try:
            account["balance"] = await client.get_account_balance(
                priority="background"
            )
        except Exception as e:
            errors.append(f"balance: {e}")
        try:
            account["btc_price"] = await client.get_current_price(
                "BTCUSDT", priority="background"
            )
        except Exception as e:
            errors.append(f"price: {e}")
        try:
            account["open_positions"] = len(
                await client.get_all_positions(
                    force_fresh=False, priority="background"
                )
            )
        except Exception as e:
            errors.append(f"positions: {e}")
        payload = {
            "status": "running" if not errors else "degraded",
            "bot_active": bool(follower_engine.running),
            "orchestrator_active": False,
            "mode": "follower",
            "account": account,
            "errors": errors,
            "config": {
                "margin_pct": settings.follower_margin_pct,
                "max_positions": settings.follower_max_positions,
                "lev_bounds": [settings.follower_lev_min, settings.follower_lev_max],
                "sl_roi_target": settings.follower_sl_roi_target,
            },
            "timestamp": _utcnow_iso(),
            # D22: gövdenin kurulduğu an (önbellekten servis edilse de sabit).
            "as_of": _utcnow_iso(),
        }
        return _store_status(_api_status_cache, cache_key, payload)

    if orchestrator:
        client = orchestrator.binance
        try:
            account["balance"] = await client.get_account_balance(
                priority="background"
            )
        except Exception as e:
            errors.append(f"balance: {e}")
        try:
            account["btc_price"] = await client.get_current_price(
                "BTCUSDT", priority="background"
            )
        except Exception as e:
            errors.append(f"price: {e}")
        try:
            # Gösterim amaçlı sayaç: force_fresh=False şart. Panel 5 sn'de bir
            # bu endpoint'i çağırıyor; taze zorlamak rate-limiter kuyruğunu
            # doyurup scan döngüsünü bayatlatıyordu (2026-08-18 degraded olayı).
            account["open_positions"] = len(
                await client.get_all_positions(
                    force_fresh=False, priority="background"
                )
            )
        except Exception as e:
            errors.append(f"positions: {e}")
    else:
        errors.append("orchestrator başlatılmadı")

    payload = {
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
        # D22: gövdenin kurulduğu an (önbellekten servis edilse de sabit).
        "as_of": _utcnow_iso(),
    }

    # D20b: pano "AlgoPro Takipçi" kartını BU gövdeden okur — ayrı bir
    # yoklama açmaz. `dashboard_snapshot()` yalnız BELLEK okur (REST YOK),
    # bu yüzden 2026-08-18 pano-açlığı riski doğurmaz. Gömülü mod kapalıyken
    # anahtar hiç EKLENMEZ → yanıt bugünküyle birebir aynıdır.
    if settings.follower_embedded:
        try:
            payload["follower"] = (
                follower_engine.dashboard_snapshot()
                if follower_engine
                else {"running": False, "embedded": True, "positions": []}
            )
        except Exception as e:  # teşhis alanı asla status'u düşürmemeli
            payload["follower"] = {"error": f"{type(e).__name__}: {e}"}

    # D23: pano "AI Karar Katmanı (gölge)" kartını BU gövdeden okur — YENİ bir
    # uç AÇILMAZ (nginx beyaz listesi: `/api/status` zaten izinli, bkz.
    # docs/RUNBOOK.md "Pano erişimi"). `_ai_gate_snapshot()` yalnız BELLEK
    # okur (REST/DB YOK) → 2026-08-18 pano-açlığı riski doğurmaz. Katman
    # KAPALIYKEN anahtar hiç EKLENMEZ → yanıt bugünküyle birebir aynıdır.
    if str(getattr(settings, "scalper_ai_gate_mode", "off") or "off") != "off":
        try:
            payload["ai_gate"] = (
                scalper_engine._ai_gate_snapshot()
                if scalper_engine
                else {"mode": settings.scalper_ai_gate_mode, "enabled": True}
            )
        except Exception as e:  # teşhis alanı asla status'u düşürmemeli
            payload["ai_gate"] = {"error": f"{type(e).__name__}: {e}"}

    # D27/B: karşı-olgu defteri sayaçları.
    # ⚠️ D27 incelemesi (D7): pano bu bloğu `/scalper/status`tan okur
    # (`static/dashboard.html`, `renderScalper` → `d.counterfactual`), BURADAN
    # DEĞİL. Bu blok bugün TÜKETİCİSİZDİR ve simetri/teşhis için durur;
    # yanlış yorum ileride yanlış tarafı sildirebilirdi.
    # `counters_snapshot()` yalnız BELLEK okur (REST/DB/disk YOK) →
    # 2026-08-18 pano-açlığı riski doğurmaz. Defter KAPALIYKEN blok yine
    # eklenir ama `enabled=false` der: "alan yok" ile "ölçüm kapalı"
    # karışmamalı.
    try:
        payload["counterfactual"] = counterfactual_store.counters_snapshot()
    except Exception as e:  # teşhis alanı asla status'u düşürmemeli
        payload["counterfactual"] = {"error": f"{type(e).__name__}: {e}"}

    return _store_status(_api_status_cache, cache_key, payload)


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

# ---------------------------------------------------------------------------
# D19 — gövde yönlendirmesi (TV olay kanalı)
# ---------------------------------------------------------------------------
# Kullanıcı yeni alarmları TV'de MEVCUT alarmları KLONLAYARAK kuruyor: webhook
# URL'si (secret + eski `?src=luxso` gibi) değişmiyor, yalnız alarm koşulu ve
# MESAJ GÖVDESİ değişiyor. Bu yüzden yönlendirme GÖVDEDEN yapılır:
#   * JSON gövde  → `src`/`source` ve `kind` ALANLARI
#   * düz metin   → `src=<token>` / `kind=<token>` BELİRTEÇLERİ
# Ayırıcı boşluk, virgül ve `|` olabilir (token karakter kümesi bunların
# hiçbirini içermez). Lookbehind, "mysrc=x" gibi gömülü eşleşmeleri eler.
# Ayraç `=` VEYA `:` (secret deseniyle tutarlı — `_TV_SECRET_RE`). AYRAÇ
# YAKALANIR çünkü ikisi AYNI güvende değildir (D19a-2): `=` kasıtlı bir
# belirteçtir, `:` ise düz yazı noktalamasıdır ("Kind: Bullish Reversal").
# Bu yüzden `:` ile gelen bir `src`/`kind` YALNIZ TANINAN bir değer taşıyorsa
# sayılır; tanınmayan değer YOK SAYILIR (422 üretmez) — aksi halde `Kind:`
# ile başlayan masum bir GİRİŞ alarmı bugün kabul edilirken yarın 422 alırdı.
_TV_BODY_SRC_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:src|source)\s*([=:])\s*([A-Za-z0-9_\-]{1,32})",
    re.IGNORECASE,
)
_TV_BODY_KIND_RE = re.compile(
    r"(?<![A-Za-z0-9_])kind\s*([=:])\s*([A-Za-z0-9_\-]{1,32})", re.IGNORECASE
)
# Aynı `src` etiketini paylaşan alt-kaynak (S&O "Trend Catcher" ile "Trend
# Tracer" ikisi de `luxso_trend`tir). YALNIZ TELEMETRİ — karar anahtarı `src`.
_TV_BODY_VIA_RE = re.compile(
    r"(?<![A-Za-z0-9_])via\s*([=:])\s*([A-Za-z0-9_\-]{1,32})", re.IGNORECASE
)
# D19a bulgu G1 — YÖNLENDİRME BELİRTEÇLERİ YALNIZ "BAŞLIK KOŞUSU"NDAN OKUNUR.
# Gövdenin tamamını taramak, TradingView'in `{{strategy.order.alert_message}}`
# gibi KULLANICI METNİNİ gövdenin ortasına basan alanlarının mevcut bir
# alarmın kimliğini (`src`) ya da yolunu (`kind`) değiştirebilmesi demekti.
# Başlık koşusu = satır başından itibaren KESİNTİSİZ `anahtar=değer`
# (veya `anahtar: değer`) belirteçleri dizisi; ilk "serbest metin"
# belirtecinde biter. `src=luxso_exit kind=exit {{ticker}}` → koşu ilk iki
# belirteçtir, `{{ticker}}` sonrası taranmaz.
# NÜANS (ölçüldü, bilinçli): ayraçtan sonra boşluk serbesttir (`SRC = x`),
# bu yüzden bir anahtarın hemen ardından gelen TEK BAŞINA bir sözcük o
# anahtarın DEĞERİ sayılır ve koşu devam eder — ör.
# `secret=… BTCUSDT.P src=pac_choch kind=choch` koşusu `BTCUSDT.P`yi
# secret'ın değeri sayıp `src`/`kind`e ulaşır. Bu GÜVENLİ yöndür: sonuç,
# yanlış yerleştirilmiş bir olay alarmının GİRİŞ OYU olmak yerine doğru
# şekilde OLAY yoluna gitmesidir. Tehlikeli yön (gövdenin ORTASINDAKİ
# serbest metnin okunması) kapalıdır ve okunamayan olay alarmları
# `_tv_body_event_source_mentions` sayesinde 422 ile GÖRÜNÜR biçimde ölür.
_TV_HEADER_RUN_RE = re.compile(
    r"^(?:[\s,|]*[A-Za-z_][A-Za-z0-9_]{0,31}\s*[=:]\s*[^\s,|]*)+"
)
_TV_HEADER_MAX_LINES = 5
# Olay yönü SÖZCÜK SINIRIYLA aranır — `resolve_tv_signal`'ın alt-dize
# taraması burada KULLANILAMAZ: olay sözlüğüne "up"/"down" da girdi ve "up"
# alt-dize olarak "SUPPORT", "SETUP" gibi masum kelimelerde geçer.
# Giriş yolunun (49 alarm) sözlüğü ve tarama biçimi DEĞİŞMEDİ.
_TV_EVENT_LONG_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:buy|long|bull|bullish|up)(?![A-Za-z0-9_])", re.IGNORECASE
)
_TV_EVENT_SHORT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:sell|short|bear|bearish|down)(?![A-Za-z0-9_])", re.IGNORECASE
)


def _tv_source_allowlist() -> set:
    """?src= için izinli kaynak kümesi (küçük harf, boşluksuz)."""
    raw = getattr(settings, "tv_source_allowlist", "") or ""
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _tv_entry_source_blocklist() -> set:
    """Scalper giriş oyundan karantinaya alınan TV kaynakları."""
    raw = getattr(settings, "tv_entry_source_blocklist", "") or ""
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _tv_event_sources() -> set:
    """"Olay kaynağı" etiketleri — bunlar GİRİŞ OYU VEREMEZ (D19a bulgu A)."""
    raw = getattr(settings, "tv_event_sources", "") or ""
    values = {s.strip().lower() for s in raw.split(",") if s.strip()}
    return values or set(DEFAULT_EVENT_SOURCES)


def _tv_header_run(scan: str) -> str:
    """Gövdenin yalnız `anahtar=değer` başlık koşusunu döndür (bkz. G1 yorumu)."""
    regions = []
    for line in scan.splitlines()[:_TV_HEADER_MAX_LINES]:
        match = _TV_HEADER_RUN_RE.match(line)
        if match:
            regions.append(match.group(0))
    return "\n".join(regions)


def _tv_token(pattern, header: str, *, known) -> str:
    """Başlık koşusundan bir yönlendirme belirteci oku (ayraca duyarlı).

    `=` ile gelen değer KOŞULSUZ kabul edilir (kasıtlı belirteç). `:` ile
    gelen değer YALNIZ `known` kümesindeyse kabul edilir (D19a-2): `:` düz
    yazı noktalamasıdır ve "Kind: Bullish Reversal" gibi masum bir GİRİŞ
    alarm metni, tanınmayan bir `kind` üzerinden 422 almamalıdır.
    `known=None` = kısıt yok (yalnız telemetri alanları için).

    Koşuda birden çok eşleşme varsa İLK KABUL EDİLEBİLİR olan alınır: bir
    `kind: prose` reddi, aynı satırdaki gerçek bir `kind=exit`i gölgelemesin.
    """
    for match in pattern.finditer(header):
        separator, value = match.group(1), match.group(2).strip().lower()
        if separator == ":" and known is not None and value not in known:
            continue
        return value
    return ""


def _tv_body_event_source_mentions(raw: str, *, secret: str = "") -> set:
    """Gövdenin HER YERİNDE geçen `src=`/`source=` OLAY KAYNAĞI adları.

    D19a-2 (bulgu A'nın ikinci yüzü): yönlendirme belirteçleri yalnız başlık
    koşusundan okunur (G1). Ama kullanıcı `src=pac_choch kind=choch bearish`i
    mesajın ORTASINA yazarsa hiçbiri okunmaz → `kind` yokluğu "entry"dir →
    bir CHoCH alarmı sessizce GİRİŞ OYUNA dönüşür ve `bearish` sözcüğü
    yüzünden yön bile çözülür (pozisyon açar).

    Bu tarama YÖNLENDİRME YAPMAZ (G1 korunur); yalnız "bu gövde bir olay
    alarmı olmaya çalışıyor" kanıtını `reject_entry_vote_from_event_source`'a
    verir → istek 422 ile GÖRÜNÜR biçimde ölür. Yanlış-pozitif riski yok
    denecek kadar küçüktür: serbest metnin `src=luxso_exit` gibi TAM bir
    olay-kaynağı adı taşıması, o gövdenin zaten yanlış kurulmuş bir olay
    alarmı olduğu anlamına gelir.
    """
    scan = raw.replace(secret, "") if secret else raw
    found = {m.group(2).strip().lower() for m in _TV_BODY_SRC_RE.finditer(scan)}
    return found & _tv_event_sources()


# `{{ticker}} BUY` / `BINANCE:BTCUSDT.P SELL` — mevcut 49 alarmın en yalın
# GİRİŞ biçimi. Sembol + yön sözcüğünden BAŞKA hiçbir şey taşımaz.
_TV_SIMPLE_ENTRY_RE = re.compile(
    r"^(?:BINANCE:)?[A-Z0-9]{2,15}USDT(?:\.P)?\s+(?:BUY|SELL|LONG|SHORT)$",
    re.IGNORECASE,
)
# "entry" giriş yolunun kendisidir; bir OLAY iddiası değildir.
_TV_NON_ENTRY_KINDS = frozenset(EVENT_KINDS) - {"entry"}


def _tv_body_is_algopro_format(raw: str) -> bool:
    """AlgoPro/BotV3 tek satır giriş biçiminin parmak izi.

    TEK parmak izi: `resolve_tv_source` (kaynak tahmini) ve
    `_tv_body_is_known_entry_format` (fail-loud kapısının yanlış-pozitif
    kalkanı) AYNI fonksiyonu çağırır — iki kopya, iki davranış demektir.
    """
    return "| TF:" in raw or "| Price:" in raw


def _tv_body_event_kind_mentions(raw: str, *, secret: str = "") -> set:
    """Gövdenin HER YERİNDE geçen `kind[=:]<TANINAN OLAY KIND>` belirteçleri.

    D19a-2 R1-1'in KAPATMADIĞI yüz (bütünleşme incelemesi, 2026-08-23):
    `_tv_body_event_source_mentions` yalnız `src=<olay kaynağı>` arar. Bir
    olay alarmının mesajında `src=` HİÇ YOKSA (ya da yanlış yazıldıysa) ve
    belirteçler başlık koşusu DIŞINDAysa — ör. `BTCUSDT.P kind=choch bullish`
    ya da `Bullish S-CHOCH kind=choch BTCUSDT.P` — hiçbir şey okunmaz,
    `kind` yokluğu "entry"dir ve `bullish` sözcüğü yönü çözer: CHoCH alarmı
    GİRİŞ OYU olur (`TV_CONFLUENCE_REQUIRED=1` ile doğrudan
    `external_signal`). Yani D19a'nın "hiçbir olay alarmı giriş oyuna
    dönüşmez" iddiası `src=` yokken tutmuyordu.

    Bu tarama da YÖNLENDİRME YAPMAZ (G1 korunur): yalnız "bu gövde bir olay
    alarmı olmaya çalışıyor" kanıtını üretir. EVENT_KINDS DIŞI değerler yok
    sayılır (`kind=momentum` bir olay kanalı iddiası değildir) ve "entry"
    zaten giriş yolunun kendisidir.

    ⚠️ JSON gövdede `"kind": "choch"` biçimi bu regex'e TAKILMAZ (anahtarla
    ayraç arasında tırnak var) — bilinçli: JSON giriş gövdeleri
    `_tv_body_is_known_entry_format` ile zaten muaftır.
    """
    scan = raw.replace(secret, "") if secret else raw
    found = {m.group(2).strip().lower() for m in _TV_BODY_KIND_RE.finditer(scan)}
    return found & _TV_NON_ENTRY_KINDS


def _tv_body_is_known_entry_format(raw: str, *, secret: str = "") -> bool:
    """Gövde, mevcut 49 GİRİŞ alarmının TANINAN biçimlerinden biri mi?

    Fail-loud kapısının (`reject_entry_vote_from_kind_mention`) YANLIŞ-POZİTİF
    kalkanı: serbest metninde tesadüfen `kind=exit` geçen MEŞRU bir giriş
    alarmı 422 almamalı (bkz. `test_free_text_without_event_source_still_enters`
    — AlgoPro girişinin `msg:` alanında `kind=exit` geçiyor).

    Tanınan üç biçim:
      1. **JSON giriş gövdesi** — `symbol`/`side`/`action`/`direction` alanı
         taşıyan bir nesne. Üst düzey ve `data` altındaki `kind` zaten
         `resolve_tv_body_fields` ile okundu; buraya geldiysek okunan bir
         `kind` YOKTUR, yani daha derindeki bir alan söz konusudur (G1
         bilinçli kör noktası — davranışı DEĞİŞMİYOR).
      2. **AlgoPro/BotV3 tek satır biçimi** — `| TF:` / `| Price:` parmak izi
         (`resolve_tv_source`'un BUGÜN kullandığının aynısı).
      3. **`{{ticker}} BUY|SELL`** — sembol + yön sözcüğünden başka hiçbir
         şey taşımayan yalın giriş metni.
    """
    payload = _tv_payload(raw)
    if payload and any(
        payload.get(name) for name in ("side", "action", "direction", "symbol")
    ):
        return True
    if _tv_body_is_algopro_format(raw):
        return True
    scan = raw.replace(secret, "") if secret else raw
    return bool(_TV_SIMPLE_ENTRY_RE.match(scan.strip()))


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
        fallback = "algopro" if _tv_body_is_algopro_format(raw_body) else "tv"
        return fallback, False
    if source not in _tv_source_allowlist():
        return "tv", True
    return source, False


def _maybe_forward_to_follower(request: Request, raw: str) -> None:
    """GERÇEK AlgoPro gövdesini takipçi halkasına ilet (fire-and-forget).

    Ana motorun akışını ETKİLEMEZ: ayrı task, kısa bağlantı timeout'u, her
    hata loglanır ve yutulur (bkz. `src/services/follower_forwarder.py`).

    D20a (düşmanca inceleme bulgu 5): iletim kararı ARTIK `resolve_tv_source`
    ile VERİLMEZ. O çözücü `?src=` yoksa gövdede `"| TF:"` ya da `"| Price:"`
    görmesi yeterli sayıyordu — elle yazılmış bir LuxAlgo/BotV3 şablonu bu
    parmak izini taşıyabilir ve takipçide sonucu POZİSYON açmaktır. Karar
    artık gövdenin KENDİSİNE bakan katı tanıyıcıdadır
    (`follower/parser.algopro_alert_kind`) ve `TV_SOURCE_ALLOWLIST`'ten
    BAĞIMSIZDIR; `?src=` yalnız telemetri/log olarak taşınır.
    """
    try:
        maybe_forward_algopro_event(
            raw, str(request.query_params.get("src") or "")
        )
    except Exception as exc:  # savunmacı: köprü ana akışı ASLA düşürmez
        app_logger.warning(f"⚠️ Takipçi köprüsü çağrılamadı ({exc})")


async def _maybe_route_embedded_follower(raw: str, *, dry_run: bool):
    """GERÇEK AlgoPro alert() gövdesini SÜREÇ İÇİ takipçiye teslim et (D20b).

    Yalnız `FOLLOWER_EMBEDDED=true` iken devreye girer. Karar gövdenin
    KENDİSİNE bakan katı tanıyıcıdadır (`parser.algopro_alert_kind`, D20a
    bulgu 5 ile AYNI kural) ve `TV_SOURCE_ALLOWLIST`/`?src=`ten BAĞIMSIZDIR.

    Bu gövde ana botun SAĞLAMASINA OY VERMEZ: AlgoPro alert() biçimi
    takipçinin giriş/çıkış komut hattıdır, scalper'ın dış sinyal oyu değil.
    Ana botun bugünkü TV davranışı DEĞİŞMEZ — eski özel mesaj biçimi
    ("BUY on {{ticker}} | TF: 5 | Price: …") katı tanıyıcıdan GEÇMEZ ve
    eskisi gibi oy vermeye devam eder.

    **TEK AYRIŞTIRICI (düşmanca inceleme):** yönlendirme kararı, dry-run
    raporu ve gerçek yürütme AYNI `parse_follower_event()` sonucundan üretilir.
    Eskiden karar `algopro_alert_kind` (yalnız AlgoPro kalıbı), yürütme ise
    `parse_follower_event` (önce `kind=` şablonu) ile veriliyordu; gövdede bir
    `kind=exit` belirteci varsa `?dry_run=1` "entry" raporlarken gerçek istek
    pozisyonu KAPATIYORDU.

    **EVREN KAPISI (düşmanca inceleme):** takipçi evreni dışındaki bir GİRİŞ
    sessizce yutulmaz — 200 + `accepted:false` +
    `reason:"symbol_not_in_follower_universe"` + WARNING + sayaç ile raporlanır.
    Giriş/oy yoluna DÜŞMEZ: ana botun davranışı bu dalda da değişmez.
    ÇIKIŞ/HIT olayları koşulsuz iletilir (evren daralsa bile açık pozisyonun
    çıkışı düşmemeli — D20a bulgu 9 ilkesi).

    Dönüş: yanıt sözlüğü (yönlendirme yapıldı) ya da None (bu gövde takipçinin
    değil → çağıran bugünkü yola devam eder).
    """
    if not settings.follower_embedded:
        return None
    try:
        from src.strategies.follower.parser import (
            algopro_alert_kind,
            parse_follower_event,
        )
        from src.strategies.follower.types import (
            KIND_ENTRY,
            ROUTE_REJECT_OUTSIDE_UNIVERSE,
            FollowerParseError,
        )
    except Exception as exc:  # pragma: no cover - savunmacı
        app_logger.warning(f"⚠️ Gömülü takipçi tanıyıcısı yüklenemedi ({exc})")
        return None

    try:
        if algopro_alert_kind(raw) is None:
            return None
    except Exception as exc:  # pragma: no cover - savunmacı
        app_logger.warning(f"⚠️ Gömülü takipçi ön kapısı çalışmadı ({exc})")
        return None

    received_monotonic = time.monotonic()
    try:
        event = parse_follower_event(raw)
    except FollowerParseError as exc:
        app_logger.warning(f"⚠️ Gömülü takipçi: AlgoPro gövdesi çözülemedi ({exc})")
        raise HTTPException(status_code=422, detail=f"AlgoPro gövdesi: {exc}")

    universe = {
        str(sym).strip().upper()
        for sym in (getattr(settings, "follower_universe", []) or [])
        if str(sym).strip()
    }
    outside = bool(
        event.kind == KIND_ENTRY and universe and event.symbol not in universe
    )

    if dry_run:
        # `?dry_run=1` HİÇBİR yan etki üretmez (pozisyon açmak bir yan
        # etkidir) — yalnız yapılacak işi RAPORLAR. Rapor, gerçek yolun
        # kullandığı AYNI ayrıştırmadan gelir.
        return {
            "dry_run": True,
            "would": {
                "routed": "follower",
                "kind": event.kind,
                "symbol": event.symbol,
                "direction": (
                    event.direction.value if event.direction is not None else None
                ),
                "accepted": not outside,
                "reason": ROUTE_REJECT_OUTSIDE_UNIVERSE if outside else None,
            },
        }

    if outside:
        if follower_engine is not None:
            follower_engine.note_route_reject(
                ROUTE_REJECT_OUTSIDE_UNIVERSE, kind=event.kind
            )
        app_logger.warning(
            f"⚠️ AlgoPro girişi işlenmedi: {event.symbol} takipçi evreninde "
            f"({sorted(universe)}) DEĞİL. Gömülü modda AlgoPro alert() gövdeleri "
            f"YALNIZ takipçiye aittir; ana botun sağlamasına oy YAZILMAZ "
            f"(D20b). Bu sembolü takipçiye vermek için FOLLOWER_SYMBOLS'a "
            f"ekleyin ya da alarmını kapatın."
        )
        return {
            "routed": "follower",
            "embedded": True,
            "kind": event.kind,
            "symbol": event.symbol,
            "accepted": False,
            "reason": ROUTE_REJECT_OUTSIDE_UNIVERSE,
        }

    if not follower_engine:
        raise HTTPException(
            status_code=503, detail="Gömülü AlgoPro takipçisi hazır değil"
        )
    result = await follower_engine.handle_event(
        event, received_monotonic=received_monotonic
    )
    return {
        "routed": "follower",
        "embedded": True,
        "kind": event.kind,
        "symbol": event.symbol,
        **result,
    }


def _tv_truthy(value) -> bool:
    """`?dry_run=1|true|yes` gibi sorgu bayraklarını çöz."""
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _tv_payload(raw: str) -> dict:
    """Gövde JSON nesnesi ise sözlük olarak döndür, değilse boş sözlük."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tv_provided_secret(payload: dict, raw: str, url_secret: str) -> str:
    """Secret'ı gövde alanı → metin içi `secret=` → `?secret=` sırasıyla bul.

    `resolve_tv_signal` ve `resolve_tv_event` AYNI sırayı kullanmak
    ZORUNDADIR (iki ayrı kopya, iki ayrı davranış demektir) — bu yüzden tek
    yerde. Karşılaştırma sabit-zamanlıdır ve ÇAĞIRANDA yapılır.
    """
    provided = str(payload.get("secret") or "")
    if not provided:
        match = _TV_SECRET_RE.search(raw)
        provided = match.group(1) if match else ""
    if not provided:
        provided = str(url_secret or "")
    return provided


def _tv_symbol(payload: dict, raw: str) -> str:
    """Sembolü `symbol` alanından veya metinden çöz. Hata → 422."""
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
    return symbol


def resolve_tv_body_fields(raw: str, *, secret: str = "") -> dict:
    """Gövdeden `src`/`source`, `kind` ve `via` oku (JSON alanı VEYA belirteç).

    Neden gövde: kullanıcı yeni alarmları MEVCUT alarmları klonlayarak
    kuruyor — webhook URL'si (secret ve eski `?src=`) aynen kalıyor, yalnız
    koşul ve mesaj değişiyor. Bu yüzden yeni yönlendirme bilgisi URL'de
    DEĞİL gövdede taşınmak zorunda.

    Okuma sırası (D19a bulgu G1 ile daraltıldı):
      1. JSON **üst düzey** alanlar (`src`/`source`, `kind`, `via`),
      2. JSON **üst düzey `data`** nesnesi (yaygın webhook sarmalayıcısı),
      3. düz metin **başlık koşusu** — `_tv_header_run`. İÇ İÇE JSON
         ARANMAZ ve SERBEST METİN TARANMAZ: `{{strategy.order.alert_message}}`
         gibi kullanıcı metni mevcut bir alarmın kimliğini/yolunu
         DEĞİŞTİREMEZ.

    Tarama öncesi secret metinden ÇIKARILIR (yön taramasındaki ilkeyle
    aynı: secret'ın içeriği hiçbir zaman anlamlı belirteç sayılmaz).

    Dönüş: yalnız BULUNAN anahtarları taşıyan sözlük ({} = hiçbiri yok =
    bugünkü davranış).
    """
    payload = _tv_payload(raw)
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}

    def _field(*names) -> str:
        for source in (payload, data):
            for name in names:
                value = str(source.get(name) or "").strip().lower()
                if value:
                    return value
        return ""

    src = _field("src", "source")
    kind = _field("kind")
    via = _field("via")

    if not src or not kind or not via:
        scan = raw
        if secret:
            scan = scan.replace(secret, "")
        header = _tv_header_run(scan)
        if header:
            if not src:
                src = _tv_token(
                    _TV_BODY_SRC_RE, header,
                    known=_tv_source_allowlist() | _tv_event_sources(),
                )
            if not kind:
                kind = _tv_token(_TV_BODY_KIND_RE, header, known=EVENT_KINDS)
            if not via:
                via = _tv_token(_TV_BODY_VIA_RE, header, known=None)

    fields = {}
    if src:
        fields["src"] = src
    if kind:
        fields["kind"] = kind
    if via:
        fields["via"] = via
    return fields


def resolve_tv_kind(raw_kind) -> str:
    """Gövdedeki `kind`i doğrula. Yoksa "entry" (bugünkü davranış).

    Tanınmayan bir `kind` "entry"ye DÜŞMEZ, 422 ile REDDEDİLİR: bir çıkış
    alarmının yazım hatası yüzünden GİRİŞ oyuna dönüşmesi (pozisyon açması)
    kabul edilemez. Bu, `?src=` allowlist'inin "reddetme, tv'ye eşle"
    davranışının BİLİNÇLİ tersidir — orada en kötü sonuç bir oyun
    sayılmaması, burada istenmeyen bir işlemdir.
    """
    kind = str(raw_kind or "").strip().lower()
    if not kind:
        return "entry"
    if kind not in EVENT_KINDS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Geçersiz kind — "
                f"{sorted(EVENT_KINDS)} değerlerinden biri olmalı"
            ),
        )
    return kind


def resolve_tv_source_with_body(raw_src_param, body_src, raw_body: str):
    """Kaynak etiketi: GÖVDEDEKİ `src` allowlist'teyse `?src=`'i geçersiz kılar.

    Klon senaryosu: alarm URL'si eski `?src=luxso`yu taşımaya devam eder ama
    gövde `src=luxso_exit` der — kaynak gövdeninkidir. Gövdedeki değer
    allowlist DIŞINDAysa geçersiz kılma YAPILMAZ (WARNING + bugünkü
    `?src=` davranışı) ki bir yazım hatası kaynak kimliğini sessizce
    bozmasın.

    Dönüş: (source, source_raw_rejected, body_src_rejected).
    """
    body_src = str(body_src or "").strip().lower()
    if body_src and body_src in _tv_source_allowlist():
        return body_src, False, False
    source, source_raw_rejected = resolve_tv_source(raw_src_param, raw_body)
    return source, source_raw_rejected, bool(body_src)


def resolve_tv_event_source(raw_src_param, body_src, raw_body: str):
    """OLAY yolunda kaynak etiketi — allowlist'ten BAĞIMSIZ (D19a bulgu E).

    Giriş yolundan farkı bilinçlidir: orada allowlist bir SAYIM korumasıdır
    (hayalet kaynak sağlama kotasını dolduramasın). Olay yolu sağlamaya HİÇ
    girmez ve istek `TV_WEBHOOK_SECRET` ile kimliklenmiştir; orada
    allowlist'i dayatmak, sunucu `.env`'i `TV_SOURCE_ALLOWLIST`'i açıkça set
    ettiğinde `src=pac_choch`ı sessizce eski `?src=luxso` etiketine düşürür
    ve kapı hiç çalışmazdı (kanal "kurulu görünüp ölü" olurdu).

    Allowlist dışı bir olay kaynağı REDDEDİLMEZ ama WARNING + telemetri ile
    GÖRÜNÜR kılınır (yazım hatası fark edilsin).

    Dönüş: (source, allowlisted, from_body).
    """
    body_src = str(body_src or "").strip().lower()
    if body_src:
        return body_src, body_src in _tv_source_allowlist(), True
    source, rejected = resolve_tv_source(raw_src_param, raw_body)
    return source, not rejected, False


def reject_entry_vote_from_event_source(
    source, body_src, raw_src_param, body_mentions=()
) -> None:
    """Olay kaynağı `kind=entry` ile GİRİŞ OYU VEREMEZ → 422 (D19a bulgu A).

    Saldırı/kaza senaryosu (uçtan uca doğrulandı): bir ÇIKIŞ alarmının
    mesajından `kind` belirteci düşerse (yazım hatası, iç içe JSON, TV
    şablonunda unutma) `kind` yokluğunun varsayılanı "entry"dir ve gövdedeki
    `src=luxso_exit`/`pac_choch` allowlist'te olduğu için istek YENİ BİR
    SAĞLAMA KAYNAĞI olarak sayılır. `TV_CONFLUENCE_REQUIRED=2` ile LuxAlgo
    ailesi tek başına 2/2 kotayı doldurup POZİSYON AÇTIRABİLİR.

    Bu yüzden kontrol yalnız ÇÖZÜLEN kaynağa değil, isteğin taşıdığı TÜM
    kaynak adaylarına (gövde `src`, `?src=`) uygulanır: allowlist dışı bir
    olay kaynağı adı `tv`ye eşlenip korumadan sıyrılamasın.

    `body_mentions` (D19a-2): başlık koşusu DIŞINDA, gövdenin herhangi bir
    yerinde geçen olay-kaynağı adları. Belirteçleri mesajın ortasına yazan
    bir kullanıcı, yönlendirmeyi kaybeder (G1 bilinçli) ama isteği SESSİZCE
    bir giriş oyuna dönüşmemeli — 422 ile GÖRÜNÜR biçimde ölmeli.
    """
    event_sources = _tv_event_sources()
    candidates = {
        str(value or "").strip().lower()
        for value in (source, body_src, raw_src_param)
    }
    candidates |= {str(value or "").strip().lower() for value in body_mentions}
    hits = sorted(candidates & event_sources)
    if not hits:
        return
    try:
        tv_events.note("rejected_entry_from_event_source")
    except Exception:  # telemetri asla akışı bozmaz
        pass
    app_logger.warning(
        f"⛔ TV webhook: olay kaynağı {hits} GİRİŞ OYU gönderdi (kind=entry) — "
        "422 ile reddedildi. Alarm mesajında `kind=exit|choch|trend|tp1` "
        "belirteci eksik olabilir (docs/INTEGRATIONS.md §7.2)"
    )
    raise HTTPException(
        status_code=422,
        detail=(
            f"Olay kaynağı giriş oyu veremez: {hits} — mesajda "
            "kind=exit|choch|trend|tp1 belirteci eksik ya da mesajın BAŞINDA "
            "değil (docs/INTEGRATIONS.md §7.1 'başlık koşusu')"
        ),
    )


def reject_entry_vote_from_kind_mention(raw: str, *, secret: str = "") -> None:
    """`kind=<olay kind>` taşıyan ama GİRİŞ biçimi OLMAYAN gövde → 422.

    Bütünleşme incelemesi bulgusu (2026-08-23, high): D19a'nın kalkanı
    (`reject_entry_vote_from_event_source`) yalnız `src=<olay kaynağı>`
    adına bakar. `src=` HİÇ YOKSA ya da yanlış yazıldıysa ve belirteçler
    başlık koşusu dışındaysa istek GİRİŞ yoluna düşüyor ve gövdedeki
    `bullish`/`bearish` sözcüğüyle OY VERİYORDU (ölçüldü:
    `BTCUSDT.P kind=choch bullish` → `external_signal`). Yani "hiçbir olay
    alarmı giriş oyuna dönüşmez" değişmez kuralı `src=` yokken tutmuyordu.

    Kapı ASİMETRİKTİR (yanlış-pozitif vermemek için):
      * gövdede TANINAN bir olay `kind`i geçiyor **ve**
      * gövde tanınan bir GİRİŞ biçimi DEĞİL
    ise 422. Giriş biçimi tanınıyorsa serbest metindeki `kind=` YOK SAYILIR
    (AlgoPro'nun `msg: … kind=exit` alanı bugünkü gibi işlem açtırmaya devam
    eder — `test_free_text_without_event_source_still_enters`).

    Yönlendirme DEĞİŞMEZ (G1 korunur): bu fonksiyon hiçbir isteği olay
    yoluna SOKMAZ, yalnız sessiz bir giriş oyunu GÖRÜNÜR bir hataya çevirir.
    """
    kinds = sorted(_tv_body_event_kind_mentions(raw, secret=secret))
    if not kinds:
        return
    if _tv_body_is_known_entry_format(raw, secret=secret):
        return
    try:
        tv_events.note("rejected_entry_kind_mention")
    except Exception:  # telemetri asla akışı bozmaz
        pass
    app_logger.warning(
        f"⛔ TV webhook: gövdede kind={kinds} geçiyor ama belirteçler mesajın "
        "BAŞINDA değil (ve gövde tanınan bir giriş biçimi değil) — 422 ile "
        "reddedildi. Şablon: `src=… kind=… {{ticker}}` "
        "(docs/INTEGRATIONS.md §7.2)"
    )
    raise HTTPException(
        status_code=422,
        detail=(
            f"Olay alarmı yanlış şablon (kind={kinds}): src= ve kind= mesajın "
            "BAŞINDA yazılmalı — `src=… kind=… {{ticker}}` "
            "(docs/INTEGRATIONS.md §7.1 'başlık koşusu')"
        ),
    )


def _tv_event_symbol(payload: dict, raw: str) -> str:
    """Olay yolunda sembol: `_tv_symbol` + KATI biçim doğrulaması (G3).

    `_tv_symbol` (giriş yolu, 49 alarm) `symbol` alanını yalnız "USDT ile
    bitiyor mu" diye süzer; olay yolu ayrıca `_TV_SYMBOL_RE` biçimini TAM
    eşleşmeyle dayatır ki defterde `"'; DROP--USDT"` gibi bir anahtar
    oluşmasın. Giriş yolunun davranışı BİLİNÇLİ olarak değiştirilmedi.
    """
    symbol = _tv_symbol(payload, raw)
    if not _TV_SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(
            status_code=422,
            detail=f"Sembol biçimi geçersiz: {symbol[:24]!r} — BTCUSDT gibi olmalı",
        )
    return symbol


def resolve_tv_event_direction(payload: dict, raw: str, provided_secret: str):
    """Olay yönünü çöz — belirsiz/yoksa None (istisna FIRLATMAZ).

    `resolve_tv_signal`'ın sözlüğü (buy/long/bull ↔ sell/short/bear) burada
    up/down ile genişletilir çünkü S&O trend koşullarının adı "Trend Catcher
    Up"/"Down"dur. Eşleşme SÖZCÜK SINIRIYLA yapılır (bkz. _TV_EVENT_*_RE).

    Metin taramasından secret ve `src=`/`kind=` belirteçleri ÇIKARILIR —
    kaynak adı (`luxso_exit`) ya da secret içeriği yön sanılmasın.
    """
    from src.strategies.scalper.types import Direction

    side_text = str(
        payload.get("side")
        or payload.get("action")
        or payload.get("direction")
        or ""
    ).lower()

    scan_text = raw.lower()
    if provided_secret:
        scan_text = scan_text.replace(provided_secret.lower(), "")
    scan_text = _TV_BODY_SRC_RE.sub(" ", scan_text)
    scan_text = _TV_BODY_KIND_RE.sub(" ", scan_text)
    scan_text = _TV_BODY_VIA_RE.sub(" ", scan_text)
    # Regex sıyırması JSON'da ÇALIŞMAZ (`"src": "…"` — anahtarla ayraç
    # arasında tırnak var). Bu yüzden ÇÖZÜLMÜŞ değerleri de metinden çıkar:
    # tireli bir kaynak adı (`pac-bull`, `luxso-down`) yön sanılmasın.
    nested = payload.get("data")
    nested = nested if isinstance(nested, dict) else {}
    for holder in (payload, nested):
        for key in ("src", "source", "kind", "via"):
            value = str(holder.get(key) or "").strip().lower()
            if len(value) >= 2:
                scan_text = scan_text.replace(value, " ")

    for source in (side_text, scan_text):
        if not source:
            continue
        is_long = bool(_TV_EVENT_LONG_RE.search(source))
        is_short = bool(_TV_EVENT_SHORT_RE.search(source))
        if is_long and not is_short:
            return Direction.LONG
        if is_short and not is_long:
            return Direction.SHORT
        if is_long and is_short:
            return None  # çelişki: yönsüz say (exit/tp1) ya da 422 (choch/trend)
    return None


def resolve_tv_event(raw: str, configured_secret: str, kind: str, url_secret: str = ""):
    """Yapı/çıkış olayını çöz ve doğrula. Dönüş: (symbol, direction|None).

    Secret doğrulaması `resolve_tv_signal` ile AYNI yardımcıyı kullanır
    (403). Sembol aynı yardımcıyla çözülür (422).

    Yön:
      * `choch`/`trend` → ZORUNLU (yapının yönü); çözülemezse 422. Yapı
        durumu yönsüz güncellenemez.
      * `exit`/`tp1`   → OPSİYONEL. LuxAlgo S&O "Exit Signal" ve AlgoPro
        "🎯 TP1 Hit" koşulları YÖNSÜZDÜR; None = "sembolde açık pozisyon
        hangi yöndeyse ona uygulanır".
    """
    payload = _tv_payload(raw)
    provided_secret = _tv_provided_secret(payload, raw, url_secret)
    if not _constant_time_equals(provided_secret, configured_secret):
        raise HTTPException(status_code=403, detail="Geçersiz webhook secret")

    symbol = _tv_event_symbol(payload, raw)
    direction = resolve_tv_event_direction(payload, raw, provided_secret)
    if direction is None and kind in STRUCTURE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"kind={kind} için yön zorunlu — mesajda "
                "bullish/bearish (veya up/down, long/short) gerekli"
            ),
        )
    return symbol, direction


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

    payload = _tv_payload(raw)
    provided_secret = _tv_provided_secret(payload, raw, url_secret)
    if not _constant_time_equals(provided_secret, configured_secret):
        raise HTTPException(status_code=403, detail="Geçersiz webhook secret")

    symbol = _tv_symbol(payload, raw)

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

    D19 (2026-08-23): gövdede `kind=exit|choch|trend|tp1` varsa istek bir
    GİRİŞ OYU DEĞİL, bir YAPI/ÇIKIŞ OLAYIDIR — sağlamaya (TvConfluence)
    HİÇ girmez, `src/services/tv_events.py`'ye yazılır. `kind` yoksa
    davranış bugünküyle birebir aynıdır (mevcut 49 alarm).

    `?dry_run=1` (yalnız sorgu parametresi) İKİ YOLDA DA yan etkisizdir:
    olay yolunda deftere yazılmaz, giriş yolunda sağlamaya oy verilmez ve
    `external_signal`/takipçi köprüsü çağrılmaz — yanıt
    `{"dry_run": true, "would": {...}}`.
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

    url_secret = request.query_params.get("secret") or ""
    # D19a bulgu G2 — SECRET ÖNCE, ayrıştırmadan ve HER 422'den ÖNCE.
    # Kimliksiz bir istek `kind` doğrulamasına ulaşırsa 422 mesajından
    # geçerli `kind` listesini (yani kanalın varlığını ve sözleşmesini)
    # öğrenir. Karşılaştırma sabit zamanlıdır (`_constant_time_equals`) ve
    # her iki dal secret'ı KENDİ içinde tekrar doğrular (saf çözücülerin
    # tek başına da güvenli kalması için — bkz. resolve_tv_signal).
    if not _constant_time_equals(
        _tv_provided_secret(_tv_payload(raw), raw, url_secret), configured
    ):
        raise HTTPException(status_code=403, detail="Geçersiz webhook secret")

    # Gövde yönlendirmesi (D19). Ayrıştırma yan etkisizdir.
    body_fields = resolve_tv_body_fields(raw, secret=configured)
    kind = resolve_tv_kind(body_fields.get("kind"))
    raw_src_param = request.query_params.get("src")
    dry_run = _tv_truthy(request.query_params.get("dry_run"))
    if kind != "entry":
        return await _handle_tv_event(
            raw=raw,
            configured=configured,
            kind=kind,
            body_src=body_fields.get("src"),
            body_via=body_fields.get("via"),
            raw_src_param=raw_src_param,
            url_secret=url_secret,
            dry_run=dry_run,
        )

    # GÖMÜLÜ TAKİPÇİ (D20b): gövde GERÇEK bir AlgoPro alert() mesajıysa
    # (BUY/SELL/EXIT/TPn HIT/SL HIT + `| BINANCE: | TF: | Price:` + girişte
    # dört seviye) istek SÜREÇ İÇİ takipçiye teslim edilir ve BURADA biter:
    # ana botun sağlamasına oy YAZILMAZ, `external_signal` ÇAĞRILMAZ.
    # `FOLLOWER_EMBEDDED=false` (varsayılan) → None döner, aşağıdaki yol
    # bugünküyle birebir aynıdır.
    routed = await _maybe_route_embedded_follower(raw, dry_run=dry_run)
    if routed is not None:
        return routed

    # Kaynak etiketi: GÖVDEDEKİ `src` (allowlist'teyse) `?src=`'i geçersiz
    # kılar (klon senaryosu, D19); yoksa alarm URL'sindeki ?src=... geçerli;
    # o da yoksa AlgoPro'nun varsayılan mesaj biçimi ("BUY on X | TF: 1 |
    # Price: ...") parmak iziyle tanınır; kalan her şey "tv". Sağlama FARKLI
    # kaynak sayar. Bilinmeyen ?src= REDDEDİLMEZ, "tv"ye eşlenir ve WARNING
    # loglanır (bkz. resolve_tv_source / config.py tv_source_allowlist yorumu).
    source, source_raw_rejected, body_src_rejected = resolve_tv_source_with_body(
        raw_src_param, body_fields.get("src"), raw
    )
    # Olay kaynağı GİRİŞ OYU VEREMEZ — sembol/yön çözümünden ÖNCE (D19a A).
    reject_entry_vote_from_event_source(
        source,
        body_fields.get("src"),
        raw_src_param,
        _tv_body_event_source_mentions(raw, secret=configured),
    )
    # `src=` YOK ama `kind=<olay kind>` VAR: aynı değişmez kuralın ikinci
    # yüzü (bütünleşme incelemesi). Giriş biçimi tanınıyorsa YOK SAYILIR.
    reject_entry_vote_from_kind_mention(raw, secret=configured)

    # Takipçi köprüsü (D20): AlgoPro kaynaklı GİRİŞ-YOLU gövdeleri takipçi
    # halkasına iletilir. Secret yukarıda zaten doğrulandı (403'te buraya hiç
    # gelinmez → kimliksiz gövde takipçiye enjekte edilemez). AlgoPro'nun
    # "⚪ EXIT | …", "🎯 TP1 HIT | …", "🛑 SL HIT | …" mesajları yön kelimesi
    # taşımaz ve resolve_tv_signal'da 422 alır — takipçi için bunlar KRİTİK
    # olaylardır, o yüzden iletim 422'den ÖNCE yapılır. D19 olay yolu
    # (kind != entry) yukarıda ayrıldı; olay kaynakları buraya gelmez.
    try:
        symbol, direction = resolve_tv_signal(raw, configured, url_secret=url_secret)
    except HTTPException as exc:
        # `dry_run` HİÇBİR yan etki üretmez — takipçi halkası da bir yan
        # etkidir (orada POZİSYON açılabilir), bu yüzden köprü atlanır.
        if exc.status_code == 422 and not dry_run:
            _maybe_forward_to_follower(request, raw)
        raise

    source_fields = {"source": source}
    if source_raw_rejected:
        source_fields["source_raw_rejected"] = True
    if body_src_rejected:
        source_fields["body_src_rejected"] = True
    elif body_fields.get("src"):
        source_fields["source_from_body"] = True

    source_blocked = source in _tv_entry_source_blocklist()

    # `?dry_run=1` GİRİŞ yolunda da yan etkisizdir (bütünleşme incelemesi):
    # daha önce sessizce YOK SAYILIYORDU, yani RUNBOOK'un "doğrulama"
    # komutu sağlamaya OY yazıp `external_signal` üzerinden GERÇEK EMİR
    # açabilirdi. Motor hazır olmasa bile yanıt verilir (503 dönmez):
    # doğrulamanın değeri motordan bağımsızdır — olay yoluyla AYNI ilke.
    if dry_run:
        app_logger.info(
            f"🧪 TV girişi (DRY-RUN, oy YAZILMADI): {symbol} "
            f"{direction.value} ← {source}"
        )
        return {
            "dry_run": True,
            "would": {
                "symbol": symbol,
                "direction": direction.value,
                **source_fields,
                **(
                    {
                        "accepted": False,
                        "blocked_by": scalp_intent.REASON_TV_SOURCE_BLOCKED,
                    }
                    if source_blocked
                    else {}
                ),
            },
        }

    # D28: katı AlgoPro alert() gövdesi yukarıda gömülü takipçide tüketildi.
    # Buraya kalan basit `src=algopro` oyu, canlı defterde zararın ana kaynağı
    # olduğu ölçüldüğünde karantinaya alınabilir. Oyu confluence halkasına
    # YAZMA ve HTTP takipçi köprüsüne iletme; aksi hâlde kapı yalnız görünüşte
    # çalışır. Reddedilen niyet ölçüm defterinde yaşamaya devam eder.
    if source_blocked:
        intent_epoch = time.time()
        intent_at = datetime.fromtimestamp(intent_epoch, timezone.utc).isoformat()
        try:
            scalp_intent.record(
                at=intent_at,
                symbol=symbol,
                direction=direction,
                stage=scalp_intent.STAGE_DECIDED,
                decision=scalp_intent.DECISION_DENY,
                reason=scalp_intent.REASON_TV_SOURCE_BLOCKED,
                source=source,
            )
            counterfactual_store.register(
                at=intent_at,
                at_epoch=intent_epoch,
                symbol=symbol,
                direction=direction,
                reason=scalp_intent.REASON_TV_SOURCE_BLOCKED,
                source=source,
            )
        except Exception:  # pragma: no cover - teşhis kaydı akışı ASLA kesmez
            pass
        app_logger.warning(
            f"TV girişi performans karantinasında: {symbol} "
            f"{direction.value} ← {source}"
        )
        return {
            "symbol": symbol,
            "direction": direction.value,
            "accepted": False,
            "blocked_by": scalp_intent.REASON_TV_SOURCE_BLOCKED,
            **source_fields,
        }

    _maybe_forward_to_follower(request, raw)

    if not scalper_engine:
        raise HTTPException(status_code=503, detail="Scalper hazır değil")

    if source_raw_rejected:
        app_logger.warning(
            f"TV webhook: allowlist dışı ?src='{str(raw_src_param)[:32]}' — "
            f"'tv' olarak eşleştirildi (yazım hatası ya da tanınmayan entegrasyon olabilir)"
        )
    if body_src_rejected:
        app_logger.warning(
            f"TV webhook: allowlist dışı gövde src='{str(body_fields.get('src'))[:32]}' — "
            f"yok sayıldı, kaynak '{source}' (?src=/parmak izi) olarak kaldı"
        )

    required = max(1, int(getattr(settings, "tv_confluence_required", 1) or 1))
    if required > 1:
        verdict = _tv_confluence().vote(symbol, direction.value, source)
        if not verdict["triggered"]:
            # D24/madde 7: sağlaması DOLMAYAN TV oyu bugün hiçbir yerde iz
            # bırakmıyor (motor hiç çağrılmıyor → adli kayıt da yok). Yalnız
            # KAYIT: yanıt gövdesi ve oy defteri DEĞİŞMEZ, hata yutulur.
            try:
                intent_at = datetime.now(timezone.utc).isoformat()
                scalp_intent.record(
                    at=intent_at,
                    symbol=symbol,
                    direction=direction,
                    stage=scalp_intent.STAGE_DECIDED,
                    decision=scalp_intent.DECISION_DENY,
                    reason=scalp_intent.REASON_TV_CONFLUENCE,
                    source=source,
                    extra={
                        "votes": verdict.get("votes"),
                        "required": verdict.get("required"),
                        "sources": list(verdict.get("sources") or []),
                    },
                )
                # D27/B karşı-olgu defteri: raporun EN KRİTİK açık sorusu
                # tam da bu kapıdır ("sağlamanın reddettiği 150+ sinyal
                # gerçekten kötü müydü?" — seçicilik gücü LONG p=0.894,
                # SHORT p=0.368 = ölçülebilir SIFIR). Burada bir
                # `ScalpSignal` YOKTUR: plan (giriş/stop/TP1) ÇÖZÜM anında,
                # niyet anından SONRAKİ ilk mumdan ROI politikasıyla
                # tamamlanır (`counterfactual_store._fill_plan`) — bu
                # istekte HİÇBİR REST çağrısı yapılmaz.
                counterfactual_store.register(
                    at=intent_at,
                    at_epoch=time.time(),
                    symbol=symbol,
                    direction=direction,
                    reason=scalp_intent.REASON_TV_CONFLUENCE,
                    source=source,
                )
            except Exception:  # pragma: no cover - kayıt akışı ASLA kesmez
                pass
            return {
                "symbol": symbol,
                "direction": direction.value,
                "accepted": False,
                "confluence": verdict,
                **source_fields,
            }
        # D21: sağlama özeti YALNIZ adli kayda taşınır (karar yoluna girmez).
        result = await scalper_engine.external_signal(
            symbol, direction, tv_meta={"source": source, **verdict}
        )
        return {
            "symbol": symbol,
            "direction": direction.value,
            **result,
            "confluence": verdict,
            **source_fields,
        }

    result = await scalper_engine.external_signal(
        symbol,
        direction,
        tv_meta={
            "source": source,
            "sources": [source],
            "votes": 1,
            "required": required,
            "window_seconds": float(
                getattr(settings, "tv_confluence_window_seconds", 0.0) or 0.0
            ),
        },
    )
    return {"symbol": symbol, "direction": direction.value, **source_fields, **result}


async def _handle_tv_event(
    *,
    raw: str,
    configured: str,
    kind: str,
    body_src,
    body_via=None,
    raw_src_param,
    url_secret: str,
    dry_run: bool = False,
):
    """`kind != entry` dalı — YAPI/ÇIKIŞ olayı (D19).

    Bu dal SAĞLAMAYA (TvConfluence) HİÇ girmez ve `external_signal`
    ÇAĞIRMAZ; yalnız `tv_events` defterine yazar. Motor hazır olmasa bile
    olay kaydedilir (503 dönmez): olayın değeri motordan bağımsızdır ve
    restart penceresinde kaybolması istenmez.

    Yönlendirme `kind`e bakar ve **allowlist'ten bağımsızdır** (D19a bulgu
    E): `TV_SOURCE_ALLOWLIST` eski/eksik olsa bile `kind != entry` bir istek
    ASLA giriş yoluna düşmez. Kaynak etiketi de gövdedeki değer olarak
    korunur; allowlist dışıysa yalnız WARNING üretilir.

    `dry_run=1` (yalnız sorgu parametresi): istek doğrulanır ve yönlendirme
    kararı döndürülür ama DEFTERE YAZILMAZ — canlı defteri kirletmeden
    kurulum doğrulaması yapılabilsin (docs/RUNBOOK.md adım 4).
    """
    symbol, direction = resolve_tv_event(raw, configured, kind, url_secret=url_secret)
    source, allowlisted, from_body = resolve_tv_event_source(
        raw_src_param, body_src, raw
    )
    if not allowlisted:
        app_logger.warning(
            f"TV olayı: allowlist dışı kaynak '{str(source)[:32]}' — olay yolu "
            "allowlist'ten bağımsızdır, etiket KORUNDU. Yazım hatasıysa "
            "SCALPER_TV_EVENTS_GATE_SOURCES eşleşmez ve kapı sessiz kalır "
            "(docs/RUNBOOK.md 'TV olay kanalı' adım 2)"
        )

    # TV sembol allowlist'i (D7) olay yolunda da uygulanır (D19a bulgu F):
    # OSC kanıtı olmayan bir sembolün olayı deftere yazılmamalı, aksi halde
    # o sembolde giriş kapısı/çıkış tetiği kanıtsız karar verirdi.
    #
    # 200 + `applied: false` (422 DEĞİL — D19a-2): AYNI ayar giriş yolunda da
    # sessizce reddeder (`engine.external_signal` → `accepted: false`, 200).
    # İki yolun aynı kapısı TV'de biri yeşil biri kırmızı görünmemeli; 422
    # yalnız BİÇİM hataları içindir (secret, kind, sembol biçimi, eksik yön).
    if not tv_events.symbol_allowed(symbol):
        if not dry_run:
            tv_events.note("rejected_symbol_allowlist")
        app_logger.info(
            f"🚫 TV olayı uygulanmadı: {symbol} — TV sembol allowlist'i dışında "
            f"(kind={kind}, ← {source}; bkz. SCALPER_TV_SYMBOL_ALLOWLIST)"
        )
        return {
            "symbol": symbol,
            "kind": kind,
            "direction": direction.value if direction is not None else None,
            "routed": "event",
            "source": source,
            "source_allowlisted": allowlisted,
            "applied": False,
            "reason": "symbol_allowlist",
            "mode": tv_events.mode(),
        }

    direction_value = direction.value if direction is not None else None
    if dry_run:
        state = tv_events.symbol_state(symbol)
        app_logger.info(
            f"🧪 TV olayı (DRY-RUN, deftere YAZILMADI): {symbol} kind={kind} "
            f"dir={direction_value or '-'} ← {source}"
        )
    else:
        state = tv_events.ingest(
            symbol=symbol,
            kind=kind,
            direction=direction_value,
            source=source,
            via=body_via,
        )
        app_logger.info(
            f"🧭 TV olayı: {symbol} kind={kind} dir={direction_value or '-'} ← {source}"
        )

    response = {
        "symbol": symbol,
        "kind": kind,
        "direction": direction_value,
        "routed": "event",
        "source": source,
        "source_allowlisted": allowlisted,
        "applied": not dry_run,
        "mode": tv_events.mode(),
        "structure": state.get("structure"),
        "state": state,
    }
    if body_via:
        response["via"] = body_via
    if dry_run:
        response["dry_run"] = True
    if from_body:
        response["source_from_body"] = True
    return response


@app.post("/tv-events/reset")
async def tv_events_reset(request: Request):
    """TV olay defterini SIFIRLA (RAM + disk) — `?secret=` ile korunur.

    Neden endpoint: `state/tv_events.json` dosyasını silmek ÇALIŞAN süreci
    temizlemez — defter RAM'de otoritedir ve bir sonraki olayda dosyayı
    yeniden yazar (D19a bulgu G7). Doğru reçete ya bu endpoint ya da
    restart'tır; RUNBOOK bunu böyle anlatır.

    Secret `/tv-signal` ile AYNIdır (aynı kanalın yönetimi) ve sabit zamanlı
    karşılaştırılır. Olay defterini boşaltmak bir RİSK kapısını açmaz:
    en kötü sonucu kapı/çıkış tetiğinin veri gelene kadar sessizleşmesidir.
    """
    configured = (settings.tv_webhook_secret or "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="TV webhook devre dışı — .env'e TV_WEBHOOK_SECRET ekleyin",
        )
    raw = (await request.body()).decode("utf-8", errors="replace").strip()
    if len(raw) > 4096:
        raise HTTPException(status_code=422, detail="Geçersiz gövde")
    provided = _tv_provided_secret(
        _tv_payload(raw), raw, request.query_params.get("secret") or ""
    )
    if not _constant_time_equals(provided, configured):
        raise HTTPException(status_code=403, detail="Geçersiz webhook secret")

    result = tv_events.reset()
    # D22: durum DEĞİŞTİ — pano 5 sn boyunca eski defteri göstermesin.
    _reset_status_caches()
    app_logger.warning(
        f"🧹 TV olay defteri sıfırlandı ({result['cleared_symbols']} sembol) — "
        "kapı/çıkış tetiği yeni olay gelene kadar sessiz"
    )
    return {"reset": True, **result, "snapshot": tv_events.snapshot()}


# ---------------------------------------------------------------------------
# Risk-olayı kanalı (D10) — haber/olay botları giriş durdur/devam et/
# her-şeyi-düzleştir diyebilir. YÖN sinyali GÖNDERMEZ, tv_confluence
# sağlamasından GEÇMEZ — bilinçli olarak AYRI secret (docs/INTEGRATIONS.md §3).
# ---------------------------------------------------------------------------

_RISK_EVENT_ACTIONS = frozenset({"halt", "resume", "flatten", "status"})
_RISK_EVENT_MAX_BODY_BYTES = 4096
_RISK_EVENT_DEFAULT_TTL_MINUTES = 120
_RISK_EVENT_MAX_TTL_MINUTES = 1440
_RISK_EVENT_MAX_REASON_CHARS = 200
_RISK_EVENT_MAX_SOURCE_CHARS = 32


@app.post("/risk-event")
async def risk_event(request: Request):
    """Haber/olay botu köprüsü: giriş kapılarını durdur/devam ettir veya
    tüm açık scalper pozisyonlarını reduce-only kapat.

    TV webhook'undan (`/tv-signal`) FARKLI amaç: bu kanal YÖN önermez, yalnız
    giriş izni verir/kapatır ya da acil kapanış tetikler. Bu yüzden AYRI
    secret (`RISK_EVENT_SECRET`) ister ve sağlamadan (tv_confluence) hiç
    geçmez — tek başına bir "olay" tüm sistemi durdurabilmeli.

    Halt durumu `state/risk_event_halt.json`'da tutulur — mevcut
    `state/scalper_entry_halt.json` (koruma hatası latch'i) dosyasından
    BİLİNÇLİ olarak AYRI ve `SCALPER_ENTRY_HALT_ENABLED` bayrağından
    BAĞIMSIZDIR (bkz. engine.py `_risk_event_halt_snapshot` yorumu).
    Açık pozisyonların SL/TP/trailing yönetimi bu kanaldan etkilenmez.
    """
    configured = (settings.risk_event_secret or "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Risk-event kanalı devre dışı — .env'e RISK_EVENT_SECRET ekleyin",
        )

    raw = await request.body()
    if len(raw) > _RISK_EVENT_MAX_BODY_BYTES:
        raise HTTPException(status_code=422, detail="Gövde çok büyük (>4KB)")

    # Tolerans: resolve_tv_signal ile aynı desen — geçersiz/boş JSON secret
    # kontrolüne kadar sessizce boş sözlüğe düşer, bilgi sızdırmaz (secret
    # doğrulaması her zaman ayrıştırma hatasından SONRA, 403 olarak döner).
    payload: dict = {}
    if raw:
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(parsed, dict):
                payload = parsed
        except ValueError:
            pass

    # Gövde secret'ı VARSA o karar verir (URL'ye sessizce düşülmez) —
    # resolve_tv_signal'daki "body secret wins" kuralıyla aynı.
    provided_secret = str(payload.get("secret") or "")
    if not provided_secret:
        provided_secret = str(request.query_params.get("secret") or "")
    if not _constant_time_equals(provided_secret, configured):
        raise HTTPException(status_code=403, detail="Geçersiz risk-event secret")

    action = str(payload.get("action") or "").strip().lower()
    if action not in _RISK_EVENT_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail="Geçersiz 'action' — halt|resume|flatten|status olmalı",
        )

    reason = str(payload.get("reason") or "").strip()
    if len(reason) > _RISK_EVENT_MAX_REASON_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"'reason' {_RISK_EVENT_MAX_REASON_CHARS} karakteri aşamaz",
        )

    raw_source = payload.get("source")
    source: Optional[str] = (
        str(raw_source).strip() if raw_source not in (None, "") else None
    )
    if source and len(source) > _RISK_EVENT_MAX_SOURCE_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"'source' {_RISK_EVENT_MAX_SOURCE_CHARS} karakteri aşamaz",
        )
    if source == "":
        source = None

    ttl_raw = payload.get("ttl_minutes", _RISK_EVENT_DEFAULT_TTL_MINUTES)
    try:
        # json.loads standart-dışı Infinity/-Infinity/NaN kabul eder;
        # int(float('inf')) OverflowError fırlatır (ArithmeticError'dır,
        # ValueError/TypeError DEĞİL) — yakalanmazsa halt hiç çalışmadan
        # 500'e düşer.
        ttl_minutes = int(ttl_raw)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=422, detail="'ttl_minutes' tamsayı olmalı")
    if ttl_minutes <= 0 or ttl_minutes > _RISK_EVENT_MAX_TTL_MINUTES:
        raise HTTPException(
            status_code=422,
            detail=f"'ttl_minutes' 1..{_RISK_EVENT_MAX_TTL_MINUTES} aralığında olmalı",
        )

    # Halkaya göre motor: scalper modunda `scalper_engine` (bugünkü davranış),
    # takipçi modunda aynı halt/resume/flatten/status sözleşmesini uygulayan
    # `FollowerEngine` (D10 semantiği iki halkada da aynı).
    engine = _risk_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="Scalper hazır değil")
    # D20b: gömülü modda komut İKİ motora da uygulanır (aynı hesap).
    engines = _risk_engines()

    if action in ("halt", "flatten") and not reason:
        raise HTTPException(
            status_code=422, detail=f"'reason' zorunlu (action={action})"
        )

    # .bind(trade=True) ile loguru'ya kwarg GEÇİLMEZ → mesaj üzerinde
    # .format() ÇAĞRILMAZ (F): reason/source çağıran-kontrollü metindir,
    # "{...}" içerirse extra=... kwarg'lı critical() KeyError/IndexError
    # fırlatıp halt/flatten'ı hiç çalışmadan 500'e düşürürdü.
    app_logger.bind(trade=True).critical(
        f"🚨 /risk-event: action={action} reason='{reason}' kaynak={source or '-'} "
        f"ttl={ttl_minutes}dk"
    )

    if action != "status":
        # D22: halt/resume/flatten durumu DEĞİŞTİRİR. Önbellek düşürülmezse
        # pano 5 sn boyunca komut hiç çalışmamış gibi görünürdü — operatör
        # bunu "komut yutuldu" diye okur ve ikinci kez tetikler.
        _reset_status_caches()

    if action == "status":
        snaps = [e.risk_event_status() for e in engines]
        snap = snaps[0]
        return {
            "ok": True,
            "action": action,
            "halted_until": snap.get("until_ts"),
            "reason": snap.get("reason"),
            "flattened": [],
            "errors": [],
            # Gömülü modda "aktif mi?" HERHANGİ bir motorda aktifse EVET'tir
            # ve açık pozisyon sayısı TOPLAMDIR — operatör hesabın tamamını
            # görmeli (tek motorlu kurulumda ikisi de eskisiyle aynı).
            "active": any(bool(s.get("active")) for s in snaps),
            "open_positions": sum(int(s.get("open_positions") or 0) for s in snaps),
        }

    if action == "resume":
        # Çapraz kontrol (doğrulayıcı bulgusu): `halt` dalındaki simetrik
        # kontrol `resume`'da YOKTU — takipçinin halt dosyası silinemezse yanıt
        # `ok:true` derken takipçi HALT'ta kalırdı.
        extra_resumes = [e.risk_event_resume() for e in engines[1:]]
        snap = engine.risk_event_resume()
        if any(bool(x.get("active")) for x in extra_resumes):
            app_logger.bind(trade=True).critical(
                "🚨 /risk-event resume: takipçi motorunda halt HÂLÂ AKTİF "
                "(dosya silinemedi?) — gömülü halkada AlgoPro girişleri kapalı "
                "kalır. state/ dizinini ve disk iznini kontrol edin."
            )
        # ok=False: dosya silinemediyse (OSError) halt file-derived olarak
        # AKTİF kalır (bkz. risk_event_resume) — yanıt bunu "başarılı resume"
        # gibi göstermemeli (I).
        return {
            # `ok` ARTIK iki motoru da kapsar: biri halt'ta kaldıysa "resume
            # başarılı" demek operatörü yanıltır.
            "ok": not snap.get("active")
            and not any(bool(x.get("active")) for x in extra_resumes),
            "action": action,
            "halted_until": snap.get("until_ts"),
            "reason": snap.get("reason"),
            "flattened": [],
            "errors": [],
        }

    if action == "halt":
        # Takipçi ÖNCE durdurulur: scalper'ınki başarısız olsa bile
        # (imkânsıza yakın) takipçi yeni pozisyon açmayı sürdürmesin.
        extra_halts = [
            await extra_engine.risk_event_halt(
                reason=reason, source=source, ttl_minutes=ttl_minutes
            )
            for extra_engine in engines[1:]
        ]
        snap = await engine.risk_event_halt(
            reason=reason, source=source, ttl_minutes=ttl_minutes
        )
        if extra_halts and not all(
            bool(s.get("active")) for s in extra_halts
        ):  # pragma: no cover - savunmacı
            app_logger.bind(trade=True).critical(
                "🚨 /risk-event halt: takipçi motorunda halt AKTİF DEĞİL — "
                "gömülü halkada yeni AlgoPro girişleri sürebilir!"
            )
        # ok=snapshot.active: RAM latch sayesinde persist başarısız olsa
        # bile halt gerçekten etkilidir (D) — ok=False YALNIZ latch de
        # kurulamamışsa (ör. TTL<=0 gibi imkansız bir durum) döner.
        # persisted=False ile ok=True birlikte görülebilir: halt etkili
        # ama restart'ta kaybolur — çağıran `persisted` alanını kontrol
        # etmeli.
        return {
            "ok": bool(snap.get("active")),
            "action": action,
            "halted_until": snap.get("until_ts"),
            "reason": snap.get("reason"),
            "flattened": [],
            "errors": [],
            "persisted": bool(snap.get("persisted", True)),
        }

    # action == "flatten"
    result = await engine.risk_event_flatten(
        reason=reason, source=source, ttl_minutes=ttl_minutes
    )
    flattened = list(result.get("flattened", []) or [])
    errors = list(result.get("errors", []) or [])
    # D20b: hesap ancak İKİ motorun pozisyonları da kapandığında FLAT'tir.
    # Takipçininki sonra çalışır; scalper'ın yetim taraması onun
    # pozisyonlarını zaten "yetim" saymaz (sahiplik kaydı).
    for extra_engine in engines[1:]:
        try:
            extra = await extra_engine.risk_event_flatten(
                reason=reason, source=source, ttl_minutes=ttl_minutes
            )
        except Exception as exc:  # pragma: no cover - savunmacı
            errors.append(f"follower: {type(exc).__name__}: {exc}")
            continue
        flattened.extend(extra.get("flattened", []) or [])
        errors.extend(extra.get("errors", []) or [])
    result = {**result, "flattened": flattened, "errors": errors}
    halt_snap = result.get("halt") or {}
    # ok=False: bir veya daha fazla sembol borsa üzerinde doğrulanamadıysa
    # (fail-closed — SL/TP iptal edilmedi, izlemede kaldı) yanıt "flat"
    # OKUNAMAZ (I).
    return {
        "ok": not result.get("errors"),
        "action": action,
        "halted_until": halt_snap.get("until_ts"),
        "reason": halt_snap.get("reason"),
        "flattened": result.get("flattened", []),
        "errors": result.get("errors", []),
        "persisted": bool(halt_snap.get("persisted", True)),
    }


# ---------------------------------------------------------------------------
# AlgoPro takipçi halkası (D20) — `BOT_MODE=follower`
# ---------------------------------------------------------------------------
# Ana bot (scalper halkası) AlgoPro kaynaklı TV olaylarını buraya İLETİR;
# TV alarm URL'leri ve secret'ları DEĞİŞMEZ. Kanal TV webhook'undan ve
# risk-olayı kanalından AYRI bir secret ister (`FOLLOWER_FORWARD_SECRET`,
# boş = 503 ile kapalı — aynı fail-closed desen).

_FOLLOWER_EVENT_MAX_BODY_BYTES = 4096
_FOLLOWER_SECRET_HEADER = "X-Follower-Secret"
_FOLLOWER_BODY_SECRET_RE = re.compile(r"(?:^|\s)secret=([^\s]+)")

_EMPTY_FOLLOWER_STATUS = {
    "mode": "follower",
    "running": False,
    "health": {"healthy": False, "running": False, "reason": "engine_not_created"},
    "entries_ready": False,
    "positions": [],
    "events": [],
    # D27/A4: motor yokken de ŞEKİL aynı olmalı (pano "alan yok" ile "hiç
    # olmadı"yı karıştırmasın). D27 incelemesi (D8): ortak alan kümesi
    # scalper halkasıyla AYNIDIR (`tp1_missing`, `tp_wrong_side`,
    # `partial_fill_split`, `window`).
    "order_health": {
        "tp1_missing": 0,
        "tp_wrong_side": 0,
        "partial_fill_split": 0,
        "window": "process_start",
    },
}


@app.post("/follower/event")
async def follower_event(request: Request):
    """AlgoPro olay köprüsü: giriş/çıkış/TP/SL olaylarını takipçi motoruna ver.

    Gövde AlgoPro'nun KENDİ alert mesajıdır (``🔴 SELL | BINANCE:BTCUSDT |
    TF: 1 | Price: … | SL: … | TP1: … | TP2: … | TP3: …``) ya da açık
    ``kind=…`` şablonudur; ayrıştırma `src/strategies/follower/parser.py`'de.

    403 = secret yanlış · 422 = gövde çözülemedi/çok büyük · 503 = kanal
    kapalı (secret yok) ya da takipçi motoru hazır değil.
    """
    configured = (settings.follower_forward_secret or "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Takipçi kanalı devre dışı — .env'e FOLLOWER_FORWARD_SECRET ekleyin",
        )

    # Olay YAŞI buradan ölçülür (D20a bulgu 6): motor, global `_entry_lock`
    # kuyruğunda beklemiş bayat bir sinyalle giriş AÇMAMALIDIR.
    received_monotonic = time.monotonic()

    raw_bytes = await request.body()
    if len(raw_bytes) > _FOLLOWER_EVENT_MAX_BODY_BYTES:
        raise HTTPException(status_code=422, detail="Gövde çok büyük (>4KB)")
    raw = raw_bytes.decode("utf-8", errors="replace").strip()

    # Secret YALNIZ başlıkta ya da gövdede taşınır. `?secret=` BİLİNÇLİ olarak
    # DESTEKLENMEZ: uvicorn erişim logu (logs/supervisor.log) query string'i
    # düz metin yazar ve rotasyonla yedeklere yayılır (CLAUDE.md kural 5).
    # Köprü zaten `X-Follower-Secret` başlığını kullanır; elle test için
    # `-H 'X-Follower-Secret: …'` ya da gövdede `secret=…`.
    provided = str(request.headers.get(_FOLLOWER_SECRET_HEADER) or "")
    if not provided:
        match = _FOLLOWER_BODY_SECRET_RE.search(raw)
        provided = match.group(1) if match else ""
    if not _constant_time_equals(provided, configured):
        raise HTTPException(status_code=403, detail="Geçersiz takipçi secret")

    if not raw:
        raise HTTPException(status_code=422, detail="Boş gövde")

    from src.strategies.follower.parser import parse_follower_event
    from src.strategies.follower.types import FollowerParseError

    try:
        event = parse_follower_event(raw)
    except FollowerParseError as exc:
        app_logger.warning(f"⚠️ Takipçi olayı ayrıştırılamadı: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))

    if not follower_engine:
        raise HTTPException(status_code=503, detail="Takipçi motoru hazır değil")

    result = await follower_engine.handle_event(
        event, received_monotonic=received_monotonic
    )
    return {
        "ok": True,
        "kind": event.kind,
        "symbol": event.symbol,
        "direction": event.direction.value if event.direction else None,
        **result,
    }


@app.get("/follower/status")
async def follower_status():
    """Takipçi motorunun anlık durumu (pozisyonlar, boyutlama, son olaylar).

    MOD İZOLASYONU: scalper halkasında (`BOT_MODE=scalper`) bu uç nokta
    **404** döner. Eskiden boş bir "takipçi durumu" gövdesi dönüyordu ve
    yanlış halkaya bakan bir operatör "takipçi çalışmıyor / pozisyon yok"
    sonucunu çıkarabiliyordu — oysa takipçi BAŞKA bir süreçte (:9093)
    çalışıyor olabilir. Boş gövde artık YALNIZ takipçi modunda (motor henüz
    kurulmamışken) döner.
    """
    # D20b: gömülü modda (FOLLOWER_EMBEDDED=true) bu süreçte GERÇEKTEN bir
    # takipçi motoru vardır; 404 dönmek operatörü yanıltırdı.
    if not settings.follower_active:
        raise HTTPException(
            status_code=404,
            detail=(
                "Bu süreçte AlgoPro takipçisi YOK (BOT_MODE="
                f"{settings.bot_mode}, FOLLOWER_EMBEDDED=false). Takipçi "
                "durumu kendi sürecindedir (varsayılan :9093/follower/status) "
                "ya da FOLLOWER_EMBEDDED=true ile bu süreçte açılır."
            ),
        )
    if not follower_engine:
        return dict(_EMPTY_FOLLOWER_STATUS)
    return follower_engine.snapshot()


@app.get("/follower/forwarder")
async def follower_forwarder_stats():
    """Köprü sayaçları — ANA BOTTA (scalper halkası) okunur.

    "Sessiz kalmaz" ilkesinin sayaç tarafı: kaç gövde iletildi, kaçı AlgoPro
    biçiminde olmadığı için ATLANDI, kaçı taşıma hatası aldı. Secret İÇERMEZ
    (`forwarder_stats` yalnız gövdenin ilk 80 karakterini teşhis için taşır).
    """
    from src.services.follower_forwarder import forwarder_stats

    return forwarder_stats()


@app.post("/signal", dependencies=[Depends(require_api_key)])
async def manual_signal(
    signal_data: SignalRequest,
    db: AsyncSession = Depends(get_db),
):
    """Manuel sinyal gönder — GERÇEK EMİR AÇAR, API anahtarı zorunludur."""
    if bool(getattr(settings, "scalper_shadow_mode", False)):
        raise HTTPException(
            status_code=503,
            detail="Gölge modunda manuel emir yolu devre dışı",
        )
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

# ⚠️ SÖZLEŞME (bütünleşme incelemesi, 2026-08-23): bu sözlüğün ANAHTAR
# KÜMESİ `ScalperEngine.snapshot()` ile BİREBİR aynı olmalıdır
# (`tests/test_market_data_source.py::TestStatusPayloadShape`). Dashboard
# "alan yok"u "değer yok" ile karıştırmamalı: motor kurulmadan önce
# `market_data_guard`/`risk_event`/`tv_events`/`entry_rejects` alanlarının
# HİÇ olmaması, panelde sessiz bir "bu kanal yok" anlamına geliyordu.
# Dinamik olanlar (`market_data_guard`, `tv_events`, `symbol_reservations`)
# istek anında `/scalper/status` içinde TAZELENİR.
_EMPTY_SCALPER_STATUS = {
    "enabled": False,
    "running": False,
    "shadow_mode": settings.scalper_shadow_mode,
    # D17: motor kurulmamışken bile "kline verisi nereden gelecek" görünür
    # olsun (ayardan türetilir; motor varken engine.snapshot() fetcher'ın
    # GERÇEK base_url'ini raporlar).
    "market_data_base_url": settings.market_data_base_url,
    "trading_base_url": settings.binance_base_url,
    "kline_source": settings.kline_source,
    # Ban/ağırlık durumu — istek anında tazelenir (aşağıya bak).
    "market_data_guard": {},
    "scan_interval": settings.scalper_scan_interval_seconds,
    "safety_interval": settings.scalper_safety_interval_seconds,
    "health": {"healthy": False, "running": False, "reason": "engine_not_created"},
    "universe": [],
    "regimes": {},
    "structure": {},
    # D15 lider piyasa kapısı — engine yokken de aynı ŞEKİLLİ sözlük dönsün
    # (dashboard alan yokluğunu "kapı yok" ile karıştırmasın).
    "market_gate": {
        "enabled": settings.scalper_market_gate,
        # Kapı fail-open'dır: "enabled" KORUYOR demek değildir. Motor yokken
        # hiçbir lider verisi çekilmemiştir → gate_effective=False.
        "gate_effective": False,
        "leader": (settings.scalper_market_gate_symbol or "BTCUSDT").strip().upper(),
        "leader_ok": None,
        "leader_source_host": None,
        "thresholds": {
            "day_pct": settings.scalper_market_gate_day_pct,
            "run_pct": settings.scalper_market_gate_run_pct,
            "run_days": settings.scalper_market_gate_run_days,
        },
        "stale": True,
        # D22: bayatlığın nedeni — motor yokken tarama hiç dönmemiştir.
        "stale_reason": "entries_blocked",
        "snapshot_age_sec": None,
        "day_drift_pct": None,
        "run_drift_pct": None,
        "day_open_source": None,
        "last_ok_at": None,
        "last_error": None,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "failures_total": 0,
        "last_reason": None,
        "last_block_at": None,
        "rejects": {},
    },
    "daily_pnl": 0.0,
    "daily_pnl_source": "unavailable",
    "risk_ready": False,
    "risk_equity_usdt": None,
    "risk_equity_source": "unavailable",
    "daily_loss_threshold_usdt": None,
    "daily_limit_pct": settings.scalper_daily_loss_limit_pct,
    "kill_switch_active": False,
    # D22: gövdenin KURULDUĞU an (ISO). Önbellekten servis edilen bir yanıtta
    # bile sabit kalır — pano "son güncelleme"yi bundan yazar ve bayat tablo
    # taze görünmez. İstek anında tazelenir.
    "as_of": None,
    # D22: motor yokken hiçbir giriş dönmez — borsa hazırlığı doğrulanmamıştır.
    "entries_blocked_by": "exchange_readiness",
    # D22: ağırlık telemetrisi süreç-genelidir (istek anında tazelenir).
    "rest_weight": {},
    # D21/D22: adli kayıt kuyruğu (istek anında tazelenir).
    "forensics_queue": {},
    # D27/B: karşı-olgu defteri sayaçları. Motor yokken de ŞEKİL aynı olmalı
    # — defter SÜREÇ-İÇİDİR ve motor kurulmadan hiçbir niyet kaydedilmez.
    # ANAHTAR KÜMESİ `counterfactual_store.counters_snapshot()` ile BİREBİR
    # aynıdır: eksik bir alt alan, panoda "ölçüm yok" ile "alan yok"u
    # karıştırırdı. Bekçi testi:
    # tests/test_counterfactual_store.py::TestApiSurface.
    #
    # ⚠️ D27 incelemesi-2 (bulgu 6): DEĞERLER LİTERALDİR, `counters_snapshot()`
    # ÇAĞRILMAZ. Eskiden çağrılıyordu ve sözlük IMPORT ANINDA donuyordu: aynı
    # süreçte defter daha önce kullanıldıysa MOTORSUZ gövde `enabled=true,
    # registered=7` diyebiliyordu — yani kendi yorumunun tam tersi. Sıfırlar
    # burada "motor yok, hiçbir şey kaydedilmedi" demektir.
    "counterfactual": {
        "enabled": False,
        "window": "process_start",
        "horizons_h": list(counterfactual_store.DEFAULT_HORIZONS),
        "dedup_sec": counterfactual_store.DEFAULT_DEDUP_SEC,
        "max_pending": counterfactual_store.DEFAULT_MAX_PENDING,
        "pending": 0,
        "registered": 0,
        "dedup_hits": 0,
        "dropped_full": 0,
        "expired": 0,
        "resolved": 0,
        "measured": 0,
        "logged": 0,
        "log_dropped": 0,
        "candle_buffer_symbols": 0,
        "candle_buffer_bars": 0,
    },
    # D23: AI karar katmanı (gölge). Motor yokken de ŞEKİL aynı olmalı —
    # pano "alan yok" ile "katman kapalı"yı karıştırmasın.
    "ai_gate": {
        "mode": str(getattr(settings, "scalper_ai_gate_mode", "off") or "off"),
        "effective_mode": str(
            getattr(settings, "scalper_ai_gate_mode", "off") or "off"
        ),
        "enabled": str(
            getattr(settings, "scalper_ai_gate_mode", "off") or "off"
        ) != "off",
    },
    "entry_halted": False,
    "entry_halt_reason": None,
    "entry_halted_at": None,
    # Risk-olayı halt'ı (D10) dosyadan okunur ve motordan BAĞIMSIZDIR; motor
    # yokken bile alan görünmeli (fail-closed bir kapıdır, yokluğu "kapalı"
    # ile karıştırılmamalı). Değer motor kurulmadan okunamaz: "bilinmiyor".
    "risk_event": {
        "active": None,
        "reason": None,
        "source": None,
        "until_ts": None,
        "open_positions": 0,
    },
    # Olay defteri de motordan bağımsızdır — istek anında tazelenir.
    "tv_events": {},
    "signals_today": 0,
    "last_scan_at": None,
    # D17: piyasa verisi kesintisiyle KESİLEN tarama turu "başarılı" sayılmaz;
    # durum burada görünür ("ok" | "degraded:market_data").
    "scan_status": "ok",
    "scan_degraded_reason": None,
    "scan_degraded_at": None,
    "scan_degraded_count": 0,
    "trailing_skips": {},
    "tracked": [],
    "pending_entries": [],
    "cooldowns": [],
    "entry_rejects": {},
    # D27/A4: TP1/TP2 emri konulamama sayaçları. Motor yokken de ŞEKİL aynı
    # olmalı — pano "alan yok" ile "hiç olmadı"yı karıştırmasın.
    # D27 incelemesi (D8): `tp1_missing` + `tp_wrong_side` +
    # `partial_fill_split` + `window` İKİ HALKADA DA vardır (ortak küme);
    # yalnız bu sayede pano tek bir uyarı satırıyla ikisini de gösterebilir.
    "order_health": {
        "tp1_missing": 0,
        "tp1_unidentified": 0,
        "tp2_missing": 0,
        "tp2_unidentified": 0,
        "tp_wrong_side": 0,
        "partial_fill_split": 0,
        "last_symbol": None,
        "last_at": None,
        "window": "process_start",
    },
    "stop_mode": str(getattr(settings, "scalper_stop_mode", "structural")),
    "sizing": {},
    "sizing_equity_usdt": None,
    "virtual_capital_enabled": False,
    "virtual_capital_base_usdt": 0.0,
    "virtual_capital_current_usdt": None,
    "virtual_capital_start_trade_id": 0,
    "symbol_reservations": {},
    # D20b/Y8: gömülü modda hesabın ham günlük income'ı — YALNIZ BİLGİ
    # (kesici defterden beslenir). Motor yokken de anahtar bulunmalı.
    "daily_income_account": None,
}


@app.get("/scalper/status")
async def scalper_status(request: Request = None):
    """Scalper motorunun anlık durumu (tarama evreni, rejimler, izlenen pozisyonlar).

    D22: motorlu yol 5 sn önbelleklenir; `as_of` gövdenin KURULDUĞU andır ve
    pano "son güncelleme"yi ondan yazar. Motor YOKKEN önbellek kullanılmaz —
    o yol REST yapmaz ve olay defteri her çağrıda taze olmalıdır.
    """
    cache_key = _status_cache_key(request)
    if not scalper_engine:
        empty = dict(_EMPTY_SCALPER_STATUS)
        # Motordan BAĞIMSIZ üç alan: motor ayakta değilken de GERÇEK değeri
        # görünmeli. Anahtar kümesi `_EMPTY_SCALPER_STATUS`'ta zaten var —
        # burada yalnız TAZELENİR (bkz. sözlüğün üstündeki sözleşme notu).
        empty["tv_events"] = tv_events.snapshot()
        empty["symbol_reservations"] = symbol_reservations.snapshot()
        try:
            empty["market_data_guard"] = MarketDataGuard.snapshot(
                settings.market_data_base_url
            )
        except Exception as e:  # teşhis alanı asla status'u düşürmemeli
            empty["market_data_guard"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            from src.trading.binance_client_improved import ImprovedBinanceClient
            from src.strategies.scalper import forensics_log

            empty["rest_weight"] = ImprovedBinanceClient.rest_weight_snapshot()
            empty["forensics_queue"] = dict(forensics_log.queue_snapshot())
        except Exception as e:  # teşhis alanı asla status'u düşürmemeli
            empty["rest_weight"] = {"error": f"{type(e).__name__}: {e}"}
        empty["as_of"] = _utcnow_iso()
        return empty

    # D22: motorlu yol 5 sn önbelleklenir (pano 5 sn'de bir yokluyor).
    # `snapshot()` REST yapmaz ama tüm izlenen pozisyonlar + kapı + adli
    # kayıt sözlüklerini yeniden kurar; motor kimliği değişirse önbellek
    # düşer (restart/lifespan yeniden kurulumu).
    cached = _cached_status(
        _scalper_status_cache, cache_key, engine=scalper_engine
    )
    if cached is not None:
        return cached

    return _store_status(
        _scalper_status_cache,
        cache_key,
        scalper_engine.snapshot(),
        engine=scalper_engine,
    )


@app.get("/scalper/stats")
async def scalper_stats(
    db: AsyncSession = Depends(get_db), strategy: Optional[str] = None
):
    """Strateji bazlı ve toplam (combined) kapanmış scalp işlem istatistikleri.

    D20b: `combined` VARSAYILAN OLARAK gömülü takipçinin (`strategy="AP"`)
    satırlarını DIŞLAR — pano "TOPLAM" kartı, veri-kalitesi bandı ve lider
    rozeti iki farklı defteri tek sayıda karıştırmamalıdır. `strategies`
    sözlüğü her defteri AYRI anahtarla göstermeye devam eder (AP dahil).
    `?strategy=AP` ile yalnız takipçinin defteri toplanır. Ayrı halkada DB'de
    AP satırı olmadığı için varsayılan davranış bugünküyle birebir aynıdır.
    """
    wanted = (strategy or "").strip().upper() or None
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

    combined_stmt = select(ScalpTradeModel).where(ScalpTradeModel.status == "CLOSED")
    if wanted:
        combined_stmt = combined_stmt.where(ScalpTradeModel.strategy == wanted)
    else:
        combined_stmt = combined_stmt.where(
            ScalpTradeModel.strategy != FOLLOWER_LEDGER_STRATEGY
        )
    result = await db.execute(combined_stmt)
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
        # Hangi defteri kapsıyor? Pano bunu kart başlığında gösterir.
        "scope": wanted or f"!{FOLLOWER_LEDGER_STRATEGY}",
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
async def scalper_trades(
    limit: int = 50, include_shadow: bool = False, db: AsyncSession = Depends(get_db)
):
    """Kapanmış scalp işlemleri, en yeni önce.

    Varsayılan: yalnız gerçek (CLOSED) işlemler — gölge modu (D14) satırları
    ("SHADOW") hiç emir göndermediği için istatistik/PnL anlamı taşımaz ve
    dışarıda bırakılır. ?include_shadow=1 ile SHADOW satırları da (opened_at'e
    göre sıralanarak, closed_at'leri olmadığından) listeye eklenir.
    """
    statuses = ["CLOSED", "SHADOW"] if include_shadow else ["CLOSED"]
    order_col = func.coalesce(ScalpTradeModel.closed_at, ScalpTradeModel.opened_at)
    result = await db.execute(
        select(ScalpTradeModel)
        .where(ScalpTradeModel.status.in_(statuses))
        .order_by(order_col.desc())
        .limit(limit)
    )
    return [
        {
            "id": t.id,
            "strategy": t.strategy,
            "symbol": t.symbol,
            "direction": t.direction,
            "status": t.status,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "realized_pnl": t.realized_pnl,
            "roi_pct": t.roi_pct,
            "exit_reason": t.exit_reason,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            "signal_reason": t.signal_reason,
            "pnl_source": None if t.status == "SHADOW" else ScalpTracker._pnl_source(t.notes),
            # D21: panonun "adli kart" düğmesini göstermesi için yeterli olan
            # tek bit. Kaydın kendisi /scalper/trades/{id}/forensics'tedir —
            # bu liste ucu 50 işlemlik JSON'u şişirmemelidir.
            "has_forensics": bool(t.forensics),
            "verdict": list(
                (ScalpTracker.parse_forensics(t.forensics) or {}).get("verdict") or []
            ),
        }
        for t in result.scalars().all()
    ]


#: `?since=`/`?until=` için azami geriye bakış. Pencereyi sınırsız bırakmak
#: SQL taramasını ve `summarize` maliyetini pano yoklamasıyla çarpar; ayrıca
#: `9999999999d` gibi bir değer `timedelta`'yı taşırıp 500 üretir (D21-R3,
#: bulgu 5). Sınır aşılırsa 400 döner — sessizce kırpmak raporu YANLIŞ okutur.
FORENSICS_MAX_WINDOW_DAYS = 365
#: `?since=` verilmediğinde adli özet penceresi.
FORENSICS_DEFAULT_SINCE = "7d"


def _parse_since(
    value: Optional[str], *, max_days: int = FORENSICS_MAX_WINDOW_DAYS
) -> Optional[datetime]:
    """`?since=` metnini naive UTC datetime'a çevirir.

    Kabul edilenler: `7d` / `36h` (göreli), `YYYY-MM-DD`,
    `YYYY-MM-DD HH:MM[:SS]`, `YYYY-MM-DDTHH:MM[:SS]`. Çözülemeyen ya da
    `max_days`'i aşan değer 400 verir — sessizce "tüm zamanlar"a düşmek ya da
    pencereyi kırpmak raporu YANLIŞ okutur; 500 ise istemciye "sunucu bozuk"
    der, oysa hata girdidedir.
    """
    text = (value or "").strip()
    if not text:
        return None
    now = datetime.utcnow()
    limit = now - timedelta(days=max(1, int(max_days)))
    if re.fullmatch(r"\d+[dh]", text.lower()):
        try:
            amount = int(text[:-1])
            hours = amount * 24 if text[-1].lower() == "d" else amount
            parsed = now - timedelta(hours=hours)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=400,
                detail=f"since aralık dışı (en fazla {max_days} gün): {value!r}",
            )
    else:
        parsed = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise HTTPException(
                status_code=400, detail=f"since ayrıştırılamadı: {value!r}"
            )
    if parsed < limit:
        raise HTTPException(
            status_code=400,
            detail=f"since aralık dışı (en fazla {max_days} gün): {value!r}",
        )
    return parsed


@app.get("/scalper/trades/{trade_id}/forensics")
async def scalper_trade_forensics(trade_id: int):
    """Tek bir işlemin adli kaydı (D21): neden girildi / nasıl çıkıldı.

    Eski (kayıt öncesi) işlemlerde `has_forensics=false` döner — bu bir hata
    değil, "o işlem ölçülmedi" demektir.
    """
    row = await ScalpTracker().forensics_for(trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"İşlem bulunamadı: #{trade_id}")
    return row


@app.get("/scalper/forensics/recent")
async def scalper_forensics_recent(limit: int = 50):
    """Son kapanmış işlemlerin adli kaydı (en yeni önce)."""
    return await ScalpTracker().recent_forensics(limit)


@app.get("/scalper/forensics/summary")
async def scalper_forensics_summary(
    since: Optional[str] = None,
    until: Optional[str] = None,
    strategy: Optional[str] = None,
):
    """Etiket × sonuç tablosu — "neler etkiliyor" sorusunun cevabı.

    Bir işlem birden çok etiket taşıyabilir; satır toplamı işlem sayısını
    AŞABİLİR. `_etiketsiz_` satırı kıyas tabanıdır.

    `since` verilmezse varsayılan pencere son `FORENSICS_DEFAULT_SINCE`'tır:
    "tüm zamanlar" hem DB'yi hem de okuyanı yanıltır (kayıt D21 ile başladı).
    Üst sınır `FORENSICS_MAX_WINDOW_DAYS`; aşan değer 400 döner.

    D20b: gömülü takipçinin (`strategy="AP"`) satırları VARSAYILAN OLARAK
    dışlanır — takipçide strateji göstergesi/rejim/lider kapısı YOKTUR ve
    onun etiketleri scalper'ın "neler etkiliyor" tablosunu kirletirdi.
    `?strategy=AP` ile takipçinin kendi tablosu çekilir.

    `intents` (D24/madde 7): gerçekleşMEyen niyetlerin (kapı reddi, kapasite,
    emir hatası) sayaçları. **SÜREÇ BAŞLANGICINDAN BERİDİR ve restart'ta
    SIFIRLANIR** (`window="process_start"`) — `since`/`until` bu bloğa
    UYGULANMAZ. Kalıcı tarihçe `logs/trades.jsonl` (`event="intent"`).
    Sayaç anlık görüntüsü O(1)'dir: bu uç panodan düzenli yoklanır, ek DB ya
    da disk işi YAPILMAZ (bkz. "dashboard polling açlığı" dersi).
    """
    wanted = (strategy or "").strip().upper() or None
    summary = await ScalpTracker().forensics_summary(
        since=_parse_since(since or FORENSICS_DEFAULT_SINCE),
        until=_parse_since(until),
        strategies=(wanted,) if wanted else None,
        exclude_strategies=None if wanted else (FOLLOWER_LEDGER_STRATEGY,),
    )
    try:
        intents = scalp_intent.counters_snapshot()
    except Exception:  # pragma: no cover - sayaç arızası ucu düşürmemeli
        intents = None
    summary["intents"] = intents
    return summary


@app.get("/scalper/counterfactual")
async def scalper_counterfactual(
    since: Optional[str] = None,
    reason: Optional[str] = None,
    limit: int = 50,
):
    """D27/B — ret gerekçesi × KARŞI-OLGU sonucu: "girilseydi ne olurdu".

    Reddedilen her giriş niyeti için, o niyet anından SONRAKİ mumlarla
    "mevcut TP/SL kurallarıyla girilseydi ne olurdu" simüle edilir ve
    sonuç `logs/trades.jsonl`'e (`event="counterfactual"`) yazılır. Bu uç
    o kalıcı satırları okur ve gerekçe bazında tablo döner (n, ölçülen,
    tp1/stop/açık dağılımı, ortalama ROI, PF, %95 güven aralığı).

    NEDEN VAR: 2026-08-24 kök-neden analizinin BÜTÜN filtre rakamları
    "üst sınır tahmini"ydi — engellenen bir işlemin yerine kapasite ve
    kayıp-cooldown serbestliğiyle başka bir işlem açılır ve bu, kapalı
    işlem defterinden çıkarılamaz. Bu tablo o tahminleri kanıta çevirir.

    DÜRÜSTLÜK:
    * `measured=False` satırlar ("mum yoktu", "plan kurulamadı") ortalama/PF
      hesabına GİRMEZ ve `no_data` olarak ayrı sayılır — uydurma sayı YOK.
    * Simülasyon yalnız **TP1 ya da ilk stop**u modeller. TP2, chandelier
      trailing, break-even çekme, 8 saatlik reaper (D4), komisyon ve kayma
      MODELLENMEZ; aynı mumda ikisi de vurursa STOP kazanır (karamsar).
    * `collapsed`, dedup penceresi içinde tek satıra indirgenmiş özdeş
      retlerin toplam ağırlığıdır (`dup_count`).

    **Pano bu ucu ÇAĞIRMAZ**; pano özeti `/scalper/status → counterfactual`
    bloğundadır. Burada GERÇEK disk okuması vardır — elle/rapor yolundan
    çağrılır ve okuma AYRI BİR İŞ PARÇACIĞINDA yapılır (Y4).

    `truncated=True` dönerse satır tavanı (`forensics_log.READ_MAX_LINES`)
    dolmuştur ve DAHA ESKİ veri okunmamıştır — tablo o pencerenin TAMAMI
    değildir. Pencereyi daraltın (`?since=`).
    """
    from src.strategies.scalper import forensics_log

    # D27 incelemesi (Y5): `since` VARSAYILANI VARDIR. Filtresiz çağrı 30
    # günün tamamını okurdu ve bu, en pahalı uçta en kötü hâli tetikleyen
    # varsayılandı; kardeş uç (`/scalper/forensics/summary`) zaten
    # `FORENSICS_DEFAULT_SINCE` kullanıyor.
    since_iso: Optional[str] = None
    parsed = _parse_since(since or FORENSICS_DEFAULT_SINCE)
    if parsed is not None:
        since_iso = parsed.replace(tzinfo=timezone.utc).isoformat()

    def _read_and_summarize() -> Dict[str, Any]:
        """Disk okuması + özet — AYRI İŞ PARÇACIĞINDA (bkz. aşağıdaki not)."""
        result = forensics_log.read_events_detailed(
            "counterfactual", since_iso=since_iso
        )
        rows = result.rows
        if wanted:
            rows = [r for r in rows if str(r.get("reason") or "") == wanted]
        return {
            "rows": rows,
            "truncated": result.truncated,
            "scanned": result.scanned,
            "summary": counterfactual_store.summary(rows),
        }

    wanted = (reason or "").strip().lower() or None
    try:
        # D27 incelemesi (Y4): `read_events` + `summarize` SENKRONDUR ve
        # `async def` gövdesinde OLAY DÖNGÜSÜNÜ BLOKLAR (ölçüldü: 200k
        # satırda ≈1.43 sn blokaj, +225 MB RSS). Blokaj süresince tarama
        # turu, safety turu ve TÜM HTTP donar — 2026-08-18 "pano açlığı"
        # dersinin okuma tarafındaki eşleniği. `to_thread` bunu ayırır.
        payload = await asyncio.to_thread(_read_and_summarize)
    except Exception as e:  # pragma: no cover - okuma hatası uç düşürmemeli
        raise HTTPException(status_code=500, detail=f"JSONL okunamadı: {e}")

    try:
        n = max(0, min(int(limit), 500))
    except (TypeError, ValueError):
        n = 50
    rows = payload["rows"]
    return {
        "since": since_iso,
        "reason": wanted,
        # `truncated=True`: satır tavanı doldu, DAHA ESKİ veri OKUNMADI.
        # "kayıt yok" ile "hepsini okuyamadık" aynı şey değildir (K1).
        "truncated": payload["truncated"],
        "scanned_lines": payload["scanned"],
        "summary": payload["summary"],
        "counters": counterfactual_store.counters_snapshot(),
        # En yeni önce; `limit` ile sınırlı ham satırlar (teşhis için).
        "rows": list(reversed(rows))[:n],
    }


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
