"""
Scalper modülü için saf indikatör kütüphanesi.

Kasıtlı olarak bağımlılıksızdır (yalnız stdlib, numpy YOK): tüm fonksiyonlar
IO yapmaz, global durum tutmaz, saat/rastgelelik kullanmaz. Girdi listeleri
EN ESKİ → EN YENİ sıralıdır (son eleman en güncel/kapanmış mumdur).

Bu modül src/strategies/scalper/types.py sözleşmesine (Candle, Direction)
bağımlıdır; başka hiçbir iç modüle bağımlı değildir.
"""

from __future__ import annotations

from src.strategies.scalper.types import Candle, Direction


# --------------------------------------------------------------------------
# EMA / RSI — kapanış fiyatı serileri üzerinde çalışan indikatörler
# --------------------------------------------------------------------------

def ema(values: list[float], period: int) -> list[float]:
    """Üstel hareketli ortalama.

    Girdiyle aynı uzunlukta liste döner. İlk `period-1` eleman kümülatif
    SMA ile doldurulur (o ana kadarki tüm değerlerin ortalaması); `period-1`
    indeksinden itibaren standart EMA uygulanır (k = 2/(period+1)), seed
    değeri ilk `period` elemanın SMA'sıdır (bu, kümülatif SMA örüntüsünün
    doğal devamıdır).
    """
    n = len(values)
    result: list[float] = [0.0] * n
    if n == 0:
        return result

    k = 2.0 / (period + 1)
    cum_sum = 0.0
    for i in range(n):
        cum_sum += values[i]
        if i < period - 1:
            # Henüz `period` kadar veri yok — kümülatif SMA seed
            result[i] = cum_sum / (i + 1)
        elif i == period - 1:
            # EMA seed'i: ilk `period` değerin SMA'sı
            result[i] = cum_sum / period
        else:
            result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    """Wilder ortalama kazanç/kayıptan RSI değeri türetir."""
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """Wilder smoothing ile RSI serisi.

    Girdiyle aynı uzunlukta liste döner. İlk `period` eleman nötr dolgu
    (50.0) — bu noktalarda henüz seed hesaplanacak kadar delta yok.
    `period` indeksinden itibaren gerçek RSI: seed ortalama kazanç/kayıp
    ilk `period` delta'nın ortalamasıdır, sonrası Wilder recurrence ile
    ((önceki_ortalama * (period-1) + yeni) / period) güncellenir.
    """
    n = len(closes)
    result: list[float] = [50.0] * n
    if n <= period:
        return result

    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [d if d > 0.0 else 0.0 for d in deltas]
    losses = [-d if d < 0.0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period + 1, n):
        gain = gains[i - 1]
        loss = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[i] = _rsi_from_averages(avg_gain, avg_loss)

    return result


# --------------------------------------------------------------------------
# ATR — mum (OHLC) serileri üzerinde çalışan volatilite indikatörü
# --------------------------------------------------------------------------

def _true_ranges(candles: list[Candle]) -> list[float]:
    """TR[j], candles[j+1] mumu için önceki kapanışa göre gerçek aralık."""
    trs: list[float] = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        high, low = candles[i].high, candles[i].low
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)
    return trs


def atr_series(candles: list[Candle], period: int = 14) -> list[float]:
    """Wilder ATR serisi. Girdiyle aynı uzunlukta; ilk `period` eleman 0.0."""
    n = len(candles)
    result: list[float] = [0.0] * n
    if n <= period:
        return result

    trs = _true_ranges(candles)
    atr_val = sum(trs[:period]) / period
    result[period] = atr_val

    for idx in range(period + 1, n):
        tr = trs[idx - 1]
        atr_val = (atr_val * (period - 1) + tr) / period
        result[idx] = atr_val

    return result


def atr(candles: list[Candle], period: int = 14) -> float:
    """Wilder ATR, son mum itibarıyla tek değer. Yetersiz veri → 0.0."""
    if len(candles) < period + 1:
        return 0.0
    return atr_series(candles, period)[-1]


# --------------------------------------------------------------------------
# Bollinger / Donchian — bant ve kanal indikatörleri
# --------------------------------------------------------------------------

def bollinger(closes: list[float], period: int = 20,
              std_mult: float = 2.0) -> tuple[float, float, float]:
    """Bollinger bantları, son değer. Yetersiz veri → (0.0, 0.0, 0.0)."""
    n = len(closes)
    if n < period:
        return (0.0, 0.0, 0.0)

    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((c - middle) ** 2 for c in window) / period
    std = variance ** 0.5

    upper = middle + std_mult * std
    lower = middle - std_mult * std
    return (upper, middle, lower)


