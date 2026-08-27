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
from src.services.tv_events import tv_events as _tv_events_singleton
from src.strategies.scalper.executor import ScalpExecutor
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.indicators import atr as compute_atr
from src.strategies.scalper import intent
from src.strategies.scalper import counterfactual_store
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
    FOLLOWER_LEDGER_STRATEGY,
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
    price_at_roi,
)
from src.trading.binance_client_improved import (
    ImprovedBinanceClient,
    RestWeightBackoff,
)
from src.trading.position_manager import PositionManager, UnprotectedPositionError
from src.trading.symbol_reservations import (
    FOLLOWER_RESERVATION_OWNER,
    symbol_reservations,
)
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
    # D21 post-mortem: mum isteği için üst sınır. `KlineFetcher` kendi içinde
    # 3 deneme × 15 sn yeniden dener; yavaş/5xx bir veri host'unda bu ~48 sn
    # eder. Post-mortem AYRI bir task'ta koşsa bile açık uçlu beklemez: bir
    # teşhis isteği bir sonraki turu ve `stop()`u geciktirmemelidir.
    _FORENSICS_POSTMORTEM_TIMEOUT = 5.0
    # Aynı işlem için azami ölçüm denemesi; sonra "ölçülemedi" yazılır.
    # Sonsuz yeniden deneme kuyruğun BAŞINI tıkar (aday listesi kapanış
    # zamanına göre sıralıdır) ve her dakika boşa istek yakar.
    _FORENSICS_POSTMORTEM_MAX_ATTEMPTS = 3
    # Piyasa verisi kesintisi uyarısının azami sıklığı (sn). Tarama turu 30
    # sn'de bir döner; uzun bir banda (180 sn+) her tur satır basmak logu
    # doldurur ve gerçek olayları gizler.
    _SCAN_DEGRADED_LOG_INTERVAL = 60.0
    # TV olay çıkışlarında TUR BAŞINA azami aksiyon (D19a bulgu G6). Reaper
    # ile aynı ders (2026-08-14): eşzamanlı çoklu kapanış safety turunu
    # şişirip watchdog restart'ını tetikliyordu; safety turu borsa tazelik
    # eşiğinin (30 sn) altında kalmalı. Kalan olaylar tüketilmez, bir
    # sonraki turda ele alınır.
    _TV_EXIT_MAX_ACTIONS_PER_TICK = 1
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
            # D17-R3: borsa-arası baz LIKE-FOR-LIKE ölçülür — veri host'unun
            # CANLI fiyatı da AYNI fetcher'dan (aynı host, aynı ağırlık
            # bütçesi/kesici) gelir. Aynı host'ta hiç çağrılmaz.
            data_price_fetch=self.fetcher.get_price,
            # D21: kapanış anındaki piyasa bağlamı (sembol rejimi, lider gün
            # sapması, BTC fiyatı). SENKRON ve önbellekten okunur — ek REST
            # çağrısı YOKTUR; yalnız adli kayda yazılır.
            forensics_context_cb=self._forensics_close_context,
        )

        # D27/B karşı-olgu defteri (YALNIZ GÖZLEM). Modül varsayılanı KAPALI
        # olduğu için `object.__new__` ile kurulan test çiftlerinde hiç
        # çalışmaz; gerçek motor burada açar. Ana anahtar adli kayıttır:
        # `SCALPER_FORENSICS_ENABLED=false` bu defteri de kapatır (D24 ile
        # aynı tek-bayrak disiplini).
        try:
            counterfactual_store.configure(
                enabled=(
                    bool(getattr(self.cfg, "scalper_forensics_enabled", True))
                    and bool(
                        getattr(self.cfg, "scalper_counterfactual_enabled", True)
                    )
                ),
                horizons_h=counterfactual_store.parse_horizons(
                    getattr(self.cfg, "scalper_counterfactual_horizons_h", None)
                ),
                max_pending=getattr(
                    self.cfg, "scalper_counterfactual_max_pending",
                    counterfactual_store.DEFAULT_MAX_PENDING,
                ),
                dedup_sec=getattr(
                    self.cfg, "scalper_counterfactual_dedup_sec",
                    counterfactual_store.DEFAULT_DEDUP_SEC,
                ),
                max_age_h=getattr(
                    self.cfg, "scalper_counterfactual_max_age_h",
                    counterfactual_store.DEFAULT_MAX_AGE_H,
                ),
                # Planı OLMAYAN niyetler (TV sağlaması `/tv-signal`'da
                # reddeder; orada ScalpSignal YOKTUR) için yedek ROI
                # politikası — motorun kendi TP1/stop yüzdeleri.
                tp1_roi_pct=getattr(self.cfg, "scalper_tp1_roi", 0.0),
                stop_roi_pct=getattr(self.cfg, "scalper_fixed_stop_roi_pct", 0.0),
                policy_leverage=getattr(self.cfg, "scalper_leverage", 0),
            )
            self._warn_counterfactual_horizon_fit()
        except Exception as e:  # pragma: no cover - teşhis kurulumu motoru düşürmez
            self.logger.warning(f"⚠️ Karşı-olgu defteri kurulamadı ({e}); kapalı kalıyor")

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
        # D21 adli kayıt: kurulum hatası bir kez uyarılır, akış etkilenmez.
        self._forensics_error_logged: bool = False
        # D23 AI karar katmanı (GÖLGE). TEMBEL kurulur: `SCALPER_AI_GATE_MODE`
        # `off` (varsayılan) iken hiç örneklenmez → sıfır bellek, sıfır çağrı,
        # bugünküyle birebir aynı davranış. Katman motor yolundan YALNIZ
        # `_ai_gate_observe` ile çağrılır ve `_entry_lock` DIŞINDADIR.
        self._ai_gate = None
        self._ai_gate_error_logged: bool = False
        # Post-mortem turu (kapanıştan N dk sonra) için son çalıştırma anı.
        self._forensics_postmortem_at: float = 0.0
        # Post-mortem AYRI bir arka plan task'ında koşar: safety turu onu
        # BEKLEMEZ (D21-R3, düşmanca inceleme bulgusu 1). Aynı anda EN FAZLA
        # BİR post-mortem çalışır; referans burada tutulur ki `stop()` onu
        # iptal edebilsin ve ikinci bir tur başlatılmasın.
        self._forensics_postmortem_task: Optional[asyncio.Task] = None
        # trade_id -> başarısız ölçüm denemesi. 3'te bir kez "ölçülemedi"
        # yazılır; sonsuz yeniden deneme kuyruğu tıkar.
        self._forensics_postmortem_attempts: Dict[int, int] = {}
        self._balance_cache: Tuple[Optional[float], float] = (None, 0.0)
        self._daily_pnl: float = 0.0
        self._daily_pnl_source: str = "unavailable"
        # Yalnız BİLGİ (D20b/Y8): gömülü modda hesabın ham günlük income'ı;
        # kesici bunu KULLANMAZ, iki sayının farkı operatöre gösterilir.
        self._daily_income_account: Optional[float] = None
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
        # TV olay kanalı (D19). Süreç-tekili defteri motor DEĞİŞTİRMEZ,
        # yalnız okur ve telemetri sayaçlarını artırır; testler bu alanı
        # kendi TvEvents örnekleriyle değiştirebilir.
        self.tv_events = _tv_events_singleton
        # Sembol başına "en son TÜKETİLEN" olay sırası: aynı olay her safety
        # turunda yeniden BE/kapanış tetiklemesin. OTORİTE DEFTERDEDİR
        # (`TvEvents.consumed_seq`, `state/tv_events.json` — D19a bulgu D);
        # bu iki sözlük yalnız RAM aynasıdır ve `consumed_seq` sunmayan eski
        # test çiftlerinde yedek olarak kullanılır.
        self._tv_exit_seen: Dict[str, int] = {}
        self._tv_struct_seen: Dict[str, int] = {}
        self._tv_attempts: Dict[Any, int] = {}
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
        await self._assert_universe_survives_follower_reservation()
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
            for task in (
                self._task,
                self._safety_task,
                self._exchange_task,
                # D21 post-mortem arka plan task'ı: kapanışta askıda kalmasın
                # (yalnız teşhis işi — iptali hiçbir koruma işini etkilemez).
                getattr(self, "_forensics_postmortem_task", None),
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # D23: bekleyen AI gözlem görevleri (ateşle-unut) askıda kalmasın.
        # İptalleri hiçbir koruma işini etkilemez — yalnız kayıt düşer.
        gate = getattr(self, "_ai_gate", None)
        if gate is not None:
            try:
                await gate.aclose()
            except Exception as e:  # pragma: no cover - savunma
                self.logger.warning(f"⚠️ AI karar katmanı kapatılamadı: {e}")

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

        if bool(getattr(self.cfg, "scalper_shadow_mode", False)):
            # D28: gerçek shadow süreç hesap kilidini bilinçli olarak atlar.
            # Bu nedenle yalnız YENİ girişteki `executor.try_open` kapısı
            # yetmez: eski DB'deki OPEN/maker satırını recover etmek,
            # `exits.recover` veya pending reconciliation üzerinden borsaya
            # koruma/iptal emri gönderebilirdi. Shadow gözlem halkası hiçbir
            # gerçek pozisyonu sahiplenmez; recovery-ready yalnız scan
            # ölçümünün fail-closed readiness kapısından geçebilmesi içindir.
            self._recovery_ready = True
            self.logger.warning(
                "👻 GÖLGE MODU: maker/pozisyon recovery ATLANDI — "
                "borsadaki gerçek pozisyonlar sahiplenilmez"
            )
            return True

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
                # D20b (doğrulayıcı bulgusu Y6): gömülü modda çakışan sembolün
                # sahibi TAKİPÇİYSE bu bir veri tutarsızlığı değil, iki motorun
                # aynı hesabı paylaşmasının olağan sonucudur (defter filtresi
                # zaten aynı satırı iki kez kurtarmayı imkânsız kılar). Kalıcı
                # halt dosyası YAZMA ve döngüyü KIRMA — atla, uyar, devam et.
                # Ayrı halka ve diğer TÜM sahipler AYNEN eski davranışta.
                if (
                    bool(getattr(self.cfg, "follower_embedded", False))
                    and symbol_reservations.owner(symbol)
                    == FOLLOWER_RESERVATION_OWNER
                ):
                    self.logger.warning(
                        f"⚠️ {symbol}: sembol gömülü AlgoPro takipçisinin "
                        f"yönetiminde — scalper kurtarmasında ATLANDI "
                        f"(kalıcı kilit yazılmadı)."
                    )
                    continue
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

    def entries_blocked_by(self) -> Optional[str]:
        """Yeni girişleri KİM durduruyor? (D22 — durum netliği)

        `None` = hiçbir şey (tarama dönüyor). Aksi halde ilk uygulanan kapı:
        `"entry_halt"` | `"kill_switch"` | `"risk_event"` |
        `"exchange_readiness"` | `"rest_weight"`.

        `rest_weight` EN SONDADIR ve yalnız geri çekilme AÇIKKEN
        (`BINANCE_WEIGHT_*_LIMIT > 0`, varsayılan KAPALI) dolabilir: diğerleri
        kalıcı/politik kapılarken bu, o dakikaya özgü bir bütçe kısıtıdır.

        Neden gerekli: giriş kapalıyken `_scan_tick` lider anlık görüntüsünü
        TAZELEMEZ; `/scalper/status.market_gate` bir süre sonra
        `stale=true, gate_effective=false` gösterir. Bu, "kapı bozuldu" gibi
        okunur ama gerçek neden bambaşkadır (2026-08-23 log incelemesi).

        `object.__new__(ScalperEngine)` test çiftlerinde alanlar hiç
        bulunmayabilir — teşhis alanı ASLA status'u düşürmez.
        """
        try:
            if getattr(self, "_entry_halted", False):
                return "entry_halt"
            if getattr(self, "_kill_switch", False):
                return "kill_switch"
            if bool(self._risk_event_halt_snapshot().get("active")):
                return "risk_event"
            exchange_last = getattr(
                self, "_exchange_last_success_monotonic", None
            )
            exchange_age = (
                time.monotonic() - exchange_last
                if exchange_last is not None
                else float("inf")
            )
            ready = bool(
                getattr(self, "_exchange_ready", False)
                and exchange_age <= self._EXCHANGE_PROBE_INTERVAL * 3.0
                and getattr(self, "_recovery_ready", False)
                and getattr(self, "_risk_ready", False)
            )
            if not ready:
                return "exchange_readiness"
            # D22: geri çekilme AÇIKSA `_scan_tick` turu hiç başlatmaz —
            # o zaman girişleri durduran gerçekten budur ve panoda da öyle
            # görünmelidir (`scan_status=degraded:rest_weight` ile birlikte).
            if self._rest_weight_backoff_level() != "off":
                return "rest_weight"
            return None
        except Exception:  # pragma: no cover - teşhis alanı asla patlamamalı
            return None

    def _follower_reserved_symbols(self) -> Set[str]:
        """Gömülü takipçiye AYRILMIŞ semboller (D20b, kullanıcı kararı).

        Boş küme = bugünkü davranış birebir. Dolu olması için İKİSİ de şart:
        `FOLLOWER_EMBEDDED=true` **ve** `FOLLOWER_SYMBOLS` dolu.
        """
        try:
            return {
                str(s).strip().upper()
                for s in (getattr(self.cfg, "follower_reserved_symbols", []) or [])
                if str(s).strip()
            }
        except Exception:  # savunmacı: teşhis alanı taramayı düşürmemeli
            return set()

    def _follower_managed_symbols(self) -> Set[str]:
        """Gömülü takipçinin YÖNETTİĞİ semboller (rezervasyon kaydından).

        Boş küme = gömülü mod kapalı ya da takipçinin açık pozisyonu yok →
        hesap-geneli kapasite hesabı bugünküyle BİREBİR aynı kalır.
        """
        if not bool(getattr(self.cfg, "follower_embedded", False)):
            return set()
        try:
            return {
                symbol
                for symbol, owner in symbol_reservations.snapshot().items()
                if owner == FOLLOWER_RESERVATION_OWNER
            }
        except Exception:  # pragma: no cover - kayıt taramayı düşürmez
            return set()

    async def _assert_universe_survives_follower_reservation(self) -> None:
        """`FOLLOWER_SYMBOLS` scalper'ın GERÇEK evrenini boşaltıyor mu? (D20b)

        NEDEN BURADA (doğrulayıcı bulgusu Y1): `config.py`'deki startup kontrolü
        yalnız `SCALPER_SYMBOL_ALLOWLIST` DOLUYKEN çalışabilir — canlı `.env`'de
        o satır YOKTUR (varsayılan boş) ve gerçek evren `scanner.get_universe()`
        ten gelir. Yani koruma canlı yapılandırmada ÖLÜYDÜ: 8 sembolün hepsini
        `FOLLOWER_SYMBOLS`a yazmak scalper'ı sessizce hiç tarama yapmaz hâle
        getiriyordu.

        Evren okunamazsa (ağ/ban) kontrol ATLANIR + WARNING: bir teşhis
        kontrolü, borsa erişimi yokken botu başlatmamazlık etmemeli
        (2026-08-12 ban dersi). O hâlde ikinci katman tarama turundadır
        (`_exclude_follower_symbols` → `_mark_scan_degraded`).
        """
        reserved = self._follower_reserved_symbols()
        if not reserved:
            return
        allowlist_csv = str(
            getattr(self.cfg, "scalper_symbol_allowlist", "") or ""
        ).strip()
        if allowlist_csv:
            universe = [s.strip().upper() for s in allowlist_csv.split(",") if s.strip()]
            source = "SCALPER_SYMBOL_ALLOWLIST"
        else:
            try:
                universe = [
                    str(s).upper() for s in (await self.scanner.get_universe() or [])
                ]
            except Exception as exc:
                self.logger.warning(
                    f"⚠️ Takipçi evren kontrolü yapılamadı (tarama evreni "
                    f"okunamadı: {exc}); tarama turunda tekrar denenecek"
                )
                return
            source = "scanner"
        if not universe:
            return
        if set(universe) - reserved:
            return
        raise RuntimeError(
            f"AYAR HATASI: FOLLOWER_SYMBOLS ({', '.join(sorted(reserved))}) "
            f"scalper'ın tarama evrenini ({source}: {', '.join(universe)}) "
            f"TAMAMEN boşaltıyor — scalper hiçbir sembolü tarayamaz. "
            f"Takipçiye ayrılmamış en az bir sembol bırakın."
        )

    def _exclude_follower_symbols(self, universe: List[str]) -> List[str]:
        """Tarama evreninden takipçi sembollerini çıkar (bir kez loglar)."""
        reserved = self._follower_reserved_symbols()
        if not reserved:
            return list(universe)
        kept = [s for s in universe if str(s).upper() not in reserved]
        dropped = sorted({str(s).upper() for s in universe} & reserved)
        if dropped and dropped != getattr(self, "_follower_excluded_logged", None):
            self._follower_excluded_logged = dropped
            self.logger.info(
                f"🤖 Tarama evreninden çıkarıldı — AlgoPro takipçisine ayrılmış: "
                f"{', '.join(dropped)}"
            )
        if universe and not kept:
            # Startup kontrolü ATLANMIŞ olabilir (evren o an okunamadı) ya da
            # evren sonradan daralmış olabilir: tur SESSİZ geçmesin.
            self._mark_scan_degraded(
                f"tarama evreni FOLLOWER_SYMBOLS ({', '.join(sorted(reserved))}) "
                f"tarafından tamamen boşaltıldı",
                kind="universe_empty",
            )
        return kept

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
        # TV olay çıkışları (D19) — exits.step()'ten SONRA: normal SL/TP/
        # trailing yolu her zaman önce işler, TV olayı yalnız hâlâ AÇIK olan
        # pozisyonlara bakar. Reaper'dan ÖNCE: bir olay varsa kapanış nedeni
        # "yaş" değil "TV_EVENT" olarak etiketlenmeli.
        await self._apply_tv_event_exits()
        await self._reap_aged_positions()
        # D21 post-mortem: TÜM çıkış/koruma işlerinden SONRA ve AYRI bir
        # task'ta — safety turu onu BEKLEMEZ. Yavaş/5xx bir veri host'unda
        # `get_klines` üç deneme × 15 sn boyunca askıda kalabilir; bunu tur
        # içinde beklemek TP1→BE, trailing ve kill-switch'i geciktirir,
        # `/health` 503'e düşer ve watchdog restart'ı davet eder
        # (2026-08-14 dersi). Bir teşhis işi asla bir koruma işini
        # geciktirmez.
        self._forensics_postmortem_schedule()
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
                # D27/A1 — YALNIZ ETİKET: emir BORSAYA GİTTİKTEN sonra damgala.
                # Emir hata verirse (except dalı) damga KONULMAZ: kapanmayan bir
                # pozisyon "REAPER" diye etiketlenmemelidir. Damga hiçbir kapıya
                # ya da çıkış kararına girmez; yalnız `_infer_exit_reason` kaba
                # çıkarımının yaş-kesmesini "SL" ile karıştırmasını engeller.
                try:
                    sp.reaper_close_at = _utcnow_iso()
                except Exception:  # pragma: no cover - test çiftleri (SimpleNamespace)
                    pass
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
        self, symbol: str, sp: Any, forced_exit_reason: str = "RISK_EVENT", *, exit_reason: Optional[str] = None
    ) -> bool:
        """Bir pozisyonu reduce-only MARKET ile kapat ve borsada DOĞRULA.

        `exit_reason`: deftere yazılacak kapanış etiketi. Varsayılan
        `RISK_EVENT` (D10 flatten'ı — davranış değişmedi); TV olay kanalı
        (D19) `TV_EVENT` geçer. Emir yolu, doğrulama ve tek-finalizer
        kilidi HER İKİSİNDE DE aynıdır — yeni bir kapanış yolu YAZILMADI.

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
        if exit_reason is not None:
            forced_exit_reason = exit_reason
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
        #
        # D22: TARAMA kritik olmayan bir tüketicidir (evren taraması, hesap
        # özeti, mumlar). Dakikalık REST ağırlık bütçesi yumuşak sınırı
        # aştıysa tur HİÇ BAŞLAMAZ — bütçe koruma turuna (positionRisk,
        # SL/TP, kapanış doğrulaması) bırakılır. Yeni pozisyon açmamak
        # ucuzdur; açık pozisyonda körleşmek 2026-08-12'de saatlerce süren
        # 418 ban döngüsünün ta kendisiydi.
        backoff = self._rest_weight_backoff_level()
        if backoff != "off":
            self._mark_scan_degraded(
                f"rest_weight: geri çekilme={backoff}, son ölçüm "
                f"{self._rest_weight_snapshot().get('last')}/dk",
                kind="rest_weight",
            )
            return

        allowlist_csv = str(
            getattr(self.cfg, "scalper_symbol_allowlist", "") or ""
        ).strip()
        if allowlist_csv:
            # Kanıt disiplini: canlı evren, backtest'in kapsadığı sembollere
            # sabitlenebilir — scanner'ın top_n listesi hiç sorgulanmaz.
            universe = [
                s.strip().upper() for s in allowlist_csv.split(",") if s.strip()
            ]
        else:
            universe = await self.scanner.get_universe()
        self._universe = self._exclude_follower_symbols(universe)

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
        except RestWeightBackoff as e:
            self._mark_scan_degraded(f"rest_weight: {e}", kind="rest_weight")
            return
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
            label = (
                "REST ağırlık bütçesi dolu"
                if kind == "rest_weight"
                else "Piyasa verisi kullanılamıyor"
            )
            self.logger.warning(
                f"⛔ {label} ({reason}); tarama turu "
                f"kesildi — tur DEGRADE sayıldı "
                f"(scan_status=degraded:{kind}, toplam "
                f"{self._scan_degraded_count})"
            )

    def _rest_weight_backoff_level(self) -> str:
        """"off" | "soft" | "hard" — kritik olmayan istekler için (D22).

        Sınıf düzeyi durum okunur; `object.__new__` test çiftlerinde ve
        istemci taklitlerinde güvenle "off" döner (fail-open: teşhis kapısı
        taramayı kazara durdurmamalı).
        """
        try:
            level = ImprovedBinanceClient.weight_backoff_level()
        except Exception:  # pragma: no cover - teşhis kapısı asla patlamamalı
            return "off"
        return str(level or "off")

    def _rest_weight_snapshot(self) -> Dict[str, Any]:
        """İmzalı REST yolunun ağırlık telemetrisi (D22) — secret içermez."""
        try:
            snapshotter = getattr(
                type(self.client), "rest_weight_snapshot", None
            )
            if not callable(snapshotter):
                return {}
            return dict(snapshotter())
        except Exception as e:  # teşhis alanı asla status'u düşürmemeli
            return {"error": f"{type(e).__name__}: {e}"}

    def _forensics_queue_snapshot(self) -> Dict[str, Any]:
        """Adli kayıt yazıcı kuyruğu + post-mortem turu (D21/D22 teşhis)."""
        try:
            from src.strategies.scalper import forensics_log

            out: Dict[str, Any] = dict(forensics_log.queue_snapshot())
        except Exception as e:  # teşhis alanı asla status'u düşürmemeli
            return {"error": f"{type(e).__name__}: {e}"}
        try:
            task = getattr(self, "_forensics_postmortem_task", None)
            out["postmortem_running"] = bool(task is not None and not task.done())
            out["postmortem_blocked"] = self._forensics_postmortem_blocked()
        except Exception as e:
            out["postmortem_running"] = None
            out["postmortem_blocked"] = f"{type(e).__name__}: {e}"
        return out

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

    async def _evaluate_symbol(
        self,
        symbol: str,
        enabled_strategies: list,
        *,
        external_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """`external_meta` (D21): TV kaynağı/sağlama özeti — YALNIZ adli kayıt.

        Hiçbir kapıya girmez; None (varsayılan) verildiğinde bu fonksiyon
        eskisiyle birebir aynıdır.
        """
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

        # D27/B karşı-olgu defteri: olgunlaşmış "reddedilen niyet" kayıtları
        # BU turun ZATEN çektiği mumlarla çözülür — yeni REST çağrısı YOK.
        # Karar yolundan ÖNCE ya da SONRA olması fark etmez (hiçbir şeye
        # dokunmaz); burada, ctx kurulur kurulmaz duruyor ki her tur her
        # sembol için tam bir çözüm şansı olsun.
        self._counterfactual_resolve(symbol, ctx)

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
            # D21 adli kayıt: sinyalin doğduğu an. Kapılar + emir gecikmesi
            # (`stale_signal`) buradan ölçülür. Karar yoluna GİRMEZ.
            signal_epoch = time.time()
            # D24/madde 7: niyet kaydının kaynak ayrımı — KARAR YOLUNA GİRMEZ.
            # (`is_external` birkaç satır aşağıda AYNI sınıf adından türetilir;
            # o satıra DOKUNULMADI.)
            intent_source = (
                "tv"
                if strat.__class__.__name__ == "_ExternalSignalStrategy"
                else "scan"
            )
            # (a) Niyet DOĞDU. `_evaluate_symbol`'ün EN BAŞINDAKİ erken
            # dönüşler (owner rezervasyonu, cooldown, `_scan_open_symbols`)
            # için kayıt YAPILMAZ: orada henüz SİNYAL yoktur, dolayısıyla bir
            # "niyet" de doğmamıştır — onları saymak reddedilenleri
            # sembol × tarama turu kadar şişirirdi.
            self._record_intent(
                symbol=symbol,
                direction=sig.direction,
                signal=sig,
                stage=intent.STAGE_PROPOSED,
                decision=intent.DECISION_ALLOW,
                strategy=getattr(sig, "strategy", None),
                source=intent_source,
            )
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
                    self._record_intent(  # (b) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_DECIDED,
                        decision=intent.DECISION_DENY,
                        reason=intent.REASON_REGIME_GATE,
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                        extra={"regime": rejim},
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
                self._record_intent(  # (c) D24/madde 7 — yalnız kayıt
                    symbol=symbol,
                    direction=sig.direction,
                    signal=sig,
                    stage=intent.STAGE_DECIDED,
                    decision=intent.DECISION_DENY,
                    # `market_reason` ZATEN "market_gate_day"/"market_gate_run"
                    # değerlerini üretir (bkz. _market_gate_reason).
                    reason=market_reason,
                    strategy=getattr(sig, "strategy", None),
                    source=intent_source,
                    extra={"leader": gate_status.get("leader")},
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
                self._record_intent(  # (d) D24/madde 7 — yalnız kayıt
                    symbol=symbol,
                    direction=sig.direction,
                    signal=sig,
                    stage=intent.STAGE_DECIDED,
                    decision=intent.DECISION_DENY,
                    reason=intent.REASON_STRUCTURE_GATE,
                    strategy=getattr(sig, "strategy", None),
                    source=intent_source,
                    extra={
                        "last_event": getattr(structure_state, "last_event", None)
                    },
                )
                continue
            # TV yapı kapısı (D19): rejim kapısının HEMEN yanında, AYNI tek
            # giriş noktasında — C stratejisi de TV dış sinyali de buradan
            # geçer. `shadow` (varsayılan) modda yalnız loglar/sayar, hiçbir
            # sinyali düşürmez; `active` modda BEAR yapıda LONG / BULL yapıda
            # SHORT girişini engeller (bkz. _tv_structure_gate_blocks).
            if self._tv_structure_gate_blocks(symbol, sig.direction):
                self._record_intent(  # (e) D24/madde 7 — yalnız kayıt
                    symbol=symbol,
                    direction=sig.direction,
                    signal=sig,
                    stage=intent.STAGE_DECIDED,
                    decision=intent.DECISION_DENY,
                    reason=intent.REASON_TV_STRUCTURE_GATE,
                    strategy=getattr(sig, "strategy", None),
                    source=intent_source,
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
                    self._record_intent(  # (f) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_DECIDED,
                        decision=intent.DECISION_DENY,
                        # Yukarıdaki `reason` insan metnidir; sayaç için AYNI
                        # ayrımın makine karşılığı kurulur (koşullar birebir).
                        reason=(
                            intent.REASON_ENTRY_HALT
                            if self._entry_halted
                            else intent.REASON_KILL_SWITCH
                            if self._kill_switch
                            else intent.REASON_RISK_EVENT
                            if risk_event_snap.get("active")
                            else intent.REASON_EXCHANGE_UNVERIFIED
                        ),
                        detail=reason,
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                    )
                    return
                # Mumlar indirilirken veya strateji hesaplanırken bir koruma
                # hatası cooldown başlatmış olabilir. POST'tan hemen önceki
                # bu ikinci kapı yarış penceresini kapatır.
                if self._executor_entry_blocked(symbol):
                    self.logger.info(
                        f"⏭️ {symbol}: giriş cooldown aktif, hazır sinyal açılmadı"
                    )
                    self._record_intent(  # (g) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_DECIDED,
                        decision=intent.DECISION_DENY,
                        reason=intent.REASON_LOSS_COOLDOWN,
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                    )
                    return
                if symbol in tracked or symbol in pending:
                    self._record_intent(  # (h) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_DECIDED,
                        decision=intent.DECISION_DENY,
                        reason=intent.REASON_ALREADY_TRACKED,
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                    )
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
                        self._record_intent(  # (i) D24/madde 7 — yalnız kayıt
                            symbol=symbol,
                            direction=sig.direction,
                            signal=sig,
                            stage=intent.STAGE_DECIDED,
                            decision=intent.DECISION_DENY,
                            reason=intent.REASON_CAPACITY,
                            detail="shadow",
                            strategy=getattr(sig, "strategy", None),
                            source=intent_source,
                            extra={
                                "open_positions": open_count,
                                "shadow_active": shadow_active,
                            },
                        )
                        return
                elif open_count >= self.cfg.scalper_max_positions:
                    self.logger.info(f"⏭️ {symbol}: scalper pozisyon kapasitesi dolu, sinyal açılmadı")
                    self._record_intent(  # (i) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_DECIDED,
                        decision=intent.DECISION_DENY,
                        reason=intent.REASON_CAPACITY,
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                        extra={"open_positions": open_count},
                    )
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
                    self._record_intent(  # (j) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_DECIDED,
                        decision=intent.DECISION_DENY,
                        reason=intent.REASON_EXCHANGE_UNVERIFIED,
                        detail=f"{type(e).__name__}: {e}",
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                    )
                    return

                live_symbols = {
                    str(raw.get("symbol", "")).upper()
                    for raw in exchange_positions
                    if float(raw.get("positionAmt", 0) or 0) != 0
                }
                if symbol in live_symbols:
                    self._record_intent(  # (k) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_DECIDED,
                        decision=intent.DECISION_DENY,
                        reason=intent.REASON_EXCHANGE_POSITION_EXISTS,
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                    )
                    return
                # D20b (düşmanca inceleme): hesap-geneli tavan takipçiyi
                # SAYMAZ. Takipçinin kendi tavanı (FOLLOWER_MAX_POSITIONS)
                # vardır; onun 4 pozisyonu scalper'ı 3 yerine 1 slota
                # düşürüyordu ve ters yönde hiçbir sınır yoktu. Gömülü mod
                # kapalıyken iki küme de boştur → davranış birebir aynı.
                follower_symbols = self._follower_managed_symbols()
                if not follower_symbols:
                    scoped_owners = None
                    scoped_live = live_symbols
                else:
                    scoped_owners = tuple(
                        o
                        for o in symbol_reservations.snapshot().values()
                        if o != FOLLOWER_RESERVATION_OWNER
                    ) + (self._RESERVATION_OWNER,)
                    scoped_live = live_symbols - follower_symbols
                if not symbol_reservations.reserve(
                    symbol,
                    self._RESERVATION_OWNER,
                    capacity=getattr(
                        self.cfg,
                        "max_positions",
                        self.cfg.scalper_max_positions,
                    ),
                    exchange_symbols=scoped_live,
                    capacity_owners=scoped_owners,
                ):
                    self.logger.info(
                        f"⏭️ {symbol}: sembol başka motorun yönetiminde veya hesap kapasitesi dolu"
                    )
                    self._record_intent(  # (l) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_DECIDED,
                        decision=intent.DECISION_DENY,
                        reason=intent.REASON_SYMBOL_RESERVED_BY_OTHER,
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                    )
                    return

                self._opening_symbols.add(symbol)
                unsafe_failure = False
                sp = None
                # D21: giriş-anı bağlamı. Yalnız hazır anlık görüntüleri okur
                # (yeni REST çağrısı YOK) ve hata hâlinde None döner —
                # girişi ASLA engellemez.
                forensics_ctx = self._forensics_entry_context(
                    symbol=symbol,
                    signal=sig,
                    ctx=ctx,
                    structure_state=structure_state,
                    is_external=is_external,
                    signal_epoch=signal_epoch,
                    open_positions=open_count,
                    external_meta=external_meta,
                )
                try:
                    # `forensics=` YALNIZ bağlam kurulabildiyse geçilir:
                    # executor yerine iki-argümanlı bir çift koyan
                    # test/entegrasyon kurulumları bu yolla bozulmaz ve adli
                    # kaydın kapalı olması giriş akışını hiç değiştirmez.
                    if forensics_ctx is not None:
                        sp = await self.executor.try_open(
                            sig, ctx, forensics=forensics_ctx
                        )
                    else:
                        sp = await self.executor.try_open(sig, ctx)
                except UnprotectedPositionError:
                    # Sembol, outer loop kalıcı entry latch'i etkinleştirene
                    # kadar in-flight kümesinde kalır. Bu kısa aralıkta safety
                    # sync fail-open biçimde ownership'i bırakamaz.
                    unsafe_failure = True
                    raise
                except Exception as e:
                    # Normal bir emir reddi/istemci hatasında try_open ya
                    # journal+pending durumunu kurmuştur ya da aşağıdaki
                    # finally rezervasyonu bırakacaktır. In-flight işareti
                    # bu sembolü sonsuza dek kapasitede tutmamalı.
                    self._opening_symbols.discard(symbol)
                    # (n) D24/madde 7: emir hatası da bir SONUÇTUR; bugün
                    # yalnız `raise` ile yukarı gidiyor ve sayısal izi yok.
                    # `raise` ÇIPLAK kalır → davranış birebir aynıdır.
                    self._record_intent(
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_EXECUTED,
                        decision=intent.DECISION_ERROR,
                        reason=intent.REASON_ORDER_ERROR,
                        detail=f"{type(e).__name__}: {e}",
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                    )
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
                    self._record_intent(  # (m) D24/madde 7 — yalnız kayıt
                        symbol=symbol,
                        direction=sig.direction,
                        signal=sig,
                        stage=intent.STAGE_EXECUTED,
                        decision=intent.DECISION_ALLOW,
                        reason=intent.REASON_OPENED,
                        strategy=getattr(sig, "strategy", None),
                        source=intent_source,
                        extra={
                            "entry_price": getattr(
                                getattr(sp, "position", None), "entry_price", None
                            )
                        },
                    )
            finally:
                if not unsafe_failure:
                    # try_open sonucunda ya pending journal ya da tracked
                    # pozisyon artık ownership'i taşıyor; başarısız normal
                    # denemede rezervasyon yukarıda zaten bırakıldı.
                    self._opening_symbols.discard(symbol)
            # D23 (GÖLGE) AI karar katmanı — `_entry_lock` DIŞINDA ve karar
            # yolu YUKARIDA BİTTİKTEN sonra. Senkron, O(1) bir çağrıdır:
            # yalnız sözlük kopyalar ve `asyncio.create_task` ateşler; motor
            # 0 ms bekler. Katman KAPALIYKEN (varsayılan) hiç örneklenmez.
            self._ai_gate_observe(
                symbol=symbol,
                signal=sig,
                ctx=ctx,
                context=forensics_ctx,
                position=sp,
                signal_epoch=signal_epoch,
            )
            # Sembol başına tek deneme: sinyal bulunduğu an (başarılı ya da
            # başarısız) bu sembol için tur biter.
            break

    # ------------------------------------------------------------------
    # İşlem adli kaydı (D21) — YALNIZ GÖZLEM
    # ------------------------------------------------------------------

    def _forensics_enabled(self) -> bool:
        return bool(getattr(self.cfg, "scalper_forensics_enabled", True))

    # ------------------------------------------------------------------
    # AI karar katmanı (D23) — GÖLGE, motor yolunda 0 ms
    # ------------------------------------------------------------------

    def _ai_gate_mode(self) -> str:
        mode = str(
            getattr(self.cfg, "scalper_ai_gate_mode", "off") or "off"
        ).strip().lower()
        return mode if mode in ("off", "shadow", "active") else "off"

    def _ai_gate_warn(self, message: str) -> None:
        """Katman arızasını BİR KEZ duyur; hiçbir akışı kesme (fail-open)."""
        if not getattr(self, "_ai_gate_error_logged", False):
            self._ai_gate_error_logged = True
            self.logger.warning(
                f"⚠️ AI karar katmanı kurulamadı/çalışmadı ({message}) — bu "
                f"uyarı bir kez loglanır, GİRİŞLER ETKİLENMEZ (D23 fail-open)"
            )

    def _ai_gate_layer(self):
        """Katmanı TEMBEL kur. Kapalıyken (varsayılan) `None` döner."""
        if self._ai_gate_mode() == "off":
            return None
        gate = getattr(self, "_ai_gate", None)
        if gate is not None:
            return gate
        try:
            from src.strategies.scalper.ai_gate import AiGate

            gate = AiGate(self.cfg, logger=self.logger, tracker=self.tracker)
        except Exception as e:
            self._ai_gate_warn(f"{type(e).__name__}: {e}")
            return None
        self._ai_gate = gate
        return gate

    def _ai_gate_observe(
        self,
        *,
        symbol: str,
        signal: Any,
        ctx: StrategyContext,
        context: Optional[Dict[str, Any]],
        position: Any,
        signal_epoch: float,
    ) -> None:
        """Motor yolundaki TEK AI çağrısı — SENKRON, O(1), fail-open.

        `_entry_lock` DIŞINDADIR ve karar yolu bittikten SONRA çağrılır:
        gölgede motorun davranışı BAYT BAYT aynıdır. Girdiler zaten kurulmuş
        `forensics_ctx` + dolum belgesidir; YENİ REST çağrısı YOKTUR.
        """
        gate = self._ai_gate_layer()
        if gate is None:
            return
        if context is None:
            # D23'ün TÜM girdileri D21 bağlamından gelir. Adli kayıt kapalıysa
            # (ya da bağlam kurulamadıysa) modele sorulacak anlamlı bir şey
            # yoktur: boş bir payload için para harcamak yerine sessizce
            # atlanır. Operatör bunu bir kez logda görür.
            self._ai_gate_warn(
                "adli giriş bağlamı yok (SCALPER_FORENSICS_ENABLED kapalı mı?)"
            )
            return
        try:
            candles = list(getattr(ctx, "candles_5m", None) or [])
            bar_close_time_ms = (
                int(getattr(candles[-1], "close_time", 0)) if candles else None
            )
            trade_id = getattr(position, "trade_id", None)
            gate.observe(
                symbol=symbol,
                direction=getattr(signal, "direction", None),
                strategy=getattr(signal, "strategy", None),
                context=context,
                entry=getattr(position, "forensics_entry", None),
                trade_id=trade_id,
                bar_close_time_ms=bar_close_time_ms,
                signal_epoch=signal_epoch,
                opened=trade_id is not None,
            )
        except Exception as e:
            self._ai_gate_warn(f"observe {type(e).__name__}: {e}")

    def _ai_gate_snapshot(self) -> Dict[str, Any]:
        """`/scalper/status` bloğu — motor yolundan BAĞIMSIZ, yalnız bellek."""
        mode = self._ai_gate_mode()
        gate = getattr(self, "_ai_gate", None)
        if gate is None:
            # Kapalı ya da henüz hiç aday görülmedi: pano "alan yok" ile
            # "katman kapalı"yı karıştırmasın diye ŞEKİL hep aynıdır.
            return {"mode": mode, "effective_mode": mode, "enabled": mode != "off"}
        try:
            snapshot = dict(gate.snapshot())
        except Exception as e:  # pragma: no cover - teşhis alanı düşmemeli
            return {"mode": mode, "error": f"{type(e).__name__}: {e}"}
        snapshot["enabled"] = mode != "off"
        return snapshot

    def _forensics_warn(self, message: str) -> None:
        """Adli kayıt arızasını BİR KEZ duyur; hiçbir akışı kesme."""
        if not getattr(self, "_forensics_error_logged", False):
            self._forensics_error_logged = True
            self.logger.warning(
                f"⚠️ Adli kayıt bağlamı kurulamadı ({message}) — bu uyarı bir "
                f"kez loglanır, giriş/çıkış akışı ETKİLENMEZ"
            )

    def _record_intent(
        self,
        *,
        symbol: str,
        direction: Any,
        stage: str,
        decision: str,
        reason: Optional[str] = None,
        detail: Optional[str] = None,
        strategy: Any = None,
        source: Any = None,
        extra: Optional[Dict[str, Any]] = None,
        signal: Any = None,
    ) -> None:
        """Üç-aşamalı niyet kaydı (D24/madde 7) — YALNIZ GÖZLEM.

        Gerçekleşmeyen bir niyet (kapı reddi, kapasite, emir hatası) bugün
        `scalp_trades`'te iz bırakmaz; bu kanca o izi bırakır. Sözleşme:

          * Adli kayıt KAPALIYSA hiç çalışmaz (tek bayrak, tek anahtar).
          * `await` YOKTUR: `intent.record` sayaç artırır ve JSONL kuyruğuna
            O(1) satır bırakır; disk yazımı ayrı iş parçacığındadır.
          * İSTİSNA SIZDIRMAZ. Bir teşhis kaydı bir girişi ya da bir reddi
            ASLA değiştirmemeli — hata hâlinde sessizce düşer.

        D27/B — `signal`: karşı-olgu defteri için giriş/stop/TP1 planı.
        `apply_stop_policy` SAF bir fonksiyondur (`dataclasses.replace` ile
        YENİ bir sinyal döndürür); burada çağrılması motorun kendi sinyalini
        DEĞİŞTİRMEZ. REDDEDİLEN (ya da emir hatası alan) niyetler ayrıca
        karşı-olgu kuyruğuna alınır; ÇÖZÜM `_evaluate_symbol`'de, tarama
        turunun ZATEN çektiği mumlarla yapılır — **yeni REST ağırlığı
        SIFIR**.
        """
        try:
            # Bayrak okuması da TRY İÇİNDE: `cfg`'si hiç kurulmamış bir test
            # çifti ya da yarım kurulmuş bir motor, bir TEŞHİS kaydı yüzünden
            # tarama turunu düşürmemeli.
            if not self._forensics_enabled():
                return
            at = _utcnow_iso()
            # D27 incelemesi (D1): plan hesabı DEFTER BAYRAĞINA da bağlıdır.
            # Eskiden yalnız `_forensics_enabled()`e bakıyordu, yani defteri
            # kapatmak fazladan `apply_stop_policy` çağrısını ve intent
            # satırlarındaki dört yeni alanı DURDURMUYORDU (ALLOW/PROPOSED
            # niyetlerde de koşuyordu).
            if counterfactual_store.enabled():
                price, stop_price, tp1_price, leverage = self._counterfactual_plan(
                    signal
                )
            else:
                price = stop_price = tp1_price = leverage = None
            intent.record(
                at=at,
                symbol=symbol,
                direction=direction,
                stage=stage,
                decision=decision,
                strategy=strategy,
                source=source,
                reason=reason,
                detail=detail,
                extra=extra,
                price=price,
                stop_price=stop_price,
                tp1_price=tp1_price,
                leverage=leverage,
            )
            if decision in (intent.DECISION_DENY, intent.DECISION_ERROR):
                counterfactual_store.register(
                    at=at,
                    at_epoch=time.time(),
                    symbol=symbol,
                    direction=direction,
                    reason=reason,
                    price=price,
                    stop_price=stop_price,
                    tp1_price=tp1_price,
                    leverage=leverage,
                    strategy=strategy,
                    source=source,
                    plan_source="signal" if price is not None else None,
                )
        except Exception:
            # Sessiz: `intent.record` zaten kendi içinde yutar; buraya yalnız
            # bir test çifti/monkeypatch patlarsa düşülür ve o da akışı
            # kesmemeli (gözlem ≠ güvenlik kilidi).
            return

    def _counterfactual_plan(self, signal: Any) -> Tuple[
        Optional[float], Optional[float], Optional[float], Optional[int]
    ]:
        """(giriş, stop, TP1, kaldıraç) — SİNYALDEN türetilir, IO yok.

        Motorun gerçekte kuracağı planla AYNI iki saf dönüşümü kullanır:
        `apply_stop_policy` (canlı + harness ortak stop politikası, bkz.
        DECISIONS #P1) ve `price_at_roi` (executor'ın TP1 formülü). Böylece
        karşı-olgu, "başka bir kurala göre ne olurdu"yu değil, **bu botun
        kendi kurallarına göre ne olurdu**yu ölçer.

        Sinyal yoksa ya da hesap patlarsa dört değer de `None` döner
        ("ölçülmedi"); karşı-olgu satırı yine açılır ama simülasyon
        `no_data` der — uydurma plan YAZILMAZ.
        """
        if signal is None:
            return None, None, None, None
        try:
            planned = apply_stop_policy(signal, self.cfg)
            entry = float(getattr(planned, "entry_price", 0.0) or 0.0)
            if entry <= 0:
                return None, None, None, None
            leverage = int(
                getattr(planned, "leverage", None)
                or getattr(self.cfg, "scalper_leverage", 0)
                or 0
            )
            if leverage <= 0:
                return entry, None, None, None
            stop = float(getattr(planned, "stop_price", 0.0) or 0.0) or None
            tp1 = price_at_roi(
                entry,
                float(getattr(self.cfg, "scalper_tp1_roi", 0.0) or 0.0),
                leverage,
                planned.direction,
            )
            return entry, stop, tp1, leverage
        except Exception:
            return None, None, None, None

    def _counterfactual_snapshot(self) -> Dict[str, Any]:
        """D27/B sayaçları — O(1), disk/DB işi YOK (pano bunu 5 sn'de yoklar)."""
        try:
            return counterfactual_store.counters_snapshot()
        except Exception as e:  # pragma: no cover - teşhis alanı status'u düşürmez
            return {"error": f"{type(e).__name__}: {e}"}

    def _counterfactual_resolve(self, symbol: str, ctx: StrategyContext) -> None:
        """Olgunlaşmış karşı-olgu kayıtlarını ZATEN ÇEKİLMİŞ mumlarla çöz.

        **Yeni REST çağrısı YOKTUR**: `ctx.candles_5m` adı tarihsel olsa da
        giriş diliminin bu tarama turunda zaten çekilmiş ~150 mumunu taşır.
        `counterfactual_store` bekleyen niyet boyunca örtüşen turları rolling
        tamponda birleştirir; böylece 1m profildeki 2.5 saatlik tek pencere de
        8 saatlik ufku süreç içinde kapsar. Hata hâlinde sessizce döner: bir
        ölçüm kaydı bir tarama turunu ASLA düşürmemeli.
        """
        try:
            candles = getattr(ctx, "candles_5m", None) or getattr(
                ctx, "candles_15m", None
            )
            if not candles:
                return
            counterfactual_store.resolve_symbol(symbol, candles, time.time())
        except Exception:
            return

    def _forensics_entry_context(
        self,
        *,
        symbol: str,
        signal: Any,
        ctx: StrategyContext,
        structure_state: Any,
        is_external: bool,
        signal_epoch: float,
        open_positions: int,
        external_meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Giriş anındaki "neden girildi" bağlamı.

        YENİ REST ÇAĞRISI YOKTUR: yalnız `ctx`'te zaten bulunan seriler ve
        senkron anlık görüntüler (`_market_gate_status`, `tv_events.snapshot`,
        `_kline_source_snapshot`) okunur. Hata hâlinde None döner ve giriş
        normal biçimde sürer — bu bir gözlem katmanıdır, güvenlik kilidi
        DEĞİLDİR.
        """
        if not self._forensics_enabled():
            return None
        try:
            from src.strategies.scalper import forensics as fx

            direction = getattr(signal.direction, "value", str(signal.direction))
            gate_status = self._market_gate_status()
            leader_gate = fx.leader_gate_snapshot(gate_status)

            # Lider (BTC) fiyatı: kapı görüntüsü BAYAT değilse okunur.
            btc_price = None
            if not gate_status.get("stale"):
                cached = getattr(self, "_market_gate_cache", {}).get(
                    self._market_gate_leader()
                )
                if cached:
                    btc_price = (cached[0] or {}).get("last_close")

            regime_filter_on = bool(
                getattr(self.cfg, "scalper_regime_filter", True)
            ) and (
                not is_external
                or bool(getattr(self.cfg, "scalper_tv_regime_filter", True))
            )
            tv_mode = self._tv_events_mode()
            gates = {
                "regime": "passed" if regime_filter_on else "off",
                "leader": {
                    "kapalı": "off",
                    "geçti": "passed",
                    "etkin_değil": "degraded",
                }.get(leader_gate.get("verdict"), "off"),
                "structure": (
                    "passed"
                    if bool(getattr(self.cfg, "scalper_structure_gate", False))
                    else "off"
                ),
                "tv_structure": {
                    "off": "off", "shadow": "shadow", "active": "passed",
                }.get(tv_mode, "off"),
                "capacity": "passed",
                "cooldown": "passed",
            }

            tv_structure = None
            ledger = self._tv_ledger()
            if ledger is not None and tv_mode != "off":
                verdict, rows = ledger.structure_verdict(symbol)
                tv_structure = {
                    "mode": tv_mode,
                    "verdict": verdict,
                    "sources": sorted(
                        {str(row.get("source")) for row in rows if row.get("source")}
                    ),
                }

            entry_candles = list(getattr(ctx, "candles_5m", None) or [])
            candle_age = None
            if entry_candles:
                candle_age = max(
                    0.0, signal_epoch - entry_candles[-1].close_time / 1000.0
                )

            context: Dict[str, Any] = {
                "source": "TV" if is_external else "C",
                "signal_epoch": signal_epoch,
                "signal_at": datetime.fromtimestamp(
                    signal_epoch, tz=timezone.utc
                ).isoformat(timespec="seconds"),
                "candle_age_sec": None if candle_age is None else round(candle_age, 1),
                "indicators": fx.indicator_snapshot(ctx, self.cfg),
                "regime": {
                    "value": getattr(ctx.regime, "value", str(ctx.regime)),
                    "tf": str(getattr(self.cfg, "scalper_tf_regime", "4h") or "4h"),
                    "direction": direction,
                },
                "leader_gate": leader_gate,
                "structure": structure_snapshot(structure_state) or None,
                "tv_structure": tv_structure,
                "gates": gates,
                "kline_source": self._kline_source_snapshot().get("kline_source"),
                "open_positions": int(open_positions),
                "daily_pnl": self._daily_pnl,
                "btc_price": btc_price,
            }
            if external_meta:
                context["tv"] = dict(external_meta)
            return context
        except Exception as e:
            self._forensics_warn(f"{type(e).__name__}: {e}")
            return None

    def _forensics_close_context(self, symbol: str) -> Dict[str, Any]:
        """Kapanış anındaki piyasa bağlamı — SENKRON, hiç IO yapmaz."""
        try:
            gate_status = self._market_gate_status()
            btc_price = None
            if not gate_status.get("stale"):
                cached = getattr(self, "_market_gate_cache", {}).get(
                    self._market_gate_leader()
                )
                if cached:
                    btc_price = (cached[0] or {}).get("last_close")
            return {
                "regime": self._regimes.get(str(symbol).upper()),
                "leader_day_drift_pct": gate_status.get("day_drift_pct"),
                "btc_price": btc_price,
            }
        except Exception as e:
            self._forensics_warn(f"close_context {type(e).__name__}: {e}")
            return {}

    def _forensics_postmortem_schedule(self) -> None:
        """Post-mortem turunu ARKA PLANDA başlat — safety turu BEKLEMEZ.

        Düşmanca inceleme bulgusu 1 (D21-R3): tur içinde `await` edilen bir
        mum isteği yavaş/5xx bir veri host'unda ~48 sn (3 deneme × 15 sn)
        askıda kalır ve TP1→BE, trailing, kill-switch ile rezervasyon
        senkronunu geciktirir; `/health` 503'e düşer, watchdog restart eder.
        Bu yüzden ölçüm ayrı bir task'a alınır:

        * eşzamanlı EN FAZLA BİR post-mortem (task referansı kontrol edilir),
        * host geneli piyasa-verisi kesintisinde tur hiç başlatılmaz,
        * dakikada en fazla bir kez (teşhis işi, koruma işi değil).

        Bu fonksiyon SENKRONDUR ve hiçbir şeyi beklemez; hatası da motoru
        düşürmez (`_forensics_postmortem_done` istisnayı tüketir).
        """
        if not self._forensics_enabled():
            return
        window_min = float(
            getattr(self.cfg, "scalper_forensics_postmortem_min", 60.0) or 0.0
        )
        if window_min <= 0:
            return
        task = getattr(self, "_forensics_postmortem_task", None)
        if task is not None and not task.done():
            return
        if self._forensics_postmortem_blocked() is not None:
            return
        if time.monotonic() - float(
            getattr(self, "_forensics_postmortem_at", 0.0)
        ) < 60.0:
            return
        try:
            new_task = asyncio.create_task(
                self._forensics_postmortem_tick(),
                name="scalper-forensics-postmortem",
            )
        except RuntimeError:  # pragma: no cover - çalışan olay döngüsü yok
            self._forensics_postmortem_task = None
            return
        self._forensics_postmortem_task = new_task
        new_task.add_done_callback(self._forensics_postmortem_done)

    def _forensics_postmortem_done(self, task: "asyncio.Task") -> None:
        """Task bitince istisnayı TÜKET: teşhis işi motoru asla düşürmez."""
        if getattr(self, "_forensics_postmortem_task", None) is task:
            self._forensics_postmortem_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._forensics_warn(f"postmortem_task {type(exc).__name__}: {exc}")

    def _forensics_postmortem_blocked(self) -> Optional[str]:
        """Tur atlanmalı mı? (atlanmalıysa neden metni, değilse None)

        Üç bağımsız sinyal:

        1. `exits._market_data_down_reason` — BU safety turunda host geneli bir
           piyasa-verisi kesintisi görüldü (trailing zaten atlandı).
        2. `MarketDataGuard.blocked_until` — kline host'u ban/kesici altında.
           (1) yalnız AÇIK POZİSYON varken dolar; ban sırasında hiç pozisyon
           yoksa tek uyarı budur.
        3. D22: imzalı REST yolunda ağırlık geri çekilmesi aktif. Post-mortem
           SAF TEŞHİSTİR; bütçenin son kırıntısını teşhise harcamak, aynı
           dakikada bir koruma isteğini 418'e itmeye değmez.

        Kesinti sırasında teşhis isteği atmak ne veri getirir ne de ağırlık
        bütçesi bırakır: tur hiç başlatılmaz ve deneme sayacı HARCANMAZ —
        yani geçici bir ban, ölçülebilir bir kapanışı "ölçülemedi"ye çevirmez.
        """
        reason = getattr(
            getattr(self, "exits", None), "_market_data_down_reason", None
        )
        if reason:
            return str(reason)
        try:
            if ImprovedBinanceClient.weight_backoff_active():
                return (
                    "REST ağırlık geri çekilmesi aktif "
                    f"({ImprovedBinanceClient.weight_backoff_level()})"
                )
        except Exception:  # pragma: no cover - teşhis kapısı asla patlamamalı
            pass
        try:
            base_url = str(getattr(self.fetcher, "base_url", "") or "")
            if base_url:
                blocked_until = MarketDataGuard.blocked_until(host_of(base_url))
                if blocked_until and time.time() < blocked_until:
                    return f"kline host'u ban altında ({blocked_until:.0f}'a kadar)"
        except Exception:  # pragma: no cover - teşhis kapısı asla patlamamalı
            return None
        return None

    async def _forensics_postmortem_tick(self) -> None:
        """Kapanıştan N dk SONRA "fiyat girişe döndü mü" alanını doldur.

        **Look-ahead değildir:** yalnız kapanış zamanından SONRAKİ mumlara
        bakar ve sonucu kaydın AYRI `postmortem` alanına yazar; hiçbir kapı,
        boyutlama ya da çıkış kararı bu alanı okumaz.

        Maliyet: tur başına EN FAZLA BİR sembol ve dakikada en fazla bir tur;
        istek `SCALPER_TF_ENTRY` (varsayılan 5m) limit 150'dir (ağırlık 2) ve
        `asyncio.wait_for` ile 5 sn'de kesilir. Post-mortem penceresi
        `SCALPER_FORENSICS_POSTMORTEM_MIN=0` ile tamamen kapatılır.

        Bu fonksiyon `_forensics_postmortem_schedule` tarafından AYRI bir
        task'ta çalıştırılır; safety turu onu beklemez.
        """
        if not self._forensics_enabled():
            return
        window_min = float(
            getattr(self.cfg, "scalper_forensics_postmortem_min", 60.0) or 0.0
        )
        if window_min <= 0:
            return
        blocked = self._forensics_postmortem_blocked()
        if blocked is not None:
            # Sayaç HARCANMAZ ve `_forensics_postmortem_at` tazelenmez:
            # kesinti bitince ilk turda yeniden denenir.
            self.logger.debug(f"Post-mortem turu atlandı (piyasa verisi yok): {blocked}")
            return
        now = time.monotonic()
        # Dakikada bir yeter: bu bir teşhis işidir, bir koruma değil.
        if now - float(getattr(self, "_forensics_postmortem_at", 0.0)) < 60.0:
            return
        self._forensics_postmortem_at = now

        row: Optional[Dict[str, Any]] = None
        try:
            from src.strategies.scalper import forensics as fx

            candidates = await self.tracker.postmortem_candidates(
                now=datetime.utcnow(),
                min_age_minutes=window_min,
            )
            if not candidates:
                return
            row = candidates[0]           # tur başına EN FAZLA BİR sembol
            symbol = str(row.get("symbol") or "").upper()
            closed_at = row.get("closed_at")
            if not symbol or closed_at is None:
                return
            closed_ms = int(
                closed_at.replace(tzinfo=timezone.utc).timestamp() * 1000
            )
            tf_entry = str(getattr(self.cfg, "scalper_tf_entry", "5m") or "5m")
            candles = await asyncio.wait_for(
                self.fetcher.get_klines(symbol, tf_entry, 150),
                timeout=self._FORENSICS_POSTMORTEM_TIMEOUT,
            )
            postmortem = fx.postmortem_from_candles(
                entry=row.get("entry"),
                exit_=row.get("exit"),
                candles=candles,
                closed_at_ms=closed_ms,
                th=fx.thresholds_from_cfg(self.cfg),
            )
            if not postmortem.get("candles_seen"):
                # Pencereyi kapsayan mum yok (ör. sembol veri kaynağında yok):
                # boş bir kayıt yazıp konuyu kapat — sonsuz yeniden deneme yok.
                postmortem["note"] = (
                    "pencereyi kapsayan mum bulunamadı; ölçüm yapılamadı"
                )
            await self.tracker.record_postmortem(int(row["id"]), postmortem)
            self._forensics_postmortem_attempt_clear(row)
            try:
                from src.strategies.scalper import forensics_log

                forensics_log.append_soon(
                    "postmortem",
                    {
                        "trade_id": int(row["id"]),
                        "symbol": symbol,
                        "postmortem": postmortem,
                    },
                )
            except Exception as e:  # pragma: no cover - savunma
                self._forensics_warn(f"postmortem_log {type(e).__name__}: {e}")
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            # Veri host'u yavaş: tur zaten kesildi (safety turu ETKİLENMEDİ).
            # Deneme sayılır; 3'ünde de dönmezse "ölçülemedi" yazılır.
            await self._forensics_postmortem_defer(
                row,
                f"veri host'u {self._FORENSICS_POSTMORTEM_TIMEOUT:g} sn içinde "
                f"yanıt vermedi",
            )
        except MarketDataRequestError as e:
            # SEMBOL kapsamlı ve KALICI hata (ör. `-1121 Invalid symbol` —
            # ayrı market-data host'unda gerçekçidir). Her dakika yeniden
            # denemek hem boşunadır hem de kuyruğu TIKAR: aday listesi
            # kapanış zamanına göre sıralıdır, bu satır çözülene kadar
            # arkasındaki hiçbir işlem ölçülemez. "Ölçülemedi" diye işaretle
            # ve geç (bir kayıt eksiği, kilitlenmiş bir kuyruktan iyidir).
            if row is not None:
                self._forensics_postmortem_attempt_clear(row)
                await self._forensics_mark_unmeasured(
                    row, f"ölçülemedi: sembol veri kaynağında bulunamadı ({e})"
                )
        except MarketDataUnavailable as e:
            # HOST geneli (ban/bütçe): geçicidir ama sonsuz da olamaz —
            # kuyruğun BAŞINI tıkamasın diye deneme bütçesinden düşülür.
            # Teşhis işi için tek WARNING satırı bile fazladır (tarama turu
            # zaten uyarıyor).
            self.logger.debug(f"Post-mortem atlandı (piyasa verisi yok): {e}")
            await self._forensics_postmortem_defer(row, f"piyasa verisi yok ({e})")
        except Exception as e:
            await self._forensics_postmortem_defer(row, f"{type(e).__name__}: {e}")
            self._forensics_warn(f"postmortem {type(e).__name__}: {e}")

    def _forensics_postmortem_attempts_map(self) -> Dict[int, int]:
        attempts = getattr(self, "_forensics_postmortem_attempts", None)
        if not isinstance(attempts, dict):
            attempts = {}
            self._forensics_postmortem_attempts = attempts
        return attempts

    def _forensics_postmortem_attempt_clear(self, row: Optional[Dict[str, Any]]) -> None:
        if not row:
            return
        try:
            self._forensics_postmortem_attempts_map().pop(int(row.get("id") or 0), None)
        except Exception:  # pragma: no cover - savunma
            pass

    async def _forensics_postmortem_defer(
        self, row: Optional[Dict[str, Any]], note: str
    ) -> None:
        """Ölçüm başarısız: denemeyi say, bütçe dolunca "ölçülemedi" yaz.

        Sonsuz yeniden deneme YOKTUR: aynı işlem için en fazla
        `_FORENSICS_POSTMORTEM_MAX_ATTEMPTS` deneme yapılır, sonra kayda
        `note="ölçülemedi (...)"` düşülür ve satır kuyruktan çıkar.
        """
        if not row:
            return
        attempts = self._forensics_postmortem_attempts_map()
        try:
            trade_id = int(row.get("id") or 0)
        except (TypeError, ValueError):  # pragma: no cover - savunma
            return
        count = int(attempts.get(trade_id, 0)) + 1
        if len(attempts) > 256:
            # Sayaç sözlüğü sızmasın: aday penceresi zaten 12 saatliktir.
            attempts.clear()
        attempts[trade_id] = count
        if count < self._FORENSICS_POSTMORTEM_MAX_ATTEMPTS:
            self.logger.debug(
                f"Post-mortem ertelendi (#{trade_id}, deneme {count}/"
                f"{self._FORENSICS_POSTMORTEM_MAX_ATTEMPTS}): {note}"
            )
            return
        attempts.pop(trade_id, None)
        await self._forensics_mark_unmeasured(
            row, f"ölçülemedi ({count} deneme): {note}"
        )

    async def _forensics_mark_unmeasured(
        self, row: Dict[str, Any], note: str
    ) -> None:
        """Post-mortem kuyruğunu tıkamasın diye "ölçülemedi" kaydı yaz."""
        try:
            await self.tracker.record_postmortem(
                int(row["id"]),
                {"window_minutes": 0.0, "candles_seen": 0,
                 "returned_to_entry": None, "tags": [], "note": note},
            )
        except Exception as e:  # pragma: no cover - savunma
            self._forensics_warn(f"postmortem_mark {type(e).__name__}: {e}")

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

    # ------------------------------------------------------------------
    # TV olay kanalı (D19, 2026-08-23) — docs/INTEGRATIONS.md §7
    # ------------------------------------------------------------------
    # `/tv-signal` gövdesinde `kind=exit|choch|trend|tp1` taşıyan istekler
    # sağlamaya HİÇ girmez; `src/services/tv_events.py` defterine yazılır.
    # Motor o defteri İKİ yerde okur:
    #   1) `_evaluate_symbol` — giriş kapısı (rejim kapısının hemen yanında,
    #      TEK giriş noktası: C stratejisi VE TV dış sinyali aynı yerden geçer)
    #   2) `_safety_tick` — açık pozisyonda BE/kapanış tetikleyicisi
    # `SCALPER_TV_EVENTS_MODE` üç kademelidir; `shadow` (varsayılan) hiçbir
    # emri/stopu DEĞİŞTİRMEZ, yalnız "ne olurdu"yu loglar ve sayar.
    # FAIL-OPEN İLKESİ: bu kanalın KENDİ hatası (bozuk defter, beklenmeyen
    # istisna) asla girişleri durdurmaz ve asla pozisyon kapatmaz — risk
    # kapıları (risk-event halt, kill switch, entry latch) fail-CLOSED'dır,
    # bu ise bir SİNYAL kanalıdır: veri yoksa bugünkü davranış aynen sürer.

    def _tv_events_mode(self) -> str:
        mode = str(
            getattr(self.cfg, "scalper_tv_events_mode", "shadow") or "shadow"
        ).strip().lower()
        return mode if mode in ("off", "shadow", "active") else "shadow"

    def _tv_events_exit_action(self) -> str:
        action = str(
            getattr(self.cfg, "scalper_tv_events_exit", "be") or "be"
        ).strip().lower()
        return action if action in ("off", "be", "close") else "be"

    def _tv_events_exit_losing(self) -> str:
        """Pozisyon ZARARDAYKEN çıkış olayı gelirse (D19a bulgu B): skip|close."""
        action = str(
            getattr(self.cfg, "scalper_tv_events_exit_losing", "skip") or "skip"
        ).strip().lower()
        return action if action in ("skip", "close") else "skip"

    def _tv_ledger(self) -> Optional[Any]:
        """Olay defteri (yoksa None).

        `getattr(..., None)`: repo konvansiyonu — `ScalperEngine.__new__` ile
        kurulan eski test çiftlerinde bu alan bulunmayabilir; olmayan defter
        "olay yok" demektir, hata değil (fail-open ilkesi).
        """
        return getattr(self, "tv_events", None)

    def _tv_note(self, counter: str) -> None:
        """Telemetri sayacı — defter yoksa/hata verirse akışı bozma."""
        ledger = self._tv_ledger()
        if ledger is None:
            return
        try:
            ledger.note(counter)
        except Exception:
            pass

    def _count_engine_reject(self, reason: str) -> None:
        """Kapı ret sayacını executor'ın sayaç sözlüğüne yaz (geriye uyumlu).

        `/scalper/status` → `entry_rejects` altında görünür; "bot healthy
        ama hiç işlem açmıyor" teşhisinde HANGİ kapının reddettiğini söyler.
        """
        counter = getattr(self.executor, "_count_reject", None)
        if not callable(counter):
            return
        try:
            counter(reason)
        except Exception as e:  # sayaç asla akışı bozmasın
            self.logger.debug(f"ret sayacı yazılamadı ({reason}): {e}")

    def _tv_structure_verdict(
        self, symbol: str, ledger: Any, *, now: Optional[float] = None
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """(BULL|BEAR|MIXED|NONE, taze satırlar) — eski deftere de dayanıklı."""
        verdict_fn = getattr(ledger, "structure_verdict", None)
        if callable(verdict_fn):
            return verdict_fn(symbol, now=now)
        rows = ledger.fresh_gate_structures(symbol, now=now)
        values = {r.get("structure") for r in rows}
        if not values:
            return "NONE", rows
        if len(values) == 1:
            return next(iter(values)), rows
        return "MIXED", rows

    def _tv_structure_block(
        self, symbol: str, direction: Any
    ) -> Optional[Dict[str, Any]]:
        """Sinyale TERS ve TAZE bir TV yapı durumu varsa onu döndür.

        Yalnız `SCALPER_TV_EVENTS_GATE_SOURCES` içindeki kaynaklar sayılır.

        ÇELİŞKİ (MIXED) → KAPI UYGULANMAZ (D19a bulgu F). Eski davranışta
        "herhangi bir ters kaynak engeller" kuralı, PAC BULL + S&O trend BEAR
        gibi bir çelişkide sembolü İKİ YÖNE DE `max_age` (240 dk) boyunca
        kilitliyordu — yani hiçbir kanıt üretmeyen bir durum, en sert kararı
        veriyordu. Çelişki "bilinmiyor" demektir ve olay kanalı fail-open'dır:
        bilinmiyorsa bugünkü davranış sürer, yalnız telemetriye yazılır.
        """
        ledger = self._tv_ledger()
        if ledger is None or self._tv_events_mode() == "off":
            return None
        yon = getattr(direction, "value", str(direction))
        opposing = "BEAR" if yon == "LONG" else "BULL"
        try:
            verdict, rows = self._tv_structure_verdict(symbol, ledger)
        except Exception as e:
            self.logger.error(f"⚠️ {symbol}: TV yapı durumu okunamadı ({e}); kapı atlandı")
            return None
        if verdict == "MIXED":
            self._tv_note("mixed_skipped")
            sources = ",".join(sorted(str(r.get("source")) for r in rows))
            self.logger.info(
                f"🤷 {symbol}: TV kapı kaynakları ÇELİŞİYOR ({sources}); kapı "
                f"UYGULANMADI — çelişki 'bilinmiyor'dur, 'her iki yön de yasak' değil"
            )
            return None
        if verdict != opposing:
            return None
        for row in rows:
            if row.get("structure") == opposing:
                return row
        return None

    def _tv_structure_gate_blocks(self, symbol: str, direction: Any) -> bool:
        """Giriş kapısı: `active` modda True dönerse sinyal AÇILMAZ.

        Sayaç sözleşmesi (D19a bulgu G8): `gate_hits` HER İKİ modda da
        artar; `would_block` (shadow) ile `blocked` (active) aynı olayın
        mod-bazlı ayrımıdır. Böylece gölge ölçümü ile aktif ölçüm birebir
        karşılaştırılabilir (`gate_hits == would_block + blocked`).
        """
        row = self._tv_structure_block(symbol, direction)
        if row is None:
            return False
        mode = self._tv_events_mode()
        yon = getattr(direction, "value", str(direction))
        detail = (
            f"{row.get('structure')} yapısı ← {row.get('source')} "
            f"({row.get('kind')}, {float(row.get('age_s') or 0.0):.0f}s)"
        )
        self._tv_note("gate_hits")
        if mode == "active":
            self._count_engine_reject("tv_structure_gate")
            self._tv_note("blocked")
            self.logger.info(
                f"⛔ {symbol}: TV yapı kapısı — {detail}; ters yönlü {yon} "
                f"girişi engellendi (SCALPER_TV_EVENTS_MODE=active)"
            )
            return True
        self._tv_note("would_block")
        self.logger.info(
            f"👻 {symbol}: TV yapı kapısı GÖLGE — {detail}; aktif olsaydı {yon} "
            f"girişi engellenecekti (davranış DEĞİŞMEDİ)"
        )
        return False

    @staticmethod
    def _position_opened_epoch(sp: Any) -> Optional[float]:
        """Pozisyonun açılış anı (epoch). Çözülemezse None."""
        opened = getattr(getattr(sp, "position", None), "opened_at", None)
        if opened is None:
            return None
        try:
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            return float(opened.timestamp())
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Tüketim imleçleri — KALICI (D19a bulgu D)
    # ------------------------------------------------------------------
    # Eski davranışta imleçler yalnız RAM'deydi, defter ise diskteydi: her
    # restart, TÜKETİLMİŞ bir çıkış olayını yeniden tetikliyordu (240 dk
    # penceresi boyunca her restartta bir kez daha). İmleç artık defterle
    # AYNI dosyada atomik olarak tutulur. RAM sözlükleri yalnız eski test
    # çiftleri (defterinde `consumed_seq` olmayan) için ayna olarak kalır.

    def _tv_consumed(self, symbol: str) -> Dict[str, int]:
        ledger = self._tv_ledger()
        getter = getattr(ledger, "consumed_seq", None)
        if callable(getter):
            try:
                row = getter(symbol) or {}
                return {
                    "exit": int(row.get("exit") or 0),
                    "structure": int(row.get("structure") or 0),
                }
            except Exception as e:
                self.logger.error(f"⚠️ {symbol}: TV tüketim imleci okunamadı ({e})")
        if not hasattr(self, "_tv_exit_seen"):
            self._tv_exit_seen = {}
        if not hasattr(self, "_tv_struct_seen"):
            self._tv_struct_seen = {}
        return {
            "exit": int(self._tv_exit_seen.get(symbol, 0)),
            "structure": int(self._tv_struct_seen.get(symbol, 0)),
        }

    def _tv_mark_consumed(self, symbol: str, group: str, seq: int) -> None:
        """Olayı 'uygulandı/gereksiz' say — bir daha tetiklemesin (kalıcı)."""
        seq = int(seq or 0)
        if not hasattr(self, "_tv_exit_seen"):
            self._tv_exit_seen = {}
        if not hasattr(self, "_tv_struct_seen"):
            self._tv_struct_seen = {}
        mirror = self._tv_exit_seen if group == "exit" else self._tv_struct_seen
        mirror[symbol] = max(mirror.get(symbol, 0), seq)
        ledger = self._tv_ledger()
        marker = getattr(ledger, "mark_consumed", None)
        if callable(marker):
            try:
                marker(symbol, group, seq)
            except Exception as e:
                self.logger.error(
                    f"⚠️ {symbol}: TV tüketim imleci yazılamadı ({e}); restart'ta "
                    "aynı olay bir kez daha değerlendirilebilir"
                )

    def _advance_tv_seen(self) -> None:
        """Olay imleçlerini en son sıraya taşı (aksiyon almadan tüket).

        `off` modda ya da `SCALPER_TV_EVENTS_EXIT=off` iken çağrılır: kanal
        sonradan açıldığında geçmiş olayların TOPLU tetiklenmesini önler.
        """
        ledger = self._tv_ledger()
        if ledger is None:
            return
        try:
            symbols = ledger.symbols()
        except Exception:
            return
        for symbol in symbols:
            try:
                seqs = ledger.latest_seq(symbol)
            except Exception:
                continue
            self._tv_mark_consumed(symbol, "exit", int(seqs.get("exit") or 0))
            self._tv_mark_consumed(symbol, "structure", int(seqs.get("structure") or 0))

    def _tv_exit_event_for(
        self, symbol: str, sp: Any
    ) -> Tuple[Optional[Dict[str, Any]], List[Tuple[str, int]]]:
        """Açık pozisyona uygulanacak olay + KOŞULSUZ tüketilecek imleçler.

        Dönüş: `(event | None, [(grup, seq), ...])`. İkinci liste
        "değerlendirildi, aksiyon GEREKTİRMİYOR" olaylarıdır (yön uyuşmadı,
        pozisyondan eski, aynı yönlü yapı, çelişkili yapı) — bunlar hemen
        tüketilir. Aksiyon gerektiren olay ise ÇAĞIRAN tarafından, YALNIZ
        AKSİYON BAŞARILI OLURSA tüketilir (D19a bulgu D: başarısız aksiyon
        olayı yutmamalı).

        İki kaynak:
          * `exit`/`tp1` — LuxAlgo S&O "Exit Signal" ve AlgoPro "🎯 TP1 Hit"
            koşulları YÖNSÜZDÜR: yön yoksa sembolde ne varsa ona uygulanır;
            yön VARSA ve açık pozisyonla uyuşmuyorsa UYGULANMAZ + loglanır.
          * `choch`/`trend` — yalnız kapı kaynaklarından ve yalnız pozisyona
            TERS yönde (BULL yapı + SHORT pozisyon gibi). Kaynaklar
            ÇELİŞİYORSA (MIXED) hiçbir şey yapılmaz (D19a bulgu F).

        POZİSYON AÇILIŞINDAN ÖNCEKİ olaylar sayılmaz: aksi halde 3 saat
        önce gelmiş bir "exit" alarmı, yeni açılan pozisyonu doğduğu anda
        kapatırdı.
        """
        now = time.time()
        consume: List[Tuple[str, int]] = []
        ledger = self._tv_ledger()
        if ledger is None:
            return None, consume

        opened_ts = self._position_opened_epoch(sp)
        pos_dir = getattr(getattr(sp, "signal", None), "direction", None)
        pos_dir = getattr(pos_dir, "value", str(pos_dir)) if pos_dir else ""
        opposing = "BEAR" if pos_dir == "LONG" else "BULL"
        seen = self._tv_consumed(symbol)

        row = ledger.pending_exit(symbol, now=now)
        if row is not None:
            seq = int(row.get("seq") or 0)
            ts = float(row.get("ts") or 0.0)
            if seq > seen["exit"]:
                event_dir = row.get("direction")
                if opened_ts is not None and ts < opened_ts:
                    consume.append(("exit", seq))
                elif event_dir and event_dir != pos_dir:
                    consume.append(("exit", seq))
                    self.logger.info(
                        f"🧭 {symbol}: TV {row.get('kind')} olayı {event_dir} "
                        f"pozisyon içindi (← {row.get('source')}); açık pozisyon "
                        f"{pos_dir} — uygulanmadı"
                    )
                else:
                    return (
                        {
                            "kind": row.get("kind"),
                            "source": row.get("source"),
                            "direction": event_dir,
                            "group": "exit",
                            "seq": seq,
                            "detail": f"{row.get('kind')} ← {row.get('source')}",
                        },
                        consume,
                    )

        verdict, rows = self._tv_structure_verdict(symbol, ledger, now=now)
        mixed_noted = False
        for struct_row in rows:
            seq = int(struct_row.get("seq") or 0)
            if seq <= seen["structure"]:
                continue
            ts = now - float(struct_row.get("age_s") or 0.0)
            if opened_ts is not None and ts < opened_ts:
                consume.append(("structure", seq))
                continue
            if verdict == "MIXED":
                consume.append(("structure", seq))
                if not mixed_noted:
                    mixed_noted = True
                    self._tv_note("mixed_skipped")
                    self.logger.info(
                        f"🤷 {symbol}: TV yapı kaynakları ÇELİŞİYOR; çıkış tetiği "
                        f"UYGULANMADI (çelişki 'bilinmiyor'dur)"
                    )
                continue
            if struct_row.get("structure") == opposing:
                return (
                    {
                        "kind": struct_row.get("kind"),
                        "source": struct_row.get("source"),
                        "direction": None,
                        "group": "structure",
                        "seq": seq,
                        "detail": (
                            f"{struct_row.get('structure')} yapısı "
                            f"({struct_row.get('kind')} ← {struct_row.get('source')})"
                        ),
                    },
                    consume,
                )
            consume.append(("structure", seq))
        return None, consume

    async def _apply_tv_event_exits(self) -> None:
        """Safety turunda TV olaylarını açık pozisyonlara uygula (D19).

        `be`    → `ExitManager.force_breakeven` (MEVCUT BE mekanizması,
                  `pm.replace_stop_loss` boşluksuz deseni; yeni emir yolu YOK).
                  **Yalnız pozisyon KÂRDAYKEN** (D19a bulgu B): zararda BE
                  stopu piyasanın ters tarafına koymak → Binance -2021 →
                  `_emergency_close`. Zarardaki pozisyonun kaderi
                  `SCALPER_TV_EVENTS_EXIT_LOSING` (skip | close) kararıdır.
        `close` → `_close_position_market` (reaper/risk-olayı flatten ile
                  AYNI reduce-only MARKET çağrısı, `force_fresh` doğrulaması,
                  `_closing` tek-finalizer kilidi) + `exit_reason="TV_EVENT"`

        TUR BAŞINA EN FAZLA `_TV_EXIT_MAX_ACTIONS_PER_TICK` aksiyon (D19a
        bulgu G6): reaper'ın 2026-08-14 dersiyle aynı — çoklu eşzamanlı
        kapanış safety turunu şişirip watchdog restart'ı tetikliyordu.
        Kalan olaylar tüketilmediği için bir sonraki turda ele alınır.
        """
        mode = self._tv_events_mode()
        action = self._tv_events_exit_action()
        ledger = self._tv_ledger()
        if ledger is None:
            return
        # Açık pozisyonu olan semboller defter budamasından MUAF (D19a-2):
        # `_MAX_SYMBOLS` eviction'ı, bekleyen bir çıkış olayını ve tüketim
        # imlecini taşıyan AKTİF sembolü bir alarm selinde düşürebiliyordu.
        self._tv_protect_tracked()
        # `mode/action = off` VE "pencere kapalı" (SIFIR/BOŞ = KAPALI) aynı
        # kapıdan geçer: imleçler yine de ilerletilir ki ayar sonradan
        # açıldığında birikmiş olaylar TOPLU tetiklemesin (INTEGRATIONS §7.4).
        if mode == "off" or action == "off" or not self._tv_window_open():
            self._advance_tv_seen()
            return

        applied = 0
        for symbol in sorted(self.exits.tracked_symbols()):
            if applied >= self._TV_EXIT_MAX_ACTIONS_PER_TICK:
                break
            sp = self.exits._positions.get(symbol)
            if sp is None:
                continue
            try:
                event, consume = self._tv_exit_event_for(symbol, sp)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(
                    f"❌ {symbol}: TV olay çıkış değerlendirmesi hata verdi ({e})",
                    exc_info=True,
                )
                continue
            for group, seq in consume:
                self._tv_mark_consumed(symbol, group, seq)
            if event is None:
                continue

            self._tv_note("exit_hits")
            if mode == "shadow":
                self._tv_note("would_exit")
                # Gölge, AKTİFTE OLACAĞI ŞEYİ tahmin etmelidir (D19a-2):
                # `be` + zararda/bilinmiyor + `skip` = HİÇBİR ŞEY. Bunu
                # ayırmazsak gölge sayacı terfi kararını şişirir.
                noop = (
                    action == "be"
                    and not self._tv_breakeven_would_act(symbol)
                    and self._tv_events_exit_losing() != "close"
                )
                if noop:
                    self._tv_note("would_exit_noop")
                self._tv_mark_consumed(symbol, event["group"], event["seq"])
                self.logger.info(
                    f"👻 {symbol}: TV olay çıkışı GÖLGE — {event['detail']}; aktif "
                    f"olsaydı "
                    + (
                        "HİÇBİR ŞEY olmazdı (borsaya emir GİTMEZDİ: stop zaten "
                        "en az BE kadar koruyucu ya da pozisyon kârda değil)"
                        if noop
                        else f"'{action}' uygulanacaktı"
                    )
                    + " (emir/stop DEĞİŞMEDİ)",
                    extra={"trade": True},
                )
                continue

            self._tv_note("exits_attempted")
            applied += 1
            try:
                status = await self._apply_tv_exit_action(symbol, sp, event, action)
            except asyncio.CancelledError:
                raise
            except UnprotectedPositionError as e:
                # D19a bulgu C — KORUMASIZ POZİSYON YUTULMAZ. Eski kodda bu
                # istisna genel `except Exception`'a düşüyor, yalnız
                # loglanıyordu: safety yolunun her diğer dalında entry-halt
                # latch'ini tetikleyen olay, TV dalında sessiz kalıyordu.
                self._tv_note("exits_failed")
                self._tv_mark_consumed(symbol, event["group"], event["seq"])
                await self._latch_entry_halt(e, source="TV olay çıkışı")
                continue
            except Exception as e:
                # Bu kanal bir SİNYAL kanalıdır: kendi hatası koruma
                # döngüsünü (exits.step / kill switch) düşürmemeli.
                self.logger.error(
                    f"❌ {symbol}: TV olay çıkışı uygulanamadı ({e})", exc_info=True
                )
                status = "failed"

            if status in ("applied", "noop"):
                # `noop` = olay ele alındı ama BORSAYA HİÇBİR İSTEK GİTMEDİ
                # (zararda `skip`). Ayrı sayılır: `exits_applied` terfi
                # kararının girdisidir, dokunulmamış pozisyonu saymamalı.
                self._tv_note(
                    "exits_applied" if status == "applied" else "exits_noop"
                )
                self._tv_mark_consumed(symbol, event["group"], event["seq"])
                continue

            # Başarısız aksiyon olayı TÜKETMEZ: sonraki turda yeniden
            # denenir (olay `max_age` içinde kaldığı sürece). Sonsuz döngü
            # olmasın diye deneme sayısı sınırlıdır.
            self._tv_note("exits_failed")
            attempts = self._tv_note_attempt(symbol, event["group"], event["seq"])
            limit = self._tv_max_attempts()
            if attempts >= limit:
                self._tv_mark_consumed(symbol, event["group"], event["seq"])
                self.logger.warning(
                    f"⚠️ {symbol}: TV olay çıkışı {attempts} denemede uygulanamadı "
                    f"({event['detail']}); olay bırakıldı — pozisyon normal "
                    f"SL/TP/trailing korumasında kalıyor"
                )
            else:
                self.logger.warning(
                    f"⚠️ {symbol}: TV olay çıkışı uygulanamadı ({event['detail']}); "
                    f"deneme {attempts}/{limit} — sonraki safety turunda yeniden denenecek"
                )

    def _tv_max_attempts(self) -> int:
        ledger = self._tv_ledger()
        getter = getattr(ledger, "max_attempts", None)
        if callable(getter):
            try:
                return max(1, int(getter()))
            except Exception:
                pass
        return 3

    def _tv_note_attempt(self, symbol: str, group: str, seq: int) -> int:
        ledger = self._tv_ledger()
        noter = getattr(ledger, "note_attempt", None)
        if callable(noter):
            try:
                return int(noter(symbol, group, seq))
            except Exception:
                pass
        key = (symbol, group, int(seq or 0))
        if not hasattr(self, "_tv_attempts"):
            self._tv_attempts = {}
        self._tv_attempts[key] = self._tv_attempts.get(key, 0) + 1
        return self._tv_attempts[key]

    async def _apply_tv_exit_action(
        self, symbol: str, sp: Any, event: Dict[str, Any], action: str
    ) -> str:
        """Tek bir TV olayına karşılık gelen aksiyonu uygula.

        Dönüş (D19a-2, üç durum — `bool` yetmiyordu):
          * `"applied"` → borsaya gerçek bir istek gitti ve başarılı oldu
            (stop taşındı ya da pozisyon kapandı). Olay tüketilir.
          * `"noop"`    → olay ELE ALINDI ama hiçbir istek gitmedi (zararda
            `skip`, ya da stop zaten BE'de/daha koruyucu). Olay tüketilir
            (aksi halde her safety turunda yeniden loglanırdı) ama
            `exits_applied` sayacını KİRLETMEZ.
          * `"failed"`  → geçici hata / bilinmeyen durum. Olay TÜKETİLMEZ,
            `_MAX_EXIT_ATTEMPTS`'e kadar yeniden denenir.

        SIRALAMA ÖNEMLİ (D19a-2 bulgu 1): `be` dalında ÖNCE
        `force_breakeven` çağrılır. O fonksiyon kendi içinde `_closing`
        kilidini, hedef geçerliliğini, "stop zaten BE'de veya daha koruyucu"
        erken dönüşünü VE zarar kontrolünü sırasıyla uygular; zararda hiçbir
        emir GÖNDERMEZ. Zarar kontrolünü ÖNE almak, stopu zaten BE'de olan
        (TP1 dolmuş, D4 reaper muafiyetindeki) bir koşucuyu fiyat geri
        çekildiğinde `EXIT_LOSING=close` ile piyasadan kapattırıyordu.
        """
        if action == "be":
            before = self._current_stop(sp)
            moved = await self.exits.force_breakeven(
                symbol, reason=f"TV olayı: {event['detail']}"
            )
            if moved:
                # `force_breakeven` "zaten en az BE kadar koruyucu" durumunda
                # da True döner — o durumda BORSAYA HİÇBİR İSTEK GİTMEZ.
                # `exits_applied` terfi kararının girdisidir; stop gerçekten
                # değiştiyse "applied", değişmediyse "noop" (D19a-2 R2-3).
                return "applied" if self._current_stop(sp) != before else "noop"
            side_ok = self._tv_breakeven_side_ok(symbol)
            if side_ok is not True:
                return await self._handle_losing_tv_exit(symbol, sp, event, side_ok)
            # BE piyasanın doğru tarafında ama emir gitmedi → geçici hata.
            return "failed"

        closed = await self._close_position_market(symbol, sp, exit_reason="TV_EVENT")
        if closed:
            self.logger.info(
                f"🧭 {symbol}: TV olayı ({event['detail']}) ile "
                f"reduce-only kapanış doğrulandı (exit_reason=TV_EVENT)",
                extra={"trade": True},
            )
            return "applied"
        self.logger.warning(
            f"⚠️ {symbol}: TV olay kapanışı borsada DOĞRULANAMADI; "
            f"SL/TP dokunulmadı, izleme sürüyor"
        )
        return "failed"

    def _tv_window_open(self) -> bool:
        """Tazelik penceresi açık mı (eski test çiftlerine dayanıklı)."""
        ledger = self._tv_ledger()
        getter = getattr(ledger, "window_open", None)
        if callable(getter):
            try:
                return bool(getter())
            except Exception:
                return True
        return True

    def _tv_protect_tracked(self) -> None:
        """Açık pozisyonlu sembolleri defter budamasından muaf tut (D19a-2)."""
        ledger = self._tv_ledger()
        protector = getattr(ledger, "protect", None)
        if not callable(protector):
            return
        try:
            protector(self.exits.tracked_symbols())
        except Exception as e:  # koruma listesi asla akışı bozmasın
            self.logger.debug(f"TV defteri koruma listesi yazılamadı: {e}")

    def _tv_breakeven_side_ok(self, symbol: str) -> Optional[bool]:
        """BE hedefi piyasanın koruyucu tarafında mı (True/False/bilinmiyor)."""
        checker = getattr(self.exits, "breakeven_side_ok", None)
        if not callable(checker):
            return None
        try:
            return checker(symbol)
        except Exception as e:
            self.logger.error(f"⚠️ {symbol}: BE tarafı okunamadı ({e})")
            return None

    def _tv_breakeven_would_act(self, symbol: str) -> bool:
        """Gölge tahmini: `be` aksiyonu borsaya emir gönderir miydi?"""
        checker = getattr(self.exits, "breakeven_would_act", None)
        if not callable(checker):
            # Eski test çifti: tahmin edemiyoruz → "aksiyon olurdu" varsay
            # (gölge sayacı ihtiyatlı yönde şişer, sessizce eksilmez).
            return True
        try:
            return bool(checker(symbol))
        except Exception as e:
            self.logger.error(f"⚠️ {symbol}: BE tahmini okunamadı ({e})")
            return True

    @staticmethod
    def _current_stop(sp: Any) -> Any:
        return getattr(getattr(sp, "position", None), "current_stoploss", None)

    async def _handle_losing_tv_exit(
        self, symbol: str, sp: Any, event: Dict[str, Any], side_ok: Optional[bool]
    ) -> str:
        """ZARARDA (ya da belirsiz) pozisyonda `be` aksiyonu — D19a bulgu B.

        `skip`  (varsayılan) → hiçbir şey; logla + say. Pozisyon kendi
                 SL/TP/trailing korumasında kalır. Dönüş `"noop"`.
        `close` → reduce-only MARKET kapanış (bilinçli karar; `be`nin
                 "geri alınabilir" vaadi zararda GEÇERSİZDİR).

        `side_ok is None` (fiyat okunamadı / BAYAT) durumunda `close` bile
        UYGULANMAZ ve dönüş **`"failed"`**tir (D19a-2 bulgu 2): "bilinmiyor"
        "ele alındı" DEĞİLDİR — geçici bir ticker hatası, olayı kalıcı olarak
        yutmamalı. Sonraki turlarda `_MAX_EXIT_ATTEMPTS` kadar yeniden denenir.
        """
        state = "zararda" if side_ok is False else "belirsiz"
        policy = self._tv_events_exit_losing()
        if side_ok is None:
            self.logger.warning(
                f"⚠️ {symbol}: TV olayı ({event['detail']}) — pozisyonun kâr/zarar "
                f"durumu BİLİNMİYOR (fiyat okunamadı ya da bayat); hiçbir emir "
                f"gönderilmedi, olay sonraki safety turunda yeniden denenecek"
            )
            return "failed"
        if side_ok is False and policy == "close":
            self._tv_note("exits_closed_losing")
            self.logger.warning(
                f"🧭 {symbol}: TV olayı ({event['detail']}) — pozisyon {state}, "
                f"BE uygulanamaz; SCALPER_TV_EVENTS_EXIT_LOSING=close ile "
                f"reduce-only kapanış deneniyor",
                extra={"trade": True},
            )
            closed = await self._close_position_market(
                symbol, sp, exit_reason="TV_EVENT"
            )
            if not closed:
                self.logger.warning(
                    f"⚠️ {symbol}: TV olay kapanışı borsada DOĞRULANAMADI; "
                    f"SL/TP dokunulmadı, izleme sürüyor"
                )
            return "applied" if closed else "failed"

        self._tv_note("exits_skipped_losing")
        self.logger.info(
            f"🛑 {symbol}: TV olayı ({event['detail']}) — pozisyon {state}; BE'ye "
            f"çekmek stopu piyasanın TERS tarafına koyardı (-2021 → acil kapanış), "
            f"UYGULANMADI (SCALPER_TV_EVENTS_EXIT_LOSING={policy})",
            extra={"trade": True},
        )
        return "noop"

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

    async def external_signal(
        self,
        symbol: str,
        direction: Direction,
        *,
        tv_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """TradingView webhook köprüsü: dış sinyali normal giriş hattına sok.

        Dönen sözlük teşhis içindir; kabul edilen sinyal bile risk
        kapılarında reddedilebilir — kesin sonuç log + DB'dedir.

        `tv_meta` (D21): hangi TV kaynakları oy verdi / sağlama penceresi —
        YALNIZ adli kayıt içindir, hiçbir kapıya girmez.
        """
        symbol = str(symbol).upper()
        if not self.running:
            return {"accepted": False, "reason": "scalper çalışmıyor"}
        # D20b: takipçiye AYRILMIŞ sembol TV oyu da ALAMAZ. `.env`'deki
        # `SCALPER_TV_SYMBOL_ALLOWLIST` unutulsa bile ana sistem o coini
        # GÖRMEZ (kullanıcı kararı 2026-08-23); allowlist'ten BAĞIMSIZ kapı.
        if symbol in self._follower_reserved_symbols():
            self.logger.info(
                f"🚫 TV sinyali reddedildi: {symbol} — AlgoPro takipçisine "
                f"ayrılmış sembol (FOLLOWER_SYMBOLS)"
            )
            return {"accepted": False, "reason": "takipçiye ayrılmış sembol"}
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
            # `external_meta` YALNIZ dolu olduğunda geçilir: `_evaluate_symbol`
            # yerine iki-argümanlı bir çift koyan test/entegrasyon kurulumları
            # (ör. tests/test_market_data_source.py) bu yolla bozulmaz.
            if tv_meta:
                await self._evaluate_symbol(
                    symbol,
                    [_ExternalSignalStrategy(direction)],
                    external_meta=tv_meta,
                )
            else:
                await self._evaluate_symbol(
                    symbol, [_ExternalSignalStrategy(direction)]
                )
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

    #: `_evaluate_symbol`in giriş diliminde çektiği mum sayısı. Karşı-olgu
    #: penceresi BUDUR (yeni REST ağırlığı sıfır sözleşmesi).
    COUNTERFACTUAL_CANDLE_COUNT = 150

    _TF_SECONDS: Dict[str, float] = {
        "1m": 60.0, "3m": 180.0, "5m": 300.0, "15m": 900.0, "30m": 1800.0,
        "1h": 3600.0, "2h": 7200.0, "4h": 14400.0,
    }

    def _warn_counterfactual_horizon_fit(self) -> None:
        """D27/D28: ufuk tek tarama penceresinden büyükse uptime riskini bildir.

        Karşı-olgu çözümü tarama turunun ZATEN çektiği ~150 giriş-dilimi
        mumuyla yapılır. 5m dilimde bu ≈12.5 saattir ve en büyük varsayılan
        ufku (8 sa) tek turda kapsar. **Hızlı profilde (1m) tek pencere 2.5
        saate düşer**, fakat D28 rolling mum tamponu örtüşen turları bekleyen
        niyet boyunca birleştirerek ufku kapsar. Operasyonel sınır şudur:
        kuyruk ve tampon süreç-içidir; ufuk dolmadan restart olursa o ölçüm
        kohortu sıfırlanır. Ufku küçültmek gerekmez.

        Yalnız UYARIR: bir teşhis kaydı motoru başlatmayı engellememeli.
        """
        try:
            if not counterfactual_store.enabled():
                return
            tf = str(getattr(self.cfg, "scalper_tf_entry", "5m") or "5m")
            step = self._TF_SECONDS.get(tf)
            if not step:
                return
            window_h = self.COUNTERFACTUAL_CANDLE_COUNT * step / 3600.0
            horizons = counterfactual_store.horizons()
            largest = max(horizons) if horizons else 0.0
            if largest >= window_h:
                self.logger.warning(
                    f"⚠️ Karşı-olgu ufku ({largest:g} sa) giriş dilimi mum "
                    f"tek-tarama penceresinden ({tf} × "
                    f"{self.COUNTERFACTUAL_CANDLE_COUNT} = {window_h:.1f} sa) "
                    f"BÜYÜK — rolling mum tamponu ufku süreç içinde "
                    f"biriktirecek. Tam ölçüm için süreç en az {largest:g} sa "
                    f"kesintisiz çalışmalı; restart bekleyen kayıtları ve "
                    f"tamponu sıfırlar. Ufku küçültmek gerekmez."
                )
        except Exception:  # pragma: no cover - teşhis uyarısı motoru düşürmez
            return

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

    def _executor_order_health_snapshot(self) -> Dict[str, Any]:
        """D27/A4: TP emri konulamama sayaçları (geriye uyumlu okuma).

        Eski/çıplak executor çiftlerinde alan olmayabilir; teşhis bloğu
        `/scalper/status`'u ASLA düşürmemeli.

        D27 incelemesi (O8): arıza hâlinde ARTIK `{}` DÖNMEZ. Boş sözlük
        panoda "0" gibi okunuyordu, yani "TP sorunu yok" — oysa bu blok tam
        da SESSİZ KALMAMAK için yazıldı. Takipçi tarafı
        (`follower/engine._order_health_snapshot`) aynı tuzağa karşı iki ayrı
        `try` kullanıyor; burada eşdeğeri, hatayı GÖRÜNÜR kılmaktır.
        """
        snapshotter = getattr(self.executor, "order_health_snapshot", None)
        if not callable(snapshotter):
            return {
                "error": "executor.order_health_snapshot YOK",
                "window": "process_start",
            }
        try:
            return dict(snapshotter())
        except Exception as e:
            self.logger.error(f"Scalper order_health snapshot okunamadı: {e}")
            return {
                "error": f"{type(e).__name__}: {e}",
                "window": "process_start",
            }

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
            # Yalnız BİLGİ: gömülü modda hesabın ham günlük income'ı (kesici
            # bunu KULLANMAZ). None = okunamadı ya da gömülü mod kapalı.
            "daily_income_account": getattr(self, "_daily_income_account", None),
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
        cache = getattr(self, "_market_gate_cache", None)
        if cache is None:  # __init__ atlanmış (test/araç) — tembel başlat
            cache = self._market_gate_cache = {}
        cached = cache.get(leader)
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
        # D22: BAYAT görüntünün İKİ farklı nedeni vardır ve ayırt edilmezse
        # yanlış teşhis üretir. Giriş kapalıyken (`kill_switch`/`entry_halt`/
        # `risk_event`/borsa hazır değil) `_scan_tick` lideri hiç tazelemez —
        # kapı "bozuk" değil, tarama durmuştur.
        stale_reason: Optional[str] = None
        if stale:
            stale_reason = (
                "entries_blocked" if self.entries_blocked_by() else "leader_stale"
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
            # "entries_blocked" (tarama duruyor) | "leader_stale" (veri gelmiyor)
            "stale_reason": stale_reason,
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

        # D20b (düşmanca inceleme, KRİTİK): gömülü modda hesap PAYLAŞILIR ve
        # `/fapi/v1/income` İKİ defteri birlikte raporlar. Eski çözüm income'dan
        # AP'yi düşmekti; ama income 120 sn önbelleklidir ve AP kapanışları
        # scalper'ın `close_seq`'ini artırmadığı için düzeltme çağrıların
        # ~%98'inde ATLANIYORDU (kill switch bir LATCH'tir: tek kirli okuma
        # scalper'ın tüm gününü kapatabilir). Ayrıca AP merdiveninin KISMİ TP
        # dolumları hiç defter satırı yazmadığı için income'da düzeltmesiz
        # kalıyor ve eşiği GEVŞETİYORDU.
        # Kökten çözüm: gömülü modda her motor KENDİ DEFTERİNDEN beslenir.
        # Bunun bilinçli bedeli: kısmi TP dolumları (iki motorda da) gün
        # içinde sayılmaz, PnL yalnız KAPANAN işlemlerden oluşur.
        # `FOLLOWER_EMBEDDED=false` → income yolu birebir korunur.
        embedded = bool(getattr(self.cfg, "follower_embedded", False))
        try:
            if embedded:
                pnl = await self._ledger_daily_pnl(today)
            else:
                pnl = await self._get_account_daily_net_income(today)
        except Exception as e:
            self._risk_ready = False
            self._daily_pnl_source = "unavailable"
            self.logger.error(
                f"❌ Net günlük PNL doğrulanamadı; yeni girişler fail-closed kapalı: {e}"
            )
            return
        self._daily_pnl = pnl
        self._daily_pnl_source = (
            "scalper_ledger" if embedded else "binance_account_income"
        )
        self._risk_ready = True

        # BİLGİ AMAÇLI (davranış DEĞİŞMEZ, doğrulayıcı bulgusu Y8): gömülü modda
        # kesici defterden beslenir ve AÇIK pozisyonların FUNDING_FEE/COMMISSION
        # kalemlerini GÖRMEZ (ölçüm: defter −50 iken hesap income −380). Hesabın
        # ham günlük income'ı burada raporlanır ki operatör iki sayı arasındaki
        # farkı görebilsin. Okuma başarısızlığı kesiciyi ETKİLEMEZ.
        if embedded:
            try:
                self._daily_income_account = await self._get_account_daily_net_income(
                    today
                )
            except Exception:
                self._daily_income_account = None

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

        # D20b (doğrulayıcı bulgusu Y9): `balance` PAYLAŞILAN cüzdandır (AP
        # kâr/zararı dahil), `pnl` ise yalnız scalper defteridir. Gömülü modda
        # GERÇEK cüzdan kullanılıyorsa (sanal kasa kapalı) günün AÇILIŞ
        # sermayesi AP'nin bugünkü defter PnL'i de düşülerek yaklaşıklanır —
        # aksi halde takipçinin kârı/zararı scalper'ın eşiğini kaydırırdı.
        day_start_offset = pnl
        if embedded and self._risk_equity_source == "exchange_wallet":
            try:
                day_start_offset = pnl + await self._ledger_daily_pnl(
                    today, strategies=(FOLLOWER_LEDGER_STRATEGY,)
                )
            except Exception as exc:  # teşhis; eşik hesabı düşmemeli
                self.logger.warning(
                    f"⚠️ Gün-başı sermaye yaklaşıklığında AP defteri okunamadı "
                    f"({exc}); eşik yalnız scalper PnL'iyle ölçüldü"
                )
        approximate_day_start_balance = max(balance - day_start_offset, 0.0)
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

    async def _ledger_daily_pnl(
        self, today: str, *, strategies=None
    ) -> float:
        """Scalper'ın KENDİ defterinden bugünkü net PnL (gömülü mod, D20b).

        Kaynak `scalp_trades` (AP satırları HARİÇ); `realized_pnl` komisyon
        düşülmüş nettir. Önbellek YOKTUR: tek `SUM()` sorgusudur ve income
        önbelleğinin doğurduğu "aynı gün iki farklı PnL" sınıfını kökten
        kapatır. Takipçinin eşleniği `FollowerEngine._ledger_daily_pnl`'dir —
        aynı mekanizma, ters filtre.
        """
        getter = getattr(self.tracker, "realized_pnl_since", None)
        if getter is None:  # pragma: no cover - eski tracker çifti
            raise RuntimeError(
                "tracker.realized_pnl_since yok — gömülü modda scalper günlük "
                "PnL'i defterden okunamıyor"
            )
        day_start = datetime.strptime(today, "%Y-%m-%d")
        if strategies:
            return float(await getter(day_start, strategies=tuple(strategies)))
        return float(
            await getter(day_start, exclude_strategies=(FOLLOWER_LEDGER_STRATEGY,))
        )

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

    def _tv_events_snapshot(self) -> Dict[str, Any]:
        """`/scalper/status` için TV olay defteri telemetrisi (secret YOK)."""
        ledger = self._tv_ledger()
        if ledger is None:
            return {}
        try:
            return ledger.snapshot()
        except Exception as e:
            self.logger.error(f"TV olay telemetrisi okunamadı: {e}")
            return {"error": f"{type(e).__name__}"}

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
            # Yalnız BİLGİ: gömülü modda hesabın ham günlük income'ı (kesici
            # bunu KULLANMAZ). None = okunamadı ya da gömülü mod kapalı.
            "daily_income_account": getattr(self, "_daily_income_account", None),
            "risk_ready": self._risk_ready,
            "risk_equity_usdt": self._risk_equity_usdt,
            "risk_equity_source": self._risk_equity_source,
            "daily_loss_threshold_usdt": self._daily_loss_threshold_usdt,
            "daily_limit_pct": self.cfg.scalper_daily_loss_limit_pct,
            "kill_switch_active": self._kill_switch,
            # D22: bu anlık görüntünün KURULDUĞU an. `/scalper/status` yanıtı
            # 5 sn önbelleklendiği için pano "son güncelleme"yi istek anından
            # değil BURADAN yazmalı — aksi halde bayat tablo taze görünür.
            "as_of": _utcnow_iso(),
            # D22: "kapı bayat" ile "tarama durdu" karışmasın — tek alanda
            # yeni girişleri kimin durdurduğu.
            "entries_blocked_by": self.entries_blocked_by(),
            # D22: REST ağırlık bütçesi (son/tepe + geri çekilme sayaçları).
            "rest_weight": self._rest_weight_snapshot(),
            # D21/D22: adli kayıt yazıcı kuyruğu + post-mortem turu durumu.
            "forensics_queue": self._forensics_queue_snapshot(),
            # D27/B: karşı-olgu defteri sayaçları (SÜREÇ-İÇİ, restart'ta
            # sıfırlanır). Kalıcı tablo: `/scalper/counterfactual`.
            "counterfactual": self._counterfactual_snapshot(),
            # D23: AI karar katmanı (gölge) — mod, kapsama, gecikme, bütçe,
            # red oranı ve son kararlar. Motor davranışını ETKİLEMEZ.
            "ai_gate": self._ai_gate_snapshot(),
            "entry_halted": self._entry_halted,
            "entry_halt_reason": self._entry_halt_reason,
            "entry_halted_at": self._entry_halted_at,
            "risk_event": self.risk_event_status(),
            "tv_events": self._tv_events_snapshot(),
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
            # D27/A4: TP1/TP2 emri konulamadı mı? TP1'siz pozisyonda
            # break-even HİÇ kurulmaz ve işlem tam risk stopuyla taşınır.
            "order_health": self._executor_order_health_snapshot(),
            "stop_mode": str(getattr(self.cfg, "scalper_stop_mode", "structural")),
            "sizing": sizing,
            "sizing_equity_usdt": sizing_equity,
            "virtual_capital_enabled": virtual_enabled,
            "virtual_capital_base_usdt": configured_virtual_base,
            "virtual_capital_current_usdt": current_virtual_capital,
            "virtual_capital_start_trade_id": virtual_start_trade_id,
            "symbol_reservations": symbol_reservations.snapshot(),
        }
