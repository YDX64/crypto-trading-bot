"""
Pozisyon yönetim modülü.
Açık pozisyonları takip eder, trailing SL/TP uygular, break-even'e taşır.

GÜVENLİK İLKELERİ:
1. Borsada gerçekten açılmış bir pozisyon ASLA korumasız veya takipsiz bırakılmaz.
   Stop-loss konulamazsa pozisyon acil olarak kapatılır.
2. Koruma değiştirilirken önce YENİ stop emri konur, sonra eskisi iptal edilir.
   Böylece iki adım arasında kalınsa bile pozisyon korumasız kalmaz.
3. Giriş fiyatı ve miktarı borsanın bildirdiği GERÇEK dolum değerlerinden alınır,
   sinyaldeki tahmini değerlerden değil.
"""

import asyncio
from typing import Optional, Dict, Any
from datetime import datetime

from src.models.position import (
    PositionModel, PositionStatus, PositionSide,
    PositionUpdate, TrailingInfo
)
from src.models.signal import SignalWithPosition, SignalDirection
from src.trading.binance_client_improved import (
    ImprovedBinanceClient as BinanceClient,
    BinanceAPIError,
    ERR_IMMEDIATE_TRIGGER,
)
from src.core.config import settings
from src.core.logger import app_logger


class UnprotectedPositionError(Exception):
    """Pozisyon açıldı ama korunamadı ve kapatılamadı — insan müdahalesi gerekir."""