def donchian(candles: list[Candle], period: int = 20,
             exclude_last: bool = True) -> tuple[float, float]:
    """(en_yuksek_high, en_dusuk_low). Yetersiz veri → (0.0, 0.0).

    exclude_last=True ise SON mum hariç son `period` mum kullanılır —
    kırılma tespiti bu sayede son mumu kendi kanalına dahil etmez.
    """
    n = len(candles)
    if exclude_last:
        if n < period + 1:
            return (0.0, 0.0)
        window = candles[-period - 1:-1]
    else:
        if n < period:
            return (0.0, 0.0)
        window = candles[-period:]

    highest = max(c.high for c in window)
    lowest = min(c.low for c in window)
    return (highest, lowest)


# --------------------------------------------------------------------------
# Swing (fraktal) noktaları ve RSI diverjansı
# --------------------------------------------------------------------------

def swing_points(candles: list[Candle], left: int = 3,
                  right: int = 3) -> tuple[list[int], list[int]]:
    """Fraktal swing noktaları: (swing_high_indeksleri, swing_low_indeksleri).

    i indeksi swing-high'dır ancak high[i], soldaki `left` VE sağdaki
    `right` mumun high'larından KESİN büyükse. Swing-low aynası (kesin
    küçük). İlk `left` ve son `right` mum doğası gereği swing üretemez.
    """
    n = len(candles)
    highs_idx: list[int] = []
    lows_idx: list[int] = []

    for i in range(left, n - right):
        h = candles[i].high
        l = candles[i].low

        left_highs = (candles[j].high for j in range(i - left, i))
        right_highs = (candles[j].high for j in range(i + 1, i + 1 + right))
        if all(h > x for x in left_highs) and all(h > x for x in right_highs):
            highs_idx.append(i)

        left_lows = (candles[j].low for j in range(i - left, i))
        right_lows = (candles[j].low for j in range(i + 1, i + 1 + right))
        if all(l < x for x in left_lows) and all(l < x for x in right_lows):
            lows_idx.append(i)

    return (highs_idx, lows_idx)


def last_swing_low(candles: list[Candle], left: int = 3,
                    right: int = 3) -> float | None:
    """En son swing-low FİYATI (low değeri). Yoksa None."""
    _, lows_idx = swing_points(candles, left, right)
    if not lows_idx:
        return None
    return candles[lows_idx[-1]].low


def last_swing_high(candles: list[Candle], left: int = 3,
                     right: int = 3) -> float | None:
    """En son swing-high FİYATI (high değeri). Yoksa None."""
    highs_idx, _ = swing_points(candles, left, right)
    if not highs_idx:
        return None
    return candles[highs_idx[-1]].high


def bullish_divergence(candles: list[Candle], rsi_values: list[float],
                        lookback: int = 30) -> bool:
    """Son `lookback` pencerede boğa (bullish) RSI diverjansı var mı.

    Fiyat daha düşük dip yaparken (son iki swing-low karşılaştırması) RSI
    aynı noktalarda daha yüksek dip yaptıysa True. Swing-low'lar
    swing_points(left=2, right=2) ile bulunur; pencerede en az iki
    swing-low yoksa False.
    """
    n = len(candles)
    start = max(0, n - lookback)
    window = candles[start:]

    _, lows_rel = swing_points(window, left=2, right=2)
    if len(lows_rel) < 2:
        return False

    i1 = start + lows_rel[-2]
    i2 = start + lows_rel[-1]

    price1, price2 = candles[i1].low, candles[i2].low
    rsi1, rsi2 = rsi_values[i1], rsi_values[i2]

    return price2 < price1 and rsi2 > rsi1


def bearish_divergence(candles: list[Candle], rsi_values: list[float],
                        lookback: int = 30) -> bool:
    """Ayı (bearish) RSI diverjansı: fiyat daha yüksek tepe, RSI daha
    düşük tepe yaptıysa True. Aynası bullish_divergence."""
    n = len(candles)
    start = max(0, n - lookback)
    window = candles[start:]

    highs_rel, _ = swing_points(window, left=2, right=2)
    if len(highs_rel) < 2:
        return False

    i1 = start + highs_rel[-2]
    i2 = start + highs_rel[-1]

    price1, price2 = candles[i1].high, candles[i2].high
    rsi1, rsi2 = rsi_values[i1], rsi_values[i2]

    return price2 > price1 and rsi2 < rsi1


# --------------------------------------------------------------------------
# Chandelier stop — ATR tabanlı takip eden stop
# --------------------------------------------------------------------------

