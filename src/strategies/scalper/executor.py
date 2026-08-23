"""
Scalper pozisyon açma motoru.

PositionManager'ın bugün elden geçirilmiş güvenlik akışını (gerçek dolum
çözümü, SL konulamazsa acil kapatma) public sarmalayıcılar üzerinden AYNEN
yeniden kullanır; üstüne kendi risk bazlı boyutlama ve TP merdiveni mantığını
kurar. Hiçbir güvenlik deseni burada yeniden yazılmaz.

Akış (try_open, "taker" modu — varsayılan):
  1. Bakiye sorgusu (None/<=0 → vazgeç)
  2. Stop mesafesi risk kapısı ([min_stop_pct, max_stop_pct])
  3. R:R kapısı (beklenen harman getiri / SL riski >= scalper_min_rr)
  4. Risk bazlı boyutlama + nominal tavan kırpma
  5. Yuvarlama + borsa filtresi doğrulaması
  6. Margin type + leverage (emirden ÖNCE — hata zararsız)
  7. Market emri (bu noktadan sonra pozisyon GERÇEK olabilir)
  8. Gerçek dolum çözümü (pm.resolve_fill)
  9. Stop-loss (pm.place_stop_loss_or_close — başarısızsa pozisyon zaten kapatıldı)
 10. TP1/TP2 merdiveni (başarısızlık pozisyonu iptal ettirmez — SL var)
 11. PositionModel + DB kaydı + tracker + ExitPlan

"maker" modu (settings.scalper_entry_mode == "maker") — İKİ FAZLI GİRİŞ:
  Backtest'te limit giriş her strateji varyantının PF'sini iyileştirdiği
  için canlıya taşındı. 1-6 adımları AYNEN yukarıdaki gibi çalışır; adım 7
  MARKET yerine LIMIT GTX (post-only) emri kor ve PendingEntry olarak kaydeder (try_open
  bu modda None döner, pozisyon HENÜZ yok). Dolum takibi engine'in her
  turunda çağırdığı check_pending() ile yapılır: FILLED olduğunda 9-11
  adımları (SL → TP merdiveni → kayıt) GERÇEK dolum fiyatından çalışır —
  bu adımlar _finalize_position() içinde iki giriş yolunun ORTAK kod yolu
  olarak paylaşılır (taker davranışı BİREBİR korunur).

Gölge modu (settings.scalper_shadow_mode == True, D14, docs/MAINNET_PLAN.md §3):
  1-5 adımları AYNEN çalışır (cooldown, bakiye, stop/R:R kapıları, boyutlama,
  borsa filtresi doğrulaması — leverage/margin bu GERÇEK hesaplamadan gelir).
  Adım 6'dan (margin/leverage ayarı) ÖNCE sinyal scalp_trades'e status="SHADOW"
  olarak kaydedilir ve try_open None döner — borsaya HİÇBİR istek gitmez (margin/
  leverage ayarı YAPILMAZ, emir GÖNDERİLMEZ, SL/TP YOKTUR, pozisyon izlenmez).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.logger import app_logger
from src.models.position import PositionModel, PositionStatus, PositionSide
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Direction,
    ExitPlan,
    Regime,
    ScalpSignal,
    StrategyContext,
    fee_aware_breakeven_price,
    price_at_roi,
)
from src.trading.binance_client_improved import (
    ImprovedBinanceClient,
    BinanceAPIError,
)
from src.trading.position_manager import PositionManager, UnprotectedPositionError


@dataclass
class ScalpPosition:
    """Executor'ın kurduğu, exits.py ve engine.py tarafından taşınan canlı kayıt.

    mae_pct/mfe_pct exits.py tarafından her turda mark fiyatına göre güncellenir
    ve kapanışta tracker.record_close'a aktarılır (ROI yüzdesi cinsinden).
    """
    trade_id: int
    signal: ScalpSignal
    position: PositionModel          # pm akışının ürettiği kayıt DEĞİL — executor kurar
    plan: ExitPlan                   # types.ExitPlan
    entry_candle_time: int           # chandelier since hesabı için (ms)
    tp1_done: bool = False
    tp2_done: bool = False
    trailing_active: bool = False
    mae_pct: float = 0.0             # en kötü (olumsuz) ROI% ucu — negatif veya 0
    mfe_pct: float = 0.0             # en iyi (olumlu) ROI% ucu — pozitif veya 0
    # `position.current_price`ın en son BAŞARIYLA okunduğu an (monotonic).
    # D19a-2: `position.current_price` yalnız `get_current_price` başarılı
    # olduğunda güncellenir ve bir zaman damgası taşımaz — ticker birkaç tur
    # hata verirse alan BAYAT kalır. "Pozisyon kârda mı" kararı bayat fiyatla
    # verilirse stop piyasanın ters tarafına gider (-2021 → acil kapanış), ki
    # `breakeven_side_ok` tam da bunu engellemek için yazıldı. None/eski damga
    # = "bilinmiyor" (fail-safe).
    price_ts: Optional[float] = None

    # --- İşlem adli kaydı (D21) — YALNIZ GÖZLEM ---------------------------
    # Bu alanların HİÇBİRİ bir karar yolunda okunmaz; çıkış zaman çizgisini
    # (giriş → TP1 → BE → trailing → çıkış) kapanışta yeniden kurabilmek
    # içindirler. Varsayılanları bugünkü davranışı birebir korur.
    forensics_entry: Optional[Dict[str, Any]] = None
    opened_epoch: Optional[float] = None      # giriş anı (unix, UTC)
    tp1_at: Optional[str] = None              # TP1 doğrulandığı an (ISO UTC)
    tp2_at: Optional[str] = None
    be_at: Optional[str] = None               # stop BE'ye çekildiği an
    be_price: Optional[float] = None
    trail_updates: int = 0                    # gönderilen trailing güncellemesi
    last_trail_stop: Optional[float] = None
    # Restart sonrası `exits.recover()` ile kurtarıldıysa True: bellekteki
    # zaman çizgisi damgaları (TP1/BE anı, trailing sayacı) restart ÖNCESİNİ
    # KAPSAMAZ. Kapanış belgesi bunu `path.restart_gap=true` ile bildirir ve
    # bilinmeyen damgaları `null` bırakır — uydurma değer yazılmaz (D21-R3).
    forensics_restart_gap: bool = False


@dataclass
class PendingEntry:
    """Maker modunda bekleyen (henüz dolmamış) LIMIT giriş emri.

    client_order_id, POST yanıtı kaybolsa bile aynı emir niyetini borsada
    sorgulayabilmek için kalıcı kimliktir. order_id uzlaştırma tamamlanana
    kadar None olabilir. PARTIALLY_FILLED görüldüğü anda kalan miktar iptal
    edilir ve gerçekleşen miktar derhal SL/TP ile korunur; kısmi dolum asla
    süresiz bekletilmez.
    """
    signal: ScalpSignal
    order_id: Optional[int]
    client_order_id: str
    limit_price: float
    quantity: float
    created_monotonic: float
    created_at_ms: int = 0
    expires_at_ms: int = 0
    phase: str = "INTENT"
    last_status: Optional[str] = None
    executed_qty: float = 0.0
    avg_price: float = 0.0
    scans_waited: int = 0


@dataclass(frozen=True)
class _FailedExecutionLedger:
    """İlk koruma başarısızlığından sonra uzlaştırılan tek execution özeti."""

    exit_price: float
    realized_pnl: float
    pnl_source: str
    notes: str


class _MakerOrderStateUnknown(Exception):
    """LIMIT POST sonucu ile sorgu sonucu birlikte belirsiz kaldı.

    Bu durumda aynı clientOrderId ile niyet pending tutulur; yeni bir POST
    gönderilmez. Böylece cevap kaybı mükerrer pozisyona dönüşmez.
    """


class PendingRecoveryError(UnprotectedPositionError):
    """Kalıcı maker niyeti güvenle uzlaştırılmadan işlem açılamaz.

    UnprotectedPositionError alt sınıfıdır; engine'in mevcut global güvenlik
    latch'i bu hatayı da fail-closed yakalar. Journal bozulması, borsa
    durumunun belirsiz kalması veya koruma/DB tamamlanmadan restart edilmesi
    bu sınıfla yüzeye çıkar.
    """


class ScalpExecutor:
    """Scalper sinyalinden güvenli, korumalı bir pozisyon açar."""

    # userTrades, matching-engine cevabından kısa süre sonra görünür olabilir.
    # Pozisyon bu noktada zaten flat olduğundan 2.5 sn toplam bounded bekleme,
    # gerçek fee ledger'ı kaybetmekten daha güvenlidir.
    FAILED_LEDGER_RETRY_DELAYS = (0.0, 0.25, 0.75, 1.5)

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
    ):
        self.client = client
        self.pm = pm
        self.tracker = tracker
        self.cfg = cfg
        self.logger = app_logger
        self._pending: Dict[str, PendingEntry] = {}
        self._pending_lock = asyncio.Lock()
        # İlk SL'nin kurulamadığı bir sembol hemen yeniden denenmez. Bu latch
        # process ömrü boyunca en az 60 dakika sürer; kalıcı global entry halt
        # gerektirecek belirsizlikler zaten PendingRecoveryError yolundadır.
        self._cooldowns: Dict[str, Dict[str, Any]] = {}
        # Gölge modu tekilleştirme penceresi (D14 adversarial review, HIGH):
        # shadow dalı hiçbir occupancy bırakmadığı için aynı sinyal her tarama
        # turunda yeniden yazılıyordu (2-5x şişme). _cooldowns'a KASITLI
        # DOKUNMADAN ayrı bir map: sembol -> son gölge kaydının epoch saniyesi.
        # Gerçek girişlerin cooldown semantiğini etkilemez (test_cooldown_
        # not_started_by_shadow_entry hâlâ _cooldowns == {} bekler).
        self._shadow_recent: Dict[str, float] = {}
        # D21 adli kayıt: maker modunda giriş bağlamı, dolum anına kadar
        # bellekte bekler. Değer biçimi:
        #   sembol -> {"key": "SEMBOL|YÖN|created_at_ms", "context": {...}}
        # `key` emrin kimliğidir ve dolumda YENİDEN hesaplanıp karşılaştırılır
        # (D21-R3, bulgu 4): yetim bir bağlam başka bir sinyalin dolumuna
        # iliştirilemez. Kalıcı DEĞİLDİR — restart'ta kayıt eksik kalır,
        # işlem akışı etkilenmez.
        self._pending_forensics: Dict[str, Dict[str, Any]] = {}
        self._reject_counters: Dict[str, int] = {}
        # Adli kayıt kurulumu bir kez uyarır (gözlem asla girişi engellemez).
        self._forensics_error_logged: bool = False
        self._last_sizing_snapshot: Dict[str, Any] = {
            "mode": "uninitialized",
            "exchange_available": None,
            "virtual_capital": None,
            "eligible_realized_pnl": None,
            "effective_equity": None,
            "start_trade_id": None,
            "updated_at": None,
        }

        # Gerçek Settings bu alanı varsayılan olarak tanımlar. Minimal/fake
        # test cfg'sinde alan YOKSA persistence bilinçli olarak kapalıdır.
        configured_path = getattr(cfg, "scalper_pending_journal_path", None)
        self._journal_path: Optional[Path] = (
            Path(configured_path).expanduser() if configured_path else None
        )
        self._journal_records: Dict[str, Dict[str, Any]] = {}
        self._journal_error: Optional[str] = None
        self._recovery_needed = False
        self._load_journal()

        # Sembol cooldown'ları restart'a dayanmalı: SL'den 2 dk sonra restart
        # edilen bot aynı düşen bıçağa anında geri girmemeli (2026-08-11 BEAT).
        # Journal ile aynı ilke: test cfg'sinde alan yoksa persistence kapalı.
        cooldown_path = getattr(cfg, "scalper_cooldown_state_path", None)
        self._cooldown_state_path: Optional[Path] = (
            Path(cooldown_path).expanduser() if cooldown_path else None
        )
        self._load_cooldowns()

    # ------------------------------------------------------------------
    # Kalıcı maker intent journal'ı.
    # ------------------------------------------------------------------

    def _load_journal(self) -> None:
        path = self._journal_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("journal version/root geçersiz")
            entries = raw.get("entries")
            if not isinstance(entries, dict):
                raise ValueError("journal entries nesne değil")
            validated: Dict[str, Dict[str, Any]] = {}
            for symbol, record in entries.items():
                if not isinstance(symbol, str) or not isinstance(record, dict):
                    raise ValueError("journal entry şeması geçersiz")
                if record.get("symbol") != symbol:
                    raise ValueError(f"journal sembol anahtarı uyuşmuyor: {symbol}")
                client_id = record.get("client_order_id")
                signal = record.get("signal")
                if not isinstance(client_id, str) or not client_id:
                    raise ValueError(f"{symbol}: client_order_id eksik")
                if not isinstance(signal, dict):
                    raise ValueError(f"{symbol}: signal eksik")
                validated[symbol] = dict(record)
            self._journal_records = validated
            self._recovery_needed = bool(validated)
        except Exception as e:
            self._journal_error = f"{type(e).__name__}: {e}"
            self.logger.critical(
                f"🚨 Maker pending journal okunamadı ({path}): {self._journal_error}. "
                "Yeni maker girişleri recovery tamamlanana kadar KAPALI."
            )

    def _assert_recovery_ready(self) -> None:
        if self._journal_error:
            raise PendingRecoveryError(
                f"Maker pending journal bozuk/okunamıyor: {self._journal_error}"
            )
        if self._recovery_needed:
            raise PendingRecoveryError(
                "Restart sonrası maker pending journal henüz uzlaştırılmadı; "
                "önce recover_pending() çalıştırılmalı"
            )

    def _atomic_write_journal(self, records: Dict[str, Dict[str, Any]]) -> None:
        path = self._journal_path
        if path is None:
            return
        if self._journal_error:
            raise PendingRecoveryError(
                f"Maker pending journal kullanılamıyor: {self._journal_error}"
            )

        tmp_path: Optional[Path] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            payload = {"version": 1, "entries": records}
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            # Rename'in kendisini de mümkün olduğunca kalıcılaştır. Bazı
            # platform/filesystem'lerde dizin fsync desteklenmeyebilir.
            try:
                dir_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except PendingRecoveryError:
            raise
        except Exception as e:
            self._journal_error = f"{type(e).__name__}: {e}"
            raise PendingRecoveryError(
                f"Maker pending journal atomik yazılamadı ({path}): {e}"
            ) from e
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _serialize_signal(signal: ScalpSignal) -> Dict[str, Any]:
        return {
            "strategy": signal.strategy,
            "symbol": signal.symbol,
            "direction": signal.direction.value,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "reason": signal.reason,
            "regime": signal.regime.value,
            "atr_5m": signal.atr_5m,
            "score": signal.score,
            "risk_multiplier": signal.risk_multiplier,
        }

    @staticmethod
    def _deserialize_signal(raw: Dict[str, Any]) -> ScalpSignal:
        try:
            return ScalpSignal(
                strategy=str(raw["strategy"]),
                symbol=str(raw["symbol"]),
                direction=Direction(str(raw["direction"])),
                entry_price=float(raw["entry_price"]),
                stop_price=float(raw["stop_price"]),
                reason=str(raw["reason"]),
                regime=Regime(str(raw["regime"])),
                atr_5m=float(raw["atr_5m"]),
                score=float(raw.get("score", 0.0)),
                risk_multiplier=float(raw.get("risk_multiplier", 1.0)),
            )
        except Exception as e:
            raise PendingRecoveryError(f"Journal signal kaydı geçersiz: {e}") from e

    def _serialize_pending(self, pending: PendingEntry) -> Dict[str, Any]:
        return {
            "symbol": pending.signal.symbol,
            "signal": self._serialize_signal(pending.signal),
            "client_order_id": pending.client_order_id,
            "order_id": pending.order_id,
            "limit_price": pending.limit_price,
            "quantity": pending.quantity,
            "created_at_ms": pending.created_at_ms,
            "expires_at_ms": pending.expires_at_ms,
            "phase": pending.phase,
            "last_status": pending.last_status,
            "executed_qty": pending.executed_qty,
            "avg_price": pending.avg_price,
        }

    def _deserialize_pending(self, record: Dict[str, Any]) -> PendingEntry:
        signal = self._deserialize_signal(record["signal"])
        now_ms = int(time.time() * 1000)
        created_at_ms = int(record.get("created_at_ms") or now_ms)
        elapsed_seconds = max(0.0, (now_ms - created_at_ms) / 1000.0)
        order_id = record.get("order_id")
        return PendingEntry(
            signal=signal,
            order_id=int(order_id) if order_id is not None else None,
            client_order_id=str(record["client_order_id"]),
            limit_price=float(record["limit_price"]),
            quantity=float(record["quantity"]),
            created_monotonic=time.monotonic() - elapsed_seconds,
            created_at_ms=created_at_ms,
            expires_at_ms=int(record.get("expires_at_ms") or now_ms),
            phase=str(record.get("phase") or "INTENT"),
            last_status=(
                str(record["last_status"])
                if record.get("last_status") is not None
                else None
            ),
            executed_qty=max(0.0, float(record.get("executed_qty") or 0.0)),
            avg_price=max(0.0, float(record.get("avg_price") or 0.0)),
        )

    def _store_pending_record(self, pending: PendingEntry) -> None:
        if self._journal_path is None:
            return
        records = dict(self._journal_records)
        records[pending.signal.symbol] = self._serialize_pending(pending)
        self._atomic_write_journal(records)
        self._journal_records = records

    def _remove_pending_record(self, symbol: str) -> None:
        if self._journal_path is None:
            return
        records = dict(self._journal_records)
        records.pop(symbol, None)
        self._atomic_write_journal(records)
        self._journal_records = records

    def _drop_pending(self, symbol: str, pending: PendingEntry) -> None:
        """Journal'ı ÖNCE temizle; başarılı olursa RAM kaydını düşür."""
        self._remove_pending_record(symbol)
        if self._pending.get(symbol) is pending:
            self._pending.pop(symbol, None)
        # D21: dolmayan/iptal edilen niyetin adli bağlamı birikmesin.
        # (Dolum yolunda bağlam zaten `pop` ile tüketilmiştir.)
        forensics_map = getattr(self, "_pending_forensics", None)
        if forensics_map is not None:
            forensics_map.pop(str(symbol).upper(), None)

    def _record_order_state(
        self, pending: PendingEntry, order: Dict[str, Any], *, phase: Optional[str] = None
    ) -> None:
        changed = False
        order_id = order.get("orderId")
        if order_id is not None and pending.order_id != int(order_id):
            pending.order_id = int(order_id)
            changed = True
        status = order.get("status")
        if status is not None and pending.last_status != str(status):
            pending.last_status = str(status)
            changed = True
        executed = self._executed_qty(order)
        if executed > pending.executed_qty:
            pending.executed_qty = executed
            changed = True
        try:
            avg_price = max(0.0, float(order.get("avgPrice") or 0.0))
        except (TypeError, ValueError):
            avg_price = 0.0
        if avg_price > 0 and avg_price != pending.avg_price:
            pending.avg_price = avg_price
            changed = True
        if phase is not None and pending.phase != phase:
            pending.phase = phase
            changed = True
        if changed:
            self._store_pending_record(pending)

    def pending_symbols(self) -> Set[str]:
        """Bekleyen (henüz dolmamış) maker girişlerinin sembol kümesi.

        Engine bu kümeyi tur içinde sembol atlama mantığına ekler — aynı
        sembole ikinci bir sinyal, ilk giriş dolmadan/iptal olmadan işlenmez.
        """
        return set(self._pending.keys())

    def pending_snapshot(self) -> List[Dict[str, Any]]:
        """API için: bekleyen maker girişlerinin anlık görüntüsü."""
        return [
            {
                "symbol": symbol,
                "order_id": pending.order_id,
                "client_order_id": pending.client_order_id,
                "limit_price": pending.limit_price,
                "scans_waited": pending.scans_waited,
            }
            for symbol, pending in self._pending.items()
        ]

    @property
    def last_sizing_equity(self) -> Optional[float]:
        """Son giriş denemesinde kullanılan etkin özsermaye (telemetry)."""
        value = self._last_sizing_snapshot.get("effective_equity")
        return float(value) if value is not None else None

    def sizing_snapshot(self) -> Dict[str, Any]:
        """Borsa/sanal sermaye kırpmasının son güvenli anlık görüntüsü."""
        return dict(self._last_sizing_snapshot)

    async def get_sizing_equity(self) -> Optional[float]:
        """Startup/kill-switch için özsermayeyi giriş açmadan hazırla."""
        try:
            exchange_available = await self.client.get_account_balance()
        except Exception as exc:
            self.logger.error(f"❌ Scalper sizing bakiyesi okunamadı ({exc})")
            return None
        if exchange_available is None or float(exchange_available) <= 0:
            return None
        return await self._resolve_sizing_equity(float(exchange_available))

    def _resolve_leverage(self, signal: Any) -> int:
        """Sinyalin coin-bazlı dinamik kaldıracı; yoksa global cfg kaldıracı.

        apply_stop_policy fixed_roi modunda volatiliteye göre çözer ve sinyale
        yazar — stop mesafesi o kaldıraçla hesaplandığı için TP fiyatları,
        boyutlama ve borsa set_leverage AYNI değeri kullanmak ZORUNDA.
        """
        try:
            lev = int(getattr(signal, "leverage", None) or 0)
        except (TypeError, ValueError):
            lev = 0
        return lev if lev > 0 else int(self.cfg.scalper_leverage)

    def _count_reject(self, reason: str) -> None:
        """Sessiz durma görünür olsun: kapı retlerini süreç-ömrü sayaçla izle.

        'Bot healthy görünüyor ama hiç işlem açmıyor' durumunda dashboard'dan
        HANGİ kapının reddettiği okunabilmeli (2026-08-12 inceleme bulgusu).
        """
        self._reject_counters[reason] = self._reject_counters.get(reason, 0) + 1

    def reject_snapshot(self) -> Dict[str, int]:
        """API/dashboard için kapı ret sayaçları (süreç başlangıcından beri)."""
        return dict(self._reject_counters)

    def _prune_cooldowns(self) -> None:
        now = time.time()
        for symbol, state in list(self._cooldowns.items()):
            if float(state.get("expires_at") or 0.0) <= now:
                self._cooldowns.pop(symbol, None)
        # Gölge tekilleştirme penceresi de burada budanır (D14 review): süresi
        # geçen kayıtlar map'te sonsuza dek birikmesin, shadow_active_count()
        # bugünkü pencereyi yansıtsın.
        hold = self._shadow_dedup_seconds()
        for symbol, last in list(self._shadow_recent.items()):
            if (now - last) >= hold:
                self._shadow_recent.pop(symbol, None)

    def _load_cooldowns(self) -> None:
        """Diskteki cooldown state'ini yükle (restart koruması).

        Dosya bozuksa fail-open DEĞİL, boş yükle + WARNING: cooldown eksikliği
        pozisyonu korumasız bırakmaz, yalnız fazladan giriş riski doğurur —
        journal'daki gibi girişleri kilitlemek orantısız olur.
        """
        path = self._cooldown_state_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("cooldown state version/root geçersiz")
            entries = raw.get("entries")
            if not isinstance(entries, dict):
                raise ValueError("cooldown entries nesne değil")
            now = time.time()
            loaded: Dict[str, Dict[str, Any]] = {}
            for symbol, state in entries.items():
                expires_at = float((state or {}).get("expires_at") or 0.0)
                if expires_at > now:
                    loaded[str(symbol).upper()] = {
                        "reason": str((state or {}).get("reason") or "unknown"),
                        "expires_at": expires_at,
                    }
            self._cooldowns.update(loaded)
            if loaded:
                self.logger.info(
                    f"🧊 Restart sonrası {len(loaded)} sembol cooldown'u diskten yüklendi: "
                    f"{sorted(loaded)}"
                )
        except Exception as e:
            self.logger.warning(f"⚠️ Cooldown state okunamadı, boş başlıyor: {e}")

    def _persist_cooldowns(self) -> None:
        """Aktif cooldown'ları atomik yaz (tmp + fsync + os.replace).

        Yazım hatası cooldown akışını asla bozmamalı — in-memory koruma sürer.
        """
        path = self._cooldown_state_path
        if path is None:
            return
        tmp_path: Optional[Path] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            payload = {"version": 1, "entries": self._cooldowns}
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception as e:
            self.logger.warning(f"⚠️ Cooldown state yazılamadı ({e}); RAM'de sürüyor")
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _set_cooldown(self, symbol: str, reason: str, expires_at: float) -> bool:
        """Ortak cooldown yazıcı: UZUN OLAN KAZANIR, sonra diske kalıcılaştır.

        Mevcut girdinin süresi daha uzunsa hiçbir şey yazılmaz (False döner) —
        ne loss_exit ne protection-failure yolu birbirinin süresini kısaltabilir.
        """
        key = str(symbol).upper()
        existing = self._cooldowns.get(key)
        if existing and float(existing.get("expires_at") or 0.0) >= expires_at:
            return False
        self._cooldowns[key] = {"reason": reason, "expires_at": expires_at}
        self._persist_cooldowns()
        return True

    def is_entry_blocked(self, symbol: str) -> bool:
        """Sembol giriş cooldown'unda mı? (loss_exit VEYA koruma hatası)"""
        self._prune_cooldowns()
        return str(symbol).upper() in self._cooldowns

    def _shadow_dedup_seconds(self) -> float:
        """Gölge tekilleştirme penceresi (saniye).

        SCALPER_SHADOW_DEDUP_MINUTES tanımlı/pozitifse o kullanılır; yoksa
        SCALPER_LOSS_COOLDOWN_MINUTES'e (canlıda aynı sembolün bir SL sonrası
        ne kadar süre yeniden giremediği), o da yoksa/≤0 ise 60 dakikaya
        düşer. Amaç: gölge deftirinin tekilleştirme aralığı, canlıda aynı
        sembolün fiilen ne kadar süre "meşgul" sayılacağıyla aynı büyüklük
        mertebesinde olsun (D14 review).
        """
        raw = getattr(self.cfg, "scalper_shadow_dedup_minutes", None)
        if not raw:
            raw = getattr(self.cfg, "scalper_loss_cooldown_minutes", 60)
        try:
            minutes = float(raw or 60)
        except (TypeError, ValueError):
            minutes = 60.0
        if minutes <= 0:
            minutes = 60.0
        return minutes * 60.0

    def shadow_active_count(self) -> int:
        """Tekilleştirme penceresi içinde gölge kaydı yapılmış sembol sayısı.

        Canlıda kapasite kapısı `tracked | pending` sayar; gölge modda bu
        küme hep boş kalır (hiçbir borsa isteği gitmediği için pozisyon/
        pending kurulmaz) — kapasite kapısı hiç devreye girmez ve gölge
        defteri, canlının reddedeceği sinyalleri de sınırsız biriktirir
        (D14 review, bulgu B). Engine bu sayıyı `open + shadow_active`
        olarak `scalper_max_positions`'a karşı sayar.
        """
        self._prune_cooldowns()
        return len(self._shadow_recent)

    def cooldown_snapshot(self) -> List[Dict[str, Any]]:
        """API/dashboard için aktif sembol cooldown'ları."""
        self._prune_cooldowns()
        now = time.time()
        rows: List[Dict[str, Any]] = []
        for symbol, state in sorted(self._cooldowns.items()):
            expires_at = float(state["expires_at"])
            rows.append(
                {
                    "symbol": symbol,
                    "reason": str(state.get("reason") or "protection_failure"),
                    "remaining_seconds": max(0.0, expires_at - now),
                    "expires_at": datetime.fromtimestamp(
                        expires_at, tz=timezone.utc
                    ).isoformat(),
                }
            )
        return rows

    def start_loss_cooldown(self, symbol: str) -> None:
        """SL/negatif net kapanış sonrası sembolü yeni girişe geçici kapat.

        ExitManager kapanış kaydından sonra çağırır (2026-08-11 BEAT bulgusu:
        düşen bıçağa 7 dakikada 4 ardışık yeniden giriş). Mevcut daha uzun bir
        cooldown'u (örn. koruma hatası) KISALTMAZ; yalnız yoksa veya daha kısa
        ise yazar. scalper_loss_cooldown_minutes<=0 → kapalı.
        """
        try:
            configured = float(
                getattr(self.cfg, "scalper_loss_cooldown_minutes", 0) or 0
            )
        except (TypeError, ValueError):
            configured = 0.0
        if configured <= 0.0:
            return
        key = str(symbol).upper()
        if self._set_cooldown(key, "loss_exit", time.time() + configured * 60.0):
            self.logger.info(
                f"🧊 {key}: kayıp sonrası giriş cooldown'u {configured:.0f} dk"
            )

    def _start_protection_failure_cooldown(self, symbol: str) -> None:
        configured = float(
            getattr(self.cfg, "scalper_protection_failure_cooldown_minutes", 60)
            or 60
        )
        duration_seconds = max(60.0, configured) * 60.0
        self._set_cooldown(
            symbol, "initial_sl_failed_emergency_close",
            time.time() + duration_seconds,
        )

    async def _resolve_sizing_equity(self, exchange_available: float) -> Optional[float]:
        """Gerçek available balance'ı opsiyonel doğrulanmış sanal kasa ile kırp."""
        virtual_base = float(getattr(self.cfg, "scalper_virtual_capital_usdt", 0.0) or 0.0)
        now = datetime.now(timezone.utc).isoformat()
        if virtual_base <= 0:
            effective = max(0.0, float(exchange_available))
            self._last_sizing_snapshot = {
                "mode": "exchange_available",
                "exchange_available": float(exchange_available),
                "virtual_capital": None,
                "eligible_realized_pnl": None,
                "effective_equity": effective,
                "start_trade_id": None,
                "updated_at": now,
            }
            return effective

        start_id = max(
            0,
            int(getattr(self.cfg, "scalper_virtual_capital_start_trade_id", 0) or 0),
        )
        try:
            snapshot_method = getattr(self.tracker, "compounding_snapshot", None)
            if snapshot_method is not None:
                tracker_snapshot = await snapshot_method(start_id)
                eligible_pnl = float(tracker_snapshot["eligible_realized_pnl"])
            else:
                eligible_method = getattr(self.tracker, "eligible_compounding_pnl")
                eligible_pnl = float(await eligible_method(start_id))
        except Exception as exc:
            # Sanal kasa doğrulanamıyorsa tam borsa bakiyesine sessizce dönmek,
            # kullanıcının 1000-USDT risk sınırını aşar. Giriş fail-closed.
            self.logger.error(
                f"❌ Sanal scalper sermayesi doğrulanamadı ({exc}); yeni giriş atlandı"
            )
            self._last_sizing_snapshot = {
                "mode": "virtual_capital_error",
                "exchange_available": float(exchange_available),
                "virtual_capital": None,
                "eligible_realized_pnl": None,
                "effective_equity": None,
                "start_trade_id": start_id,
                "updated_at": now,
            }
            return None

        virtual_capital = max(0.0, virtual_base + eligible_pnl)
        effective = min(max(0.0, float(exchange_available)), virtual_capital)
        self._last_sizing_snapshot = {
            "mode": "virtual_compounding",
            "exchange_available": float(exchange_available),
            "virtual_capital": virtual_capital,
            "eligible_realized_pnl": eligible_pnl,
            "effective_equity": effective,
            "start_trade_id": start_id,
            "updated_at": now,
        }
        return effective

    async def try_open(
        self,
        signal: ScalpSignal,
        ctx: StrategyContext,
        *,
        forensics: Optional[Dict[str, Any]] = None,
    ) -> Optional[ScalpPosition]:
        """`forensics` (D21): motorun kurduğu giriş-anı bağlamı.

        YALNIZ GÖZLEM — hiçbir kapıya, boyutlamaya ya da emre girmez. None
        (varsayılan) verildiğinde bu fonksiyon eskisiyle birebir aynıdır.
        """
        symbol = signal.symbol
        # Sözlüğü kopyala: çağıranın nesnesini bu fonksiyonun ara sonuçlarıyla
        # kirletmeyelim (aynı sinyal başka bir yolda yeniden kullanılabilir).
        # `is not None`: BOŞ bir sözlük de geçerli bir bağlamdır (kapı
        # sonuçları aşağıda ona yazılır); `if forensics` onu sessizce
        # "bağlam yok"a çevirirdi.
        forensics = dict(forensics) if forensics is not None else None
        direction = signal.direction
        entry_hint = signal.entry_price
        stop_price = signal.stop_price
        leverage = self._resolve_leverage(signal)

        if self.is_entry_blocked(symbol):
            state = next(
                (row for row in self.cooldown_snapshot() if row["symbol"] == symbol.upper()),
                None,
            )
            remaining = float((state or {}).get("remaining_seconds") or 0.0)
            reason = str((state or {}).get("reason") or "cooldown")
            self._count_reject("cooldown")
            self.logger.warning(
                f"⏸️ {symbol}: giriş cooldown aktif ({reason}, "
                f"{remaining / 60.0:.1f} dk kaldı), giriş atlandı"
            )
            return None

        # --- 1. Bakiye ---
        try:
            balance = await self.client.get_account_balance()
        except Exception as e:
            self.logger.error(f"❌ {symbol}: bakiye sorgusunda beklenmeyen hata ({e})")
            return None
        if balance is None or balance <= 0:
            self.logger.error(
                f"❌ {symbol}: bakiye bilinmiyor veya sıfır ({balance}), scalp girişi iptal"
            )
            return None
        balance = await self._resolve_sizing_equity(float(balance))
        if balance is None or balance <= 0:
            self.logger.error(
                f"❌ {symbol}: etkin scalper sermayesi bilinmiyor veya sıfır ({balance}), "
                "giriş iptal"
            )
            return None

        # --- 2. Stop mesafesi risk kapısı ---
        if entry_hint <= 0:
            self.logger.error(f"❌ {symbol}: geçersiz giriş fiyatı ({entry_hint}), sinyal atlandı")
            return None
        stop_distance_pct = abs(entry_hint - stop_price) / entry_hint * 100.0
        if not (self.cfg.scalper_min_stop_pct <= stop_distance_pct <= self.cfg.scalper_max_stop_pct):
            self._count_reject("stop_distance")
            self.logger.info(
                f"⏭️ {symbol}: stop mesafesi sınır dışı (%{stop_distance_pct:.3f}, izin verilen "
                f"[%{self.cfg.scalper_min_stop_pct}-%{self.cfg.scalper_max_stop_pct}]), sinyal atlandı"
            )
            return None
        if forensics is not None:
            forensics.setdefault("gates", {})["stop_distance"] = "passed"

        # --- 3. R:R kapısı ---
        # Beklenen harman getiri (ROI%): tp1_roi*tp1_frac + tp2_roi*tp2_frac + tp1_roi*runner_frac
        # (runner'ın en az TP1 kadar taşıdığı varsayımı — muhafazakâr)
        # SL riski (ROI%): stop_distance_pct * kaldıraç
        # rr = beklenen_getiri / sl_riski ; rr < cfg.scalper_min_rr -> None
        # cfg.scalper_min_rr <= 0 ise kapı atlanır
        min_rr = self.cfg.scalper_min_rr
        if forensics is not None:
            forensics.setdefault("gates", {})["min_rr"] = (
                "passed" if min_rr > 0 else "off"
            )
        if min_rr > 0:
            tp1_frac = self.cfg.scalper_tp1_fraction
            tp2_frac = self.cfg.scalper_tp2_fraction
            runner_frac = max(0.0, 1.0 - tp1_frac - tp2_frac)
            expected_roi = (
                self.cfg.scalper_tp1_roi * tp1_frac
                + self.cfg.scalper_tp2_roi * tp2_frac
                + self.cfg.scalper_tp1_roi * runner_frac
            )
            sl_risk_roi = stop_distance_pct * leverage
            if sl_risk_roi <= 0:
                self.logger.error(f"❌ {symbol}: SL riski hesaplanamadı (sl_risk_roi<=0), sinyal atlandı")
                return None
            rr = expected_roi / sl_risk_roi
            if rr < min_rr:
                self._count_reject("min_rr")
                self.logger.info(
                    f"⏭️ {symbol}: R:R yetersiz (rr={rr:.2f} < min={min_rr:.2f}, "
                    f"beklenen_getiri=%{expected_roi:.2f}, sl_riski=%{sl_risk_roi:.2f}), sinyal atlandı"
                )
                return None
            if forensics is not None:
                forensics["rr"] = rr

        # --- 4. Risk bazlı boyutlama + nominal tavan ---
        price_distance = abs(entry_hint - stop_price)
        if price_distance <= 0:
            self.logger.error(f"❌ {symbol}: giriş/stop mesafesi sıfır, boyutlama yapılamıyor")
            return None

        risk_amount = balance * (self.cfg.scalper_risk_percentage / 100.0) * signal.risk_multiplier
        qty = risk_amount / price_distance

        # Marj tavanı: pozisyon marjı kasanın scalper_max_margin_pct'sini aşamaz
        # (nominal = marj × kaldıraç). getattr: eski sahte-cfg testleri alan
        # tanımlamaz — onlarda tarihsel %50 davranışı korunur.
        margin_pct = getattr(self.cfg, "scalper_max_margin_pct", 50.0) / 100.0
        nominal_cap = balance * leverage * margin_pct
        nominal = qty * entry_hint
        if nominal > nominal_cap and entry_hint > 0:
            qty = nominal_cap / entry_hint
            self.logger.info(
                f"✂️ {symbol}: nominal değer kırpıldı ({nominal:.2f} -> {nominal_cap:.2f} USDT tavanı)"
            )

        # --- 5. Yuvarlama + borsa filtresi doğrulaması ---
        try:
            qty = await self.client.quantize_quantity(symbol, qty)
            await self.client.validate_order(symbol, qty, entry_hint)
        except BinanceAPIError as e:
            self.logger.error(f"❌ {symbol}: emir doğrulanamadı (kod={e.code}: {e.msg})")
            return None
        except Exception as e:
            self.logger.error(f"❌ {symbol}: boyutlama/doğrulama sırasında beklenmeyen hata ({e})")
            return None

        # --- Gölge modu (D14, docs/MAINNET_PLAN.md §3): buraya kadar TÜM
        #     kapılar (cooldown/stop-mesafesi/R:R/boyutlama/borsa filtresi)
        #     bugünkü gibi çalıştı — leverage/margin bu GERÇEK hesaplamadan
        #     gelir. Bundan sonrası (margin/leverage AYARI dahil) borsa
        #     hesabını değiştirir veya emir gönderir; gölge modda HİÇBİRİ
        #     ÇALIŞMAZ — sinyal SHADOW olarak deftere yazılır ve dönülür. ---
        if getattr(self.cfg, "scalper_shadow_mode", False):
            # Tekilleştirme (D14 review, bulgu A): occupancy bırakmayan gölge
            # dalı düzeltilmezse aynı sinyal her tarama turunda yeniden
            # yazılır (2-5x şişme). Pencere içindeyse sessizce atla — canlıda
            # bu sembol zaten açık pozisyon/cooldown nedeniyle yeniden
            # denenmezdi.
            key = symbol.upper()
            now = time.time()
            hold = self._shadow_dedup_seconds()
            last = self._shadow_recent.get(key)
            if last is not None and (now - last) < hold:
                return None
            self._shadow_recent[key] = now
            margin_usdt = (qty * entry_hint) / leverage if leverage else qty * entry_hint
            await self.tracker.record_shadow(
                signal=signal,
                entry_price=entry_hint,
                quantity=qty,
                leverage=leverage,
                margin_usdt=margin_usdt,
            )
            return None

        # --- 6. Margin type + leverage (emirden ÖNCE — burada hata zararsız) ---
        try:
            await self.client.set_margin_type(symbol, "ISOLATED")
            await self.client.set_leverage(symbol, leverage)
        except BinanceAPIError as e:
            self.logger.error(
                f"❌ {symbol}: margin/leverage ayarlanamadı (kod={e.code}: {e.msg}), pozisyon açılmadı"
            )
            return None
        except Exception as e:
            self.logger.error(f"❌ {symbol}: margin/leverage ayarında beklenmeyen hata ({e})")
            return None

        side = "BUY" if direction == Direction.LONG else "SELL"

        # --- 7a. Maker modu: LIMIT GTX emri kor, PendingEntry olarak sakla ---
        if getattr(self.cfg, "scalper_entry_mode", "taker") == "maker":
            # Adli bağlam dolum anında (check_pending → _on_pending_filled)
            # gerekir; bellekte sembol başına saklanır. Restart'ta kaybolur —
            # kayıt eksik kalır, İŞLEM ETKİLENMEZ (gözlem, kilit değil).
            # D21-R3 (bulgu 4): saklama artık BURADA değil, PendingEntry
            # kurulduktan SONRA yapılır ve emrin kimliğiyle damgalanır —
            # emir hiç konmazsa yetim bir bağlam kalmaz, kalırsa da başka bir
            # sinyalin dolumuna iliştirilemez.
            await self._open_maker_entry(
                signal=signal, ctx=ctx, side=side, quantity=qty,
                forensics=forensics,
            )
            return None

        # --- 7b. Taker modu (varsayılan): Market emri — BU NOKTADAN SONRA
        #     pozisyon GERÇEK olabilir ---
        try:
            entry_order = await self.client.open_market_order(symbol=symbol, side=side, quantity=qty)
        except BinanceAPIError as e:
            self.logger.error(f"❌ {symbol}: market order başarısız (kod={e.code}: {e.msg})")
            return None
        except Exception as e:
            self.logger.error(f"❌ {symbol}: market order sırasında beklenmeyen hata ({e})")
            return None

        # --- 8. Gerçek dolum çözümü ---
        sl_side = "SELL" if direction == Direction.LONG else "BUY"
        try:
            entry_price, filled_qty = await self.pm.resolve_fill(symbol, entry_order)
        except Exception as e:
            self.logger.critical(
                f"🚨 {symbol}: dolum bilgisi hiçbir kaynaktan okunamadı ({e}). Pozisyon açık "
                f"olabilir — PositionManager'ın acil kapatma akışı devreye sokuluyor."
            )
            await self.pm.place_stop_loss_or_close(symbol=symbol, sl_side=sl_side, stop_price=stop_price)
            return None

        if filled_qty <= 0:
            self.logger.error(f"❌ {symbol}: emir dolmadı (executedQty=0), pozisyon yok")
            return None

        self.logger.info(f"✅ Scalp dolum: {symbol} {filled_qty} @ {entry_price}")

        entry_candle_time = (
            ctx.candles_5m[-1].close_time if ctx.candles_5m
            else int(datetime.utcnow().timestamp() * 1000)
        )

        return await self._finalize_position(
            signal=signal,
            direction=direction,
            sl_side=sl_side,
            entry_price=entry_price,
            filled_qty=filled_qty,
            entry_order_id=str(entry_order.get("orderId") or ""),
            entry_candle_time=entry_candle_time,
            forensics=forensics,
        )

    def _configured_conservative_fee_rate(self) -> float:
        """Config fallback'ında maker/taker oranlarının yüksek olanını seç."""
        maker = max(0.0, float(getattr(self.cfg, "scalper_maker_fee_pct", 0.02))) / 100.0
        taker = max(0.0, float(getattr(self.cfg, "scalper_taker_fee_pct", 0.05))) / 100.0
        return max(maker, taker)

    @staticmethod
    def _valid_fee_rate(value: Any) -> Optional[float]:
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(rate) or rate < 0 or rate >= 1:
            return None
        return rate

    @staticmethod
    def _coerce_price(value: Any) -> Optional[float]:
        """Bir borsa/emir alanını pozitif sonlu fiyata çevir, olmuyorsa None."""
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        return price

    async def _resolve_commission_rates(self, symbol: str) -> Tuple[float, float, str]:
        """Sembolün gerçek entry/exit fee oranlarını veya güvenli fallback'i al."""
        fallback = self._configured_conservative_fee_rate()
        getter = getattr(self.client, "get_user_commission_rate", None)
        if getter is None:
            return fallback, fallback, "config_conservative"
        try:
            raw = await getter(symbol)
            maker = self._valid_fee_rate((raw or {}).get("makerCommissionRate"))
            taker = self._valid_fee_rate((raw or {}).get("takerCommissionRate"))
            if maker is None or taker is None:
                raise ValueError(f"eksik/geçersiz commission response: {raw!r}")
            entry_rate = maker if getattr(self.cfg, "scalper_entry_mode", "taker") == "maker" else taker
            return entry_rate, taker, "binance_user_commission"
        except Exception as exc:
            self.logger.warning(
                f"⚠️ {symbol}: gerçek komisyon oranı okunamadı ({exc}); "
                f"iki bacakta muhafazakâr config oranı {fallback:.8f} kullanılacak"
            )
            return fallback, fallback, "config_conservative"

    @staticmethod
    def _trade_quantity(row: Dict[str, Any]) -> Optional[float]:
        try:
            value = float(row.get("qty") or row.get("quantity") or 0.0)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value > 0 else None

    @staticmethod
    def _trade_price(row: Dict[str, Any]) -> Optional[float]:
        try:
            value = float(row.get("price") or 0.0)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value > 0 else None

    @staticmethod
    def _trade_is_buyer(row: Dict[str, Any]) -> Optional[bool]:
        value = row.get("buyer")
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        side = str(row.get("side") or "").upper()
        if side in ("BUY", "SELL"):
            return side == "BUY"
        return None

    @staticmethod
    def _trade_order_id(row: Dict[str, Any]) -> Optional[int]:
        try:
            return int(row.get("orderId"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _trade_time_ms(row: Dict[str, Any]) -> Optional[int]:
        try:
            value = int(row.get("time"))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _estimated_gross(
        direction: Direction, entry_price: float, exit_price: float, quantity: float
    ) -> float:
        if direction == Direction.LONG:
            return (exit_price - entry_price) * quantity
        return (entry_price - exit_price) * quantity

    async def _fallback_failed_execution_ledger(
        self,
        *,
        symbol: str,
        direction: Direction,
        entry_price: float,
        filled_qty: float,
        detail: str,
    ) -> _FailedExecutionLedger:
        try:
            exit_price = float(await self.client.get_current_price(symbol))
        except Exception:
            exit_price = entry_price
        if not math.isfinite(exit_price) or exit_price <= 0:
            exit_price = entry_price
        return _FailedExecutionLedger(
            exit_price=exit_price,
            realized_pnl=self._estimated_gross(
                direction, entry_price, exit_price, filled_qty
            ),
            pnl_source="estimated_gross",
            notes=f"ledger_uncertain={detail}",
        )

    async def _fetch_failed_execution_ledger(
        self,
        *,
        symbol: str,
        direction: Direction,
        entry_price: float,
        filled_qty: float,
        entry_order_id: str,
    ) -> _FailedExecutionLedger:
        """Acil kapanan fill'i exact account-trade satırlarıyla uzlaştır.

        Giriş orderId ile birebir alınır. Ardından aynı dar zaman penceresindeki
        ters-yön fill'leri, tam giriş miktarını kapatana kadar toplanır. Miktar,
        yön veya commission asset belirsizse gerçekmiş gibi davranılmaz.
        """
        getter = getattr(self.client, "get_account_trades", None)
        try:
            numeric_entry_id = int(entry_order_id)
        except (TypeError, ValueError):
            numeric_entry_id = 0
        if getter is None or numeric_entry_id <= 0:
            return await self._fallback_failed_execution_ledger(
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                filled_qty=filled_qty,
                detail="account_trade_api_or_entry_id_missing",
            )

        last_detail = "account_trades_empty"
        for delay in self.FAILED_LEDGER_RETRY_DELAYS:
            if delay:
                await asyncio.sleep(delay)
            try:
                entry_rows = await getter(
                    symbol, order_id=numeric_entry_id, limit=500
                )
                if not isinstance(entry_rows, list) or not entry_rows:
                    last_detail = "entry_trades_missing"
                    continue
                entry_rows = [
                    row for row in entry_rows
                    if isinstance(row, dict)
                    and self._trade_order_id(row) == numeric_entry_id
                ]
                entry_times = [
                    value for value in (self._trade_time_ms(row) for row in entry_rows)
                    if value is not None
                ]
                if not entry_rows or not entry_times:
                    last_detail = "entry_trade_identity_invalid"
                    continue

                entry_is_buyer = direction == Direction.LONG
                if any(self._trade_is_buyer(row) != entry_is_buyer for row in entry_rows):
                    last_detail = "entry_trade_side_mismatch"
                    continue
                entry_qtys = [self._trade_quantity(row) for row in entry_rows]
                if any(qty is None for qty in entry_qtys):
                    last_detail = "entry_trade_qty_invalid"
                    continue
                entry_qty = sum(float(qty) for qty in entry_qtys if qty is not None)
                tolerance = max(1e-12, filled_qty * 1e-6)
                if abs(entry_qty - filled_qty) > tolerance:
                    last_detail = "entry_trade_qty_mismatch"
                    continue

                start_ms = min(entry_times) - 1000
                end_ms = int(time.time() * 1000) + 2000
                window_rows = await getter(
                    symbol,
                    start_time=start_ms,
                    end_time=end_ms,
                    limit=500,
                )
                if not isinstance(window_rows, list):
                    last_detail = "trade_window_invalid"
                    continue

                close_candidates = sorted(
                    (
                        row for row in window_rows
                        if isinstance(row, dict)
                        and self._trade_is_buyer(row) is (not entry_is_buyer)
                        and (self._trade_time_ms(row) or 0) >= min(entry_times)
                    ),
                    key=lambda row: (
                        self._trade_time_ms(row) or 0,
                        int(row.get("id") or 0),
                    ),
                )
                selected_close: List[Dict[str, Any]] = []
                close_qty = 0.0
                ambiguous = False
                for row in close_candidates:
                    qty = self._trade_quantity(row)
                    if qty is None:
                        ambiguous = True
                        break
                    if close_qty + qty > filled_qty + tolerance:
                        ambiguous = True
                        break
                    selected_close.append(row)
                    close_qty += qty
                    if abs(close_qty - filled_qty) <= tolerance:
                        break
                if ambiguous or abs(close_qty - filled_qty) > tolerance:
                    last_detail = "exact_close_fill_not_proven"
                    continue

                selected = entry_rows + selected_close
                commission_assets = {
                    str(row.get("commissionAsset") or "") for row in selected
                }
                commission_assets.discard("")
                # Virtual capital USDT cinsindedir. USDC/FDUSD/BNB komisyonunu
                # burada 1:1 varsaymak doğrulanmış sermayeyi şişirebilir; kur
                # dönüşümü yoksa exact etiketi yalnız USDT'ye verilir.
                if commission_assets != {"USDT"}:
                    last_detail = "commission_asset_not_additive"
                    continue

                realized_pnl = 0.0
                valid_money = True
                for row in selected:
                    try:
                        realized = float(row.get("realizedPnl") or 0.0)
                        commission = float(row.get("commission") or 0.0)
                    except (TypeError, ValueError):
                        valid_money = False
                        break
                    if not math.isfinite(realized) or not math.isfinite(commission) or commission < 0:
                        valid_money = False
                        break
                    realized_pnl += realized - commission
                if not valid_money:
                    last_detail = "trade_money_invalid"
                    continue

                close_notional = 0.0
                for row in selected_close:
                    price = self._trade_price(row)
                    qty = self._trade_quantity(row)
                    if price is None or qty is None:
                        valid_money = False
                        break
                    close_notional += price * qty
                if not valid_money or close_qty <= 0:
                    last_detail = "close_price_invalid"
                    continue

                return _FailedExecutionLedger(
                    exit_price=close_notional / close_qty,
                    realized_pnl=realized_pnl,
                    pnl_source="binance_account_trades_net",
                    notes=(
                        f"ledger_entry_fills={len(entry_rows)},"
                        f"close_fills={len(selected_close)}"
                    ),
                )
            except Exception as exc:
                last_detail = f"account_trade_query_error:{type(exc).__name__}"

        return await self._fallback_failed_execution_ledger(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            filled_qty=filled_qty,
            detail=last_detail,
        )

    async def _record_initial_protection_failure(
        self,
        *,
        signal: ScalpSignal,
        direction: Direction,
        entry_price: float,
        filled_qty: float,
        entry_order_id: str,
    ) -> None:
        symbol = signal.symbol
        self._start_protection_failure_cooldown(symbol)
        ledger = await self._fetch_failed_execution_ledger(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            filled_qty=filled_qty,
            entry_order_id=entry_order_id,
        )
        try:
            await self.tracker.record_failed_execution(
                signal=signal,
                entry_price=entry_price,
                exit_price=ledger.exit_price,
                quantity=filled_qty,
                leverage=self._resolve_leverage(signal),
                realized_pnl=ledger.realized_pnl,
                pnl_source=ledger.pnl_source,
                entry_order_id=entry_order_id,
                notes=ledger.notes,
            )
        except Exception as exc:
            self.logger.critical(
                f"🚨 {symbol}: acil kapatılan başarısız execution ledger'a yazılamadı ({exc})"
            )

    # ------------------------------------------------------------------
    # Ortak kod yolu: SL → TP merdiveni → PositionModel/DB/tracker kaydı.
    #
    # Taker akışının 9-11. adımlarıdır; maker akışı (FILLED olduğunda)
    # AYNI metodu GERÇEK dolum fiyatıyla çağırır — TP fiyatları her zaman
    # gerçek entry_price'tan (sinyal anındaki tahmini fiyattan DEĞİL)
    # yeniden hesaplanır.
    # ------------------------------------------------------------------

    def _delay_adjusted_stop(
        self, *, signal: ScalpSignal, direction: Direction, entry_price: float
    ) -> float:
        """Yapısal stop'u GERÇEK dolum fiyatına göre yeniden çapala.

        Sinyalin üretildiği an ile emrin gerçekten dolduğu an arasında saniyeler
        geçer; maker (GTX post-only) modunda bu süre bir tarama turunu aşabilir.
        TP fiyatları zaten gerçek ``entry_price``'tan hesaplanıyor, fakat stop
        sinyal anındaki fiyata çapalı kalıyordu. Bu asimetri iki soruna yol açar:

        1. Maker LONG emri ancak fiyat DÜŞERKEN dolar (SHORT'ta yükselirken).
           Yani dolum, stop'a doğru bir hareketin içinde gerçekleşir. Sinyal
           anına çapalı stop bu yüzden sık sık "zaten geçilmiş" olur ve Binance
           koruma emrini -2021 ile reddeder; pozisyon açılır açılmaz kapatılır.
        2. Gerçekleşen risk, boyutlamada varsayılan riskten sapar.

        Çözüm: stop'u dolum kayması kadar ötele. Böylece giriş–stop MESAFESİ
        (dolayısıyla boyutlamada kullanılan birim risk) birebir korunur ve stop
        piyasanın doğru tarafında kalır.

        Öteleme yalnız güvenliyse uygulanır: stop girisin yanlış tarafına
        düşüyorsa veya mesafe risk tavanını aşıyorsa yapısal seviye korunur.
        """
        signal_entry = float(getattr(signal, "entry_price", 0.0) or 0.0)
        structural_stop = float(getattr(signal, "stop_price", 0.0) or 0.0)
        if signal_entry <= 0 or structural_stop <= 0 or entry_price <= 0:
            return structural_stop

        drift = entry_price - signal_entry
        if drift == 0.0:
            return structural_stop

        adjusted = structural_stop + drift
        if adjusted <= 0:
            return structural_stop

        # Stop, pozisyonun koruma tarafında kalmalı.
        if direction == Direction.LONG and adjusted >= entry_price:
            return structural_stop
        if direction == Direction.SHORT and adjusted <= entry_price:
            return structural_stop

        max_pct = float(getattr(self.cfg, "scalper_max_stop_pct", 0.0) or 0.0)
        if max_pct > 0:
            distance_pct = abs(entry_price - adjusted) / entry_price * 100.0
            if distance_pct > max_pct:
                # Tavan aşıldığında yapısal seviyeye DÖNMEK yanlış olur: büyük bir
                # dolum kaymasından sonra o seviye girişin ters tarafında kalmış
                # olabilir. Doğrusu stop'u risk tavanına tam oturtmaktır.
                capped = (
                    entry_price * (1.0 - max_pct / 100.0)
                    if direction == Direction.LONG
                    else entry_price * (1.0 + max_pct / 100.0)
                )
                self.logger.info(
                    f"✂️ {signal.symbol}: dolum kayması telafisi risk tavanına kırpıldı "
                    f"(%{distance_pct:.3f} -> %{max_pct:.3f}), stop={capped}"
                )
                return capped

        self.logger.info(
            f"🎯 {signal.symbol}: stop dolum kaymasına göre çapalandı "
            f"{structural_stop} -> {adjusted} "
            f"(sinyal={signal_entry}, dolum={entry_price}, kayma={drift:+.10g})"
        )
        return adjusted

    async def _finalize_position(
        self,
        *,
        signal: ScalpSignal,
        direction: Direction,
        sl_side: str,
        entry_price: float,
        filled_qty: float,
        entry_order_id: str,
        entry_candle_time: int,
        forensics: Optional[Dict[str, Any]] = None,
    ) -> Optional[ScalpPosition]:
        symbol = signal.symbol
        stop_price = self._delay_adjusted_stop(
            signal=signal, direction=direction, entry_price=entry_price
        )

        # --- 9. Stop-loss: BAŞARISIZ OLURSA pm zaten pozisyonu acil kapattı ---
        # reference_price/max_distance_pct: -2021 gelirse pm stop'u canlı fiyata
        # göre yeniden çapalar, ama asla risk tavanının ötesine genişletmez.
        sl_order = await self.pm.place_stop_loss_or_close(
            symbol=symbol,
            sl_side=sl_side,
            stop_price=stop_price,
            reference_price=entry_price,
            max_distance_pct=float(getattr(self.cfg, "scalper_max_stop_pct", 0.0) or 0.0)
            or None,
        )
        if sl_order is None:
            self.logger.error(
                f"❌ {symbol}: SL konulamadı — pozisyon PositionManager tarafından kapatıldı"
            )
            await self._record_initial_protection_failure(
                signal=signal,
                direction=direction,
                entry_price=entry_price,
                filled_qty=filled_qty,
                entry_order_id=entry_order_id,
            )
            return None
        sl_algo_id = self._extract_id(sl_order)

        # pm, -2021 sonrası stop'u canlı fiyata göre yeniden çapalamış olabilir.
        # Kayıt ve çıkış planı borsadaki GERÇEK tetik fiyatını yansıtmalı; aksi
        # halde breakeven/trailing mantığı var olmayan bir seviyeye göre çalışır.
        effective_stop = self._coerce_price(sl_order.get("effectiveStopPrice"))
        if effective_stop is not None and effective_stop != stop_price:
            self.logger.warning(
                f"📌 {symbol}: kayıtlı stop borsadaki etkin tetik fiyatına hizalandı "
                f"{stop_price} -> {effective_stop}"
            )
            stop_price = effective_stop

        # --- 10. TP merdiveni: başarısızlık pozisyonu tehlikeye atmaz (SL var) ---
        trade_leverage = self._resolve_leverage(signal)
        tp1_price = price_at_roi(entry_price, self.cfg.scalper_tp1_roi, trade_leverage, direction)
        tp2_price = price_at_roi(entry_price, self.cfg.scalper_tp2_roi, trade_leverage, direction)
        tp1_qty = filled_qty * self.cfg.scalper_tp1_fraction
        tp2_qty = filled_qty * self.cfg.scalper_tp2_fraction
        runner_qty = max(filled_qty - tp1_qty - tp2_qty, 0.0)

        tp1_algo_id = await self._place_tp_safely(symbol, sl_side, tp1_price, tp1_qty, "TP1")
        tp2_algo_id = await self._place_tp_safely(symbol, sl_side, tp2_price, tp2_qty, "TP2")

        entry_fee_rate, exit_fee_rate, fee_rate_source = await self._resolve_commission_rates(
            symbol
        )
        breakeven_price = fee_aware_breakeven_price(
            entry=entry_price,
            direction=direction,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            buffer_pct=float(getattr(self.cfg, "scalper_breakeven_buffer_pct", 0.05)),
        )
        breakeven_cost_pct = abs(breakeven_price - entry_price) / entry_price * 100.0

        # --- 11. Kayıt: PositionModel + DB + tracker + ExitPlan ---
        leverage = trade_leverage
        margin_usdt = (filled_qty * entry_price) / leverage if leverage else filled_qty * entry_price

        position_side = PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT
        position = PositionModel(
            symbol=symbol,
            side=position_side,
            leverage=leverage,
            margin_type="ISOLATED",
            entry_price=entry_price,
            current_price=entry_price,
            quantity=filled_qty,
            position_size=filled_qty * entry_price,
            initial_stoploss=stop_price,
            current_stoploss=stop_price,
            first_tp_price=tp1_price,
            first_tp_quantity=tp1_qty,
            targets=str([tp1_price, tp2_price]),
            status=PositionStatus.OPEN,
            entry_order_id=entry_order_id,
            sl_order_id=sl_algo_id,
            tp_order_id=tp1_algo_id,
            highest_price=entry_price,
            lowest_price=entry_price,
            trailing_stop_distance=self.cfg.scalper_chandelier_atr_mult,
            trailing_profit_distance=self.cfg.scalper_tp1_roi,
            opened_at=datetime.utcnow(),
            notes=f"scalper:{signal.strategy}",
        )

        plan = ExitPlan(
            tp1_price=tp1_price,
            tp1_quantity=tp1_qty,
            tp2_price=tp2_price,
            tp2_quantity=tp2_qty,
            runner_quantity=runner_qty,
            initial_stop=stop_price,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=self.cfg.scalper_chandelier_atr_mult,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            fee_rate_source=fee_rate_source,
            breakeven_cost_pct=breakeven_cost_pct,
            runner_floor_price=tp1_price,
            tp1_algo_id=tp1_algo_id,
            tp2_algo_id=tp2_algo_id,
        )

        opened_epoch = time.time()
        forensics_document = self._build_entry_forensics(
            context=forensics,
            signal=signal,
            direction=direction,
            entry_price=entry_price,
            filled_qty=filled_qty,
            leverage=leverage,
            margin_usdt=margin_usdt,
            stop_price=stop_price,
            plan=plan,
            opened_epoch=opened_epoch,
        )

        try:
            trade_id = await self.tracker.record_open(
                signal=signal,
                entry_price=entry_price,
                quantity=filled_qty,
                leverage=leverage,
                margin_usdt=margin_usdt,
                sl_algo_id=sl_algo_id,
                tp1_algo_id=tp1_algo_id,
                tp2_algo_id=tp2_algo_id,
                entry_order_id=entry_order_id,
                forensics=forensics_document,
            )
        except Exception as e:
            self.logger.critical(
                f"🚨 {symbol}: scalp işlem kaydı DB'ye yazılamadı ({e}). Pozisyon borsada AÇIK "
                f"ve SL korumalı ama takip kaydı yok — exits.recover() borsa taramasında bulmalı."
            )
            return None

        self.logger.info(
            f"✅ Scalp pozisyon açıldı: {signal.strategy}/{symbol} {direction.value} "
            f"{filled_qty} @ {entry_price} (SL={stop_price}, TP1={tp1_price}, TP2={tp2_price})",
            extra={"trade": True},
        )

        if forensics_document is not None:
            self._forensics_event(
                "entry",
                trade_id=trade_id,
                symbol=symbol,
                document=forensics_document,
            )

        return ScalpPosition(
            trade_id=trade_id,
            signal=signal,
            position=position,
            plan=plan,
            entry_candle_time=entry_candle_time,
            forensics_entry=(forensics_document or {}).get("entry"),
            opened_epoch=opened_epoch,
        )

    # ------------------------------------------------------------------
    # İşlem adli kaydı (D21) — YALNIZ GÖZLEM, akışı ASLA engellemez
    # ------------------------------------------------------------------

    def _forensics_enabled(self) -> bool:
        return bool(getattr(self.cfg, "scalper_forensics_enabled", True))

    def _forensics_warn(self, message: str) -> None:
        """Adli kayıt arızasını bir KEZ duyur; akışı asla kesme.

        `getattr` savunması: `ScalpExecutor.__new__` ile `__init__` atlanarak
        kurulan test çiftlerinde bu alan yoktur; adli kayıt yolunda bir
        `AttributeError` işlemi ETKİLEMEMELİDİR (D21-R3, bulgu 5).
        """
        if not getattr(self, "_forensics_error_logged", False):
            self._forensics_error_logged = True
            self.logger.warning(
                f"⚠️ Adli kayıt kurulamadı ({message}) — bu uyarı bir kez "
                f"loglanır, işlem akışı ETKİLENMEZ"
            )

    @staticmethod
    def _forensics_pending_key(pending: PendingEntry) -> str:
        """Bekleyen maker emrinin adli kimliği: sembol|yön|zaman damgası.

        Bağlam bu kimlikle damgalanır ve dolumda YENİDEN hesaplanıp
        karşılaştırılır. Yetim bir bağlam (emir konmadı/iptal oldu ama sözlük
        temizlenmedi) böylece BAŞKA bir sinyalin dolumuna iliştirilemez —
        yanlış "neden girildi" kaydı, hiç kayıt olmamasından kötüdür
        (D21-R3, bulgu 4).
        """
        signal = getattr(pending, "signal", None)
        symbol = str(getattr(signal, "symbol", "") or "").upper()
        direction = getattr(signal, "direction", None)
        direction = str(getattr(direction, "value", direction) or "").upper()
        return f"{symbol}|{direction}|{int(getattr(pending, 'created_at_ms', 0) or 0)}"

    def _store_pending_forensics(
        self, pending: PendingEntry, forensics: Optional[Dict[str, Any]]
    ) -> None:
        """Dolum anına kadar bekleyecek giriş bağlamını kimliğiyle sakla."""
        if forensics is None:
            return
        # getattr savunması: __init__'i atlayan test çiftleri bu alanı
        # kurmayabilir.
        pending_map = getattr(self, "_pending_forensics", None)
        if pending_map is None:
            return
        pending_map[str(getattr(pending.signal, "symbol", "")).upper()] = {
            "key": self._forensics_pending_key(pending),
            "context": forensics,
        }

    def _take_pending_forensics(
        self, pending: PendingEntry
    ) -> Optional[Dict[str, Any]]:
        """Dolan emrin giriş bağlamını al; kimlik uyuşmazsa ATIP uyar."""
        pending_map = getattr(self, "_pending_forensics", None)
        if not pending_map:
            return None
        symbol = str(getattr(pending.signal, "symbol", "")).upper()
        stored = pending_map.pop(symbol, None)
        if stored is None:
            return None
        if not isinstance(stored, dict) or "key" not in stored:
            # Eski/biçimsiz kayıt: kimliği doğrulanamayan bağlam KULLANILMAZ.
            self.logger.warning(
                f"⚠️ {symbol}: adli giriş bağlamı kimliksiz, atıldı "
                f"(kayıt eksik kalır, işlem akışı ETKİLENMEZ)"
            )
            return None
        expected = self._forensics_pending_key(pending)
        if stored.get("key") != expected:
            self.logger.warning(
                f"⚠️ {symbol}: adli giriş bağlamı BAŞKA bir sinyale ait "
                f"(beklenen={expected}, bulunan={stored.get('key')}), atıldı — "
                f"kayıt eksik kalır, işlem akışı ETKİLENMEZ"
            )
            return None
        return stored.get("context")

    def _build_entry_forensics(
        self,
        *,
        context: Optional[Dict[str, Any]],
        signal: ScalpSignal,
        direction: Direction,
        entry_price: float,
        filled_qty: float,
        leverage: int,
        margin_usdt: float,
        stop_price: float,
        plan: ExitPlan,
        opened_epoch: float,
    ) -> Optional[Dict[str, Any]]:
        """Motorun bağlamını GERÇEK dolum sayılarıyla birleştirip belge kur."""
        if not self._forensics_enabled():
            return None
        try:
            from src.strategies.scalper import forensics as fx

            context = dict(context or {})
            signal_epoch = context.pop("signal_epoch", None)
            latency = None
            if isinstance(signal_epoch, (int, float)):
                latency = max(0.0, opened_epoch - float(signal_epoch))
            entry = fx.build_entry(
                at=datetime.fromtimestamp(opened_epoch, tz=timezone.utc).isoformat(
                    timespec="seconds"
                ),
                signal=signal,
                ctx=None,
                cfg=self.cfg,
                fill_price=entry_price,
                quantity=filled_qty,
                leverage=leverage,
                margin_usdt=margin_usdt,
                stop_price=stop_price,
                tp1_price=plan.tp1_price,
                tp2_price=plan.tp2_price,
                breakeven_price=plan.breakeven_price,
                signal_at=context.pop("signal_at", None),
                fill_latency_sec=latency,
                entry_mode=str(getattr(self.cfg, "scalper_entry_mode", "taker")),
                indicators=context.pop("indicators", None),
                regime_info=context.pop("regime", None),
                leader_gate=context.pop("leader_gate", None),
                structure=context.pop("structure", None),
                tv_structure=context.pop("tv_structure", None),
                gates=context.pop("gates", None),
                tv=context.pop("tv", None),
                source=str(context.pop("source", "C")),
                kline_source=context.pop("kline_source", None),
                open_positions=context.pop("open_positions", None),
                daily_pnl=context.pop("daily_pnl", None),
                btc_price=context.pop("btc_price", None),
                rr=context.pop("rr", None),
            )
            # Motorun eklediği ve build_entry'nin tanımadığı alanlar kaybolmasın.
            for key, value in context.items():
                entry.setdefault(key, value)
            thresholds = fx.thresholds_from_cfg(self.cfg)
            return {
                "v": fx.FORENSICS_VERSION,
                "entry": entry,
                "verdict": fx.classify_entry(entry, thresholds),
            }
        except Exception as e:
            self._forensics_warn(f"{type(e).__name__}: {e}")
            return None

    def _forensics_event(
        self, event: str, *, trade_id: int, symbol: str, document: Dict[str, Any]
    ) -> None:
        """`logs/trades.jsonl`'e tek satır yaz (fail-safe, KİLİT DIŞINDA).

        `append_soon` yalnız kuyruğa koyar; gerçek `write()` ayrı bir yazıcı
        iş parçacığındadır. Bu çağrı `engine._entry_lock` altında yapılır ve
        yavaş bir disk orada TP1→BE/trailing/kill-switch işlerini
        geciktirmemelidir (D21-R3, düşmanca inceleme bulgusu 3).
        """
        try:
            from src.strategies.scalper import forensics_log

            forensics_log.append_soon(
                event,
                {
                    "trade_id": int(trade_id),
                    "symbol": str(symbol),
                    "verdict": list(document.get("verdict") or []),
                    "entry": document.get("entry"),
                },
            )
        except Exception as e:  # pragma: no cover - savunma
            self._forensics_warn(f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Maker modu — Faz 1: LIMIT GTX girişini kor, PendingEntry olarak sakla.
    # ------------------------------------------------------------------

    async def _open_maker_entry(
        self,
        signal: ScalpSignal,
        ctx: StrategyContext,
        side: str,
        quantity: float,
        *,
        forensics: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._pending_lock:
            await self._open_maker_entry_locked(
                signal, ctx, side, quantity, forensics=forensics
            )

    async def _open_maker_entry_locked(
        self,
        signal: ScalpSignal,
        ctx: StrategyContext,
        side: str,
        quantity: float,
        *,
        forensics: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._assert_recovery_ready()
        symbol = signal.symbol
        if symbol in self._pending:
            self.logger.warning(
                f"⚠️ {symbol}: zaten bekleyen maker girişi var, ikinci emir gönderilmedi"
            )
            return

        # BUY yalnızca en iyi bid'e, SELL yalnızca en iyi ask'e yazılır.
        # GTX, kotasyon GET'i ile POST arasında spread değişse emri taker'a
        # dönüştürmek yerine reddeder.
        try:
            book = await self.client.get_book_ticker(symbol)
            price_key = "bidPrice" if side == "BUY" else "askPrice"
            book_price = float(book[price_key])
            limit_price = await self.client.quantize_maker_price(
                symbol, book_price, side
            )
        except BinanceAPIError as e:
            self.logger.error(
                f"❌ {symbol}: maker kotasyonu alınamadı (kod={e.code}: {e.msg}), giriş iptal"
            )
            return
        except Exception as e:
            self.logger.error(
                f"❌ {symbol}: maker kotasyonu hazırlanırken beklenmeyen hata ({e}), giriş iptal"
            )
            return

        if limit_price <= 0:
            self.logger.error(f"❌ {symbol}: geçersiz limit fiyatı ({limit_price}), maker giriş iptal")
            return

        client_order_id = self._new_client_order_id()
        created_at_ms = int(time.time() * 1000)
        timeout_candles = max(
            0, int(getattr(self.cfg, "scalper_maker_fill_timeout_candles", 3))
        )
        pending = PendingEntry(
            signal=signal,
            order_id=None,
            client_order_id=client_order_id,
            limit_price=limit_price,
            quantity=quantity,
            created_monotonic=time.monotonic(),
            created_at_ms=created_at_ms,
            expires_at_ms=created_at_ms + timeout_candles * 300_000,
        )
        # KRİTİK SIRA: kalıcı intent atomik olarak diske yazılmadan POST yok.
        # Ağ yanıtı kaybolursa restart sonrası aynı clientOrderId ile
        # uzlaştırılır; ikinci bir emir gönderilmez.
        self._store_pending_record(pending)
        self._pending[symbol] = pending
        self._store_pending_forensics(pending, forensics)

        try:
            order = await self._place_limit_entry(
                symbol, side, quantity, limit_price, client_order_id
            )
        except _MakerOrderStateUnknown as e:
            pending.phase = "SUBMIT_UNKNOWN"
            self._store_pending_record(pending)
            self.logger.critical(
                f"🚨 {symbol}: maker emir sonucu belirsiz ({e}); clientOrderId="
                f"{client_order_id} pending tutuluyor ve POST tekrarlanmayacak"
            )
            return
        except BinanceAPIError as e:
            # _place_limit_entry yalnız POST'un kesin reddedildiğini ve aynı
            # client ID'nin borsada bulunmadığını kanıtladığında bu dalı açar.
            self._drop_pending(symbol, pending)
            self.logger.error(f"❌ {symbol}: limit order başarısız (kod={e.code}: {e.msg})")
            return
        except Exception as e:
            pending.phase = "SUBMIT_UNKNOWN"
            self._store_pending_record(pending)
            raise PendingRecoveryError(
                f"{symbol}: maker POST durumu beklenmeyen biçimde belirsiz: {e}"
            ) from e

        order_id = order.get("orderId")
        if order_id is None:
            pending.phase = "SUBMIT_UNKNOWN"
            self._store_pending_record(pending)
            self.logger.critical(
                f"🚨 {symbol}: limit order yanıtında orderId yok; clientOrderId="
                f"{client_order_id} pending korunuyor"
            )
            return

        self._record_order_state(pending, order, phase="WORKING")
        self.logger.info(
            f"📝 Maker giriş emri kondu: {symbol} {side} {quantity} @ {limit_price} "
            f"(orderId={order_id}, clientOrderId={client_order_id}, GTX/post-only)",
            extra={"trade": True},
        )

    async def _place_limit_entry(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        client_order_id: str,
    ) -> Dict[str, Any]:
        """LIMIT GTX giriş emrini idempotent bir istemci kimliğiyle gönder.

        ImprovedBinanceClient'ta market dışında public bir emir sarmalayıcısı
        YOK; mevcut _request_with_retry katmanı DOĞRUDAN kullanılır — tıpkı
        position_manager.py'nin _emergency_close'ında yapıldığı gibi
        (imzalama, retry, hata sınıflandırması AYNEN yeniden kullanılır,
        yeniden yazılmaz).
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTX",
            "quantity": quantity,
            "price": price,
            "newClientOrderId": client_order_id,
            "newOrderRespType": "RESULT",
        }
        placement_error: Optional[Exception] = None
        try:
            response = await self.client._request_with_retry(
                "POST", "/fapi/v1/order", params=params, signed=True
            )
            if response.get("orderId") is not None:
                return response
            placement_error = RuntimeError("başarılı POST yanıtında orderId yok")
        except Exception as e:
            placement_error = e

        try:
            reconciled = await self.client.get_order_by_client_id(
                symbol, client_order_id
            )
        except BinanceAPIError as query_error:
            # Kesin bir 4xx ret + borsada kimliğin bulunmaması, emrin
            # oluşmadığını kanıtlar. Ağ/5xx/duplicate sonuçlarında ise
            # "bulunamadı" anlık gecikme olabilir; niyet fail-closed tutulur.
            definitive_reject = (
                isinstance(placement_error, BinanceAPIError)
                and placement_error.status_code < 500
                and placement_error.status_code != 429
                and "duplicate" not in placement_error.msg.lower()
                and "client order" not in placement_error.msg.lower()
            )
            if query_error.code == -2013 and definitive_reject:
                raise placement_error
            raise _MakerOrderStateUnknown(
                f"POST={placement_error}; query={query_error}"
            ) from placement_error
        except Exception as query_error:
            raise _MakerOrderStateUnknown(
                f"POST={placement_error}; query={query_error}"
            ) from placement_error

        if reconciled.get("orderId") is None:
            raise _MakerOrderStateUnknown(
                f"clientOrderId sorgusu orderId döndürmedi: {reconciled!r}"
            )
        self.logger.warning(
            f"♻️ {symbol}: maker POST sonucu clientOrderId={client_order_id} ile uzlaştırıldı"
        )
        return reconciled

    @staticmethod
    def _new_client_order_id() -> str:
        """Binance regex/36 karakter sınırına uyan benzersiz kimlik."""
        return f"awa2sc_{uuid.uuid4().hex[:24]}"

    # ------------------------------------------------------------------
    # Maker modu — Faz 2: her motor turunda bekleyen girişleri kontrol et.
    # ------------------------------------------------------------------

    async def check_pending(self) -> List[ScalpPosition]:
        """Bekleyen tüm maker girişlerini bir tur ilerlet.

        engine._loop, exits.step()'ten hemen sonra bunu çağırır. Yeni dolan
        girişler ScalpPosition olarak döner; çağıran taraf bunları
        exits.track() ile izlemeye almalıdır.
        """
        async with self._pending_lock:
            if self._journal_error:
                raise PendingRecoveryError(
                    f"Maker pending journal bozuk/okunamıyor: {self._journal_error}"
                )
            if self._recovery_needed:
                # Engine değişikliği gerektirmeden ilk güvenlik polling turu
                # restart journal'ını otomatik uzlaştırır.
                return await self._recover_pending_locked()
            return await self._check_pending_locked()

    async def _check_pending_locked(self) -> List[ScalpPosition]:
        opened: List[ScalpPosition] = []
        for symbol in list(self._pending.keys()):
            pending = self._pending.get(symbol)
            if pending is None:
                continue
            try:
                sp = await self._check_one_pending(symbol, pending)
            except UnprotectedPositionError:
                # Engine bu istisnayı global giriş latch'ine dönüştürür.
                # Burada yutmak botun yeni pozisyon açmaya devam etmesine yol açar.
                raise
            except Exception as e:
                self.logger.error(
                    f"❌ {symbol}: pending giriş kontrolünde beklenmeyen hata ({e}), "
                    f"pending korunuyor (izleme bırakılmaz)"
                )
                continue
            if sp is not None:
                opened.append(sp)
        return opened

    async def _check_one_pending(
        self, symbol: str, pending: PendingEntry
    ) -> Optional[ScalpPosition]:
        try:
            order = await self._get_pending_order(symbol, pending)
        except Exception as e:
            # Sorgu hatası: "bilinmiyor" ASLA "iptal" sayılmaz — pending
            # olduğu gibi kalır, sonraki turda tekrar denenir.
            self.logger.warning(
                f"⚠️ {symbol}: bekleyen emir sorgulanamadı ({e}), bu tur atlanıyor "
                f"('bilinmiyor' 'iptal' sayılmaz)"
            )
            return None

        return await self._process_pending_order(symbol, pending, order)

    async def _process_pending_order(
        self, symbol: str, pending: PendingEntry, order: Dict[str, Any]
    ) -> Optional[ScalpPosition]:
        """REST sorgusu ve WS eventi için ortak, exactly-once durum makinesi."""
        self._record_order_state(pending, order)

        status = order.get("status")

        if status == "FILLED":
            return await self._on_pending_filled(symbol, pending, order)

        if status == "PARTIALLY_FILLED":
            # Kalan miktarı bekletmek, dolan miktarı süresiz SL'siz bırakır.
            # Görülür görülmez iptal et; terminal iptal yanıtındaki
            # executedQty _on_pending_filled ile derhal korumaya alınır.
            self.logger.warning(
                f"⚠️ {symbol}: maker giriş kısmen doldu; kalan miktar hemen iptal ediliyor"
            )
            return await self._cancel_pending(
                symbol, pending, observed_order=order, reason="partial_fill"
            )

        if status == "NEW":
            self._record_order_state(pending, order, phase="WORKING")
            pending.scans_waited += 1
            timeout_candles = max(0, int(getattr(self.cfg, "scalper_maker_fill_timeout_candles", 3)))
            # Poll sıklığı engine tarafında değişebilir (güvenlik döngüsü
            # 2sn, ana tarama 30sn vb.). Timeout bu nedenle tur sayısına değil
            # gerçek monotonic zamana bağlıdır: bir mum = 300 saniye.
            elapsed = max(0.0, time.monotonic() - pending.created_monotonic)
            timeout_seconds = timeout_candles * 300.0
            wall_expired = (
                pending.expires_at_ms > 0
                and int(time.time() * 1000) >= pending.expires_at_ms
            )
            if timeout_candles == 0 or elapsed >= timeout_seconds or wall_expired:
                return await self._cancel_pending(
                    symbol, pending, reason="timeout"
                )
            return None

        if status in ("CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"):
            if self._executed_qty(order) > 0:
                self.logger.warning(
                    f"⚠️ {symbol}: maker giriş {status} ama kısmi dolum var; "
                    f"gerçekleşen miktar korunuyor"
                )
                return await self._on_pending_filled(symbol, pending, order)
            self.logger.info(
                f"⏭️ {symbol}: maker giriş {status} — pending kaydı düşürüldü "
                f"(orderId={pending.order_id})"
            )
            self._drop_pending(symbol, pending)
            return None

        # Bilinmeyen/işlenmemiş durum — dokunma, sonraki turda tekrar bak.
        self.logger.debug(f"{symbol}: bekleyen emir durumu bilinmiyor ({status!r}), tur atlanıyor")
        return None

    async def _get_pending_order(
        self, symbol: str, pending: PendingEntry
    ) -> Dict[str, Any]:
        """Pending emri orderId varsa onunla, yoksa clientOrderId ile sorgula."""
        if pending.order_id is not None:
            return await self.client.get_order(symbol, pending.order_id)

        order = await self.client.get_order_by_client_id(
            symbol, pending.client_order_id
        )
        order_id = order.get("orderId")
        if order_id is not None:
            self._record_order_state(pending, order)
            self.logger.info(
                f"♻️ {symbol}: belirsiz maker niyeti orderId={order_id} ile uzlaştırıldı"
            )
        return order

    async def _on_pending_filled(
        self, symbol: str, pending: PendingEntry, order: Dict[str, Any]
    ) -> Optional[ScalpPosition]:
        """FILLED durumu: HEMEN koruma kur, sonra TP merdiveni ve kayıt.

        Pozisyonun asla korumasız kalmaması birincil kural — SL, pending
        kaydı silinmeden ÖNCE değil, dolum bilgisi çözüldükten HEMEN sonra
        kurulur (bkz. _finalize_position).
        """
        if pending.phase == "DB_OPEN":
            # DB commit olmuş, yalnız journal cleanup başarısız kalmış olabilir.
            # Aynı dolumu tekrar finalize etmek çift SL/TP üretir.
            raise PendingRecoveryError(
                f"{symbol}: DB_OPEN maker kaydı journal'da kaldı; yeniden finalize edilmiyor"
            )
        if pending.phase in ("PROTECTING", "RECOVERY_REQUIRED"):
            await self._recover_uncertain_protection(symbol, pending)
            return None

        signal = pending.signal
        direction = signal.direction
        sl_side = "SELL" if direction == Direction.LONG else "BUY"

        # Crash sınırı: bundan sonraki restart, DB'de OPEN bulamazsa yeniden
        # stop/TP kurmak yerine pozisyonu fail-closed acil kapatır.
        self._record_order_state(pending, order, phase="PROTECTING")

        try:
            entry_price, filled_qty = await self.pm.resolve_fill(symbol, order)
        except UnprotectedPositionError:
            raise
        except Exception as e:
            pending.phase = "RECOVERY_REQUIRED"
            self._store_pending_record(pending)
            self.logger.critical(
                f"🚨 {symbol}: maker dolum bilgisi hiçbir kaynaktan okunamadı ({e}). Pozisyon "
                f"açık olabilir — PositionManager'ın acil kapatma akışı devreye sokuluyor."
            )
            try:
                protection = await self.pm.place_stop_loss_or_close(
                    symbol=symbol, sl_side=sl_side, stop_price=signal.stop_price
                )
            except UnprotectedPositionError:
                raise
            except Exception as protection_error:
                raise PendingRecoveryError(
                    f"{symbol}: dolum ve koruma sonucu belirsiz: {protection_error}"
                ) from protection_error
            if protection is None and await self._position_is_flat(symbol):
                self._drop_pending(symbol, pending)
                return None
            raise PendingRecoveryError(
                f"{symbol}: dolum çözülemedi; pozisyon korumalı/açık olabilir, "
                "manuel recovery gerekli"
            ) from e

        if filled_qty <= 0:
            pending.phase = "RECOVERY_REQUIRED"
            self._store_pending_record(pending)
            raise PendingRecoveryError(
                f"{symbol}: FILLED durumunda resolved executedQty=0; pozisyon durumu belirsiz"
            )

        if filled_qty > pending.executed_qty:
            pending.executed_qty = filled_qty
        if entry_price > 0:
            pending.avg_price = entry_price
        self._store_pending_record(pending)

        self.logger.info(f"✅ Maker dolum: {symbol} {filled_qty} @ {entry_price}")

        entry_candle_time = int(datetime.utcnow().timestamp() * 1000)

        try:
            scalp_position = await self._finalize_position(
                signal=signal,
                direction=direction,
                sl_side=sl_side,
                entry_price=entry_price,
                filled_qty=filled_qty,
                entry_order_id=str(order.get("orderId") or pending.order_id),
                entry_candle_time=entry_candle_time,
                forensics=self._take_pending_forensics(pending),
            )
        except UnprotectedPositionError:
            pending.phase = "RECOVERY_REQUIRED"
            self._store_pending_record(pending)
            raise
        except Exception as e:
            pending.phase = "RECOVERY_REQUIRED"
            self._store_pending_record(pending)
            raise PendingRecoveryError(
                f"{symbol}: maker finalize beklenmeyen hata verdi: {e}"
            ) from e

        if scalp_position is not None:
            # DB commit tamamlandı. Önce bunu journal'a işaretle; ardından
            # atomik cleanup. Cleanup hata verirse DB_OPEN kaydı korunur ve
            # aynı dolum ikinci kez finalize edilmez.
            pending.phase = "DB_OPEN"
            self._store_pending_record(pending)
            self._drop_pending(symbol, pending)
            return scalp_position

        # _finalize_position None: SL kurulamadığı için emergency close
        # başarılı olmuş OLABİLİR veya DB yazımı başarısızken SL korumalı
        # pozisyon açık kalmış olabilir. Yalnız kesin flat ise kayıt silinir.
        pending.phase = "RECOVERY_REQUIRED"
        self._store_pending_record(pending)
        if await self._position_is_flat(symbol):
            self._drop_pending(symbol, pending)
            return None
        raise PendingRecoveryError(
            f"{symbol}: finalize tamamlanmadı ve borsada pozisyon hâlâ açık; "
            "journal korunuyor"
        )

    async def _position_is_flat(self, symbol: str) -> bool:
        try:
            position = await self.client.get_position_risk(symbol)
            if position is None:
                return True
            return abs(float(position.get("positionAmt") or 0.0)) <= 0.0
        except Exception as e:
            raise PendingRecoveryError(
                f"{symbol}: pozisyon flat doğrulaması başarısız: {e}"
            ) from e

    async def _recover_uncertain_protection(
        self, symbol: str, pending: PendingEntry
    ) -> None:
        """Crash koruma sınırında kalmış pozisyonu tekrar finalize etme.

        STOP zaten kurulmuş olabilir; yeniden finalize çift koşullu emir
        üretir. DB kaydı yoksa güvenli seçenek reduceOnly emergency close ve
        ardından kesin flat doğrulamasıdır.
        """
        if await self._position_is_flat(symbol):
            self._drop_pending(symbol, pending)
            return
        emergency_close = getattr(self.pm, "emergency_close", None)
        if emergency_close is None:
            emergency_close = getattr(self.pm, "_emergency_close", None)
        if emergency_close is None:
            raise PendingRecoveryError(
                f"{symbol}: recovery emergency_close desteği yok; journal korunuyor"
            )
        try:
            closed = await emergency_close(symbol)
        except Exception as e:
            raise PendingRecoveryError(
                f"{symbol}: recovery emergency close hata verdi: {e}"
            ) from e
        if closed and await self._position_is_flat(symbol):
            self._drop_pending(symbol, pending)
            return
        raise PendingRecoveryError(
            f"{symbol}: recovery emergency close sonrası flat doğrulanamadı; "
            "journal korunuyor"
        )

    async def _cancel_pending(
        self,
        symbol: str,
        pending: PendingEntry,
        *,
        observed_order: Optional[Dict[str, Any]] = None,
        reason: str = "timeout",
    ) -> Optional[ScalpPosition]:
        """Pending girişi iptal et ve tüm dolum yarışlarını uzlaştır.

        PARTIALLY_FILLED için `observed_order` ilk bilinen dolumu taşır;
        cancel yanıtıyla birleştirilir ve terminal durum kesinleştiğinde
        gerçekleşen miktar derhal korunur. Herhangi bir sorgu/iptal belirsizliği
        pending kaydını korur. "Bilinmiyor" asla "iptal edildi" sayılmaz.
        """
        known = observed_order
        if known is None:
            try:
                known = await self._get_pending_order(symbol, pending)
            except Exception as e:
                self.logger.warning(
                    f"⚠️ {symbol}: iptal öncesi son doğrulama sorgusu başarısız ({e}), "
                    f"bu tur atlanıyor (pending korunuyor)"
                )
                return None

        if known.get("status") == "FILLED":
            self.logger.info(
                f"✅ {symbol}: iptal denemesi sırasında emir dolmuş; koruma akışı işleniyor"
            )
            return await self._on_pending_filled(symbol, pending, known)

        if (
            known.get("status") in ("CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED")
            and self._executed_qty(known) > 0
        ):
            return await self._on_pending_filled(symbol, pending, known)

        try:
            cancel_resp = await self._cancel_pending_order(symbol, pending)
        except BinanceAPIError as e:
            if e.code == -2011:
                cancel_resp = {"status": "ALREADY_GONE"}
            else:
                self.logger.error(
                    f"⚠️ {symbol}: pending giriş iptal edilemedi (kod={e.code}: {e.msg}), "
                    f"pending korunuyor, sonraki turda tekrar denenecek"
                )
                return None
        except Exception as e:
            self.logger.error(
                f"⚠️ {symbol}: pending giriş iptalinde beklenmeyen hata ({e}), pending korunuyor"
            )
            return None

        if cancel_resp.get("status") == "ALREADY_GONE":
            # -2011: emir zaten yok — dolmuş/kısmen dolup iptal olmuş olabilir.
            try:
                final_order = await self._get_pending_order(symbol, pending)
            except Exception as e:
                self.logger.error(
                    f"🚨 {symbol}: iptal sonrası doğrulama sorgusu başarısız ({e}). Pozisyon açık "
                    f"olabilir ama pending kaydı BELİRSİZ — korunuyor, sonraki turda tekrar denenecek."
                )
                return None
        else:
            final_order = self._merge_order_states(known, cancel_resp)

        status = final_order.get("status")
        executed_qty = self._executed_qty(final_order)
        if status == "FILLED" or (
            status in ("CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED")
            and executed_qty > 0
        ):
            self.logger.warning(
                f"⚠️ {symbol}: maker iptali sonrası {executed_qty} miktar gerçekleşmiş; "
                f"SL/TP koruması kuruluyor"
            )
            return await self._on_pending_filled(symbol, pending, final_order)

        if status in ("CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"):
            self.logger.info(
                f"⏭️ {symbol}: maker giriş iptal edildi "
                f"(neden={reason}, orderId={pending.order_id}, "
                f"clientOrderId={pending.client_order_id}, scans_waited={pending.scans_waited})"
            )
            self._drop_pending(symbol, pending)
            return None

        # Cancel yanıtı terminal değilse sonucu kesinleştirmek için bir
        # kez daha sorgula. Bu sorgu başarısızsa pending fail-closed kalır.
        try:
            reconciled = await self._get_pending_order(symbol, pending)
        except Exception as e:
            self.logger.error(
                f"🚨 {symbol}: iptal sonucu terminal değil ({status!r}) ve tekrar "
                f"doğrulanamadı ({e}); pending korunuyor"
            )
            return None

        reconciled_status = reconciled.get("status")
        reconciled_qty = self._executed_qty(reconciled)
        if reconciled_status == "FILLED" or (
            reconciled_status in ("CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED")
            and reconciled_qty > 0
        ):
            return await self._on_pending_filled(symbol, pending, reconciled)
        if reconciled_status in ("CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"):
            self._drop_pending(symbol, pending)
            return None

        self.logger.error(
            f"🚨 {symbol}: iptal sonrası emir hâlâ terminal değil "
            f"({reconciled_status!r}); pending korunuyor"
        )
        return None

    async def _cancel_pending_order(
        self, symbol: str, pending: PendingEntry
    ) -> Dict[str, Any]:
        if pending.order_id is not None:
            return await self.client.cancel_order(symbol, pending.order_id)
        return await self.client.cancel_order_by_client_id(
            symbol, pending.client_order_id
        )

    @staticmethod
    def _executed_qty(order: Dict[str, Any]) -> float:
        try:
            return max(0.0, float(order.get("executedQty") or 0.0))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _merge_order_states(
        cls, *states: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Cancel yanıtı eksik olsa bile bilinen kümülatif dolumu kaybetme."""
        merged: Dict[str, Any] = {}
        max_executed = 0.0
        fill_price: Any = None
        for state in states:
            if not state:
                continue
            merged.update({k: v for k, v in state.items() if v is not None})
            executed = cls._executed_qty(state)
            if executed > max_executed:
                max_executed = executed
                fill_price = None
                try:
                    if float(state.get("avgPrice") or 0.0) > 0:
                        fill_price = state.get("avgPrice")
                except (TypeError, ValueError):
                    pass
            elif executed == max_executed:
                try:
                    if float(state.get("avgPrice") or 0.0) > 0:
                        fill_price = state.get("avgPrice")
                except (TypeError, ValueError):
                    pass
        merged["executedQty"] = str(max_executed)
        if fill_price is not None:
            merged["avgPrice"] = fill_price
        elif max_executed > 0:
            # Daha yüksek kümülatif qty'nin ortalama fiyatı yoksa eski,
            # daha küçük dolumun avgPrice'ını yanlış qty ile eşleme;
            # resolve_fill borsayı tekrar sorgulasın.
            merged["avgPrice"] = "0"
        return merged

    async def recover_pending(self) -> List[ScalpPosition]:
        """Restart journal'ındaki maker niyetlerini borsayla uzlaştır.

        Aynı sembol DB'de OPEN ise executor yeniden SL/TP üretmez; journal
        temizlenir ve pozisyon exits recovery'ye bırakılır. DB yoksa emir
        yalnız clientOrderId ile üç kez sorgulanır. Kesin üç adet -2013/no
        order sonucu intent'in hiç oluşmadığını kanıtlar; diğer belirsizlikler
        journal'ı korur ve PendingRecoveryError ile yeni girişleri kilitler.
        """
        async with self._pending_lock:
            return await self._recover_pending_locked()

    async def _recover_pending_locked(self) -> List[ScalpPosition]:
        if self._journal_error:
            raise PendingRecoveryError(
                f"Maker pending journal bozuk/okunamıyor: {self._journal_error}"
            )
        if self._journal_path is None or not self._journal_records:
            self._recovery_needed = False
            return []

        try:
            open_rows = await self.tracker.open_trades()
        except Exception as e:
            raise PendingRecoveryError(
                f"Maker recovery DB OPEN kayıtlarını okuyamadı: {e}"
            ) from e

        open_symbols: Set[str] = set()
        for row in open_rows:
            symbol = row.get("symbol") if isinstance(row, dict) else getattr(row, "symbol", None)
            if symbol:
                open_symbols.add(str(symbol))

        opened: List[ScalpPosition] = []
        unresolved: List[str] = []
        for symbol, record in list(self._journal_records.items()):
            if symbol in open_symbols:
                # DB commit crash'ten önce tamamlanmış. Aynı fill'i yeniden
                # finalize etmek çift SL/TP yaratır; exits recover sorumludur.
                existing = self._pending.get(symbol)
                self._remove_pending_record(symbol)
                if existing is not None:
                    self._pending.pop(symbol, None)
                self.logger.warning(
                    f"♻️ {symbol}: DB'de OPEN bulundu; maker journal temizlendi, "
                    "pozisyon exits recovery'ye bırakıldı"
                )
                continue

            try:
                pending = self._pending.get(symbol) or self._deserialize_pending(record)
            except PendingRecoveryError as e:
                self._journal_error = str(e)
                raise
            self._pending[symbol] = pending

            order: Optional[Dict[str, Any]] = None
            not_found = 0
            last_error: Optional[Exception] = None
            for attempt in range(3):
                try:
                    candidate = await self.client.get_order_by_client_id(
                        symbol, pending.client_order_id
                    )
                    if not candidate or (
                        candidate.get("orderId") is None
                        and candidate.get("status") is None
                    ):
                        not_found += 1
                    else:
                        order = dict(candidate)
                        break
                except BinanceAPIError as e:
                    if e.code == -2013:
                        not_found += 1
                    else:
                        last_error = e
                except Exception as e:
                    last_error = e
                if attempt < 2:
                    await asyncio.sleep(0)

            if order is None:
                if not_found == 3 and last_error is None:
                    self.logger.warning(
                        f"♻️ {symbol}: maker intent üç sorguda kesin bulunamadı; "
                        "journal kaydı temizleniyor"
                    )
                    self._drop_pending(symbol, pending)
                    continue
                unresolved.append(f"{symbol}: clientOrderId sorgusu belirsiz ({last_error})")
                continue

            self._record_order_state(pending, order)

            # Crash, koruma kurulurken veya DB commit/cleanup arasında olmuş
            # olabilir. DB kaydı yoksa tekrar finalize etmek yerine güvenli
            # emergency close + flat doğrulaması yapılır.
            if pending.phase in ("PROTECTING", "RECOVERY_REQUIRED", "DB_OPEN"):
                if order.get("status") in ("NEW", "PARTIALLY_FILLED"):
                    unresolved.append(
                        f"{symbol}: koruma-phase journal ile non-terminal emir "
                        f"durumu çelişiyor ({order.get('status')}); kayıt korunuyor"
                    )
                    continue
                try:
                    await self._recover_uncertain_protection(symbol, pending)
                except PendingRecoveryError as e:
                    unresolved.append(str(e))
                continue

            status = order.get("status")
            if status not in (
                "NEW",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELED",
                "EXPIRED",
                "EXPIRED_IN_MATCH",
                "REJECTED",
            ):
                unresolved.append(f"{symbol}: bilinmeyen recovery emir durumu {status!r}")
                continue

            try:
                scalp_position = await self._process_pending_order(
                    symbol, pending, order
                )
            except UnprotectedPositionError as e:
                unresolved.append(str(e))
                continue
            if scalp_position is not None:
                opened.append(scalp_position)
            remaining = self._pending.get(symbol)
            if remaining is not None and (
                remaining.phase in ("PROTECTING", "RECOVERY_REQUIRED", "DB_OPEN")
                or remaining.last_status == "PARTIALLY_FILLED"
            ):
                unresolved.append(
                    f"{symbol}: kısmi dolum/koruma recovery'si terminal olmadı"
                )

        if unresolved:
            self._recovery_needed = True
            raise PendingRecoveryError("; ".join(unresolved))

        self._recovery_needed = False
        return opened

    async def handle_order_update(
        self, event: Dict[str, Any]
    ) -> Optional[ScalpPosition]:
        """ORDER_TRADE_UPDATE eventini REST polling ile aynı kilitte işle.

        Yalnız awa2sc_ prefix'li ve RAM pending ile clientOrderId'si birebir
        eşleşen giriş eventleri kabul edilir. Kilit, WS ile REST aynı FILLED'i
        eşzamanlı görse bile finalize/SL/DB akışının exactly-once olmasını
        sağlar.
        """
        if event.get("e") != "ORDER_TRADE_UPDATE":
            return None
        raw_order = event.get("o")
        if not isinstance(raw_order, dict):
            return None
        client_order_id = str(raw_order.get("c") or "")
        if not client_order_id.startswith("awa2sc_"):
            return None
        symbol = str(raw_order.get("s") or "")
        if not symbol:
            return None

        async with self._pending_lock:
            if self._journal_error:
                raise PendingRecoveryError(
                    f"Maker pending journal bozuk/okunamıyor: {self._journal_error}"
                )
            if self._recovery_needed:
                raise PendingRecoveryError(
                    "User stream eventi geldi ancak restart journal recovery tamamlanmadı"
                )
            pending = self._pending.get(symbol)
            if pending is None or pending.client_order_id != client_order_id:
                return None

            order: Dict[str, Any] = {
                "symbol": symbol,
                "clientOrderId": client_order_id,
                "orderId": raw_order.get("i"),
                "status": raw_order.get("X"),
                "executedQty": raw_order.get("z", "0"),
                "avgPrice": raw_order.get("ap", "0"),
                "origQty": raw_order.get("q"),
                "price": raw_order.get("p"),
                "side": raw_order.get("S"),
            }
            return await self._process_pending_order(symbol, pending, order)

    async def cancel_all_pending(self) -> List[ScalpPosition]:
        """Tüm pending girişleri race-safe iptal et; belirsiz olanı koru."""
        async with self._pending_lock:
            if self._journal_error:
                raise PendingRecoveryError(
                    f"Maker pending journal bozuk/okunamıyor: {self._journal_error}"
                )
            opened: List[ScalpPosition] = []
            if self._recovery_needed:
                opened.extend(await self._recover_pending_locked())
            opened.extend(await self._cancel_all_pending_locked())
            return opened

    async def _cancel_all_pending_locked(self) -> List[ScalpPosition]:
        opened: List[ScalpPosition] = []
        for symbol, pending in list(self._pending.items()):
            try:
                sp = await self._cancel_pending(
                    symbol, pending, reason="cancel_all"
                )
            except UnprotectedPositionError:
                raise
            except Exception as e:
                self.logger.warning(
                    f"⚠️ {symbol}: toplu pending iptalinde beklenmeyen hata ({e}); "
                    f"pending korunuyor"
                )
                continue
            if sp is not None:
                opened.append(sp)
        return opened

    async def _place_tp_safely(
        self, symbol: str, side: str, price: float, quantity: float, label: str
    ) -> Optional[str]:
        """TP emrini koymayı dene; başarısızlık pozisyonu İPTAL ETTİRMEZ (SL zaten var)."""
        if quantity <= 0:
            self.logger.warning(f"⚠️ {symbol}: {label} miktarı sıfır, atlanıyor")
            return None
        try:
            order = await self.client.place_take_profit(
                symbol=symbol, side=side, stop_price=price, quantity=quantity
            )
            return self._extract_id(order)
        except BinanceAPIError as e:
            self.logger.error(
                f"⚠️ {symbol}: {label} konulamadı (kod={e.code}: {e.msg}). "
                f"Pozisyon SL ile korunuyor, {label} olmadan devam ediliyor."
            )
            return None
        except Exception as e:
            self.logger.error(
                f"⚠️ {symbol}: {label} konulurken beklenmeyen hata ({e}). "
                f"Pozisyon SL ile korunuyor, {label} olmadan devam ediliyor."
            )
            return None

    @staticmethod
    def _extract_id(order: dict) -> Optional[str]:
        value = order.get("algoId") or order.get("orderId")
        return str(value) if value is not None else None
