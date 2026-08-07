"""
ScalperEngine — scalper alt sisteminin orkestrasyon katmanı.

Kendi ImprovedBinanceClient + PositionManager çiftini kurar (orchestrator'ın
Telegram akışıyla PAYLAŞMAZ — iki bağımsız istemci, iki bağımsız bağlantı
havuzu; scalper'ın hızlı tarama döngüsü Telegram sinyal akışını asla
bloklamaz). KlineFetcher/UniverseScanner de public/imzasız endpoint'ler
üzerinden kendi httpx havuzlarını kurar (data.py/scanner.py'nin kendi
tasarım ilkeleri).

Ana döngü (_loop, her scalper_scan_interval_seconds'ta bir):
  1. exits.step() — HER turda, taramadan BAĞIMSIZ, ÖNCE çalışır (açık
     pozisyonların TP1/trailing/kapanış takibi hiçbir zaman durmaz).
  2. Günlük zarar kesici (kill switch) — limit aşılırsa yeni giriş
     durur, exits.step() sürmeye devam eder.
  3. Evren taraması.
  4. kill_switch değilse: her sembol için tek strateji denemesi.

Hata izolasyonu: bir sembolün hatası turu öldürmez (sembol başına
try/except), ama asyncio.CancelledError her zaman yükseltilir (görev
iptali yutulmaz).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.scalper.data import KlineFetcher
from src.strategies.scalper.executor import ScalpExecutor
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.indicators import atr as compute_atr
from src.strategies.scalper.regime import detect_regime
from src.strategies.scalper.scanner import UniverseScanner
from src.strategies.scalper.setups import get_enabled
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import Regime, StrategyContext
from src.trading.binance_client_improved import ImprovedBinanceClient
from src.trading.position_manager import PositionManager


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScalperEngine:
    """Tarama → sinyal değerlendirme → giriş → çıkış döngüsünü yürütür."""

    _REGIME_CACHE_TTL = 300.0    # saniye — sembol başına rejim önbelleği
    _BALANCE_CACHE_TTL = 300.0   # saniye — kill switch için bakiye önbelleği

    def __init__(self) -> None:
        self.cfg = settings
        self.logger = app_logger

        # Orchestrator'dan bağımsız kendi istemci çifti.
        self.client = ImprovedBinanceClient()
        self.pm = PositionManager(self.client)
        self.fetcher = KlineFetcher()
        self.scanner = UniverseScanner(top_n=settings.scalper_top_n)
        self.tracker = ScalpTracker()
        self.executor = ScalpExecutor(self.client, self.pm, self.tracker, self.cfg)
        self.exits = ExitManager(
            self.client, self.pm, self.tracker, self.cfg, self.fetcher.get_klines
        )

        self._task: Optional[asyncio.Task] = None
        self.running = False

        # Anlık durum — snapshot() bunları okur.
        self._universe: List[str] = []
        self._regimes: Dict[str, str] = {}
        self._regime_cache: Dict[str, Tuple[Regime, float]] = {}
        self._balance_cache: Tuple[Optional[float], float] = (None, 0.0)
        self._daily_pnl: float = 0.0
        self._kill_switch: bool = False
        self._kill_switch_day: Optional[str] = None
        self._signals_today: int = 0
        self._last_scan_at: Optional[str] = None

    # ------------------------------------------------------------------
    # Yaşam döngüsü
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self.logger.info("⚡ Scalper motoru başlatılıyor...")
        self.logger.info(
            f"🎯 Evren={self.cfg.scalper_top_n} sembol, tarama={self.cfg.scalper_scan_interval_seconds}sn, "
            f"stratejiler={self.cfg.scalper_strategies}, kaldıraç={self.cfg.scalper_leverage}x"
        )
        await self.exits.recover()

        self.running = True
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._loop())

        self.logger.info("✅ Scalper motoru hazır")

    async def stop(self) -> None:
        self.logger.info("🛑 Scalper motoru durduruluyor...")
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        for closer in (self.fetcher.close, self.scanner.close, self.client.close):
            try:
                await closer()
            except Exception as e:
                self.logger.warning(f"⚠️ Scalper motoru kapatılırken kaynak temizleme hatası: {e}")

        self.logger.info("✅ Scalper motoru durduruldu")

    # ------------------------------------------------------------------
    # Ana döngü
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        self.logger.info("👁️ Scalper tarama döngüsü başladı")
        while self.running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                self.logger.info("👁️ Scalper tarama döngüsü durduruldu")
                raise
            except Exception as e:
                self.logger.error(f"❌ Scalper döngü hatası: {e}", exc_info=True)

            await asyncio.sleep(self.cfg.scalper_scan_interval_seconds)

    async def _tick(self) -> None:
        # 1. Açık pozisyonların çıkış takibi — taramadan BAĞIMSIZ, her zaman önce.
        await self.exits.step()

        # 2. Günlük zarar kesici.
        await self._update_kill_switch()

        # 3. Evren taraması.
        self._universe = await self.scanner.get_universe()
        self._last_scan_at = _utcnow_iso()

        if self._kill_switch:
            return

        # 4. Sembol başına tek strateji denemesi.
        enabled_strategies = get_enabled(self.cfg.scalper_strategies)
        if not enabled_strategies:
            return

        for symbol in self._universe:
            if symbol in self.exits.tracked_symbols():
                continue
            if len(self.exits.tracked_symbols()) >= self.cfg.scalper_max_positions:
                # Sembol başına değil, TUR başına kesici: kapasite dolduysa bu
                # turda başka hiçbir yeni sembol denenmez.
                break

            try:
                await self._evaluate_symbol(symbol, enabled_strategies)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"❌ {symbol}: tur değerlendirmesi hata verdi ({e})", exc_info=True)

    async def _evaluate_symbol(self, symbol: str, enabled_strategies: list) -> None:
        try:
            pos = await self.client.get_position_risk(symbol)
        except Exception as e:
            self.logger.debug(f"{symbol}: pozisyon durumu sorgulanamadı, atlanıyor ({e})")
            return
        if pos is not None and float(pos.get("positionAmt", 0) or 0) != 0:
            # Telegram botu veya elle açılmış pozisyon — dokunma.
            return

        candles_4h = await self.fetcher.get_klines(symbol, "4h", 250)
        candles_15m = await self.fetcher.get_klines(symbol, "15m", 100)
        candles_5m = await self.fetcher.get_klines(symbol, "5m", 150)

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

        for strat in enabled_strategies:
            sig = strat.evaluate(ctx)
            if sig is None:
                continue

            sp = await self.executor.try_open(sig, ctx)
            if sp:
                self.exits.track(sp)
                self._signals_today += 1
                self.logger.info(
                    f"🎯 {symbol}: strateji {sig.strategy} sinyali işlendi -> pozisyon açıldı "
                    f"({sig.direction.value} @ {sp.position.entry_price})",
                    extra={"trade": True},
                )
            # Sembol başına tek deneme: sinyal bulunduğu an (başarılı ya da
            # başarısız) bu sembol için tur biter.
            break

    def _get_cached_regime(self, symbol: str, candles_4h: list) -> Regime:
        now = time.monotonic()
        cached = self._regime_cache.get(symbol)
        if cached is not None and (now - cached[1]) < self._REGIME_CACHE_TTL:
            return cached[0]
        regime = detect_regime(candles_4h)
        self._regime_cache[symbol] = (regime, now)
        return regime

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
            return  # kesici kapalı

        try:
            pnl = await self.tracker.today_realized_pnl()
        except Exception as e:
            self.logger.error(f"❌ Günlük PNL okunamadı, kill switch bu turda değerlendirilemedi: {e}")
            return
        self._daily_pnl = pnl

        if self._kill_switch:
            return  # zaten tetiklenmiş — gün UTC değişene kadar kapalı kalır

        balance = await self._get_cached_balance()
        if balance is None or balance <= 0:
            return

        threshold = -balance * self.cfg.scalper_daily_loss_limit_pct / 100.0
        if pnl <= threshold:
            self._kill_switch = True
            self.logger.warning(
                f"⛔ Scalper kill switch TETİKLENDİ: günlük PNL={pnl:.2f} <= eşik={threshold:.2f} "
                f"(bakiye={balance:.2f}, limit=%{self.cfg.scalper_daily_loss_limit_pct}). "
                f"Yeni giriş durduruldu, açık pozisyonların çıkış takibi sürüyor."
            )

    async def _get_cached_balance(self) -> Optional[float]:
        balance, cached_at = self._balance_cache
        now = time.monotonic()
        if balance is not None and (now - cached_at) < self._BALANCE_CACHE_TTL:
            return balance
        try:
            fresh = await self.client.get_account_balance()
        except Exception as e:
            self.logger.error(f"❌ Bakiye sorgusu hatası (kill switch): {e}")
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

            tracked.append({
                "symbol": symbol,
                "strategy": sp.signal.strategy,
                "direction": direction.value,
                "entry_price": entry,
                "quantity": quantity,
                "current_stoploss": sp.position.current_stoploss,
                "tp1_done": sp.tp1_done,
                "trailing_active": sp.trailing_active,
                "unrealized_pnl": unrealized_pnl,
                "roi_pct": roi_pct,
                "opened_at": sp.position.opened_at.isoformat() if sp.position.opened_at else None,
            })

        return {
            "enabled": self.cfg.scalper_enabled,
            "running": self.running,
            "scan_interval": self.cfg.scalper_scan_interval_seconds,
            "universe": list(self._universe),
            "regimes": dict(self._regimes),
            "daily_pnl": self._daily_pnl,
            "daily_limit_pct": self.cfg.scalper_daily_loss_limit_pct,
            "kill_switch_active": self._kill_switch,
            "signals_today": self._signals_today,
            "last_scan_at": self._last_scan_at,
            "tracked": tracked,
        }
