"""Takipçi girişleri — korumalı açılış (MARKET → SL → 3× TP).

Scalper'ın KANITLANMIŞ güvenlik disiplini AYNEN uygulanır (yeniden yazılmaz):
  * emir öncesi borsa filtresi doğrulaması (``validate_order``),
  * ``pm.place_stop_loss_or_close`` — SL kurulamazsa pozisyon ACİL KAPATILIR
    (``UnprotectedPositionError`` yukarı taşınır, motor global latch'i kurar),
  * TP başarısızlığı pozisyonu İPTAL ETTİRMEZ (SL zaten var),
  * borsanın döndürdüğü ``effectiveStopPrice`` ile kayıt hizalanır.

FARKLAR (kullanıcı kararı):
  * Giriş DAİMA MARKET — 1m sinyal gecikmesinde maker beklemek sinyali kaçırır.
  * Çıkış 3 EŞİT PARÇA (TP1/TP2/TP3, ``TAKE_PROFIT_MARKET`` reduce-only);
    yuvarlama artığı SON parçaya gider.
  * Boyutlama: marj = sermayenin %``FOLLOWER_MARGIN_PCT``'i, kaldıraç
    volatiliteye göre dinamik (bkz. ``plan.py``).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.logger import app_logger
from src.models.position import PositionModel, PositionSide, PositionStatus
from src.strategies.follower.brackets import LeverageBracketCache
from src.strategies.follower.levels import (
    signal_drift_limit_pct,
    stop_on_correct_side,
    tp_on_correct_side,
)
from src.strategies.follower.plan import (
    build_plan,
    split_three_quantities,
    with_exchange_quantity,
)
from src.strategies.follower.types import (
    FollowerEvent,
    FollowerLevels,
    FollowerPlan,
    FollowerRejected,
)
from src.strategies.scalper.executor import ScalpPosition
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    FOLLOWER_LEDGER_STRATEGY,
    Direction,
    ExitPlan,
    Regime,
    ScalpSignal,
    fee_aware_breakeven_price,
)
from src.trading.binance_client_improved import BinanceAPIError, ImprovedBinanceClient
from src.trading.position_manager import PositionManager, UnprotectedPositionError

# Deftere yazılan strateji etiketi — `ledger_report.py --strategy AP`.
#: `scalp_trades.strategy` etiketi — tek gerçek kaynak scalper/types.py'dedir
#: (gömülü modda scalper defteri AP satırlarını AYNI sabitle dışlar, D20b).
FOLLOWER_STRATEGY = FOLLOWER_LEDGER_STRATEGY


@dataclass
class FollowerPosition(ScalpPosition):
    """``ScalpPosition`` + üçüncü TP durumu ve boyutlama meta verisi.

    ``ExitManager``'ın kapanış defteri (``_finalize_close``) aynı alanları
    okuduğu için ScalpPosition'dan TÜRETİLİR — kapanış doğrulama merdiveni
    (income → userTrades → tahmini) yeniden yazılmadan kullanılır.
    """

    tp3_done: bool = False
    # TP1'in GERÇEK dolumu kanıtlandı mı? ``tp1_done``dan AYRIDIR: o "stop
    # break-even'e taşındı" demektir ve ücret-farkında BE ulaşılamıyorsa
    # (D20 "ücret eşiği") HİÇ True olmaz. Merdiven aritmetiği (TP2/TP3
    # kontrolü) bu bayrağa bağlanır — aksi halde TP2/TP3 hiç doğrulanmaz.
    tp1_filled: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)


class FollowerExecutor:
    """AlgoPro sinyalinden korumalı bir pozisyon açar."""

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
        brackets: Optional[LeverageBracketCache] = None,
    ):
        self.client = client
        self.pm = pm
        self.tracker = tracker
        self.cfg = cfg
        self.logger = app_logger
        self.brackets = brackets or LeverageBracketCache(client, cfg)
        # Sembol → cooldown bitiş epoch'u. RAM'de tutulur: varsayılan pencere
        # 60 sn, süreç yeniden başlaması (~90 sn) zaten bundan uzundur.
        self._cooldowns: Dict[str, float] = {}
        self._reject_counters: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Cooldown ve teşhis
    # ------------------------------------------------------------------

    def _cooldown_seconds(self) -> float:
        try:
            value = float(getattr(self.cfg, "follower_cooldown_sec", 60.0) or 0.0)
        except (TypeError, ValueError):
            value = 60.0
        return max(0.0, value)

    def start_cooldown(self, symbol: str) -> None:
        """Çıkıştan sonra sembolü kısa süre yeni girişe kapat."""
        seconds = self._cooldown_seconds()
        if seconds <= 0:
            return
        key = str(symbol).upper()
        expires_at = time.time() + seconds
        if self._cooldowns.get(key, 0.0) >= expires_at:
            return
        self._cooldowns[key] = expires_at
        self.logger.info(f"🧊 {key}: takipçi cooldown {seconds:.0f} sn")

    def is_entry_blocked(self, symbol: str) -> bool:
        key = str(symbol).upper()
        expires_at = self._cooldowns.get(key)
        if expires_at is None:
            return False
        if expires_at <= time.time():
            self._cooldowns.pop(key, None)
            return False
        return True

    def cooldown_snapshot(self) -> List[Dict[str, Any]]:
        now = time.time()
        rows: List[Dict[str, Any]] = []
        for symbol, expires_at in sorted(self._cooldowns.items()):
            if expires_at <= now:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "remaining_seconds": round(expires_at - now, 1),
                    "expires_at": datetime.fromtimestamp(
                        expires_at, tz=timezone.utc
                    ).isoformat(),
                }
            )
        return rows

    def count_reject(self, reason: str) -> None:
        self._reject_counters[reason] = self._reject_counters.get(reason, 0) + 1

    def reject_snapshot(self) -> Dict[str, int]:
        return dict(self._reject_counters)

    # ------------------------------------------------------------------
    # Giriş
    # ------------------------------------------------------------------

    async def open_position(
        self,
        *,
        event: FollowerEvent,
        levels: FollowerLevels,
        equity_usdt: float,
    ) -> Optional[FollowerPosition]:
        """Planı kur, borsada aç, koru ve deftere yaz.

        ``FollowerRejected``: bir kapı reddetti (emir GÖNDERİLMEDİ).
        ``None``: emir yolu başarısız oldu (loglandı; koruma kurulamadıysa
        pozisyon PositionManager tarafından kapatılmıştır).
        """
        symbol = event.symbol
        direction = event.direction
        if direction is None:
            raise FollowerRejected("Giriş olayında yön yok", code="no_direction")

        brackets = await self.brackets.get(symbol)
        try:
            filters = await self.client.get_symbol_filters(symbol)
            step_size = float(filters.get("stepSize") or 0.0)
        except Exception as exc:
            raise FollowerRejected(
                f"Borsa filtreleri okunamadı ({exc})", code="filters"
            ) from exc

        # Komisyon oranları EMİRDEN ÖNCE okunur: ücret eşiği kapısı (bulgu 3)
        # GERÇEK taker oranıyla çalışmalı ve reddedecekse emir GÖNDERİLMEDEN
        # reddetmeli. Oranlar 1 saat önbelleklidir (client), ikinci bir REST
        # çağrısı doğurmaz.
        entry_fee_rate, exit_fee_rate, fee_rate_source = await self._resolve_fee_rates(
            symbol
        )

        plan = build_plan(
            symbol=symbol,
            direction=direction,
            levels=levels,
            equity_usdt=equity_usdt,
            brackets=brackets,
            cfg=self.cfg,
            step_size=step_size,
            fee_rate=max(float(entry_fee_rate), float(exit_fee_rate)),
        )

        try:
            quantity = await self.client.quantize_quantity(symbol, plan.quantity)
            await self.client.validate_order(symbol, quantity, levels.entry)
        except BinanceAPIError as exc:
            raise FollowerRejected(
                f"Emir doğrulanamadı (kod={exc.code}: {exc.msg})", code="validate"
            ) from exc
        except Exception as exc:
            raise FollowerRejected(
                f"Boyutlama/doğrulama hatası ({exc})", code="validate"
            ) from exc

        plan = with_exchange_quantity(plan, quantity, step_size)
        if min(plan.tp_quantities[0], plan.tp_quantities[1]) <= 0:
            raise FollowerRejected(
                f"Pozisyon 3 parçaya bölünemiyor (miktar={quantity}, "
                f"stepSize={step_size}) — giriş yapılmadı",
                code="split",
            )

        # --- CANLI FİYAT KAPISI (emirden ÖNCE — düşmanca inceleme bulgu 1) ---
        await self._preflight_price_gate(
            symbol=symbol, direction=direction, levels=levels, event=event
        )

        # --- Margin type + leverage (emirden ÖNCE — hata zararsız) ---
        try:
            await self.client.set_margin_type(symbol, "ISOLATED")
            await self.client.set_leverage(symbol, plan.leverage)
        except BinanceAPIError as exc:
            raise FollowerRejected(
                f"Margin/leverage ayarlanamadı (kod={exc.code}: {exc.msg})",
                code="leverage",
            ) from exc
        except Exception as exc:
            raise FollowerRejected(
                f"Margin/leverage ayarında hata ({exc})", code="leverage"
            ) from exc

        side = "BUY" if direction == Direction.LONG else "SELL"
        sl_side = "SELL" if direction == Direction.LONG else "BUY"

        # --- BU NOKTADAN SONRA pozisyon GERÇEK olabilir ---
        try:
            entry_order = await self.client.open_market_order(
                symbol=symbol, side=side, quantity=plan.quantity
            )
        except BinanceAPIError as exc:
            self.logger.error(
                f"❌ {symbol}: market emri başarısız (kod={exc.code}: {exc.msg})"
            )
            self.count_reject("market_order")
            return None
        except Exception as exc:
            self.logger.error(f"❌ {symbol}: market emrinde beklenmeyen hata ({exc})")
            self.count_reject("market_order")
            return None

        try:
            entry_price, filled_qty = await self.pm.resolve_fill(symbol, entry_order)
        except Exception as exc:
            self.logger.critical(
                f"🚨 {symbol}: dolum bilgisi hiçbir kaynaktan okunamadı ({exc}). "
                f"Pozisyon açık olabilir — acil koruma/kapatma akışı devreye giriyor.",
                extra={"trade": True},
            )
            await self.pm.place_stop_loss_or_close(
                symbol=symbol, sl_side=sl_side, stop_price=levels.stop
            )
            return None

        if filled_qty <= 0:
            self.logger.error(f"❌ {symbol}: emir dolmadı (executedQty=0), pozisyon yok")
            return None

        self.logger.info(
            f"✅ Takipçi dolum: {symbol} {filled_qty} @ {entry_price} "
            f"(lev={plan.leverage}x, marj={plan.margin_usdt:.2f} USDT)"
        )
        return await self._finalize(
            event=event,
            plan=plan,
            direction=direction,
            sl_side=sl_side,
            entry_price=float(entry_price),
            filled_qty=float(filled_qty),
            entry_order_id=str(entry_order.get("orderId") or ""),
            step_size=step_size,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            fee_rate_source=fee_rate_source,
        )

    async def _preflight_price_gate(
        self,
        *,
        symbol: str,
        direction: Direction,
        levels: FollowerLevels,
        event: FollowerEvent,
    ) -> float:
        """MARKET emrinden ÖNCE canlı fiyatla taraf + sapma kapısı.

        NEDEN (düşmanca inceleme bulgu 1): sinyal ile emir arasında saniyeler
        geçer. Fiyat AlgoPro'nun stopunu ZATEN geçmişse:
          * ``_finalize``'daki ``abs()`` bunu göremez ve stopu "bütçeye"
            sıkıştırır — AlgoPro'nun HİÇ seçmediği bir stop uydurulur;
          * ya da SL emri ``-2021`` alır ve ``pm._reanchor_stop_price`` stopu
            canlı fiyatın buffer'ına (%0.15) çapalar — 100x'te marjın %15'i.
        İkisi de "AlgoPro'yu takip et" sözleşmesinin İHLALİDİR. Doğru
        davranış: pozisyonu HİÇ AÇMAMAK.

        Ayrıca sapma kapısı: alarm fiyatı ile canlı fiyat farkı
        ``FOLLOWER_MAX_SIGNAL_DRIFT_PCT``i (vars. SL mesafesinin %50'si)
        aşıyorsa RR merdiveni artık mesajdaki merdiven değildir → giriş yok.

        Dönüş: canlı fiyat (telemetri için). Kapı reddederse
        ``FollowerRejected`` yükseltilir — emir GÖNDERİLMEZ.
        """
        try:
            live_price = await self.client.get_current_price(symbol)
        except Exception as exc:
            raise FollowerRejected(
                f"Canlı fiyat okunamadı ({exc}) — giriş yapılmadı",
                code="live_price",
            ) from exc
        if not live_price or float(live_price) <= 0:
            raise FollowerRejected(
                "Canlı fiyat çözülemedi — giriş yapılmadı", code="live_price"
            )
        live_price = float(live_price)

        if not stop_on_correct_side(direction, live_price, levels.stop):
            raise FollowerRejected(
                f"AlgoPro stopu ({levels.stop:g}) canlı fiyatın "
                f"({live_price:g}) yanlış tarafında — sinyal fiyatı "
                f"{levels.entry:g} iken stop ZATEN geçilmiş; giriş yapılmadı "
                f"(yeniden çapalama YASAK: AlgoPro'nun seçmediği bir stop "
                f"uydurulamaz)",
                code="stop_already_passed",
            )

        reference = float(event.price or levels.entry)
        if reference > 0:
            drift_pct = abs(live_price - reference) / reference * 100.0
            limit_pct = signal_drift_limit_pct(levels.sl_pct, self.cfg)
            if limit_pct > 0 and drift_pct > limit_pct:
                raise FollowerRejected(
                    f"Sinyal fiyatı bayat: alarm {reference:g} vs canlı "
                    f"{live_price:g} (sapma %{drift_pct:.4f} > "
                    f"%{limit_pct:.4f}) — giriş yapılmadı",
                    code="signal_drift",
                )
        return live_price

    async def _finalize(
        self,
        *,
        event: FollowerEvent,
        plan: FollowerPlan,
        direction: Direction,
        sl_side: str,
        entry_price: float,
        filled_qty: float,
        entry_order_id: str,
        step_size: float,
        entry_fee_rate: float,
        exit_fee_rate: float,
        fee_rate_source: str,
    ) -> Optional[FollowerPosition]:
        symbol = plan.symbol
        levels = plan.levels

        # --- SL: kurulamazsa pm pozisyonu ZATEN acil kapattı ---
        # `max_distance_pct` = -2021 ("emir anında tetiklenir") sonrası yeniden
        # çapalama RİSK BÜTÇESİDİR; aşılırsa pm pozisyonu kapatır. Burada
        # FOLLOWER_MAX_SL_PCT (vars. %5) TEK BAŞINA KULLANILAMAZ: 100x'te %5
        # fiyat mesafesi marjın 5 katıdır — likidasyonun çok ötesi. Bütçe bu
        # yüzden likidasyon kapısıyla (lev × sl_pct ≤ LIQ_GUARD) TUTARLI
        # kırpılır: azami mesafe = LIQ_GUARD / kaldıraç (100x'te %0.5).
        liq_guard_pct = float(
            getattr(self.cfg, "follower_lev_liq_guard_pct", 50.0) or 50.0
        )
        band_pct = float(getattr(self.cfg, "follower_max_sl_pct", 0.0) or 0.0)
        # ÜÇÜNCÜ aday: bakım marjı kapısının (plan.py `_guards_ok`) fiyat
        # mesafesi karşılığı. Yalnız liq_guard kullanmak TUTARSIZDI: 100x'te
        # liq_guard %0.50'ye izin verirken mmr kapısı %0.30'da kesiyor ve
        # likidasyon mesafesi (1/lev − mmr) yalnız %0.60 — yeniden çapalama
        # stopu likidasyonun 0.1 puan yakınına taşıyabilirdi.
        mmr = float(plan.maint_margin_ratio or 0.0)
        safety_mult = float(getattr(self.cfg, "follower_mmr_safety_mult", 2.0) or 0.0)
        mmr_cap_pct = (
            100.0 * (1.0 / max(1, plan.leverage) - mmr) / safety_mult
            if safety_mult > 0
            else 0.0
        )
        budget_candidates = [
            value
            for value in (
                band_pct,
                liq_guard_pct / max(1, plan.leverage),
                mmr_cap_pct,
            )
            if value > 0
        ]
        stop_budget_pct = min(budget_candidates) if budget_candidates else None

        stop_target = float(levels.stop)

        # --- İŞARETLİ TARAF KONTROLÜ (düşmanca inceleme bulgu 1) ---
        # `abs()` ile ölçülen mesafe, stopun GERÇEK DOLUMUN yanlış tarafında
        # kalmasını GİZLER: LONG'da dolum 100 iken stop 101 ise mesafe %1
        # görünür ve bütçe kapısı onu "sıkıştırarak" AlgoPro'nun hiç seçmediği
        # bir stop (ör. %0.15) uydurur; sıkıştırılmasa SL emri -2021 alır ve
        # `pm._reanchor_stop_price` stopu canlı fiyatın buffer'ına çapalar.
        # İkisi de tezi bozar. Doğru davranış: AlgoPro tezi GEÇERSİZ →
        # pozisyonu reduce-only MARKET ile KAPAT, asla yeniden çapalama.
        if not stop_on_correct_side(direction, entry_price, stop_target):
            self.logger.critical(
                f"🚨 {symbol}: dolum ({entry_price}) AlgoPro stopunu "
                f"({stop_target}) ZATEN GEÇMİŞ ({direction.value}) — tez "
                f"geçersiz, pozisyon reduce-only MARKET ile kapatılıyor "
                f"(stop yeniden ÇAPALANMAZ)",
                extra={"trade": True},
            )
            self.count_reject("stop_already_passed")
            closed = await self.pm.emergency_close(symbol)
            if not closed:
                raise UnprotectedPositionError(
                    f"{symbol}: dolum stopu geçmişti ve pozisyon kapatılamadı "
                    f"(dolum={entry_price}, stop={stop_target}) — DERHAL ELLE "
                    f"MÜDAHALE EDİN"
                )
            self.start_cooldown(symbol)
            await self._record_protection_failure(
                event=event,
                plan=plan,
                entry_price=entry_price,
                filled_qty=filled_qty,
                entry_order_id=entry_order_id,
                notes="follower_stop_already_passed;exit_fill=unverified",
            )
            return None

        # GERÇEK dolum fiyatına göre stop mesafesini yeniden ölç. `sl_pct`
        # SİNYAL fiyatından hesaplanmıştı; MARKET girişte kayma bu oranı
        # büyütebilir ve planlanan risk (marjın %8'i) sessizce likidasyon
        # bölgesine kayabilir. Kapı yalnız SIKILAŞTIRIR — AlgoPro'nun stopu
        # asla GENİŞLETİLMEZ.
        fill_sl_pct = abs(entry_price - stop_target) / entry_price * 100.0
        if stop_budget_pct is not None and fill_sl_pct > stop_budget_pct:
            clamped = (
                entry_price * (1.0 - stop_budget_pct / 100.0)
                if direction == Direction.LONG
                else entry_price * (1.0 + stop_budget_pct / 100.0)
            )
            self.logger.warning(
                f"⚠️ {symbol}: dolum kayması stop mesafesini %{fill_sl_pct:.4f}'e "
                f"çıkardı (bütçe %{stop_budget_pct:.4f}, lev={plan.leverage}x) — "
                f"stop {stop_target} -> {clamped} olarak SIKILAŞTIRILDI",
                extra={"trade": True},
            )
            stop_target = clamped
            fill_sl_pct = stop_budget_pct

        sl_order = await self.pm.place_stop_loss_or_close(
            symbol=symbol,
            sl_side=sl_side,
            stop_price=stop_target,
            reference_price=entry_price,
            max_distance_pct=stop_budget_pct,
        )
        if sl_order is None:
            self.logger.error(
                f"❌ {symbol}: SL konulamadı — pozisyon PositionManager tarafından kapatıldı"
            )
            self.count_reject("initial_sl_failed")
            self.start_cooldown(symbol)
            await self._record_protection_failure(
                event=event,
                plan=plan,
                entry_price=entry_price,
                filled_qty=filled_qty,
                entry_order_id=entry_order_id,
            )
            return None

        stop_price = stop_target
        effective_stop = self._coerce_price(sl_order.get("effectiveStopPrice"))
        if effective_stop is not None and effective_stop != stop_price:
            self.logger.warning(
                f"📌 {symbol}: kayıtlı stop borsadaki etkin tetik fiyatına hizalandı "
                f"{stop_price} -> {effective_stop}"
            )
            stop_price = effective_stop

        # TELEMETRİ, GERÇEKTEN KONAN STOPTAN (düşmanca inceleme bulgu 1):
        # `sl_pct_fill` daha önce "konmasını istediğimiz" stoptan yazılıyordu;
        # borsa tetik fiyatını yuvarladıysa (`effectiveStopPrice`) ya da
        # `pm` gecikme telafisiyle kaydırdıysa defter GERÇEK riski göstermezdi.
        fill_sl_pct = abs(entry_price - stop_price) / entry_price * 100.0

        # --- 3 parça TP (reduce-only) — GERÇEK dolum miktarından bölünür ---
        parts = split_three_quantities(filled_qty, step_size)
        tp_prices = (levels.tp1, levels.tp2, levels.tp3)
        algo_ids: List[Optional[str]] = []
        for index, (price, qty) in enumerate(zip(tp_prices, parts), start=1):
            # TP SEVİYESİ GERÇEK DOLUMA GÖRE DOĞRULANIR (bulgu 9): LONG'da
            # TP dolumun ÜSTÜNDE olmalı. Kayma TP1'i dolumun arkasında
            # bırakmışsa TAKE_PROFIT_MARKET tetiklendiği anda 1/3 pozisyonu
            # ZARARLA kapatır (ya da -2021 alır). Böyle bir bacak KONULMAZ.
            if price > 0 and not tp_on_correct_side(direction, entry_price, price):
                self.count_reject("tp_wrong_side")
                self.logger.warning(
                    f"⚠️ {symbol}: TP{index} ({price}) gerçek dolumun "
                    f"({entry_price}) yanlış tarafında — emir KONULMADI "
                    f"(anında tetiklenip zararla kapatırdı)",
                    extra={"trade": True},
                )
                algo_ids.append(None)
                continue
            algo_ids.append(
                await self._place_tp_safely(symbol, sl_side, price, qty, f"TP{index}")
            )

        # TP1 KRİTİKTİR: break-even yalnız TP1'in GERÇEK fill'iyle kanıtlanır
        # (`exits._check_tp1_breakeven` → `_confirmed_algo_fill`). TP1 emri
        # yoksa BE hiç kurulamaz ve pozisyon tam risk stopuna kadar taşınır.
        # Bir kez yeniden dene; olmazsa SESSİZ KALMA: sayaç + CRITICAL.
        if (
            algo_ids[0] is None
            and parts[0] > 0
            and tp_prices[0] > 0
            and tp_on_correct_side(direction, entry_price, tp_prices[0])
        ):
            algo_ids[0] = await self._place_tp_safely(
                symbol, sl_side, tp_prices[0], parts[0], "TP1 (2. deneme)"
            )
        if algo_ids[0] is None:
            self.count_reject("tp1_missing")
            self.logger.critical(
                f"🚨 {symbol}: TP1 emri KONULAMADI — break-even bu pozisyonda "
                f"HİÇ kurulamayacak, işlem tam risk stopuyla taşınıyor. "
                f"/follower/status → reject_counters.tp1_missing",
                extra={"trade": True},
            )

        if min(parts[0], parts[1]) <= 0:
            # Kısmi dolum, planlanan miktarın 3'e bölünemeyeceği kadar küçük
            # kaldı (MARKET emirlerde nadir). TP1 yok → BE yok; sessiz kalma.
            self.logger.warning(
                f"⚠️ {symbol}: gerçek dolum ({filled_qty}) 3 parçaya bölünemedi "
                f"(stepSize={step_size}) — TP kademeleri eksik, break-even yok"
            )
            self.count_reject("partial_fill_split")

        # ÜCRET EŞİĞİ (kapı `build_plan`'da, EMİRDEN ÖNCE — D20a bulgu 3).
        # `sl_roi = lev × sl_pct` tavana kırpıldığında (sl_pct < ~%0.30 → lev
        # 100) TP1 ROI gidiş-dönüş komisyonun ALTINA düşer: BTC örneğinde
        # TP1 = marjın %4'ü, komisyon %10'u → üç TP de dolsa NET NEGATİF.
        # Ayrıca ücret-farkında break-even seviyesi TP1'in ÖTESİNDE kalır ve
        # bu yüzden hiç kurulamaz (bkz. exits._check_tp1_breakeven).
        real_fee_roi = (
            (float(entry_fee_rate) + float(exit_fee_rate)) * plan.leverage * 100.0
        )
        if real_fee_roi > 0 and plan.tp_roi_pct[0] <= real_fee_roi:
            self.logger.warning(
                f"⚠️ {symbol}: TP1 ROI (marjın %{plan.tp_roi_pct[0]:.2f}'i) "
                f"gidiş-dönüş komisyonun (%{real_fee_roi:.2f}) ALTINDA — "
                f"lev={plan.leverage}x, sl_pct=%{plan.sl_pct:.4f}. Üç TP de "
                f"dolsa işlem net negatif olabilir ve break-even kurulamaz. "
                f"(Bu satırı görüyorsan FOLLOWER_MIN_TP1_FEE_RATIO ELLE 0'a "
                f"çekilmiş demektir — varsayılan 1.0 böyle bir girişi HİÇ "
                f"açmaz.)",
                extra={"trade": True},
            )

        breakeven_price = fee_aware_breakeven_price(
            entry=entry_price,
            direction=direction,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            buffer_pct=float(getattr(self.cfg, "scalper_breakeven_buffer_pct", 0.05)),
        )

        signal = ScalpSignal(
            strategy=FOLLOWER_STRATEGY,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            reason=(
                f"algopro:{event.kind};tf={event.timeframe};{plan.ledger_note()};"
                f"sl_pct_fill={fill_sl_pct:.4f};fee_roi_real={real_fee_roi:.2f}"
            )[:480],
            regime=Regime.UNKNOWN,
            atr_5m=float(levels.atr_value or 0.0),
            leverage=plan.leverage,
        )

        position = PositionModel(
            symbol=symbol,
            side=PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT,
            leverage=plan.leverage,
            margin_type="ISOLATED",
            entry_price=entry_price,
            current_price=entry_price,
            quantity=filled_qty,
            position_size=filled_qty * entry_price,
            initial_stoploss=stop_price,
            current_stoploss=stop_price,
            first_tp_price=levels.tp1,
            first_tp_quantity=parts[0],
            targets=str([levels.tp1, levels.tp2, levels.tp3]),
            status=PositionStatus.OPEN,
            entry_order_id=entry_order_id,
            sl_order_id=self._extract_id(sl_order),
            tp_order_id=algo_ids[0],
            highest_price=entry_price,
            lowest_price=entry_price,
            opened_at=datetime.utcnow(),
            notes=f"follower:{FOLLOWER_STRATEGY}",
        )

        exit_plan = ExitPlan(
            tp1_price=levels.tp1,
            tp1_quantity=parts[0],
            tp2_price=levels.tp2,
            tp2_quantity=parts[1],
            runner_quantity=0.0,
            initial_stop=stop_price,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=0.0,  # takipçide trailing YOK — çıkış AlgoPro'nun
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            fee_rate_source=fee_rate_source,
            breakeven_cost_pct=abs(breakeven_price - entry_price) / entry_price * 100.0,
            runner_floor_price=levels.tp1,
            tp1_algo_id=algo_ids[0],
            tp2_algo_id=algo_ids[1],
            tp3_price=levels.tp3,
            tp3_quantity=parts[2],
            tp3_algo_id=algo_ids[2],
        )

        margin_usdt = (filled_qty * entry_price) / plan.leverage
        forensics_document = self._build_entry_forensics(
            signal=signal,
            event=event,
            plan=plan,
            levels=levels,
            direction=direction,
            entry_price=entry_price,
            filled_qty=filled_qty,
            margin_usdt=margin_usdt,
            stop_price=stop_price,
            breakeven_price=breakeven_price,
            fill_sl_pct=fill_sl_pct,
            real_fee_roi=real_fee_roi,
            fee_rate_source=fee_rate_source,
        )
        try:
            trade_id = await self.tracker.record_open(
                signal=signal,
                entry_price=entry_price,
                quantity=filled_qty,
                leverage=plan.leverage,
                margin_usdt=margin_usdt,
                sl_algo_id=self._extract_id(sl_order),
                tp1_algo_id=algo_ids[0],
                tp2_algo_id=algo_ids[1],
                tp3_algo_id=algo_ids[2],
                entry_order_id=entry_order_id,
                forensics=forensics_document,
            )
        except Exception as exc:
            self.logger.critical(
                f"🚨 {symbol}: takipçi işlem kaydı DB'ye yazılamadı ({exc}). Pozisyon "
                f"borsada AÇIK ve SL korumalı ama takip kaydı yok — recover() bulmalı.",
                extra={"trade": True},
            )
            return None

        self.logger.info(
            f"✅ Takipçi pozisyon açıldı: {symbol} {direction.value} {filled_qty} @ "
            f"{entry_price} (lev={plan.leverage}x, SL={stop_price} [%{plan.sl_pct:.3f} "
            f"= marjın %{plan.sl_roi_pct:.1f}'i], TP={levels.tp1}/{levels.tp2}/{levels.tp3})",
            extra={"trade": True},
        )

        return FollowerPosition(
            trade_id=trade_id,
            signal=signal,
            position=position,
            plan=exit_plan,
            entry_candle_time=int(time.time() * 1000),
            meta={
                "plan": {
                    **plan.as_dict(),
                    # GERÇEK (dolum sonrası) değerler — planlananla karışmasın.
                    "sl_pct_fill": fill_sl_pct,
                    "fee_roi_real_pct": real_fee_roi,
                    "tp1_covers_fees_real": bool(
                        plan.tp_roi_pct[0] > real_fee_roi > 0
                    ),
                },
                "event": event.as_dict(),
                "opened_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _build_entry_forensics(
        self,
        *,
        signal: ScalpSignal,
        event: FollowerEvent,
        plan: Any,
        levels: FollowerLevels,
        direction: Direction,
        entry_price: float,
        filled_qty: float,
        margin_usdt: float,
        stop_price: float,
        breakeven_price: float,
        fill_sl_pct: float,
        real_fee_roi: float,
        fee_rate_source: str,
    ) -> Optional[Dict[str, Any]]:
        """Takipçi girişinin adli kaydı (D21 belgesi, D20b'de takipçiye açıldı).

        SALT GÖZLEM: hiçbir kapıya girmez, hata hâlinde None döner ve giriş
        normal biçimde sürer. Scalper'ın belgesiyle AYNI şemadır — pano ve
        `ledger_report.py` iki defteri tek kartla okuyabilsin diye:
        `source="AlgoPro"`, göstergeler yerine AlgoPro telemetrisi
        (`TQI`/`Score`) ve MESAJDAN gelen seviyeler yazılır. Takipçide
        strateji göstergesi, rejim ve lider kapısı YOKTUR; o alanlar
        uydurulmaz, `off` olarak işaretlenir.
        """
        if not bool(getattr(self.cfg, "scalper_forensics_enabled", True)):
            return None
        try:
            from src.strategies.scalper import forensics as fx

            now = datetime.now(timezone.utc)
            entry = fx.build_entry(
                at=now.isoformat(timespec="seconds"),
                signal=signal,
                ctx=None,
                cfg=self.cfg,
                fill_price=entry_price,
                quantity=filled_qty,
                leverage=plan.leverage,
                margin_usdt=margin_usdt,
                stop_price=stop_price,
                tp1_price=levels.tp1,
                tp2_price=levels.tp2,
                breakeven_price=breakeven_price,
                signal_at=event.ts or None,
                entry_mode="taker",
                indicators={},
                regime_info={},
                leader_gate={},
                gates={
                    # Takipçide strateji kapıları YOKTUR — "passed" yazmak
                    # olmayan bir kapıyı geçmiş göstermek olurdu.
                    "regime": "off",
                    "leader": "off",
                    "structure": "off",
                    "tv_structure": "off",
                    "capacity": "passed",
                    "cooldown": "passed",
                    "fee_gate": "passed",
                },
                tv={
                    "source": "algopro",
                    "sources": ["algopro"],
                    "kind": event.kind,
                    "timeframe": event.timeframe,
                    "alarm_price": event.price,
                    "tqi": event.tqi,
                    "score": event.score,
                },
                source="AlgoPro",
                open_positions=None,
                daily_pnl=None,
            )
            # Takipçiye ÖZGÜ alanlar (build_entry'nin şemasında yoktur).
            entry["algopro"] = {
                "tqi": event.tqi,
                "score": event.score,
                "alarm_price": event.price,
                "levels_source": getattr(levels, "source", None),
                "sl": levels.stop,
                "tp1": levels.tp1,
                "tp2": levels.tp2,
                "tp3": levels.tp3,
            }
            entry["tp3_price"] = levels.tp3
            entry["sl_pct_plan"] = plan.sl_pct
            entry["sl_pct_fill"] = fill_sl_pct
            entry["tp_roi_pct"] = list(plan.tp_roi_pct)
            entry["fee_roi_real_pct"] = real_fee_roi
            entry["fee_rate_source"] = fee_rate_source
            thresholds = fx.thresholds_from_cfg(self.cfg)
            return {
                "v": fx.FORENSICS_VERSION,
                "entry": entry,
                "verdict": fx.classify_entry(entry, thresholds),
            }
        except Exception as exc:
            self.logger.warning(
                f"⚠️ {signal.symbol}: takipçi adli kaydı kurulamadı ({exc}) — "
                f"giriş ETKİLENMEDİ"
            )
            return None

    async def _record_protection_failure(
        self,
        *,
        event: FollowerEvent,
        plan: FollowerPlan,
        entry_price: float,
        filled_qty: float,
        entry_order_id: str,
        notes: str = "follower_initial_sl_failed;exit_fill=unverified",
    ) -> None:
        """İlk SL kurulamayıp acil kapatılan dolumu deftere DÜŞÜR.

        PnL doğrulanmadığı için ``pnl_source=estimated_gross`` (fallback) ve
        0.0 yazılır — "bilinmiyor" ASLA "kâr" sayılmaz; gerçek tutar Binance
        income'dan elle doğrulanır (bkz. docs/RUNBOOK.md "Entry-halt").
        """
        signal = ScalpSignal(
            strategy=FOLLOWER_STRATEGY,
            symbol=plan.symbol,
            direction=plan.direction,
            entry_price=entry_price,
            stop_price=plan.levels.stop,
            reason=f"algopro:{event.kind};{plan.ledger_note()}"[:480],
            regime=Regime.UNKNOWN,
            atr_5m=0.0,
            leverage=plan.leverage,
        )
        try:
            await self.tracker.record_failed_execution(
                signal=signal,
                entry_price=entry_price,
                exit_price=entry_price,
                quantity=filled_qty,
                leverage=plan.leverage,
                realized_pnl=0.0,
                pnl_source="estimated_gross",
                entry_order_id=entry_order_id,
                notes=notes,
            )
        except Exception as exc:
            self.logger.error(
                f"⚠️ {plan.symbol}: koruma hatası kaydı yazılamadı ({exc})"
            )

    async def _place_tp_safely(
        self, symbol: str, side: str, price: float, quantity: float, label: str
    ) -> Optional[str]:
        """TP koymayı dene; başarısızlık pozisyonu İPTAL ETTİRMEZ (SL var)."""
        if quantity <= 0 or price <= 0:
            self.logger.warning(
                f"⚠️ {symbol}: {label} atlandı (miktar={quantity}, fiyat={price})"
            )
            return None
        try:
            order = await self.client.place_take_profit(
                symbol=symbol, side=side, stop_price=price, quantity=quantity
            )
            return self._extract_id(order)
        except BinanceAPIError as exc:
            self.logger.error(
                f"⚠️ {symbol}: {label} konulamadı (kod={exc.code}: {exc.msg}). "
                f"Pozisyon SL ile korunuyor."
            )
            return None
        except Exception as exc:
            self.logger.error(
                f"⚠️ {symbol}: {label} konulurken beklenmeyen hata ({exc}). "
                f"Pozisyon SL ile korunuyor."
            )
            return None

    async def _resolve_fee_rates(self, symbol: str) -> Tuple[float, float, str]:
        """Gerçek komisyon oranları; okunamazsa muhafazakâr config oranı.

        Takipçi girişi DAİMA taker'dır (MARKET); çıkış da MARKET/koşullu
        emirdir — bu yüzden config fallback'ında iki bacakta da taker/maker
        oranlarının YÜKSEĞİ kullanılır (scalper ile aynı ilke).
        """
        conservative = (
            max(
                float(getattr(self.cfg, "scalper_taker_fee_pct", 0.05) or 0.0),
                float(getattr(self.cfg, "scalper_maker_fee_pct", 0.02) or 0.0),
            )
            / 100.0
        )
        getter = getattr(self.client, "get_user_commission_rate", None)
        if getter is None:
            return conservative, conservative, "config_conservative"
        try:
            raw = await getter(symbol)
            taker = float((raw or {}).get("takerCommissionRate"))
            if not math.isfinite(taker) or taker < 0 or taker >= 1:
                raise ValueError(f"geçersiz commission response: {raw!r}")
            # İki bacak da taker: giriş MARKET, çıkış MARKET/koşullu emir.
            return taker, taker, "binance_user_commission"
        except Exception as exc:
            self.logger.warning(
                f"⚠️ {symbol}: gerçek komisyon okunamadı ({exc}); "
                f"muhafazakâr fallback={conservative:.8f}"
            )
        return conservative, conservative, "config_conservative"

    @staticmethod
    def _coerce_price(value: Any) -> Optional[float]:
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        return price if price > 0 else None

    @staticmethod
    def _extract_id(order: Any) -> Optional[str]:
        if not isinstance(order, dict):
            return None
        value = order.get("algoId") or order.get("orderId")
        return str(value) if value is not None else None
