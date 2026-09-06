"""
ExitManager — scalper pozisyonları için TP dolum takibi, break-even geçişi ve
chandelier trailing döngüsü.

GÜVENLİK İLKESİ (bugünkü onarımlarla birebir): "bilinmiyor" ASLA "kapandı"
sayılmaz. Pozisyon durumu sorgulanamazsa izleme BIRAKILMAZ, o tur atlanır.
SL değişimleri PositionManager'ın boşluksuz deseniyle yapılır (önce yeni
reduceOnly SL, sonra eskisi iptal) — pm.replace_stop_loss sarmalayıcısı
üzerinden.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from src.core.logger import app_logger
from src.models.position import PositionModel, PositionStatus, PositionSide
from src.strategies.scalper.data import MarketDataUnavailable
from src.strategies.scalper.executor import ScalpPosition
from src.strategies.scalper.indicators import chandelier_stop
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    EXIT_REASON_STALE_TP,
    FOLLOWER_LEDGER_STRATEGY,
    Candle,
    Direction,
    ExitPlan,
    Regime,
    ScalpSignal,
    fee_aware_breakeven_price,
    price_at_roi,
    resolve_trail_mult,
)
from src.trading.binance_client_improved import (
    ImprovedBinanceClient,
    BinanceAPIError,
)
from src.trading.position_manager import (
    PositionManager,
    StopReplaceResult,
    UnprotectedPositionError,
)

KlineFetch = Callable[[str, str, int], Awaitable[List[Candle]]]
# Veri host'unun CANLI fiyatını veren çağrı (`KlineFetcher.get_price`).
# Opsiyoneldir: verilmezse ayrı-host çevirisi baz ölçemez ve turu atlar
# (fail-closed) — aynı host'ta (varsayılan) HİÇ çağrılmaz.
PriceFetch = Callable[[str], Awaitable[float]]

# D22 (daraltılmış): bot KENDİ İSTEĞİYLE piyasa emri GÖNDERMEZ. Bu etiketler
# yalnız ŞU durumda yazılır: koruyucu stop borsaya gönderildi, Binance -2021
# ("Order would immediately trigger") döndürdü ve `position_manager` mevcut
# (D22 ÖNCESİ de var olan) `_emergency_close` yolunu çalıştırıp pozisyonu
# reduce-only MARKET ile kapattı. Yani etiket YENİ BİR DAVRANIŞ DEĞİL, zaten
# olan bir kapanışın DÜRÜST ADIDIR — eskiden bu kapanış deftere "TRAIL"
# yazılıyor ve log "eski SL korunuyor" diyordu (2026-08-23: DOGE/BNB/ETH).
#
#   TRAIL_MARKET → trailing / runner-tabanı kararı böyle uygulandı
#   BE_MARKET    → TP1 sonrası break-even kararı böyle uygulandı
#
# İkisi de TRAIL ailesindendir (stop kararının piyasa emriyle uygulanmış hâli)
# ama AYRI SAYILIR: sayıları artıyorsa stop kararı piyasa hızının gerisinde
# kalıyordur.
EXIT_REASON_TRAIL_MARKET = "TRAIL_MARKET"
EXIT_REASON_BE_MARKET = "BE_MARKET"
TRAIL_EXIT_REASONS = frozenset({"TRAIL", EXIT_REASON_TRAIL_MARKET})
# -2021 sonrası acil kapanışla sonlanan etiketler (defter/telemetri ortak).
MARKET_EXIT_REASONS = frozenset({EXIT_REASON_TRAIL_MARKET, EXIT_REASON_BE_MARKET})

# D27/A1 — YAŞ KESMESİ (reaper, D4) AYRI ETİKET. Bu bir DAVRANIŞ değişikliği
# değil, zaten olan bir kapanışın dürüst adıdır: `engine._reap_aged_positions`
# yaş limitini dolduran KORUMASIZ (TP1 görmemiş) pozisyonu reduce-only MARKET
# ile kapatır ve bu kapanış bugüne kadar deftere "SL" yazılıyordu.
# **GERİYE DÖNÜK VERİ DÜZELTMESİ YOKTUR**: eski satırlar "SL" olarak kalır,
# yeni kapanışlar "REAPER" yazılır. Raporlar bunu "REAPER ayrımı
# 2026-08-24'ten itibaren" notuyla söyler (scripts/ledger_report.py).
EXIT_REASON_REAPER = "REAPER"

# D30 — BAYAT-KÂR KAPANIŞI ("STALE_TP"): `engine._close_stale_profitable_
# positions` TP1 görmemiş, `SCALPER_STALE_TP_HOURS` yaşını doldurmuş ve o an
# ROI ≥ `SCALPER_STALE_TP_MIN_ROI_PCT` olan pozisyonu reduce-only MARKET ile
# kapatır. REAPER gibi düz bir MARKET emridir → `_verified_close_ledger`
# göremez → kaba çıkarıma düşer; damga (`ScalpPosition.stale_tp_close_at`)
# olmadan bu kapanış "TP_LADDER"/"SL" diye yanlış etiketlenirdi. Dize
# `types.EXIT_REASON_STALE_TP`ten gelir (harness ile ortak; yukarıda import
# edildi, bu modülden de `EXIT_REASON_STALE_TP` adıyla erişilir).

# --- D17: ayrı market-data host'unda fiyat uzayı çevirisi ----------------
# Chandelier seviyesi VERİ host'unun mumlarından çıkar ama emir İŞLEM host'una
# gider. Baz (iki defter arasındaki anlık fark) her turda YENİDEN ölçülür ve
# D17-R3'ten (bütünleşme incelemesi) beri LIKE-FOR-LIKE'tır:
#     baz = işlem_host_CANLI_fiyat − veri_host_CANLI_fiyat
# Eski biçim (`… − veri_host_son_KAPANIŞ`) iki farklı türü karıştırıyordu:
# fark, borsa-arası bazın ÜSTÜNE mum-içi sürüklenmeyi de bindiriyordu.
# Aşağıdaki sabitler bu çevirinin akıl sağlığı sınırlarıdır.
#
# İşlem host'u fiyatının azami yaşı (sn). `_step_one` her turda (≈2 sn)
# `client.get_current_price` ile tazeler; bu sınır yalnız o çağrının başarısız
# olduğu ya da restart kurtarmasının ilk turu gibi durumlar içindir — bayat bir
# işlem fiyatı ile taze bir veri kapanışını çıkarmak SAHTE bir baz üretir.
_TRADING_PRICE_MAX_AGE_SECONDS = 30.0
# Baz, işlem fiyatının bu yüzdesini aşarsa çeviri güvenilmez sayılır (yanlış
# sembol/ölçek, borsalardan birinin donması). E8.0 ölçümü: iki defter arası
# medyan sapma %0.054 — %2 tavanı normal işletmede asla bağlamaz.
_MAX_PRICE_BASIS_PCT = 2.0
# Koruma-tarafı kapısı payı (yüzde): stop, güncel fiyattan en az bu kadar uzak
# ve DOĞRU tarafta olmalı. Binance koşullu emri MARK fiyatına göre tetikler;
# `get_current_price` son işlem fiyatını verir — aradaki tipik baz bu payın
# altındadır. `should_update` eşiğiyle (±%0.05) aynı büyüklükte tutuldu.
_PROTECTIVE_GATE_MARGIN_PCT = 0.05
# Aynı sembol için kapı/çeviri uyarısı en fazla bu sıklıkta loglanır (safety
# turu 2 sn'de bir döner — aksi halde saatte 1800 satır).
_TRAILING_SKIP_LOG_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class _CloseLedger:
    """Borsa userTrades satırlarıyla kanıtlanmış kapanış özeti."""
    exit_price: float        # pozisyonu sıfırlayan emrin fill VWAP'ı
    exit_reason: str         # "SL" | "TRAIL" | "TRAIL_MARKET" | "TP_LADDER"
    net_pnl_estimate: float  # Σ(realizedPnl − commission) − tahmini giriş komisyonu
    close_fills: int
    flatten_kind: str        # "SL" | "TP1" | "TP2"
    # --- D27/A2: merdiven-farkında BRÜT (yalnız ölçüm) --------------------
    # `gross_pnl` = TÜM kapanış bacaklarının Σ(realizedPnl)'i — komisyon HARİÇ.
    # `_estimate_gross_pnl` brütü TEK çıkış fiyatıyla hesaplar; TP1/TP2/runner
    # üç ayrı fiyattan doldursa bile tek fiyat varsayar. Ölçüldü (2026-08-24):
    # 22 işlemin 8'inde `forensics.fee_estimate` teorik komisyonun 2 katından
    # fazla, 5'inde NEGATİF çıkmıştı → `fee_dominated` etiketi geçersizdi.
    # Bu alan borsa satırlarından gelen GERÇEK brüttür; PnL/karar yollarına
    # GİRMEZ (net hesabı `net_pnl_estimate` ile birebir aynı kaldı).
    gross_pnl: Optional[float] = None
    legs: int = 0            # kapanışı oluşturan TOPLAM fill satırı sayısı


class ExitManager:
    """Açık scalper pozisyonlarını izler ve çıkış merdivenini yönetir."""

    # Income history, gerçekleşen çıkıştan birkaç yüz milisaniye sonra
    # tamamlanabilir. Tüm bekleme kesin olarak sınırlıdır; testler bu tuple'ı
    # örnek üzerinde (0.0,) yaparak gerçek uyku olmadan çalışır.
    INCOME_RETRY_DELAYS = (0.0, 0.5, 1.0, 2.0)
    # Binance order updateTime milisaniye hassasiyetindedir. Aynı dolumun
    # komisyonunu kaçırmamak için yalnızca çok küçük bir güvenlik payı bırakılır.
    INCOME_ENTRY_LOOKBACK_SECONDS = 5
    NET_INCOME_TYPES = frozenset({"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"})

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
        kline_fetch: KlineFetch,
        loss_cooldown_cb: Optional[Callable[[str], None]] = None,
        data_price_fetch: Optional[PriceFetch] = None,
        forensics_context_cb: Optional[Callable[[str], Dict[str, Any]]] = None,
    ):
        self.client = client
        self.pm = pm
        self.tracker = tracker
        self.cfg = cfg
        self.kline_fetch = kline_fetch
        # D17-R3: veri host'unun CANLI fiyatı (borsa-arası bazın veri tarafı).
        # Opsiyonel — verilmezse ayrı host'ta baz ölçülemez ve trailing turu
        # atlanır (fail-closed). Aynı host'ta HİÇ çağrılmaz.
        self.data_price_fetch = data_price_fetch
        self.logger = app_logger
        self._positions: Dict[str, ScalpPosition] = {}
        # Aynı sembol için _handle_closed'a İKİ YOLDAN (safety turu ve
        # risk-olayı flatten'ı) eşzamanlı girişi engelleyen uçuş-halinde
        # kümesi. Tek event-loop'ta check+add arasında await yoktur.
        self._closing: Set[str] = set()
        # SL/negatif kapanışta executor'ın sembol cooldown'unu başlatır.
        # Opsiyonel: verilmezse (eski kurulum/testler) davranış değişmez.
        self._loss_cooldown_cb = loss_cooldown_cb
        # D21 adli kayıt: kapanış anındaki piyasa bağlamı (sembol rejimi,
        # lider gün sapması, BTC fiyatı) motordan SENKRON okunur — ek REST
        # çağrısı YOKTUR. Opsiyonel: verilmezse alanlar boş kalır, davranış
        # birebir aynıdır.
        self._forensics_context_cb = forensics_context_cb
        self._forensics_error_logged: bool = False
        # D17: piyasa verisi host GENELİ kesildiğinde (ban/bütçe) her tur her
        # sembol için ayrı WARNING basılmasın — tur başına bir kez.
        self._market_data_down_reason: Optional[str] = None
        # D17 fiyat-uzayı çevirisi (ayrı market-data host'u):
        #   * sembol → işlem host'u fiyatının en son tazelendiği monotonic an
        #     (bayat fiyatla baz ölçülemez),
        #   * atlanan trailing güncellemesi sayaçları (teşhis; /scalper/status),
        #   * sembol başına oran-sınırlı uyarı zamanı.
        self._trading_price_seen_at: Dict[str, float] = {}
        self._trailing_space_skips: int = 0    # baz ölçülemedi
        self._trailing_gate_skips: int = 0     # koruma-tarafı kapısı reddetti
        self._trailing_skip_log_at: Dict[str, float] = {}
        # D22: koruyucu stop -2021 aldı ve `position_manager._emergency_close`
        # pozisyonu piyasa emriyle kapattı (bot kendiliğinden emir GÖNDERMEZ).
        self._trailing_market_exits: int = 0
        # D17-R3: sembol başına veri host'u canlı fiyatı okuma hatasının
        # oran-sınırlı loglanması (`_log_trailing_skip` ile aynı disiplin).
        self._data_price_error_log_at: Dict[str, float] = {}

    def _maybe_start_loss_cooldown(
        self,
        symbol: str,
        exit_reason: str,
        realized_pnl: float,
        loss_threshold: float = 0.0,
    ) -> None:
        """Kayıplı kapanışta sembol cooldown'u tetikle; kapanış yolunu asla bozma.

        ``loss_threshold`` doğrulanmış NET PnL için 0'dır. PnL kaynağı BRÜT
        tahminse (estimated_gross) çağıran, tahmini gidiş-dönüş komisyonunu
        eşik olarak geçer: brüt küçük artı görünen ama net eksi olan kapanışlar
        da cooldown başlatır — backtest'in NET pnl<0 kuralıyla parite korunur.
        """
        if self._loss_cooldown_cb is None:
            return
        if exit_reason != "SL" and realized_pnl >= loss_threshold:
            return
        try:
            self._loss_cooldown_cb(symbol)
        except Exception as e:
            self.logger.error(f"⚠️ {symbol}: kayıp cooldown'u başlatılamadı ({e})")

    def _estimated_roundtrip_fee(
        self, entry_price: float, exit_price: float, quantity: float
    ) -> float:
        """Muhafazakâr (taker) iki bacak komisyon tahmini — brüt PnL eşiği için."""
        try:
            rate = max(
                float(getattr(self.cfg, "scalper_taker_fee_pct", 0.05) or 0.05),
                float(getattr(self.cfg, "scalper_maker_fee_pct", 0.02) or 0.02),
            ) / 100.0
        except (TypeError, ValueError):
            rate = 0.0005
        return (abs(entry_price) + abs(exit_price)) * abs(quantity) * rate

    def track(self, sp: ScalpPosition) -> None:
        """Pozisyonu izleme listesine ekle (sembol anahtarlı)."""
        self._positions[sp.position.symbol] = sp

    def tracked_symbols(self) -> Set[str]:
        return set(self._positions.keys())

    # ------------------------------------------------------------------
    # Ana döngü adımı
    # ------------------------------------------------------------------

    async def step(self) -> None:
        """Engine her turda çağırır: her izlenen sembol için bir adım işlet."""
        # Tur başında sıfırlanır: host geneli bir piyasa-verisi kesintisi bu
        # tur içinde yalnız BİR kez loglanır ve kalan sembollerde trailing
        # atlanır. TP/kapanış tespiti İMZALI yoldan geldiği için ATLANMAZ.
        self._market_data_down_reason = None
        for symbol in list(self._positions.keys()):
            sp = self._positions.get(symbol)
            if sp is None:
                continue
            await self._step_one(symbol, sp)

    async def _step_one(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            pos_info = await self.client.get_position_risk(symbol)
        except BinanceAPIError as e:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgulanamadı (kod={e.code}: {e.msg}). "
                f"İzleme sürüyor — 'bilinmiyor' 'kapandı' sayılmaz."
            )
            return
        except Exception as e:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgusunda beklenmeyen hata ({e}). İzleme sürüyor."
            )
            return

        # Eşzamanlı finalize kontrolü: yukarıdaki await sırasında başka bir yol
        # (risk-olayı flatten → _handle_closed) bu pozisyonu bitirip listeden
        # çıkarmış ya da değiştirmiş olabilir. Bayat `sp` nesnesiyle devam etmek
        # ikinci bir finalize/cancel_all demektir — tek-finalizer kilidinin
        # (`_closing`) tamamlayıcısı: izlenen nesne artık bu değilse adımı bırak.
        if self._positions.get(symbol) is not sp:
            self.logger.debug(
                f"{symbol}: pozisyon bu adım sırasında başka yolca sonlandırıldı — adım atlandı"
            )
            return

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0

        if amt == 0:
            await self._handle_closed(symbol, sp)
            return

        # MAE/MFE güncelle (mark/güncel fiyat ile)
        try:
            current_price = await self.client.get_current_price(symbol)
        except Exception as e:
            current_price = None
            self.logger.debug(f"{symbol}: güncel fiyat alınamadı ({e}), MAE/MFE bu turda güncellenmiyor")
        if current_price:
            self._update_mae_mfe(sp, current_price)
            sp.position.current_price = current_price
            # D17: bu fiyat İŞLEM host'undandır ve ayrı market-data host'unda
            # fiyat-uzayı bazının tek eş-anlı referansıdır — tazeliği kayıtlı
            # olmalı (bkz. _to_trading_price_space).
            seen_at = getattr(self, "_trading_price_seen_at", None)
            if seen_at is None:
                seen_at = {}
                self._trading_price_seen_at = seen_at
            seen_at[symbol] = time.monotonic()
            # D19a-2: fiyatın YAŞI da kaydedilir. `breakeven_side_ok` bayat
            # fiyatla "kârda" hükmü verip stopu piyasanın ters tarafına
            # koymasın (-2021 → _emergency_close).
            sp.price_ts = time.monotonic()

        # TP1 dolum kontrolü → break-even
        if not sp.tp1_done:
            await self._check_tp1(symbol, sp, amt)
            # D22: TP1 yolunda -2021 → acil kapanış olmuş olabilir. Kapanmış
            # bir pozisyonda TP2/trailing turuna devam etmek boşa REST
            # harcar ve yanıltıcı log üretir.
            if self._position_finalized(symbol, sp):
                return

        # TP2 yalnız gerçek algo fill + gerçek futures trade satırlarıyla
        # doğrulanır; ardından runner tabanı sabit TP1 fiyatına yükseltilir.
        if not sp.tp2_done:
            await self._check_tp2(symbol, sp, amt)
            if self._position_finalized(symbol, sp):
                return

        # Chandelier trailing
        if sp.trailing_active:
            await self._update_trailing(symbol, sp)

    def _position_finalized(self, symbol: str, sp: ScalpPosition) -> bool:
        """Bu adım sırasında pozisyon kapanıp deftere yazıldı mı? (D22)"""
        if getattr(sp, "close_recorded", False):
            return True
        return self._positions.get(symbol) is not sp

    async def _tp_reduction_hint(self, symbol: str, quantity: float) -> float:
        """Use the same LOT_SIZE rounding as the submitted TP order.

        The plan stores pre-rounding fractions, including after recovery. A
        small TP can round down by more than 10%, so comparing live quantity
        with that raw fraction can prevent querying a fully filled TP forever.
        Symbol filters are cached by the client; this does not add a signed
        request. A failed filter lookup disables only this optimization: the
        algo order and its actual fills must still prove the TP below.
        """
        quantize = getattr(self.client, "quantize_quantity", None)
        if quantize is None:
            return quantity
        try:
            rounded = float(await quantize(symbol, quantity))
            if math.isfinite(rounded) and 0 <= rounded <= quantity:
                return rounded
        except Exception as exc:
            self.logger.debug(f"{symbol}: TP quantity hint unavailable ({exc})")
        return 0.0

    async def _check_tp1(self, symbol: str, sp: ScalpPosition, live_qty: float) -> None:
        filled = sp.position.quantity
        if filled <= 0 or live_qty >= filled or not sp.plan.tp1_algo_id:
            return
        expected = await self._tp_reduction_hint(symbol, sp.plan.tp1_quantity)
        threshold = filled - expected * 0.9

        if live_qty > threshold:
            return  # TP1 henüz dolmadı

        if not await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=sp.plan.tp1_algo_id,
            expected_quantity=sp.plan.tp1_quantity,
            label="TP1",
        ):
            self.logger.warning(
                f"⚠️ {symbol}: miktar azaldı ancak TP1 algo fill'i doğrulanamadı; "
                "salt quantity reduction break-even tetiklemeyecek"
            )
            return

        self.logger.info(
            f"🎯 {symbol}: TP1 gerçek fill ile doğrulandı (kalan={live_qty}) — "
            f"SL break-even'e taşınıyor"
        )
        current_sl = sp.position.current_stoploss
        target = sp.plan.breakeven_price
        already_tighter = self._is_at_least_as_protective(
            sp.signal.direction, current_sl, target
        )
        result = (
            StopReplaceResult(True, "already_tighter")
            if already_tighter
            else await self._apply_stop(sp.position, target)
        )
        ok = result.ok
        if ok:
            sp.tp1_done = True
            sp.trailing_active = True
            if not already_tighter:
                sp.position.current_stoploss = target
            # D21 zaman çizgisi (yalnız gözlem): TP1 → BE geçişinin anı.
            self._mark_path(sp, "tp1_at")
            self._mark_path(sp, "be_at")
            sp.be_price = sp.position.current_stoploss
            self.logger.info(
                f"✅ {symbol}: ücret-dahil break-even aktif, "
                f"SL={sp.position.current_stoploss}"
            )
        elif result.outcome == "emergency_closed":
            # D22: "eski SL korunuyor" YALNIZ pozisyon gerçekten açıksa
            # yazılır. Burada -2021 sonrası pozisyon PİYASA emriyle kapandı.
            await self._on_emergency_closed(
                symbol,
                sp,
                result,
                exit_reason=EXIT_REASON_BE_MARKET,
                what=f"BE seviyesi ({target})",
            )
        elif result.outcome == "no_position":
            self.logger.debug(
                f"{symbol}: BE taşınmadı — borsada pozisyon kalmamış"
            )
        else:
            self.logger.warning(
                f"⚠️ {symbol}: SL break-even'e taşınamadı, eski SL korunuyor. "
                f"Sonraki turda tekrar denenecek."
            )

    async def _check_tp2(self, symbol: str, sp: ScalpPosition, live_qty: float) -> None:
        filled = sp.position.quantity
        expected = sp.plan.tp2_quantity
        if (
            filled <= 0 or expected <= 0 or live_qty >= filled
            or not sp.plan.tp2_algo_id
        ):
            return
        # Bu eşik yalnız pahalı signed sorguyu erteleyen bir ipucudur; fill
        # kanıtı değildir. TP1/manual reduction tek başına state değiştiremez.
        tp1_hint = (
            await self._tp_reduction_hint(symbol, sp.plan.tp1_quantity)
            if sp.plan.tp1_algo_id else 0.0
        )
        tp2_hint = await self._tp_reduction_hint(symbol, expected)
        if live_qty > filled - (tp1_hint + tp2_hint) * 0.9:
            return
        if not await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=sp.plan.tp2_algo_id,
            expected_quantity=expected,
            label="TP2",
        ):
            return

        floor = sp.plan.runner_floor_price or sp.plan.tp1_price
        current_sl = sp.position.current_stoploss
        already_tighter = self._is_at_least_as_protective(
            sp.signal.direction, current_sl, floor
        )
        result = (
            StopReplaceResult(True, "already_tighter")
            if already_tighter
            else await self._apply_stop(sp.position, floor)
        )
        if not result.ok:
            if result.outcome == "emergency_closed":
                await self._on_emergency_closed(
                    symbol,
                    sp,
                    result,
                    exit_reason=EXIT_REASON_TRAIL_MARKET,
                    what=f"runner tabanı ({floor})",
                )
            elif result.outcome == "no_position":
                self.logger.debug(
                    f"{symbol}: runner tabanı taşınmadı — borsada pozisyon kalmamış"
                )
            else:
                self.logger.warning(
                    f"⚠️ {symbol}: TP2 doğrulandı ama runner stopu TP1 tabanına "
                    "yükseltilemedi; eski SL korunuyor, sonraki tur tekrar denenecek"
                )
            return

        sp.tp2_done = True
        sp.trailing_active = True
        if not already_tighter:
            sp.position.current_stoploss = floor
        self._mark_path(sp, "tp2_at")   # D21 zaman çizgisi (yalnız gözlem)
        self.logger.info(
            f"✅ {symbol}: TP2 gerçek fill doğrulandı; runner sabit tabanı "
            f"TP1={floor} (aktif SL={sp.position.current_stoploss})"
        )

    @staticmethod
    def _is_at_least_as_protective(
        direction: Direction,
        current_stop: Optional[float],
        candidate: float,
    ) -> bool:
        """True ise candidate'a geçmek stopu sıkılaştırmaz (gevşetme yasak)."""
        if current_stop is None or current_stop <= 0:
            return False
        if direction == Direction.LONG:
            return current_stop >= candidate
        return current_stop <= candidate

    async def _confirmed_algo_fill(
        self,
        *,
        symbol: str,
        algo_id: Optional[str],
        expected_quantity: float,
        label: str,
    ) -> bool:
        """Algo emrinin gerçek child order fill'ini account trades ile kanıtla."""
        if not algo_id or expected_quantity <= 0:
            return False
        algo_getter = getattr(self.client, "get_algo_order", None)
        trades_getter = getattr(self.client, "get_account_trades", None)
        if algo_getter is None or trades_getter is None:
            return False
        try:
            algo = await algo_getter(algo_id=int(algo_id))
            actual_order_id = (algo or {}).get("actualOrderId")
            if actual_order_id in (None, "", 0, "0"):
                return False
            numeric_actual_id = int(actual_order_id)
            try:
                algo_quantity = float(
                    (algo or {}).get("quantity")
                    or (algo or {}).get("origQty")
                    or expected_quantity
                )
            except (TypeError, ValueError):
                return False
            if not math.isfinite(algo_quantity) or algo_quantity <= 0:
                return False
            rows = await trades_getter(
                symbol,
                order_id=numeric_actual_id,
                limit=500,
            )
        except Exception as exc:
            self.logger.warning(
                f"⚠️ {symbol}: {label} gerçek fill sorgusu başarısız ({exc})"
            )
            return False
        if not isinstance(rows, list) or not rows:
            return False

        total_qty = 0.0
        for row in rows:
            if not isinstance(row, dict):
                return False
            try:
                if int(row.get("orderId")) != numeric_actual_id:
                    return False
                qty = float(row.get("qty") or row.get("quantity") or 0.0)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(qty) or qty <= 0:
                return False
            total_qty += qty

        tolerance = max(1e-12, algo_quantity * 1e-6)
        if total_qty + tolerance < algo_quantity:
            return False
        # Beklenen miktarı aşan child fill başka bir emre ait olamaz; algo
        # miktarı ile uyumsuzsa state'i yine fail-closed bırak.
        if total_qty > algo_quantity + tolerance:
            self.logger.warning(
                f"⚠️ {symbol}: {label} fill miktarı beklenenden büyük "
                f"({total_qty} > {algo_quantity}); doğrulanmadı"
            )
            return False
        return True

    def _market_data_is_separate(self) -> bool:
        """Kline'lar İŞLEM borsasından FARKLI bir host'tan mı geliyor? (D17)

        Varsayılan (ayar boş) ve tüm eski test çiftleri için False → davranış
        bugünküyle birebir aynıdır.
        """
        market_url = str(
            getattr(self.cfg, "scalper_market_data_base_url", "") or ""
        ).strip()
        if not market_url:
            return False
        trading_url = str(getattr(self.cfg, "binance_base_url", "") or "").strip()
        return bool(trading_url) and market_url != trading_url

    def _to_trading_price_space(
        self,
        sp: ScalpPosition,
        price: float,
        data_reference: float,
        symbol: str = "",
    ) -> Optional[float]:
        """Chandelier'ın MUTLAK seviyesini işlem borsasının fiyat uzayına taşı.

        D17 düşmanca inceleme bulgusu (HIGH): ayrı market-data host'unda
        `chandelier_stop` mainnet mumlarından MUTLAK bir fiyat üretir, ama bu
        değer `pm.replace_stop_loss` ile TESTNET'e emir olarak gönderilir. İki
        defter arasındaki baz farkı `k×ATR`'yi aşarsa Binance -2021 verir ve
        `position_manager._replace_stop_loss` bunu "piyasa stop'u geçti" sayıp
        pozisyonu ACİL KAPATIR — yani kârlı bir koşucu, borsalar arası fiyat
        farkı yüzünden piyasa emriyle kapanabilirdi (log "eski SL korunuyor"
        derken kayıt TRAIL olarak etiketlenirdi). Ters yönde de gerçekleşen
        risk boyutlamadan sapar ve canlı defteri (nihai hakem) kirletir.

        BAZ DİNAMİK ve LIKE-FOR-LIKE'tır (D17-R3, bütünleşme incelemesi):
            baz = işlem_host_CANLI_fiyat − veri_host_CANLI_fiyat
        İkisi de AYNI TURDA ve AYNI TÜRDEN ölçülür: `sp.position.current_price`
        `_step_one`'da işlem host'unun ticker'ından tazelenir; `data_reference`
        `_data_host_price` ile veri host'unun public `/fapi/v1/ticker/price`
        okumasıdır.

        ⚠️ `data_reference` MUM KAPANIŞI OLAMAZ. İlk dinamik sürüm
        `candles[-1].close` (veri host'unun son KAPANMIŞ mumu) kullanıyordu;
        bu iki büyüklük AYNI TÜRDEN DEĞİLDİR ve fark, borsa-arası bazın
        ÜSTÜNE MUM-İÇİ SÜRÜKLENMEYİ bindirir. Sonuç sistematikti: fiyat
        pozisyonun lehine gittikçe (LONG'ta yükseldikçe) sürüklenme pozitif
        olur, chandelier mandalı (`new_stop > current_sl`) her turda biraz
        daha yukarı kilitlenir ve stop fiilen CANLI FİYATI izler — chandelier
        MESAFESİ değil. Ters yönde ise koruma-tarafı kapısı turu boşa
        atlatır. Baz artık iki CANLI fiyatın farkıdır; mum kapanışı yalnız
        chandelier SEVİYESİNİ üretir (`raw_stop`), bazı DEĞİL.

        İlk sürümdeki statik baz (`position.entry_price − signal.entry_price`)
        iki yerde kırılıyordu:
          1. yalnız GİRİŞ anında ölçülüp pozisyon ömrü boyunca sabit
             uygulanıyordu — iki defter arasındaki baz saatler içinde kayar;
          2. `recover()` (restart) `signal.entry_price` ile
             `position.entry_price`'ı AYNI değerden (`trade.entry_price`)
             kurduğu için baz 0 çıkıyordu → düzeltme sessizce no-op oluyordu
             (DB'de sinyal-anı fiyatı kolonu yok). Dinamik baz her turda
             yeniden ölçüldüğü için restart'ta ek alana/migrasyona GEREK YOK.

        `executor._delay_adjusted_stop` ile DESEN aynıdır (mutlak seviyeyi
        ötele, MESAFEYİ koru), ama referansları farklıdır ve olmalıdır:
        oradaki öteleme TEK SEFERLİK bir gecikme telafisidir (sinyal anı →
        gerçek dolum, aynı host) ve koruma tarafını GİRİŞ fiyatına göre
        denetler; buradaki öteleme SÜREKLİ bir borsa-arası baz çevirisidir ve
        koruma tarafı GÜNCEL fiyata göre denetlenir (pozisyon çoktan açık,
        piyasa girişten uzaklaşmış olabilir).

        Dönüş `None` = çeviri güvenilir DEĞİL (bayat/eksik işlem fiyatı, veri
        host'u fiyatı okunamadı, absürt baz): çağıran turu atlar, borsadaki SL
        yerinde kalır — fail-closed.
        Aynı host'ta (varsayılan, ayar boş) hiç uygulanmaz: `price` aynen döner
        ve `data_reference` OKUNMAZ (byte-for-byte no-op).
        """
        if not self._market_data_is_separate():
            return price
        if price <= 0:
            return price
        trading_ref = float(getattr(sp.position, "current_price", 0.0) or 0.0)
        data_ref = float(data_reference or 0.0)
        if trading_ref <= 0 or data_ref <= 0:
            return None
        if not self._trading_price_is_fresh(symbol):
            return None
        basis = trading_ref - data_ref
        if abs(basis) > trading_ref * (_MAX_PRICE_BASIS_PCT / 100.0):
            return None
        adjusted = price + basis
        if adjusted <= 0:
            return None
        return adjusted

    async def _data_host_price(self, symbol: str) -> Optional[float]:
        """VERİ host'unun CANLI fiyatı — borsa-arası bazın veri tarafı (D17-R3).

        Yalnız ayrı market-data host'unda çağrılır; aynı host'ta (varsayılan)
        `None` döner ve çağıran zaten hiç sormaz.

        Ağırlık: `/fapi/v1/ticker/price` TEK sembolde 1'dir ve `KlineFetcher`
        önbelleği sembol başına tur başına en fazla bir istek bırakır (TTL =
        safety turu). Hesap: `docs/ARCHITECTURE.md` §"Kline ağırlık bütçesi".

        Hata (ban/bütçe/ağ/geçersiz sembol) → `None`: çeviri yapılamaz,
        `_update_trailing` turu atlar ve borsadaki SL yerinde kalır
        (fail-closed, mevcut davranışın aynısı). Host GENELİ bir kesinti
        turun kalanını da susturur (`_market_data_down_reason`).
        """
        fetch = getattr(self, "data_price_fetch", None)
        if fetch is None:
            return None
        try:
            price = float(await fetch(symbol) or 0.0)
        except MarketDataUnavailable as e:
            # Host geneli: turun kalanında trailing atlanır, TEK satır log.
            self._market_data_down_reason = str(e)
            self.logger.warning(
                f"⛔ Piyasa verisi fiyatı kullanılamıyor ({e}); bu safety "
                f"turunda trailing güncellemesi atlandı ({symbol} ve kalan "
                f"semboller)"
            )
            return None
        except Exception as e:
            self._log_data_price_error(
                symbol,
                f"⚠️ {symbol}: veri host'unun canlı fiyatı okunamadı ({e}); "
                f"baz ölçülemez, trailing güncellemesi atlandı",
            )
            return None
        return price if price > 0 else None

    def _log_data_price_error(self, symbol: str, message: str) -> None:
        """Sembol başına oran-sınırlı WARNING (safety turu 2 sn'de bir döner)."""
        log_at = getattr(self, "_data_price_error_log_at", None)
        if log_at is None:
            log_at = {}
            self._data_price_error_log_at = log_at
        now = time.monotonic()
        if now - log_at.get(symbol, 0.0) < _TRAILING_SKIP_LOG_INTERVAL_SECONDS:
            return
        log_at[symbol] = now
        self.logger.warning(message)

    def _trading_price_is_fresh(self, symbol: str) -> bool:
        """`sp.position.current_price` bu turda işlem host'undan tazelendi mi?

        Bayat bir işlem fiyatını taze bir veri fiyatından çıkarmak SAHTE bir
        baz üretir; en tehlikeli hâli `recover()` sonrası ilk turdur (orada
        `current_price` = giriş fiyatıdır, saatler önceki bir değer olabilir).
        """
        seen_at = getattr(self, "_trading_price_seen_at", None)
        if not isinstance(seen_at, dict):
            return False
        stamp = seen_at.get(symbol)
        if stamp is None:
            return False
        return (time.monotonic() - float(stamp)) <= _TRADING_PRICE_MAX_AGE_SECONDS

    @staticmethod
    def _is_protective_side(
        direction: Direction, new_stop: float, current_price: float
    ) -> bool:
        """Stop, güncel fiyatın KORUMA tarafında ve pay kadar uzakta mı?

        LONG stop güncel fiyatın ALTINDA, SHORT stop ÜSTÜNDE olmalıdır; aksi
        halde Binance emri -2021 ("Order would immediately trigger") ile
        reddeder ve `position_manager._replace_stop_loss` bunu bir çıkış
        kararı sayıp pozisyonu PİYASA emriyle kapatır.
        """
        if current_price <= 0 or new_stop <= 0:
            return False
        margin = _PROTECTIVE_GATE_MARGIN_PCT / 100.0
        if direction == Direction.LONG:
            return new_stop < current_price * (1.0 - margin)
        return new_stop > current_price * (1.0 + margin)

    def _log_trailing_skip(self, symbol: str, message: str) -> None:
        """Sembol başına oran-sınırlı WARNING (safety turu 2 sn'de bir döner)."""
        log_at = getattr(self, "_trailing_skip_log_at", None)
        if log_at is None:
            log_at = {}
            self._trailing_skip_log_at = log_at
        now = time.monotonic()
        last = log_at.get(symbol, 0.0)
        if now - last < _TRAILING_SKIP_LOG_INTERVAL_SECONDS:
            return
        log_at[symbol] = now
        self.logger.warning(message)

    def trailing_skip_snapshot(self) -> Dict[str, int]:
        """Teşhis: fiyat-uzayı/koruma-kapısı yüzünden atlanan güncellemeler."""
        return {
            "price_space_skips": int(getattr(self, "_trailing_space_skips", 0) or 0),
            "protective_gate_skips": int(
                getattr(self, "_trailing_gate_skips", 0) or 0
            ),
            # D22: koruyucu stop -2021 aldı → acil kapanış (TRAIL_MARKET /
            # BE_MARKET). Artıyorsa stop kararı piyasa hızının gerisinde
            # kalıyordur. Bot kendiliğinden piyasa emri GÖNDERMEZ.
            "market_exits": int(getattr(self, "_trailing_market_exits", 0) or 0),
        }

    async def _apply_stop(
        self, position: Any, new_stop: float
    ) -> "StopReplaceResult":
        """`pm.replace_stop_loss` çağrısının YAPILANDIRILMIŞ sonucu (D22).

        Geriye uyumlu: `pm` yalnız eski `replace_stop_loss`i sunuyorsa (eski
        kurulumlar, SimpleNamespace test çiftleri) bool sonuç aynı sözleşmeye
        sarılır — davranış değişmez, yalnız "neden başarısız oldu" bilgisi
        `unknown` kalır.
        """
        structured = getattr(self.pm, "replace_stop_loss_result", None)
        if callable(structured):
            result = await structured(position, new_stop)
            if isinstance(result, StopReplaceResult):
                return result
            return StopReplaceResult(bool(result), "replaced" if result else "unknown")
        ok = bool(await self.pm.replace_stop_loss(position, new_stop))
        return StopReplaceResult(ok, "replaced" if ok else "unknown")

    async def _update_trailing(self, symbol: str, sp: ScalpPosition) -> None:
        if self._market_data_down_reason is not None:
            # Bu turda host geneli kesinti zaten raporlandı; sembol sembol
            # tekrar denemek ne veri getirir ne de log değeri katar.
            return
        try:
            candles = await self.kline_fetch(
                symbol, str(getattr(self.cfg, "scalper_tf_entry", "5m") or "5m"), 200
            )
        except MarketDataUnavailable as e:
            # Host geneli (ban/ağırlık bütçesi): turun kalanında trailing
            # atlanır, TEK satır loglanır. SL/TP borsada yerinde durur.
            self._market_data_down_reason = str(e)
            self.logger.warning(
                f"⛔ Piyasa verisi kullanılamıyor ({e}); bu safety turunda "
                f"trailing güncellemesi atlandı ({symbol} ve kalan semboller)"
            )
            return
        except Exception as e:
            self.logger.warning(f"⚠️ {symbol}: trailing için mum verisi alınamadı ({e}), tur atlanıyor")
            return

        if not candles:
            return

        since_index = len(candles) - 1
        for i, c in enumerate(candles):
            if c.close_time > sp.entry_candle_time:
                since_index = i
                break

        direction = sp.signal.direction
        try:
            raw_stop = chandelier_stop(
                candles,
                direction=direction,
                # Kademeli gevşeyen iz: tepe ROI büyüdükçe çarpan büyür
                # (bkz. types.resolve_trail_mult — backtest ile paritedir).
                atr_mult=resolve_trail_mult(self.cfg, sp.mfe_pct),
                atr_period=self.cfg.scalper_chandelier_atr_period,
                since_index=since_index,
            )
        except Exception as e:
            self.logger.error(f"❌ {symbol}: chandelier hesaplanamadı ({e})")
            return

        if raw_stop == 0.0:
            # indicators.chandelier_stop yetersiz veride 0.0 döner — "hesaplanamadı"
            # anlamına gelir, gerçek fiyat DEĞİLDİR. Bu turda güncelleme yapılmaz.
            self.logger.debug(f"{symbol}: chandelier için yetersiz veri, trailing bu turda atlandı")
            return

        # D17: mumlar ayrı bir borsadan geliyorsa seviyeyi İŞLEM borsasının
        # fiyat uzayına taşı (aynı host'ta no-op). Baz DİNAMİK ve
        # LIKE-FOR-LIKE'tır (D17-R3): İKİ host'un da CANLI fiyatı. Veri
        # host'unun fiyatı ancak ayrı host'ta ve ancak bu noktada istenir —
        # aynı host'ta ek istek YOKTUR (`data_reference` okunmaz bile).
        if self._market_data_is_separate():
            data_reference = await self._data_host_price(symbol)
        else:
            data_reference = candles[-1].close   # okunmaz; no-op yolunda ölü
        translated = self._to_trading_price_space(
            sp, raw_stop, data_reference or 0.0, symbol
        )
        if translated is None:
            # Baz güvenilir ölçülemedi (bayat işlem fiyatı / veri host'u
            # fiyatı okunamadı / absürt fark): YABANCI uzaydan emir
            # göndermektense turu atla — borsadaki SL yerinde kalır
            # (fail-closed).
            self._trailing_space_skips = (
                int(getattr(self, "_trailing_space_skips", 0) or 0) + 1
            )
            self._log_trailing_skip(
                symbol,
                f"⚠️ {symbol}: piyasa verisi/işlem fiyatı arasındaki baz "
                f"ölçülemedi (ayrı host); trailing güncellemesi atlandı, "
                f"eski SL korunuyor",
            )
            return
        raw_stop = translated

        floor = self._active_runner_floor(sp)
        current_sl = sp.position.current_stoploss or floor
        if direction == Direction.LONG:
            new_stop = max(floor, raw_stop)
            should_update = new_stop > current_sl * 1.0005
        else:
            new_stop = min(floor, raw_stop)
            should_update = new_stop < current_sl * 0.9995

        if not should_update:
            return

        # KORUMA-TARAFI KAPISI (YALNIZ ayrı market-data host'unda — D17):
        # seviye işlem host'unun GÜNCEL fiyatına göre yanlış taraftaysa emri
        # hiç gönderme. Oradaki yanlış taraf borsalar arası BAZ hatası
        # olabilir; kârlı bir koşucuyu ölçüm hatası yüzünden kapatmak
        # yanlıştır — tur atlanır ve borsadaki SL yerinde kalır.
        #
        # AYNI host'ta kapı UYGULANMAZ (D22 daraltması, 12-ajan incelemesi):
        # gönderilmeyen emir yerine ÖNDEN piyasa kapanışı yapmak, botun
        # kendi fiyat okumasına dayanarak geri alınamaz bir emir göndermesi
        # demekti. Stop borsaya gönderilir; Binance -2021 derse mevcut
        # `position_manager._emergency_close` (D22 ÖNCESİ de vardı) çalışır
        # ve kapanış aşağıda DÜRÜST etiketiyle deftere yazılır.
        if self._market_data_is_separate() and not self._is_protective_side(
            direction, new_stop, float(sp.position.current_price or 0.0)
        ):
            self._trailing_gate_skips = (
                int(getattr(self, "_trailing_gate_skips", 0) or 0) + 1
            )
            self._log_trailing_skip(
                symbol,
                f"⚠️ {symbol}: hesaplanan trailing SL ({new_stop}) işlem "
                f"host'unun güncel fiyatına ({sp.position.current_price}) göre "
                f"koruma tarafında değil; emir GÖNDERİLMEDİ, eski SL "
                f"({current_sl}) korunuyor",
            )
            return

        result = await self._apply_stop(sp.position, new_stop)
        if result.ok:
            sp.position.current_stoploss = new_stop
            # D21 zaman çizgisi (yalnız gözlem): kaç kez ve nereye çekildi.
            try:
                sp.trail_updates = int(getattr(sp, "trail_updates", 0) or 0) + 1
                sp.last_trail_stop = new_stop
            except Exception:  # pragma: no cover - SimpleNamespace fixture'ları
                pass
            self.logger.info(f"📈 {symbol}: chandelier trailing SL güncellendi -> {new_stop}")
        elif result.outcome == "emergency_closed":
            # Seviye borsaya varmadan piyasa tarafından geçildi (-2021) ve
            # `position_manager` pozisyonu ACİL KAPATTI. "Eski SL korunuyor"
            # demek burada düpedüz yanlış olurdu: pozisyon YOK.
            await self._on_emergency_closed(
                symbol,
                sp,
                result,
                exit_reason=EXIT_REASON_TRAIL_MARKET,
                what=f"trailing SL ({new_stop})",
            )
        elif result.outcome == "no_position":
            self.logger.debug(
                f"{symbol}: trailing güncellenmedi — borsada pozisyon kalmamış"
            )
        else:
            self.logger.warning(f"⚠️ {symbol}: trailing SL güncellenemedi, eski SL korunuyor")

    async def _on_emergency_closed(
        self,
        symbol: str,
        sp: ScalpPosition,
        result: "StopReplaceResult",
        *,
        exit_reason: str,
        what: str,
    ) -> None:
        """Koruyucu stop -2021 aldı → pozisyon acil kapatıldı: DÜRÜST kayıt.

        D22'nin tek davranışsal iddiası budur: kapanış ZATEN olmuştur
        (`position_manager._emergency_close`, D22 öncesinden beri). Burada
        yapılan üç şey de yalnız DOĞRU KAYITTIR:
          1. log "acil kapanış gerçekleşti" der — ASLA "eski SL korunuyor",
          2. kapanışı yapan emrin kimliği/fiyatı `sp`ye yazılır (defter
             kapanış fiyatını tahmin etmesin),
          3. etiket (`TRAIL_MARKET`/`BE_MARKET`) `sp`ye ÇİVİLENİR ve kapanış
             finalize edilir. Etiket, finalize bu turda başarısız olsa bile
             kaybolmaz — sonraki safety turu aynı etiketi kullanır.
        """
        self._trailing_market_exits = (
            int(getattr(self, "_trailing_market_exits", 0) or 0) + 1
        )
        self._note_market_close(sp, result, exit_reason)
        self.logger.warning(
            f"🔻 {symbol}: {what} piyasa tarafından geçilmiş (-2021); "
            f"ACİL KAPANIŞ GERÇEKLEŞTİ (reduce-only MARKET) — eski SL "
            f"KORUNMUYOR, defterde {exit_reason}",
            extra={"trade": True},
        )
        await self._finalize_market_exit(symbol, sp, exit_reason=exit_reason)

    @staticmethod
    def _note_market_close(
        sp: ScalpPosition, result: Any, exit_reason: str
    ) -> None:
        """Acil kapanışın kimliğini/fiyatını ve etiketini `sp`ye çivile.

        `pending_exit_reason` ETİKET SİGORTASIDIR: finalize bu turda
        doğrulanamazsa (borsa hâlâ miktar gösteriyor, -2022 yarışı, REST
        hatası) kapanışı sonraki turda hangi yol yakalarsa yakalasın deftere
        AYNI etiket yazılır. Etiketin kaybolması, D22'nin düzeltmek için var
        olduğu kusurun ta kendisidir.
        """
        try:
            sp.pending_exit_reason = exit_reason
            order_id = getattr(result, "close_order_id", None)
            if order_id:
                sp.market_close_order_id = str(order_id)
            price = getattr(result, "close_price", None)
            if price:
                sp.market_close_price = float(price)
        except Exception:  # pragma: no cover - SimpleNamespace fixture'ları
            pass

    async def _finalize_market_exit(
        self, symbol: str, sp: ScalpPosition, *, exit_reason: str
    ) -> None:
        """-2021 acil kapanışı sonrası kapanışı deftere doğru etiketle yaz.

        **İKİNCİ BİR MARKET EMRİ GÖNDERİLMEZ.** Pozisyonu kapatan emir zaten
        `position_manager._emergency_close` tarafından gönderildi; buradan
        bir kapanış daha göndermek (D22'nin ilk hâli) `-2022 ReduceOnly
        rejected` yarışı üretir ve en kötü hâlde ters yönde emir riski taşır.
        Yapılan tek şey borsadan FLAT DOĞRULAMASI (`force_fresh`) ve
        `_handle_closed` çağrısıdır — koruma emirlerinin iptali de oradadır,
        yani doğrulanamayan kapanışta SL/TP'ye DOKUNULMAZ (fail-closed).

        Doğrulanamazsa etiket `sp.pending_exit_reason`da durur: bir sonraki
        safety turu `positionAmt==0` gördüğünde `_handle_closed` aynı etiketi
        kullanır.
        """
        for delay in (0.0, 0.3, 0.6, 1.0, 2.0):
            if delay:
                await asyncio.sleep(delay)
            try:
                pos_info = await self.client.get_position_risk(
                    symbol, force_fresh=True
                )
            except Exception as e:
                self.logger.warning(
                    f"⚠️ {symbol}: acil kapanış sonrası pozisyon doğrulanamadı "
                    f"({e}); etiket {exit_reason} korunuyor, sonraki tur "
                    f"kapanışı işleyecek"
                )
                return
            amt = abs(float(pos_info.get("positionAmt", 0) or 0)) if pos_info else 0.0
            if amt == 0:
                await self._handle_closed(
                    symbol, sp, forced_exit_reason=exit_reason
                )
                return

        self.logger.error(
            f"🚨 {symbol}: acil kapanış sonrası borsa hâlâ pozisyon gösteriyor; "
            f"koruma emirleri iptal EDİLMEDİ (fail-closed). Etiket "
            f"{exit_reason} korunuyor — sonraki safety turu kapanışı işleyecek."
        )

    # `breakeven_side_ok`ın kabul ettiği azami fiyat yaşı (sn). Safety turu
    # saniyeler mertebesindedir; 30 sn, borsa okumalarında repo genelinde
    # kullanılan tazelik eşiğidir (bkz. D10 dersi #2 `force_fresh`).
    _BE_PRICE_MAX_AGE_S = 30.0

    def breakeven_side_ok(self, symbol: str) -> Optional[bool]:
        """BE hedefi piyasanın KORUYUCU tarafında mı? (D19a bulgu B)

        Dönüş:
          * `True`  → stop BE'ye çekilebilir (pozisyon kârda, pay dahil).
          * `False` → BE piyasanın TERS tarafında (pozisyon ZARARDA). Böyle
            bir stopu göndermek Binance'ten `-2021 Order would immediately
            trigger` alır; `position_manager._replace_stop_loss` bunu
            "koruma kararı" sayıp pozisyonu **ACİL KAPATIR**. Yani "yalnız
            stop sıkışır, geri alınabilir" sanılan `SCALPER_TV_EVENTS_EXIT=be`
            fiilen piyasa emriyle kapanışa dönüşürdü. ASLA denenmemeli.
          * `None`  → bilinmiyor (fiyat ya da BE okunamadı) → yine
            denenmemeli (fail-safe: eksik veriyle geri alınamaz emir yok).

        Pay: `SCALPER_TV_EVENTS_BE_MARGIN_PCT` (varsayılan %0.05) — tick/
        spread gürültüsünde sınıra teğet geçen fiyat "kârda" sayılmasın.
        """
        sp = self._positions.get(symbol)
        if sp is None:
            return None
        try:
            target = float(getattr(sp.plan, "breakeven_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if target <= 0.0:
            return None
        price = getattr(sp.position, "current_price", None)
        try:
            price = float(price or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0.0:
            return None
        # TAZELİK (D19a-2): `current_price` yalnız `get_current_price`
        # BAŞARILI olduğunda yazılır (`step` → `if current_price:`); ticker
        # birkaç tur hata verirse alan sessizce bayatlar. Damgasız ya da
        # `_BE_PRICE_MAX_AGE_S`'ten eski fiyat "bilinmiyor"dur.
        stamp = getattr(sp, "price_ts", None)
        if stamp is None:
            return None
        try:
            age = time.monotonic() - float(stamp)
        except (TypeError, ValueError):
            return None
        if age > self._BE_PRICE_MAX_AGE_S:
            return None
        try:
            margin_pct = float(
                getattr(
                    getattr(self, "cfg", None),
                    "scalper_tv_events_be_margin_pct",
                    0.05,
                )
                or 0.0
            )
        except (TypeError, ValueError):
            margin_pct = 0.05
        margin = abs(target) * max(0.0, margin_pct) / 100.0
        if sp.signal.direction == Direction.LONG:
            return price > target + margin
        return price < target - margin

    def breakeven_would_act(self, symbol: str) -> bool:
        """`force_breakeven` BORSAYA BİR EMİR GÖNDERİR miydi? (yan etkisiz)

        `force_breakeven`ın kapılarını (izlenen pozisyon → `_closing` kilidi →
        geçerli BE hedefi → "stop zaten en az BE kadar koruyucu" → zarar
        kontrolü) AYNI SIRAYLA, ama hiçbir şey değiştirmeden uygular.
        Gölge modunun "aktifte ne olurdu" tahmini bunu kullanır (D19a-2 R2-4):
        aksi halde gölge sayacı, aktifte hiçbir isteğin gitmeyeceği olayları
        da "çıkış olurdu" diye sayıp terfi kararını şişirirdi.
        """
        sp = self._positions.get(symbol)
        if sp is None:
            return False
        closing = getattr(self, "_closing", None)
        if closing and symbol in closing:
            return False
        try:
            target = float(getattr(sp.plan, "breakeven_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        if target <= 0.0:
            return False
        if self._is_at_least_as_protective(
            sp.signal.direction, sp.position.current_stoploss, target
        ):
            return False
        return self.breakeven_side_ok(symbol) is True

    async def force_breakeven(self, symbol: str, *, reason: str) -> bool:
        """Dış tetikleyiciyle (TV olayı, D19) stopu ücret-dahil BE'ye çek.

        MEVCUT BE mekanizmasını kullanır — `_check_tp1` ile AYNI hedef
        (`sp.plan.breakeven_price`), AYNI gevşetme koruması
        (`_is_at_least_as_protective`) ve AYNI boşluksuz emir yolu
        (`pm.replace_stop_loss`: önce yeni reduceOnly SL, sonra eskisi
        iptal). YENİ BİR EMİR YOLU YAZILMADI.

        BİLİNÇLİ SINIR — `tp1_done`/`trailing_active` DEĞİŞTİRİLMEZ:
          * `trailing_active=True` yapmak pozisyonu reaper'dan MUAF kılardı
            (D4) ve chandelier izini TP1 dolmadan başlatırdı; ikisi de bu
            değişikliğin kapsamı dışında sessiz davranış değişiklikleridir.
          * TP1 sonradan gerçekten dolarsa `_check_tp1` zaten `already_
            tighter` yolundan geçer (fazladan emir göndermez) ve bayrakları
            normal akışta kurar.
        Yani buradaki tek etki: stop SIKILAŞIR, asla gevşemez.

        ZARARDA UYGULANMAZ (D19a bulgu B): BE piyasanın ters tarafındaysa
        `breakeven_side_ok` False/None döner ve stop TAŞINMAZ — aksi halde
        Binance -2021 → `position_manager._emergency_close` (piyasa emriyle
        ACİL KAPANIŞ) tetiklenirdi. Zarardaki pozisyona ne yapılacağı
        `SCALPER_TV_EVENTS_EXIT_LOSING` kararıdır (skip | close).

        Dönüş: stop BE'de mi (zaten öyleyse de True), değiştirilemediyse False.
        """
        sp = self._positions.get(symbol)
        if sp is None:
            return False
        closing = getattr(self, "_closing", None)
        if closing and symbol in closing:
            # Tek-finalizer kilidi (D10 dersi): kapanış işlenirken SL
            # değiştirmek, iptal edilmek üzere olan emirle yarışır.
            self.logger.debug(f"{symbol}: kapanış işleniyor, BE tetiği atlandı")
            return False
        try:
            target = float(getattr(sp.plan, "breakeven_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            target = 0.0
        if target <= 0.0:
            self.logger.warning(
                f"⚠️ {symbol}: BE fiyatı yok/geçersiz — {reason} için stop taşınamadı"
            )
            return False

        current_sl = sp.position.current_stoploss
        if self._is_at_least_as_protective(sp.signal.direction, current_sl, target):
            self.logger.info(
                f"🛡️ {symbol}: {reason} — stop zaten BE'de veya daha koruyucu "
                f"(SL={current_sl}), değişiklik yok"
            )
            return True

        # D19a bulgu B — ZARARDA BE YASAK. Bu kontrol çağıranda da vardır
        # (engine `_apply_tv_event_exits` skip/close politikasını uygular);
        # burada ikinci kez yapılır çünkü -2021 → acil kapanış yolunun
        # tetiklenmesi geri alınamaz ve tek bir çağıranın unutması yeterlidir.
        side_ok = self.breakeven_side_ok(symbol)
        if side_ok is not True:
            self.logger.warning(
                f"⚠️ {symbol}: {reason} — BE ({target}) piyasanın koruyucu "
                f"tarafında DEĞİL (fiyat={getattr(sp.position, 'current_price', None)}, "
                f"durum={'zararda' if side_ok is False else 'bilinmiyor'}); stop "
                "taşınmadı (-2021 → acil kapanış riski)"
            )
            return False

        result = await self._apply_stop(sp.position, target)
        if result.ok:
            sp.position.current_stoploss = target
            self.logger.info(
                f"🛡️ {symbol}: {reason} — SL ücret-dahil BE'ye çekildi (SL={target})",
                extra={"trade": True},
            )
        elif result.outcome == "emergency_closed":
            # D22: yukarıdaki `breakeven_side_ok` kapısına RAĞMEN yarış
            # kaybedildi. Pozisyon acil kapatıldı; "eski SL korunuyor"
            # yazmak defteri kirletirdi.
            await self._on_emergency_closed(
                symbol,
                sp,
                result,
                exit_reason=EXIT_REASON_BE_MARKET,
                what=f"{reason} — BE ({target})",
            )
        elif result.outcome == "no_position":
            self.logger.info(
                f"ℹ️ {symbol}: {reason} — BE taşınmadı, borsada pozisyon kalmamış"
            )
        else:
            self.logger.warning(
                f"⚠️ {symbol}: {reason} — SL BE'ye taşınamadı, eski SL korunuyor"
            )
        return bool(result.ok)

    @staticmethod
    def _active_runner_floor(sp: ScalpPosition) -> float:
        """BE tabanı; TP2 gerçek fill sonrası en az TP1 seviyesine yükselir."""
        breakeven = sp.plan.breakeven_price
        if not sp.tp2_done:
            return breakeven
        runner_floor = sp.plan.runner_floor_price or sp.plan.tp1_price
        if sp.signal.direction == Direction.LONG:
            return max(breakeven, runner_floor)
        return min(breakeven, runner_floor)

    async def force_stop_to(
        self, symbol: str, sp: ScalpPosition, new_stop: float, *, reason: str
    ) -> bool:
        """Stop'u verilen seviyeye taşı (boşluksuz `replace_stop_loss` yolu).

        Yapı-tabanlı çıkış (`engine._apply_structure_exits`, D18 adayı) için
        eklendi. KARAR burada verilmez — çağıran, saf `structure_exit_action`
        fonksiyonundan aldığı kararı uygular; bu metot yalnız emir yolunu
        paylaşır (yeni bir emir yolu yazılmadı, `_update_trailing` ile aynı
        `pm.replace_stop_loss`). Seviye geçersizse (<=0) hiçbir şey yapmaz.
        """
        if not new_stop or new_stop <= 0:
            return False
        result = await self._apply_stop(sp.position, new_stop)
        if result.ok:
            sp.position.current_stoploss = new_stop
            self.logger.info(f"🛡️ {symbol}: SL {new_stop} seviyesine çekildi ({reason})")
        elif result.outcome == "emergency_closed":
            await self._on_emergency_closed(
                symbol,
                sp,
                result,
                exit_reason=EXIT_REASON_TRAIL_MARKET,
                what=f"{reason} — stop ({new_stop})",
            )
        elif result.outcome == "no_position":
            self.logger.info(
                f"ℹ️ {symbol}: SL {new_stop} seviyesine çekilmedi ({reason}), "
                f"borsada pozisyon kalmamış"
            )
        else:
            self.logger.warning(
                f"⚠️ {symbol}: SL {new_stop} seviyesine çekilemedi ({reason}), eski SL korunuyor"
            )
        return bool(result.ok)

    async def _handle_closed(
        self,
        symbol: str,
        sp: ScalpPosition,
        *,
        forced_exit_reason: Optional[str] = None,
    ) -> None:
        """Pozisyon kapanışını kaydet.

        `forced_exit_reason`: normal (SL/TP/trailing) kapanışlarda kullanılmaz
        (None); risk-olayı `flatten` gibi harici tetikleyicilerin kapanış
        NEDENİNİ (örn. "RISK_EVENT") zorlaması için — fiyat/PnL doğrulaması
        (`_verified_close_ledger`/`_fetch_net_income`) DEĞİŞMEDEN aynen
        çalışır, yalnız etiketlenen `exit_reason` üzerine yazılır.

        D22 ETİKET SİGORTASI: verilmediyse `sp.pending_exit_reason` okunur.
        Orada bir değer varsa pozisyon -2021 sonrası ACİL KAPATILMIŞ ama o
        turda flat doğrulanamamıştır; kapanışı hangi yol yakalarsa yakalasın
        deftere aynı (`TRAIL_MARKET`/`BE_MARKET`) etiket yazılmalıdır.
        """
        if forced_exit_reason is None:
            pending = getattr(sp, "pending_exit_reason", None)
            if pending:
                forced_exit_reason = str(pending)
        # SimpleNamespace/object.__new__ fixture'larında alan bulunmayabilir
        # (repo konvansiyonu: hasattr yerine getattr(..., None)).
        if getattr(sp, "close_recorded", False):
            # ARDIŞIK mükerrer finalize kalkanı (`_closing` yalnız EŞZAMANLI
            # olanı tutar): bu pozisyonun kapanışı zaten deftere yazıldı.
            # İkinci bir `record_close` exit_reason'ı ÜZERİNE YAZAR.
            self.logger.debug(
                f"{symbol}: kapanış zaten deftere yazılmış, mükerrer finalize atlandı"
            )
            return
        closing = getattr(self, "_closing", None)
        if closing is None:
            closing = set()
            self._closing = closing
        if symbol in closing:
            # Zaten başka bir yol (safety turu / flatten) bu pozisyonu
            # finalize ediyor: ikinci kez cancel_all + userTrades + income
            # çekmek REST weight'i ikiye katlar ve record_close'u ÜZERİNE
            # yazar (exit_reason kaybı, close_seq çift artışı).
            self.logger.debug(
                f"{symbol}: kapanış zaten işleniyor, mükerrer finalize atlandı"
            )
            return
        closing.add(symbol)
        try:
            await self._finalize_close(
                symbol, sp, forced_exit_reason=forced_exit_reason
            )
            try:
                sp.close_recorded = True
            except Exception:  # pragma: no cover - SimpleNamespace fixture'ları
                pass
        finally:
            closing.discard(symbol)
            # KİMLİK kontrolü: `_finalize_close` saniyeler sürer (cancel_all +
            # userTrades + income merdiveni). O sırada başka bir yol AYNI sembol
            # için YENİ bir pozisyon izlemeye almış olabilir (takipçi halkasının
            # flip yolu: AlgoPro ters sinyali eski pozisyonu kapatıp yenisini
            # açar). Koşulsuz pop, yeni ve GERÇEK pozisyonu izleme listesinden
            # düşürür → TP1→BE hiç taşınmaz, kapanış defteri hiç yazılmaz,
            # kapasite sayacı boş slot gösterir. Yalnız KENDİ nesnesini düşür.
            if self._positions.get(symbol) is sp:
                self._positions.pop(symbol, None)

    async def _finalize_close(
        self,
        symbol: str,
        sp: ScalpPosition,
        *,
        forced_exit_reason: Optional[str] = None,
    ) -> None:
        try:
            await self.client.cancel_all_open_orders(symbol)
        except Exception as e:
            self.logger.warning(f"⚠️ {symbol}: artık emirler temizlenemedi ({e})")

        direction = sp.signal.direction
        entry = sp.position.entry_price
        qty = sp.position.quantity

        # SimpleNamespace fixture'larında (testler) bu alanlar hiç bulunmayabilir
        # — hasattr yerine getattr(..., None) kullanılır, fail-closed davranış
        # bu alanların yokluğunda da ledger'ı sessizce None'a düşürür.
        ledger = await self._verified_close_ledger(
            symbol=symbol,
            direction=direction,
            quantity=qty,
            entry_price=entry,
            opened_at=sp.position.opened_at,
            sl_order_id=getattr(sp.position, "sl_order_id", None),
            tp1_algo_id=getattr(sp.plan, "tp1_algo_id", None),
            tp2_algo_id=getattr(sp.plan, "tp2_algo_id", None),
            # Yalnız AlgoPro takipçi halkası (D20) doldurur; scalper'da DAİMA
            # None olduğu için aday listesi ve davranış birebir aynıdır.
            tp3_algo_id=getattr(sp.plan, "tp3_algo_id", None),
            trailing_active=sp.trailing_active,
            entry_fee_rate=float(getattr(sp.plan, "entry_fee_rate", 0.0) or 0.0),
        )

        market_close_price: Optional[float] = None
        if ledger is not None:
            exit_price = ledger.exit_price
        else:
            # D22: -2021 sonrası acil kapanışı YAPAN emir düz (algo DEĞİL)
            # bir reduce-only MARKET emridir; `_verified_close_ledger`
            # yalnız algo adaylarına (SL/TP1/TP2/TP3) baktığı için onu
            # GÖREMEZ. Fiyatı ticker'dan TAHMİN etmek yerine o emrin gerçek
            # dolumundan oku — income merdiveni aşağıda AYNEN çalışır.
            market_close_price = await self._market_close_exit_price(symbol, sp)
            exit_price = market_close_price
            if not exit_price:
                try:
                    exit_price = await self.client.get_current_price(symbol)
                except Exception:
                    pass
            if not exit_price:
                exit_price = sp.position.current_price or sp.position.entry_price

        estimated_gross = self._estimate_gross_pnl(direction, entry, exit_price, qty)
        income_net = await self._fetch_net_income(
            symbol=symbol,
            opened_at=sp.position.opened_at,
            entry_order_id=sp.position.entry_order_id,
        )

        verification_notes: List[str] = []
        if ledger is None:
            verification_notes.append(
                "exit_fill=market_close_order"
                if market_close_price
                else "exit_fill=unverified"
            )

        if income_net is not None:
            realized_pnl = income_net
            pnl_source = "binance_income_net"
        elif ledger is not None:
            realized_pnl = ledger.net_pnl_estimate
            pnl_source = "binance_trades_close_net"
            verification_notes.append("pnl=close_fills_net_entry_fee_estimated")
        else:
            realized_pnl = estimated_gross
            pnl_source = "estimated_gross"
            verification_notes.append("close_verification=unverified")

        # D27/A2 — ADLİ KAYIT İÇİN merdiven-farkında brüt. `estimated_gross`
        # PnL merdivenindeki rolünü (son çare `realized_pnl` kaynağı)
        # DEĞİŞTİRMEDEN korur; yalnız forensics'e giden brüt ayrışır.
        # SIRA: `pnl_source` BELİRLENDİKTEN sonra çağrılır — `_forensics_gross`
        # SAF bir fonksiyondur (IO yok), yer değiştirmesi motor kararlarını
        # ETKİLEMEZ; ama `pnl_source`u görmeden "komisyon = 0" uyduruyordu
        # (D27 incelemesi O1).
        forensics_gross, gross_source = self._forensics_gross(
            sp=sp,
            ledger=ledger,
            estimated_gross=estimated_gross,
            pnl_source=pnl_source,
        )

        exit_reason = (
            forced_exit_reason
            if forced_exit_reason is not None
            else (
                ledger.exit_reason
                if ledger is not None
                else self._infer_exit_reason(sp, exit_price, realized_pnl=realized_pnl)
            )
        )
        # D27/A1 PARİTE: kayıp-cooldown kapısı etiketi OKUR. Yeni "REAPER"
        # etiketi o kapıya SIZMAMALI — aksi hâlde yaş kesmesi cooldown
        # kararını (her iki yönde de) sessizce değiştirirdi. Bu satır kapıya
        # D27 ÖNCESİ etiket uzayını verir; deftere/adli kayda yazılan etiket
        # `exit_reason`dır. Bkz. `_infer_exit_reason_legacy` docstring'i.
        cooldown_reason = (
            forced_exit_reason
            if forced_exit_reason is not None
            else (
                ledger.exit_reason
                if ledger is not None
                else self._infer_exit_reason_legacy(
                    sp, exit_price, realized_pnl=realized_pnl
                )
            )
        )
        notes = ";".join(verification_notes) or None

        # D21 adli kayıt: "nasıl çıkıldı" + kural tabanlı etiketler. Hesap
        # hatası kapanışı ASLA engellemez (aşağıda try/except).
        forensics_exit, verdict = self._build_exit_forensics(
            symbol=symbol,
            sp=sp,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            gross_pnl=forensics_gross,
            gross_source=gross_source,
            pnl_source=pnl_source,
            exit_reason=exit_reason,
            verification_notes=verification_notes,
        )

        try:
            await self.tracker.record_close(
                trade_id=sp.trade_id,
                exit_price=exit_price,
                realized_pnl=realized_pnl,
                exit_reason=exit_reason,
                mae_pct=sp.mae_pct,
                mfe_pct=sp.mfe_pct,
                pnl_source=pnl_source,
                notes=notes,
                forensics_exit=forensics_exit,
                verdict=verdict,
            )
        except Exception as e:
            self.logger.error(f"❌ {symbol}: kapanış kaydı yazılamadı (#{sp.trade_id}): {e}")

        if forensics_exit is not None:
            self._forensics_event(
                symbol=symbol,
                trade_id=sp.trade_id,
                exit_document=forensics_exit,
                verdict=verdict or [],
            )

        loss_threshold = (
            0.0
            if pnl_source == "binance_income_net"
            else self._estimated_roundtrip_fee(entry, exit_price, qty)
        )
        self._maybe_start_loss_cooldown(
            symbol, cooldown_reason, realized_pnl, loss_threshold
        )

        self.logger.info(
            f"🏁 Scalp pozisyon kapandı: {symbol} PNL={realized_pnl:.2f} "
            f"kaynak={pnl_source} neden={exit_reason}",
            extra={"trade": True},
        )

    # ------------------------------------------------------------------
    # İşlem adli kaydı (D21) — YALNIZ GÖZLEM, kapanışı ASLA engellemez
    # ------------------------------------------------------------------

    @staticmethod
    def _mark_path(sp: Any, field: str) -> None:
        """Zaman çizgisi damgası; İLK damga korunur (yeniden yazılmaz)."""
        try:
            if getattr(sp, field, None) is None:
                setattr(
                    sp,
                    field,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
        except Exception:  # pragma: no cover - fixture savunması
            pass

    def _forensics_enabled(self) -> bool:
        return bool(getattr(self.cfg, "scalper_forensics_enabled", True))

    def _forensics_warn(self, message: str) -> None:
        if not getattr(self, "_forensics_error_logged", False):
            self._forensics_error_logged = True
            self.logger.warning(
                f"⚠️ Adli kayıt (çıkış) kurulamadı ({message}) — bu uyarı bir "
                f"kez loglanır, kapanış akışı ETKİLENMEZ"
            )

    def _restore_forensics_entry(self, sp: ScalpPosition, trade: Any) -> None:
        """Restart kurtarmasında DB'deki `forensics.entry`'yi belleğe geri al.

        Düşmanca inceleme bulgusu 2 (D21-R3): `recover()` D21 damgalarını
        (`tp1_at`/`be_at`/`trail_updates`/`last_trail_stop`…) geri yüklemiyordu,
        bu yüzden restart sonrası kapanan bir işlemin ÇIKIŞ zaman çizgisi
        yanlış okunuyordu (0 trailing güncellemesi = "hiç trail olmadı" gibi)
        ve `verdict` yalnız çıkış etiketlerinden ibaret kalıyordu.

        Doğru davranış: DB'de KAYITLI olan (`entry`) bölüm geri yüklenir —
        bu gerçek bir ölçümdür. Yalnız BELLEKTE tutulan damgalar restart'ta
        gerçekten kaybolmuştur; onlar `null` kalır ve kayıt
        `path.restart_gap=true` ile bunu açıkça söyler. UYDURMA değer yazılmaz.

        `price_ts` KASITLI olarak geri yüklenmez: o bir karar-yolu tazelik
        damgasıdır (D19a-2, `breakeven_side_ok`); restart sonrası "taze"
        göstermek stopu piyasanın ters tarafına koydurabilir. Adli kayıtta da
        yeri yoktur.
        """
        try:
            document = ScalpTracker.parse_forensics(
                getattr(trade, "forensics", None)
            ) or {}
        except Exception as e:  # pragma: no cover - savunma
            self._forensics_warn(f"recover_parse {type(e).__name__}: {e}")
            document = {}
        sp.forensics_restart_gap = True
        entry_doc = document.get("entry")
        if isinstance(entry_doc, dict):
            sp.forensics_entry = entry_doc
        opened_at = getattr(trade, "opened_at", None)
        if opened_at is not None and sp.opened_epoch is None:
            try:
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                sp.opened_epoch = opened_at.timestamp()
            except Exception:  # pragma: no cover - savunma
                sp.opened_epoch = None

    #: D27/A2 — adli kayıttaki brütün KAYNAĞI (rapor "ölçülmedi" diyebilsin).
    GROSS_SOURCE_LEDGER = "ledger_legs"        # borsa fill'lerinden, merdiven dahil
    GROSS_SOURCE_SINGLE = "single_leg_estimate"  # tek çıkış fiyatı — merdiven YOK
    GROSS_SOURCE_UNMEASURED = "unmeasured_ladder"  # merdiven VAR ama ledger YOK
    #: D27 incelemesi (O1): NET de aynı tahminden geliyor (`estimated_gross`)
    #: → brüt ≡ net → komisyon FARKI 0 çıkar. Hiçbir şeyin doğrulanmadığı bu
    #: hâlde "komisyonu 0 ÖLÇTÜK" demek, A2'nin kendi kuralının ihlalidir.
    GROSS_SOURCE_SELF_REFERENTIAL = "self_referential_estimate"

    @staticmethod
    def _forensics_gross(
        *,
        sp: ScalpPosition,
        ledger: Optional[_CloseLedger],
        estimated_gross: Optional[float],
        pnl_source: Optional[str] = None,
    ) -> Tuple[Optional[float], str]:
        """Adli kayda yazılacak BRÜT PnL ve kaynağı — SAF, IO yok.

        Dört durum vardır ve dördü de AYRI raporlanır:

        1. **`ledger_legs`** — borsa `userTrades` satırları doğrulandı:
           brüt = Σ(realizedPnl), merdivenin (TP1/TP2/runner) her bacağı
           KENDİ fiyatından sayılır. Doğru olan budur.
        2. **`single_leg_estimate`** — ledger yok AMA pozisyon hiç kısmi
           dolum görmedi (`tp1_done`/`tp2_done` False, `trailing_active`
           False): kapanış TEK bacaktır, tek fiyatla hesaplanan brüt
           GEÇERLİDİR.
        3. **`unmeasured_ladder`** — ledger yok ve merdiven kısmen dolmuş:
           tek çıkış fiyatıyla hesaplanan brüt YANLIŞTIR (ölçüldü: 22
           işlemin 8'inde tahmini komisyon teorik değerin 2 katından fazla,
           5'inde NEGATİF). Bu hâlde brüt **`None`** bırakılır ve
           `fee_estimate` de `None` olur: **uydurma sayı YASAK**, rapor
           "ölçülemedi" der.

        4. **`self_referential_estimate`** — `pnl_source == "estimated_gross"`,
           yani NET de aynı tahminden geliyor. Brüt ≡ net olduğu için
           `fee_estimate = brüt − net = 0.0` çıkar ve adli kayıt "komisyonu
           SIFIR ölçtük" der. Hiçbir şeyin doğrulanmadığı tam da bu hâlde bu
           iddia yanlıştır (D27 incelemesi O1) → brüt **`None`**, komisyon
           `None`, rapor "ölçülemedi" der.

        `realized_pnl` (defter) bu fonksiyondan ETKİLENMEZ; `_finalize_close`
        PnL merdivenini (income → ledger net → brüt tahmin) aynen sürdürür.
        """
        if ledger is not None and ledger.gross_pnl is not None:
            return float(ledger.gross_pnl), ExitManager.GROSS_SOURCE_LEDGER
        laddered = bool(
            getattr(sp, "tp1_done", False)
            or getattr(sp, "tp2_done", False)
            or getattr(sp, "trailing_active", False)
        )
        if laddered:
            return None, ExitManager.GROSS_SOURCE_UNMEASURED
        if str(pnl_source or "") == "estimated_gross":
            return None, ExitManager.GROSS_SOURCE_SELF_REFERENTIAL
        return estimated_gross, ExitManager.GROSS_SOURCE_SINGLE

    def _build_exit_forensics(
        self,
        *,
        symbol: str,
        sp: ScalpPosition,
        exit_price: Any,
        realized_pnl: Any,
        gross_pnl: Any,
        pnl_source: Optional[str],
        exit_reason: Optional[str],
        verification_notes: List[str],
        gross_source: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[List[str]]]:
        """(çıkış sözlüğü, etiket listesi) — hata hâlinde (None, None)."""
        if not self._forensics_enabled():
            return None, None
        try:
            from src.strategies.scalper import forensics as fx

            now = time.time()
            opened_epoch = getattr(sp, "opened_epoch", None)
            opened_at = getattr(sp.position, "opened_at", None)
            if opened_epoch is None and opened_at is not None:
                opened_epoch = opened_at.replace(tzinfo=timezone.utc).timestamp()
            duration = None if opened_epoch is None else max(0.0, now - opened_epoch)

            context: Dict[str, Any] = {}
            if self._forensics_context_cb is not None:
                try:
                    context = dict(self._forensics_context_cb(symbol) or {})
                except Exception as e:
                    self._forensics_warn(f"context_cb {type(e).__name__}: {e}")

            entry_doc = getattr(sp, "forensics_entry", None)
            entry_doc = entry_doc if isinstance(entry_doc, dict) else None
            restart_gap = bool(getattr(sp, "forensics_restart_gap", False))

            path = {
                "tp1_at": getattr(sp, "tp1_at", None),
                "tp1_price": getattr(sp.plan, "tp1_price", None),
                "tp1_done": bool(getattr(sp, "tp1_done", False)),
                "tp2_at": getattr(sp, "tp2_at", None),
                "tp2_price": getattr(sp.plan, "tp2_price", None),
                "tp2_done": bool(getattr(sp, "tp2_done", False)),
                "be_at": getattr(sp, "be_at", None),
                "be_price": getattr(sp, "be_price", None),
                "trail_updates": int(getattr(sp, "trail_updates", 0) or 0),
                "last_trail_stop": getattr(sp, "last_trail_stop", None),
                "trailing_active": bool(getattr(sp, "trailing_active", False)),
                "initial_stop": getattr(sp.plan, "initial_stop", None),
                "final_stop": getattr(sp.position, "current_stoploss", None),
                "age_hours": None if duration is None else round(duration / 3600.0, 3),
            }
            if restart_gap:
                # Restart kurtarması: aşağıdaki damgalar YALNIZ bellekteydi ve
                # restart'ta kayboldu. `0` yazmak "hiç olmadı" der — bu bir
                # uydurmadır. Bilinmeyen = `null`, ve `restart_gap` bunun
                # nedenini söyler (D21-R3, bulgu 2).
                path["restart_gap"] = True
                if not path["trail_updates"]:
                    path["trail_updates"] = None
                # `plan.initial_stop` kurtarmada CANLI stop ile kurulur, ilk
                # stop DEĞİLDİR. Gerçek ilk stop DB'deki giriş belgesindedir.
                path["initial_stop"] = (entry_doc or {}).get("stop_price")

            exit_doc = fx.build_exit(
                at=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(
                    timespec="seconds"
                ),
                reason=exit_reason or "UNKNOWN",
                exit_price=exit_price,
                entry_price=sp.position.entry_price,
                quantity=sp.position.quantity,
                leverage=sp.position.leverage,
                direction=sp.signal.direction,
                realized_pnl=realized_pnl,
                gross_pnl=gross_pnl,
                gross_source=gross_source,
                pnl_source=pnl_source,
                mae_roi_pct=getattr(sp, "mae_pct", None),
                mfe_roi_pct=getattr(sp, "mfe_pct", None),
                # D27/A3: MAE yoklama sıklığı — düzeltmenin ne kadar kaba bir
                # örneklemeden geldiğini okuyan bilsin.
                mae_samples=getattr(sp, "mae_samples", None),
                duration_sec=duration,
                path=path,
                leader_day_drift_pct=context.get("leader_day_drift_pct"),
                regime=context.get("regime"),
                btc_price=context.get("btc_price"),
                verification_notes=verification_notes,
            )
            thresholds = fx.thresholds_from_cfg(self.cfg)
            verdict = fx.classify(entry_doc, exit_doc, thresholds)
            return exit_doc, verdict
        except Exception as e:
            self._forensics_warn(f"{type(e).__name__}: {e}")
            return None, None

    def _forensics_event(
        self,
        *,
        symbol: str,
        trade_id: int,
        exit_document: Dict[str, Any],
        verdict: List[str],
    ) -> None:
        """`logs/trades.jsonl`'e tek satır yaz (fail-safe, safety turunu bloklamaz).

        `append_soon` kuyruğa koyar; disk yazımı ayrı yazıcı iş parçacığında
        olur (D21-R3) — `_finalize_close` safety turunun ortasındadır.
        """
        try:
            from src.strategies.scalper import forensics_log

            forensics_log.append_soon(
                "exit",
                {
                    "trade_id": int(trade_id),
                    "symbol": str(symbol),
                    "verdict": list(verdict),
                    "exit": exit_document,
                },
            )
        except Exception as e:  # pragma: no cover - savunma
            self._forensics_warn(f"{type(e).__name__}: {e}")

    async def _market_close_exit_price(
        self, symbol: str, sp: ScalpPosition
    ) -> Optional[float]:
        """-2021 acil kapanışını yapan MARKET emrinin GERÇEK dolum VWAP'ı.

        Sıra: (1) emrin `userTrades` satırları — borsa kanıtı; (2) emir
        yanıtındaki `avgPrice`. İkisi de yoksa `None` döner ve çağıran eski
        tahmin yoluna düşer. Satırlarda en ufak anormallik varsa TÜM sonuç
        atılır (`_verified_close_ledger` ile aynı ilke: "kısmen doğrulanmış"
        diye bir şey yoktur).
        """
        raw_id = getattr(sp, "market_close_order_id", None)
        if raw_id:
            trades_getter = getattr(self.client, "get_account_trades", None)
            try:
                numeric_id: Optional[int] = int(raw_id)
            except (TypeError, ValueError):
                numeric_id = None
            if callable(trades_getter) and numeric_id and numeric_id > 0:
                rows: Any = None
                try:
                    rows = await trades_getter(symbol, order_id=numeric_id, limit=500)
                except Exception as exc:
                    self.logger.debug(
                        f"{symbol}: acil kapanış emri #{numeric_id} fill sorgusu "
                        f"başarısız ({exc}); kapanış fiyatı tahmine düşüyor"
                    )
                vwap = self._fill_vwap(rows)
                if vwap is not None:
                    return vwap
        try:
            fallback = float(getattr(sp, "market_close_price", None) or 0.0)
        except (TypeError, ValueError):
            fallback = 0.0
        return fallback if fallback > 0 else None

    @staticmethod
    def _fill_vwap(rows: Any) -> Optional[float]:
        """userTrades satırlarının miktar-ağırlıklı ortalama fiyatı (katı)."""
        if not isinstance(rows, list) or not rows:
            return None
        notional = 0.0
        quantity = 0.0
        for row in rows:
            if not isinstance(row, dict):
                return None
            raw_qty = row.get("qty")
            if raw_qty is None:
                raw_qty = row.get("quantity")
            try:
                qty = float(raw_qty)
                price = float(row.get("price"))
            except (TypeError, ValueError):
                return None
            if not math.isfinite(qty) or qty <= 0:
                return None
            if not math.isfinite(price) or price <= 0:
                return None
            notional += qty * price
            quantity += qty
        if quantity <= 0:
            return None
        return notional / quantity

    async def _verified_close_ledger(
        self,
        *,
        symbol: str,
        direction: Direction,
        quantity: float,
        entry_price: float,
        opened_at: Optional[datetime],
        sl_order_id: Optional[str],
        tp1_algo_id: Optional[str],
        tp2_algo_id: Optional[str],
        trailing_active: bool,
        entry_fee_rate: float,
        tp3_algo_id: Optional[str] = None,
    ) -> Optional[_CloseLedger]:
        """Kapanışı borsa userTrades satırlarıyla doğrula.

        GÜVENLİK İLKESİ: herhangi bir aday emrin fill satırı beklenmedik
        görünüyorsa (miktar, yön, komisyon varlığı, zaman penceresi, ...)
        TÜM sonuç atılır — "kısmen doğrulanmış" diye bir şey yoktur, belirsiz
        veri asla doğrulanmış sayılmaz. Bu durumda None döner ve çağıran
        income/tahmini kaynağa düşer.
        """
        algo_getter = getattr(self.client, "get_algo_order", None)
        trades_getter = getattr(self.client, "get_account_trades", None)
        if algo_getter is None or trades_getter is None:
            return None
        if opened_at is None:
            return None

        opened_utc = opened_at
        if opened_utc.tzinfo is None:
            opened_utc = opened_utc.replace(tzinfo=timezone.utc)
        else:
            opened_utc = opened_utc.astimezone(timezone.utc)
        opened_ms = int(opened_utc.timestamp() * 1000)
        window_start_ms = opened_ms - self.INCOME_ENTRY_LOOKBACK_SECONDS * 1000

        # LONG pozisyon SELL ile kapanır (buyer=False); SHORT BUY ile (buyer=True).
        expected_buyer = direction != Direction.LONG

        candidates: List[Tuple[str, int]] = []
        seen_ids: Set[int] = set()
        # TP3 yalnız takipçi halkasında (D20) doludur; scalper'da None geçer ve
        # aday listesi bugünküyle BİREBİR aynı kalır.
        for kind, raw_id in (
            ("SL", sl_order_id),
            ("TP1", tp1_algo_id),
            ("TP2", tp2_algo_id),
            ("TP3", tp3_algo_id),
        ):
            if not raw_id:
                continue
            try:
                numeric_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if numeric_id in seen_ids:
                continue
            seen_ids.add(numeric_id)
            candidates.append((kind, numeric_id))

        fills: List[Tuple[str, Dict[str, Any]]] = []
        for kind, algo_id in candidates:
            try:
                algo = await algo_getter(algo_id=algo_id)
            except Exception as exc:
                self.logger.debug(f"{symbol}: {kind} algo emri sorgulanamadı ({exc}), aday atlandı")
                continue
            actual = (algo or {}).get("actualOrderId")
            if actual in (None, "", 0, "0"):
                continue
            try:
                numeric_actual = int(actual)
            except (TypeError, ValueError):
                continue
            try:
                rows = await trades_getter(symbol, order_id=numeric_actual, limit=500)
            except Exception as exc:
                self.logger.debug(f"{symbol}: {kind} fill sorgusu başarısız ({exc}), aday atlandı")
                continue
            if not isinstance(rows, list):
                return None

            for row in rows:
                if not isinstance(row, dict):
                    return None
                try:
                    if int(row.get("orderId")) != numeric_actual:
                        return None
                except (TypeError, ValueError):
                    return None
                buyer = row.get("buyer")
                if buyer is None or bool(buyer) != expected_buyer:
                    return None
                try:
                    raw_qty = row.get("qty")
                    if raw_qty is None:
                        raw_qty = row.get("quantity")
                    qty_value = float(raw_qty)
                    price_value = float(row.get("price"))
                except (TypeError, ValueError):
                    return None
                if not math.isfinite(qty_value) or qty_value <= 0:
                    return None
                if not math.isfinite(price_value) or price_value <= 0:
                    return None
                try:
                    row_time_ms = int(row.get("time"))
                except (TypeError, ValueError):
                    return None
                if row_time_ms < window_start_ms:
                    return None
                try:
                    realized_value = float(row.get("realizedPnl"))
                    commission_value = float(row.get("commission"))
                except (TypeError, ValueError):
                    return None
                if not math.isfinite(realized_value) or not math.isfinite(commission_value):
                    return None
                if commission_value < 0:
                    return None
                fills.append((kind, row))

        if not fills:
            return None

        commission_assets = {str(row.get("commissionAsset") or "") for _, row in fills}
        commission_assets.discard("")
        if commission_assets != {"USDT"}:
            return None

        def _row_qty(row: Dict[str, Any]) -> float:
            raw = row.get("qty")
            if raw is None:
                raw = row.get("quantity")
            return float(raw)

        total_qty = sum(_row_qty(row) for _, row in fills)
        tolerance = max(1e-12, quantity * 1e-6)
        if abs(total_qty - quantity) > tolerance:
            return None

        gross = sum(float(row.get("realizedPnl")) for _, row in fills)
        fees = sum(float(row.get("commission")) for _, row in fills)

        if entry_fee_rate and entry_fee_rate > 0:
            rate = entry_fee_rate
        else:
            try:
                rate = max(
                    float(getattr(self.cfg, "scalper_taker_fee_pct", 0.05) or 0.05),
                    float(getattr(self.cfg, "scalper_maker_fee_pct", 0.02) or 0.02),
                ) / 100.0
            except (TypeError, ValueError):
                rate = 0.0005

        entry_fee_est = entry_price * quantity * rate
        net_pnl_estimate = gross - fees - entry_fee_est

        def _fill_sort_key(item: Tuple[str, Dict[str, Any]]) -> Tuple[int, int]:
            _, row = item
            try:
                row_time = int(row.get("time"))
            except (TypeError, ValueError):
                row_time = 0
            try:
                row_id = int(row.get("id") or 0)
            except (TypeError, ValueError):
                row_id = 0
            return row_time, row_id

        closing_kind, closing_row = max(fills, key=_fill_sort_key)
        if closing_kind in ("TP1", "TP2", "TP3"):
            exit_reason = "TP_LADDER"
        else:
            exit_reason = "TRAIL" if trailing_active else "SL"

        closing_order_id = int(closing_row.get("orderId"))
        closing_fills = [row for _, row in fills if int(row.get("orderId")) == closing_order_id]
        closing_notional = sum(float(row.get("price")) * _row_qty(row) for row in closing_fills)
        closing_qty = sum(_row_qty(row) for row in closing_fills)
        exit_price = closing_notional / closing_qty if closing_qty > 0 else entry_price

        return _CloseLedger(
            exit_price=exit_price,
            exit_reason=exit_reason,
            net_pnl_estimate=net_pnl_estimate,
            close_fills=len(closing_fills),
            flatten_kind=closing_kind,
            # D27/A2: merdivenin TÜM bacaklarının brütü (komisyon hariç).
            # `net_pnl_estimate = gross - fees - entry_fee_est` ZATEN bu
            # `gross`tan türüyordu; burada yalnız GÖRÜNÜR kılınıyor.
            gross_pnl=gross,
            legs=len(fills),
        )

    @staticmethod
    def _estimate_gross_pnl(
        direction: Direction,
        entry_price: float,
        exit_price: float,
        quantity: float,
    ) -> float:
        if direction == Direction.LONG:
            return (exit_price - entry_price) * quantity
        return (entry_price - exit_price) * quantity

    async def _resolve_commission_rates(self, symbol: str) -> tuple[float, float, str]:
        """Recovery için executor ile aynı gerçek-fee/fallback sözleşmesi."""
        maker_cfg = max(
            0.0, float(getattr(self.cfg, "scalper_maker_fee_pct", 0.02))
        ) / 100.0
        taker_cfg = max(
            0.0, float(getattr(self.cfg, "scalper_taker_fee_pct", 0.05))
        ) / 100.0
        fallback = max(maker_cfg, taker_cfg)
        getter = getattr(self.client, "get_user_commission_rate", None)
        if getter is None:
            return fallback, fallback, "config_conservative"
        try:
            raw = await getter(symbol)
            maker = float((raw or {}).get("makerCommissionRate"))
            taker = float((raw or {}).get("takerCommissionRate"))
            if (
                not math.isfinite(maker)
                or not math.isfinite(taker)
                or maker < 0
                or taker < 0
                or maker >= 1
                or taker >= 1
            ):
                raise ValueError(f"geçersiz commission response: {raw!r}")
            entry = maker if getattr(self.cfg, "scalper_entry_mode", "taker") == "maker" else taker
            return entry, taker, "binance_user_commission"
        except Exception as exc:
            self.logger.warning(
                f"⚠️ {symbol}: recovery gerçek komisyonu okunamadı ({exc}); "
                f"muhafazakâr fallback={fallback:.8f}"
            )
            return fallback, fallback, "config_conservative"

    async def _fetch_net_income(
        self,
        symbol: str,
        opened_at: Optional[datetime],
        entry_order_id: Optional[str],
    ) -> Optional[float]:
        """Pozisyon penceresindeki signed Binance gelirlerini net PnL yap.

        ``None``, doğrulama yapılamadığını anlatır; sıfır ise borsadan
        doğrulanmış gerçek bir sonuçtur. Her başarılı yanıtta yalnız son
        snapshot kullanılır; retry yanıtları birbirine eklenmez ve dolayısıyla
        aynı gelir kaydı iki kez sayılmaz.
        """
        start_time_ms = await self._income_window_start_ms(
            symbol=symbol,
            opened_at=opened_at,
            entry_order_id=entry_order_id,
        )
        if start_time_ms is None:
            self.logger.warning(
                f"⚠️ {symbol}: kesin giriş-emir zamanı doğrulanamadı; aynı-sembol income "
                f"bulaşmasını önlemek için brüt tahmine düşülüyor"
            )
            return None

        latest_values: Optional[List[float]] = None
        latest_fingerprint: Optional[tuple] = None
        last_error: Optional[Exception] = None

        for delay in self.INCOME_RETRY_DELAYS:
            if delay:
                await asyncio.sleep(delay)
            end_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 1000
            try:
                rows = await self.client.get_income_history(
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                    symbol=symbol,
                    limit=1000,
                )
            except Exception as exc:
                last_error = exc
                continue

            if not isinstance(rows, list):
                last_error = TypeError(f"income history list bekleniyordu, {type(rows).__name__} geldi")
                continue

            parsed: List[tuple] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("symbol") and row.get("symbol") != symbol:
                    continue
                income_type = str(row.get("incomeType") or "")
                if income_type not in self.NET_INCOME_TYPES:
                    continue
                raw_row_time = row.get("time")
                if raw_row_time not in (None, ""):
                    try:
                        row_time_ms = int(raw_row_time)
                    except (TypeError, ValueError):
                        continue
                    if row_time_ms < start_time_ms or row_time_ms > end_time_ms:
                        continue
                try:
                    income = float(row.get("income"))
                except (TypeError, ValueError):
                    continue
                parsed.append(
                    (
                        str(row.get("tranId") or ""),
                        income_type,
                        str(row.get("time") or ""),
                        str(row.get("asset") or ""),
                        str(row.get("income")),
                        income,
                    )
                )

            if parsed:
                assets = {item[3] for item in parsed if item[3]}
                income_types = {item[1] for item in parsed}
                if len(assets) > 1:
                    last_error = ValueError(
                        f"birden çok income asset'i toplamak güvenli değil: {sorted(assets)}"
                    )
                    continue
                if "REALIZED_PNL" not in income_types:
                    # Yalnız giriş komisyonu görünmüş olabilir; closing income
                    # henüz gecikiyorsa bu snapshot doğrulanmış sayılmamalı.
                    continue
                # Fingerprint telemetry/debug için tutulur; toplama tek bir
                # response snapshot'ı üzerinde yapılır, retry'lar birleştirilmez.
                latest_fingerprint = tuple(sorted(item[:-1] for item in parsed))
                latest_values = [item[-1] for item in parsed]

        if latest_values is not None:
            net_pnl = sum(latest_values)
            self.logger.info(
                f"✅ {symbol}: Binance income net PNL doğrulandı: {net_pnl:.8f} "
                f"({len(latest_values)} kayıt, snapshot={len(latest_fingerprint or ())})"
            )
            return net_pnl

        if last_error:
            self.logger.warning(
                f"⚠️ {symbol}: Binance income doğrulanamadı ({last_error}); brüt tahmine düşülüyor"
            )
        else:
            self.logger.warning(
                f"⚠️ {symbol}: Binance income penceresi boş; brüt tahmine düşülüyor"
            )
        return None

    async def _income_window_start_ms(
        self,
        symbol: str,
        opened_at: Optional[datetime],
        entry_order_id: Optional[str],
    ) -> Optional[int]:
        """Giriş order'ının borsaca doğrulanmış zamanından pencereyi başlat.

        Yalnız DB ``opened_at`` değerinden geriye geniş bir pencere açmak,
        hızlı aynı-sembol yeniden girişlerinde önceki işlemin komisyon/PnL'ini
        yeni işleme yazabilir. Order zamanı doğrulanamıyorsa bu yüzden güvenli
        biçimde ``None`` döner ve çağıran tahmini kaynağa düşer.
        """
        if not entry_order_id:
            return None
        try:
            numeric_order_id = int(entry_order_id)
        except (TypeError, ValueError):
            return None
        try:
            order = await self.client.get_order(symbol, numeric_order_id)
        except Exception as exc:
            self.logger.warning(f"⚠️ {symbol}: giriş emri zamanı okunamadı ({exc})")
            return None
        if not isinstance(order, dict):
            return None
        if order.get("symbol") and order.get("symbol") != symbol:
            return None
        raw_time = order.get("updateTime") or order.get("time")
        try:
            order_time_ms = int(raw_time)
        except (TypeError, ValueError):
            return None
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if order_time_ms <= 0 or order_time_ms > now_ms + 60_000:
            return None

        if opened_at:
            opened_utc = opened_at
            if opened_utc.tzinfo is None:
                opened_utc = opened_utc.replace(tzinfo=timezone.utc)
            else:
                opened_utc = opened_utc.astimezone(timezone.utc)
            opened_ms = int(opened_utc.timestamp() * 1000)
            # Bir order günlerce beklemişse tek income çağrısındaki 1000 satır
            # sınırı güvenilir ilişkilendirme yapmaya yetmez.
            if abs(opened_ms - order_time_ms) > 24 * 60 * 60 * 1000:
                return None

        return order_time_ms - self.INCOME_ENTRY_LOOKBACK_SECONDS * 1000

    @staticmethod
    def _infer_exit_reason(sp: ScalpPosition, exit_price: float, realized_pnl: float) -> str:
        """Kaba çıkarım (ledger doğrulaması yoksa son çare): son fiyat SL'ye mi TP
        tarafına mı yakındı + tp1_done bilgisi — TAHMİNİ bir etikettir.

        Mantık kapısı: negatif net PnL asla TP_LADDER olarak etiketlenemez —
        mesafe kıyası fiyat sıçraması/kayma nedeniyle yanılabilir, ama kayıplı
        bir kapanışın "kâr merdiveni" olarak görünmesi asıl bulguyu (2026-08-13
        ADAUSDT vakası) tekrarlar.

        D27/A1 — REAPER AYRIMI. Yaş limitini (D4, `SCALPER_MAX_HOLD_HOURS`)
        dolduran pozisyonu `engine._reap_aged_positions` düz bir reduce-only
        MARKET emriyle kapatır. `_verified_close_ledger` YALNIZ algo adaylarına
        (SL/TP1/TP2/TP3) bakar, o emri GÖREMEZ ve buraya düşülür; buradaki
        mesafe kıyası da kesmeyi çoğunlukla "SL" diye etiketliyordu. Ölçüldü
        (2026-08-24 kök-neden analizi): 43 yaş-kesmesi = **-172.3 USDT** =
        brüt zararın **%27'si**, ve bunların **12'si ARTIDA** kesilmişti. Bu
        etiket kirliliği her SL analizini bozuyordu.

        Sıra ÖNEMLİ: reaper damgası `trailing_active`ten ÖNCE bakılır. Reaper
        `trailing_active` pozisyonları zaten MUAF tutar (D4), yani ikisi
        normalde bir arada olamaz; olduysa (TP1 emirle aynı turda dolduysa)
        pozisyonu FİİLEN kapatan reaper'ın MARKET emridir. Borsa kanıtı
        (gerçek bir SL/TP fill) varsa buraya HİÇ düşülmez — ledger kazanır.

        D30 — STALE_TP damgası reaper damgasından ÖNCE okunur: safety turunda
        bayat-kâr kapanışı reaper'dan önce koşar ve emri gönderdiği turda
        döner (tur başına tek kapanış); ikisi normalde bir arada olamaz,
        olduysa borsaya ilk giden emir bayat-kâr kapanışıdır.
        """
        if getattr(sp, "stale_tp_close_at", None):
            return EXIT_REASON_STALE_TP
        if getattr(sp, "reaper_close_at", None):
            return EXIT_REASON_REAPER
        return ExitManager._infer_exit_reason_legacy(sp, exit_price, realized_pnl)

    @staticmethod
    def _infer_exit_reason_legacy(
        sp: ScalpPosition, exit_price: float, realized_pnl: float
    ) -> str:
        """D27 ÖNCESİ kaba çıkarım — DEĞİŞMEDİ, bit düzeyinde aynı gövde.

        NEDEN AYRI DURUYOR: `_maybe_start_loss_cooldown` kapısı etiketi OKUR
        (`exit_reason != "SL" and realized_pnl >= threshold` → çık). Sapma TEK
        yönlüdür ve tam olarak şu köşededir: **eski etiket "SL" + PnL eşiğin
        ÜSTÜNDE**. Bugün böyle bir kapanış cooldown BAŞLATIR; "REAPER" etiketi
        kapıya sızsaydı BAŞLATMAZDI — ve ölçüldüğü üzere yaş kesmelerinin
        12'si tam da ARTIDA kapanmıştı. (Eski etiket "TP_LADDER" olan artıda
        kapanışlarda fark YOKTUR: ikisi de erken döner.) D27 sözleşmesi
        "motor karar yolu bayt bayt aynı" der; bu yüzden cooldown kapısı ESKİ
        etiket uzayını okumaya devam eder ve deftere/adli kayda YENİ etiket
        yazılır. Parite testi:
        `tests/test_reaper_exit_label.py::TestCooldownParity`.

        SINIR (dürüstlük): `sp.reaper_close_at` yalnız BELLEKTEDİR — DB'de
        sütunu yoktur ve `recover()` onu geri yüklemez. Emir gönderildikten
        sonra, kapanış finalize edilmeden ÖNCE süreç yeniden başlarsa damga
        kaybolur ve kapanış eski yoldan "SL" etiketlenir. Yani REAPER ayrımı
        restart'lar boyunca EKSİK SAYAR (asla fazla saymaz).
        """
        if sp.trailing_active:
            # TP1 sonrası trailing aktifken kapanmışsa TRAIL veya son SL — TRAIL say
            return "TRAIL"
        sl_price = sp.plan.initial_stop
        tp_price = sp.plan.tp1_price
        dist_to_sl = abs(exit_price - sl_price)
        dist_to_tp = abs(exit_price - tp_price)
        label = "TP_LADDER" if dist_to_tp < dist_to_sl else "SL"
        if label == "TP_LADDER" and realized_pnl < 0:
            return "SL"
        return label

    def _update_mae_mfe(self, sp: ScalpPosition, current_price: float) -> None:
        entry = sp.position.entry_price
        leverage = sp.position.leverage or 1
        if entry <= 0:
            return
        price_delta_pct = (current_price - entry) / entry * 100.0
        if sp.signal.direction == Direction.SHORT:
            price_delta_pct = -price_delta_pct
        roi_pct = price_delta_pct * leverage
        sp.mfe_pct = max(sp.mfe_pct, roi_pct)
        sp.mae_pct = min(sp.mae_pct, roi_pct)
        # D27/A3 — yoklama sayacı (YALNIZ ÖLÇÜM). MAE/MFE bir ÖRNEKLEMEDİR;
        # kaç kez örneklendiği bilinmeden "MAE −7.16" ifadesinin çözünürlüğü
        # bilinemez. Hata akışı kesmemeli: alan yoksa (eski test çiftleri)
        # sessizce atlanır.
        try:
            sp.mae_samples = int(getattr(sp, "mae_samples", 0) or 0) + 1
        except Exception:  # pragma: no cover - SimpleNamespace fixture'ları
            pass

    @staticmethod
    def _live_stop_order(
        algo_orders: List[Dict[str, Any]],
        direction: Direction,
    ) -> Optional[Tuple[Optional[str], float]]:
        """Açık algo emirlerinden pozisyon yönünü gerçekten koruyan STOP'u bul.

        ``(algo_id, tetik_fiyatı)`` döner; algo_id (veya orderId alias'ı)
        yanıtta yoksa ``None`` olur — çağıran bu durumda DB'deki son bilinen
        kimliğe düşer.
        """
        expected_side = "SELL" if direction == Direction.LONG else "BUY"
        for order in algo_orders or []:
            order_type = order.get("orderType") or order.get("type")
            if order_type not in ("STOP_MARKET", "STOP"):
                continue
            side = str(order.get("side") or "").upper()
            if side and side != expected_side:
                continue
            trigger = order.get("triggerPrice") or order.get("stopPrice")
            try:
                trigger_value = float(trigger)
            except (TypeError, ValueError):
                continue
            if trigger_value > 0:
                algo_id = order.get("algoId")
                if algo_id in (None, ""):
                    algo_id = order.get("orderId")
                algo_id_str = str(algo_id) if algo_id not in (None, "") else None
                return algo_id_str, trigger_value
        return None

    @staticmethod
    def _live_stop_trigger(
        algo_orders: List[Dict[str, Any]],
        direction: Direction,
    ) -> Optional[float]:
        """Açık algo emirlerinden pozisyon yönünü gerçekten koruyan STOP'u bul.

        Geriye dönük uyumluluk için ince sarmalayıcı — davranışı değişmez,
        yalnız algo_id'yi düşürür. ``_recover_one`` doğrudan ``_live_stop_order``
        kullanır.
        """
        found = ExitManager._live_stop_order(algo_orders, direction)
        return found[1] if found is not None else None

    async def _record_recovery_estimate(self, trade: Any, notes: str) -> bool:
        """Belirsiz restart kapanışını borsa income/ledger doğrulamasıyla kaydet.

        Restart sırasında borsada zaten kapanmış bulunan pozisyonlar (recover'da
        canlı miktar<=0) için de canlı kapanışla AYNI doğrulama merdiveni
        uygulanır: önce income, sonra userTrades ledger, ancak ikisi de
        doğrulanamazsa tahmini brüte düşülür. ``notes`` içindeki mevcut
        ``recovery=...`` etiketi her zaman korunur; doğrulama etiketleri
        ``;`` ile eklenir.
        """
        income_net = await self._fetch_net_income(
            symbol=trade.symbol,
            opened_at=getattr(trade, "opened_at", None),
            entry_order_id=getattr(trade, "entry_order_id", None),
        )
        ledger = await self._verified_close_ledger(
            symbol=trade.symbol,
            direction=Direction(trade.direction),
            quantity=float(trade.quantity),
            entry_price=float(trade.entry_price),
            opened_at=getattr(trade, "opened_at", None),
            sl_order_id=getattr(trade, "sl_algo_id", None),
            tp1_algo_id=getattr(trade, "tp1_algo_id", None),
            tp2_algo_id=getattr(trade, "tp2_algo_id", None),
            trailing_active=False,
            entry_fee_rate=0.0,
        )

        if ledger is not None:
            exit_price = ledger.exit_price
        else:
            try:
                exit_price = await self.client.get_current_price(trade.symbol)
            except Exception:
                exit_price = None
            exit_price = float(exit_price or trade.entry_price)

        estimated_gross = self._estimate_gross_pnl(
            Direction(trade.direction),
            float(trade.entry_price),
            exit_price,
            float(trade.quantity),
        )

        verification_notes: List[str] = []
        if ledger is None:
            verification_notes.append("exit_fill=unverified")

        if income_net is not None:
            realized_pnl = income_net
            pnl_source = "binance_income_net"
        elif ledger is not None:
            realized_pnl = ledger.net_pnl_estimate
            pnl_source = "binance_trades_close_net"
            verification_notes.append("pnl=close_fills_net_entry_fee_estimated")
        else:
            realized_pnl = estimated_gross
            pnl_source = "estimated_gross"
            verification_notes.append("close_verification=unverified")

        exit_reason = ledger.exit_reason if ledger is not None else "UNKNOWN"
        merged_notes = ";".join(
            part for part in (notes, ";".join(verification_notes)) if part
        )

        try:
            await self.tracker.record_close(
                trade_id=trade.id,
                exit_price=exit_price,
                realized_pnl=realized_pnl,
                exit_reason=exit_reason,
                pnl_source=pnl_source,
                notes=merged_notes,
            )
        except Exception as e:
            self.logger.error(
                f"❌ recover(): {trade.symbol} #{trade.id} {exit_reason} kapanışı yazılamadı ({e})"
            )
            return False
        self._maybe_start_loss_cooldown(
            trade.symbol,
            exit_reason,
            realized_pnl,
            0.0
            if pnl_source == "binance_income_net"
            else self._estimated_roundtrip_fee(
                float(trade.entry_price), exit_price, float(trade.quantity)
            ),
        )
        return True

    # ------------------------------------------------------------------
    # Restart kurtarma
    # ------------------------------------------------------------------

    def recovery_strategies(self):
        """Bu motorun kurtarabileceği defter etiketleri (D20b).

        Döner: ``(strategies, exclude_strategies)`` — `tracker.open_trades`
        imzasıyla aynı sözleşme. Scalper gömülü takipçinin satırlarını
        (``strategy="AP"``) DIŞLAR; ayrı halkada DB'de AP satırı olmadığı için
        davranış birebir aynıdır. `FollowerExitManager` bunu ezer ve YALNIZ
        AP'yi ister.
        """
        return None, (FOLLOWER_LEDGER_STRATEGY,)

    def _recovery_row_is_mine(self, trade) -> bool:
        """Defter satırı bu motora mı ait? (ikinci savunma; WARNING loglar)"""
        wanted, unwanted = self.recovery_strategies()
        strategy = str(getattr(trade, "strategy", "") or "").strip().upper()
        allowed = True
        if wanted:
            allowed = strategy in {str(s).strip().upper() for s in wanted}
        if allowed and unwanted:
            allowed = strategy not in {str(s).strip().upper() for s in unwanted}
        if not allowed:
            self.logger.warning(
                f"⚠️ recover(): #{getattr(trade, 'id', '?')} "
                f"{getattr(trade, 'symbol', '?')} strategy='{strategy}' BU motorun "
                f"defterine ait değil — kurtarma ATLANDI (D20b: sembol başına tek "
                f"yönetici). Diğer motor onu kendi turunda kurtarır."
            )
        return allowed

    async def recover(self) -> bool:
        """DB'de status=OPEN olan scalp işlemlerini borsadaki gerçek pozisyonlarla
        eşleştirip izlemeye geri al.

        Borsada karşılığı bulunmayan bir DB kaydı (manuel kapatma, dış müdahale,
        vb.) exit_reason=UNKNOWN ile kapatılır — "bilinmiyor" gerçeği maskelemez.
        Borsa durumu veya koruma emirleri kesin okunamazsa ``False`` dönülür;
        çağıran bunu readiness başarısızlığı olarak ele almalıdır.
        """
        # D20b (düşmanca inceleme, KRİTİK): gömülü modda iki motor AYNI
        # `scalp_trades` tablosunu paylaşır. Her motor YALNIZ kendi defter
        # etiketini kurtarır; `recovery_strategies()` alt sınıfta ezilir.
        wanted, unwanted = self.recovery_strategies()
        try:
            try:
                open_trades = await self.tracker.open_trades(
                    strategies=wanted, exclude_strategies=unwanted
                )
            except TypeError:
                # Eski tracker çiftleri (test) filtreyi bilmiyor olabilir —
                # aşağıdaki ikinci savunma katmanı yine de satırı eler.
                open_trades = await self.tracker.open_trades()
        except Exception as e:
            self.logger.error(f"❌ recover(): açık scalp kayıtları okunamadı ({e})")
            return False

        # İKİNCİ SAVUNMA: filtre bir yoldan atlanırsa (eski tracker çifti,
        # ileride eklenecek bir çağrı yolu) satır BURADA elenir. Sessiz
        # kalmaz: yabancı defter satırını kurtarmak, aynı pozisyona ikinci
        # bir yönetici atamak demektir.
        open_trades = [t for t in open_trades if self._recovery_row_is_mine(t)]

        if not open_trades:
            self.logger.info("ℹ️ recover(): DB'de açık scalp işlemi yok")
            return True

        recovered = True
        for trade in open_trades:
            try:
                recovered = await self._recover_one(trade) and recovered
            except UnprotectedPositionError:
                # Açık ve korumasız pozisyon kapatılamadı: bunu boolean içinde
                # gizlemek insan müdahalesini geciktirir; kritik hatayı yükselt.
                raise
            except Exception as e:
                recovered = False
                self.logger.error(
                    f"❌ recover(): {getattr(trade, 'symbol', '?')} "
                    f"#{getattr(trade, 'id', '?')} kurtarılamadı ({e})"
                )
        return recovered

    async def _recover_one(self, trade) -> bool:
        symbol = trade.symbol
        try:
            # UNKNOWN kapatma kararı geri alınamaz — önbellek değil taze veri.
            pos_info = await self.client.get_position_risk(symbol, force_fresh=True)
        except Exception as e:
            self.logger.error(
                f"⚠️ recover(): {symbol} pozisyon durumu sorgulanamadı ({e}), "
                f"#{trade.id} bu turda atlanıyor"
            )
            return False

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0

        if amt <= 0:
            self.logger.warning(
                f"⚠️ recover(): {symbol} borsada açık pozisyon yok ama DB'de #{trade.id} "
                f"OPEN görünüyor — UNKNOWN ile kapatılıyor"
            )
            return await self._record_recovery_estimate(
                trade,
                notes="recovery=no_live_position",
            )

        direction = Direction(trade.direction)
        leverage = trade.leverage

        try:
            algo_orders = await self.client.get_open_algo_orders(symbol)
        except Exception as e:
            self.logger.error(
                f"⚠️ recover(): {symbol} koşullu emirler okunamadı ({e}); "
                f"koruma durumu belirsiz, readiness başarısız"
            )
            return False

        live_stop = self._live_stop_order(algo_orders, direction)
        live_sl_algo_id = live_stop[0] if live_stop is not None else None
        current_stop = live_stop[1] if live_stop is not None else None
        if current_stop is None:
            self.logger.critical(
                f"🚨 recover(): {symbol} borsada açık ama canlı STOP yok. "
                f"Korumasız pozisyon acil kapatılacak.",
                extra={"trade": True},
            )
            try:
                closed = await self.pm.emergency_close(symbol)
            except UnprotectedPositionError:
                raise
            except Exception as e:
                raise UnprotectedPositionError(
                    f"{symbol}: restart kurtarmasında korumasız pozisyon kapatılamadı ({e})"
                ) from e
            if not closed:
                raise UnprotectedPositionError(
                    f"{symbol}: restart kurtarmasında canlı STOP yok ve acil kapatma başarısız"
                )
            return await self._record_recovery_estimate(
                trade,
                notes="recovery=missing_stop_emergency_close",
            )

        tp1_price = price_at_roi(trade.entry_price, self.cfg.scalper_tp1_roi, leverage, direction)
        tp2_price = price_at_roi(trade.entry_price, self.cfg.scalper_tp2_roi, leverage, direction)
        entry_fee_rate, exit_fee_rate, fee_rate_source = await self._resolve_commission_rates(
            symbol
        )
        breakeven_price = fee_aware_breakeven_price(
            entry=float(trade.entry_price),
            direction=direction,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            buffer_pct=float(getattr(self.cfg, "scalper_breakeven_buffer_pct", 0.05)),
        )
        breakeven_cost_pct = (
            abs(breakeven_price - float(trade.entry_price))
            / float(trade.entry_price)
            * 100.0
        )

        tp1_fraction = self.cfg.scalper_tp1_fraction
        tp2_fraction = self.cfg.scalper_tp2_fraction
        tp1_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=trade.tp1_algo_id,
            expected_quantity=float(trade.quantity) * tp1_fraction,
            label="TP1/recovery",
        )
        tp2_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=trade.tp2_algo_id,
            expected_quantity=float(trade.quantity) * tp2_fraction,
            label="TP2/recovery",
        )

        # NOT (D17): `signal.entry_price` burada `position.entry_price` ile
        # AYNI değerden (`trade.entry_price`) kurulur — DB'de sinyal-anı
        # fiyatı için kolon yoktur. Fiyat-uzayı çevirisi bir dönem bu farkı
        # statik baz olarak kullanıyordu; restart sonrası baz 0 çıktığı için
        # düzeltme SESSİZCE no-op oluyordu (düşmanca inceleme, HIGH). Çeviri
        # artık her turda dinamik ölçülüyor (`_to_trading_price_space`), bu
        # yüzden burada ek alan/migrasyon GEREKMEZ.
        signal = ScalpSignal(
            strategy=trade.strategy,
            symbol=symbol,
            direction=direction,
            entry_price=trade.entry_price,
            stop_price=current_stop,
            reason=trade.signal_reason or "recover",
            regime=Regime.UNKNOWN,
            atr_5m=0.0,
        )

        position = PositionModel(
            symbol=symbol,
            side=PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT,
            leverage=leverage,
            margin_type="ISOLATED",
            entry_price=trade.entry_price,
            current_price=trade.entry_price,
            # quantity burada ORİJİNAL fill'dir; canlı kalan miktar her stop
            # replacement'ında borsadan okunur. Aksi halde restart sonrası TP
            # eşikleri küçülüp false positive üretir.
            quantity=trade.quantity,
            position_size=trade.quantity * trade.entry_price,
            initial_stoploss=current_stop,
            current_stoploss=current_stop,
            first_tp_price=tp1_price,
            first_tp_quantity=trade.quantity * tp1_fraction,
            targets=str([tp1_price, tp2_price]),
            status=PositionStatus.OPEN,
            # Kapanışta ledger doğrulaması için borsaca doğrulanmış giriş
            # order id'si — DB'ye kalıcı yazılmış olan trade.entry_order_id.
            entry_order_id=str(getattr(trade, "entry_order_id", "") or ""),
            # Canlı algo_id varsa DB'dekinden daha güncel — trailing SL
            # değişince DB güncellenmiyor, bu yüzden canlı yanıt öncelikli.
            sl_order_id=live_sl_algo_id or trade.sl_algo_id,
            tp_order_id=trade.tp1_algo_id,
            highest_price=trade.entry_price,
            lowest_price=trade.entry_price,
            trailing_stop_distance=self.cfg.scalper_chandelier_atr_mult,
            trailing_profit_distance=self.cfg.scalper_tp1_roi,
            opened_at=trade.opened_at,
            notes=f"scalper:{trade.strategy}:recovered",
        )

        plan = ExitPlan(
            tp1_price=tp1_price,
            tp1_quantity=trade.quantity * tp1_fraction,
            tp2_price=tp2_price,
            tp2_quantity=trade.quantity * tp2_fraction,
            runner_quantity=max(trade.quantity * (1 - tp1_fraction - tp2_fraction), 0.0),
            initial_stop=current_stop,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=self.cfg.scalper_chandelier_atr_mult,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            fee_rate_source=fee_rate_source,
            breakeven_cost_pct=breakeven_cost_pct,
            runner_floor_price=tp1_price,
            tp1_algo_id=trade.tp1_algo_id,
            tp2_algo_id=trade.tp2_algo_id,
        )

        entry_candle_time = 0
        if trade.opened_at:
            opened_at = trade.opened_at
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            entry_candle_time = int(opened_at.timestamp() * 1000)

        sp = ScalpPosition(
            trade_id=trade.id,
            signal=signal,
            position=position,
            plan=plan,
            entry_candle_time=entry_candle_time,
            tp1_done=tp1_done,
            tp2_done=tp2_done,
            trailing_active=tp1_done or tp2_done,
        )
        self._restore_forensics_entry(sp, trade)
        self.track(sp)
        self.logger.info(
            f"♻️ recover(): {symbol} #{trade.id} izlemeye geri alındı "
            f"(canlı_miktar={amt}, tp1_done={tp1_done}, tp2_done={tp2_done})",
            extra={"trade": True},
        )
        return True