class PositionManager:
    """Pozisyon yönetim motoru"""

    SL_PLACEMENT_ATTEMPTS = 3

    # Sinyal ile gerçek dolum arasında saniyeler geçer (maker/GTX girişte
    # onlarca saniye). Bu sürede fiyat stop seviyesinin ötesine kayarsa
    # Binance koşullu emri -2021 "Order would immediately trigger" ile
    # reddeder. Stop'u canlı fiyatın güvenli tarafına taşıyıp yeniden
    # denemek, pozisyonu daha açılır açılmaz zararla kapatmaktan iyidir.
    #
    # Buffer iki tabandan büyüğü olur: yüzdesel pay (volatil sembollerde
    # tetik ile emrin borsaya ulaşması arasındaki hareketi karşılar) ve
    # tick tabanı (quantize_protective_price koruyucu fiyatı piyasaya doğru
    # yuvarladığı için saf yüzde yetmeyebilir).
    SL_LIVE_PRICE_BUFFER_PCT = 0.15
    SL_MIN_TICK_BUFFER = 3

    def __init__(self, binance_client: BinanceClient):
        self.binance = binance_client
        self.logger = app_logger
        self.config = settings

    # ------------------------------------------------------------------
    # Pozisyon açma
    # ------------------------------------------------------------------

    async def open_position(
        self,
        analyzed_signal: SignalWithPosition
    ) -> Optional[PositionModel]:
        """Pozisyon aç ve koruma emirlerini kur.

        Akış:
          1. Margin type + leverage (emirden ÖNCE — başarısızlık zararsız)
          2. Market order
          3. Gerçek dolum fiyatı/miktarını borsadan doğrula
          4. Stop-loss — başarısız olursa pozisyonu ACİL KAPAT
          5. İlk take-profit — başarısız olursa pozisyon SL ile korunduğu için devam
        """
        signal = analyzed_signal.signal.signal
        symbol = signal.symbol

        margin_type = signal.margin_type or self.config.margin_type
        leverage = signal.leverage or self.config.max_leverage

        # --- 1. Emir ÖNCESİ hazırlık: burada hata olursa pozisyon yok, güvenli ---
        try:
            self.logger.info(f"📊 Pozisyon açılıyor: {symbol} {signal.direction}")
            self.logger.info(f"⚙️ Margin: {margin_type}, Leverage: {leverage}x")

            if not signal.targets:
                self.logger.error(f"❌ Sinyalde hedef fiyat yok: {symbol}")
                return None
            if not signal.stoploss or signal.stoploss <= 0:
                self.logger.error(f"❌ Sinyalde geçerli stop-loss yok: {symbol}")
                return None

            await self.binance.set_margin_type(symbol, margin_type)
            await self.binance.set_leverage(symbol, leverage)
        except Exception as e:
            self.logger.error(f"❌ Emir öncesi hazırlık hatası ({symbol}): {e}")
            return None

        # --- 2. Market order: bu noktadan sonra pozisyon GERÇEK olabilir ---
        side = "BUY" if signal.direction == SignalDirection.LONG else "SELL"
        try:
            entry_order = await self.binance.open_market_order(
                symbol=symbol, side=side, quantity=analyzed_signal.quantity
            )
        except Exception as e:
            self.logger.error(f"❌ Market order başarısız ({symbol}): {e}")
            return None

        # --- 3. Gerçek dolum bilgisini doğrula ---
        try:
            entry_price, filled_qty = await self._resolve_fill(symbol, entry_order)
        except Exception as e:
            self.logger.critical(
                f"🚨 {symbol}: dolum bilgisi okunamadı ({e}). "
                f"Pozisyon açık olabilir — acil kapatma deneniyor."
            )
            await self._emergency_close(symbol)
            return None

        if filled_qty <= 0:
            self.logger.error(f"❌ {symbol}: emir dolmadı (executedQty=0), pozisyon yok")
            return None

        self.logger.info(f"✅ Dolum: {filled_qty} @ {entry_price}")

        # --- 4. Stop-loss: BAŞARISIZ OLURSA POZİSYONU KAPAT ---
        # reference_price gerçek dolum fiyatıdır: -2021 durumunda stop bu fiyata
        # göre yeniden çapalanır, sinyaldeki tahmini fiyata göre değil.
        sl_side = "SELL" if signal.direction == SignalDirection.LONG else "BUY"
        sl_order = await self._place_stop_loss_or_close(
            symbol=symbol,
            sl_side=sl_side,
            stop_price=signal.stoploss,
            reference_price=entry_price,
        )
        if sl_order is None:
            # _place_stop_loss_or_close pozisyonu kapattı ya da fırlattı
            return None

        # --- 5. İlk TP: başarısızlık pozisyonu tehlikeye atmaz (SL var) ---
        tp_order: Dict[str, Any] = {}
        first_tp_qty = filled_qty * (self.config.first_tp_percentage / 100)
        try:
            tp_order = await self.binance.place_take_profit(
                symbol=symbol, side=sl_side,
                stop_price=signal.targets[0], quantity=first_tp_qty,
            )
        except Exception as e:
            self.logger.error(
                f"⚠️ {symbol}: İlk TP konulamadı ({e}). Pozisyon SL ile korunuyor, "
                f"TP olmadan devam ediliyor."
            )

        # --- 6. Kayıt oluştur. Buradaki hata pozisyonu korumasız BIRAKMAZ ---
        try:
            position = PositionModel(
                symbol=symbol,
                side=PositionSide.LONG if signal.direction == SignalDirection.LONG else PositionSide.SHORT,
                leverage=leverage,
                margin_type=margin_type,
                entry_price=entry_price,
                current_price=entry_price,
                quantity=filled_qty,
                position_size=filled_qty * entry_price,
                initial_stoploss=signal.stoploss,
                current_stoploss=signal.stoploss,
                first_tp_price=signal.targets[0],
                first_tp_quantity=first_tp_qty,
                targets=str(signal.targets),
                status=PositionStatus.OPEN,
                entry_order_id=str(entry_order.get("orderId")),
                sl_order_id=str(sl_order.get("orderId")),
                tp_order_id=str(tp_order.get("orderId")) if tp_order else None,
                highest_price=entry_price,
                lowest_price=entry_price,
                trailing_stop_distance=self.config.trailing_stop_percentage,
                trailing_profit_distance=self.config.trailing_profit_percentage,
                opened_at=datetime.utcnow(),
                notes=None if tp_order else "İlk TP konulamadı",
            )
        except Exception as e:
            self.logger.critical(
                f"🚨 {symbol}: pozisyon KAYDI oluşturulamadı ({e}) ama borsada "
                f"açık ve SL korumalı. Kayıt olmadan izlenemez — acil kapatılıyor."
            )
            await self._emergency_close(symbol)
            return None

        self.logger.info(
            f"✅ Pozisyon açıldı: {symbol} {signal.direction} {filled_qty} @ {entry_price}",
            extra={"trade": True},
        )
        return position

    async def _resolve_fill(
        self, symbol: str, entry_order: Dict[str, Any]
    ) -> tuple[float, float]:
        """Emrin GERÇEK dolum fiyatını ve miktarını belirle.

        newOrderRespType=RESULT avgPrice/executedQty döndürür, ancak bunlar
        null veya '0' gelebilir. Sırayla üç kaynak denenir:
          1. Emir yanıtındaki avgPrice
          2. GET /fapi/v1/order ile emrin güncel hali
          3. positionRisk üzerinden pozisyonun entryPrice'ı
        """
        def _num(value: Any) -> float:
            # avgPrice None, '', '0' veya '0.00000' olabilir — hepsi "bilinmiyor"
            if value in (None, "", "null"):
                return 0.0
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        price = _num(entry_order.get("avgPrice"))
        qty = _num(entry_order.get("executedQty"))

        if price > 0 and qty > 0:
            return price, qty

        order_id = entry_order.get("orderId")
        if order_id:
            for delay in (0.3, 0.7, 1.5):
                await asyncio.sleep(delay)
                try:
                    fresh = await self.binance.get_order(symbol, int(order_id))
                except Exception as e:
                    self.logger.warning(f"Emir sorgusu başarısız ({symbol}): {e}")
                    break
                price = _num(fresh.get("avgPrice")) or price
                qty = _num(fresh.get("executedQty")) or qty
                if price > 0 and qty > 0:
                    return price, qty

        # Son çare: pozisyonun kendisi
        pos = await self.binance.get_position_risk(symbol)
        if pos:
            pos_price = _num(pos.get("entryPrice"))
            pos_qty = abs(_num(pos.get("positionAmt")))
            if pos_price > 0 and pos_qty > 0:
                self.logger.info(f"Dolum bilgisi positionRisk'ten alındı: {pos_qty} @ {pos_price}")
                return pos_price, pos_qty

        raise BinanceAPIError(
            503, None,
            f"{symbol}: gerçek dolum fiyatı hiçbir kaynaktan okunamadı "
            f"(avgPrice={entry_order.get('avgPrice')!r})",
        )

    async def _reanchor_stop_price(
        self,
        symbol: str,
        sl_side: str,
        desired_stop: float,
        *,
        reference_price: Optional[float] = None,
        max_distance_pct: Optional[float] = None,
    ) -> Optional[float]:
        """Canlı fiyatın anında tetiklemeyeceği en yakın koruyucu stop'u bul.

        Sinyal üretimi ile emrin gerçekten dolması arasında geçen sürede fiyat,
        hesaplanmış stop seviyesinin ötesine geçmiş olabilir. Bu durumda stop'u
        "şu anki fiyatın güvenli tarafına" taşımak, pozisyonu daha açılır açılmaz
        piyasa emriyle kapatmaktan hem daha ucuz hem de işlem tezini korur.

        Stop yalnız pozisyonun ZARARINA doğru genişletilir; asla kâra doğru
        daraltılmaz. ``max_distance_pct`` verildiğinde genişleme risk bütçesiyle
        sınırlıdır: bütçe aşılıyorsa ``None`` döner ve çağıran pozisyonu kapatır —
        çünkü o noktada işlem zaten planlanan riskini kaybetmiştir.
        """
        try:
            price = await self.binance.get_current_price(symbol)
        except Exception as e:
            self.logger.error(f"{symbol}: yeniden fiyatlama için canlı fiyat alınamadı ({e})")
            return None
        if price is None or price <= 0:
            self.logger.error(f"{symbol}: yeniden fiyatlama için canlı fiyat alınamadı")
            return None

        tick = 0.0
        try:
            filters = await self.binance.get_symbol_filters(symbol)
            tick = float(filters["tickSize"])
        except Exception as e:
            self.logger.warning(
                f"{symbol}: tickSize okunamadı ({e}), yalnız yüzdesel buffer kullanılıyor"
            )

        buffer = max(
            tick * self.SL_MIN_TICK_BUFFER,
            price * self.SL_LIVE_PRICE_BUFFER_PCT / 100.0,
        )

        if sl_side.upper() == "SELL":
            # LONG pozisyonu koruyor — stop canlı fiyatın ALTINDA kalmalı.
            candidate = min(desired_stop, price - buffer)
        else:
            # BUY: SHORT pozisyonu koruyor — stop canlı fiyatın ÜSTÜNDE kalmalı.
            candidate = max(desired_stop, price + buffer)

        if candidate <= 0:
            self.logger.error(
                f"⛔ {symbol}: gecikme telafili stop pozitif bir fiyata çözülemedi "
                f"(aday={candidate}, canlı={price})"
            )
            return None

        if reference_price and reference_price > 0 and max_distance_pct and max_distance_pct > 0:
            distance_pct = abs(candidate - reference_price) / reference_price * 100.0
            if distance_pct > max_distance_pct:
                self.logger.error(
                    f"⛔ {symbol}: gecikme telafili stop risk bütçesini aşıyor "
                    f"(%{distance_pct:.3f} > %{max_distance_pct:.3f}) — "
                    f"pozisyon korunmak yerine kapatılacak"
                )
                return None

        self.logger.warning(
            f"🔄 {symbol}: stop yürütme gecikmesine göre yeniden çapalandı "
            f"{desired_stop} -> {candidate} (canlı fiyat={price}, buffer={buffer:.10g})"
        )
        return candidate

    async def _place_stop_loss_or_close(
        self,
        symbol: str,
        sl_side: str,
        stop_price: float,
        *,
        reference_price: Optional[float] = None,
        max_distance_pct: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Stop-loss koymayı dene; başarısız olursa pozisyonu acil kapat.

        Korumasız bir kaldıraçlı pozisyon, kapatılmış bir pozisyondan çok daha
        tehlikelidir. Bu yüzden koruma kurulamıyorsa pozisyondan çıkılır.

        İSTISNA — ``-2021 Order would immediately trigger``: bu kod hatalı girdi
        DEĞİL, "stop fiyatı piyasanın yanlış tarafında kaldı" demektir ve giriş
        ile koruma arasındaki gecikmenin doğal sonucudur. Aynı fiyatla tekrar
        denemek anlamsızdır, ancak canlı fiyata göre YENİDEN HESAPLANMIŞ bir
        fiyatla denemek doğru davranıştır. Yeniden çapalama risk bütçesiyle
        sınırlıdır; bütçe aşılırsa acil kapatmaya düşülür.

        ``reference_price``/``max_distance_pct`` verilmezse genişleme ölçülemez;
        bu durumda tek bir yeniden çapalama denenir (buffer kadar, sınırlı).
        """
        last_error: Optional[Exception] = None
        current_stop = stop_price
        risk_bounded = bool(
            reference_price and reference_price > 0
            and max_distance_pct and max_distance_pct > 0
        )
        reprices_left = self.SL_PLACEMENT_ATTEMPTS - 1 if risk_bounded else 1

        for attempt in range(self.SL_PLACEMENT_ATTEMPTS):
            try:
                placed = await self.binance.place_stop_loss(
                    symbol=symbol, side=sl_side,
                    stop_price=current_stop, close_position=True,
                )
                if isinstance(placed, dict):
                    # Yeniden çapalama sonrası gerçekte konan tetik fiyatı çağırana
                    # bildir: DB kaydı ve çıkış planı borsadaki emirle uyuşmalı.
                    placed["effectiveStopPrice"] = current_stop
                return placed
            except BinanceAPIError as e:
                last_error = e
                self.logger.error(
                    f"⚠️ SL denemesi {attempt + 1}/{self.SL_PLACEMENT_ATTEMPTS} "
                    f"başarısız ({symbol}) kod={e.code}: {e.msg}"
                )
                if e.code == ERR_IMMEDIATE_TRIGGER and reprices_left > 0:
                    reprices_left -= 1
                    repriced = await self._reanchor_stop_price(
                        symbol, sl_side, current_stop,
                        reference_price=reference_price,
                        max_distance_pct=max_distance_pct,
                    )
                    if repriced is None:
                        break  # risk bütçesi aşıldı veya fiyat okunamadı
                    if repriced == current_stop:
                        break  # aynı fiyat — tekrar denemek anlamsız
                    current_stop = repriced
                    continue
                if not e.is_retryable and e.code is not None:
                    break  # tekrar denemek aynı sonucu verir
                await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as e:
                last_error = e
                self.logger.error(f"⚠️ SL denemesi {attempt + 1} başarısız ({symbol}): {e}")
                await asyncio.sleep(1.0 * (attempt + 1))

        self.logger.critical(
            f"🚨 {symbol}: STOP-LOSS KONULAMADI ({last_error}). "
            f"Korumasız pozisyon bırakılmayacak — ACİL KAPATILIYOR.",
            extra={"trade": True},
        )
        closed = await self._emergency_close(symbol)
        if not closed:
            raise UnprotectedPositionError(
                f"{symbol}: pozisyon ne korunabildi ne kapatılabildi. "
                f"DERHAL ELLE MÜDAHALE EDİN. Son hata: {last_error}"
            )
        return None

    async def emergency_close(self, symbol: str) -> bool:
        """Korumasız pozisyonu reduceOnly piyasa emriyle güvenle kapat.

        Restart/reconciliation katmanları özel metoda bağlanmadan aynı güvenlik
        akışını kullanabilsin diye sağlanan public sarmalayıcıdır.
        """
        return await self._emergency_close(symbol)

    async def _emergency_close(self, symbol: str) -> bool:
        """Pozisyonu piyasa emriyle reduceOnly olarak kapat."""
        for attempt in range(3):
            try:
                pos = await self.binance.get_position_risk(symbol)
                if not pos:
                    return True
                amt = float(pos.get("positionAmt", 0))
                if amt == 0:
                    self.logger.info(f"✅ {symbol}: kapatılacak pozisyon yok")
                    return True

                close_side = "SELL" if amt > 0 else "BUY"
                await self.binance._request_with_retry(
                    "POST", "/fapi/v1/order",
                    params={
                        "symbol": symbol, "side": close_side, "type": "MARKET",
                        "quantity": abs(amt), "reduceOnly": "true",
                    },
                    signed=True,
                )
                self.logger.warning(
                    f"🔻 ACİL KAPATMA yapıldı: {symbol} {close_side} {abs(amt)}",
                    extra={"trade": True},
                )
                # Artık koruma emri gerekmiyor
                try:
                    await self.binance.cancel_all_open_orders(symbol)
                except Exception:
                    pass
                return True
            except Exception as e:
                self.logger.error(f"Acil kapatma denemesi {attempt + 1} başarısız ({symbol}): {e}")
                await asyncio.sleep(1.5 * (attempt + 1))
        return False

    # ------------------------------------------------------------------
    # Take-profit / break-even / trailing
    # ------------------------------------------------------------------

    async def check_first_tp_hit(self, position: PositionModel) -> bool:
        """İlk TP dolarak pozisyonun bir kısmını kapattı mı?

        Eşik config'deki first_tp_percentage'tan türetilir; eskiden sabit %80'di
        ve first_tp_percentage değiştirildiğinde yanlış sonuç veriyordu.
        """
        try:
            pos_info = await self.binance.get_position_risk(position.symbol)
        except Exception as e:
            # API hatası "TP vurdu" anlamına GELMEZ
            self.logger.error(f"TP kontrolü hatası [{position.symbol}]: {e}")
            return False

        if not pos_info:
            return False

        current_qty = abs(float(pos_info.get("positionAmt", 0)))
        original_qty = position.quantity
        if original_qty <= 0:
            return False

        remaining_pct = (current_qty / original_qty) * 100
        # TP %25'i kapatırsa %75 kalır. Yarım dolumlara tolerans için eşiği ortada tut.
        threshold = 100 - (self.config.first_tp_percentage / 2)

        if remaining_pct <= threshold:
            self.logger.info(
                f"🎯 İlk TP vurdu! Kalan: {remaining_pct:.1f}% (eşik: {threshold:.1f}%)",
                extra={"trade": True},
            )
            return True
        return False

    async def move_to_break_even(self, position: PositionModel) -> bool:
        """Stop-loss'u giriş fiyatına taşı.

        SIRA ÖNEMLİ: önce yeni SL konur, sonra eskisi iptal edilir. Ters sırada
        araya bir hata girerse pozisyon korumasız kalır.
        """
        if not position.entry_price or position.entry_price <= 0:
            self.logger.error(
                f"❌ {position.symbol}: giriş fiyatı geçersiz ({position.entry_price}), "
                f"break-even yapılamaz. Mevcut SL korunuyor."
            )
            return False

        ok = await self._replace_stop_loss(position, position.entry_price)
        if not ok:
            return False

        position.current_stoploss = position.entry_price
        position.is_break_even = True
        position.is_trailing = True
        position.status = PositionStatus.BREAK_EVEN
        position.first_tp_hit_at = datetime.utcnow()

        self.logger.info(
            f"✅ Break-even aktif! SL: {position.entry_price}", extra={"trade": True}
        )
        return True

    async def update_trailing_stop(self, position: PositionModel) -> bool:
        """Trailing stop-loss'u güncelle."""
        try:
            current_price = await self.binance.get_current_price(position.symbol)
        except Exception as e:
            self.logger.error(f"Trailing: fiyat alınamadı [{position.symbol}]: {e}")
            return False

        if not current_price:
            return False

        trailing_info = self._calculate_trailing_stop(position, current_price)
        if not trailing_info.should_update:
            # Fiyat takibi yine de güncellensin
            self._track_extremes(position, current_price)
            return False

        self.logger.info(
            f"📈 Trailing SL güncelleniyor: {position.symbol} "
            f"Eski: {position.current_stoploss} -> Yeni: {trailing_info.new_stop_loss}"
        )

        ok = await self._replace_stop_loss(position, trailing_info.new_stop_loss)
        if not ok:
            return False

        position.current_stoploss = trailing_info.new_stop_loss
        position.current_price = current_price
        self._track_extremes(position, current_price)

        pnl_info = self._calculate_pnl(position, current_price)
        position.unrealized_pnl = pnl_info["unrealized_pnl"]
        position.pnl_percentage = pnl_info["pnl_percentage"]
        position.last_checked_at = datetime.utcnow()

        self.logger.info(
            f"✅ Trailing SL güncellendi: {trailing_info.new_stop_loss} "
            f"(P&L: {pnl_info['pnl_percentage']:.2f}%)",
            extra={"trade": True},
        )
        return True

    async def _replace_stop_loss(self, position: PositionModel, new_stop: float) -> bool:
        """Stop-loss'u güvenli sırayla değiştir: önce yeni koy, sonra eskisini iptal et.

        Yeni emir reduceOnly + canlı pozisyon miktarı ile konur. Bunun nedeni
        Binance'in aynı yönde ikinci bir closePosition stop emrini reddetmesidir
        (-4130): closePosition kullanılsaydı önce eskiyi iptal etmek gerekir ve
        arada ~1 saniyelik KORUMASIZ bir pencere oluşurdu. reduceOnly stoplar
        bir arada durabildiği için bu pencere tamamen ortadan kalkar.

        Yeni emir konamazsa eski koruma yerinde kalır ve False dönülür.
        """
        symbol = position.symbol
        sl_side = "SELL" if position.side == PositionSide.LONG else "BUY"
        old_sl_id = position.sl_order_id

        # Canlı miktarı borsadan al: kısmi TP sonrası kayıttaki miktar eskimiş olur
        try:
            live = await self.binance.get_position_risk(symbol)
            live_qty = abs(float(live.get("positionAmt", 0))) if live else 0.0
        except Exception as e:
            self.logger.error(f"❌ {symbol}: canlı miktar okunamadı ({e}). Eski SL korunuyor.")
            return False

        if live_qty <= 0:
            self.logger.info(f"{symbol}: pozisyon kalmamış, SL güncellenmiyor")
            return False

        # 1) Yeni korumayı kur (reduceOnly — eskisiyle bir arada durabilir)
        try:
            new_order = await self.binance.place_stop_loss(
                symbol=symbol, side=sl_side, stop_price=new_stop,
                close_position=False, quantity=live_qty,
            )
        except BinanceAPIError as e:
            if e.code == -2021:
                # The market has already crossed the proposed protective
                # trigger. Keeping only the older, looser stop silently
                # violates the trailing/BE exit decision and caused repeated
                # -2021 loops in production telemetry. Enforce that decision
                # by flattening reduceOnly now.
                self.logger.critical(
                    f"🚨 {symbol}: yeni SL seviyesi piyasa tarafından geçilmiş "
                    f"(kod=-2021). Koruma kararı uygulanıyor: pozisyon acil kapatılacak.",
                    extra={"trade": True},
                )
                closed = await self._emergency_close(symbol)
                if not closed:
                    raise UnprotectedPositionError(
                        f"{symbol}: stop seviyesi geçildi ve acil kapatma başarısız oldu"
                    ) from e
                return False
            self.logger.error(
                f"❌ {symbol}: yeni SL konulamadı (kod={e.code}: {e.msg}). "
                f"ESKİ SL YERİNDE KALDI — pozisyon korumasız değil."
            )
            return False
        except Exception as e:
            self.logger.error(f"❌ {symbol}: yeni SL konulamadı ({e}). Eski SL korunuyor.")
            return False

        new_id = new_order.get("algoId") or new_order.get("orderId")
        position.sl_order_id = str(new_id)

        # 2) Eski korumayı kaldır. Başarısız olursa fazladan stop kalır ama
        #    closePosition=true olduğu için ilki tetiklendiğinde diğeri zararsızdır.
        if old_sl_id and str(old_sl_id) != str(new_id):
            try:
                await self.binance.cancel_algo_order(int(old_sl_id))
            except Exception as e:
                self.logger.warning(
                    f"⚠️ {symbol}: eski SL #{old_sl_id} iptal edilemedi ({e}). "
                    f"Fazladan stop emri var — closePosition sayesinde zararsız."
                )
        else:
            # Kayıtta emir kimliği yoksa artık kalan eski stopları temizle
            await self._cancel_stale_stops(symbol, keep_order_id=new_id)

        return True

    async def _cancel_stale_stops(self, symbol: str, keep_order_id: Any) -> None:
        """Belirtilen emir dışındaki koşullu STOP emirlerini iptal et.

        Koşullu emirler /fapi/v1/openOrders'ta GÖRÜNMEZ; bu yüzden algo emir
        listesi kullanılır. get_open_algo_orders hata durumunda [] dönmez,
        istisna fırlatır — "emir yok" ile "sorgulanamadı" karışmaz.
        """
        try:
            algo_orders = await self.binance.get_open_algo_orders(symbol)
        except Exception as e:
            self.logger.warning(
                f"⚠️ {symbol}: koşullu emirler sorgulanamadı ({e}). "
                f"Eski stop emirleri temizlenemedi."
            )
            return

        for order in algo_orders:
            if order.get("orderType") in ("STOP_MARKET", "STOP") and \
                    str(order.get("algoId")) != str(keep_order_id):
                try:
                    await self.binance.cancel_algo_order(int(order["algoId"]))
                except Exception as e:
                    self.logger.warning(
                        f"Eski stop iptal edilemedi algoId={order.get('algoId')}: {e}"
                    )

    @staticmethod
    def _track_extremes(position: PositionModel, current_price: float) -> None:
        if position.side == PositionSide.LONG:
            position.highest_price = max(position.highest_price or 0, current_price)
        else:
            low = position.lowest_price
            position.lowest_price = current_price if not low else min(low, current_price)

    def _calculate_trailing_stop(
        self, position: PositionModel, current_price: float
    ) -> TrailingInfo:
        """Trailing stop hesapla."""
        trailing_pct = position.trailing_stop_distance / 100

        if position.side == PositionSide.LONG:
            new_sl = current_price * (1 - trailing_pct)
            should_update = new_sl > (position.current_stoploss or 0)
            return TrailingInfo(
                symbol=position.symbol, side="LONG",
                current_price=current_price, entry_price=position.entry_price,
                new_stop_loss=new_sl, should_update=should_update,
                trailing_pct=position.trailing_stop_distance,
                highest_price=max(position.highest_price or 0, current_price),
            )

        new_sl = current_price * (1 + trailing_pct)
        current_sl = position.current_stoploss or float("inf")
        should_update = new_sl < current_sl
        return TrailingInfo(
            symbol=position.symbol, side="SHORT",
            current_price=current_price, entry_price=position.entry_price,
            new_stop_loss=new_sl, should_update=should_update,
            trailing_pct=position.trailing_stop_distance,
            lowest_price=min(position.lowest_price or float("inf"), current_price),
        )

    def _calculate_pnl(
        self, position: PositionModel, current_price: float
    ) -> Dict[str, float]:
        """Gerçekleşmemiş K/Z.

        pnl_percentage, yatırılan MARJA göre hesaplanır (nominal değere göre
        değil): kaldıraçlı bir pozisyonda gerçek getiri budur.
        """
        if position.side == PositionSide.LONG:
            pnl = (current_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - current_price) * position.quantity

        notional = position.position_size or 0.0
        leverage = position.leverage or 1
        margin = notional / leverage if leverage else notional
        pnl_pct = (pnl / margin) * 100 if margin > 0 else 0.0

        return {"unrealized_pnl": pnl, "pnl_percentage": pnl_pct}

    # ------------------------------------------------------------------
    # Durum sorguları
    # ------------------------------------------------------------------

    async def is_position_still_open(self, position: PositionModel) -> Optional[bool]:
        """Pozisyon hâlâ açık mı?

        Döner: True (açık), False (kapalı), None (BİLİNMİYOR — API hatası).
        None dönmesi önemlidir: eskiden API hatası False sayılıyordu ve bot
        gerçekte açık olan pozisyonu 'kapandı' kabul edip izlemeyi bırakıyordu.
        """
        try:
            pos_info = await self.binance.get_position_risk(position.symbol)
        except Exception as e:
            self.logger.error(
                f"⚠️ Pozisyon durumu SORGULANAMADI [{position.symbol}]: {e}. "
                f"İzlemeye devam ediliyor."
            )
            return None

        if not pos_info:
            return False
        return abs(float(pos_info.get("positionAmt", 0))) > 0

    async def close_position_record(self, position: PositionModel):
        """Pozisyon kaydını kapat ve artık emirleri temizle."""
        position.status = PositionStatus.CLOSED
        position.closed_at = datetime.utcnow()

        try:
            await self.binance.cancel_all_open_orders(position.symbol)
            self.logger.debug(f"{position.symbol}: artık emirler temizlendi")
        except Exception as e:
            self.logger.warning(f"{position.symbol}: artık emirler temizlenemedi: {e}")

        pnl_pct = position.pnl_percentage or 0.0
        self.logger.info(
            f"🏁 Pozisyon kapandı: {position.symbol} (P&L: {pnl_pct:.2f}%)",
            extra={"trade": True},
        )

    # ------------------------------------------------------------------
    # Scalper entegrasyonu — public sarmalayıcılar
    #
    # Scalper modülü (src/strategies/scalper) bu güvenlik akışlarını AYNEN
    # yeniden kullanır; davranışta hiçbir değişiklik yoktur, yalnızca dahili
    # metodlar dışarıya açılır.
    # ------------------------------------------------------------------

    async def resolve_fill(
        self, symbol: str, entry_order: Dict[str, Any]
    ) -> tuple[float, float]:
        """_resolve_fill'in public sarmalayıcısı: emrin GERÇEK dolum fiyatı/miktarını döner."""
        return await self._resolve_fill(symbol, entry_order)

    async def place_stop_loss_or_close(
        self,
        symbol: str,
        sl_side: str,
        stop_price: float,
        *,
        reference_price: Optional[float] = None,
        max_distance_pct: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """_place_stop_loss_or_close'un public sarmalayıcısı: SL koymayı dener,

        başarısız olursa pozisyonu acil kapatır (korumasız pozisyon bırakılmaz).

        ``reference_price`` gerçek dolum fiyatı, ``max_distance_pct`` ise o
        fiyattan izin verilen azami stop mesafesidir. İkisi birlikte verildiğinde
        -2021 sonrası yeniden çapalama risk bütçesi içinde kalacak şekilde
        sınırlanır; verilmezse tek ve dar bir yeniden deneme yapılır.
        """
        return await self._place_stop_loss_or_close(
            symbol=symbol,
            sl_side=sl_side,
            stop_price=stop_price,
            reference_price=reference_price,
            max_distance_pct=max_distance_pct,
        )

    async def replace_stop_loss(self, position: PositionModel, new_stop: float) -> bool:
        """_replace_stop_loss'un public sarmalayıcısı: boşluksuz SL değiştirme.

        Önce yeni reduceOnly SL konur, sonra eskisi iptal edilir — iki adım
        arasında pozisyon bir an bile korumasız kalmaz.
        """
        return await self._replace_stop_loss(position, new_stop)
