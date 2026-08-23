"""FollowerEngine — AlgoPro takipçi halkasının orkestrasyonu (D20).

Scalper motorundan FARKI: tarama YOK, strateji YOK, sağlama (confluence) YOK.
Tek giriş kaynağı ``POST /follower/event``'e düşen AlgoPro alarmlarıdır.

İki arka plan döngüsü:
  * safety (varsayılan 2 sn): ``exits.step()`` (TP1→BE, kapanış defteri) +
    günlük zarar kesici.
  * exchange readiness (30 sn): imzalı hesap erişimi + restart kurtarması.

KAPILAR (hepsi ``_entries_ready()``de toplanır — scalper'daki tek-kapı
ilkesiyle aynı): imzalı borsa erişimi tazeliği, restart kurtarması,
günlük risk kapısı, ``UnprotectedPositionError`` latch'i, kill switch ve
risk-olayı halt'ı (``POST /risk-event``, D10 semantiği).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.follower.brackets import LeverageBracketCache
from src.strategies.follower.exits import FollowerExitManager
from src.strategies.follower.executor import FOLLOWER_STRATEGY, FollowerExecutor
from src.strategies.follower.levels import (
    calibration_record,
    resolve_levels,
    signal_drift_limit_pct,
    stop_on_correct_side,
)
from src.strategies.follower.risk_halt import RiskEventHaltStore
from src.strategies.follower.types import (
    KIND_ENTRY,
    KIND_EXIT,
    KIND_SL,
    KIND_TP3,
    FollowerEvent,
    FollowerRejected,
)
from src.strategies.scalper.data import KlineFetcher
from src.strategies.scalper.indicators import atr as compute_atr
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import Direction
from src.trading.binance_client_improved import ImprovedBinanceClient
from src.trading.position_manager import PositionManager, UnprotectedPositionError
from src.trading.symbol_reservations import (
    FOLLOWER_RESERVATION_OWNER,
    symbol_reservations,
)

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_timeframe(value: str) -> str:
    """TV ``{{interval}}`` ("1") ile insan yazımını ("1m") aynı kefeye koy."""
    text = str(value or "").strip().lower()
    return text[:-1] if text.endswith("m") and text[:-1].isdigit() else text


def _fmt(value: Any, spec: str = ".4f") -> str:
    """None-güvenli sayı biçimleyici (log satırları bir girişi düşüremez)."""
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return "?"


class FollowerEngine:
    """AlgoPro olaylarını korumalı pozisyonlara çeviren motor."""

    _EXCHANGE_PROBE_INTERVAL = 30.0
    # Reduce-only kapanışın borsada doğrulanması için sınırlı bekleme
    # merdiveni (toplam ~3.9 sn). Testler bu tuple'ı (0.0,) yaparak gerçek
    # uyku olmadan çalışır — scalper'ın INCOME_RETRY_DELAYS deseniyle aynı.
    _CLOSE_VERIFY_DELAYS = (0.0, 0.3, 0.6, 1.0, 2.0)
    _INCOME_CACHE_TTL = 120.0
    #: Sanal defter toplamının önbellek ömrü (scalper'ın DB toplamalarıyla
    #: aynı mertebe). `tracker.close_seq` değişince ANINDA geçersizleşir.
    _VIRTUAL_EQUITY_CACHE_TTL = 30.0
    _BALANCE_CACHE_TTL = 300.0
    _EVENT_HISTORY = 50
    #: `symbol_reservations` sahiplik etiketi (scalper'ınki "scalper").
    #: Gömülü modda (D20b) iki motor AYNI süreçte olduğu için sembol
    #: sahipliği tek bir süreç-içi kayıttan yürür.
    _RESERVATION_OWNER = FOLLOWER_RESERVATION_OWNER
    # Sanal defter telemetrisi SINIF düzeyinde varsayılanlıdır: motoru
    # `__init__` çalıştırmadan kuran test çiftleri de `snapshot()` alabilsin.
    _virtual_equity_usdt: Optional[float] = None
    _virtual_realized_pnl: float = 0.0
    _exchange_available_usdt: Optional[float] = None
    _virtual_equity_cache_seq: int = -1
    _virtual_equity_cached_at: float = 0.0
    #: Gömülü modda: hiçbir motorun izlemediği/rezerve etmediği CANLI
    #: pozisyonlar. Bunlar YETİM DEĞİLDİR (elle/Telegram açılmış olabilir) —
    #: yalnız görünürlük; entry-halt ya da flatten TETİKLEMEZ.
    _unknown_positions: List[str] = []
    #: Yetim denetimi bu turda GERÇEKTEN çalıştı mı? (borsa okuması patlarsa
    #: False kalır ve rezervasyon senkronu sahipliği bırakmaz.)
    _orphans_check_ok: bool = False
    #: Diğer motorların izlediği sembolleri veren callback (main.py enjekte
    #: eder; ayrı halkada None → D20a davranışı birebir).
    foreign_tracked_cb = None

    def __init__(self) -> None:
        self.cfg = settings
        self.logger = app_logger

        self.client = ImprovedBinanceClient()
        self.pm = PositionManager(self.client)
        self.tracker = ScalpTracker()
        self.brackets = LeverageBracketCache(self.client, self.cfg)
        self.executor = FollowerExecutor(
            self.client, self.pm, self.tracker, self.cfg, self.brackets
        )
        self.exits = FollowerExitManager(
            self.client,
            self.pm,
            self.tracker,
            self.cfg,
            exit_cooldown_cb=self.executor.start_cooldown,
        )
        # ATR YEDEK kuralı için 1m mumları (yalnız mesajda SL yoksa çekilir).
        # SCALPER_MARKET_DATA_BASE_URL tanımlıysa (ayrı bir çalışmada
        # eklenmektedir) o kullanılır; yoksa trading host'u.
        self.fetcher = KlineFetcher(
            base_url=str(getattr(self.cfg, "scalper_market_data_base_url", "") or "")
            or None
        )

        self.halt = RiskEventHaltStore(
            getattr(self.cfg, "risk_event_halt_path", None), logger=self.logger
        )

        self.running = False
        self._safety_task: Optional[asyncio.Task] = None
        self._exchange_task: Optional[asyncio.Task] = None
        self._entry_lock = asyncio.Lock()

        self._exchange_ready = False
        self._exchange_last_success_monotonic: Optional[float] = None
        self._exchange_last_success_at: Optional[str] = None
        self._exchange_last_error: Optional[str] = None
        self._recovery_ready = False

        self._entry_halted = False
        self._entry_halt_reason: Optional[str] = None
        self._entry_halted_at: Optional[str] = None
        halt_path = getattr(self.cfg, "follower_entry_halt_path", None)
        self._entry_halt_path: Optional[Path] = (
            Path(halt_path).expanduser() if halt_path else None
        )
        self._load_entry_halt()

        self._risk_ready = False
        self._kill_switch = False
        self._kill_switch_day: Optional[str] = None
        self._daily_pnl = 0.0
        self._daily_loss_threshold_usdt: Optional[float] = None
        self._risk_equity_usdt: Optional[float] = None
        self._income_cache: Tuple[Optional[float], float, Optional[str]] = (
            None,
            0.0,
            None,
        )
        self._income_cache_close_seq = -1
        self._balance_cache: Tuple[Optional[float], float] = (None, 0.0)
        # D20b sanal defter: son hesaplanan sanal sermaye ve AP net PnL'i
        # (telemetri; kaynak DAİMA DB'dir, bu alanlar yalnız gösterim için).
        self._virtual_equity_usdt: Optional[float] = None
        self._virtual_realized_pnl: float = 0.0
        self._exchange_available_usdt: Optional[float] = None
        self._virtual_equity_cache_seq = -1
        self._virtual_equity_cached_at = 0.0

        # Borsada AÇIK ama motorun İZLEMEDİĞİ pozisyonlar (bulgu 8).
        self._orphans: List[str] = []
        self._orphans_checked_at: Optional[str] = None
        self._unknown_positions: List[str] = []
        self._orphans_check_ok = False

        self._events: Deque[Dict[str, Any]] = deque(maxlen=self._EVENT_HISTORY)
        self._event_counters: Dict[str, int] = {}
        self._reject_counters: Dict[str, int] = {}
        self._last_event_at: Optional[str] = None
        self._safety_last_success_monotonic: Optional[float] = None
        self._safety_last_error: Optional[str] = None
        self._safety_consecutive_errors = 0

    # ------------------------------------------------------------------
    # Yaşam döngüsü
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self.logger.info("🤖 AlgoPro takipçi motoru başlatılıyor...")
        self.logger.info(
            f"🎯 Evren={sorted(self.symbol_allowlist())} tf={self.cfg.follower_timeframe} "
            f"marj=%{self.cfg.follower_margin_pct} kaldıraç="
            f"[{self.cfg.follower_lev_min}-{self.cfg.follower_lev_max}]x "
            f"SL başına marj=%{getattr(self.cfg, 'follower_sl_margin_pct', self.cfg.follower_sl_roi_target)} "
            f"azami pozisyon={self.cfg.follower_max_positions}"
        )
        if bool(getattr(self.cfg, "follower_embedded", False)):
            reserved = sorted(
                getattr(self.cfg, "follower_reserved_symbols", []) or []
            )
            self.logger.info(
                f"🧩 GÖMÜLÜ mod (D20b): scalper ile aynı süreç/hesap. "
                f"Sanal defter tabanı={self._virtual_capital_base():.2f} USDT, "
                f"günlük kesici=%{getattr(self.cfg, 'follower_daily_loss_limit_pct', 0)}, "
                f"takipçiye ayrılmış semboller="
                f"{', '.join(reserved) if reserved else '(yok — evren paylaşılıyor)'}"
            )
        if await self._probe_exchange():
            # Sembol sahipliği artık `_attempt_recovery` İÇİNDE alınır (hem
            # buradaki hem `_exchange_loop`'taki ertelenmiş yol kapsanır).
            await self._attempt_recovery()
            # Kurtarmadan HEMEN SONRA: DB'de satırı olmayan (ör. record_open
            # hatası) açık pozisyonlar ancak burada görünür (bulgu 8).
            await self._check_orphans()
            await self.brackets.warm(sorted(self.symbol_allowlist()))
            await self._update_kill_switch()

        self.running = True
        if not self._safety_task or self._safety_task.done():
            self._safety_task = asyncio.create_task(
                self._safety_loop(), name="follower-safety-loop"
            )
        if not self._exchange_task or self._exchange_task.done():
            self._exchange_task = asyncio.create_task(
                self._exchange_loop(), name="follower-exchange-loop"
            )
        self.logger.info("✅ Takipçi motoru hazır")

    async def stop(self) -> None:
        self.logger.info("🛑 Takipçi motoru durduruluyor...")
        self.running = False
        tasks = [t for t in (self._safety_task, self._exchange_task) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Süreç kapanırken sembol sahipliği bırakılır (kayıt süreç-içidir;
        # kalıcı bir şey sızdırmaz ama test/reload izolasyonunu korur).
        for symbol in list(self.exits.tracked_symbols()):
            self._release_symbol(symbol)
        for closer in (self.fetcher.close, self.client.close):
            try:
                await closer()
            except Exception as exc:
                self.logger.warning(f"⚠️ Takipçi kaynak temizleme hatası: {exc}")
        self.logger.info("✅ Takipçi motoru durduruldu")

    # ------------------------------------------------------------------
    # Kalıcı giriş kilidi (fail-closed)
    # ------------------------------------------------------------------

    def _load_entry_halt(self) -> None:
        path = self._entry_halt_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            reason = str((payload or {}).get("reason") or "bilinmiyor")
            halted_at = str((payload or {}).get("halted_at") or "")
        except Exception as exc:
            reason = f"halt dosyası okunamadı: {type(exc).__name__}: {exc}"
            halted_at = ""
        self._entry_halted = True
        self._entry_halt_reason = reason
        self._entry_halted_at = halted_at or _utcnow_iso()
        self.logger.critical(
            f"🚨 Takipçi giriş kilidi AKTİF (diskten yüklendi): {reason}. "
            f"Açmak için dosyayı incele ve yeniden adlandır: {path}",
            extra={"trade": True},
        )

    def _persist_entry_halt(self) -> None:
        path = self._entry_halt_path
        if path is None:
            return
        tmp_path: Optional[Path] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            payload = {
                "version": 1,
                "reason": self._entry_halt_reason,
                "halted_at": self._entry_halted_at,
            }
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception as exc:
            self.logger.critical(
                f"🚨 Takipçi giriş kilidi diske yazılamadı ({path}): {exc}. "
                f"Kilit RAM'de aktif, restart'ta KAYBOLUR.",
                extra={"trade": True},
            )
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _latch_entry_halt(self, error: Exception, *, source: str) -> None:
        if self._entry_halted:
            return
        self._entry_halted = True
        self._entry_halt_reason = f"{type(error).__name__}: {error}"
        self._entry_halted_at = _utcnow_iso()
        self.logger.critical(
            f"🚨 Takipçi YENİ GİRİŞLERİ DURDURULDU ({source}): {error}. "
            f"İzleme sürüyor; manuel kontrol + restart gerekli.",
            extra={"trade": True},
        )
        self._persist_entry_halt()

    # ------------------------------------------------------------------
    # Borsa hazırlığı / kurtarma
    # ------------------------------------------------------------------

    async def _probe_exchange(self) -> bool:
        try:
            await self.client.get_all_positions()
        except Exception as exc:
            self._exchange_ready = False
            self._exchange_last_error = f"{type(exc).__name__}: {exc}"
            self.logger.error(
                f"❌ Takipçi imzalı Binance erişimi başarısız; girişler kapalı: {exc}"
            )
            return False
        self._exchange_ready = True
        self._exchange_last_success_monotonic = time.monotonic()
        self._exchange_last_success_at = _utcnow_iso()
        self._exchange_last_error = None
        return True

    async def _attempt_recovery(self) -> bool:
        try:
            recovered = await self.exits.recover()
        except UnprotectedPositionError as exc:
            self._recovery_ready = False
            self._latch_entry_halt(exc, source="restart recovery")
            return False
        except Exception as exc:
            self._recovery_ready = False
            self.logger.error(
                f"❌ Takipçi restart kurtarması başarısız: {exc}", exc_info=True
            )
            return False
        self._recovery_ready = bool(recovered)
        # SEMBOL SAHİPLİĞİ BURADA alınır — start()'ta DEĞİL (düşmanca inceleme):
        # ilk borsa probu başarısız olursa kurtarma `_exchange_loop`'ta GEÇ
        # çalışır ve start()'taki döngü o yolu hiç görmezdi; kurtarılan
        # pozisyonlar süreç ömrü boyunca SAHİPSİZ kalırdı. Scalper aynı işi
        # `_attempt_recovery` içinde yapar — iki motor artık simetriktir.
        self._reserve_recovered_symbols()
        if not self._recovery_ready:
            self.logger.error(
                "⛔ Takipçi kurtarma güvenliği kanıtlanamadı; izleme sürüyor, "
                "yeni girişler kapalı"
            )
        return self._recovery_ready

    def _reserve_recovered_symbols(self) -> None:
        """Kurtarılan AP pozisyonlarını takipçi adına sahiplen.

        Çakışmada `break` DEĞİL `continue`: bir sembol başka motordaysa geri
        kalan pozisyonlar yine de sahiplenilmelidir (aksi halde tek çakışma
        tüm listeyi sahipsiz bırakırdı). Çakışma artık KALICI disk halt'ı
        yazmaz — defter filtresi (aynı düzeltme paketinde) iki motorun aynı
        satırı kurtarmasını zaten imkânsız kıldığı için buraya düşmek bir
        VERİ TUTARSIZLIĞI işaretidir: RAM'de girişleri kapat + CRITICAL logla,
        ama operatörün her deploy'da dosya silmesini gerektirme.
        """
        conflicts = []
        for symbol in sorted(self.exits.tracked_symbols()):
            if not self._reserve_symbol(symbol):
                conflicts.append(symbol)
        if not conflicts:
            return
        self._recovery_ready = False
        self.logger.critical(
            f"🚨 Takipçi kurtarmasında sembol sahipliği çakıştı: {conflicts} "
            f"(sahipler={ {s: symbol_reservations.owner(s) for s in conflicts} }). "
            f"Yeni girişler kapatıldı; açık pozisyonların çıkış takibi sürüyor. "
            f"KALICI kilit YAZILMADI — süreci yeniden başlatmak yeterlidir, ama "
            f"önce iki defterin (scalp_trades.strategy) tutarlılığını inceleyin.",
            extra={"trade": True},
        )

    # ------------------------------------------------------------------
    # Yetim pozisyon denetimi (düşmanca inceleme bulgu 8)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Sembol sahipliği (D20b — scalper ile AYNI süreçte)
    # ------------------------------------------------------------------

    def _embedded(self) -> bool:
        return bool(getattr(self.cfg, "follower_embedded", False))

    def _foreign_tracked_symbols(self) -> Set[str]:
        """Diğer motorların GERÇEKTEN izlediği semboller (gömülü mod).

        Rezervasyon bir NİYET işaretidir, "o motor bu pozisyonu yönetiyor"
        KANITI değildir: scalper entry-halt'a düştüğünde `_sync_scalper_
        reservations` ilk satırda döner ve rezervasyonlar DONAR. Donmuş bir
        rezervasyon, o sembolde açılan GERÇEK bir yetimi görünmez yapardı.
        Bu yüzden yetim denetimi asıl kaynağı buradan alır; kayıt yalnız
        ikinci katmandır. Callback yoksa (ayrı halka) boş küme döner ve
        davranış D20a ile birebir aynıdır.
        """
        callback = getattr(self, "foreign_tracked_cb", None)
        if callback is None:
            return set()
        try:
            return {str(s).strip().upper() for s in (callback() or ()) if str(s).strip()}
        except Exception as exc:  # pragma: no cover - teşhis motoru düşürmez
            self.logger.warning(
                f"⚠️ Diğer motorun izleme listesi okunamadı ({exc}); yetim "
                f"denetimi yalnız rezervasyon kaydına dayanıyor"
            )
            return set()

    def _foreign_symbols(self) -> Set[str]:
        """BAŞKA bir motorun (scalper/orchestrator) sahiplendiği semboller."""
        try:
            return {
                symbol
                for symbol, owner in symbol_reservations.snapshot().items()
                if owner != self._RESERVATION_OWNER
            }
        except Exception:  # pragma: no cover - kayıt asla motoru düşürmemeli
            return set()

    def _reserve_symbol(self, symbol: str, *, enforce_capacity: bool = False) -> bool:
        """Sembolü takipçi adına sahiplen (aynı sahip için idempotent).

        ``enforce_capacity`` (D20b düşmanca inceleme): GİRİŞ yolunda takipçi
        de KENDİ tavanına (`FOLLOWER_MAX_POSITIONS`) atomik olarak takılır.
        Sayım YALNIZ takipçinin kendi rezervasyonlarını kapsar — scalper'ın
        ve Telegram'ın slotları tüketilmez, tersi de olmaz. Kurtarma yolunda
        tavan UYGULANMAZ: borsada zaten AÇIK olan bir pozisyonun sahipliğini
        reddetmek onu sahipsiz bırakırdı.
        """
        try:
            kwargs = {}
            if enforce_capacity:
                kwargs = {
                    "capacity": int(
                        getattr(self.cfg, "follower_max_positions", 4) or 4
                    ),
                    "capacity_owners": (self._RESERVATION_OWNER,),
                    "exchange_symbols": set(self.exits.tracked_symbols()),
                }
            return bool(
                symbol_reservations.reserve(
                    symbol, self._RESERVATION_OWNER, **kwargs
                )
            )
        except Exception as exc:  # pragma: no cover
            self.logger.warning(f"⚠️ {symbol}: sembol rezervasyonu yapılamadı ({exc})")
            return False

    def _release_symbol(self, symbol: str) -> None:
        try:
            symbol_reservations.release(symbol, self._RESERVATION_OWNER)
        except Exception as exc:  # pragma: no cover
            self.logger.warning(f"⚠️ {symbol}: sembol rezervasyonu bırakılamadı ({exc})")

    def _sync_follower_reservations(self) -> None:
        """İzlenmeyen sembollerin sahipliğini bırak (scalper'ın eşleniği).

        İKİ fail-closed koşulda hiçbir sahiplik bırakılmaz:
          * `_entry_halted`: korumasız/yetim bir pozisyon varken sembolü
            serbest bırakmak diğer motorun aynı net pozisyona ikinci bir
            yönetici olmasına yol açardı;
          * `_entry_lock` tutuluyor: UÇUŞTA bir giriş vardır, sahiplik
            emirden ÖNCE alınır ama sembol `track()` edilene kadar
            `tracked_symbols()`ta GÖRÜNMEZ — bu pencerede bırakmak
            scalper'ı aynı sembole davet ederdi;
          * yetim denetimi BU TURDA çalışamadı (`_orphans_check_ok=False`):
            borsa okuması patladığında (418/ağ) halt latch'lenmez; sahipliği
            yine de bırakmak, defter satırı yazılamamış açık bir pozisyonu
            hem defterden hem kayıttan düşürürdü (düşmanca inceleme).
        """
        if (
            self._entry_halted
            or self._entry_lock.locked()
            or not self._orphans_check_ok
        ):
            return
        active = set(self.exits.tracked_symbols()) | set(
            getattr(self.exits, "_closing", ()) or ()
        )
        try:
            owned = [
                symbol
                for symbol, owner in symbol_reservations.snapshot().items()
                if owner == self._RESERVATION_OWNER
            ]
        except Exception:  # pragma: no cover
            return
        for symbol in owned:
            if symbol not in active:
                self._release_symbol(symbol)

    @staticmethod
    def _open_symbols(rows: Any) -> Set[str]:
        found: Set[str] = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                amount = float(row.get("positionAmt", 0) or 0)
            except (TypeError, ValueError):
                continue
            symbol = str(row.get("symbol") or "").upper()
            if symbol and amount != 0:
                found.add(symbol)
        return found

    async def _check_orphans(self) -> List[str]:
        """Borsada AÇIK ama HİÇBİR motorun izlemediği pozisyonları bul.

        NEDEN (D20a bulgu 8): izlenmeyen bir pozisyon ne EXIT'e, ne flip'e, ne
        risk-olayı ``flatten``ına görünür; ``recover()`` yalnız DB'deki OPEN
        satırlarına bakar, bu yüzden defter satırı hiç yazılamamış bir dolum
        (ör. `record_open` DB hatası) SONSUZA KADAR görünmez kalır.

        **D20b: VARSAYIM DEĞİŞTİ.** Ayrı halkada hesap YALNIZ takipçinindi, bu
        yüzden izlenmeyen her pozisyon takipçinin kayıp pozisyonuydu ve kalıcı
        entry-halt doğru cevaptı. Gömülü modda hesap PAYLAŞILIR: Telegram
        orchestrator'ı, elle açılmış bir pozisyon ya da scalper'ın uçuştaki
        girişi MEŞRU biçimde "takipçinin izlemediği" pozisyonlardır. Bu yüzden:

        * **ayrı halka** (`BOT_MODE=follower`): D20a davranışı AYNEN —
          ENTRY-HALT (kalıcı) + CRITICAL;
        * **gömülü mod**: entry-halt YOK, flatten YOK — WARNING + sayaç +
          panoda uyarı satırı. Yabancı pozisyona dokunmak, operatörün
          "takipçiyi düzleştir" komutunu Telegram defterini kapatmaya
          çevirirdi.

        "Yabancı" kümesi İKİ kaynaktan gelir (düşmanca inceleme): (a) diğer
        motorların GERÇEKTEN izlediği semboller (`foreign_tracked_cb` — gömülü
        modda main.py enjekte eder), (b) `symbol_reservations` kaydı. (b) tek
        başına yeterli DEĞİLDİR: scalper entry-halt'a düştüğünde rezervasyonları
        DONAR ve o pozisyon kapansa bile kayıtta kalır — gerçek bir yetim o
        sembolde görünmez olurdu.

        Yanlış pozitif korumaları: (a) `_entry_lock` tutuluyorsa uçuşta bir
        giriş vardır ve henüz `track` edilmemiştir → tur atlanır;
        (b) kapanış defteri işlenen semboller (`_closing`) hariç tutulur;
        (c) kurtarma tamamlanmadıysa denetim yapılmaz; (d) şüphe TAZE bir
        okumayla doğrulanır; (e) gömülü modda kalıcı bir karar verilmediği
        için tek turluk yarışlar zararsızdır.
        """
        self._orphans_check_ok = False
        if self._entry_lock.locked() or not self._recovery_ready:
            return []
        try:
            rows = await self.client.get_all_positions(force_fresh=False)
        except Exception as exc:
            self.logger.error(f"❌ Takipçi yetim denetimi okunamadı: {exc}")
            return []

        def _suspects(source: Any) -> Set[str]:
            tracked = set(self.exits.tracked_symbols())
            closing = set(getattr(self.exits, "_closing", ()) or ())
            foreign = self._foreign_symbols() | self._foreign_tracked_symbols()
            return self._open_symbols(source) - tracked - closing - foreign

        suspects = _suspects(rows)
        self._orphans_checked_at = _utcnow_iso()
        if not suspects:
            self._orphans = []
            self._unknown_positions = []
            self._orphans_check_ok = True
            return []

        # Önbellek 15 sn'liktir: geri alınamaz bir karar (entry-halt) taze
        # okumayla doğrulanmadan verilmez.
        try:
            fresh_rows = await self.client.get_all_positions(force_fresh=True)
        except Exception as exc:
            self.logger.error(
                f"❌ Takipçi yetim şüphesi taze okumayla doğrulanamadı: {exc}"
            )
            return []
        confirmed = _suspects(fresh_rows)
        self._orphans_check_ok = True
        if not confirmed:
            self._orphans = []
            self._unknown_positions = []
            return []

        # AYRIM (kullanıcı kararı): takipçinin KENDİ rezerve ettiği ama
        # izlemediği pozisyon onun YETİMİDİR (ör. `record_open` DB hatası) →
        # gömülü modda bile D20a davranışı geçerlidir. Hiç kimsenin rezerve
        # ETMEDİĞİ pozisyon ise "sahipsiz"dir (elle/Telegram) ve dokunulmaz.
        mine = {
            symbol
            for symbol in confirmed
            if symbol_reservations.owner(symbol) == self._RESERVATION_OWNER
        }
        found = sorted(confirmed)
        if self._embedded() and not mine:
            # Gömülü mod: SAHİPSİZ ama MEŞRU olabilir → yalnız GÖRÜNÜRLÜK.
            self._orphans = []
            first_time = found != self._unknown_positions
            self._unknown_positions = found
            self._count_reject("unknown_position")
            if first_time:
                self.logger.warning(
                    f"⚠️ Hesapta SAHİPSİZ açık pozisyon(lar): {found} — hiçbir "
                    f"motor izlemiyor ve rezerve etmemiş (elle ya da Telegram "
                    f"botuyla açılmış olabilir). Takipçi bunlara DOKUNMAZ ve "
                    f"girişleri DURDURMAZ (D20b); /risk-event flatten de "
                    f"kapatmaz. İzlenen(takipçi)="
                    f"{sorted(self.exits.tracked_symbols())}",
                    extra={"trade": True},
                )
            return []

        # Gömülü modda YALNIZ takipçinin kendi yetimi halt üretir; sahipsiz
        # olanlar görünürlük listesinde kalır.
        self._orphans = sorted(mine) if self._embedded() else found
        self._unknown_positions = (
            sorted(confirmed - mine) if self._embedded() else []
        )
        self.logger.critical(
            f"🚨 TAKİPÇİ YETİM POZİSYON(LAR): {self._orphans} borsada AÇIK ama "
            f"motor İZLEMİYOR (izlenen={sorted(self.exits.tracked_symbols())}). "
            f"Yeni girişler durduruldu. Kapatmak için: "
            f"POST /risk-event " + '{"action":"flatten"}',
            extra={"trade": True},
        )
        self._latch_entry_halt(
            RuntimeError(f"izlenmeyen açık pozisyon(lar): {self._orphans}"),
            source="yetim pozisyon denetimi",
        )
        return self._orphans

    async def _exchange_loop(self) -> None:
        while self.running:
            try:
                ready = await self._probe_exchange()
                if ready and not self._recovery_ready:
                    await self._attempt_recovery()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._exchange_ready = False
                self._exchange_last_error = f"{type(exc).__name__}: {exc}"
                self.logger.error(f"❌ Takipçi readiness döngüsü hatası: {exc}")
            await asyncio.sleep(self._EXCHANGE_PROBE_INTERVAL)

    async def _safety_loop(self) -> None:
        self.logger.info("🛡️ Takipçi safety döngüsü başladı")
        while self.running:
            failure: Optional[str] = None
            try:
                await self.exits.step()
            except asyncio.CancelledError:
                raise
            except UnprotectedPositionError as exc:
                self._latch_entry_halt(exc, source="safety")
                failure = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                failure = f"{type(exc).__name__}: {exc}"
                self.logger.error(f"❌ Takipçi safety hatası: {exc}", exc_info=True)

            # Yetim denetimi AYRI try'da (bulgu 8): `exits.step()` patlasa
            # bile borsa gerçeği ile izleme listesi karşılaştırılmalıdır.
            try:
                # SIRA ÖNEMLİ: önce yetim denetimi. Defter satırı yazılamamış
                # (ama borsada AÇIK) bir pozisyon önce entry-halt latch'ler;
                # sonra çalışan sahiplik senkronu o hâlde hiçbir sembolü
                # bırakmaz. Ters sırada, halt latch'lenmeden önceki pencerede
                # sembol serbest kalır ve scalper aynı net pozisyona girerdi.
                await self._check_orphans()
                self._sync_follower_reservations()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = failure or f"{type(exc).__name__}: {exc}"
                self.logger.error(
                    f"❌ Takipçi yetim denetimi başarısız: {exc}", exc_info=True
                )

            # Kill switch AYRI try'da: `exits.step()` patlarsa günlük zarar
            # kapısı bayat `_risk_ready=True` ile açık kalırdı. Risk kapısı,
            # çıkış turunun sağlığına BAĞLI OLMAMALIDIR.
            try:
                await self._update_kill_switch()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = failure or f"{type(exc).__name__}: {exc}"
                self.logger.error(
                    f"❌ Takipçi kill switch güncellenemedi: {exc}", exc_info=True
                )

            if failure is None:
                self._safety_last_success_monotonic = time.monotonic()
                self._safety_consecutive_errors = 0
                self._safety_last_error = None
            else:
                self._safety_consecutive_errors += 1
                self._safety_last_error = failure
            await asyncio.sleep(self._safety_interval_seconds())

    def _safety_interval_seconds(self) -> float:
        try:
            return max(
                0.5, float(getattr(self.cfg, "follower_safety_interval_seconds", 2.0))
            )
        except (TypeError, ValueError):
            return 2.0

    # ------------------------------------------------------------------
    # Günlük zarar kesici (SCALPER_DAILY_LOSS_LIMIT_PCT mantığı)
    # ------------------------------------------------------------------

    async def _update_kill_switch(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._kill_switch_day != today:
            self._kill_switch_day = today
            self._kill_switch = False

        limit_pct = float(
            getattr(self.cfg, "follower_daily_loss_limit_pct", 0.0) or 0.0
        )
        if limit_pct <= 0:
            self._risk_ready = True
            self._daily_loss_threshold_usdt = None
            return

        try:
            pnl = await self._daily_net_income(today)
        except Exception as exc:
            # Fail-closed: günlük riski doğrulayamıyorsak yeni giriş YOK.
            self._risk_ready = False
            self.logger.error(
                f"❌ Takipçi günlük net PNL doğrulanamadı; girişler kapalı: {exc}"
            )
            return
        self._daily_pnl = pnl
        self._risk_ready = True
        if self._kill_switch:
            return

        balance = await self._risk_equity()
        if balance is None or balance <= 0:
            self._risk_ready = False
            self._risk_equity_usdt = None
            self._daily_loss_threshold_usdt = None
            return
        self._risk_equity_usdt = balance

        day_start_balance = max(balance - pnl, 0.0)
        threshold = -day_start_balance * limit_pct / 100.0
        self._daily_loss_threshold_usdt = threshold
        if pnl <= threshold:
            self._kill_switch = True
            self.logger.warning(
                f"⛔ Takipçi kill switch TETİKLENDİ: günlük PNL={pnl:.2f} <= "
                f"eşik={threshold:.2f} (sermaye={balance:.2f}, limit=%{limit_pct}). "
                f"Yeni giriş yok; açık pozisyonların çıkış takibi sürüyor.",
                extra={"trade": True},
            )

    async def _daily_net_income(self, today: str) -> float:
        """Takipçinin BUGÜNKÜ net PnL'i.

        Gömülü modda (D20b) hesap scalper ile PAYLAŞILDIĞI için
        `/fapi/v1/income` iki defteri birlikte raporlar ve takipçinin
        kesicisini scalper'ın işlemleri tetikleyebilirdi. O yüzden gömülü
        modda kaynak DEFTERDİR (`scalp_trades`, `strategy='AP'`,
        `realized_pnl` = komisyon düşülmüş net). Ayrı halkada hesap zaten
        takipçinindir → Binance income'ı (bugünkü davranış) kullanılır.
        """
        if bool(getattr(self.cfg, "follower_embedded", False)):
            return await self._ledger_daily_pnl(today)
        return await self._account_daily_net_income(today)

    async def _ledger_daily_pnl(self, today: str) -> float:
        """AP defterinin bugünkü (UTC) toplam net PnL'i."""
        getter = getattr(self.tracker, "strategy_realized_pnl_since", None)
        if getter is None:  # pragma: no cover - eski tracker çifti
            raise RuntimeError(
                "tracker.strategy_realized_pnl_since yok — takipçi günlük "
                "PnL'i defterden okunamıyor"
            )
        day_start = datetime.strptime(today, "%Y-%m-%d")
        return float(await getter(FOLLOWER_STRATEGY, day_start))

    async def _account_daily_net_income(self, today: str) -> float:
        cached_value, cached_at, cached_day = self._income_cache
        now_monotonic = time.monotonic()
        if (
            cached_value is not None
            and cached_day == today
            and now_monotonic - cached_at < self._INCOME_CACHE_TTL
            and self._income_cache_close_seq == getattr(self.tracker, "close_seq", 0)
        ):
            return cached_value

        start = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 1000
        rows = await self.client.get_income_history(
            start_time_ms=start_ms, end_time_ms=end_ms, limit=1000
        )
        if len(rows) >= 1000:
            raise RuntimeError(
                "Günlük income sonucu 1000 satır sınırına ulaştı; eksik PNL riski"
            )
        allowed = {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}
        net = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("incomeType") or "") not in allowed:
                continue
            try:
                net += float(row.get("income") or 0.0)
            except (TypeError, ValueError):
                raise RuntimeError(f"Geçersiz Binance income satırı: {row!r}")
        self._income_cache = (net, now_monotonic, today)
        self._income_cache_close_seq = getattr(self.tracker, "close_seq", 0)
        return net

    async def _risk_equity(self) -> Optional[float]:
        """Günlük kesicinin sermaye tabanı.

        D20b: gömülü modda SANAL sermaye (taban + AP net PnL) — hesap
        bakiyesi scalper'ın marjıyla birlikte dalgalanır ve takipçinin
        günlük eşiğini onun pozisyonları belirleyemez. Sanal defter kapalıysa
        (ayrı halka) bugünkü tanım: hesabın `availableBalance`'ı.
        """
        if self._virtual_capital_base() > 0:
            # Telemetri: `virtual_ledger.exchange_available_usdt` İLK GİRİŞE
            # kadar null kalıyordu; 5 dk önbellekli bu okuma ihmal edilebilir
            # bir yük getirir ve pano kartı süreç başlar başlamaz dolu olur.
            await self._cached_balance()
            try:
                equity = await self._virtual_equity()
            except FollowerRejected as exc:
                # Fail-closed: defter okunamıyorsa günlük kapı doğrulanamaz.
                self.logger.error(f"❌ Takipçi sanal sermayesi okunamadı: {exc.reason}")
                return None
            return equity if equity > 0 else None
        return await self._cached_balance()

    async def _cached_balance(self) -> Optional[float]:
        """Hesabın kullanılabilir bakiyesi (kısa önbellekli).

        BOYUTLAMA İLE AYNI TANIM (bulgu 9): `_entry_equity` →
        ``get_account_balance()`` = **availableBalance**. Eskiden burada
        ``get_wallet_balance()`` (totalWalletBalance) okunuyordu; iki farklı
        tanım, "marj = sermayenin %10'u" ile "günlük zarar limiti = günün
        açılış sermayesinin %15'i" arasında sessiz bir tutarsızlık yaratır
        (açık pozisyonların marjı kadar). availableBalance daha MUHAFAZAKÂR
        bir eşik üretir (açık marj düşülmüştür) — fail-closed yön.
        """
        balance, cached_at = self._balance_cache
        now = time.monotonic()
        if balance is not None and (now - cached_at) < self._BALANCE_CACHE_TTL:
            self._exchange_available_usdt = balance
            return balance
        try:
            fresh = await self.client.get_account_balance()
        except Exception as exc:
            self.logger.error(f"❌ Takipçi bakiye sorgusu hatası: {exc}")
            return balance
        self._balance_cache = (fresh, now)
        self._exchange_available_usdt = fresh
        return fresh

    # ------------------------------------------------------------------
    # Kapılar
    # ------------------------------------------------------------------

    def symbol_allowlist(self) -> Set[str]:
        """Takipçinin GİRİŞ evreni.

        D20b: `FOLLOWER_SYMBOLS` doluysa evren ODUR (ve aynı semboller
        scalper'ın tarama evreninden + TV giriş oylamasından OTOMATİK
        dışlanır — bkz. `config.follower_reserved_symbols`). Boşsa bugünkü
        davranış korunur: `FOLLOWER_SYMBOL_ALLOWLIST` (8 majör).

        Sembol listesi KODDA SABİT DEĞİLDİR; tamamı `.env`'den gelir ve
        her okumada güncel değeri yansıtır.
        """
        universe = getattr(self.cfg, "follower_universe", None)
        if universe:
            return {str(s).strip().upper() for s in universe if str(s).strip()}
        raw = str(getattr(self.cfg, "follower_symbols", "") or "") or str(
            getattr(self.cfg, "follower_symbol_allowlist", "") or ""
        )
        return {s.strip().upper() for s in raw.split(",") if s.strip()}

    def _safety_stale_limit(self) -> float:
        """Safety turunun bayat sayılacağı yaş (health_snapshot ile AYNI eşik)."""
        return max(15.0, self._safety_interval_seconds() * 10.0)

    def _safety_fresh(self) -> bool:
        """Çıkış/BE/kapanış turu canlı mı? Bayatsa YENİ GİRİŞ YOK (fail-closed).

        Takipçide safety turu TEK risk kapısıdır: TP1→BE, kapanış defteri ve
        günlük zarar kesici hep oradan işler. Tur sürekli hata veriyorsa
        `_risk_ready` bayat kalabilir; o hâlde giriş açmak, kapısı çalışmayan
        bir sisteme pozisyon eklemektir.
        """
        if self._safety_last_success_monotonic is None:
            return False
        return (
            time.monotonic() - self._safety_last_success_monotonic
        ) <= self._safety_stale_limit()

    def _entries_ready(self) -> bool:
        exchange_age = (
            time.monotonic() - self._exchange_last_success_monotonic
            if self._exchange_last_success_monotonic is not None
            else float("inf")
        )
        return bool(
            self._exchange_ready
            and exchange_age <= self._EXCHANGE_PROBE_INTERVAL * 3.0
            and self._recovery_ready
            and self._risk_ready
            and self._safety_fresh()
            and not self._entry_halted
            and not self._kill_switch
            and not self.halt.active
        )

    def _entry_block_reason(self) -> Optional[str]:
        if self._entry_halted:
            return f"giriş kilidi aktif ({self._entry_halt_reason})"
        if self._kill_switch:
            return "kill switch (günlük zarar limiti)"
        snapshot = self.halt.snapshot()
        if snapshot.get("active"):
            return f"risk-event halt ({snapshot.get('reason')})"
        if not self._risk_ready:
            return "günlük risk kapısı doğrulanamadı"
        if not self._exchange_ready or not self._recovery_ready:
            return "borsa/kurtarma hazır değil"
        if not self._safety_fresh():
            return (
                f"safety turu bayat (>{self._safety_stale_limit():.0f} sn): "
                f"{self._safety_last_error or 'henüz başarılı tur yok'}"
            )
        return None

    def _count_event(self, kind: str) -> None:
        self._event_counters[kind] = self._event_counters.get(kind, 0) + 1

    def _count_reject(self, reason: str) -> None:
        self._reject_counters[reason] = self._reject_counters.get(reason, 0) + 1

    def note_route_reject(self, reason: str) -> None:
        """Köprü katmanında (motora HİÇ ulaşmadan) reddedilen olayı say.

        D20b: takipçi evreni dışındaki AlgoPro girişleri motora verilmez ama
        SESSİZ de kalmaz — `/follower/status → reject_counters` üzerinden
        ölçülebilir olmalıdır ("kaç alarm boşa gitti?").
        """
        self._count_reject(reason)
        self._last_event_at = _utcnow_iso()

    def _record_event(self, event: FollowerEvent, result: Dict[str, Any]) -> None:
        self._last_event_at = _utcnow_iso()
        self._events.append(
            {
                "at": self._last_event_at,
                **event.as_dict(),
                "accepted": bool(result.get("accepted")),
                "reason": result.get("reason"),
            }
        )

    # ------------------------------------------------------------------
    # Olay işleme
    # ------------------------------------------------------------------

    async def handle_event(
        self, event: FollowerEvent, received_monotonic: Optional[float] = None
    ) -> Dict[str, Any]:
        """AlgoPro olayını işle. ASLA istisna yükseltmez (yanıt sözleşmesi).

        ``received_monotonic``: olayın HTTP'de alındığı an (``time.monotonic``).
        Uç nokta bunu geçirir; geçirilmezse "şimdi" varsayılır. Giriş yolu
        bununla olay YAŞINI ölçer (bkz. ``_handle_entry``, bulgu 6).
        """
        if received_monotonic is None:
            received_monotonic = time.monotonic()
        self._count_event(event.kind)
        try:
            result = await self._dispatch(event, received_monotonic)
        except UnprotectedPositionError as exc:
            self._latch_entry_halt(exc, source="olay işleme")
            result = {"accepted": False, "reason": f"korumasız pozisyon: {exc}"}
        except FollowerRejected as exc:
            self._count_reject(exc.code)
            result = {"accepted": False, "reason": exc.reason}
        except Exception as exc:
            self.logger.error(
                f"❌ {event.symbol}: takipçi olayı işlenemedi ({exc})", exc_info=True
            )
            result = {"accepted": False, "reason": f"beklenmeyen hata: {exc}"}
        result.setdefault("kind", event.kind)
        result.setdefault("symbol", event.symbol)
        self._record_event(event, result)
        if not result.get("accepted"):
            self.logger.info(
                f"🚫 Takipçi olayı işlenmedi: {event.symbol} {event.kind} — "
                f"{result.get('reason')}"
            )
        return result

    async def _dispatch(
        self, event: FollowerEvent, received_monotonic: float
    ) -> Dict[str, Any]:
        symbol = event.symbol
        # EVREN/ZAMAN DİLİMİ KAPILARI YALNIZ GİRİŞTE (düşmanca inceleme
        # bulgu 9). Eskiden ÇIKIŞ ve HIT olayları da bu kapılardan geçiyordu:
        # allowlist bir sembolden çıkarıldığında (ya da alarm başka bir
        # zaman diliminden geldiğinde) AÇIK bir pozisyonun EXIT'i sessizce
        # düşerdi — kapılar "yeni risk alma" içindir, "riskten çıkma"yı
        # ASLA engellememelidir.
        if event.kind == KIND_ENTRY:
            allowlist = self.symbol_allowlist()
            if allowlist and symbol not in allowlist:
                self._count_reject("symbol_allowlist")
                return {"accepted": False, "reason": "sembol takipçi evreninde değil"}

            configured_tf = str(getattr(self.cfg, "follower_timeframe", "1") or "1")
            tf = str(event.timeframe or "")
            # TradingView {{interval}} 1 dakikada "1" döner; elle yazılan
            # şablonda "1m" görülebilir — ikisi AYNI dilimdir.
            if tf and _norm_timeframe(tf) != _norm_timeframe(configured_tf):
                self._count_reject("timeframe")
                return {
                    "accepted": False,
                    "reason": (
                        f"zaman dilimi eşleşmiyor (tf={tf}, beklenen {configured_tf})"
                    ),
                }
            return await self._handle_entry(event, received_monotonic)
        if event.kind == KIND_EXIT:
            return await self._handle_exit(event)
        return await self._handle_hit(event)

    # -- giriş ---------------------------------------------------------

    async def _handle_entry(
        self, event: FollowerEvent, received_monotonic: float = 0.0
    ) -> Dict[str, Any]:
        symbol = event.symbol
        direction = event.direction
        if direction is None:
            return {"accepted": False, "reason": "giriş olayında yön yok"}

        min_score = float(getattr(self.cfg, "follower_min_score", 0.0) or 0.0)
        if min_score > 0 and event.score is not None and event.score < min_score:
            self._count_reject("min_score")
            return {
                "accepted": False,
                "reason": f"AlgoPro skoru düşük ({event.score} < {min_score})",
            }

        async with self._entry_lock:
            existing = self.exits._positions.get(symbol)
            flipped = False
            if existing is not None:
                if existing.signal.direction == direction:
                    self._count_reject("already_open")
                    return {
                        "accepted": False,
                        "reason": "sembolde aynı yönde açık pozisyon var",
                    }
                if not bool(getattr(self.cfg, "follower_flip", True)):
                    self._count_reject("flip_disabled")
                    return {
                        "accepted": False,
                        "reason": "ters sinyal — FOLLOWER_FLIP kapalı, pozisyon korunuyor",
                    }
                closed = await self._close_tracked(
                    symbol, existing, reason="AP_REVERSE"
                )
                if not closed:
                    self._count_reject("flip_close_failed")
                    return {
                        "accepted": False,
                        "reason": "ters sinyal — mevcut pozisyon kapatılamadı, "
                        "yeni giriş yapılmadı",
                    }
                flipped = True

            # Kapılar KAPANIŞTAN SONRA kontrol edilir — bilinçli: kill switch /
            # risk-olayı halt'ı / giriş kilidi "YENİ GİRİŞ YOK" demektir, "açık
            # pozisyonu tut" demez. AlgoPro'nun ters sinyali bir ÇIKIŞ
            # kararıdır; kapı kapalıysa sonuç FLAT kalmaktır (en güvenli hâl).
            blocked = self._entry_block_reason()
            if blocked is not None or not self._entries_ready():
                self._count_reject("gate")
                return {
                    "accepted": False,
                    "reason": blocked or "girişler hazır değil",
                }

            # Ters sinyalde cooldown BİLİNÇLİ olarak atlanır: AlgoPro'nun açık
            # dönüş komutudur, aksi halde FOLLOWER_FLIP fiilen ölü olurdu.
            if not flipped and self.executor.is_entry_blocked(symbol):
                self._count_reject("cooldown")
                return {"accepted": False, "reason": "sembol cooldown'da"}

            # D20b: sembol BAŞKA bir motorun (scalper) yönetimindeyse giriş
            # YOK. Binance one-way modda sembol başına TEK net pozisyon
            # vardır; iki yönetici aynı pozisyona `closePosition` stop
            # koyarsa biri diğerinin miktarını kapatır.
            if symbol in self._foreign_symbols():
                self._count_reject("reserved_by_other")
                return {
                    "accepted": False,
                    "reason": "sembol başka bir motorun yönetiminde "
                    "(scalper) — giriş yapılmadı",
                }

            # Kapasite takipçinin KENDİ tavanıdır (scalper'ınki değişmez).
            tracked = self.exits.tracked_symbols()
            max_positions = int(getattr(self.cfg, "follower_max_positions", 4) or 4)
            if symbol not in tracked and len(tracked) >= max_positions:
                self._count_reject("capacity")
                return {
                    "accepted": False,
                    "reason": f"kapasite dolu ({len(tracked)}/{max_positions})",
                }

            # Borsa gerçeği son kapıdır: izlenmeyen ama AÇIK bir pozisyon
            # (ör. record_open DB hatası, elle açılmış pozisyon) üstüne ikinci
            # bir giriş yapmak pozisyonu İKİYE KATLAR. Scalper'ın
            # `_evaluate_symbol`'daki `live_symbols` kapısının eşleniği;
            # okuma başarısızsa fail-closed.
            try:
                live_info = await self.client.get_position_risk(
                    symbol, force_fresh=True
                )
                live_amt = (
                    abs(float(live_info.get("positionAmt", 0) or 0))
                    if live_info
                    else 0.0
                )
            except Exception as exc:
                self._count_reject("position_check")
                return {
                    "accepted": False,
                    "reason": f"borsa pozisyonu doğrulanamadı ({exc})",
                }
            if live_amt != 0:
                self._count_reject("live_position")
                return {
                    "accepted": False,
                    "reason": f"borsada izlenmeyen açık pozisyon var ({live_amt}) — "
                    f"giriş yapılmadı",
                }

            # --- BAYATLIK KAPILARI (düşmanca inceleme bulgu 6) ------------
            # Buraya kadar gelen yol GLOBAL `_entry_lock` altındadır: aynı
            # anda gelen ikinci bir alarm, birincinin MARKET+SL+3×TP turunu
            # (3-6 sn) kuyrukta bekler. 1 dakikalık grafikte 20 sn beklemiş
            # bir sinyal artık o sinyal DEĞİLDİR.
            max_age = float(
                getattr(self.cfg, "follower_max_event_age_sec", 20.0) or 0.0
            )
            age = time.monotonic() - float(received_monotonic or 0.0)
            if max_age > 0 and received_monotonic and age > max_age:
                self._count_reject("event_age")
                return {
                    "accepted": False,
                    "reason": (
                        f"olay bayat ({age:.1f} sn > {max_age:.0f} sn) — "
                        f"giriş yapılmadı"
                    ),
                    "flipped": flipped,
                }

            # Boyutlama ve seviyeler ASLA alarm fiyatından hesaplanmaz:
            # `sl_pct` kaldıraç formülünün paydasıdır ve bayat bir fiyat
            # kaldıracı (dolayısıyla nominali) YANLIŞ ölçekler.
            try:
                entry_price = await self.client.get_current_price(symbol)
            except Exception as exc:
                self._count_reject("price")
                return {
                    "accepted": False,
                    "reason": f"giriş fiyatı okunamadı ({exc})",
                }
            if not entry_price or entry_price <= 0:
                self._count_reject("price")
                return {"accepted": False, "reason": "giriş fiyatı çözülemedi"}
            entry_price = float(entry_price)

            alarm_price = float(event.price or 0.0)
            message_sl = float(event.levels.sl or 0.0)
            if alarm_price > 0 and message_sl > 0:
                message_sl_pct = (
                    abs(alarm_price - message_sl) / alarm_price * 100.0
                )
                limit_pct = signal_drift_limit_pct(message_sl_pct, self.cfg)
                drift_pct = abs(entry_price - alarm_price) / alarm_price * 100.0
                if limit_pct > 0 and drift_pct > limit_pct:
                    self._count_reject("signal_drift")
                    return {
                        "accepted": False,
                        "reason": (
                            f"sinyal fiyatı bayat (alarm {alarm_price:g} vs canlı "
                            f"{entry_price:g}, sapma %{drift_pct:.4f} > "
                            f"%{limit_pct:.4f})"
                        ),
                        "flipped": flipped,
                    }
            if message_sl > 0 and not stop_on_correct_side(
                direction, entry_price, message_sl
            ):
                self._count_reject("stop_already_passed")
                return {
                    "accepted": False,
                    "reason": (
                        f"AlgoPro stopu ({message_sl:g}) canlı fiyatın "
                        f"({entry_price:g}) yanlış tarafında — giriş yapılmadı"
                    ),
                    "flipped": flipped,
                }

            atr_value = None
            if not event.levels.has_sl:
                atr_value = await self._atr_fallback(symbol)

            levels = resolve_levels(
                entry=float(entry_price),
                direction=direction,
                message=event.levels,
                atr_value=atr_value,
                cfg=self.cfg,
            )
            for warning in levels.warnings:
                self.logger.warning(f"⚠️ {symbol}: seviye uyarısı — {warning}")
            self._log_calibration(event, levels, direction)

            equity = await self._entry_equity()
            if equity is None:
                self._count_reject("equity")
                return {"accepted": False, "reason": "hesap bakiyesi okunamadı"}

            # SON kapı: aynı sembol için bir kapanış defteri HÂLÂ işleniyorsa
            # yeni pozisyon AÇMA. `_finalize_close`'un ilk işi
            # `cancel_all_open_orders(symbol)`'dır ve saniyeler sürebilir
            # (userTrades + income merdiveni); bu pencerede açılan pozisyonun
            # SL/TP emirleri o iptal turuna yakalanabilir → KORUMASIZ pozisyon.
            # (Kapanış defterinin izleme listesinden düşürmesi artık kimlik
            # kontrollüdür — bkz. scalper/exits.py `_handle_closed` — ama emir
            # iptali yarışını yalnız bu kapı kapatır.)
            if symbol in getattr(self.exits, "_closing", ()):
                self._count_reject("close_in_flight")
                return {
                    "accepted": False,
                    "reason": "aynı sembolde kapanış defteri işleniyor — "
                    "yeni giriş yapılmadı",
                    "flipped": flipped,
                }

            # SEMBOL SAHİPLİĞİ emirden HEMEN ÖNCE alınır: `_entry_lock`
            # yalnız takipçinin kendi girişlerini sıraya sokar, scalper'ın
            # tarama turunu DEĞİL. Rezervasyon atomiktir (RLock) ve
            # scalper `_evaluate_symbol`'da aynı kayda bakar.
            if not self._reserve_symbol(symbol, enforce_capacity=True):
                self._count_reject("reserved_by_other")
                return {
                    "accepted": False,
                    "reason": "sembol sahipliği alınamadı (başka motor ya da "
                    "takipçi kapasitesi dolu) — giriş yapılmadı",
                    "flipped": flipped,
                }

            try:
                position = await self.executor.open_position(
                    event=event, levels=levels, equity_usdt=equity
                )
            except UnprotectedPositionError:
                # Sahiplik BIRAKILMAZ: korumasız pozisyon şüphesi varken
                # sembolü serbest bırakmak ikinci bir yöneticiyi davet eder
                # (motor zaten entry-halt latch'ler).
                raise
            except FollowerRejected as exc:
                self._release_symbol(symbol)
                # Kapıda reddedilen giriş DEFTERE yazılır (D20a bulgu 3):
                # "kaç işlem ücret eşiğine takıldı" sorusu ancak ölçülebilir
                # bir kayıtla yanıtlanır; sayaç süreç ömrüyle sınırlıdır.
                self._log_calibration(
                    event, levels, direction, rejected=exc.code, reason=exc.reason
                )
                raise
            if position is None:
                return {
                    "accepted": False,
                    "reason": "emir yolu başarısız (log'a bakın)",
                    "flipped": flipped,
                }
            self.exits.track(position)

        plan_meta = position.meta.get("plan", {})
        self.logger.info(
            f"🎯 {symbol}: AlgoPro {direction.value} girişi açıldı "
            f"(lev={plan_meta.get('leverage')}x, sl_pct=%{_fmt(plan_meta.get('sl_pct'))}, "
            f"sl_roi=%{_fmt(plan_meta.get('sl_roi_pct'), '.2f')}, "
            f"marj={_fmt(plan_meta.get('margin_usdt'), '.2f')} USDT, "
            f"skor={event.score}, tqi={event.tqi})",
            extra={"trade": True},
        )
        return {
            "accepted": True,
            "reason": "pozisyon açıldı",
            "flipped": flipped,
            "trade_id": position.trade_id,
            "plan": plan_meta,
        }

    async def _atr_fallback(self, symbol: str) -> Optional[float]:
        """Mesajda SL yoksa 1m mumlarından ATR — yalnız YEDEK yol."""
        period = int(getattr(self.cfg, "follower_atr_len", 14) or 14)
        try:
            candles = await self.fetcher.get_klines(symbol, "1m", max(period * 6, 100))
        except Exception as exc:
            self.logger.error(f"❌ {symbol}: ATR için 1m mumları alınamadı ({exc})")
            return None
        value = compute_atr(candles, period)
        return value if value > 0 else None

    # -- sanal defter (D20b) -------------------------------------------

    def _virtual_capital_base(self) -> float:
        """Sanal defterin TABANI (USDT) — 0 = sanal defter KAPALI.

        YALNIZ gömülü modda (`FOLLOWER_EMBEDDED=true`) uygulanır: orada hesap
        scalper ile PAYLAŞILIR ve gerçek bakiye takipçinin kenarını ölçmez.
        Ayrı halkada (`BOT_MODE=follower`) hesap zaten takipçinindir →
        bugünkü davranış birebir korunur (gerçek bakiye).
        """
        if not bool(getattr(self.cfg, "follower_embedded", False)):
            return 0.0
        try:
            return max(
                0.0, float(getattr(self.cfg, "follower_virtual_capital_usdt", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            return 0.0

    async def _virtual_equity(self) -> float:
        """Sanal sermaye = taban + AP işlemlerinin gerçekleşmiş net PnL'i.

        Restart'a dayanıklıdır: toplam RAM'de değil `scalp_trades`
        (`strategy='AP'`) satırlarından hesaplanır. Muhafazakâr kural
        scalper'ın sanal kasasıyla AYNIdır (`compounding_snapshot`):
        Binance'ın doğruladığı net PnL her iki işaretiyle sayılır, tahmini
        (fallback) satır YALNIZ negatifse sayılır, legacy satır hiç sayılmaz —
        yani sermaye doğrulanmamış kârla ŞİŞMEZ.

        Okunamazsa `FollowerRejected` (fail-closed): defteri doğrulayamadan
        1000 USDT'lik varsayımla pozisyon açmak sessiz bir risk artışıdır.
        """
        base = self._virtual_capital_base()
        # 30 sn ÖNBELLEK (düşmanca inceleme): safety turu 2 sn'de bir çalışır
        # ve her turda iki DB toplaması yapmak gereksiz iştir (scalper'ın
        # eşdeğer yolu da önbelleklidir). Kapanış olduğunda (`close_seq`
        # artışı) önbellek ANINDA düşer — sanal sermaye bir işlem geriden
        # gelmez.
        close_seq = int(getattr(self.tracker, "close_seq", 0) or 0)
        cached_at = self._virtual_equity_cached_at
        if (
            self._virtual_equity_usdt is not None
            and self._virtual_equity_cache_seq == close_seq
            and (time.monotonic() - cached_at) < self._VIRTUAL_EQUITY_CACHE_TTL
        ):
            return self._virtual_equity_usdt
        try:
            snapshot_method = getattr(self.tracker, "compounding_snapshot", None)
            if snapshot_method is not None:
                snapshot = await snapshot_method(
                    0, strategies=(FOLLOWER_STRATEGY,)
                )
                eligible = float(snapshot["eligible_realized_pnl"])
            else:
                eligible = float(
                    await self.tracker.eligible_compounding_pnl(
                        0, strategies=(FOLLOWER_STRATEGY,)
                    )
                )
        except Exception as exc:
            raise FollowerRejected(
                f"Takipçi sanal sermayesi doğrulanamadı ({exc}) — giriş yapılmadı",
                code="virtual_equity",
            ) from exc
        self._virtual_equity_usdt = max(0.0, base + eligible)
        self._virtual_realized_pnl = eligible
        self._virtual_equity_cache_seq = close_seq
        self._virtual_equity_cached_at = time.monotonic()
        return self._virtual_equity_usdt

    async def _entry_equity(self) -> Optional[float]:
        """Boyutlamanın sermaye tabanı (marj = equity × FOLLOWER_MARGIN_PCT).

        Gömülü modda SANAL sermaye döner; gerçek hesap bakiyesi yalnız
        "marjı ödeyebiliyor muyum?" kapısıdır (yetmezse giriş YOK + log).
        """
        try:
            balance = await self.client.get_account_balance()
        except Exception as exc:
            self.logger.error(f"❌ Takipçi bakiye sorgusunda hata ({exc})")
            return None
        if balance is None or float(balance) <= 0:
            return None
        available = float(balance)
        self._exchange_available_usdt = available

        if self._virtual_capital_base() <= 0:
            self._virtual_equity_usdt = None
            return available

        equity = await self._virtual_equity()
        if equity <= 0:
            self._count_reject("virtual_equity_zero")
            self.logger.error(
                f"⛔ Takipçi sanal sermayesi tükendi (taban="
                f"{self._virtual_capital_base():.2f} + AP net PnL="
                f"{self._virtual_realized_pnl:.2f} ≤ 0) — yeni giriş yok"
            )
            return None

        margin_pct = float(getattr(self.cfg, "follower_margin_pct", 10.0) or 0.0)
        margin_needed = equity * margin_pct / 100.0
        if available < margin_needed:
            self.logger.error(
                f"⛔ Takipçi girişi atlandı: hesabın kullanılabilir bakiyesi "
                f"({available:.2f} USDT) gereken marjı ({margin_needed:.2f} USDT "
                f"= sanal sermaye {equity:.2f} × %{margin_pct:g}) KARŞILAMIYOR. "
                f"Scalper'ın açık marjı hesabı doldurmuş olabilir.",
                extra={"trade": True},
            )
            raise FollowerRejected(
                f"gerçek bakiye yetersiz ({available:.2f} < {margin_needed:.2f} USDT)",
                code="insufficient_balance",
            )
        return equity

    def _log_calibration(
        self,
        event: FollowerEvent,
        levels: Any,
        direction: Direction,
        *,
        rejected: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Kalibrasyon defteri (JSONL) — yazma hatası girişi ASLA bozmaz.

        ``rejected``: giriş bir KAPIDA reddedildiyse kapının kodu
        (ör. ``fee_gate``) ve insan-okur gerekçe; ``scalp_trades`` KİRLETİLMEZ
        (dolum olmadan işlem satırı yazmak PF/PnL raporlarını bozar).
        """
        path_value = getattr(self.cfg, "follower_levels_log_path", "") or ""
        if not path_value:
            return
        try:
            record = calibration_record(
                symbol=event.symbol,
                direction=direction,
                kind=event.kind,
                ts=event.ts or _utcnow_iso(),
                levels=levels,
                cfg=self.cfg,
            )
            record["score"] = event.score
            record["tqi"] = event.tqi
            if rejected:
                record["rejected"] = rejected
                record["rejected_reason"] = str(reason or "")[:240]
            path = Path(path_value).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning(f"⚠️ Kalibrasyon defteri yazılamadı ({exc})")

    # -- çıkış ---------------------------------------------------------

    async def _handle_exit(self, event: FollowerEvent) -> Dict[str, Any]:
        symbol = event.symbol
        async with self._entry_lock:
            sp = self.exits._positions.get(symbol)
            if sp is None:
                return {"accepted": False, "reason": "izlenen pozisyon yok"}
            closed = await self._close_tracked(symbol, sp, reason="AP_EXIT")
        if not closed:
            return {
                "accepted": False,
                "reason": "kapanış borsa üzerinde doğrulanamadı (izleme sürüyor)",
            }
        return {"accepted": True, "reason": "pozisyon kapatıldı"}

    async def _handle_hit(self, event: FollowerEvent) -> Dict[str, Any]:
        """TP/SL HIT olayları: borsa çapraz doğrulaması + GEREKİRSE KAPATMA.

        TERMİNAL olaylarda (SL HIT, TP3 HIT) borsada pozisyon HÂLÂ AÇIKSA bu
        bir telemetri notu değil, bir ARIZADIR: stop dolmamış ya da hiç
        konulamamıştır. Eski davranış yalnız WARNING'ti — AlgoPro "stop
        vuruldu" derken 100x'lik bir pozisyon korumasız taşınabiliyordu.
        Artık kalan miktar reduce-only MARKET ile KAPATILIR
        (``exit_reason=ALGOPRO_SL`` / ``ALGOPRO_TP3``).

        TERMİNAL OLMAYAN (TP1/TP2 HIT) olaylarda pozisyon açıksa merdiven
        emirleri kontrol edilir ve EKSİK olan TP bacakları yeniden konur
        (bkz. ``exits.ensure_tp_orders``).
        """
        symbol = event.symbol
        terminal = event.kind in (KIND_SL, KIND_TP3)
        async with self._entry_lock:
            sp = self.exits._positions.get(symbol)
            if sp is None:
                return {
                    "accepted": False,
                    "reason": "izlenen pozisyon yok (telemetri)",
                }

            try:
                pos_info = await self.client.get_position_risk(
                    symbol, force_fresh=True
                )
                amt = (
                    abs(float(pos_info.get("positionAmt", 0) or 0))
                    if pos_info
                    else 0.0
                )
            except Exception as exc:
                self.logger.warning(
                    f"⚠️ {symbol}: {event.kind} çapraz doğrulaması yapılamadı ({exc})"
                )
                return {"accepted": False, "reason": f"borsa okunamadı ({exc})"}

            if amt == 0:
                # KİMLİK KONTROLÜ (bulgu 9): bu await'ler sırasında başka bir
                # yol (flip/flatten/safety) pozisyonu bitirip YENİSİNİ
                # izlemeye almış olabilir. O hâlde BİZ hiçbir şey yapmadık —
                # `accepted: true` demek yanıltıcı olurdu.
                if self.exits._positions.get(symbol) is not sp:
                    return {
                        "accepted": False,
                        "reason": "izlenen pozisyon bu olay işlenirken değişti "
                        "(kapanış başka bir yolda işlendi)",
                    }
                await self.exits._handle_closed(symbol, sp)
                return {"accepted": True, "reason": "kapanış deftere işlendi"}

            if terminal:
                reason = "ALGOPRO_SL" if event.kind == KIND_SL else "ALGOPRO_TP3"
                self.logger.warning(
                    f"⚠️ {symbol}: AlgoPro {event.kind.upper()} bildirdi ama "
                    f"borsada pozisyon AÇIK (miktar={amt}) — stop dolmamış ya "
                    f"da konulamamış olabilir; kalan miktar reduce-only MARKET "
                    f"ile KAPATILIYOR ({reason})",
                    extra={"trade": True},
                )
                self._count_reject("terminal_hit_position_open")
                closed = await self._close_tracked(symbol, sp, reason=reason)
                if not closed:
                    return {
                        "accepted": False,
                        "reason": "AlgoPro kapanış bildirdi, borsada pozisyon "
                        "açık ve kapanış DOĞRULANAMADI (izleme sürüyor)",
                    }
                return {
                    "accepted": True,
                    "reason": f"AlgoPro {event.kind.upper()} — kalan miktar kapatıldı",
                }

            self.logger.info(
                f"📌 {symbol}: AlgoPro {event.kind.upper()} bildirdi "
                f"(kalan miktar={amt})"
            )
            # Merdiven emirleri hâlâ yerinde mi? Eksik bacak = o dilimin
            # AlgoPro hedefinde değil STOPTA kapanması demektir.
            try:
                await self.exits.ensure_tp_orders(symbol, sp)
            except Exception as exc:
                self.logger.error(
                    f"⚠️ {symbol}: eksik TP onarımı başarısız ({exc})"
                )
        return {"accepted": True, "reason": "telemetri kaydedildi"}

    # -- ortak kapanış yolu -------------------------------------------

    async def _submit_reduce_only_market_close(
        self, symbol: str, close_side: str, qty: float
    ) -> None:
        """Reduce-only MARKET kapanış — takipçinin TEK emir yolu.

        AlgoPro çıkışı, ters sinyal (flip) ve risk-olayı ``flatten`` AYNI
        çağrıyı kullanır (scalper motorundaki desenin birebir eşleniği).
        """
        await self.client._request_with_retry(
            "POST",
            "/fapi/v1/order",
            params={
                "symbol": symbol,
                "side": close_side,
                "type": "MARKET",
                "quantity": qty,
                "reduceOnly": "true",
                "newOrderRespType": "RESULT",
            },
            signed=True,
        )

    async def _close_tracked(self, symbol: str, sp: Any, *, reason: str) -> bool:
        """Pozisyonu reduce-only MARKET ile kapat ve borsada DOĞRULA.

        Doğrulanamazsa ``exits._handle_closed`` ASLA çağrılmaz (fail-closed —
        aksi halde SL/TP iptal edilip pozisyon korumasız kalabilirdi). Miktar
        CANLI ``positionAmt``'tan alınır; ``sp.position.quantity`` giriş
        dolumudur ve kısmi TP'lerden sonra güncellenmez.
        """
        try:
            pos_info = await self.client.get_position_risk(symbol, force_fresh=True)
        except Exception as exc:
            self.logger.error(f"❌ {symbol}: kapanış için pozisyon okunamadı ({exc})")
            return False
        amt = float(pos_info.get("positionAmt", 0) or 0) if pos_info else 0.0
        if amt == 0:
            await self.exits._handle_closed(symbol, sp, forced_exit_reason=reason)
            return True

        close_side = "SELL" if amt > 0 else "BUY"
        try:
            qty = await self.client.quantize_quantity(symbol, abs(amt))
            await self._submit_reduce_only_market_close(symbol, close_side, qty)
        except Exception as exc:
            # Emir reddi "pozisyon hâlâ açık" DEMEK DEĞİLDİR: TP3/SL aynı anda
            # dolduysa Binance reduce-only emri -2022 ile reddeder, oysa
            # pozisyon KAPANMIŞTIR. Bir kez taze okuyup gerçeği sor; hâlâ
            # açıksa fail-closed (False) — safety turu izlemeyi sürdürür.
            self.logger.error(f"❌ {symbol}: reduce-only kapanış gönderilemedi ({exc})")
            try:
                after = await self.client.get_position_risk(symbol, force_fresh=True)
                after_amt = (
                    abs(float(after.get("positionAmt", 0) or 0)) if after else 0.0
                )
            except Exception:
                return False
            if after_amt != 0:
                return False
            self.logger.warning(
                f"⚠️ {symbol}: kapanış emri reddedildi ama pozisyon zaten kapalı "
                f"(eşzamanlı TP/SL dolumu) — defter işleniyor"
            )
            await self.exits._handle_closed(symbol, sp, forced_exit_reason=reason)
            return True

        for delay in self._CLOSE_VERIFY_DELAYS:
            if delay:
                await asyncio.sleep(delay)
            try:
                pos_info = await self.client.get_position_risk(symbol, force_fresh=True)
            except Exception:
                continue
            amt2 = abs(float(pos_info.get("positionAmt", 0) or 0)) if pos_info else 0.0
            if amt2 == 0:
                await self.exits._handle_closed(
                    symbol, sp, forced_exit_reason=reason
                )
                self.logger.info(
                    f"🏁 {symbol}: {reason} — pozisyon reduce-only kapatıldı",
                    extra={"trade": True},
                )
                return True
        self.logger.error(
            f"❌ {symbol}: kapanış borsa üzerinde DOĞRULANAMADI ({reason}); "
            f"izleme sürüyor, SL/TP dokunulmadı"
        )
        return False

    # ------------------------------------------------------------------
    # Risk-olayı kanalı (D10 semantiği)
    # ------------------------------------------------------------------

    async def risk_event_halt(
        self, *, reason: str, source: Optional[str], ttl_minutes: int
    ) -> Dict[str, Any]:
        return self.halt.halt(reason=reason, source=source, ttl_minutes=ttl_minutes)

    def risk_event_resume(self) -> Dict[str, Any]:
        return self.halt.resume()

    def risk_event_status(self) -> Dict[str, Any]:
        snapshot = dict(self.halt.snapshot())
        snapshot["open_positions"] = len(self.exits.tracked_symbols())
        return snapshot

    async def risk_event_flatten(
        self, *, reason: str, source: Optional[str], ttl_minutes: int
    ) -> Dict[str, Any]:
        """Halt'ı ÖNCE kur, sonra tüm izlenen pozisyonları düzleştir (D10)."""
        halt_snapshot = self.halt.halt(
            reason=reason, source=source, ttl_minutes=ttl_minutes
        )
        flattened: List[str] = []
        errors: List[str] = []
        seen: Set[str] = set()
        # `_entry_lock` ZORUNLU: halt kurulduğu anda `_handle_entry` içinde
        # UÇUŞTA olan bir giriş (MARKET + SL + 3×TP, saniyeler sürer) HENÜZ
        # `tracked_symbols()`'a girmemiştir. Kilit alınmazsa flatten "hiç
        # pozisyon yok" raporu döner ve saniyeler sonra AKTİF HALT ALTINDA
        # açık bir pozisyon kalır (halt yalnız yeni girişi engeller, açığı
        # kapatmaz). Kilit `_close_tracked` tarafından ALINMADIĞI için
        # kilitlenme (deadlock) yoktur.
        async with self._entry_lock:
            for _ in range(2):  # 2. tur: savunma amaçlı (kilit altında boş kalır)
                remaining = sorted(self.exits.tracked_symbols() - seen)
                if not remaining:
                    break
                for symbol in remaining:
                    seen.add(symbol)
                    sp = self.exits._positions.get(symbol)
                    if sp is None:
                        continue
                    try:
                        closed = await self._close_tracked(
                            symbol, sp, reason="RISK_EVENT"
                        )
                    except Exception as exc:
                        message = f"{symbol}: {type(exc).__name__}: {exc}"
                        errors.append(message)
                        self.logger.error(
                            f"❌ takipçi flatten: {message}", exc_info=True
                        )
                        continue
                    if closed:
                        flattened.append(symbol)
                    else:
                        errors.append(
                            f"{symbol}: kapanış borsa üzerinde doğrulanamadı"
                        )

            # YETİMLER DE KAPATILIR (bulgu 8): izlenmeyen bir pozisyon
            # `tracked_symbols()`'a hiç girmez, bu yüzden yukarıdaki döngü
            # onu GÖRMEZ — "flatten" ise "hesapta pozisyon KALMASIN" demektir.
            orphan_flattened, orphan_errors = await self._flatten_orphans(seen)
            flattened.extend(orphan_flattened)
            errors.extend(orphan_errors)

        return {
            "flattened": flattened,
            "errors": errors,
            "halt": {**self.halt.snapshot(force=True),
                     "persisted": halt_snapshot.get("persisted", True)},
        }

    async def _flatten_orphans(
        self, seen: Set[str]
    ) -> Tuple[List[str], List[str]]:
        """İzlenmeyen (yetim) açık pozisyonları reduce-only MARKET ile kapat.

        `_close_tracked` kullanılamaz (izlenen bir `sp` nesnesi yoktur ve
        kapanış defteri yazılamaz); kapanış borsadan DOĞRULANIR ve doğrulama
        başarısızsa hata olarak raporlanır (fail-closed).

        **D20b (düşmanca inceleme):** gömülü modda hesap PAYLAŞILIR.
        `flatten` YALNIZ takipçinin kendi pozisyonlarını ve gerçekten YETİM
        saydıklarını kapatır; başka bir motorun (scalper / Telegram
        orchestrator) yönettiği ya da hiç kimseye ait olmayan (elle açılmış)
        pozisyonlara DOKUNMAZ — aksi halde operatörün "takipçiyi düzleştir"
        komutu sessizce Telegram defterini kapatırdı. Atlananlar loglanır.
        """
        flattened: List[str] = []
        errors: List[str] = []
        try:
            rows = await self.client.get_all_positions(force_fresh=True)
        except Exception as exc:
            errors.append(f"yetim taraması başarısız: {type(exc).__name__}: {exc}")
            return flattened, errors

        tracked = set(self.exits.tracked_symbols())
        # Gömülü modda: yabancı sahipli + sahipsiz semboller KORUNUR.
        # Ayrı halkada bu kümeler boştur → D20a davranışı birebir.
        protected: Set[str] = set()
        if self._embedded():
            protected = self._foreign_symbols() | self._foreign_tracked_symbols()
        skipped: List[str] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            try:
                amount = float(row.get("positionAmt", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not symbol or amount == 0 or symbol in tracked or symbol in seen:
                continue
            if self._embedded():
                # Gömülü modda YALNIZ takipçinin KENDİ pozisyonları kapatılır:
                # izlenenler (yukarıda `_close_tracked` ile) ve kendi
                # rezervasyonunu taşıyan yetimler. Yabancı ya da sahipsiz
                # (elle/Telegram) her pozisyona DOKUNULMAZ.
                is_mine = (
                    symbol_reservations.owner(symbol) == self._RESERVATION_OWNER
                )
                if not is_mine or symbol in protected:
                    skipped.append(symbol)
                    continue
            elif symbol in protected:
                skipped.append(symbol)
                continue
            seen.add(symbol)
            close_side = "SELL" if amount > 0 else "BUY"
            try:
                qty = await self.client.quantize_quantity(symbol, abs(amount))
                await self._submit_reduce_only_market_close(symbol, close_side, qty)
            except Exception as exc:
                errors.append(f"{symbol} (yetim): {type(exc).__name__}: {exc}")
                self.logger.error(
                    f"❌ takipçi flatten: yetim {symbol} kapatılamadı ({exc})",
                    exc_info=True,
                )
                continue
            closed = False
            for delay in self._CLOSE_VERIFY_DELAYS:
                if delay:
                    await asyncio.sleep(delay)
                try:
                    info = await self.client.get_position_risk(
                        symbol, force_fresh=True
                    )
                except Exception:
                    continue
                amount_after = (
                    abs(float(info.get("positionAmt", 0) or 0)) if info else 0.0
                )
                if amount_after == 0:
                    closed = True
                    break
            if closed:
                flattened.append(symbol)
                self.logger.warning(
                    f"🏁 {symbol}: YETİM pozisyon risk-olayı flatten ile kapatıldı "
                    f"(defter satırı YOK — Binance income'dan elle doğrulanmalı)",
                    extra={"trade": True},
                )
            else:
                errors.append(
                    f"{symbol} (yetim): kapanış borsa üzerinde doğrulanamadı"
                )
        if skipped:
            self.logger.critical(
                f"🛑 takipçi flatten: {sorted(set(skipped))} DOKUNULMADI — bu "
                f"pozisyonlar takipçinin DEĞİL (başka motorun ya da elle "
                f"açılmış). Hesap TAMAMEN düz DEĞİLDİR; onları kapatmak için "
                f"ilgili motorun kendi flatten'ını çalıştırın ya da elle "
                f"kapatın (D20b).",
                extra={"trade": True},
            )
        return flattened, errors

    # ------------------------------------------------------------------
    # Durum / sağlık
    # ------------------------------------------------------------------

    def health_snapshot(self) -> Dict[str, Any]:
        safety_age = (
            time.monotonic() - self._safety_last_success_monotonic
            if self._safety_last_success_monotonic is not None
            else None
        )
        safety_limit = max(15.0, self._safety_interval_seconds() * 10.0)
        safety_healthy = safety_age is not None and safety_age <= safety_limit
        healthy = bool(
            self.running
            and safety_healthy
            and self._safety_task is not None
            and not self._safety_task.done()
        )
        return {
            "healthy": healthy,
            "running": self.running,
            "mode": "follower",
            "exchange_ready": self._exchange_ready,
            "recovery_ready": self._recovery_ready,
            "risk_ready": self._risk_ready,
            "entry_halted": self._entry_halted,
            "kill_switch": self._kill_switch,
            "safety_age_seconds": round(safety_age, 2) if safety_age else None,
            "safety_last_error": self._safety_last_error,
            "exchange_last_error": self._exchange_last_error,
        }

    def dashboard_snapshot(self) -> Dict[str, Any]:
        """Panonun "AlgoPro Takipçi" kartı için KÜÇÜK özet (D20b).

        `/api/status` gövdesine gömülür — pano AYRI bir yoklama yapmaz ve bu
        yol HİÇ REST çağrısı üretmez (2026-08-18 pano-açlığı dersi: panonun
        her tikte taze borsa okuması istemesi rate-limiter'ı doyurup tarama
        döngüsünü aç bırakmıştı).
        """
        full = self.snapshot()
        rejects = full.get("reject_counters") or {}
        events = full.get("event_counters") or {}
        return {
            "running": full.get("running"),
            "embedded": full.get("embedded"),
            "forward_bridge_conflict": full.get("forward_bridge_conflict"),
            "universe": full.get("universe"),
            "reserved_symbols": full.get("reserved_symbols"),
            "timeframe": full.get("timeframe"),
            "max_positions": full.get("max_positions"),
            "entries_ready": full.get("entries_ready"),
            "entry_block_reason": full.get("entry_block_reason"),
            "kill_switch_active": full.get("kill_switch_active"),
            "entry_halted": full.get("entry_halted"),
            "entry_halt_reason": full.get("entry_halt_reason"),
            "orphan_positions": full.get("orphan_positions"),
            "unknown_positions": full.get("unknown_positions"),
            "virtual_ledger": full.get("virtual_ledger"),
            "daily_pnl": full.get("daily_pnl"),
            "daily_pnl_source": full.get("daily_pnl_source"),
            "daily_loss_threshold_usdt": full.get("daily_loss_threshold_usdt"),
            "daily_loss_limit_pct": full.get("daily_loss_limit_pct"),
            "positions": full.get("positions"),
            "sizing": full.get("sizing"),
            # "durum": kaç alarm olayı geldi, en sonuncusu ne zaman.
            "events_total": sum(int(v or 0) for v in events.values()),
            "event_counters": dict(events),
            "last_event_at": full.get("last_event_at"),
            # Komisyon kapısı ret sayacı (D20a bulgu 3) panoda görünür.
            "fee_gate_rejects": int(rejects.get("fee_gate", 0) or 0),
            "reject_counters": dict(rejects),
        }

    @staticmethod
    def _position_roi_pct(sp: Any) -> Optional[float]:
        """Açık pozisyonun MARJ ROI'si (%) — YENİ REST ÇAĞRISI YOK.

        `current_price` safety turunda zaten tazelenir (`exits.step`); pano
        bu bellek değerini okur, kendi başına fiyat sormaz (2026-08-18
        pano-açlığı dersi).
        """
        try:
            entry = float(sp.position.entry_price or 0.0)
            last = float(sp.position.current_price or 0.0)
            lev = int(sp.position.leverage or 1) or 1
            if entry <= 0 or last <= 0:
                return None
            move = (last - entry) / entry * 100.0
            if sp.signal.direction != Direction.LONG:
                move = -move
            return round(move * lev, 2)
        except Exception:
            return None

    def snapshot(self) -> Dict[str, Any]:
        positions: List[Dict[str, Any]] = []
        for symbol, sp in self.exits._positions.items():
            plan_meta = getattr(sp, "meta", {}).get("plan", {}) if hasattr(sp, "meta") else {}
            positions.append(
                {
                    "symbol": symbol,
                    "direction": sp.signal.direction.value,
                    "entry_price": sp.position.entry_price,
                    "current_price": sp.position.current_price,
                    "roi_pct": self._position_roi_pct(sp),
                    "quantity": sp.position.quantity,
                    "leverage": sp.position.leverage,
                    "stop_loss": sp.position.current_stoploss,
                    "tp1": sp.plan.tp1_price,
                    "tp2": sp.plan.tp2_price,
                    "tp3": getattr(sp.plan, "tp3_price", 0.0),
                    # `tp1_filled` = TP1 GERÇEK dolumu kanıtlandı;
                    # `tp1_done` = stop break-even'e taşındı (ücret-farkında
                    # BE ulaşılamıyorsa False kalır — D20 "ücret eşiği").
                    "tp1_filled": bool(getattr(sp, "tp1_filled", False)),
                    "tp1_done": sp.tp1_done,
                    "tp2_done": sp.tp2_done,
                    "tp3_done": bool(getattr(sp, "tp3_done", False)),
                    "sl_pct": plan_meta.get("sl_pct"),
                    "sl_pct_fill": plan_meta.get("sl_pct_fill"),
                    "sl_roi_pct": plan_meta.get("sl_roi_pct"),
                    "tp1_roi_pct": (plan_meta.get("tp_roi_pct") or [None])[0],
                    # Ücret eşiği (D20): TP1 ROI'si gidiş-dönüş komisyonu
                    # karşılıyor mu? False = yapısal negatif beklenti adayı.
                    "fee_roi_pct": plan_meta.get("fee_roi_real_pct"),
                    "tp1_covers_fees": plan_meta.get("tp1_covers_fees_real"),
                    "margin_usdt": plan_meta.get("margin_usdt"),
                    "levels_source": (plan_meta.get("levels") or {}).get("source"),
                    "trade_id": sp.trade_id,
                    "opened_at": sp.position.opened_at.isoformat()
                    if sp.position.opened_at
                    else None,
                }
            )

        halt_snapshot = self.halt.snapshot()
        return {
            "mode": "follower",
            "strategy": FOLLOWER_STRATEGY,
            "running": self.running,
            "health": self.health_snapshot(),
            "entries_ready": self._entries_ready(),
            "entry_block_reason": self._entry_block_reason(),
            "universe": sorted(self.symbol_allowlist()),
            "timeframe": str(getattr(self.cfg, "follower_timeframe", "1")),
            "max_positions": int(getattr(self.cfg, "follower_max_positions", 4)),
            "positions": positions,
            "cooldowns": self.executor.cooldown_snapshot(),
            "kill_switch_active": self._kill_switch,
            "daily_pnl": self._daily_pnl,
            "daily_pnl_source": (
                "ap_ledger"
                if bool(getattr(self.cfg, "follower_embedded", False))
                else "binance_account_income"
            ),
            "daily_loss_threshold_usdt": self._daily_loss_threshold_usdt,
            "daily_loss_limit_pct": float(
                getattr(self.cfg, "follower_daily_loss_limit_pct", 0.0) or 0.0
            ),
            "risk_equity_usdt": self._risk_equity_usdt,
            # D20b sanal defter (gömülü mod). `enabled=False` → bugünkü
            # davranış: boyutlama ve günlük kesici gerçek bakiyeye bakar.
            "virtual_ledger": {
                "enabled": self._virtual_capital_base() > 0,
                "base_usdt": self._virtual_capital_base(),
                "equity_usdt": self._virtual_equity_usdt,
                "realized_pnl_usdt": self._virtual_realized_pnl,
                "exchange_available_usdt": self._exchange_available_usdt,
                "margin_per_trade_usdt": (
                    None
                    if self._virtual_equity_usdt is None
                    else self._virtual_equity_usdt
                    * float(getattr(self.cfg, "follower_margin_pct", 10.0) or 0.0)
                    / 100.0
                ),
            },
            "embedded": bool(getattr(self.cfg, "follower_embedded", False)),
            # Gömülü modda HTTP köprüsü HİÇ çağrılmaz. Bu bayrak doluysa ayrı
            # halka (tradingbot_ap) alarmsız kalmış demektir — startup CRITICAL
            # loglar, operatör burada da görür (düşmanca inceleme).
            "forward_bridge_conflict": bool(
                getattr(self.cfg, "follower_embedded", False)
                and str(getattr(self.cfg, "follower_forward_url", "") or "").strip()
            ),
            "reserved_symbols": sorted(
                getattr(self.cfg, "follower_reserved_symbols", []) or []
            ),
            "symbol_reservations": symbol_reservations.snapshot(),
            "risk_event_halt": {
                "active": halt_snapshot.get("active"),
                "reason": halt_snapshot.get("reason"),
                "until_ts": halt_snapshot.get("until_ts"),
            },
            "entry_halted": self._entry_halted,
            "entry_halt_reason": self._entry_halt_reason,
            "sizing": {
                "margin_pct": float(getattr(self.cfg, "follower_margin_pct", 10.0)),
                # `sl_margin_pct` (yeni ad) ve `sl_roi_target` (eski ad) AYNI
                # büyüklüktür; config startup'ta eşitler (bkz. D20b).
                "sl_margin_pct": float(
                    getattr(
                        self.cfg,
                        "follower_sl_margin_pct",
                        getattr(self.cfg, "follower_sl_roi_target", 30.0),
                    )
                ),
                "sl_roi_target": float(
                    getattr(self.cfg, "follower_sl_roi_target", 30.0)
                ),
                "lev_min": int(getattr(self.cfg, "follower_lev_min", 3)),
                "lev_max": int(getattr(self.cfg, "follower_lev_max", 100)),
                "liq_guard_pct": float(
                    getattr(self.cfg, "follower_lev_liq_guard_pct", 50.0)
                ),
                "tp_rr": [
                    float(getattr(self.cfg, "follower_tp_rr1", 0.5)),
                    float(getattr(self.cfg, "follower_tp_rr2", 1.0)),
                    float(getattr(self.cfg, "follower_tp_rr3", 1.5)),
                ],
                # Ücret eşiği kapısı — VARSAYILAN 1.0 (AÇIK). 0 = kapalı
                # (kullanıcı kararı; bkz. docs/DECISIONS.md D20/D20a).
                "min_tp1_fee_ratio": float(
                    getattr(self.cfg, "follower_min_tp1_fee_ratio", 1.0)
                ),
                "max_signal_drift_pct": float(
                    getattr(self.cfg, "follower_max_signal_drift_pct", 0.0)
                ),
                "max_event_age_sec": float(
                    getattr(self.cfg, "follower_max_event_age_sec", 20.0)
                ),
            },
            # Borsada açık ama izlenmeyen pozisyonlar (bulgu 8). Boş liste =
            # borsa gerçeği ile izleme listesi UYUŞUYOR.
            "orphan_positions": list(self._orphans),
            # D20b: hiçbir motorun izlemediği/rezerve etmediği CANLI
            # pozisyonlar. YETİM DEĞİLDİR (elle/Telegram açılmış olabilir):
            # entry-halt kurmaz, flatten kapsamına GİRMEZ — yalnız görünürlük.
            "unknown_positions": list(self._unknown_positions),
            "orphans_checked_at": self._orphans_checked_at,
            "orphans_check_ok": bool(self._orphans_check_ok),
            "tp_repair": (
                self.exits.tp_repair_snapshot()
                if hasattr(self.exits, "tp_repair_snapshot")
                else {}
            ),
            "brackets": self.brackets.snapshot(),
            "events": list(self._events),
            "event_counters": dict(self._event_counters),
            "reject_counters": {
                **self._reject_counters,
                **self.executor.reject_snapshot(),
            },
            "last_event_at": self._last_event_at,
        }
