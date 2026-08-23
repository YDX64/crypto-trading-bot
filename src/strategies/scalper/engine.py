"""
ScalperEngine — scalper alt sisteminin orkestrasyon katmanı.

Kendi ImprovedBinanceClient + PositionManager çiftini kurar (orchestrator'ın
Telegram akışıyla PAYLAŞMAZ — iki bağımsız istemci, iki bağımsız bağlantı
havuzu; scalper'ın hızlı tarama döngüsü Telegram sinyal akışını asla
bloklamaz). KlineFetcher/UniverseScanner de public/imzasız endpoint'ler
üzerinden kendi httpx havuzlarını kurar (data.py/scanner.py'nin kendi
tasarım ilkeleri).

İki bağımsız döngü vardır:
  * Safety döngüsü (varsayılan 2sn): exits.step(), günlük zarar kesici ve
    maker dolum takibi. Uzun sembol taraması bu koruma işlerini geciktiremez.
  * Scan döngüsü (scalper_scan_interval_seconds): evreni ve stratejileri
    tarar. Kill switch ve kapasite, emir açılmadan hemen önce tekrar
    doğrulanır.

Hata izolasyonu: bir sembolün hatası turu öldürmez (sembol başına
try/except), ama asyncio.CancelledError her zaman yükseltilir (görev
iptali yutulmaz).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.scalper.data import (
    KlineFetcher,
    MarketDataGuard,
    MarketDataRequestError,
    MarketDataUnavailable,
    host_of,
)
from src.strategies.scalper.executor import ScalpExecutor
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.indicators import atr as compute_atr
from urllib.parse import urlsplit

from src.strategies.scalper.market_gate import (
    MARKET_GATE_INTRADAY_LIMIT,
    MARKET_GATE_INTRADAY_TF,
    evaluate_market_gate,
    market_gate_metrics,
    resolve_day_open,
    utc_day_start_ms,
)
from src.strategies.scalper.regime import detect_regime
from src.strategies.scalper.scanner import UniverseScanner
from src.strategies.scalper.setups import apply_stop_policy, get_enabled
from src.strategies.scalper.structure import (
    StructureExitInput,
    detect_structure,
    structure_exit_action,
    structure_exit_mode,
    structure_gate_blocks,
    structure_pivot,
    structure_snapshot,
    structure_state_for,
    structure_timeframe,
    structure_use_close,
    structure_window_bars,
)
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
)
from src.trading.binance_client_improved import ImprovedBinanceClient
from src.trading.position_manager import PositionManager, UnprotectedPositionError
from src.trading.symbol_reservations import symbol_reservations
from src.trading.user_stream import BinanceUserDataStream


# Lider piyasa kapısı (D15): günlük seri talebinin ASGARİ üstüne eklenen pay.
# Uzama alt-kapısı N günlük koşu için N+1 TAMAMLANMIŞ kapanış ister; oluşmakta
# olan mum atıldığı için asgari N+2'dir. Tam asgariyi istemek SIFIR PAYLIDIR:
# borsanın tek bir eksik/geç günlük mumu alt-kapıyı sessizce fail-open yapar.
# Ağırlık değişmez (limit ≤ 100 → ağırlık 1), bu yüzden pay ucuzdur.
_LEADER_DAILY_LIMIT_MARGIN = 5


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_pct(value: Optional[float]) -> str:
    """Log için yüzde biçimi; hesaplanamayan büyüklük '?' (0.0 DEĞİL)."""
    if value is None:
        return "?"
    return f"{value:+.2f}%"


class _ExternalSignalStrategy:
    """TradingView webhook'undan gelen tek-atımlık sinyal sarmalayıcısı.

    StrategyProtocol uyumlu: _evaluate_symbol'ün normal akışına takılır,
    böylece dış sinyal de İÇ sinyallerle AYNI hattan geçer — stop politikası
    (fixed_roi/ATR taban), risk boyutlama, maker giriş, TP/BE/chandelier,
    cooldown, kapasite ve rezervasyon kapılarının hiçbiri atlanmaz. Yalnız
    setup koşulları (RSI/BB/diverjans) dışarıya devredilmiştir — yön ve
    zamanlama TradingView'in işidir.

    seed stop = giriş ∓ 1×ATR yalnız TOHUM değerdir; apply_stop_policy canlı
    profile göre (örn. fixed_roi %50) yeniden yazar.
    """

    name = "TV"

    def __init__(self, direction: Direction):
        self._direction = direction

    def evaluate(self, ctx: StrategyContext) -> Optional[ScalpSignal]:
        if ctx.atr_5m <= 0.0 or ctx.current_price <= 0.0:
            return None
        if self._direction == Direction.LONG:
            seed_stop = ctx.current_price - ctx.atr_5m
            if seed_stop <= 0.0:
                return None
        else:
            seed_stop = ctx.current_price + ctx.atr_5m
        return ScalpSignal(
            strategy=self.name,
            symbol=ctx.symbol,
            direction=self._direction,
            entry_price=ctx.current_price,
            stop_price=seed_stop,
            reason="TradingView webhook sinyali",
            regime=ctx.regime,
            atr_5m=ctx.atr_5m,
        )


class ScalperEngine:
    """Tarama → sinyal değerlendirme → giriş → çıkış döngüsünü yürütür."""

    _REGIME_CACHE_TTL = 300.0    # saniye — sembol başına rejim önbelleği
    _BALANCE_CACHE_TTL = 300.0   # saniye — kill switch için bakiye önbelleği
    # 2026-08-14: 30→120 sn. /fapi/v1/income weight=30; 30 sn TTL tek başına
    # 60 weight/dk yiyordu — testnet'in düşük IP bütçesinde 418'e katkı.
    # Kill switch tepkisi bu TTL'e MAHKUM DEĞİL: her tracker.record_close
    # önbelleği düşürür (close_seq karşılaştırması), yani limiti aşan kapanış
    # bir sonraki kill-switch turunda sıfır ek REST maliyetiyle görülür.
    _INCOME_CACHE_TTL = 120.0
    # Sanal sermaye çözümü income endpoint'ine değil sizing/bakiye okumalarına
    # dayanır — income diyeti gerekçesi onu kapsamaz, eski 30 sn'de kalır.
    _VIRTUAL_EQUITY_CACHE_TTL = 30.0
    _EXCHANGE_PROBE_INTERVAL = 30.0
    _RESERVATION_OWNER = "scalper"
    # Piyasa verisi kesintisi uyarısının azami sıklığı (sn). Tarama turu 30
    # sn'de bir döner; uzun bir banda (180 sn+) her tur satır basmak logu
    # doldurur ve gerçek olayları gizler.
    _SCAN_DEGRADED_LOG_INTERVAL = 60.0
    # Risk-olayı halt dosyası _evaluate_symbol'ün sembol döngüsünde her
    # sinyal denemesinde okunabilir — kısa TTL disk I/O'yu boğmadan ~1sn
    # içinde halt/resume'u yansıtır (POST /risk-event canlı etkisi için
    # yeterince taze).
    _RISK_EVENT_HALT_CACHE_TTL = 1.0
    # Lider piyasa kapısı (D15): sembol BAŞINA değil, LİDER başına tek
    # anlık görüntü. 60 sn TTL — REST ağırlık diyeti (docs/RUNBOOK.md
    # "418/ban" + docs/ARCHITECTURE.md ağırlık bölümü): 20 sembol × 3 istek
    # yerine dakikada 3 istek — 1d (limit RUN_DAYS + _LEADER_DAILY_LIMIT_MARGIN,
    # tavan 100), giriş TF (limit 3) ve 15m (limit 100, gerçek gün açılışı
    # için); ÜÇÜ DE limit ≤ 100 olduğundan ağırlık 1, toplam 3 ağırlık/dakika
    # (bütçe 2400/dk). Tarama aralığı 60 sn olduğu için pratikte tur başına
    # en çok bir tazeleme yapılır — ama kapı AÇIKSA her tur bir tazeleme
    # YAPILIR (bkz. _refresh_leader_snapshot): alt sınır 0 değil ~3/dk.
    _MARKET_GATE_CACHE_TTL = 60.0
    # Kapı WARNING'lerinin oran sınırı (sn): lider verisi KALICI olarak
    # alınamıyorsa her sinyal denemesinde bir satır basmak bot.log'u boğar ve
    # gerçek arızayı gizler — dakikada en çok bir satır.
    _MARKET_GATE_WARN_INTERVAL = 60.0
    # Başlangıçtaki lider doğrulamasının zaman sınırı (sn) — motor açılışı
    # ulaşılamayan bir borsada dakikalarca beklememeli.
    _MARKET_GATE_VALIDATE_TIMEOUT = 15.0
    # Tur başı lider tazelemesinin zaman sınırı (sn). KRİTİK: tazeleme
    # `_scan_tick`'in İLK adımı ve `await`li; `KlineFetcher` 3 deneme ×
    # (15 sn okuma timeout'u + 1s/2s backoff) yaptığı için sınırsız bırakılsa
    # lider erişilemezken TARAMA TURUNU ~48 sn bloke eder. Kapı tavsiye
    # niteliğinde bir alt-sistemdir: giriş hattı ONUN yüzünden GECİKMEMELİ.
    _MARKET_GATE_REFRESH_TIMEOUT = 10.0
    # Anlık görüntü bu katsayı × tarama aralığından eskiyse BAYAT sayılır:
    # `gate_effective` false olur ve türetilmiş metrikler null verilir.
    _MARKET_GATE_STALE_SCANS = 2.0

    def __init__(self) -> None:
        self.cfg = settings
        self.logger = app_logger

        # Orchestrator'dan bağımsız kendi istemci çifti.
        self.client = ImprovedBinanceClient()
        self.pm = PositionManager(self.client)
        # D17: piyasa verisi (yalnız public /fapi/v1/klines) ayrı bir host'tan
        # çekilebilir — "veri mainnet'ten, emirler testnet'te". Boş ayar =
        # bugünkü davranış (KlineFetcher settings.binance_base_url'e düşer).
        # DİKKAT: `self.client` (imzalı: emir/bakiye/pozisyon) ve `self.scanner`
        # (24s ticker → evren sıralaması) BİLİNÇLİ olarak İŞLEM host'unda kalır;
        # evrenin işlem yapılamayan sembollerle dolması kabul edilemez.
        market_data_base_url = str(
            getattr(settings, "scalper_market_data_base_url", "") or ""
        ).strip()
        self.fetcher = KlineFetcher(base_url=market_data_base_url or None)
        self.scanner = UniverseScanner(top_n=settings.scalper_top_n)
        self.tracker = ScalpTracker()
        self.executor = ScalpExecutor(self.client, self.pm, self.tracker, self.cfg)
        self.exits = ExitManager(
            self.client, self.pm, self.tracker, self.cfg, self.fetcher.get_klines,
            loss_cooldown_cb=self.executor.start_loss_cooldown,
        )

        # _task eski iç kullanımlar için scan task alias'ı olarak korunur.
        self._task: Optional[asyncio.Task] = None
        self._safety_task: Optional[asyncio.Task] = None
        self._exchange_task: Optional[asyncio.Task] = None
        self._entry_lock = asyncio.Lock()
        # reserve() ile try_open() arasındaki await'ler sırasında safety
        # döngüsü normal senkronizasyonda sembolü erken bırakmamalı. Başarılı
        # giriş pending/tracked durumuna geçene kadar bu küme ownership'i canlı
        # tutar; koruma hatasında ise global safety latch devralır.
        self._opening_symbols: Set[str] = set()
        self.running = False
        self.user_stream = BinanceUserDataStream(
            self.client,
            self._handle_user_order_update,
            ws_base_url=getattr(self.cfg, "binance_ws_base_url", None),
        )

        # Anlık durum — snapshot() bunları okur.
        self._universe: List[str] = []
        self._regimes: Dict[str, str] = {}
        # Sembol -> piyasa yapısı özeti (BOS/CHoCH). Kapı KAPALIYKEN de
        # doldurulur: operatör kapıyı açmadan önce ne yapacağını
        # /scalper/status'tan izleyebilsin (gözlem ucuz, karar değil).
        self._structure: Dict[str, Dict[str, Any]] = {}
        self._regime_cache: Dict[str, Tuple[Regime, float]] = {}
        # Lider piyasa kapısı (D15): {lider: (anlık_görüntü, monotonic_ts,
        # utc_gün_damgası)}. Anlık görüntü türetilmiş metrikleri de taşır ki
        # senkron snapshot() hiç IO yapmadan /scalper/status'e yazabilsin.
        # Gün damgası: "gün açılışı" UTC gün sınırında DEĞİŞİR — TTL'i (60 sn)
        # gün sınırına taşan bir önbellek DÜNÜN açılışıyla karar verirdi.
        self._market_gate_cache: Dict[str, Tuple[Dict[str, Any], float, int]] = {}
        self._market_gate_rejects: Dict[str, int] = {}
        self._market_gate_last_reason: Optional[str] = None
        self._market_gate_last_block_at: Optional[str] = None
        # Kapının GÖRÜNÜRLÜĞÜ (fail-open sessiz kalmasın): lider doğrulaması ve
        # son veri denemesinin sonucu. leader_ok None = henüz denenmedi.
        self._market_gate_leader_ok: Optional[bool] = None
        self._market_gate_last_ok_at: Optional[str] = None
        self._market_gate_last_error: Optional[str] = None
        self._market_gate_consecutive_failures: int = 0
        # Kümülatif: toparlanmada SIFIRLANMAZ (dönüşümlü arıza görünür kalsın).
        self._market_gate_failures_total: int = 0
        self._market_gate_last_failure_at: Optional[str] = None
        # Negatif önbellek: başarısızlıktan sonra bu monotonic ana kadar
        # yeniden DENENMEZ (boşa REST + kline kilidi tutmayı önler).
        self._market_gate_retry_after: float = 0.0
        self._market_gate_warn_at: Dict[str, float] = {}
        self._balance_cache: Tuple[Optional[float], float] = (None, 0.0)
        self._daily_pnl: float = 0.0
        self._daily_pnl_source: str = "unavailable"
        self._risk_equity_usdt: Optional[float] = None
        self._risk_equity_source: str = "unavailable"
        self._daily_loss_threshold_usdt: Optional[float] = None
        self._virtual_equity_cache: Tuple[Optional[float], float] = (None, 0.0)
        self._daily_income_cache: Tuple[Optional[float], float, Optional[str]] = (
            None,
            0.0,
            None,
        )
        # Önbellek dolduğunda tracker.close_seq'in değeri: sonradan bir
        # kapanış kaydedilirse (seq artar) TTL dolmadan taze okunur.
        self._income_cache_close_seq: int = -1
        self._risk_ready: bool = False
        self._kill_switch: bool = False
        self._kill_switch_day: Optional[str] = None
        # UnprotectedPositionError görülürse restart/reconcile edilene kadar
        # açılmaz. Safety döngüsü mevcut pozisyonları izlemeyi sürdürür.
        self._entry_halted: bool = False
        self._entry_halt_reason: Optional[str] = None
        self._entry_halted_at: Optional[str] = None
        configured_halt_path = getattr(
            self.cfg, "scalper_entry_halt_path", None
        )
        self._entry_halt_path: Optional[Path] = (
            Path(configured_halt_path).expanduser() if configured_halt_path else None
        )
        self._load_entry_halt()
        # Risk-olayı kanalı (D10): scalper_entry_halt_path'ten AYRI durum
        # dosyası — bkz. _risk_event_halt_snapshot. TTL'li olduğu için
        # (scalper_entry_halt gibi) başlangıçta RAM'e yüklenip tutulmaz;
        # her okumada dosyadan (kısa TTL önbellekli) taze değerlendirilir.
        configured_risk_event_halt_path = getattr(
            self.cfg, "risk_event_halt_path", None
        )
        self._risk_event_halt_path: Optional[Path] = (
            Path(configured_risk_event_halt_path).expanduser()
            if configured_risk_event_halt_path
            else None
        )
        self._risk_event_halt_cache: Optional[Tuple[float, Dict[str, Any]]] = None
        # RAM latch (D): dosya yazımı BAŞARISIZ olsa bile halt RAM'de
        # otoriter kalır — bkz. _persist_risk_event_halt / risk_event_halt.
        self._risk_event_halt_ram: Optional[Dict[str, Any]] = None
        self._exchange_ready: bool = False
        self._recovery_ready: bool = False
        self._exchange_last_success_at: Optional[str] = None
        self._exchange_last_success_monotonic: Optional[float] = None
        self._exchange_last_error: Optional[str] = None
        self._exchange_last_error_at: Optional[str] = None
        self._exchange_success_count: int = 0
        self._signals_today: int = 0
        self._last_scan_at: Optional[str] = None

        # Döngü telemetrisi. ISO zamanlar API için, monotonic zamanlar
        # saat ayarı değişimlerinden etkilenmeyen freshness hesabı içindir.
        self._scan_last_started_at: Optional[str] = None
        self._scan_last_success_at: Optional[str] = None
        self._scan_last_success_monotonic: Optional[float] = None
        self._scan_last_duration_seconds: Optional[float] = None
        self._scan_last_error: Optional[str] = None
        self._scan_last_error_at: Optional[str] = None
        self._scan_consecutive_errors: int = 0
        self._scan_success_count: int = 0
        # D17 (düşmanca inceleme): piyasa verisi kesintisiyle YARIDA KESİLEN
        # bir tur "başarılı" DEĞİLDİR. Hata da değildir (fail-closed davranış,
        # `_scan_tick` bilinçli olarak `break` eder) — bu yüzden ayrı sayaç +
        # ayrı durum etiketiyle görünür kılınır. `_scan_last_success_monotonic`
        # BİLİNÇLİ tazelenmeye devam eder: freshness'ı düşürmek watchdog
        # restart'ını davet eder, ki bu 2026-08-14 felaket yoludur (ban
        # ortasında restart → toplu UNKNOWN kapanış).
        self._scan_degraded_reason: Optional[str] = None
        self._scan_degraded_at: Optional[str] = None
        self._scan_degraded_count: int = 0
        self._scan_degraded_log_at: float = 0.0

        self._safety_last_started_at: Optional[str] = None
        self._safety_last_success_at: Optional[str] = None
        self._safety_last_success_monotonic: Optional[float] = None
        self._safety_last_duration_seconds: Optional[float] = None
        self._safety_last_error: Optional[str] = None
        self._safety_last_error_at: Optional[str] = None
        self._safety_consecutive_errors: int = 0
        self._safety_success_count: int = 0

    # ------------------------------------------------------------------
    # Yaşam döngüsü
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if (
            self.running
            and self._task
            and not self._task.done()
            and self._safety_task
            and not self._safety_task.done()
            and self._exchange_task
            and not self._exchange_task.done()
        ):
            self.logger.info("ℹ️ Scalper motoru zaten çalışıyor")
            return

        self.logger.info("⚡ Scalper motoru başlatılıyor...")
        self._maybe_log_shadow_mode_banner()
        self._maybe_log_market_gate_banner()
        self._log_kline_source()
        self.logger.info(
            f"🎯 Evren={self.cfg.scalper_top_n} sembol, tarama={self.cfg.scalper_scan_interval_seconds}sn, "
            f"safety={self._safety_interval_seconds():g}sn, "
            f"stratejiler={self.cfg.scalper_strategies}, kaldıraç={self.cfg.scalper_leverage}x"
        )
        if await self._probe_exchange():
            # Kapı açıksa liderin borsada GERÇEKTEN var olduğunu doğrula:
            # yanlış yazılmış bir sembol kapıyı SESSİZCE devre dışı bırakırdı
            # (fail-open). Borsa HAZIRSA anlamlı — bu yüzden probe'un içinde;
            # probe başarısızsa `leader_ok` None kalır (= "henüz denenmedi",
            # `gate_effective` false) ve ilk tarama turu kendiliğinden çözer.
            await self._validate_market_gate_leader()
            await self._attempt_recovery()
            await self._update_kill_switch()

        self.running = True
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="scalper-scan-loop")
        if not self._safety_task or self._safety_task.done():
            self._safety_task = asyncio.create_task(
                self._safety_loop(), name="scalper-safety-loop"
            )
        if not self._exchange_task or self._exchange_task.done():
            self._exchange_task = asyncio.create_task(
                self._exchange_loop(), name="scalper-exchange-readiness-loop"
            )
        await self.user_stream.start()

        self.logger.info("✅ Scalper scan ve safety görevleri başlatıldı")

    async def stop(self) -> None:
        self.logger.info("🛑 Scalper motoru durduruluyor...")
        self.running = False
        tasks = [
            task
            for task in (self._task, self._safety_task, self._exchange_task)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Yeni WS fill olayı ile shutdown iptali yarışmasın; REST terminal
        # reconciliation aşağıda executor kilidi altında devam eder.
        await self.user_stream.stop()

        # Maker modunda bekleyen tüm LIMIT girişlerini iptal et — temiz
        # kapanış (client henüz kapatılmadan, aşağıdaki closer döngüsünden
        # ÖNCE çalışmalı).
        try:
            opened_during_cancel = await self.executor.cancel_all_pending()
            self._track_opened_positions(
                opened_during_cancel, source="shutdown pending iptal yarışı"
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Bekleyen maker girişleri iptal edilirken hata: {e}")

        self._sync_scalper_reservations()

        for closer in (self.fetcher.close, self.scanner.close, self.client.close):
            try:
                await closer()
            except Exception as e:
                self.logger.warning(f"⚠️ Scalper motoru kapatılırken kaynak temizleme hatası: {e}")

        self.logger.info("✅ Scalper motoru durduruldu")

    # ------------------------------------------------------------------
    # Restart recovery, signed readiness and persistent safety latch
    # ------------------------------------------------------------------

    def _load_entry_halt(self) -> None:
        path = self._entry_halt_path
        if not getattr(self.cfg, "scalper_entry_halt_enabled", True):
            if path is not None and path.exists():
                self.logger.warning(
                    "⚠️ Entry halt devre dışı (scalper_entry_halt_enabled=false); "
                    f"mevcut halt dosyası yok sayılıyor: {path}"
                )
            return
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("active") is not True:
                raise ValueError("entry halt state şeması geçersiz")
            self._entry_halted = True
            self._entry_halt_reason = str(
                payload.get("reason") or "persisted safety hold"
            )
            self._entry_halted_at = str(payload.get("halted_at") or _utcnow_iso())
            self.logger.critical(
                f"🚨 Kalıcı scalper entry hold yüklendi: {self._entry_halt_reason}. "
                "Restart yeni girişleri açmayacak.",
                extra={"trade": True},
            )
        except Exception as e:
            # Bozuk güvenlik dosyası fail-open olamaz.
            self._entry_halted = True
            self._entry_halt_reason = f"entry halt state okunamadı: {type(e).__name__}: {e}"
            self._entry_halted_at = _utcnow_iso()
            self.logger.critical(
                f"🚨 {self._entry_halt_reason}; yeni girişler fail-closed kapalı",
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
                "active": True,
                "reason": self._entry_halt_reason,
                "halted_at": self._entry_halted_at,
            }
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except Exception as e:
            self.logger.critical(
                f"🚨 Entry hold RAM'de aktif ancak kalıcılaştırılamadı ({path}): {e}",
                extra={"trade": True},
            )
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Risk-olayı kanalı (D10, POST /risk-event) — bkz. docs/INTEGRATIONS.md §3
    # ------------------------------------------------------------------
    # `state/risk_event_halt.json`, `state/scalper_entry_halt.json`'dan
    # BİLİNÇLİ olarak AYRI bir dosyadır:
    #   * scalper_entry_halt → yalnız KORUMA HATASINDA (UnprotectedPositionError)
    #     otomatik tetiklenir, `scalper_entry_halt_enabled=false` iken hem
    #     yüklenmesi hem yeni latch'lenmesi ATLANIR (bkz. _load_entry_halt,
    #     _latch_entry_halt) — canlı sunucu bu bayrağı false tutuyor.
    #   * risk_event_halt → yalnız bu API ile (haber/olay botu) YAZILIR,
    #     `scalper_entry_halt_enabled` bayrağından TAMAMEN bağımsız her zaman
    #     `_entries_ready()` tarafından uygulanır (yukarıdaki yorum), TTL ile
    #     kendiliğinden süresi dolar, fail-closed: dosya bozuksa/parse
    #     edilemezse HALT AKTİF sayılır (aksi "sessizce fail-open" olurdu).
    # Açık pozisyonların SL/TP/trailing yönetimi bu kanaldan HİÇ etkilenmez —
    # yalnız YENİ giriş kapılarını (_entries_ready) etkiler.

    def _risk_event_halt_snapshot(self, *, force: bool = False) -> Dict[str, Any]:
        """`risk_event_halt_path`'i oku (kısa TTL önbellekli), fail-closed.

        Dönüş: {"active": bool, "reason": str|None, "source": str|None,
        "until_ts": float|None}. `force=True` önbelleği atlar (halt/resume/
        flatten API çağrılarından hemen sonra taze durum döndürmek için).
        """
        path = getattr(self, "_risk_event_halt_path", None)
        now_mono = time.monotonic()
        cached = getattr(self, "_risk_event_halt_cache", None)
        if (
            not force
            and cached is not None
            and (now_mono - cached[0]) < self._RISK_EVENT_HALT_CACHE_TTL
        ):
            return cached[1]

        if path is None or not path.exists():
            snapshot: Dict[str, Any] = {
                "active": False, "reason": None, "source": None, "until_ts": None,
            }
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("risk_event_halt şeması geçersiz (dict değil)")
                until_ts = float(payload["until_ts"])
                reason = str(payload.get("reason") or "risk olayı")
                raw_source = payload.get("source")
                source = str(raw_source) if raw_source not in (None, "") else None
            except Exception as e:
                # Fail-closed: bozuk/okunamayan risk-event halt dosyası
                # "resume" gibi davranamaz — _load_entry_halt ile aynı ilke.
                snapshot = {
                    "active": True,
                    "reason": f"risk_event_halt dosyası okunamadı: {type(e).__name__}: {e}",
                    "source": None,
                    "until_ts": None,
                }
            else:
                if until_ts <= time.time():
                    snapshot = {
                        "active": False, "reason": None, "source": None,
                        "until_ts": until_ts,
                    }
                else:
                    snapshot = {
                        "active": True, "reason": reason, "source": source,
                        "until_ts": until_ts,
                    }

        # RAM latch (D): dosya yazımı başarısız olsa/olmasa da halt RAM'de
        # otoriter kalır — persist edilemeyen bir halt "aktif değil" gibi
        # davranamaz (fail-open olurdu). TTL kendiliğinden süresi dolar;
        # dosyanın süresi RAM'den daha ileri gösteriyorsa (ör. resume dosyayı
        # sildi ama RAM temizlenmeden önce okundu — pratikte olmaz, ama
        # savunmacı) dosya kazanır.
        ram = getattr(self, "_risk_event_halt_ram", None)
        if ram is not None:
            ram_until = float(ram.get("until_ts") or 0.0)
            if ram_until <= time.time():
                self._risk_event_halt_ram = None
            else:
                ram_snapshot = {
                    "active": True,
                    "reason": ram.get("reason"),
                    "source": ram.get("source"),
                    "until_ts": ram_until,
                }
                if not snapshot.get("active"):
                    snapshot = ram_snapshot
                elif (
                    snapshot.get("until_ts") is not None
                    and float(snapshot["until_ts"]) < ram_until
                ):
                    snapshot = ram_snapshot

        self._risk_event_halt_cache = (now_mono, snapshot)
        return snapshot

    def _persist_risk_event_halt(
        self, *, reason: str, source: Optional[str], until_ts: float
    ) -> bool:
        """Halt durumunu diske yaz. Dönüş: kalıcılaştırma başarılı mı.

        BAŞARISIZ olursa (veya yol yapılandırılmamışsa) `False` döner ama
        istisna FIRLATMAZ — çağıran `risk_event_halt`, RAM latch'ini (bkz.
        `_risk_event_halt_ram`) ÖNCEDEN kurmuş olduğu için halt yine de
        etkilidir; yalnız restart'ta kaybolur.
        """
        path = getattr(self, "_risk_event_halt_path", None)
        if path is None:
            return False
        tmp_path: Optional[Path] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            payload = {
                "version": 1,
                "reason": reason,
                "source": source,
                "until_ts": until_ts,
                "created_at": _utcnow_iso(),
            }
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            return True
        except Exception as e:
            self.logger.critical(
                f"🚨 risk-event halt RAM'de aktif ancak kalıcılaştırılamadı — "
                f"halt yalnız RAM'de ({path}): {e}",
                extra={"trade": True},
            )
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        finally:
            self._risk_event_halt_cache = None

    async def _cancel_pending_for_risk_event(self, *, source_label: str) -> None:
        """Halt/flatten anında bekleyen maker girişlerini hemen iptal et.

        `_latch_entry_halt`/`_safety_tick` ile aynı desen: entry_lock altında
        cancel_all_pending, dolan varsa _track_opened_positions ile izlemeye
        al. İptal başarısız olsa bile halt/flatten API çağrısı BAŞARISIZ
        sayılmaz — safety döngüsü zaten her turda aynı iptali dener.
        """
        async with self._entry_lock:
            if not self.executor.pending_symbols():
                return
            try:
                opened_during_cancel = await self.executor.cancel_all_pending()
                self._track_opened_positions(
                    opened_during_cancel, source=source_label
                )
            except Exception as cancel_error:
                self.logger.error(
                    f"⚠️ risk-event: bekleyen girişler iptal edilemedi: {cancel_error}",
                    exc_info=True,
                )

    async def risk_event_halt(
        self, *, reason: str, source: Optional[str], ttl_minutes: int
    ) -> Dict[str, Any]:
        """POST /risk-event action=halt|flatten tarafından çağrılır."""
        ttl_minutes = max(1, min(int(ttl_minutes), 1440))
        until_ts = time.time() + ttl_minutes * 60.0
        # RAM latch ÖNCE kurulur (D): _persist_risk_event_halt diske
        # yazamasa bile halt _risk_event_halt_snapshot üzerinden hemen
        # otoriter olur — _entries_ready() dosya olmadan da False döner.
        self._risk_event_halt_ram = {
            "reason": reason, "source": source, "until_ts": until_ts,
        }
        self._risk_event_halt_cache = None
        persisted = self._persist_risk_event_halt(
            reason=reason, source=source, until_ts=until_ts
        )
        # .bind(trade=True) ile loguru'ya kwarg GEÇİLMEZ → mesaj üzerinde
        # .format() ÇAĞRILMAZ (F): reason/source çağıran-kontrollü metindir,
        # "{...}" içerirse extra=... kwarg'lı critical() KeyError/IndexError
        # fırlatıp halt'ı 500'e düşürürdü — persist YİNE de yukarıda oldu.
        self.logger.bind(trade=True).critical(
            f"🚨 RİSK-OLAYI HALT: yeni scalper girişleri durduruldu — "
            f"neden='{reason}' kaynak={source or '-'} ttl={ttl_minutes}dk "
            f"kalıcı={persisted}"
        )
        await self._cancel_pending_for_risk_event(source_label="risk-event halt iptal yarışı")
        snap = dict(self._risk_event_halt_snapshot(force=True))
        snap["persisted"] = persisted
        return snap

    def risk_event_resume(self) -> Dict[str, Any]:
        """POST /risk-event action=resume tarafından çağrılır.

        YALNIZ risk_event_halt_path'i temizler — koruma-hatası
        scalper_entry_halt dosyasına DOKUNMAZ (ayrı kilit, ayrı kurtarma).
        """
        path = getattr(self, "_risk_event_halt_path", None)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                self.logger.error(
                    f"⚠️ risk-event halt dosyası silinemedi ({path}): {e}"
                )
        self._risk_event_halt_ram = None
        self._risk_event_halt_cache = None
        self.logger.warning(
            "✅ RİSK-OLAYI RESUME: giriş kilidi kaldırıldı", extra={"trade": True}
        )
        return self._risk_event_halt_snapshot(force=True)

    def risk_event_status(self) -> Dict[str, Any]:
        """POST /risk-event action=status tarafından çağrılır."""
        snapshot = self._risk_event_halt_snapshot()
        snapshot = dict(snapshot)
        snapshot["open_positions"] = len(self.exits.tracked_symbols())
        return snapshot

    async def risk_event_flatten(
        self, *, reason: str, source: Optional[str], ttl_minutes: int
    ) -> Dict[str, Any]:
        """POST /risk-event action=flatten: TÜM izlenen pozisyonları kapat.

        Reaper'ın (`_reap_aged_positions`) kullandığı AYNI reduce-only
        MARKET emir çağrısını (`_submit_reduce_only_market_close`) yeniden
        kullanır — YENİ bir emir yolu yazılmadı. Kapanış her sembol için
        borsa üzerinde doğrulanır (`_close_position_market`); doğrulanamayan
        sembol `errors`'a düşer ve izlemede KALIR (fail-closed — SL/TP asla
        doğrulanmadan iptal edilmez).

        Halt ÖNCE kurulur, kapatma turundan ÖNCE: kapanış turu sembol başına
        ~4 sn + ledger REST'i sürebilir; tarama döngüsü BAĞIMSIZ bir task'tır
        (`_loop`, `start()`), halt sonradan kurulursa `_entries_ready()` bu
        pencere boyunca AÇIK kalır ve scan/TV kanalının turun ORTASINDA açtığı
        pozisyon tek-atımlık `tracked_symbols()` anlık görüntüsüne girmediği
        için ASLA kapanmaz (yanıt yine de "flat" der). `risk_event_halt`
        bekleyen maker'ları da HALT ALTINDA iptal eder — ayrı bir
        `_cancel_pending_for_risk_event` çağrısına gerek kalmaz. Turdan sonra
        `tracked_symbols()` İKİNCİ kez taranır: halt kurulmasıyla eşzamanlı
        dolan (ör. WS fill yarışı) bir pozisyon varsa o da düzleştirilir.
        """
        halt_snapshot = await self.risk_event_halt(
            reason=reason, source=source, ttl_minutes=ttl_minutes
        )

        flattened: List[str] = []
        errors: List[str] = []
        seen: Set[str] = set()
        for _ in range(2):  # 2. tur: halt ile eşzamanlı dolan girişleri yakalar
            remaining = sorted(self.exits.tracked_symbols() - seen)
            if not remaining:
                break
            for symbol in remaining:
                seen.add(symbol)
                sp = self.exits._positions.get(symbol)
                if sp is None:
                    continue
                try:
                    closed = await self._close_position_market(symbol, sp)
                except Exception as e:
                    msg = f"{symbol}: {type(e).__name__}: {e}"
                    errors.append(msg)
                    self.logger.error(f"❌ risk-event flatten: {msg}", exc_info=True)
                    continue
                if closed:
                    flattened.append(symbol)
                    self.logger.critical(
                        f"🚨 risk-event flatten: {symbol} reduce-only kapatıldı",
                        extra={"trade": True},
                    )
                else:
                    msg = f"{symbol}: kapanış borsa üzerinde doğrulanamadı"
                    errors.append(msg)
                    self.logger.error(f"❌ risk-event flatten: {msg}")

        return {
            "flattened": flattened,
            "errors": errors,
            "halt": self._risk_event_halt_snapshot(force=True),
        }

    async def _probe_exchange(self) -> bool:
        """Prove signed Binance Futures account access; never fake readiness."""

        try:
            await self.client.get_all_positions()
        except Exception as e:
            self._exchange_ready = False
            self._exchange_last_error = f"{type(e).__name__}: {e}"
            self._exchange_last_error_at = _utcnow_iso()
            self.logger.error(
                f"❌ Scalper signed Binance readiness başarısız; yeni girişler kapalı: {e}"
            )
            return False

        self._exchange_ready = True
        self._exchange_last_success_at = _utcnow_iso()
        self._exchange_last_success_monotonic = time.monotonic()
        self._exchange_last_error = None
        self._exchange_success_count += 1
        return True

    async def _attempt_recovery(self) -> bool:
        """Reconcile persistent maker intents, then verify every OPEN scalp."""

        try:
            recovered_pending = await self.executor.recover_pending()
            self._track_opened_positions(
                recovered_pending, source="restart maker recovery"
            )
            exits_ok = await self.exits.recover()
        except UnprotectedPositionError as e:
            self._recovery_ready = False
            await self._latch_entry_halt(e, source="restart recovery")
        except Exception as e:
            self._recovery_ready = False
            self.logger.error(f"❌ Scalper restart recovery başarısız: {e}", exc_info=True)
        else:
            self._recovery_ready = bool(exits_ok)

        # Başarısız/yarım recovery'de de RAM'e yüklenmiş journal sembolleri
        # başka motor tarafından sahiplenilemez. Hold, ownership ile birlikte
        # korunur; restart asla bu sınırı temizlemez.
        for symbol in self.exits.tracked_symbols() | self.executor.pending_symbols():
            if not symbol_reservations.reserve(symbol, self._RESERVATION_OWNER):
                await self._latch_entry_halt(
                    UnprotectedPositionError(
                        f"{symbol}: restart recovery sırasında başka motor sahipliği bulundu"
                    ),
                    source="symbol ownership recovery",
                )
                self._recovery_ready = False
                break

        if not self._recovery_ready:
            self.logger.error(
                "⛔ Scalper recovery güvenliği kanıtlanamadı; safety izlemeye devam "
                "ediyor ancak yeni girişler kapalı"
            )
        return self._recovery_ready

    async def _exchange_loop(self) -> None:
        self.logger.info("🔐 Scalper signed exchange readiness döngüsü başladı")
        while self.running:
            try:
                ready = await self._probe_exchange()
                if ready and not self._recovery_ready:
                    await self._attempt_recovery()
            except asyncio.CancelledError:
                self.logger.info("🔐 Scalper exchange readiness döngüsü durduruldu")
                raise
            except Exception as e:
                self._exchange_ready = False
                self._exchange_last_error = f"{type(e).__name__}: {e}"
                self._exchange_last_error_at = _utcnow_iso()
                self.logger.error(f"❌ Exchange readiness döngüsü hatası: {e}", exc_info=True)
            await asyncio.sleep(self._EXCHANGE_PROBE_INTERVAL)

    async def _handle_user_order_update(self, event: Dict[str, Any]) -> None:
        """Protect a maker fill immediately; REST safety loop remains fallback."""

        try:
            sp = await self.executor.handle_order_update(event)
            if sp is not None:
                self._track_opened_positions([sp], source="ORDER_TRADE_UPDATE")
        except UnprotectedPositionError as e:
            await self._latch_entry_halt(e, source="user-data stream")
        except Exception as e:
            self.logger.error(
                f"❌ Binance order update işlenemedi; REST reconciliation sürecek: {e}",
                exc_info=True,
            )

    def _entries_ready(self) -> bool:
        exchange_age = (
            time.monotonic() - self._exchange_last_success_monotonic
            if self._exchange_last_success_monotonic is not None
            else float("inf")
        )
        # Risk-olayı halt'ı scalper_entry_halt_enabled'tan BAĞIMSIZ uygulanır
        # (bkz. _risk_event_halt_snapshot) — canlı sunucu bu bayrağı false
        # tutsa bile (yalnız koruma-hatası otomatik latch'ini gater) haber
        # botunun halt'ı yeni girişleri durdurur.
        risk_event_active = bool(self._risk_event_halt_snapshot().get("active"))
        return bool(
            self._exchange_ready
            and exchange_age <= self._EXCHANGE_PROBE_INTERVAL * 3.0
            and self._recovery_ready
            and self._risk_ready
            and not self._entry_halted
            and not self._kill_switch
            and not risk_event_active
        )

    def _sync_scalper_reservations(self) -> None:
        """Release normal closed/cancelled symbols, never an active safety hold."""

        if self._entry_halted:
            return
        active = (
            self.exits.tracked_symbols()
            | self.executor.pending_symbols()
            | set(self._opening_symbols)
        )
        for symbol, owner in symbol_reservations.snapshot().items():
            if owner == self._RESERVATION_OWNER and symbol not in active:
                symbol_reservations.release(symbol, self._RESERVATION_OWNER)

    # ------------------------------------------------------------------
    # Ana döngü
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        self.logger.info("👁️ Scalper tarama döngüsü başladı")
        while self.running:
            started = time.monotonic()
            self._scan_last_started_at = _utcnow_iso()
            # Tur başında temizlenir: yalnız BU turda oluşan bir kesinti
            # turu "degraded" yapar (bkz. _mark_scan_degraded).
            self._scan_degraded_reason = None
            try:
                await self._scan_tick()
            except asyncio.CancelledError:
                self.logger.info("👁️ Scalper tarama döngüsü durduruldu")
                raise
            except UnprotectedPositionError as e:
                await self._latch_entry_halt(e, source="scan")
                self._scan_consecutive_errors += 1
                self._scan_last_error = f"{type(e).__name__}: {e}"
                self._scan_last_error_at = _utcnow_iso()
            except Exception as e:
                self._scan_consecutive_errors += 1
                self._scan_last_error = f"{type(e).__name__}: {e}"
                self._scan_last_error_at = _utcnow_iso()
                self.logger.error(f"❌ Scalper döngü hatası: {e}", exc_info=True)
            else:
                succeeded_at = _utcnow_iso()
                # Freshness (watchdog) her hâlükârda tazelenir: piyasa verisi
                # kesintisinde "unhealthy" göstermek watchdog restart'ını davet
                # eder, ki bu 2026-08-14 felaket yoludur (ban ortasında restart
                # → toplu UNKNOWN kapanış). D17'nin bilinçli kararı.
                self._scan_last_success_at = succeeded_at
                self._scan_last_success_monotonic = time.monotonic()
                if getattr(self, "_scan_degraded_reason", None):
                    # Tur YARIDA kesildi (piyasa verisi host geneli kesinti):
                    # başarı sayacı ARTMAZ, `last_scan_at` tazelenmez ve önceki
                    # hata serisi silinmez — "tamamlanmış tur" ile "kesilmiş
                    # tur" sayaç düzeyinde ayrışmalı (düşmanca inceleme).
                    pass
                else:
                    self._scan_consecutive_errors = 0
                    self._scan_success_count += 1
                    self._last_scan_at = succeeded_at
            finally:
                self._scan_last_duration_seconds = time.monotonic() - started

            await asyncio.sleep(self.cfg.scalper_scan_interval_seconds)

    async def _safety_loop(self) -> None:
        self.logger.info("🛡️ Scalper safety döngüsü başladı")
        while self.running:
            started = time.monotonic()
            self._safety_last_started_at = _utcnow_iso()
            try:
                await self._safety_tick()
            except asyncio.CancelledError:
                self.logger.info("🛡️ Scalper safety döngüsü durduruldu")
                raise
            except UnprotectedPositionError as e:
                await self._latch_entry_halt(e, source="safety")
                self._safety_consecutive_errors += 1
                self._safety_last_error = f"{type(e).__name__}: {e}"
                self._safety_last_error_at = _utcnow_iso()
            except Exception as e:
                self._safety_consecutive_errors += 1
                self._safety_last_error = f"{type(e).__name__}: {e}"
                self._safety_last_error_at = _utcnow_iso()
                self.logger.error(f"❌ Scalper safety döngü hatası: {e}", exc_info=True)
            else:
                self._safety_last_success_at = _utcnow_iso()
                self._safety_last_success_monotonic = time.monotonic()
                self._safety_consecutive_errors = 0
                self._safety_success_count += 1
            finally:
                self._safety_last_duration_seconds = time.monotonic() - started

            await asyncio.sleep(self._safety_interval_seconds())

    async def _safety_tick(self) -> None:
        # Pending giriş, gerçekleştiği anda henüz SL'sizdir; bu yüzden REST
        # reconciliation yedeğinde bile exits/PNL gibi daha yavaş işlerden
        # ÖNCE ele alınır. User-data stream ayrıca aynı olayı anlık işleyecek;
        # executor kilidi iki yolun double-finalize etmesini engeller.
        if self._kill_switch or self._entry_halted:
            # Entry lock, tarama döngüsünün try_open() kritik kesitiyle
            # serileştirir: tetik sonrası yeni pending eklenip iptal turunu
            # kaçıramaz. Executor iptal-dolum yarışını borsa durumuyla
            # uzlaştırmakla sorumludur.
            async with self._entry_lock:
                if self.executor.pending_symbols():
                    opened_during_cancel = await self.executor.cancel_all_pending()
                    self._track_opened_positions(
                        opened_during_cancel, source="pending iptal yarışı"
                    )
        else:
            # Maker modunda bekleyen LIMIT girişlerini hızlı ilerlet; yeni
            # dolanlar hemen exits.track() ile izlemeye alınır.
            self._track_opened_positions(
                await self.executor.check_pending(), source="maker dolumu"
            )

        # Artık tüm yeni dolumlar korumalı ve izleniyor: açık pozisyon çıkış
        # yönetimi ile gerçek günlük risk kapısı bundan sonra çalışabilir.
        await self.exits.step()
        await self._apply_structure_exits()
        await self._reap_aged_positions()
        self._sync_scalper_reservations()
        was_blocked = self._kill_switch or self._entry_halted
        await self._update_kill_switch()

        # Kill switch bu turda yeni tetiklendiyse hâlâ NEW olan maker
        # emirlerini aynı turda iptal et. Dolu/partial yarışları executor
        # tarafından korunup izlemeye alınır.
        if not was_blocked and (self._kill_switch or self._entry_halted):
            async with self._entry_lock:
                if self.executor.pending_symbols():
                    opened_during_cancel = await self.executor.cancel_all_pending()
                    self._track_opened_positions(
                        opened_during_cancel, source="risk kapısı iptal yarışı"
                    )

    async def _apply_structure_exits(self) -> None:
        """Açık pozisyonun TERSİNE CHoCH gelince stopu BE'ye çek ya da kapat.

        `SCALPER_STRUCTURE_EXIT=off` (varsayılan) iken TEK BİR satır bile
        çalışmaz — mum da çekilmez, davranış bugünküyle birebir aynıdır.

        Karar saf fonksiyondan gelir (`structure.structure_exit_action`);
        backtest harness'ı (`backtest.manage_position`) AYNI fonksiyonu AYNI
        girdiyle çağırır — parite (DECISIONS P1).

        Mum çekimi yeni bir REST maliyeti getirmez: (sembol, aralık, limit)
        üçlüsü tarama döngüsünün istediğiyle BİREBİR aynıdır, yani
        KlineFetcher'ın TTL önbelleğine düşer (bkz. data.KlineFetcher._cache).

        Tur başına EN FAZLA BİR aksiyon: 2026-08-14'te 5 eşzamanlı kapanış
        safety turunu şişirip watchdog restart'ı tetiklemişti (bkz.
        `_reap_aged_positions`). Kalan pozisyonlar bir sonraki turda (≈8 sn)
        değerlendirilir.
        """
        mode = structure_exit_mode(self.cfg)
        if mode == "off":
            return
        try:
            tf = structure_timeframe(self.cfg)
            limit = structure_window_bars(self.cfg)
            left, right = structure_pivot(self.cfg)
            use_close = structure_use_close(self.cfg)
        except Exception as e:
            self.logger.warning(f"⚠️ Yapı çıkışı ayarı çözülemedi ({e}); tur atlandı")
            return

        for symbol in list(self.exits.tracked_symbols()):
            sp = self.exits._positions.get(symbol)
            if sp is None:
                continue
            try:
                candles = await self.fetcher.get_klines(symbol, tf, limit)
            except Exception as e:
                self.logger.warning(
                    f"⚠️ {symbol}: yapı çıkışı için mum alınamadı ({e}); tur atlandı"
                )
                continue
            if not candles:
                continue
            try:
                state = detect_structure(
                    candles, pivot_left=left, pivot_right=right, use_close=use_close
                )
                plan = getattr(sp, "plan", None)
                action = structure_exit_action(
                    state,
                    StructureExitInput(
                        direction=sp.signal.direction,
                        entry_close_time=int(getattr(sp, "entry_candle_time", 0) or 0),
                        current_price=float(
                            sp.position.current_price or sp.position.entry_price or 0.0
                        ),
                        current_stop=float(sp.position.current_stoploss or 0.0),
                        breakeven_price=float(getattr(plan, "breakeven_price", 0.0) or 0.0),
                    ),
                    self.cfg,
                )
            except Exception as e:
                self.logger.warning(f"⚠️ {symbol}: yapı çıkışı hesaplanamadı ({e})")
                continue

            if action == "none":
                continue

            yon = sp.signal.direction.value
            if action == "be":
                ok = await self.exits.force_stop_to(
                    symbol, sp, float(getattr(plan, "breakeven_price", 0.0) or 0.0),
                    reason="yapı CHoCH",
                )
                if ok:
                    self.logger.info(
                        f"🔄 {symbol}: {yon} pozisyonun TERSİNE CHoCH — stop BE'ye "
                        f"çekildi (SCALPER_STRUCTURE_EXIT=be)",
                        extra={"trade": True},
                    )
                return  # tur başına en fazla bir aksiyon

            try:
                closed = await self._close_position_market(
                    symbol, sp, forced_exit_reason="STRUCT_CHOCH"
                )
            except Exception as e:
                self.logger.warning(
                    f"⚠️ {symbol}: yapı CHoCH kapanışı gönderilemedi ({e}); "
                    f"sonraki turda denenecek"
                )
                return
            if closed:
                self.logger.info(
                    f"🔄 {symbol}: {yon} pozisyonun TERSİNE CHoCH — reduce-only "
                    f"MARKET ile kapatıldı (SCALPER_STRUCTURE_EXIT=close)",
                    extra={"trade": True},
                )
            else:
                self.logger.warning(
                    f"⚠️ {symbol}: yapı CHoCH kapanışı borsada DOĞRULANAMADI — "
                    f"pozisyon izlemede kalıyor (fail-closed)"
                )
            return  # tur başına en fazla bir aksiyon

    async def _reap_aged_positions(self) -> None:
        """Yaş limitini aşan KORUMASIZ pozisyonları kapat (ölü-sermaye reaper'ı).

        2026-08-15: 8 slot 10-15 saatlik pozisyonlarla kilitlenince tüm yeni
        confluence sinyalleri reddedildi. Kapanış reduce-only MARKET'tir ve
        exits._handle_closed'un normal dış-kapanış yolundan (ledger + emir
        temizliği) doğrulanır. Tur başına EN FAZLA BİR kapanış: 2026-08-14'te
        5 eşzamanlı kapanış safety turunu şişirip watchdog restart'ı
        tetiklemişti.

        2026-08-21 (kullanıcı kararı): trailing_active pozisyonlar MUAF —
        TP1 dolmuş, SL girişte/üstünde, chandelier iz sürüyor. Bugün kesilen
        trend yarın devam edebilir; koşucuyu yalnız stop/trailing durdurur,
        saat DEĞİL. Beklemenin en kötü sonucu ~breakeven. Reaper yalnız TP1'i
        hiç görememiş (BE koruması olmayan) yaşlı pozisyonları keser.
        """
        limit_h = float(getattr(self.cfg, "scalper_max_hold_hours", 0.0) or 0.0)
        if limit_h <= 0:
            return
        now = datetime.now(timezone.utc)
        for symbol in list(self.exits.tracked_symbols()):
            sp = self.exits._positions.get(symbol)
            if sp is None:
                continue
            if getattr(sp, "trailing_active", False):
                continue  # BE korumalı koşucu: tek çıkış stop/trailing
            opened = getattr(sp.position, "opened_at", None)
            if opened is None:
                continue
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age_h = (now - opened).total_seconds() / 3600.0
            if age_h < limit_h:
                continue
            side_val = getattr(sp.position.side, "value", str(sp.position.side))
            close_side = "SELL" if str(side_val).endswith("LONG") else "BUY"
            qty = abs(float(sp.position.quantity))
            try:
                qty = await self.client.quantize_quantity(symbol, qty)
                await self._submit_reduce_only_market_close(symbol, close_side, qty)
                self.logger.info(
                    f"⏳ Reaper: {symbol} {age_h:.1f}sa yaşında (limit {limit_h:.0f}sa) — "
                    f"reduce-only kapanış gönderildi; ledger doğrulaması safety turunda",
                    extra={"trade": True},
                )
            except Exception as e:
                self.logger.warning(
                    f"⏳ Reaper: {symbol} kapanışı gönderilemedi ({e}); sonraki turda denenecek"
                )
            return  # tur başına en fazla bir kapanış

    async def _submit_reduce_only_market_close(
        self, symbol: str, close_side: str, qty: float
    ) -> None:
        """Reduce-only MARKET kapanış emri — TEK emir yolu.

        Reaper (`_reap_aged_positions`) VE risk-olayı `flatten` aksiyonu
        (`risk_event_flatten`/`_close_position_market`) AYNI çağrıyı kullanır;
        risk-olayı kanalı için YENİ bir emir yolu YAZILMADI (bkz. görev
        sözleşmesi).
        """
        await self.client._request_with_retry(
            "POST", "/fapi/v1/order",
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

    async def _close_position_market(
        self, symbol: str, sp: Any, forced_exit_reason: str = "RISK_EVENT"
    ) -> bool:
        """Bir pozisyonu reduce-only MARKET ile kapat ve borsada DOĞRULA.

        Reaper'dan farkı: reaper "gönder ve bir sonraki safety turunda
        exits.step() algılasın" der (tur başına tek kapanış disiplini).
        Risk-olayı flatten'ı SENKRON, tüm pozisyonlar için ve HTTP yanıtında
        `flattened=[...]` listesi gerektirir — o yüzden burada kapanışı
        birkaç kısa deneme ile borsa üzerinde (positionAmt==0) doğrulayıp
        exits._handle_closed'u DOĞRUDAN çağırıyoruz. Doğrulanamazsa
        _handle_closed ASLA çağrılmaz — aksi halde SL/TP emirleri iptal
        edilip pozisyon KORUMASIZ kalabilirdi (fail-closed).

        Boyut ve doğrulama borsadan CANLI okunur (force_fresh=True):
        `sp.position.quantity` GİRİŞ dolumudur ve kısmi TP'lerden sonra ASLA
        güncellenmez (exits.py onu `filled` referansı olarak kullanır) —
        trailing_active bir koşucuda bunu göndermek canlının 1.6-3.3 katı
        reduce-only MARKET demektir (-2022 NON_RETRYABLE riski, pozisyon
        korumasız kalır). position_manager._emergency_close ile aynı yol.
        force_fresh=True her iki okuma için de zorunludur: geri alınamaz
        kararlar (kapanış boyutu, kapanış doğrulaması) önbellek değil taze
        veri ister — force_fresh olmadan doğrulama denemeleri 5 sn'lik
        pozisyon snapshot önbelleğinden aynı bayat kaydı döndürebilir ve
        retry döngüsü fiilen ölü kalır.
        """
        pos_info = await self.client.get_position_risk(symbol, force_fresh=True)
        amt = float(pos_info.get("positionAmt", 0) or 0) if pos_info else 0.0
        if amt == 0:
            # Zaten flat: emir gönderme, yalnız SL/TP temizliği + ledger.
            await self.exits._handle_closed(
                symbol, sp, forced_exit_reason=forced_exit_reason
            )
            return True
        close_side = "SELL" if amt > 0 else "BUY"
        qty = await self.client.quantize_quantity(symbol, abs(amt))
        await self._submit_reduce_only_market_close(symbol, close_side, qty)

        for delay in (0.0, 0.3, 0.6, 1.0, 2.0):
            if delay:
                await asyncio.sleep(delay)
            pos_info = await self.client.get_position_risk(symbol, force_fresh=True)
            amt2 = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0
            if amt2 == 0:
                await self.exits._handle_closed(
                    symbol, sp, forced_exit_reason=forced_exit_reason
                )
                return True
        return False

    def _track_opened_positions(self, positions: list, *, source: str) -> None:
        """Pending uzlaştırmasında gerçekleşmiş dolumları izlemeye al."""
        for sp in positions:
            symbol = sp.position.symbol
            if not symbol_reservations.reserve(symbol, self._RESERVATION_OWNER):
                reason = (
                    f"{symbol}: korunmuş fill başka bir motorun sembol sahipliğiyle çakıştı"
                )
                # _load_entry_halt ve _latch_entry_halt ile aynı kapı: flag
                # kapalıyken latch'lemek hem sözleşmeyi bozar hem de load
                # tarafının yok sayacağı bir halt dosyası persist ederdi.
                if not getattr(self.cfg, "scalper_entry_halt_enabled", True):
                    self.logger.critical(
                        f"🚨 {reason}. Entry halt DEVRE DIŞI "
                        "(scalper_entry_halt_enabled=false) — yeni girişler "
                        "durdurulmadı, yalnız loglandı.",
                        extra={"trade": True},
                    )
                else:
                    self._entry_halted = True
                    self._entry_halt_reason = reason
                    self._entry_halted_at = _utcnow_iso()
                    self._persist_entry_halt()
                    self.logger.critical(
                        f"🚨 {reason}; tüm yeni scalper girişleri durduruldu",
                        extra={"trade": True},
                    )
            self.exits.track(sp)
            self._signals_today += 1
            self.logger.info(
                f"🎯 {sp.position.symbol}: {source} -> pozisyon korundu ve izlemeye alındı "
                f"({sp.signal.direction.value} @ {sp.position.entry_price})",
                extra={"trade": True},
            )

    async def _scan_tick(self) -> None:
        # Evren taraması ve sinyal üretimi safety döngüsünden tamamen
        # ayrıdır; yavaş bir sembol koruma izlemesini geciktiremez.
        allowlist_csv = str(
            getattr(self.cfg, "scalper_symbol_allowlist", "") or ""
        ).strip()
        if allowlist_csv:
            # Kanıt disiplini: canlı evren, backtest'in kapsadığı sembollere
            # sabitlenebilir — scanner'ın top_n listesi hiç sorgulanmaz.
            self._universe = [
                s.strip().upper() for s in allowlist_csv.split(",") if s.strip()
            ]
        else:
            self._universe = await self.scanner.get_universe()

        if not self._entries_ready():
            return

        # Lider piyasa kapısı (D15): anlık görüntü TUR BAŞINDA bir kez
        # tazelenir; turdaki tüm semboller AYNI görüntüyü kullanır (harness
        # paritesi — bkz. _refresh_leader_snapshot). REST maliyetinin ÜST
        # sınırı değişmez (tur başına yine en çok 3 istek) ama ALT sınırı
        # 0'dan 3 ağırlık/dakikaya çıkar: eskiden sinyal gelmeyen turda hiç
        # istek gitmiyordu, artık her tur tazeleniyor.
        await self._refresh_leader_snapshot()

        # Sembol başına tek strateji denemesi.
        enabled_strategies = get_enabled(self.cfg.scalper_strategies)
        if not enabled_strategies:
            return

        # Pozisyon durumu TEK toplu çağrıyla (20× /positionRisk yerine 1×
        # /fapi/v2/account) — 2026-08-12 IP ban kök nedeni istek ağırlığıydı.
        # Tick içi bayatlama kabul edilir: entry_lock altındaki son kapı ve
        # executor zaten yarışları yakalar.
        try:
            open_positions = await self.client.get_all_positions()
        except Exception as e:
            self.logger.warning(f"⛔ Tarama pozisyon özeti alınamadı, tur atlandı ({e})")
            return
        self._scan_open_symbols = {
            str(p.get("symbol", "")).upper() for p in open_positions
        }

        for symbol in self._universe:
            if not self._entries_ready():
                break

            # Koruma emri/çıkış kurulumu başarısız olmuş bir sembole,
            # executor'ın kalıcı cooldown süresi dolmadan yeniden girme.
            # Eski executor sürümleri için public API yoksa davranış
            # geriye uyumlu biçimde değişmez.
            if self._executor_entry_blocked(symbol):
                continue

            tracked = self.exits.tracked_symbols()
            pending = self.executor.pending_symbols()
            if symbol in tracked or symbol in pending:
                continue
            owner = symbol_reservations.owner(symbol)
            if owner is not None and owner != self._RESERVATION_OWNER:
                continue
            if len(tracked | pending) >= self.cfg.scalper_max_positions:
                # Sembol başına değil, TUR başına kesici: kapasite dolduysa bu
                # turda başka hiçbir yeni sembol denenmez.
                break

            try:
                await self._evaluate_symbol(symbol, enabled_strategies)
            except asyncio.CancelledError:
                raise
            except UnprotectedPositionError:
                # Bu, sembol bazında atlanabilecek normal bir API hatası
                # değildir; üst döngü global entry latch'i kapatmalıdır.
                raise
            except MarketDataUnavailable as e:
                # D17: ban/ağırlık koruması HOST geneliyse kalan sembolleri
                # denemek anlamsızdır (her biri ayrı bir traceback üretirdi:
                # 12 sembol × tur). Tur burada kesilir, 30 sn sonra yeniden
                # denenir. Sinyal üretilmemesi fail-closed'dır — açık
                # pozisyonların SL/TP'si borsada yerinde durur.
                # Tur "başarılı" SAYILMAZ: `_mark_scan_degraded` ayrı sayacı
                # artırır ve `/scalper/status.scan_status` alanını
                # "degraded:market_data" yapar (aksi halde sağlık YEŞİL kalır
                # ve operatörün tek izi bu log satırı olurdu).
                self._mark_scan_degraded(f"market_data: {e}")
                break
            except Exception as e:
                self.logger.error(f"❌ {symbol}: tur değerlendirmesi hata verdi ({e})", exc_info=True)

    def _mark_scan_degraded(self, reason: str, kind: str = "market_data") -> None:
        """Tarama turunu "yarıda kesildi" olarak işaretle (hata DEĞİL).

        D17'de kesilen tur `_loop`'un `else` dalına düşüyordu: `success_count`
        artıyor, `consecutive_errors` sıfırlanıyor, `last_scan_at` tazeleniyor
        ve sağlık YEŞİL kalıyordu — operatörün tek izi tek bir log satırıydı
        (düşmanca inceleme bulgusu). Artık:
          * ayrı sayaç (`_scan_degraded_count`) ve neden/zaman alanları,
          * `/scalper/status.scan_status` = "degraded:<kind>",
          * ORAN-SINIRLI (60 sn) tek WARNING — safety/scan turları saniyeler
            içinde döndüğü için uzun bir banda log seli oluşurdu.
        Freshness alanları BİLİNÇLİ olarak bozulmaz (watchdog restart'ı ban
        ortasında felaket yoludur).
        """
        self._scan_degraded_reason = reason
        self._scan_degraded_kind = kind
        self._scan_degraded_at = _utcnow_iso()
        self._scan_degraded_count = (
            int(getattr(self, "_scan_degraded_count", 0) or 0) + 1
        )
        now = time.monotonic()
        last = float(getattr(self, "_scan_degraded_log_at", 0.0) or 0.0)
        if last <= 0.0 or (now - last) >= self._SCAN_DEGRADED_LOG_INTERVAL:
            self._scan_degraded_log_at = now
            self.logger.warning(
                f"⛔ Piyasa verisi kullanılamıyor ({reason}); tarama turu "
                f"kesildi — tur DEGRADE sayıldı "
                f"(scan_status=degraded:{kind}, toplam "
                f"{self._scan_degraded_count})"
            )

    def _exits_trailing_skip_snapshot(self) -> Dict[str, int]:
        """ExitManager'ın atlanan trailing sayaçlarını geriye uyumlu oku."""
        snapshotter = getattr(self.exits, "trailing_skip_snapshot", None)
        if not callable(snapshotter):
            return {}
        try:
            return dict(snapshotter())
        except Exception as e:  # teşhis alanı asla status'u düşürmemeli
            return {"error": f"{type(e).__name__}: {e}"}

    def _scan_status(self) -> str:
        """"ok" | "degraded:<kind>" — son turun sonucu (teşhis alanı)."""
        if getattr(self, "_scan_degraded_reason", None):
            return f"degraded:{getattr(self, '_scan_degraded_kind', 'market_data')}"
        return "ok"

    def _scan_degraded_snapshot(self) -> Dict[str, Any]:
        """Kesinti telemetrisi (secret içermez; eski test çiftlerinde boş)."""
        return {
            "scan_status": self._scan_status(),
            "scan_degraded_reason": getattr(self, "_scan_degraded_reason", None),
            "scan_degraded_at": getattr(self, "_scan_degraded_at", None),
            "scan_degraded_count": int(
                getattr(self, "_scan_degraded_count", 0) or 0
            ),
        }

    async def _evaluate_symbol(self, symbol: str, enabled_strategies: list) -> None:
        owner = symbol_reservations.owner(symbol)
        if owner is not None and owner != self._RESERVATION_OWNER:
            return
        if self._executor_entry_blocked(symbol):
            return
        # Tarama turunun başında toplu çekilen açık pozisyon kümesi (bkz.
        # _scan_tick). getattr: eski test çiftleri bu alanı kurmayabilir.
        if symbol.upper() in getattr(self, "_scan_open_symbols", set()):
            # Telegram botu veya elle açılmış pozisyon — dokunma.
            return

        # Zaman dilimi profili konfigüre edilebilir (varsayılan 5m/15m/4h;
        # hızlı profil 1m/5m/15m). Alan adları rol belirtir: candles_5m =
        # giriş dilimi, candles_15m = bağlam, candles_4h = rejim.
        tf_regime = str(getattr(self.cfg, "scalper_tf_regime", "4h") or "4h")
        tf_context = str(getattr(self.cfg, "scalper_tf_context", "15m") or "15m")
        tf_entry = str(getattr(self.cfg, "scalper_tf_entry", "5m") or "5m")
        candles_4h = await self.fetcher.get_klines(symbol, tf_regime, 250)
        candles_15m = await self.fetcher.get_klines(symbol, tf_context, 100)
        candles_5m = await self.fetcher.get_klines(symbol, tf_entry, 150)

        if not candles_5m:
            return

        regime = self._get_cached_regime(symbol, candles_4h)
        self._regimes[symbol] = regime.value

        current_price = candles_5m[-1].close
        atr_5m = compute_atr(candles_5m, 14)

        ctx = StrategyContext(
            symbol=symbol,
            regime=regime,
            candles_4h=candles_4h,
            candles_15m=candles_15m,
            candles_5m=candles_5m,
            current_price=current_price,
            atr_5m=atr_5m,
            leverage=self.cfg.scalper_leverage,
        )

        # Piyasa yapısı (BOS/CHoCH) — ctx'te ZATEN çekilmiş serilerden türetilir,
        # yeni REST çağrısı YOKTUR (bkz. structure.structure_series). Hesap saf ve
        # ucuzdur (~100 mum); yine de bir hata tüm taramayı düşürmemeli.
        structure_state = None
        # getattr: object.__new__ ile kurulan test çiftleri bu alanı kurmayabilir
        # (repo konvansiyonu — bkz. _scan_open_symbols).
        structure_store = getattr(self, "_structure", None)
        try:
            structure_state = structure_state_for(ctx, self.cfg)
            if structure_store is not None:
                structure_store[symbol] = structure_snapshot(structure_state)
        except Exception as e:
            if structure_store is not None:
                structure_store[symbol] = {"error": f"{type(e).__name__}: {e}"}
            # Hata kalıcıdır (ör. SCALPER_STRUCTURE_TF çözülemiyor): her sembol
            # × her tarama turu loglamak bot.log'u boğar. Bir kez yüksek sesle
            # söyle, gerisi /scalper/status'taki "error" alanından okunur.
            if not getattr(self, "_structure_error_logged", False):
                self._structure_error_logged = True
                self.logger.warning(
                    f"⚠️ {symbol}: piyasa yapısı hesaplanamadı ({e}) — bu uyarı "
                    f"bir kez loglanır, durum /scalper/status'ta"
                )

        for strat in enabled_strategies:
            sig = strat.evaluate(ctx)
            if sig is None:
                continue
            # 2026-08-16 rejim kapisi: C, DOWN rejimde LONG / UP rejimde SHORT
            # acamaz (30 saatlik RANGE/DOWN penceresinde rejime ters girisler
            # -35 USDT kanatti). 2026-08-18: TV muafiyeti KALDIRILDI (ayrı
            # bayrakla): 2 kaynaklı sağlamaya rağmen TV 2 günde −41 USDT
            # kaybetti; en kötüsü 8 saat yükselen piyasada taşınan SHORT
            # (SOL #92, −30.65). Rejime ters dış sinyal de artık bloklanır.
            is_external = strat.__class__.__name__ == "_ExternalSignalStrategy"
            gate_on = bool(getattr(self.cfg, "scalper_regime_filter", True)) and (
                not is_external
                or bool(getattr(self.cfg, "scalper_tv_regime_filter", True))
            )
            if gate_on:
                rejim = getattr(regime, "value", str(regime))
                yon = getattr(sig.direction, "value", str(sig.direction))
                if (rejim == "DOWN" and yon == "LONG") or (
                        rejim == "UP" and yon == "SHORT"):
                    kaynak = "TV sinyali" if is_external else "girişi"
                    self.logger.info(
                        f"⛔ {symbol}: rejim kapısı — {rejim} rejiminde {yon} "
                        f"{kaynak} engellendi (SCALPER_REGIME_FILTER)"
                    )
                    continue
            # 2026-08-23 (D15) lider piyasa kapısı: rejim kapısının HEMEN
            # yanında, AYNI tek giriş noktasında — bu döngüden hem C taraması
            # hem TV dış sinyali (external_signal → _evaluate_symbol) geçer,
            # bu yüzden ayrı bir TV muafiyeti YOKTUR. Varsayılan kapalı.
            market_reason = await self._market_gate_reason(sig.direction)
            if market_reason is not None:
                kaynak = "TV sinyali" if is_external else "girişi"
                gate_status = self._market_gate_status()
                yon = getattr(sig.direction, "value", str(sig.direction))
                alt_kapi = (
                    "gün-içi sapma" if market_reason == "market_gate_day" else "çok-günlük uzama"
                )
                self.logger.info(
                    f"⛔ {symbol}: piyasa kapısı — {alt_kapi} ({gate_status.get('leader')} "
                    f"gün {_fmt_pct(gate_status.get('day_drift_pct'))}, "
                    f"koşu {_fmt_pct(gate_status.get('run_pct'))}) nedeniyle {yon} "
                    f"{kaynak} engellendi (SCALPER_MARKET_GATE)"
                )
                continue
            # 2026-08-23 yapı kapısı (E9/D18 adayı, varsayılan KAPALI): rejim
            # kapısı 15m EMA50/200 ile dönüşleri saatler geç görüyor; yapı
            # (son swing kırılımı) aynı soruyu daha erken yanıtlar. TEK kapı:
            # C ve TV sinyalleri aynı yerden geçer (rejim kapısının yanı).
            # Harness'ta simulate_symbol AYNI saf fonksiyonu AYNI girdiyle
            # çağırır (DECISIONS P1).
            if structure_gate_blocks(structure_state, sig.direction, self.cfg):
                yon = getattr(sig.direction, "value", str(sig.direction))
                kaynak = "TV sinyali" if is_external else "girişi"
                self.logger.info(
                    f"⛔ {symbol}: yapı kapısı — "
                    f"{structure_state.direction.value} yapıda {yon} {kaynak} "
                    f"engellendi (son olay {structure_state.last_event}, "
                    f"{structure_state.age_bars} mum önce; SCALPER_STRUCTURE_GATE)"
                )
                continue
            # Ortak stop politikası: structural modda ATR tabanı, fixed_roi
            # modda marj-yüzdesi stopu. Backtest'te simulate_symbol aynı
            # dönüşümü uygular — canlı/backtest paritesi bozulmamalı.
            sig = apply_stop_policy(sig, self.cfg)

            # Veri toplama sırasında kill switch veya kapasite değişmiş
            # olabilir. Emir açma kararını, safety kill-switch iptaliyle aynı
            # kilit altında son kez doğrula.
            async with self._entry_lock:
                tracked = self.exits.tracked_symbols()
                pending = self.executor.pending_symbols()
                if not self._entries_ready():
                    risk_event_snap = self._risk_event_halt_snapshot()
                    if self._entry_halted:
                        reason = "entry safety latch"
                    elif self._kill_switch:
                        reason = "kill switch"
                    elif risk_event_snap.get("active"):
                        reason = f"risk-event halt ({risk_event_snap.get('reason')})"
                    else:
                        reason = "exchange/recovery readiness"
                    self.logger.info(f"⏭️ {symbol}: {reason} aktif, hazır sinyal açılmadı")
                    return
                # Mumlar indirilirken veya strateji hesaplanırken bir koruma
                # hatası cooldown başlatmış olabilir. POST'tan hemen önceki
                # bu ikinci kapı yarış penceresini kapatır.
                if self._executor_entry_blocked(symbol):
                    self.logger.info(
                        f"⏭️ {symbol}: giriş cooldown aktif, hazır sinyal açılmadı"
                    )
                    return
                if symbol in tracked or symbol in pending:
                    return
                open_count = len(tracked | pending)
                if bool(getattr(self.cfg, "scalper_shadow_mode", False)):
                    # D14 review (bulgu B): gölge girişler tracked/pending'e
                    # hiç girmediği için bu kapı canlıda hiç devreye girmiyordu
                    # — gölge defteri canlının reddedeceği sinyalleri de
                    # sınırsız biriktiriyordu. Tekilleştirme penceresindeki
                    # sembol sayısı (shadow_active_count) "açık" gibi sayılır
                    # ki gölge satır sayısı canlı kapasiteyle kıyaslanabilsin.
                    shadow_active = self.executor.shadow_active_count()
                    if open_count + shadow_active >= self.cfg.scalper_max_positions:
                        self.logger.info(f"👻 {symbol}: GÖLGE kapasite dolu, sinyal açılmadı")
                        return
                elif open_count >= self.cfg.scalper_max_positions:
                    self.logger.info(f"⏭️ {symbol}: scalper pozisyon kapasitesi dolu, sinyal açılmadı")
                    return
                try:
                    exchange_positions = await self.client.get_all_positions()
                except Exception as e:
                    self._exchange_ready = False
                    self._exchange_last_error = f"{type(e).__name__}: {e}"
                    self._exchange_last_error_at = _utcnow_iso()
                    self.logger.error(
                        f"⛔ {symbol}: hesap pozisyonları doğrulanamadı; giriş fail-closed reddedildi ({e})"
                    )
                    return

                live_symbols = {
                    str(raw.get("symbol", "")).upper()
                    for raw in exchange_positions
                    if float(raw.get("positionAmt", 0) or 0) != 0
                }
                if symbol in live_symbols:
                    return
                if not symbol_reservations.reserve(
                    symbol,
                    self._RESERVATION_OWNER,
                    capacity=getattr(
                        self.cfg,
                        "max_positions",
                        self.cfg.scalper_max_positions,
                    ),
                    exchange_symbols=live_symbols,
                ):
                    self.logger.info(
                        f"⏭️ {symbol}: sembol başka motorun yönetiminde veya hesap kapasitesi dolu"
                    )
                    return

                self._opening_symbols.add(symbol)
                unsafe_failure = False
                sp = None
                try:
                    sp = await self.executor.try_open(sig, ctx)
                except UnprotectedPositionError:
                    # Sembol, outer loop kalıcı entry latch'i etkinleştirene
                    # kadar in-flight kümesinde kalır. Bu kısa aralıkta safety
                    # sync fail-open biçimde ownership'i bırakamaz.
                    unsafe_failure = True
                    raise
                except Exception:
                    # Normal bir emir reddi/istemci hatasında try_open ya
                    # journal+pending durumunu kurmuştur ya da aşağıdaki
                    # finally rezervasyonu bırakacaktır. In-flight işareti
                    # bu sembolü sonsuza dek kapasitede tutmamalı.
                    self._opening_symbols.discard(symbol)
                    raise
                finally:
                    if (
                        not unsafe_failure
                        and sp is None
                        and symbol not in self.exits.tracked_symbols()
                        and symbol not in self.executor.pending_symbols()
                    ):
                        symbol_reservations.release(symbol, self._RESERVATION_OWNER)

            try:
                if sp:
                    self.exits.track(sp)
                    self._signals_today += 1
                    self.logger.info(
                        f"🎯 {symbol}: strateji {sig.strategy} sinyali işlendi -> pozisyon açıldı "
                        f"({sig.direction.value} @ {sp.position.entry_price})",
                        extra={"trade": True},
                    )
            finally:
                if not unsafe_failure:
                    # try_open sonucunda ya pending journal ya da tracked
                    # pozisyon artık ownership'i taşıyor; başarısız normal
                    # denemede rezervasyon yukarıda zaten bırakıldı.
                    self._opening_symbols.discard(symbol)
            # Sembol başına tek deneme: sinyal bulunduğu an (başarılı ya da
            # başarısız) bu sembol için tur biter.
            break

    def _safety_interval_seconds(self) -> float:
        """Hatalı/negatif ayarı yoğun bir busy-loop'a çevirmeden sınırla."""
        return max(0.5, float(getattr(self.cfg, "scalper_safety_interval_seconds", 2.0)))

    def _maybe_log_shadow_mode_banner(self) -> None:
        """Gölge modu (D14) açıksa başlangıçta YÜKSEK SESLE uyar.

        Operatör bir supervisorctl restart sonrası bot.log'a bakınca kaçırmasın
        diye WARNING seviyesinde ve ayrı satırda — kapalıyken hiçbir şey basmaz.
        """
        if bool(getattr(self.cfg, "scalper_shadow_mode", False)):
            self.logger.warning("⚠️ GÖLGE MODU AÇIK — emir gönderilmez")

    def _maybe_log_market_gate_banner(self) -> None:
        """Lider piyasa kapısı (D15) açıksa başlangıçta durumunu bildir.

        Uzama alt-kapısı için AYRI ve daha sert bir uyarı var: iki BAĞIMSIZ
        ölçüm onu desteklemiyor — (1) harness'ta yalnız AYI penceresinde ve
        TEK bir lider olayında tetikleniyor, gün-içi alt-kapısının üstüne
        hiçbir şey eklemiyor (E7: V3 ≡ V1); (2) canlı defterde net NEGATİF
        (E8: −152.7). Varsayılan `SCALPER_MARKET_GATE_RUN_PCT=15` bu yüzden
        bir tuzak: kapıyı açan operatör istemeden uzama alt-kapısını da
        açar. Sessizce değiştirmek yerine (spec'te 15 onaylanmıştı) açıkça
        uyarıyoruz — bkz. docs/DECISIONS.md D15.
        """
        if not bool(getattr(self.cfg, "scalper_market_gate", False)):
            return
        leader = self._market_gate_leader()
        day_pct = getattr(self.cfg, "scalper_market_gate_day_pct", 0.0)
        run_pct = getattr(self.cfg, "scalper_market_gate_run_pct", 0.0)
        run_days = getattr(self.cfg, "scalper_market_gate_run_days", 0)
        self.logger.warning(
            f"🧭 PİYASA KAPISI AÇIK — lider {leader}, gün-içi %{day_pct}, "
            f"uzama %{run_pct}/{run_days}g"
        )
        try:
            run_active = float(run_pct or 0.0) > 0.0
        except (TypeError, ValueError):
            run_active = False
        if run_active:
            self.logger.warning(
                "⚠️ Piyasa kapısının UZAMA alt-kapısı açık "
                "(SCALPER_MARKET_GATE_RUN_PCT>0) — iki bağımsız ölçüm bunu "
                "DESTEKLEMİYOR (E7: gün-içi kapısının üstüne katkısı yok; "
                "E8: canlı defterde net negatif). Kapatmak için "
                "SCALPER_MARKET_GATE_RUN_PCT=0 (bkz. docs/DECISIONS.md D15)"
            )

    def _kline_source_snapshot(self) -> Dict[str, Any]:
        """Kline verisinin GERÇEKTEN geldiği host + kaynak etiketi (D17).

        Ayarı değil, `self.fetcher`ın kullandığı base_url'i raporlar — ayar
        ile fetcher arasındaki olası bir sapma teşhiste görünsün. Secret
        içermez (public host adı).
        """
        market_data_url = str(getattr(self.fetcher, "base_url", "") or "")
        trading_url = str(getattr(self.client, "base_url", "") or "")
        info: Dict[str, Any] = {
            "market_data_base_url": market_data_url,
            "trading_base_url": trading_url,
            "kline_source": (
                "separate"
                if market_data_url and trading_url and market_data_url != trading_url
                else "trading_host"
            ),
        }
        # Ban/ağırlık durumu da görünür olmalı: aksi halde veri host'u banlıyken
        # tarama turu "başarılı" sayıldığı için (`_scan_tick` turu keser ama
        # hata FIRLATMAZ) sağlık YEŞİL kalır ve operatörün tek izi log satırı
        # olurdu (düşmanca inceleme bulgusu). health_snapshot BİLİNÇLİ olarak
        # DEĞİŞTİRİLMEDİ — ban sırasında "unhealthy" göstermek watchdog
        # restart'ını davet eder, ki bu 2026-08-14 felaket yoludur.
        if market_data_url:
            try:
                info["market_data_guard"] = MarketDataGuard.snapshot(market_data_url)
            except Exception as e:  # teşhis alanı asla status'u düşürmemeli
                info["market_data_guard"] = {"error": f"{type(e).__name__}: {e}"}
        return info

    def _log_kline_source(self) -> None:
        """Başlangıçta TEK satır: kline verisi hangi host'tan geliyor.

        Operatör bir restart sonrası bot.log'da "piyasa verisi nereden
        geliyor" sorusuna tek satırda cevap bulmalı (D17 doğrulama adımı,
        docs/RUNBOOK.md "Kline kaynağını mainnet'e alma").
        """
        info = self._kline_source_snapshot()
        host = host_of(info["market_data_base_url"])
        if info["kline_source"] == "separate":
            self.logger.info(
                f"📡 Kline kaynağı: {host} (AYRI — emirler: "
                f"{host_of(info['trading_base_url'])})"
            )
        else:
            self.logger.info(f"📡 Kline kaynağı: {host} (işlem host'u)")

    def _executor_entry_blocked(self, symbol: str) -> bool:
        """Executor'ın sembol cooldown kapısını güvenli/geriye uyumlu oku."""
        checker = getattr(self.executor, "is_entry_blocked", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(symbol))
        except Exception as e:
            # Koruma kapısı mevcutken durumunun okunamaması fail-open
            # olmamalı. Yalnız bu sembol atlanır; safety döngüsü sürer.
            self.logger.error(
                f"⛔ {symbol}: giriş cooldown durumu okunamadı; giriş fail-closed atlandı ({e})"
            )
            return True

    async def external_signal(self, symbol: str, direction: Direction) -> Dict[str, Any]:
        """TradingView webhook köprüsü: dış sinyali normal giriş hattına sok.

        Dönen sözlük teşhis içindir; kabul edilen sinyal bile risk
        kapılarında reddedilebilir — kesin sonuç log + DB'dedir.
        """
        symbol = str(symbol).upper()
        if not self.running:
            return {"accepted": False, "reason": "scalper çalışmıyor"}
        tv_allow = str(
            getattr(self.cfg, "scalper_tv_symbol_allowlist", "") or ""
        ).strip()
        if tv_allow:
            allowed = {s.strip().upper() for s in tv_allow.split(",") if s.strip()}
            if symbol not in allowed:
                self.logger.info(
                    f"🚫 TV sinyali reddedildi: {symbol} — TV sembol allowlist'i "
                    f"dışında (OSC kanıtı yok; bkz. scalper_tv_symbol_allowlist)"
                )
                return {"accepted": False, "reason": "TV sembol allowlist'i dışında"}
        if not self._entries_ready():
            risk_event_snap = self._risk_event_halt_snapshot()
            if self._entry_halted:
                reason = self._entry_halt_reason
            elif self._kill_switch:
                reason = "kill switch"
            elif risk_event_snap.get("active"):
                reason = f"risk-event halt ({risk_event_snap.get('reason')})"
            else:
                reason = "girişler hazır değil"
            # 2026-08-14: Sessiz retler teşhisi köreltiyordu (sağlama tamam →
            # iz yok). Ret nedeni HTTP yanıtına ek olarak log'a da yazılır.
            self.logger.info(f"🚫 TV sinyali reddedildi: {symbol} — {reason}")
            return {"accepted": False, "reason": reason}

        before = self.exits.tracked_symbols() | self.executor.pending_symbols()
        if symbol in before:
            self.logger.info(
                f"🚫 TV sinyali reddedildi: {symbol} — sembolde zaten pozisyon/pending var"
            )
            return {"accepted": False, "reason": "sembolde zaten pozisyon/pending var"}

        self.logger.info(
            f"📡 TradingView sinyali alındı: {symbol} {direction.value}",
            extra={"trade": True},
        )
        try:
            await self._evaluate_symbol(symbol, [_ExternalSignalStrategy(direction)])
        except MarketDataUnavailable as e:
            # D17: piyasa verisi host geneli kesikken (ban/ağırlık bütçesi) bu
            # istisna FastAPI'ye sızıp /tv-signal'i HTTP 500'e düşürürdü;
            # TradingView 2xx olmayan yanıtta alarmı TEKRAR gönderir ve her
            # tekrar yine 500 üretir (sağlama oyu da boşa gider). Yapısal ret
            # olarak dön: kaynak bunu normal bir "kabul edilmedi" gibi işler.
            self.logger.warning(
                f"🚫 TV sinyali işlenemedi: {symbol} — piyasa verisi yok ({e})"
            )
            return {
                "accepted": False,
                "reason": "piyasa verisi kullanılamıyor (ban/ağırlık bütçesi)",
            }
        except MarketDataRequestError as e:
            # İkinci tur düşmanca inceleme: SEMBOL kapsamlı kalıcı hata
            # (`-1121 Invalid symbol`) `MarketDataUnavailable` DEĞİLDİR, yani
            # yukarıdaki dal onu yakalamıyordu → /tv-signal HTTP 500. Ayrı
            # market-data host'unda bu senaryo GERÇEKÇİDİR: işlem host'unda
            # olup veri host'unda olmayan bir sembol için TradingView alarmı
            # gelir ve her tekrar yine 500 üretirdi.
            self.logger.warning(
                f"🚫 TV sinyali işlenemedi: {symbol} — piyasa verisi kaynağı "
                f"bu sembolü tanımıyor ({e})"
            )
            return {
                "accepted": False,
                "reason": "sembol piyasa verisi kaynağında bulunamadı",
            }

        after = self.exits.tracked_symbols() | self.executor.pending_symbols()
        opened = symbol in after
        return {
            "accepted": opened,
            "reason": "giriş hattına alındı" if opened else
            "risk/kapasite/cooldown kapısında reddedildi (log'a bakın)",
        }

    def _executor_reject_snapshot(self) -> Dict[str, int]:
        """Kapı ret sayaçlarını güvenli/geriye uyumlu oku (eski executor'da yok)."""
        snapshotter = getattr(self.executor, "reject_snapshot", None)
        if snapshotter is None:
            return {}
        try:
            return snapshotter()
        except Exception as e:
            self.logger.error(f"Scalper reject snapshot okunamadı: {e}")
            return {}

    def _executor_cooldown_snapshot(self) -> List[Dict[str, Any]]:
        """Dashboard için secret içermeyen cooldown telemetrisi."""
        snapshotter = getattr(self.executor, "cooldown_snapshot", None)
        if not callable(snapshotter):
            return []
        try:
            raw = snapshotter()
        except Exception as e:
            self.logger.error(f"Scalper cooldown snapshot okunamadı: {e}")
            return []
        if not isinstance(raw, list):
            return []

        safe: List[Dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            safe.append({
                "symbol": str(row.get("symbol") or ""),
                "reason": str(row.get("reason") or "protection_failure"),
                "remaining_seconds": row.get("remaining_seconds"),
                "expires_at": row.get("expires_at"),
            })
        return safe

    def _executor_sizing_snapshot(self) -> Dict[str, Any]:
        """Executor sermaye görünümünü geriye uyumlu ve JSON-güvenli oku."""
        snapshotter = getattr(self.executor, "sizing_snapshot", None)
        if callable(snapshotter):
            try:
                raw = snapshotter()
                if isinstance(raw, dict):
                    allowed = {
                        "exchange_available",
                        "virtual_capital",
                        "eligible_realized_pnl",
                        "effective_equity",
                        "mode",
                        "start_trade_id",
                        "updated_at",
                    }
                    return {key: raw.get(key) for key in allowed}
            except Exception as e:
                self.logger.error(f"Scalper sizing snapshot okunamadı: {e}")

        last_equity = getattr(self.executor, "last_sizing_equity", None)
        return {
            "exchange_available": None,
            "virtual_capital": getattr(self.cfg, "scalper_virtual_capital_usdt", 0.0),
            "eligible_realized_pnl": None,
            "effective_equity": last_equity,
            "mode": (
                "virtual"
                if float(getattr(self.cfg, "scalper_virtual_capital_usdt", 0.0) or 0.0) > 0
                else "exchange"
            ),
            "start_trade_id": getattr(
                self.cfg, "scalper_virtual_capital_start_trade_id", 0
            ),
            "updated_at": None,
        }

    async def _virtual_risk_equity(self) -> Optional[float]:
        """Etkin TESTNET sanal sermayeyi günlük risk kapısı için çöz."""
        try:
            configured = float(
                getattr(self.cfg, "scalper_virtual_capital_usdt", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            configured = 0.0
        if configured <= 0:
            return None

        cached_equity, cached_at = getattr(
            self, "_virtual_equity_cache", (None, 0.0)
        )
        now_monotonic = time.monotonic()
        if (
            cached_equity is not None
            and now_monotonic - cached_at < self._VIRTUAL_EQUITY_CACHE_TTL
        ):
            return cached_equity

        # Engine startup'ında henüz try_open çalışmadığı için cached sizing
        # snapshot "uninitialized" olabilir. Public resolver borsa available
        # bakiyesi + doğrulanmış tracker ledger'ını burada hazırlar; böylece
        # virtual-capital risk kapısı kendi kendini kilitlemez.
        resolver = getattr(self.executor, "get_sizing_equity", None)
        if callable(resolver):
            try:
                resolved = await resolver()
                if resolved is not None and float(resolved) > 0:
                    equity = float(resolved)
                    self._virtual_equity_cache = (equity, now_monotonic)
                    return equity
            except Exception as e:
                self.logger.error(f"Scalper sizing equity çözülemedi: {e}")

        sizing = self._executor_sizing_snapshot()
        candidates = (
            sizing.get("effective_equity"),
            getattr(self.executor, "last_sizing_equity", None),
        )
        for value in candidates:
            try:
                equity = float(value)
            except (TypeError, ValueError):
                continue
            if equity > 0:
                self._virtual_equity_cache = (equity, now_monotonic)
                return equity
        return None

    async def _latch_entry_halt(
        self, error: UnprotectedPositionError, *, source: str
    ) -> None:
        """Korunamayan pozisyon sinyalini process-restart'e kadar kilitle."""
        if not getattr(self.cfg, "scalper_entry_halt_enabled", True):
            self.logger.critical(
                f"🚨 UnprotectedPositionError ({source}): {error}. Entry halt "
                "DEVRE DIŞI (scalper_entry_halt_enabled=false) — yeni girişler "
                "durdurulmadı, yalnız loglandı.",
                extra={"trade": True},
            )
            return
        if not self._entry_halted:
            self._entry_halted = True
            self._entry_halt_reason = f"{type(error).__name__}: {error}"
            self._entry_halted_at = _utcnow_iso()
            self.logger.critical(
                f"🚨 Scalper YENİ GİRİŞLERİ DURDURULDU ({source}): {error}. "
                "Safety izlemesi sürüyor; manuel kontrol ve restart/reconcile gerekli.",
                extra={"trade": True},
            )
            self._persist_entry_halt()

        # try_open ile aynı kilit: latch'ten hemen önce başlamış bir maker
        # girişi varsa tamamlanınca bu iptal turuna yakalanır.
        async with self._entry_lock:
            if self.executor.pending_symbols():
                try:
                    opened_during_cancel = await self.executor.cancel_all_pending()
                    self._track_opened_positions(
                        opened_during_cancel, source="safety latch iptal yarışı"
                    )
                except Exception as cancel_error:
                    self.logger.critical(
                        f"🚨 Entry safety latch aktif ancak pending girişler iptal "
                        f"edilemedi: {cancel_error}",
                        extra={"trade": True},
                    )

    @staticmethod
    def _task_exception(task: Optional[asyncio.Task]) -> Optional[str]:
        """Bitmiş bir task'In exception'ını JSON-güvenli metin olarak döndür."""
        if task is None or not task.done() or task.cancelled():
            return None
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return None
        return f"{type(exc).__name__}: {exc}" if exc is not None else None

    def _loop_health(
        self,
        *,
        task: Optional[asyncio.Task],
        last_started_at: Optional[str],
        last_success_at: Optional[str],
        last_success_monotonic: Optional[float],
        last_duration_seconds: Optional[float],
        last_error: Optional[str],
        last_error_at: Optional[str],
        consecutive_errors: int,
        success_count: int,
        freshness_limit_seconds: float,
    ) -> Dict[str, Any]:
        now = time.monotonic()
        success_age = (
            max(0.0, now - last_success_monotonic)
            if last_success_monotonic is not None
            else None
        )
        task_alive = bool(task and not task.done())
        fresh = bool(success_age is not None and success_age <= freshness_limit_seconds)
        task_exception = self._task_exception(task)
        healthy = bool(self.running and task_alive and fresh)

        return {
            "healthy": healthy,
            "task_alive": task_alive,
            "task_done": bool(task.done()) if task else None,
            "task_cancelled": bool(task.cancelled()) if task else None,
            "task_exception": task_exception,
            "last_started_at": last_started_at,
            "last_success_at": last_success_at,
            "last_success_age_seconds": round(success_age, 3) if success_age is not None else None,
            "freshness_limit_seconds": freshness_limit_seconds,
            "fresh": fresh,
            "last_duration_seconds": (
                round(last_duration_seconds, 3)
                if last_duration_seconds is not None
                else None
            ),
            "last_error": last_error,
            "last_error_at": last_error_at,
            "consecutive_errors": consecutive_errors,
            "success_count": success_count,
        }

    def health_snapshot(self) -> Dict[str, Any]:
        """Scan ve safety task'larının gerçek liveness/freshness durumu."""
        scan_freshness_limit = max(
            180.0, float(self.cfg.scalper_scan_interval_seconds) * 5.0
        )
        safety_freshness_limit = max(30.0, self._safety_interval_seconds() * 10.0)

        scan = self._loop_health(
            task=self._task,
            last_started_at=self._scan_last_started_at,
            last_success_at=self._scan_last_success_at,
            last_success_monotonic=self._scan_last_success_monotonic,
            last_duration_seconds=self._scan_last_duration_seconds,
            last_error=self._scan_last_error,
            last_error_at=self._scan_last_error_at,
            consecutive_errors=self._scan_consecutive_errors,
            success_count=self._scan_success_count,
            freshness_limit_seconds=scan_freshness_limit,
        )
        # D17: "yarıda kesilmiş tur" sağlıkta GÖRÜNÜR olmalı, ama `healthy`
        # bayrağını DÜŞÜRMEZ — ban sırasında unhealthy göstermek watchdog
        # restart'ını davet eder (2026-08-14 felaket yolu).
        scan.update(self._scan_degraded_snapshot())
        safety = self._loop_health(
            task=self._safety_task,
            last_started_at=self._safety_last_started_at,
            last_success_at=self._safety_last_success_at,
            last_success_monotonic=self._safety_last_success_monotonic,
            last_duration_seconds=self._safety_last_duration_seconds,
            last_error=self._safety_last_error,
            last_error_at=self._safety_last_error_at,
            consecutive_errors=self._safety_consecutive_errors,
            success_count=self._safety_success_count,
            freshness_limit_seconds=safety_freshness_limit,
        )
        exchange_age = (
            max(0.0, time.monotonic() - self._exchange_last_success_monotonic)
            if self._exchange_last_success_monotonic is not None
            else None
        )
        exchange_task_alive = bool(
            self._exchange_task and not self._exchange_task.done()
        )
        exchange_fresh = bool(
            exchange_age is not None
            and exchange_age <= self._EXCHANGE_PROBE_INTERVAL * 3.0
        )
        exchange = {
            "healthy": bool(
                exchange_task_alive and self._exchange_ready and exchange_fresh
            ),
            "task_alive": exchange_task_alive,
            "signed_account_ready": self._exchange_ready,
            "recovery_ready": self._recovery_ready,
            "risk_ready": self._risk_ready,
            "daily_pnl_source": self._daily_pnl_source,
            "last_success_at": self._exchange_last_success_at,
            "last_success_age_seconds": (
                round(exchange_age, 3) if exchange_age is not None else None
            ),
            "freshness_limit_seconds": self._EXCHANGE_PROBE_INTERVAL * 3.0,
            "last_error": self._exchange_last_error,
            "last_error_at": self._exchange_last_error_at,
            "success_count": self._exchange_success_count,
        }
        user_stream = {
            "running": self.user_stream.running,
            "connected": self.user_stream.connected,
            "last_event_at": self.user_stream.last_event_at,
            "last_error": self.user_stream.last_error,
            "reconnect_count": self.user_stream.reconnect_count,
            "rest_reconciliation_fallback": True,
        }
        return {
            "healthy": bool(
                self.running
                and not self._entry_halted
                and scan["healthy"]
                and safety["healthy"]
                and exchange["healthy"]
                and self._recovery_ready
                and self._risk_ready
            ),
            "running": self.running,
            "entry_halted": self._entry_halted,
            "entry_halt_reason": self._entry_halt_reason,
            "entry_halted_at": self._entry_halted_at,
            "risk_event_halted": bool(self._risk_event_halt_snapshot().get("active")),
            "risk_event_halt_reason": self._risk_event_halt_snapshot().get("reason"),
            "scan": scan,
            "safety": safety,
            "exchange": exchange,
            "user_stream": user_stream,
        }

    def _get_cached_regime(self, symbol: str, candles_4h: list) -> Regime:
        now = time.monotonic()
        cached = self._regime_cache.get(symbol)
        if cached is not None and (now - cached[1]) < self._REGIME_CACHE_TTL:
            return cached[0]
        regime = detect_regime(candles_4h)
        self._regime_cache[symbol] = (regime, now)
        return regime

    # ------------------------------------------------------------------
    # Lider piyasa kapısı (D15 — "ters-gün kapısı")
    # ------------------------------------------------------------------

    def _market_gate_leader(self) -> str:
        raw = getattr(self.cfg, "scalper_market_gate_symbol", "") or ""
        return str(raw).strip().upper() or "BTCUSDT"

    def _market_gate_retry_seconds(self) -> float:
        """Negatif önbellek süresi (sn) — okunamazsa 60."""
        try:
            value = float(getattr(self.cfg, "scalper_market_gate_retry_sec", 60.0))
        except (TypeError, ValueError):
            return 60.0
        return value if value > 0.0 else 0.0

    def _leader_source_host(self) -> Optional[str]:
        """Lider mumlarının geldiği HOST (secret YOK — public base_url'in
        yalnız ana bilgisayar kısmı).

        Neden durumda: harness mainnet verisiyle ölçüyor, canlı motor
        `settings.binance_base_url`'i (testnet) kullanıyor — E7/E8 sayılarını
        soak ile kıyaslarken hangi kaynakla karar verildiği GÖRÜNÜR olmalı
        (bkz. docs/DECISIONS.md D15 "Veri kaynağı paritesi").
        """
        fetcher = getattr(self, "fetcher", None)
        raw = getattr(fetcher, "base_url", None)
        if not raw:
            return None
        # urlsplit: elle string bölme `user:pass@host` biçimindeki userinfo'yu
        # sıyırmaz ve kimlik bilgisini status'e/log'a taşırdı (CLAUDE.md #5).
        try:
            host = urlsplit(str(raw)).hostname
        except ValueError:
            return None
        return host or None

    def _market_gate_warn(self, kind: str, message: str) -> None:
        """Kapı WARNING'i — TÜR BAŞINA dakikada en çok bir satır.

        Kalıcı bir arızada (yanlış lider sembolü, ağ) her sinyal denemesi bir
        satır basmamalı: log gürültüsü gerçek arızayı gizler. Bastırılan
        satırların sayısı `/scalper/status` → `consecutive_failures`'ta durur.

        Neden TÜR başına ve tek bir küresel sayaç değil: tek sayaçla, önce
        basılan ÖNEMSİZ bir uyarı (ör. "uzama serisi kısa") hemen ardından
        gelen ÖNEMLİ olanı (fail-open) susturur. Tür sayısı sabit ve küçük
        olduğu için üst sınır yine dakikada birkaç satırdır.
        """
        now = time.monotonic()
        stamps = getattr(self, "_market_gate_warn_at", None)
        if not isinstance(stamps, dict):
            stamps = {}
            self._market_gate_warn_at = stamps
        last = stamps.get(kind, 0.0)
        if last and (now - last) < self._MARKET_GATE_WARN_INTERVAL:
            return
        stamps[kind] = now
        self.logger.warning(message)

    def _market_gate_note_failure(
        self, detail: str, *, suppress_retry: bool = True
    ) -> None:
        """Lider verisi denemesi başarısız — sayaçlar, negatif önbellek, durum.

        `suppress_retry=False`: negatif önbelleğe DOKUNMA. Başlangıçtaki lider
        doğrulaması bunu kullanır — o çağrı İMZALI istemcinin `/exchangeInfo`
        ucudur, lider mumları ise AYRI bir istemciden (`KlineFetcher`,
        `/klines`) gelir. Geçici bir exchangeInfo hatası (ya da 15 sn timeout)
        yüzünden sapasağlam kline yolunu bir tam tarama turu susturmak
        gereksiz koruma kaybıdır.
        """
        self._market_gate_leader_ok = False
        self._market_gate_last_error = detail
        self._market_gate_last_failure_at = _utcnow_iso()
        self._market_gate_consecutive_failures = (
            getattr(self, "_market_gate_consecutive_failures", 0) + 1
        )
        # Kümülatif sayaç TOPARLANMADA SIFIRLANMAZ: dönüşümlü (flapping) bir
        # arıza — 60 sn bozuk / 60 sn sağlıklı — yalnız `consecutive_failures`
        # ile bakıldığında tertemiz görünür, oysa zamanın yarısında KORUMA
        # YOKTUR. Soak değerlendirmesi bu sayaç olmadan yapılamaz.
        self._market_gate_failures_total = (
            getattr(self, "_market_gate_failures_total", 0) + 1
        )
        if not suppress_retry:
            return
        retry = self._market_gate_retry_seconds()
        if retry > 0.0:
            self._market_gate_retry_after = time.monotonic() + retry

    @staticmethod
    def _looks_like_unknown_symbol(exc: Exception) -> bool:
        """Hata "sembol borsada yok" mu (KALICI), yoksa ağ/ban mı (GEÇİCİ)?"""
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return False
        text = str(exc).lower()
        return (
            "bulunamadı" in text
            or "invalid symbol" in text
            or "-1121" in text
        )

    async def _validate_market_gate_leader(self) -> bool:
        """Başlangıçta lider sembolünün borsada GERÇEKTEN var olduğunu doğrula.

        Neden: kapı fail-open'dır (spec §C) — yanlış yazılmış bir lider
        sembolü (`SCALPER_MARKET_GATE_SYMBOL=BTCUSD`) kapıyı SESSİZCE devre
        dışı bırakırdı; operatör `enabled: true` görüp korunduğunu sanırdı.
        `get_symbol_filters` exchangeInfo'yu okur ve sembol listede yoksa
        `BinanceAPIError` yükseltir. Başarısızlık kapıyı KAPATMAZ (fail-open
        semantiği korunur) ama `gate_effective=false` ile GÖRÜNÜR olur.
        """
        if not bool(getattr(self.cfg, "scalper_market_gate", False)):
            return False
        leader = self._market_gate_leader()
        try:
            # ZAMAN SINIRI ŞART: `ImprovedBinanceClient._request_with_retry`
            # 3 deneme × 60 sn timeout + backoff yapar — sınırsız bırakılırsa
            # ulaşılamayan bir borsada motor AÇILIŞINI dakikalarca bloke eder.
            # Aşarsa kapı "degraded" işaretlenir (fail-open korunur) ve ilk
            # tarama turunda kendiliğinden yeniden denenir.
            await asyncio.wait_for(
                self.client.get_symbol_filters(leader),
                timeout=self._MARKET_GATE_VALIDATE_TIMEOUT,
            )
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            # Doğrulama hatası kline yolunu SUSTURMAZ (ayrı istemci/uç).
            self._market_gate_note_failure(detail, suppress_retry=False)
            # Kalıcı (config) hata ile geçici (ağ/ban) hatayı AYIR: aynı metni
            # basmak operatörü doğru olan .env'i kurcalamaya, 418 sırasında ise
            # restart'a (CLAUDE.md yasak #3) itiyordu.
            if self._looks_like_unknown_symbol(e):
                tavsiye = (
                    f"SCALPER_MARKET_GATE_SYMBOL={leader} borsada YOK — değeri "
                    f"düzeltip yeniden başlatın (KALICI hata)"
                )
            else:
                tavsiye = (
                    "borsaya ULAŞILAMADI (geçici olabilir: ağ, timeout, 418 ban). "
                    "Ban aktifken RESTART YASAK (CLAUDE.md #3) — kapı ilk başarılı "
                    "tarama turunda kendiliğinden toparlar"
                )
            self.logger.error(
                f"⛔ PİYASA KAPISI DOĞRULANAMADI (degraded) — lider {leader} "
                f"({detail}). Kapı fail-open'dır: girişler BUGÜNKÜ GİBİ devam "
                f"eder ama KORUMA YOKTUR. {tavsiye}. Durum: /scalper/status → "
                f"market_gate.gate_effective"
            )
            return False
        self._market_gate_leader_ok = True
        self._market_gate_last_error = None
        self._market_gate_consecutive_failures = 0
        self._market_gate_retry_after = 0.0
        self.logger.info(f"🧭 Piyasa kapısı lideri doğrulandı: {leader}")
        return True

    async def _market_gate_reason(self, direction: Any) -> Optional[str]:
        """Kapı bu yönü engelliyorsa neden dizesi, aksi hâlde None.

        Kapı kapalıysa (varsayılan) HİÇ veri çekilmez — mevcut REST ağırlığı
        birebir korunur. Lider verisi alınamazsa kapı UYGULANMAZ (fail-open,
        spec §C) ve oran-sınırlı WARNING loglanır: lider verisinin gelmemesi
        bir risk olayı değildir, giriş hattı bugünkü davranışını sürdürür.

        Tazelik: anlık görüntü tarama TURU BAŞINDA bir kez yenilenir
        (`_scan_tick`), tur içindeki tüm semboller AYNI görüntüyü kullanır.
        TV `external_signal` tur dışında da gelebildiği için burada azami yaş
        `min(TTL, tarama aralığı)` ile sınırlanır — yani TV yolu hiçbir zaman
        bir tarama turundan daha bayat bir liderle karar vermez.
        """
        if not bool(getattr(self.cfg, "scalper_market_gate", False)):
            return None
        snapshot = await self._leader_market_snapshot(
            max_age=self._market_gate_max_age()
        )
        if snapshot is None:
            return None
        reason = evaluate_market_gate(
            direction,
            snapshot.get("day_open"),
            snapshot.get("last_close"),
            snapshot.get("daily_closes"),
            self.cfg,
        )
        # `last_reason` YALNIZ engellemede yazılır: serbest geçişte de
        # yazılırsa (ilk sürümdeki hata) her serbest sinyal onu None'a
        # döndürür ve `/scalper/status` pratikte HER ZAMAN null gösterir —
        # operatör "kapı hiç tetiklenmedi" sanır. Sayaçlar (`rejects`)
        # kümülatiftir, `last_reason`/`last_block_at` ise SON ENGELLEME.
        if reason is not None:
            self._market_gate_last_reason = reason
            self._market_gate_last_block_at = _utcnow_iso()
            self._market_gate_rejects[reason] = (
                self._market_gate_rejects.get(reason, 0) + 1
            )
        return reason

    def _market_gate_max_age(self) -> float:
        """Kapı kararında kabul edilen azami anlık görüntü yaşı (sn)."""
        try:
            scan_interval = float(
                getattr(self.cfg, "scalper_scan_interval_seconds", 60) or 60
            )
        except (TypeError, ValueError):
            scan_interval = 60.0
        return min(self._MARKET_GATE_CACHE_TTL, max(0.0, scan_interval))

    async def _refresh_leader_snapshot(self) -> None:
        """Tarama turu başında lider görüntüsünü BİR KEZ tazele.

        Parite (CLAUDE.md #2): harness kararı tam o mumun kapanışıyla verir;
        canlıda ise anlık görüntü TTL'i kadar bayat olabilirdi. Tur başında
        zorla tazeleyip tur boyunca aynı görüntüyü kullanmak, "bir tarama turu
        = bir lider görüntüsü" diyerek bu sapmayı tur süresine indirir.

        REST maliyeti: ÜST sınır değişmez (tur başına yine en çok 3 istek =
        3 ağırlık, bütçe 2400/dk) ama ALT sınır 0'dan 3 ağırlık/dakikaya
        ÇIKAR — eskiden kapı yalnız bir sinyal geldiğinde veri çekiyordu,
        sinyalsiz turda hiç istek gitmiyordu; artık kapı AÇIKSA her tur
        tazelenir. "Maliyet değişmez" ifadesi bu yüzden yanlıştı.
        """
        if not bool(getattr(self.cfg, "scalper_market_gate", False)):
            return
        try:
            await asyncio.wait_for(
                self._leader_market_snapshot(max_age=0.0),
                timeout=self._MARKET_GATE_REFRESH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # Zaman sınırı ŞART: lider erişilemezken `KlineFetcher`'ın
            # 3 deneme × (okuma timeout'u + backoff) zinciri tarama turunu
            # ~48 sn bloke edebilirdi. Kapı tavsiye niteliğindedir; TRADE
            # HATTI ONUN YÜZÜNDEN DURMAZ. Negatif önbellek de kurulur ki
            # sonraki turlar aynı bedeli ödemesin.
            self._market_gate_note_failure(
                f"TimeoutError: tur başı tazeleme "
                f"{self._MARKET_GATE_REFRESH_TIMEOUT:g} sn içinde bitmedi"
            )
            self._market_gate_warn(
                "refresh_timeout",
                f"⚠️ Piyasa kapısı: lider tazelemesi "
                f"{self._MARKET_GATE_REFRESH_TIMEOUT:g} sn'de bitmedi — tur "
                f"BEKLETİLMEDİ, kapı bu turda UYGULANMADI (fail-open)"
            )
        except Exception as e:
            # Kapı, tarama turunu düşürmeye yetkili DEĞİLDİR.
            self._market_gate_note_failure(f"{type(e).__name__}: {e}")
            self._market_gate_warn(
                "refresh_error",
                f"⚠️ Piyasa kapısı: tur başı tazeleme hata verdi "
                f"({type(e).__name__}: {e}) — tarama turu SÜRÜYOR (fail-open)"
            )

    async def _leader_market_snapshot(
        self, max_age: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """Lider sembolün (gün açılışı + günlük kapanışlar + giriş TF son
        kapanış) anlık görüntüsü.

        `max_age` (sn) verilmezse `_MARKET_GATE_CACHE_TTL` kullanılır; 0.0
        zorla tazeler. Önbellek AYRICA UTC gün damgasıyla anahtarlanır: gün
        sınırında (00:00 UTC) "gün açılışı" değişir, TTL'i gün sınırına taşan
        bir görüntü DÜNÜN açılışıyla karar verirdi.

        Negatif önbellek: son deneme başarısızsa `SCALPER_MARKET_GATE_RETRY_SEC`
        (vars. 60 sn) boyunca YENİDEN DENENMEZ — aksi hâlde kalıcı bir arızada
        (yanlış lider sembolü, ağ) her sinyal 3 seri × `KlineFetcher` yeniden
        denemeleri kadar boşa istek açar ve `KlineFetcher`'ın PAYLAŞILAN
        önbellek kilidini saniyelerce tutar (tüm sembollerin mum çekimi durur).
        """
        leader = self._market_gate_leader()
        now = time.monotonic()

        ttl = self._MARKET_GATE_CACHE_TTL if max_age is None else max(0.0, max_age)
        # Gün damgası önbelleğin İÇERİĞİNİN gününü temsil etmeli. İlk sürüm
        # bunu DUVAR SAATİNDEN türetiyordu; oysa `day_open`'ın hangi güne ait
        # olduğunu son KAPANAN giriş mumunun `close_time`'ı belirler. İkisi
        # 00:00 UTC'de değil, yeni günün İLK giriş mumu kapandığında hizalanır
        # — yani guard, tam da yazıldığı pencereyi (00:00-00:0X) kaçırıyordu ve
        # o pencerede DÜNÜN açılışıyla karar veriliyordu (üstelik
        # `day_open_source: intraday_open` etiketiyle). Artık damga içerikten
        # (`cutoff_ms`) türetilir; duvar saati yeni güne geçtiği andan itibaren
        # içerik de geçene kadar tazelemeye devam edilir.
        wall_day = utc_day_start_ms(int(time.time() * 1000))
        cached = self._market_gate_cache.get(leader)
        if (
            cached is not None
            and (now - cached[1]) < ttl
            and (len(cached) < 3 or cached[2] == wall_day)
        ):
            return cached[0]

        # Negatif önbellek önbellek OKUMASINDAN SONRA: aksi hâlde araya giren
        # TEK bir geçici hata, elde TAZE ve geçerli bir görüntü VARKEN kapıyı
        # `RETRY_SEC` boyunca tamamen kör bırakıyordu. Negatif önbelleğin işi
        # yeni ÇEKİMİ engellemektir, mevcut görüntüyü saklamak değil.
        if now < getattr(self, "_market_gate_retry_after", 0.0):
            return None

        try:
            run_days = int(getattr(self.cfg, "scalper_market_gate_run_days", 3) or 3)
        except (TypeError, ValueError):
            run_days = 3
        run_days = max(1, run_days)
        # N günlük koşu N+1 TAMAMLANMIŞ kapanış ister; KlineFetcher oluşmakta
        # olan günlük mumu attığı için (+1) bir mum daha gerekir → asgari N+2.
        # Talep N+5: asgari sınırda istemek SIFIR PAYLI'dır — borsanın tek bir
        # eksik/geç günlük mumu uzama alt-kapısını sessizce fail-open yapardı.
        # Ek 3 mum ağırlığı DEĞİŞTİRMEZ (limit ≤ 100 → ağırlık 1).
        tf_entry = str(getattr(self.cfg, "scalper_tf_entry", "5m") or "5m")
        # min(100, ...): limit > 100 Binance'te ağırlığı 1'den 2'ye çıkarır.
        # `SCALPER_MARKET_GATE_RUN_DAYS` büyük verilirse sessizce ağırlık
        # ödememek için tavan konur (varsayılan 3 → 8, tavana çok uzak).
        daily_limit = min(100, run_days + _LEADER_DAILY_LIMIT_MARGIN)

        try:
            daily = await self.fetcher.get_klines(leader, "1d", daily_limit)
            entry = await self.fetcher.get_klines(leader, tf_entry, 3)
            # Gerçek gün açılışı için 00:00 UTC 15m mumu (bkz.
            # market_gate.day_open_from_intraday): 100 mum = 25 saat ve
            # limit <= 100 olduğu için ağırlık 1. `_drop_unclosed`'a HİÇ
            # dokunmadan `1d` mumunun open'ıyla BİREBİR aynı değeri verir.
            intraday = await self.fetcher.get_klines(
                leader, MARKET_GATE_INTRADAY_TF, MARKET_GATE_INTRADAY_LIMIT
            )
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            self._market_gate_note_failure(detail)
            self._market_gate_warn(
                "fetch_error",
                f"⚠️ Piyasa kapısı: lider {leader} verisi alınamadı, kapı bu "
                f"turda UYGULANMADI ({detail}); "
                f"{self._market_gate_retry_seconds():g} sn yeniden denenmeyecek "
                f"(üst üste {self._market_gate_consecutive_failures} hata)"
            )
            return None

        daily_closes = [float(c.close) for c in daily]
        last_close = float(entry[-1].close) if entry else None
        # Karar anı: son KAPANAN giriş mumunun kapanışı (duvar saati DEĞİL) —
        # harness'ta da kesim zamanı mum kapanışıdır, parite böyle korunur.
        cutoff_ms = int(entry[-1].close_time) if entry else int(time.time() * 1000)
        day_open, day_open_source = resolve_day_open(
            [int(c.open_time) for c in intraday],
            [float(c.open) for c in intraday],
            [int(c.close_time) for c in intraday],
            daily_closes,
            cutoff_ms,
        )
        if day_open is None or last_close is None:
            detail = (
                f"seri yetersiz (1d={len(daily_closes)}, "
                f"{tf_entry}={len(entry)}, "
                f"{MARKET_GATE_INTRADAY_TF}={len(intraday)})"
            )
            self._market_gate_note_failure(detail)
            self._market_gate_warn(
                "insufficient_series",
                f"⚠️ Piyasa kapısı: lider {leader} {detail}, kapı bu turda "
                f"UYGULANMADI"
            )
            return None

        # Uzama alt-kapısı AÇIKKEN günlük seri kısa kalırsa o alt-kapı
        # SESSİZCE inert olurdu (saf fonksiyon veri yetersizliğinde atlar).
        # Gün-içi alt-kapısı çalışmaya devam ettiği için bu hata değil uyarı.
        try:
            run_pct_active = float(
                getattr(self.cfg, "scalper_market_gate_run_pct", 0.0) or 0.0
            ) > 0.0
        except (TypeError, ValueError):
            run_pct_active = False
        if run_pct_active and len(daily_closes) < run_days + 1:
            self._market_gate_warn(
                "short_daily_series",
                f"⚠️ Piyasa kapısı: lider {leader} günlük serisi kısa "
                f"({len(daily_closes)} mum; uzama alt-kapısı {run_days + 1} "
                f"TAMAMLANMIŞ kapanış ister, talep {daily_limit}) — uzama "
                f"alt-kapısı bu turda İNERT"
            )

        snapshot: Dict[str, Any] = {
            "leader": leader,
            "day_open": day_open,
            "day_open_source": day_open_source,
            "last_close": last_close,
            "daily_closes": daily_closes,
            **market_gate_metrics(day_open, last_close, daily_closes, self.cfg),
        }
        self._market_gate_cache[leader] = (
            snapshot, now, utc_day_start_ms(cutoff_ms)
        )
        self._market_gate_leader_ok = True
        self._market_gate_last_ok_at = _utcnow_iso()
        self._market_gate_last_error = None
        self._market_gate_consecutive_failures = 0
        self._market_gate_retry_after = 0.0
        # Uyarı zaman damgalarını da sıfırla: aksi hâlde T=0 arıza → T=30
        # toparlanma → T=40 YENİ arıza dizisinde ikinci epizot hiç loglanmaz
        # (60 sn penceresi ilk epizottan sayılırdı) ve operatör tek bir arıza
        # olduğunu sanır. `failures_total` yine de kümülatiftir.
        stamps = getattr(self, "_market_gate_warn_at", None)
        if isinstance(stamps, dict):
            stamps.clear()
        return snapshot

    def _market_gate_thresholds(self) -> Dict[str, Optional[float]]:
        """Yürürlükteki EŞİKLER — `/scalper/status`'te dışa verilir.

        D15'in kendi ilkesi "log'daki WARNING bir KONTROL DEĞİLDİR" (D14
        bulgusu #4). Eşiklerin doğru olduğunun tek kanıtı açılış banner'ıysa
        o ilke ihlal edilmiş olur: RUNBOOK'un doğrulaması log-DIŞI bir yüzeyde
        eşikleri görebilmeli.
        """
        def _f(name: str) -> Optional[float]:
            try:
                return float(getattr(self.cfg, name, 0.0) or 0.0)
            except (TypeError, ValueError):
                return None

        try:
            run_days: Optional[int] = int(
                getattr(self.cfg, "scalper_market_gate_run_days", 0) or 0
            )
        except (TypeError, ValueError):
            run_days = None
        return {
            "day_pct": _f("scalper_market_gate_day_pct"),
            "run_pct": _f("scalper_market_gate_run_pct"),
            "run_days": run_days,
        }

    def _market_gate_status(self) -> Dict[str, Any]:
        """`/scalper/status` alt-sözlüğü — SENKRON, hiç IO yapmaz (yalnız
        önbellekteki son anlık görüntüyü okur).

        **`gate_effective` = kapı GERÇEKTEN koruyor mu.** Üç şart birden:
        (1) `enabled`, (2) lider verisi ALINABİLDİ (`leader_ok` ve en az bir
        başarılı çekim), (3) görüntü BAYAT değil, (4) en az bir alt-kapı
        eşiği > 0. `enabled` bunların hiçbirini söylemez — kapı fail-open'dır.
        İnceleme bulguları: (a) yalnız `enabled AND leader_ok` bakmak, tek bir
        mum bile çekilmeden (sadece exchangeInfo doğrulamasıyla) `true`
        veriyordu; (b) `DAY_PCT=0` + `RUN_PCT=0` iken kapı hiçbir şey
        engellemezken `true` veriyordu — ikisi de RUNBOOK'un ZORUNLU
        doğrulamasını yanlış-yeşil yapardı.

        Türetilmiş metrikler (`day_drift_pct`, `run_drift_pct`,
        `day_open_source`) BAYAT görüntüde `null` verilir: karar yolu tazelik
        ve gün-sınırı testlerini uygularken status onları uygulamıyordu, yani
        "kapının ŞU AN gördüğü değer" diye sınırsız yaşta bir sayı
        gösterilebiliyordu.

        getattr savunması: `object.__new__(ScalperEngine)` ile kurulan test
        çiftleri (tests/test_runtime_liveness.py) __init__'i çalıştırmaz —
        snapshot() bu yüzden AttributeError'a düşmemeli.
        """
        leader = self._market_gate_leader()
        cache = getattr(self, "_market_gate_cache", {})
        cached = cache.get(leader)
        snapshot = cached[0] if cached is not None else {}

        age: Optional[float] = None
        if cached is not None:
            age = max(0.0, time.monotonic() - cached[1])
        # Gün sınırı da bayatlatır: içerik dünün gününe aitse metrik yanlıştır.
        day_rolled = (
            cached is not None
            and len(cached) >= 3
            and cached[2] != utc_day_start_ms(int(time.time() * 1000))
        )
        stale = age is None or age > self._stale_after_seconds() or day_rolled

        enabled = bool(getattr(self.cfg, "scalper_market_gate", False))
        leader_ok = getattr(self, "_market_gate_leader_ok", None)
        last_ok_at = getattr(self, "_market_gate_last_ok_at", None)
        thresholds = self._market_gate_thresholds()
        active = any(
            (thresholds.get(k) or 0.0) > 0.0 for k in ("day_pct", "run_pct")
        )
        return {
            "enabled": enabled,
            "gate_effective": bool(
                enabled and leader_ok is True and last_ok_at is not None
                and not stale and active
            ),
            "leader": leader,
            "leader_ok": leader_ok,
            "leader_source_host": self._leader_source_host(),
            "thresholds": thresholds,
            "stale": bool(stale),
            "snapshot_age_sec": None if age is None else round(age, 1),
            # Ölçülen büyüklükler. `run_drift_pct` adı bilerek eşikten
            # (`thresholds.run_pct`) FARKLI: ikisine de `run_pct` demek,
            # "uzama kapısı %4.2'de açık kalmış" gibi gerçekçi bir
            # yanlış-teşhis üretiyordu.
            "day_drift_pct": None if stale else snapshot.get("day_drift_pct"),
            "run_drift_pct": None if stale else snapshot.get("run_pct"),
            "day_open_source": None if stale else snapshot.get("day_open_source"),
            "last_ok_at": last_ok_at,
            "last_error": getattr(self, "_market_gate_last_error", None),
            "last_failure_at": getattr(self, "_market_gate_last_failure_at", None),
            "consecutive_failures": getattr(
                self, "_market_gate_consecutive_failures", 0
            ),
            "failures_total": getattr(self, "_market_gate_failures_total", 0),
            "last_reason": getattr(self, "_market_gate_last_reason", None),
            "last_block_at": getattr(self, "_market_gate_last_block_at", None),
            "rejects": dict(getattr(self, "_market_gate_rejects", {})),
        }

    def _stale_after_seconds(self) -> float:
        """Anlık görüntü bu yaştan sonra BAYAT (varsayılan 2 × tarama aralığı)."""
        try:
            scan_interval = float(
                getattr(self.cfg, "scalper_scan_interval_seconds", 60) or 60
            )
        except (TypeError, ValueError):
            scan_interval = 60.0
        return max(1.0, scan_interval) * self._MARKET_GATE_STALE_SCANS

    # ------------------------------------------------------------------
    # Günlük zarar kesici
    # ------------------------------------------------------------------

    async def _update_kill_switch(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._kill_switch_day != today:
            self._kill_switch_day = today
            self._kill_switch = False
            self._signals_today = 0

        if self.cfg.scalper_daily_loss_limit_pct <= 0:
            self._risk_ready = True
            self._daily_pnl_source = "disabled"
            self._risk_equity_usdt = None
            self._risk_equity_source = "disabled"
            self._daily_loss_threshold_usdt = None
            return  # kesici kapalı

        try:
            pnl = await self._get_account_daily_net_income(today)
        except Exception as e:
            self._risk_ready = False
            self._daily_pnl_source = "unavailable"
            self.logger.error(
                f"❌ Binance net günlük PNL doğrulanamadı; yeni girişler fail-closed kapalı: {e}"
            )
            return
        self._daily_pnl = pnl
        self._daily_pnl_source = "binance_account_income"
        self._risk_ready = True

        if self._kill_switch:
            return  # zaten tetiklenmiş — gün UTC değişene kadar kapalı kalır

        virtual_capital_enabled = bool(
            float(getattr(self.cfg, "scalper_virtual_capital_usdt", 0.0) or 0.0) > 0
        )
        if virtual_capital_enabled:
            balance = await self._virtual_risk_equity()
            self._risk_equity_source = "virtual_scalper_equity"
            if balance is None or balance <= 0:
                # Sanal sermaye seçilmişken tam TESTNET cüzdanına sessizce
                # dönmek günlük kayıp eşiğini gereksiz büyütür. Sizing ledger
                # hazır değilse yeni girişler fail-closed kalır.
                self._risk_ready = False
                self._risk_equity_usdt = None
                self._daily_loss_threshold_usdt = None
                self.logger.error(
                    "❌ Sanal scalper sermayesi çözülemedi; günlük risk kapısı "
                    "TESTNET wallet'a düşmeden fail-closed kapalı"
                )
                return
        else:
            balance = await self._get_cached_balance()
            self._risk_equity_source = "exchange_wallet"
            if balance is None or balance <= 0:
                self._risk_ready = False
                self._risk_equity_usdt = None
                self._daily_loss_threshold_usdt = None
                return

        self._risk_equity_usdt = balance

        approximate_day_start_balance = max(balance - pnl, 0.0)
        threshold = (
            -approximate_day_start_balance
            * self.cfg.scalper_daily_loss_limit_pct
            / 100.0
        )
        self._daily_loss_threshold_usdt = threshold
        if pnl <= threshold:
            self._kill_switch = True
            self.logger.warning(
                f"⛔ Scalper kill switch TETİKLENDİ: günlük PNL={pnl:.2f} <= eşik={threshold:.2f} "
                f"(risk_sermayesi={balance:.2f}, kaynak={self._risk_equity_source}, "
                f"limit=%{self.cfg.scalper_daily_loss_limit_pct}). "
                f"Yeni giriş durduruldu, açık pozisyonların çıkış takibi sürüyor."
            )

    async def _get_account_daily_net_income(self, today: str) -> float:
        """Account-level realized PnL + commissions + funding, signed by Binance."""

        cached_value, cached_at, cached_day = self._daily_income_cache
        now_monotonic = time.monotonic()
        if (
            cached_value is not None
            and cached_day == today
            and now_monotonic - cached_at < self._INCOME_CACHE_TTL
            # Son okumadan beri kapanış kaydedildiyse önbellek bayattır:
            # limit aşımı 120 sn TTL'i beklemeden bir sonraki turda görülür.
            and self._income_cache_close_seq == getattr(self.tracker, "close_seq", 0)
        ):
            return cached_value

        start = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 1000
        rows = await self.client.get_income_history(
            start_time_ms=start_ms,
            end_time_ms=end_ms,
            limit=1000,
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

        self._daily_income_cache = (net, now_monotonic, today)
        self._income_cache_close_seq = getattr(self.tracker, "close_seq", 0)
        return net

    async def _get_cached_balance(self) -> Optional[float]:
        balance, cached_at = self._balance_cache
        now = time.monotonic()
        if balance is not None and (now - cached_at) < self._BALANCE_CACHE_TTL:
            return balance
        try:
            fresh = await self.client.get_wallet_balance()
        except Exception as e:
            self.logger.error(f"❌ Wallet bakiye sorgusu hatası (kill switch): {e}")
            return balance
        self._balance_cache = (fresh, now)
        return fresh

    # ------------------------------------------------------------------
    # API için anlık durum
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        tracked = []
        # ExitManager sembol->ScalpPosition eşlemesini herkese açık bir
        # erişimci olarak sunmuyor (bkz. exits.py); bu paket-içi sıkı
        # bağımlılık kasıtlıdır — exits.py bu amaçla DEĞİŞTİRİLMEDİ.
        for symbol, sp in self.exits._positions.items():
            entry = sp.position.entry_price
            current_price = sp.position.current_price or entry
            quantity = sp.position.quantity
            leverage = sp.position.leverage or 1
            direction = sp.signal.direction

            unrealized_pnl = 0.0
            roi_pct = 0.0
            if entry > 0:
                if direction.value == "LONG":
                    unrealized_pnl = (current_price - entry) * quantity
                    price_delta_pct = (current_price - entry) / entry * 100.0
                else:
                    unrealized_pnl = (entry - current_price) * quantity
                    price_delta_pct = (entry - current_price) / entry * 100.0
                roi_pct = price_delta_pct * leverage

            plan = getattr(sp, "plan", None)
            entry_fee_rate = getattr(plan, "entry_fee_rate", None)
            exit_fee_rate = getattr(plan, "exit_fee_rate", None)
            breakeven_cost_pct = getattr(plan, "breakeven_cost_pct", None)
            fee_rate_source = getattr(plan, "fee_rate_source", None)
            fee_aware_breakeven = bool(
                breakeven_cost_pct is not None
                or entry_fee_rate is not None
                or exit_fee_rate is not None
                or fee_rate_source
            )

            tracked.append({
                "symbol": symbol,
                "strategy": sp.signal.strategy,
                "direction": direction.value,
                "entry_price": entry,
                "current_price": current_price,
                "quantity": quantity,
                "current_stoploss": sp.position.current_stoploss,
                "tp1_done": sp.tp1_done,
                "tp2_done": bool(getattr(sp, "tp2_done", False)),
                "trailing_active": sp.trailing_active,
                "breakeven_active": bool(
                    getattr(sp, "breakeven_active", sp.tp1_done)
                ),
                "breakeven_price": getattr(plan, "breakeven_price", None),
                "breakeven_cost_pct": breakeven_cost_pct,
                "fee_aware_breakeven": fee_aware_breakeven,
                "entry_fee_rate": entry_fee_rate,
                "exit_fee_rate": exit_fee_rate,
                "fee_rate_source": fee_rate_source,
                "runner_floor_price": getattr(plan, "runner_floor_price", None),
                "unrealized_pnl": unrealized_pnl,
                "roi_pct": roi_pct,
                "opened_at": sp.position.opened_at.isoformat() if sp.position.opened_at else None,
            })

        cooldowns = self._executor_cooldown_snapshot()
        sizing = self._executor_sizing_snapshot()
        sizing_equity = sizing.get("effective_equity")
        if sizing_equity is None:
            sizing_equity = getattr(self.executor, "last_sizing_equity", None)
        configured_virtual_base = getattr(
            self.cfg, "scalper_virtual_capital_usdt", 0.0
        )
        current_virtual_capital = sizing.get("virtual_capital")
        virtual_start_trade_id = sizing.get("start_trade_id")
        if virtual_start_trade_id is None:
            virtual_start_trade_id = getattr(
                self.cfg, "scalper_virtual_capital_start_trade_id", 0
            )
        virtual_enabled = bool(
            str(sizing.get("mode") or "").startswith("virtual")
            or float(configured_virtual_base or 0.0) > 0
        )

        return {
            "enabled": self.cfg.scalper_enabled,
            "running": self.running,
            "shadow_mode": bool(getattr(self.cfg, "scalper_shadow_mode", False)),
            # D17 teşhis: kline verisinin geldiği host + "trading_host"/"separate".
            # Ayarı değil FETCHER'ın gerçeğini raporlar (bkz.
            # _kline_source_snapshot); secret içermez.
            **self._kline_source_snapshot(),
            "scan_interval": self.cfg.scalper_scan_interval_seconds,
            "safety_interval": self._safety_interval_seconds(),
            "health": self.health_snapshot(),
            "universe": list(self._universe),
            "regimes": dict(self._regimes),
            "structure": {
                k: dict(v) for k, v in (getattr(self, "_structure", None) or {}).items()
            },
            "market_gate": self._market_gate_status(),
            "daily_pnl": self._daily_pnl,
            "daily_pnl_source": self._daily_pnl_source,
            "risk_ready": self._risk_ready,
            "risk_equity_usdt": self._risk_equity_usdt,
            "risk_equity_source": self._risk_equity_source,
            "daily_loss_threshold_usdt": self._daily_loss_threshold_usdt,
            "daily_limit_pct": self.cfg.scalper_daily_loss_limit_pct,
            "kill_switch_active": self._kill_switch,
            "entry_halted": self._entry_halted,
            "entry_halt_reason": self._entry_halt_reason,
            "entry_halted_at": self._entry_halted_at,
            "risk_event": self.risk_event_status(),
            "signals_today": self._signals_today,
            "last_scan_at": self._last_scan_at,
            # D17: kesilen tarama turu artık "başarılı" sayılmıyor; durum
            # burada görünür ("ok" | "degraded:market_data").
            **self._scan_degraded_snapshot(),
            # D17: ayrı market-data host'unda fiyat-uzayı çevirisi ya da
            # koruma-tarafı kapısı yüzünden GÖNDERİLMEYEN trailing emirleri.
            # Sürekli artıyorsa iki defter arasındaki baz bozulmuştur.
            "trailing_skips": self._exits_trailing_skip_snapshot(),
            "tracked": tracked,
            "pending_entries": self.executor.pending_snapshot(),
            "cooldowns": cooldowns,
            "entry_rejects": self._executor_reject_snapshot(),
            "stop_mode": str(getattr(self.cfg, "scalper_stop_mode", "structural")),
            "sizing": sizing,
            "sizing_equity_usdt": sizing_equity,
            "virtual_capital_enabled": virtual_enabled,
            "virtual_capital_base_usdt": configured_virtual_base,
            "virtual_capital_current_usdt": current_virtual_capital,
            "virtual_capital_start_trade_id": virtual_start_trade_id,
            "symbol_reservations": symbol_reservations.snapshot(),
        }
