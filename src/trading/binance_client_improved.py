"""
Geliştirilmiş Binance Futures API client.

Tasarım ilkeleri:
- İmzalanan sorgu dizesi ile gönderilen sorgu dizesi YAPISAL olarak aynıdır
  (tek bir urlencode çıktısı hem imzalanır hem URL'e gömülür).
- Her retry denemesi parametreleri SIFIRDAN kurar; eski imza asla yeniden
  imzalanmaz.
- Borsa filtreleri (LOT_SIZE / PRICE_FILTER / MIN_NOTIONAL) önbelleğe alınır ve
  emir göndermeden önce uygulanır.
- API hataları Binance hata kodunu koruyan BinanceAPIError ile yüzeye çıkar;
  çağıranlar "yeniden denenebilir" ile "ölümcül" hatayı ayırt edebilir.
"""

import hmac
import hashlib
import time
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import httpx

from src.core.config import settings
from src.core.logger import app_logger
from src.core.rate_limiter import rate_limiter


# Binance hata kodları — kurtarma mantığı bunlara göre karar verir
ERR_TIMESTAMP_AHEAD = -1021       # timestamp recvWindow dışında
ERR_INVALID_SIGNATURE = -1022     # imza geçersiz
ERR_PRECISION = -1111             # precision maksimumun üzerinde
ERR_MIN_NOTIONAL = -4164          # notional çok küçük
ERR_NO_NEED_MARGIN = -4046        # margin type zaten ayarlı
ERR_IMMEDIATE_TRIGGER = -2021     # emir anında tetiklenirdi
ERR_INSUFFICIENT_MARGIN = -2019   # marj yetersiz
ERR_REDUCE_ONLY_REJECTED = -2022  # reduceOnly emri reddedildi

# Çağıranın normal akışta ele aldığı, gerçek arıza olmayan kodlar.
# Bunlar ERROR yerine DEBUG seviyesinde loglanır.
BENIGN_CODES = frozenset({
    ERR_NO_NEED_MARGIN,  # margin type zaten istenen değerde
    -2011,               # iptal edilecek emir zaten yok
})

# Yeniden denenmesi ANLAMSIZ olan hatalar: girdi yanlış, tekrar aynı sonucu verir
NON_RETRYABLE_CODES = frozenset({
    ERR_PRECISION,
    ERR_MIN_NOTIONAL,
    ERR_IMMEDIATE_TRIGGER,
    ERR_INSUFFICIENT_MARGIN,
    ERR_REDUCE_ONLY_REJECTED,
})


class BinanceAPIError(Exception):
    """Binance hata kodunu ve mesajını koruyan istisna.

    httpx.HTTPStatusError'ın metni yalnızca '400 Bad Request' içerir; gövdedeki
    {"code": -4164, "msg": "..."} kaybolur. Bu sınıf onu yüzeye çıkarır.
    """

    def __init__(self, status_code: int, code: Optional[int], msg: str, endpoint: str = ""):
        self.status_code = status_code
        self.code = code
        self.msg = msg
        self.endpoint = endpoint
        super().__init__(f"Binance [{status_code}] kod={code}: {msg} ({endpoint})")

    @property
    def is_retryable(self) -> bool:
        """Aynı isteği tekrar göndermenin anlamı var mı?"""
        if self.code in NON_RETRYABLE_CODES:
            return False
        return self.status_code >= 500 or self.status_code == 429