def chandelier_stop(candles: list[Candle], direction: Direction,
                     atr_mult: float = 2.5, atr_period: int = 14,
                     since_index: int = 0) -> float:
    """Chandelier (avize) stop.

    LONG: max(high[since_index:]) - atr_mult*ATR
    SHORT: min(low[since_index:]) + atr_mult*ATR
    ATR tüm seriden hesaplanır. Yetersiz veri (< atr_period+1 mum veya
    geçersiz since_index) → 0.0 (çağıran 0'ı 'hesaplanamadı' sayar).
    """
    n = len(candles)
    if n < atr_period + 1 or since_index < 0 or since_index >= n:
        return 0.0

    atr_val = atr(candles, atr_period)
    window = candles[since_index:]

    if direction == Direction.LONG:
        highest = max(c.high for c in window)
        return highest - atr_mult * atr_val
    else:
        lowest = min(c.low for c in window)
        return lowest + atr_mult * atr_val


# --------------------------------------------------------------------------
# Para akışı (money flow) — MFI ve CMF, kamuya mal olmuş standart formüller
# --------------------------------------------------------------------------

def mfi_series(candles: list[Candle], period: int = 14) -> list[float]:
    """Money Flow Index (Quong/Soudack).

    typical = (H+L+C)/3; raw_flow = typical*volume. typical önceki mumdan
    artmışsa raw_flow o mumda POZİTİF akışa, azalmışsa NEGATİF akışa yazılır
    (eşitse hiçbirine). Her noktada son `period` mumluk pencerede pozitif ve
    negatif akışlar toplanır: MFI = 100 - 100/(1 + pos_sum/neg_sum).
    neg_sum == 0 → 100.0 (saf alım baskısı). Girdiyle aynı uzunlukta liste;
    ilk `period` eleman nötr dolgu (50.0) — pencere henüz dolmamış.
    """
    n = len(candles)
    result: list[float] = [50.0] * n
    if n <= period:
        return result

    typical = [(c.high + c.low + c.close) / 3.0 for c in candles]
    raw_flow = [typical[i] * candles[i].volume for i in range(n)]

    pos_flow = [0.0] * n
    neg_flow = [0.0] * n
    for i in range(1, n):
        if typical[i] > typical[i - 1]:
            pos_flow[i] = raw_flow[i]
        elif typical[i] < typical[i - 1]:
            neg_flow[i] = raw_flow[i]

    prefix_pos = [0.0] * (n + 1)
    prefix_neg = [0.0] * (n + 1)
    for i in range(n):
        prefix_pos[i + 1] = prefix_pos[i] + pos_flow[i]
        prefix_neg[i + 1] = prefix_neg[i] + neg_flow[i]

    for i in range(period, n):
        start = i - period + 1
        pos_sum = prefix_pos[i + 1] - prefix_pos[start]
        neg_sum = prefix_neg[i + 1] - prefix_neg[start]
        if neg_sum == 0.0:
            result[i] = 100.0
        else:
            money_ratio = pos_sum / neg_sum
            result[i] = 100.0 - 100.0 / (1.0 + money_ratio)

    return result


def cmf(candles: list[Candle], period: int = 20) -> float:
    """Chaikin Money Flow, son mum itibarıyla tek değer.

    Her mum için para akışı çarpanı: ((C-L)-(H-C))/(H-L) — kapanış mumun
    üst yarısındaysa pozitif (alım baskısı), alt yarısındaysa negatif (satış
    baskısı); H==L ise (doji/sıfır aralık) çarpan 0. MFV = çarpan*volume.
    CMF = son `period` mumun MFV toplamı / hacim toplamı. Yetersiz veri veya
    sıfır hacim → 0.0.
    """
    n = len(candles)
    if n < period:
        return 0.0

    window = candles[-period:]
    mfv_sum = 0.0
    vol_sum = 0.0
    for c in window:
        if c.high == c.low:
            multiplier = 0.0
        else:
            multiplier = ((c.close - c.low) - (c.high - c.close)) / (c.high - c.low)
        mfv_sum += multiplier * c.volume
        vol_sum += c.volume

    if vol_sum == 0.0:
        return 0.0
    return mfv_sum / vol_sum


# --------------------------------------------------------------------------
# Smart Money Concepts — yapı kırılımı, likidite süpürmesi, FVG, order block
#
# Bu bölümdeki kavramlar (BOS/CHoCH yapı kırılımı, likidite süpürmesi/stop
# avı, fair value gap, order block) kamuya mal olmuş piyasa mikro-yapısı
# terimleridir; kod tamamen sıfırdan, bu dosyanın geri kalanıyla aynı saf
# stdlib üslubunda yazılmıştır — hiçbir üçüncü taraf ürünün kodu kopyalanmadı.
# --------------------------------------------------------------------------

