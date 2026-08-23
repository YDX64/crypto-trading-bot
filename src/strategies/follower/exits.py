"""Takipçi çıkışları — TP1 → break-even, TP2/TP3 telemetri, kapanış defteri.

``ExitManager``'dan TÜRETİLİR: kapanış doğrulama merdiveni (income →
userTrades ledger → tahmini brüt), ``_confirmed_algo_fill`` fill kanıtı,
``_handle_closed`` tek-finalizer kilidi ve MAE/MFE takibi YENİDEN YAZILMAZ.

Scalper'dan FARKLAR (kullanıcı kararı — "çıkışı AlgoPro söyler"):
  * Chandelier trailing YOKTUR. ``_update_trailing`` hiç çağrılmaz; koşucu
    kavramı yoktur, üçüncü parça TP3'e kadar taşınır.
  * TP1 doğrulanınca SL ücret-farkında break-even'e çekilir (scalper ile AYNI
    ``fee_aware_breakeven_price`` seviyesi ve AYNI boşluksuz
    ``pm.replace_stop_loss`` deseni).
  * TP2/TP3 dolumları yalnız telemetridir (SL taşınmaz; TP3 zaten pozisyonu
    kapatır).
  * Cooldown her çıkışta başlar (yalnız kayıpta değil) — takipçi aynı sembole
    saniyeler içinde yeniden girmemeli.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Callable, Dict, List, Optional

from src.strategies.follower.executor import FOLLOWER_STRATEGY, FollowerPosition
from src.strategies.follower.plan import split_three_quantities
from src.strategies.follower.types import parse_ledger_levels
from src.strategies.scalper.exits import EXIT_REASON_BE_MARKET, ExitManager
from src.strategies.scalper.executor import ScalpPosition
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Direction,
    ExitPlan,
    Regime,
    ScalpSignal,
    fee_aware_breakeven_price,
)
from src.models.position import PositionModel, PositionSide, PositionStatus
from src.trading.binance_client_improved import BinanceAPIError, ImprovedBinanceClient
from src.trading.position_manager import (
    PositionManager,
    StopReplaceResult,
    UnprotectedPositionError,
)


# Fiyat okuması ile emrin borsaya varması arasındaki boşluk payı (%).
# Break-even stopu bu payın içindeyse KONULMAZ: `-2021` (emir anında
# tetiklenir) `PositionManager._replace_stop_loss`'ta ACİL KAPATMAYA döner.
_BE_PLACEMENT_MARGIN_PCT = 0.02


async def _no_klines(*_args: Any, **_kwargs: Any) -> List[Any]:
    """Takipçide mum verisi ÇEKİLMEZ (trailing yok) — ExitManager sözleşmesi
    bir fetcher beklediği için boş liste döndüren açık bir yer tutucu."""
    return []


class FollowerExitManager(ExitManager):
    """AlgoPro takipçi pozisyonlarının çıkış yöneticisi."""

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
        exit_cooldown_cb: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(
            client, pm, tracker, cfg, _no_klines, loss_cooldown_cb=exit_cooldown_cb
        )

    # ------------------------------------------------------------------
    # Defter izolasyonu (D20b düşmanca inceleme, KRİTİK bulgu)
    # ------------------------------------------------------------------

    def recovery_strategies(self):
        """Takipçi YALNIZ kendi defter satırlarını (``strategy="AP"``) kurtarır.

        Gömülü modda scalper ile AYNI `scalp_trades` tablosu paylaşılır. Bu
        filtre olmadan takipçi restart'ta scalper'ın C pozisyonunu sahiplenip
        ona AlgoPro çıkış kurallarını (3 parça TP, trailing YOK) uygular ve
        aynı net pozisyonun ikinci yöneticisi olurdu.
        """
        return (FOLLOWER_STRATEGY,), None

    # ------------------------------------------------------------------
    # Cooldown: takipçide HER çıkış cooldown başlatır
    # ------------------------------------------------------------------

    def _maybe_start_loss_cooldown(
        self,
        symbol: str,
        exit_reason: str,
        realized_pnl: float,
        loss_threshold: float = 0.0,
    ) -> None:
        """Kâr/zarar ayrımı YAPMADAN cooldown başlat (scalper'da yalnız kayıp).

        Gerekçe: takipçi 1 dakikalık sinyalleri izler; bir çıkışın hemen
        ardından gelen aynı yönlü sinyal (ör. TP3'ten 5 sn sonra yeni BUY)
        pratikte aynı hareketin devamıdır ve slot israfıdır.
        """
        if self._loss_cooldown_cb is None:
            return
        try:
            self._loss_cooldown_cb(symbol)
        except Exception as exc:
            self.logger.error(f"⚠️ {symbol}: takipçi cooldown'u başlatılamadı ({exc})")

    # ------------------------------------------------------------------
    # Tur adımı
    # ------------------------------------------------------------------

    async def _step_one(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            pos_info = await self.client.get_position_risk(symbol)
        except BinanceAPIError as exc:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgulanamadı (kod={exc.code}: {exc.msg}). "
                f"İzleme sürüyor — 'bilinmiyor' 'kapandı' sayılmaz."
            )
            return
        except Exception as exc:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgusunda beklenmeyen hata ({exc}). "
                f"İzleme sürüyor."
            )
            return

        # Eşzamanlı finalize: bu await sırasında başka bir yol (AlgoPro exit /
        # risk-olayı flatten) pozisyonu bitirmiş olabilir.
        if self._positions.get(symbol) is not sp:
            return

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0
        if amt == 0:
            await self._handle_closed(symbol, sp)
            return

        try:
            current_price = await self.client.get_current_price(symbol)
        except Exception as exc:
            current_price = None
            self.logger.debug(f"{symbol}: güncel fiyat alınamadı ({exc})")
        if current_price:
            self._update_mae_mfe(sp, current_price)
            sp.position.current_price = current_price

        if not sp.tp1_done:
            await self._check_tp1_breakeven(symbol, sp, amt)
        # MERDİVEN, BE'YE DEĞİL BORSA DOLUMUNA BAĞLIDIR (düşmanca inceleme
        # bulgu 9): eskiden TP2 kontrolü `sp.tp1_done`un arkasındaydı, o da
        # "stop break-even'e taşındı" demektir. Ücret-farkında BE takipçide
        # çoğu zaman ULAŞILAMAZ (D20 "ücret eşiği") → `tp1_done` YAPISAL
        # OLARAK hiç True olmaz → TP2/TP3 dolumu HİÇ doğrulanmazdı.
        if getattr(sp, "tp1_filled", False) and not sp.tp2_done:
            await self._check_tp_telemetry(symbol, sp, amt, index=2)
        if sp.tp2_done and not getattr(sp, "tp3_done", False):
            await self._check_tp_telemetry(symbol, sp, amt, index=3)

    async def _check_tp1_breakeven(
        self, symbol: str, sp: ScalpPosition, live_qty: float
    ) -> None:
        """TP1 GERÇEK fill ile kanıtlanınca SL'yi ücret-dahil BE'ye çek."""
        filled = sp.position.quantity
        expected = float(getattr(sp.plan, "tp1_quantity", 0.0) or 0.0)
        if filled <= 0 or expected <= 0:
            return
        # Miktar azalması yalnız pahalı sorguyu erteleyen bir İPUCUDUR;
        # fill kanıtı _confirmed_algo_fill'dir (scalper ile aynı ilke).
        if live_qty > filled - expected * 0.9:
            return

        # FILL KANITI ÖNCE, EYLEM SONRA (bulgu 9): dolumun doğrulanması bir
        # OLGUDUR ve BE'nin konulabilirliğinden BAĞIMSIZDIR. Eskiden BE
        # ulaşılamaz olduğunda buradan erken dönülüyordu ve `tp1_filled`
        # hiç işaretlenmiyordu → merdivenin geri kalanı ölüydü.
        if not getattr(sp, "tp1_filled", False):
            if not await self._confirmed_algo_fill(
                symbol=symbol,
                algo_id=getattr(sp.plan, "tp1_algo_id", None),
                expected_quantity=expected,
                label="TP1",
            ):
                self.logger.warning(
                    f"⚠️ {symbol}: miktar azaldı ancak TP1 algo fill'i doğrulanamadı; "
                    f"salt miktar azalması break-even tetiklemeyecek"
                )
                return
            setattr(sp, "tp1_filled", True)
            self.logger.info(
                f"🎯 {symbol}: TP1 gerçek fill ile doğrulandı (kalan={live_qty})",
                extra={"trade": True},
            )

        target = sp.plan.breakeven_price
        current_sl = sp.position.current_stoploss
        already_tighter = self._is_at_least_as_protective(
            sp.signal.direction, current_sl, target
        )
        # KRİTİK: BE seviyesi piyasanın YANLIŞ tarafındaysa emir gönderme.
        # `pm._replace_stop_loss` `-2021` alırsa "koruma kararını uygula"
        # diyerek pozisyonu ACİL KAPATIR. Takipçide bu durum İSTİSNA DEĞİL
        # KURALDIR: ücret-farkında BE mesafesi ≈ giriş+çıkış+tampon ≈ %0.15
        # iken TP1 mesafesi `RR1 × sl_pct`tir; kaldıraç tavana dayandığı her
        # işlemde (sl_pct < ~%0.30) TP1 %0.15'in İÇİNDE kalır → her TP1
        # dolumu kalan 2/3'ü zorla düzleştirirdi. Doğru davranış: BE'yi
        # atlamak ve eski (AlgoPro) stopunu korumak.
        if not already_tighter and not self._be_target_placeable(
            sp.signal.direction, target, sp.position.current_price
        ):
            self._log_be_unreachable(symbol, sp, target)
            return
        # D22: yapılandırılmış sonuç — `-2021 → _emergency_close` ile
        # "emir reddedildi, eski SL yerinde" AYNI `False` değildir. Yukarıdaki
        # `_be_target_placeable` kapısı bu yolu ender kılar ama YARIŞI
        # tamamen kapatamaz; kaybedildiğinde defter DOĞRU etiketlenmelidir.
        result = (
            StopReplaceResult(True, "already_tighter")
            if already_tighter
            else await self._apply_stop(sp.position, target)
        )
        if not result.ok:
            if result.outcome == "emergency_closed":
                await self._on_emergency_closed(
                    symbol,
                    sp,
                    result,
                    exit_reason=EXIT_REASON_BE_MARKET,
                    what=f"BE seviyesi ({target})",
                )
            elif result.outcome == "no_position":
                self.logger.info(
                    f"ℹ️ {symbol}: BE taşınmadı — borsada pozisyon kalmamış"
                )
            else:
                self.logger.warning(
                    f"⚠️ {symbol}: SL break-even'e taşınamadı, eski SL korunuyor. "
                    f"Sonraki turda tekrar denenecek."
                )
            return
        sp.tp1_done = True
        if not already_tighter:
            sp.position.current_stoploss = target
        self.logger.info(
            f"✅ {symbol}: TP1 doğrulandı — ücret-dahil break-even aktif "
            f"(SL={sp.position.current_stoploss})",
            extra={"trade": True},
        )

    @staticmethod
    def _be_target_placeable(
        direction: Direction, target: Optional[float], live_price: Optional[float]
    ) -> bool:
        """BE stopu piyasanın GÜVENLİ tarafında mı? (fiyat yoksa: hayır)

        LONG'da SELL STOP piyasanın ALTINDA olmalı, SHORT'ta ÜSTÜNDE. Fiyat
        okunamadıysa `False` — "bilinmiyor" ASLA "konulabilir" sayılmaz.
        """
        if not target or target <= 0 or not live_price or live_price <= 0:
            return False
        margin = float(live_price) * _BE_PLACEMENT_MARGIN_PCT / 100.0
        if direction == Direction.LONG:
            return float(target) <= float(live_price) - margin
        return float(target) >= float(live_price) + margin

    def _log_be_unreachable(self, symbol: str, sp: ScalpPosition, target: float) -> None:
        """Ulaşılamayan BE'yi POZİSYON BAŞINA BİR KEZ uyar (log seli yok)."""
        meta = getattr(sp, "meta", None)
        if isinstance(meta, dict):
            if meta.get("be_unreachable_logged"):
                return
            meta["be_unreachable_logged"] = True
        self.logger.warning(
            f"⚠️ {symbol}: break-even seviyesi ({target}) güncel fiyatın "
            f"({sp.position.current_price}) yanlış tarafında — stop TAŞINMADI. "
            f"Ücret-farkında BE, TP1'den uzaksa bu kalıcıdır (bkz. D20 'ücret "
            f"eşiği'); AlgoPro stopu yerinde kalır, pozisyon acil KAPATILMAZ.",
            extra={"trade": True},
        )

    async def _check_tp_telemetry(
        self, symbol: str, sp: ScalpPosition, live_qty: float, *, index: int
    ) -> None:
        """TP2/TP3 dolumunu doğrula ve İŞARETLE (SL taşınmaz).

        Borsada dolum kanıtlanamıyorsa hiçbir durum değişmez — takipçinin
        çıkışı zaten AlgoPro'nun ``TPn HIT`` olayıyla çapraz doğrulanır
        (bkz. engine._handle_tp_event).
        """
        expected = float(
            getattr(sp.plan, "tp2_quantity" if index == 2 else "tp3_quantity", 0.0)
            or 0.0
        )
        algo_id = getattr(
            sp.plan, "tp2_algo_id" if index == 2 else "tp3_algo_id", None
        )
        filled = sp.position.quantity
        if filled <= 0 or expected <= 0:
            return
        consumed = float(getattr(sp.plan, "tp1_quantity", 0.0) or 0.0)
        if index == 3:
            consumed += float(getattr(sp.plan, "tp2_quantity", 0.0) or 0.0)
        if live_qty > filled - (consumed + expected * 0.9):
            return
        if not await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=algo_id,
            expected_quantity=expected,
            label=f"TP{index}",
        ):
            return
        if index == 2:
            sp.tp2_done = True
        else:
            setattr(sp, "tp3_done", True)
        setattr(sp, "tp1_filled", True)  # merdiven sırayla dolar
        self.logger.info(
            f"🎯 {symbol}: TP{index} gerçek fill ile doğrulandı (kalan={live_qty})",
            extra={"trade": True},
        )

    # ------------------------------------------------------------------
    # Eksik TP emirlerini yeniden koyma (düşmanca inceleme bulgu 7 ve 9)
    # ------------------------------------------------------------------

    def tp_repair_snapshot(self) -> Dict[str, int]:
        return dict(getattr(self, "_tp_repair_counters", {}) or {})

    def _count_tp_repair(self, name: str) -> None:
        counters = getattr(self, "_tp_repair_counters", None)
        if counters is None:
            counters = {}
            self._tp_repair_counters = counters
        counters[name] = counters.get(name, 0) + 1

    async def ensure_tp_orders(self, symbol: str, sp: ScalpPosition) -> int:
        """Borsada EKSİK olan TP bacaklarını yeniden koy. Dönüş: konan sayısı.

        NEDEN: TP emri konulamamış (`tp1_missing`), restart sırasında
        kaybolmuş ya da `_finalize_close`'un `cancel_all_open_orders`
        turuna yakalanmış olabilir. Takipçide TP'ler ÇIKIŞIN KENDİSİDİR
        (trailing yok): eksik bir bacak, o dilimin AlgoPro'nun hedefinde
        değil stopta kapanması demektir.

        GÜVENLİK: yeniden koyma yalnız (a) bacak henüz dolmamışsa,
        (b) borsada o algoId'ye ait CANLI emir yoksa, (c) tetik fiyatı
        güncel fiyatın KÂR tarafındaysa (aksi halde anında tetiklenir),
        (d) toplam TP miktarı canlı pozisyonu AŞMIYORSA yapılır.
        """
        direction = sp.signal.direction
        close_side = "SELL" if direction == Direction.LONG else "BUY"
        try:
            algo_orders = await self.client.get_open_algo_orders(symbol)
        except Exception as exc:
            self._count_tp_repair("read_failed")
            self.logger.warning(
                f"⚠️ {symbol}: TP onarımı için koşullu emirler okunamadı ({exc})"
            )
            return 0
        live_ids = {
            str(order.get("algoId"))
            for order in (algo_orders or [])
            if isinstance(order, dict) and order.get("algoId") is not None
        }

        try:
            pos_info = await self.client.get_position_risk(symbol, force_fresh=True)
            remaining = (
                abs(float(pos_info.get("positionAmt", 0) or 0)) if pos_info else 0.0
            )
        except Exception as exc:
            self._count_tp_repair("read_failed")
            self.logger.warning(
                f"⚠️ {symbol}: TP onarımı için pozisyon okunamadı ({exc})"
            )
            return 0
        if remaining <= 0:
            return 0

        try:
            current_price = await self.client.get_current_price(symbol)
        except Exception:
            current_price = None
        if not current_price or float(current_price) <= 0:
            self._count_tp_repair("no_price")
            self.logger.warning(
                f"⚠️ {symbol}: TP onarımı için canlı fiyat yok — atlandı "
                f"(anında tetiklenecek bir emir konulamaz)"
            )
            return 0

        plan = sp.plan
        legs = (
            (
                1,
                float(getattr(plan, "tp1_price", 0.0) or 0.0),
                float(getattr(plan, "tp1_quantity", 0.0) or 0.0),
                getattr(plan, "tp1_algo_id", None),
                bool(getattr(sp, "tp1_filled", False) or sp.tp1_done),
            ),
            (
                2,
                float(getattr(plan, "tp2_price", 0.0) or 0.0),
                float(getattr(plan, "tp2_quantity", 0.0) or 0.0),
                getattr(plan, "tp2_algo_id", None),
                bool(sp.tp2_done),
            ),
            (
                3,
                float(getattr(plan, "tp3_price", 0.0) or 0.0),
                float(getattr(plan, "tp3_quantity", 0.0) or 0.0),
                getattr(plan, "tp3_algo_id", None),
                bool(getattr(sp, "tp3_done", False)),
            ),
        )

        # Canlı emirlerin tükettiği miktar düşülür; kalan bütçe kadar konur.
        budget = remaining
        for _index, _price, qty, algo_id, done in legs:
            if not done and algo_id and str(algo_id) in live_ids:
                budget -= qty

        placed = 0
        for index, price, qty, algo_id, done in legs:
            if done or price <= 0 or qty <= 0:
                continue
            if algo_id and str(algo_id) in live_ids:
                continue
            self._count_tp_repair("missing")
            if not self._tp_price_is_ahead(direction, float(current_price), price):
                self._count_tp_repair("skipped_wrong_side")
                self.logger.warning(
                    f"⚠️ {symbol}: TP{index} emri EKSİK ama tetik fiyatı "
                    f"({price}) güncel fiyatın ({current_price}) yanlış "
                    f"tarafında — yeniden konulmadı (anında tetiklenirdi)",
                    extra={"trade": True},
                )
                continue
            size = min(qty, budget)
            if size <= 0:
                self._count_tp_repair("skipped_no_budget")
                continue
            try:
                order = await self.client.place_take_profit(
                    symbol=symbol, side=close_side, stop_price=price, quantity=size
                )
            except Exception as exc:
                self._count_tp_repair("replace_failed")
                self.logger.error(
                    f"⚠️ {symbol}: TP{index} yeniden konulamadı ({exc}); "
                    f"pozisyon SL ile korunuyor"
                )
                continue
            new_id = order.get("algoId") or order.get("orderId") if isinstance(order, dict) else None
            if new_id is not None:
                setattr(plan, f"tp{index}_algo_id", str(new_id))
            budget -= size
            placed += 1
            self._count_tp_repair("replaced")
            self.logger.warning(
                f"🔧 {symbol}: EKSİK TP{index} yeniden kondu "
                f"({size} @ {price}, algoId={new_id})",
                extra={"trade": True},
            )
        return placed

    @staticmethod
    def _tp_price_is_ahead(
        direction: Direction, current_price: float, price: float
    ) -> bool:
        """TP tetik fiyatı güncel fiyatın KÂR tarafında mı? (LONG: üstünde)"""
        if price <= 0 or current_price <= 0:
            return False
        if direction == Direction.LONG:
            return price > current_price
        return price < current_price

    # ------------------------------------------------------------------
    # Restart kurtarma
    # ------------------------------------------------------------------

    async def _recover_one(self, trade) -> bool:
        """Açık takipçi işlemini borsadaki gerçek pozisyonla eşleştir.

        Scalper'ın ``_recover_one``'ı TP fiyatlarını cfg ROI'lerinden yeniden
        üretir; takipçide seviyeler AlgoPro'nundur ve DB'de saklanmaz — bu
        yüzden CANLI algo emirlerinden okunur. Okunamayan TP fiyatı 0.0 kalır
        (yalnız log/telemetri değeri vardır; BE ve kapanış defteri miktar ve
        algoId üzerinden çalışır).
        """
        symbol = trade.symbol
        try:
            pos_info = await self.client.get_position_risk(symbol, force_fresh=True)
        except Exception as exc:
            self.logger.error(
                f"⚠️ recover(): {symbol} pozisyon durumu sorgulanamadı ({exc}), "
                f"#{trade.id} bu turda atlanıyor"
            )
            return False

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0
        if amt <= 0:
            self.logger.warning(
                f"⚠️ recover(): {symbol} borsada açık pozisyon yok ama DB'de "
                f"#{trade.id} OPEN görünüyor — UNKNOWN ile kapatılıyor"
            )
            return await self._record_recovery_estimate(
                trade, notes="recovery=no_live_position"
            )

        direction = Direction(trade.direction)
        leverage = trade.leverage

        try:
            algo_orders = await self.client.get_open_algo_orders(symbol)
        except Exception as exc:
            self.logger.error(
                f"⚠️ recover(): {symbol} koşullu emirler okunamadı ({exc}); "
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
            except Exception as exc:
                raise UnprotectedPositionError(
                    f"{symbol}: restart kurtarmasında korumasız pozisyon "
                    f"kapatılamadı ({exc})"
                ) from exc
            if not closed:
                raise UnprotectedPositionError(
                    f"{symbol}: restart kurtarmasında canlı STOP yok ve acil "
                    f"kapatma başarısız"
                )
            return await self._record_recovery_estimate(
                trade, notes="recovery=missing_stop_emergency_close"
            )

        quantity = float(trade.quantity)
        # Parçalar `quantity/3` DEĞİL, canlı yolda kullanılan AYNI kuralla
        # (stepSize'a AŞAĞI yuvarlama + artık son parçaya) yeniden kurulur.
        # Aksi halde `_check_tp1_breakeven`'in miktar-azalma eşiği
        # (`live_qty > filled - expected*0.9`) küçük adım sayılarında HİÇ
        # geçilmez ve restart sonrası break-even KALICI olarak ölür
        # (ör. 11 adım: gerçek parçalar 3/3/5, quantity/3 = 3.67).
        try:
            filters = await self.client.get_symbol_filters(symbol)
            step_size = float(filters.get("stepSize") or 0.0)
        except Exception as exc:
            step_size = 0.0
            self.logger.warning(
                f"⚠️ recover(): {symbol} borsa filtreleri okunamadı ({exc}); "
                f"TP parçaları 1/3 varsayımıyla kuruluyor"
            )
        part1, part2, part3 = split_three_quantities(quantity, step_size)
        # TP FİYATLARI: canlı emirler BİRİNCİL kaynaktır (borsadaki gerçek),
        # defter notundaki AlgoPro seviyeleri (`ap_tp1..3`) YEDEKTİR. Bir TP
        # emri düşmüşse fiyatı yalnız notta vardır — yoksa eksik bacak
        # yeniden KONULAMAZ (D20a bulgu 9).
        live_tps = self._live_tp_prices(algo_orders, direction, trade.entry_price)
        note_levels = parse_ledger_levels(getattr(trade, "signal_reason", ""))
        tp_prices = tuple(
            live if live and live > 0 else float(note_levels.get(key) or 0.0)
            for live, key in zip(live_tps, ("tp1", "tp2", "tp3"))
        )

        tp1_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=trade.tp1_algo_id,
            expected_quantity=part1,
            label="TP1/recovery",
        )
        tp2_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=trade.tp2_algo_id,
            expected_quantity=part2,
            label="TP2/recovery",
        )
        tp3_done = await self._confirmed_algo_fill(
            symbol=symbol,
            algo_id=getattr(trade, "tp3_algo_id", None),
            expected_quantity=part3,
            label="TP3/recovery",
        )

        entry_fee_rate, exit_fee_rate, fee_rate_source = (
            await self._resolve_commission_rates(symbol)
        )
        breakeven_price = fee_aware_breakeven_price(
            entry=float(trade.entry_price),
            direction=direction,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            buffer_pct=float(getattr(self.cfg, "scalper_breakeven_buffer_pct", 0.05)),
        )

        # TP1 dolmuş ama CANLI stop hâlâ break-even'den GEVŞEKSE, süreç
        # `replace_stop_loss` ile BE arasında yeniden başlamış demektir.
        # `tp1_done=True` ile kurtarırsak `_check_tp1_breakeven` bir daha HİÇ
        # çağrılmaz (takipçide trailing yoktur, telafi eden ikinci yol yok) ve
        # pozisyonun 2/3'ü tam risk stopuyla taşınır. Bayrağı düşürerek BE'yi
        # yeniden denenebilir kıl — fill kanıtı zaten her turda aranıyor.
        # DOLUM OLGUSU ile BE EYLEMİ ayrıdır: aşağıda `tp1_done` BE'yi
        # yeniden denemek için düşürülebilir, ama TP1'in DOLDUĞU gerçeği
        # merdiven aritmetiği için korunmalıdır (bulgu 9).
        tp1_filled = bool(tp1_done)
        if tp1_done and not self._is_at_least_as_protective(
            direction, current_stop, breakeven_price
        ):
            self.logger.warning(
                f"⚠️ recover(): {symbol} TP1 dolmuş ama canlı stop ({current_stop}) "
                f"break-even'den ({breakeven_price}) gevşek — BE yeniden denenecek",
                extra={"trade": True},
            )
            tp1_done = False

        signal = ScalpSignal(
            strategy=trade.strategy or FOLLOWER_STRATEGY,
            symbol=symbol,
            direction=direction,
            entry_price=trade.entry_price,
            stop_price=current_stop,
            reason=trade.signal_reason or "algopro:recover",
            regime=Regime.UNKNOWN,
            atr_5m=0.0,
            leverage=leverage,
        )

        position = PositionModel(
            symbol=symbol,
            side=PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT,
            leverage=leverage,
            margin_type="ISOLATED",
            entry_price=trade.entry_price,
            current_price=trade.entry_price,
            # ORİJİNAL dolum: TP eşikleri buna göre hesaplanır (canlı kalan
            # miktar her turda borsadan okunur).
            quantity=quantity,
            position_size=quantity * trade.entry_price,
            initial_stoploss=current_stop,
            current_stoploss=current_stop,
            first_tp_price=tp_prices[0],
            first_tp_quantity=part1,
            targets=str(list(tp_prices)),
            status=PositionStatus.OPEN,
            entry_order_id=str(getattr(trade, "entry_order_id", "") or ""),
            sl_order_id=live_sl_algo_id or trade.sl_algo_id,
            tp_order_id=trade.tp1_algo_id,
            highest_price=trade.entry_price,
            lowest_price=trade.entry_price,
            opened_at=trade.opened_at,
            notes=f"follower:{trade.strategy}:recovered",
        )

        plan = ExitPlan(
            tp1_price=tp_prices[0],
            tp1_quantity=part1,
            tp2_price=tp_prices[1],
            tp2_quantity=part2,
            runner_quantity=0.0,
            initial_stop=current_stop,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=0.0,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
            fee_rate_source=fee_rate_source,
            breakeven_cost_pct=abs(breakeven_price - float(trade.entry_price))
            / float(trade.entry_price)
            * 100.0,
            runner_floor_price=tp_prices[0],
            tp1_algo_id=trade.tp1_algo_id,
            tp2_algo_id=trade.tp2_algo_id,
            tp3_price=tp_prices[2],
            tp3_quantity=part3,
            tp3_algo_id=getattr(trade, "tp3_algo_id", None),
        )

        entry_candle_time = 0
        if trade.opened_at:
            opened_at = trade.opened_at
            if opened_at.tzinfo is None:
                # DB'deki zamanlar naive UTC'dir (datetime.utcnow); yerel saat
                # varsayımı restart sonrası saatlerce kayma demek olurdu.
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            entry_candle_time = int(opened_at.timestamp() * 1000)

        sp = FollowerPosition(
            trade_id=trade.id,
            signal=signal,
            position=position,
            plan=plan,
            entry_candle_time=entry_candle_time,
            tp1_done=tp1_done,
            tp1_filled=tp1_filled or tp2_done or tp3_done,
            tp2_done=tp2_done,
            tp3_done=tp3_done,
            meta={"recovered": True},
        )
        self.track(sp)
        self.logger.info(
            f"♻️ recover(): {symbol} #{trade.id} takipçi izlemesine geri alındı "
            f"(canlı_miktar={amt}, tp1={tp1_done}, tp2={tp2_done}, tp3={tp3_done})",
            extra={"trade": True},
        )
        # KAYIP TP EMİRLERİNİ YENİDEN KOY (bulgu 9): restart sırasında bir TP
        # emri iptal edilmiş/konulamamış olabilir. Takipçide TP'ler ÇIKIŞIN
        # KENDİSİDİR; eksik bacak o dilimin stopta kapanması demektir.
        # Hata kurtarmayı DÜŞÜRMEZ (SL zaten doğrulandı).
        try:
            await self.ensure_tp_orders(symbol, sp)
        except Exception as exc:
            self.logger.error(
                f"⚠️ recover(): {symbol} eksik TP onarımı başarısız ({exc}); "
                f"pozisyon SL ile korunuyor"
            )
        return True

    @staticmethod
    def _live_tp_prices(
        algo_orders: List[Dict[str, Any]],
        direction: Direction,
        entry_price: float,
    ) -> tuple:
        """Canlı TAKE_PROFIT emirlerinin tetik fiyatlarını girişe uzaklığa göre sırala."""
        expected_side = "SELL" if direction == Direction.LONG else "BUY"
        triggers: List[float] = []
        for order in algo_orders or []:
            order_type = order.get("orderType") or order.get("type")
            if order_type not in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
                continue
            side = str(order.get("side") or "").upper()
            if side and side != expected_side:
                continue
            raw = order.get("triggerPrice") or order.get("stopPrice")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                triggers.append(value)
        triggers.sort(key=lambda price: abs(price - float(entry_price)))
        while len(triggers) < 3:
            triggers.append(0.0)
        return tuple(triggers[:3])