class ImprovedBinanceClient:
    """Geliştirilmiş Binance Futures API istemcisi"""

    # Borsa filtreleri nadiren değişir; süreç ömrü boyunca önbellek yeterli
    _FILTER_CACHE_TTL = 3600.0

    def __init__(self):
        self.api_key = settings.binance_api_key
        self.api_secret = settings.binance_api_secret
        self.base_url = settings.binance_base_url
        self.logger = app_logger

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            follow_redirects=True,
        )

        self.max_retries = 3
        self.retry_delay = 2.0
        self.recv_window = 10000

        # Sunucu saati ile yerel saat farkı (ms). İlk imzalı istekte hesaplanır.
        self._time_offset_ms: Optional[int] = None

        # symbol -> (filtreler, önbelleğe alma zamanı)
        self._symbol_filters: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self._filter_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # İmzalama
    # ------------------------------------------------------------------

    def _sign(self, query_string: str) -> str:
        """Verilen sorgu dizesini imzala."""
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """Geriye dönük uyumluluk için korunan yardımcı.

        DİKKAT: params içinde 'signature' anahtarı BULUNMAMALIDIR. İç kod artık
        bunun yerine _sign(query_string) kullanır.
        """
        clean = {k: v for k, v in params.items() if k != "signature"}
        return self._sign(urlencode(clean))

    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TradingBot/2.1",
        }

    async def _sync_time_offset(self) -> int:
        """Sunucu-yerel saat farkını hesapla ve önbelleğe al.

        Sabit '-500ms' hack'i yerine gerçek ölçüm kullanılır; böylece saat
        kayması olan makinelerde de imzalı istekler -1021 almaz.
        """
        if self._time_offset_ms is not None:
            return self._time_offset_ms
        try:
            resp = await self.client.get(f"{self.base_url}/fapi/v1/time", timeout=10.0)
            resp.raise_for_status()
            server_time = int(resp.json()["serverTime"])
            self._time_offset_ms = server_time - int(time.time() * 1000)
            if abs(self._time_offset_ms) > 1000:
                self.logger.warning(
                    f"⏱️ Saat farkı düzeltiliyor: {self._time_offset_ms}ms"
                )
            return self._time_offset_ms
        except Exception as e:
            self.logger.warning(f"Saat senkronu yapılamadı, offset=0 kullanılıyor: {e}")
            self._time_offset_ms = 0
            return 0

    # ------------------------------------------------------------------
    # İstek katmanı
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_error(response: httpx.Response) -> Tuple[Optional[int], str]:
        """Binance hata gövdesinden kod ve mesajı çıkar."""
        try:
            body = response.json()
            return body.get("code"), body.get("msg", response.text)
        except Exception:
            return None, response.text

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        """API isteği yap — her deneme parametreleri sıfırdan kurar.

        KRİTİK: base_params asla mutasyona uğramaz. Retry sırasında önceki
        denemenin 'signature' alanı yeniden imzalanırsa Binance -1022 döndürür.
        """
        base_params = dict(params or {})
        url = f"{self.base_url}{endpoint}"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                await rate_limiter.wait_for_binance()

                # Her denemede TAZE kopya — mutasyon yok
                attempt_params = dict(base_params)
                headers = {}

                if signed:
                    offset = await self._sync_time_offset()
                    attempt_params["timestamp"] = int(time.time() * 1000) + offset
                    attempt_params["recvWindow"] = self.recv_window
                    query = urlencode(attempt_params)
                    # İmzalanan dize ile gönderilen dize aynı: tek urlencode çıktısı
                    query = f"{query}&signature={self._sign(query)}"
                    headers = self._get_headers()
                else:
                    query = urlencode(attempt_params)

                request_url = f"{url}?{query}" if query else url

                if method == "GET":
                    response = await self.client.get(request_url, headers=headers)
                elif method == "POST":
                    response = await self.client.post(request_url, headers=headers)
                elif method == "DELETE":
                    response = await self.client.delete(request_url, headers=headers)
                else:
                    raise ValueError(f"Desteklenmeyen HTTP metodu: {method}")

                if response.status_code >= 400:
                    code, msg = self._parse_error(response)
                    err = BinanceAPIError(response.status_code, code, msg, endpoint)

                    if code == ERR_TIMESTAMP_AHEAD:
                        # Saat kaymış — offset'i sıfırla ve yeniden ölç
                        self.logger.warning("⏱️ Timestamp reddedildi, saat yeniden senkronize ediliyor")
                        self._time_offset_ms = None
                        last_error = err
                        if attempt < self.max_retries - 1:
                            continue

                    if response.status_code == 429:
                        self.logger.warning("Rate limit, 60s bekleniyor...")
                        last_error = err
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(60)
                            continue

                    if response.status_code == 418:
                        self.logger.critical("IP ban algılandı! Yönetici müdahalesi gerekli.")
                        raise err

                    if err.is_retryable and attempt < self.max_retries - 1:
                        wait = self.retry_delay * (attempt + 1)
                        self.logger.warning(f"Sunucu hatası ({code}), {wait}s sonra tekrar...")
                        last_error = err
                        await asyncio.sleep(wait)
                        continue

                    # Yeniden denenmeyecek hata — hemen yüzeye çıkar.
                    # Çağıran tarafın normal akışta yakaladığı kodlar (örn.
                    # "margin type zaten ayarlı") ERROR olarak loglanmaz;
                    # aksi halde loglar sahte hatalarla dolar.
                    if code in BENIGN_CODES:
                        self.logger.debug(str(err))
                    else:
                        self.logger.error(str(err))
                    raise err

                return response.json()

            except BinanceAPIError:
                raise

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait = self.retry_delay * (attempt + 1)
                    self.logger.warning(f"Ağ hatası ({type(e).__name__}), {wait}s sonra tekrar...")
                    await asyncio.sleep(wait)
                    continue
                self.logger.error(f"Ağ hatası (denemeler tükendi): {e}")
                raise

        raise last_error or Exception(
            f"API isteği {self.max_retries} denemeden sonra başarısız: {endpoint}"
        )

    # ------------------------------------------------------------------
    # Borsa filtreleri
    # ------------------------------------------------------------------

    async def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Sembolün borsa filtrelerini getir (önbellekli).

        Döner: {"stepSize", "minQty", "tickSize", "minNotional",
                "quantityPrecision", "pricePrecision"}
        """
        now = time.monotonic()
        cached = self._symbol_filters.get(symbol)
        if cached and (now - cached[1]) < self._FILTER_CACHE_TTL:
            return cached[0]

        async with self._filter_lock:
            # Kilit beklerken başka bir görev doldurmuş olabilir
            cached = self._symbol_filters.get(symbol)
            if cached and (time.monotonic() - cached[1]) < self._FILTER_CACHE_TTL:
                return cached[0]

            info = await self._request_with_retry("GET", "/fapi/v1/exchangeInfo")
            found = None
            for s in info.get("symbols", []):
                if s["symbol"] == symbol:
                    found = s
                    break

            if not found:
                raise BinanceAPIError(400, None, f"Sembol borsada bulunamadı: {symbol}")

            by_type = {f["filterType"]: f for f in found.get("filters", [])}
            lot = by_type.get("LOT_SIZE", {})
            price_f = by_type.get("PRICE_FILTER", {})
            notional = by_type.get("MIN_NOTIONAL", {})

            filters = {
                "stepSize": Decimal(str(lot.get("stepSize", "0.001"))),
                "minQty": Decimal(str(lot.get("minQty", "0"))),
                "maxQty": Decimal(str(lot.get("maxQty", "9999999"))),
                "tickSize": Decimal(str(price_f.get("tickSize", "0.01"))),
                "minNotional": Decimal(str(notional.get("notional", "0"))),
                "quantityPrecision": int(found.get("quantityPrecision", 3)),
                "pricePrecision": int(found.get("pricePrecision", 2)),
            }
            self._symbol_filters[symbol] = (filters, time.monotonic())
            self.logger.debug(
                f"📐 {symbol} filtreleri: step={filters['stepSize']} "
                f"tick={filters['tickSize']} minNotional={filters['minNotional']}"
            )
            return filters

    @staticmethod
    def _quantize_down(value: float, step: Decimal) -> Decimal:
        """Değeri adım büyüklüğünün katına AŞAĞI yuvarla."""
        if step <= 0:
            return Decimal(str(value))
        d = Decimal(str(value))
        return (d / step).to_integral_value(rounding=ROUND_DOWN) * step

    async def quantize_quantity(self, symbol: str, quantity: float) -> float:
        """Miktarı LOT_SIZE stepSize'a göre yuvarla."""
        f = await self.get_symbol_filters(symbol)
        q = self._quantize_down(quantity, f["stepSize"])
        return float(q)

    async def quantize_price(self, symbol: str, price: float) -> float:
        """Fiyatı PRICE_FILTER tickSize'a göre yuvarla."""
        f = await self.get_symbol_filters(symbol)
        p = self._quantize_down(price, f["tickSize"])
        return float(p)

    async def validate_order(
        self, symbol: str, quantity: float, reference_price: float
    ) -> None:
        """Emir göndermeden ÖNCE borsa filtrelerine uygunluğu doğrula.

        Uymayan emirler Binance tarafından reddedilir; hatayı burada yakalamak
        pozisyonun yarım açılmasını engeller.
        """
        f = await self.get_symbol_filters(symbol)
        qty = Decimal(str(quantity))

        if qty <= 0:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"Miktar sıfır veya negatif ({quantity}). Yuvarlama sonrası "
                f"stepSize={f['stepSize']} altına düşmüş olabilir.",
            )
        if qty < f["minQty"]:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"Miktar minQty altında: {qty} < {f['minQty']}",
            )
        if qty > f["maxQty"]:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"Miktar maxQty üstünde: {qty} > {f['maxQty']}",
            )

        notional = qty * Decimal(str(reference_price))
        if notional < f["minNotional"]:
            raise BinanceAPIError(
                400, ERR_MIN_NOTIONAL,
                f"Emir değeri MIN_NOTIONAL altında: {notional:.2f} < "
                f"{f['minNotional']} USDT. Pozisyon büyüklüğünü artırın.",
            )

    # ------------------------------------------------------------------
    # Bağlantı / hesap
    # ------------------------------------------------------------------

    async def test_connection(self) -> bool:
        try:
            self.logger.info("🔍 Binance bağlantısı test ediliyor...")
            await self._request_with_retry("GET", "/fapi/v1/ping")
            self.logger.info("✅ Ping başarılı")

            response = await self._request_with_retry("GET", "/fapi/v1/time")
            server_time = response.get("serverTime", 0)
            time_diff = abs(server_time - int(time.time() * 1000))
            if time_diff > 5000:
                self.logger.warning(f"⚠️ Server zaman farkı yüksek: {time_diff}ms (offset uygulanacak)")
            else:
                self.logger.info(f"✅ Server zaman senkronu iyi: {time_diff}ms fark")

            await self._request_with_retry("GET", "/fapi/v2/account", signed=True)
            self.logger.info("✅ API anahtarı geçerli")
            return True
        except Exception as e:
            self.logger.error(f"❌ Bağlantı testi başarısız: {e}")
            return False

    async def get_account_balance(self) -> Optional[float]:
        """Kullanılabilir USDT bakiyesi.

        DİKKAT: Hata durumunda None döner (0.0 DEĞİL). Çağıran taraf bunu
        "bakiye bilinmiyor" olarak ele almalı ve işlem AÇMAMALIDIR. Eskiden
        0.0 dönüyordu ve bu, config'deki sahte bakiyeye düşülmesine yol açıyordu.
        """
        try:
            response = await self._request_with_retry("GET", "/fapi/v2/account", signed=True)
            for asset in response.get("assets", []):
                if asset["asset"] == "USDT":
                    balance = float(asset["availableBalance"])
                    self.logger.info(f"💰 Hesap bakiyesi: {balance:.2f} USDT")
                    return balance
            self.logger.error("USDT varlığı hesapta bulunamadı")
            return None
        except Exception as e:
            self.logger.error(f"Bakiye sorgusu hatası: {e}")
            return None

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        try:
            response = await self._request_with_retry(
                "POST", "/fapi/v1/leverage",
                params={"symbol": symbol, "leverage": leverage}, signed=True,
            )
            self.logger.info(f"⚡ {symbol} leverage {leverage}x olarak ayarlandı")
            return response
        except Exception as e:
            self.logger.error(f"Leverage ayarlama hatası: {e}")
            raise

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        try:
            response = await self._request_with_retry(
                "POST", "/fapi/v1/marginType",
                params={"symbol": symbol, "marginType": margin_type.upper()}, signed=True,
            )
            self.logger.info(f"🔧 {symbol} margin type {margin_type} olarak ayarlandı")
            return response
        except BinanceAPIError as e:
            # -4046: zaten istenen değerde — hata değil
            if e.code == ERR_NO_NEED_MARGIN or "No need to change margin type" in e.msg:
                self.logger.debug(f"Margin type zaten {margin_type}")
                return {"msg": "Already set"}
            raise

    async def get_symbol_precision(self, symbol: str) -> Tuple[int, int]:
        """(quantity_precision, price_precision) — geriye dönük uyumluluk."""
        try:
            f = await self.get_symbol_filters(symbol)
            return f["quantityPrecision"], f["pricePrecision"]
        except Exception as e:
            self.logger.error(f"Precision sorgusu hatası: {e}")
            raise

    def round_quantity(self, quantity: float, precision: int) -> float:
        return float(Decimal(str(quantity)).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_DOWN
        ))

    def round_price(self, price: float, precision: int) -> float:
        return float(Decimal(str(price)).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_DOWN
        ))

    # ------------------------------------------------------------------
    # Emirler
    # ------------------------------------------------------------------

    async def open_market_order(
        self, symbol: str, side: str, quantity: float
    ) -> Dict[str, Any]:
        """Market emri aç ve GERÇEK dolum bilgisiyle dön.

        newOrderRespType=RESULT kullanılır. Varsayılan ACK yanıtı avgPrice=null
        ve executedQty=0 döndürür; buna güvenen kod dolum fiyatını asla öğrenemez.
        """
        quantity = await self.quantize_quantity(symbol, quantity)
        reference_price = await self.get_current_price(symbol)
        if reference_price is None:
            raise BinanceAPIError(
                503, None, f"{symbol} fiyatı alınamadı — emir doğrulanamıyor"
            )
        await self.validate_order(symbol, quantity, reference_price)

        response = await self._request_with_retry(
            "POST", "/fapi/v1/order",
            params={
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
            },
            signed=True,
        )
        self.logger.info(
            f"🚀 Market order açıldı: {symbol} {side} {quantity} "
            f"(dolum: {response.get('avgPrice')} / {response.get('executedQty')})",
            extra={"trade": True},
        )
        return response

    async def get_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Tek bir emrin güncel durumunu sorgula (dolum fiyatı doğrulaması için)."""
        return await self._request_with_retry(
            "GET", "/fapi/v1/order",
            params={"symbol": symbol, "orderId": order_id}, signed=True,
        )

    # --- Koşullu emirler: Algo Order API ------------------------------
    #
    # 2025-12-09'dan itibaren Binance USDⓈ-M Futures'ta koşullu emirler
    # (STOP_MARKET / TAKE_PROFIT_MARKET / STOP / TAKE_PROFIT /
    # TRAILING_STOP_MARKET) /fapi/v1/order üzerinden KABUL EDİLMEZ; -4120 ile
    # reddedilir. Bunlar /fapi/v1/algoOrder üzerinden gönderilir.
    #
    # Farklar:
    #   - stopPrice  -> triggerPrice
    #   - orderId    -> algoId
    #   - listeleme  -> /fapi/v1/openAlgoOrders (eski /fapi/v1/openOrders
    #                   koşullu emirleri GÖSTERMEZ)
    #   - iptal      -> DELETE /fapi/v1/algoOrder?algoId=
    #
    # Aşağıdaki metodlar yanıta `orderId` takma adını ekler; böylece emir
    # kimliğini saklayan mevcut kod (PositionModel.sl_order_id vb.) değişmeden
    # çalışır.

    async def _place_conditional(
        self, symbol: str, params: Dict[str, Any], label: str
    ) -> Dict[str, Any]:
        response = await self._request_with_retry(
            "POST", "/fapi/v1/algoOrder",
            params={"algoType": "CONDITIONAL", "symbol": symbol, **params},
            signed=True,
        )
        algo_id = response.get("algoId")
        # Eski çağıranlar için takma ad
        response["orderId"] = algo_id
        response["isAlgo"] = True
        self.logger.debug(f"{label}: algoId={algo_id}")
        return response

    async def place_stop_loss(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        close_position: bool = True,
        quantity: Optional[float] = None,
    ) -> Dict[str, Any]:
        """STOP_MARKET koşullu emri koy (Algo Order API).

        İki mod vardır ve seçim güvenlik açısından önemlidir:

        - close_position=True: pozisyonun TAMAMINI kapatır, miktar gerektirmez.
          İlk koruma için idealdir. ANCAK aynı yönde ikinci bir closePosition
          stop emri Binance tarafından reddedilir (-4130), bu yüzden mevcut bir
          stop'u DEĞİŞTİRİRKEN kullanılamaz.

        - close_position=False + quantity: reduceOnly stop. Bunlardan birden
          fazlası bir arada bulunabilir; bu sayede stop değiştirilirken önce
          yeni emir konup sonra eskisi iptal edilebilir ve pozisyon bir an bile
          korumasız kalmaz.
        """
        stop_price = await self.quantize_price(symbol, stop_price)
        if stop_price <= 0:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"Geçersiz stop fiyatı: {stop_price}. Giriş fiyatı doğru "
                f"okunamamış olabilir.",
            )

        params: Dict[str, Any] = {
            "side": side,
            "type": "STOP_MARKET",
            "triggerPrice": stop_price,
        }
        if close_position:
            params["closePosition"] = "true"
        else:
            if quantity is None or quantity <= 0:
                raise BinanceAPIError(
                    400, ERR_PRECISION,
                    "reduceOnly stop emri için geçerli bir miktar gerekir",
                )
            params["quantity"] = await self.quantize_quantity(symbol, quantity)
            params["reduceOnly"] = "true"

        response = await self._place_conditional(symbol, params, "stop-loss")
        self.logger.info(
            f"🛡️ Stop Loss kondu: {symbol} @ {stop_price} (algoId={response.get('algoId')})",
            extra={"trade": True},
        )
        return response

    async def place_take_profit(
        self, symbol: str, side: str, stop_price: float, quantity: float
    ) -> Dict[str, Any]:
        """TAKE_PROFIT_MARKET koşullu emri koy — DAİMA reduceOnly.

        reduceOnly olmadan, pozisyon SL ile kapandıktan sonra bekleyen TP emri
        tetiklenirse TERS YÖNDE YENİ bir pozisyon açar.
        """
        quantity = await self.quantize_quantity(symbol, quantity)
        stop_price = await self.quantize_price(symbol, stop_price)

        if quantity <= 0:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"TP miktarı yuvarlama sonrası sıfır ({quantity}). "
                f"Pozisyon stepSize'a göre çok küçük.",
            )
        if stop_price <= 0:
            raise BinanceAPIError(400, ERR_PRECISION, f"Geçersiz TP fiyatı: {stop_price}")

        response = await self._place_conditional(
            symbol,
            {
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "triggerPrice": stop_price,
                "quantity": quantity,
                "reduceOnly": "true",
            },
            "take-profit",
        )
        self.logger.info(
            f"🎯 Take Profit kondu: {symbol} {quantity} @ {stop_price} "
            f"(reduceOnly, algoId={response.get('algoId')})",
            extra={"trade": True},
        )
        return response

    async def get_open_algo_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Açık KOŞULLU emirler.

        /fapi/v1/openOrders bu emirleri GÖSTERMEZ — stop-loss aramak için
        mutlaka bu metod kullanılmalıdır.
        """
        response = await self._request_with_retry(
            "GET", "/fapi/v1/openAlgoOrders", params={"symbol": symbol}, signed=True
        )
        return response if isinstance(response, list) else []

    async def cancel_algo_order(self, algo_id: int) -> Dict[str, Any]:
        """Koşullu emri iptal et."""
        try:
            response = await self._request_with_retry(
                "DELETE", "/fapi/v1/algoOrder", params={"algoId": algo_id}, signed=True
            )
            self.logger.info(f"❌ Koşullu emir iptal edildi: algoId={algo_id}")
            return response
        except BinanceAPIError as e:
            # Emir zaten tetiklenmiş/iptal edilmiş olabilir — idempotent kabul et
            if e.code in (-2011, -4046) or "not exist" in e.msg.lower():
                self.logger.debug(f"Koşullu emir zaten yok: algoId={algo_id}")
                return {"algoStatus": "ALREADY_GONE"}
            raise

    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        try:
            response = await self._request_with_retry(
                "DELETE", "/fapi/v1/order",
                params={"symbol": symbol, "orderId": order_id}, signed=True,
            )
            self.logger.info(f"❌ Order iptal edildi: {symbol} #{order_id}")
            return response
        except BinanceAPIError as e:
            # -2011: emir zaten yok (dolmuş/iptal) — idempotent kabul et
            if e.code == -2011:
                self.logger.debug(f"Order #{order_id} zaten yok")
                return {"status": "ALREADY_GONE"}
            raise

    async def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Açık emirleri getir.

        DİKKAT: Hata durumunda BOŞ LİSTE DÖNDÜRMEZ, istisna fırlatır. Eskiden
        [] dönüyordu; çağıran "iptal edilecek emir yok" sanıp yeni bir SL
        ekliyordu ve borsada mükerrer stop emirleri birikiyordu.
        """
        return await self._request_with_retry(
            "GET", "/fapi/v1/openOrders", params={"symbol": symbol}, signed=True
        )

    async def cancel_all_open_orders(self, symbol: str) -> Dict[str, Any]:
        """Sembolün TÜM açık emirlerini iptal et — normal VE koşullu.

        /fapi/v1/allOpenOrders koşullu (algo) emirleri kapsamaz; onlar tek tek
        iptal edilmelidir. Yalnızca ilkini çağırmak, pozisyon kapandıktan sonra
        borsada asılı kalan stop/TP emirleri bırakır.
        """
        result: Dict[str, Any] = {}
        try:
            result["orders"] = await self._request_with_retry(
                "DELETE", "/fapi/v1/allOpenOrders", params={"symbol": symbol}, signed=True
            )
        except Exception as e:
            self.logger.warning(f"{symbol}: normal emirler iptal edilemedi: {e}")
            result["orders"] = {"error": str(e)}

        cancelled = 0
        try:
            for algo in await self.get_open_algo_orders(symbol):
                await self.cancel_algo_order(int(algo["algoId"]))
                cancelled += 1
        except Exception as e:
            self.logger.warning(f"{symbol}: koşullu emirler iptal edilemedi: {e}")
        result["algo_cancelled"] = cancelled
        return result

    # ------------------------------------------------------------------
    # Pozisyon / piyasa
    # ------------------------------------------------------------------

    async def get_position_risk(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Pozisyon bilgisi. Hata durumunda istisna fırlatır (None DEĞİL).

        None yalnızca "borsa bu sembol için kayıt döndürmedi" anlamına gelir.
        Ağ hatasını 'pozisyon kapandı' sanmak, izlemenin sessizce durmasına
        yol açıyordu.
        """
        response = await self._request_with_retry(
            "GET", "/fapi/v2/positionRisk", params={"symbol": symbol}, signed=True
        )
        if isinstance(response, list) and response:
            return response[0]
        return None

    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """Borsadaki TÜM açık pozisyonlar — restart sonrası kurtarma için."""
        account = await self._request_with_retry("GET", "/fapi/v2/account", signed=True)
        return [
            p for p in account.get("positions", [])
            if float(p.get("positionAmt", 0)) != 0
        ]

    async def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            response = await self._request_with_retry(
                "GET", "/fapi/v1/ticker/price", params={"symbol": symbol}
            )
            return float(response["price"])
        except Exception as e:
            self.logger.error(f"Fiyat sorgusu hatası: {e}")
            return None

    async def get_server_time(self) -> int:
        try:
            response = await self._request_with_retry("GET", "/fapi/v1/time")
            return response.get("serverTime", 0)
        except Exception as e:
            self.logger.error(f"Server zamanı sorgusu hatası: {e}")
            return 0

    async def close(self):
        await self.client.aclose()