def market_structure(candles: list[Candle], left: int = 3, right: int = 3) -> dict:
    """Piyasa yapısını (trend + son kırılım olayı) swing dizisinden çıkarır.

    Son iki swing-high ve son iki swing-low karşılaştırılır: ikisi de
    yükseliyorsa (higher-high + higher-low) trend 'bull'; ikisi de
    düşüyorsa (lower-high + lower-low) trend 'bear'; karışıksa veya yeterli
    swing yoksa None.

    Son olay yalnız bir trend belirlenebildiğinde sınıflandırılır: fiyat
    (kapanışla) son swing-high'ın üzerine kırılırsa — trend zaten 'bull'
    ise bu trendin DEVAMIdır → 'BOS'; trend 'bear' iken olursa mevcut
    aşağı trende karşı bir kırılımdır → 'CHOCH' (olası boğa dönüşü). Son
    swing-low'un altına kırılma aynası (bear trendde BOS, bull trendde
    CHOCH). İki yönde de kırılım varsa en son (en yüksek indeksli) olan
    raporlanır.

    Dönüş: {"trend": "bull"|"bear"|None,
            "last_event": "BOS"|"CHOCH"|None,
            "event_index": int|None}
    """
    highs_idx, lows_idx = swing_points(candles, left, right)

    trend: str | None = None
    if len(highs_idx) >= 2 and len(lows_idx) >= 2:
        h1, h2 = candles[highs_idx[-2]].high, candles[highs_idx[-1]].high
        l1, l2 = candles[lows_idx[-2]].low, candles[lows_idx[-1]].low
        if h2 > h1 and l2 > l1:
            trend = "bull"
        elif h2 < h1 and l2 < l1:
            trend = "bear"

    last_event: str | None = None
    event_index: int | None = None

    if trend is not None:
        candidates: list[tuple[int, str]] = []

        if highs_idx:
            last_high_idx = highs_idx[-1]
            last_high_price = candles[last_high_idx].high
            for i in range(last_high_idx + 1, len(candles)):
                if candles[i].close > last_high_price:
                    event_type = "BOS" if trend == "bull" else "CHOCH"
                    candidates.append((i, event_type))
                    break

        if lows_idx:
            last_low_idx = lows_idx[-1]
            last_low_price = candles[last_low_idx].low
            for i in range(last_low_idx + 1, len(candles)):
                if candles[i].close < last_low_price:
                    event_type = "BOS" if trend == "bear" else "CHOCH"
                    candidates.append((i, event_type))
                    break

        if candidates:
            candidates.sort(key=lambda pair: pair[0])
            event_index, last_event = candidates[-1]

    return {"trend": trend, "last_event": last_event, "event_index": event_index}


def liquidity_sweep(candles: list[Candle], left: int = 3, right: int = 3,
                     lookback: int = 30) -> str | None:
    """Likidite süpürmesi (stop avı) tespiti.

    Son 3 mumdan biri, kendisinden ÖNCE oluşmuş bir swing-low'un altına
    fitiliyle sarkıp (low < swing_low) ama KAPANIŞINI o seviyenin üstünde
    tutuşuyla (close > swing_low) altındaki stop emirlerini süpürüp
    reddedildiğini gösteriyorsa → 'low' (boğa işareti — dipten dönüş
    imzası). Aynı mantık bir swing-high'ın üzerine sarkıp kapanışın altında
    kalması için 'high' (ayı işareti). Ne varsa en yeni (en son) mum önce
    değerlendirilir. Eşleşme yoksa None.

    Swing noktaları yalnızca son `lookback` mumluk pencerede aranır.
    """
    n = len(candles)
    start = max(0, n - lookback)
    window = candles[start:]

    highs_rel, lows_rel = swing_points(window, left, right)
    highs_idx = [start + i for i in highs_rel]
    lows_idx = [start + i for i in lows_rel]

    last_n = min(3, n)
    for offset in range(last_n):
        idx = n - 1 - offset
        candle = candles[idx]

        prior_lows = [li for li in lows_idx if li < idx]
        if prior_lows:
            swing_low_price = candles[prior_lows[-1]].low
            if candle.low < swing_low_price and candle.close > swing_low_price:
                return "low"

        prior_highs = [hi for hi in highs_idx if hi < idx]
        if prior_highs:
            swing_high_price = candles[prior_highs[-1]].high
            if candle.high > swing_high_price and candle.close < swing_high_price:
                return "high"

    return None


def find_fvg(candles: list[Candle], lookback: int = 50) -> list[dict]:
    """Fair Value Gap (3 mumlu fiyat dengesizliği) tarar.

    Üç ardışık mumda ortadaki mum [i] atlanıp uçlar karşılaştırılır:
    mum[i-1].high < mum[i+1].low → aralarında fiyatın hiç işlem görmediği
    bir boşluk var → boğa FVG {top: mum[i+1].low, bottom: mum[i-1].high,
    index: i, direction: 'bull', filled: <bool>}. Ayı FVG aynası
    (mum[i-1].low > mum[i+1].high). 'filled': sonraki mumlardan biri
    boşluğun en uzak sınırına değdiyse (bull → low <= bottom, bear →
    high >= top) True.

    Son `lookback` mumluk pencerede arar; sonucu dolmamış (filled=False)
    olanlar önde olacak şekilde sıralar (aksi halde kronolojik sıra korunur).
    """
    n = len(candles)
    start = max(0, n - lookback)
    gaps: list[dict] = []

    for i in range(max(1, start), n - 1):
        prev_c = candles[i - 1]
        next_c = candles[i + 1]

        if prev_c.high < next_c.low:
            top, bottom, direction = next_c.low, prev_c.high, "bull"
        elif prev_c.low > next_c.high:
            top, bottom, direction = prev_c.low, next_c.high, "bear"
        else:
            continue

        filled = False
        if direction == "bull":
            for c in candles[i + 2:]:
                if c.low <= bottom:
                    filled = True
                    break
        else:
            for c in candles[i + 2:]:
                if c.high >= top:
                    filled = True
                    break

        gaps.append({
            "top": top,
            "bottom": bottom,
            "index": i,
            "direction": direction,
            "filled": filled,
        })

    gaps.sort(key=lambda g: g["filled"])
    return gaps


def equilibrium(candles: list[Candle], left: int = 3, right: int = 3) -> float | None:
    """Son anlamlı dealing range'in orta noktası (ICT %50 seviyesi).

    swing_points ile bulunan en son swing-high ve en son swing-low FİYATLARI
    kullanılır (hangisi kronolojik olarak daha yeni olursa olsun ikisi de
    gereklidir — biri eksikse aralık tanımsızdır). eq = (son_swing_high +
    son_swing_low) / 2. Ya swing-high ya da swing-low bulunamazsa None.
    """
    highs_idx, lows_idx = swing_points(candles, left, right)
    if not highs_idx or not lows_idx:
        return None

    last_high = candles[highs_idx[-1]].high
    last_low = candles[lows_idx[-1]].low
    return (last_high + last_low) / 2.0


def find_order_block(candles: list[Candle], direction: Direction,
                      lookback: int = 40) -> dict | None:
    """Basitleştirilmiş order block (kurumsal emir bloğu) tespiti.

    LONG için: son `lookback` mumluk pencerede, ardından gelen 3 mumun
    toplam gövde büyüklüğü (|close-open| toplamı) kendi gövdesinin 2
    katından büyük olan VE bu 3 mumun sonunda fiyat kendi high'ının
    üzerine kapanan (kararlı bir yukarı hareket başlattığını doğrulayan)
    SON kırmızı (close<open) mum aranır. Bu mumun [low, high] aralığı,
    kurumsal alım emirlerinin biriktiği ve fiyatın geri dönüp tepki
    verebileceği bölge olarak kabul edilir. SHORT tam aynası (son yeşil
    mum, 3 mum sonunda kendi low'unun altına kapanış).

    Bulunamazsa None; bulunursa {"low": float, "high": float, "index": int}.
    """
    n = len(candles)
    if n < 4:
        return None

    start = max(0, n - lookback)
    last_i = n - 4
    if last_i < start:
        return None

    for i in range(last_i, start - 1, -1):
        ob = candles[i]
        impulse = candles[i + 1:i + 4]
        body_ob = abs(ob.close - ob.open)
        body_impulse = sum(abs(c.close - c.open) for c in impulse)

        if body_impulse <= 2.0 * body_ob:
            continue

        if direction == Direction.LONG:
            if ob.close >= ob.open:
                continue
            if impulse[-1].close <= ob.high:
                continue
            return {"low": ob.low, "high": ob.high, "index": i}
        else:
            if ob.close <= ob.open:
                continue
            if impulse[-1].close >= ob.low:
                continue
            return {"low": ob.low, "high": ob.high, "index": i}

    return None
