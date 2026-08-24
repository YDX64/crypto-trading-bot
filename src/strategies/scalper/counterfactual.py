"""Karşı-olgu defteri (counterfactual ledger) — SAF çekirdek (D27).

Sorun: `intent.py` (D24) reddedilen giriş niyetlerini SAYAR ama "girseydik ne
olurdu" sorusunu YANITLAMAZ. Bu yüzden bir kapının kâr mı koruduğu yoksa kâr
mı kestiği ölçülemiyor: "rejim kapısı 40 girişi reddetti" cümlesi tek başına
ne iyi ne kötü haberdir.

Bu modül o sorunun SAF çekirdeğidir: reddedilen bir niyet için "mevcut TP1/SL
kurallarıyla girilseydi" senaryosunu, niyet anından SONRAKİ mumlarla bar-bar
yürütür. Durum tutma, disk/DB yazımı ve motor kancası BU DOSYADA DEĞİLDİR.

Tasarım kuralları (forensics.py / intent.py ile aynı üslup):

1. **SAF.** IO yok, saat okuma (`time.time()`) yok, global durum yok,
   rastgelelik yok. Zaman damgaları ve `now_epoch` DAİMA çağıran tarafından
   geçirilir; böylece her kural tek tek ve deterministik test edilebilir.
2. **Look-ahead yok.** Simülasyona YALNIZ niyet anında ya da sonrasında
   AÇILMIŞ mumlar girer (`window()`); niyet anını İÇEREN yarım mum DIŞLANIR.
   Gerekçe fonksiyonun docstring'indedir.
3. **Karamsar taraf.** Aynı mumda hem stop hem TP1 seviyesi görülürse STOP
   kazanır (mum içi sıra bilinmez). Böylece defter "girseydik kazanırdık"
   yönünde sistematik olarak şişmez.
4. **Uydurma sayı YASAK.** Veri yoksa alan `None` ve `measured=False`'tur;
   eksik veri asla 0 ya da "açık pozisyon" gibi bir sayıya çevrilmez.

Modelin dürüst sınırları
------------------------
`simulate()` gerçek motorun bir KOPYASI DEĞİLDİR. Modellenen:

* İlk TP1 seviyesi (çıkış merdiveninin İLK bacağı) ve ilk (yapısal) stop.

Modellenmeyen:

* **TP2** ve kalan koşucu (runner),
* **chandelier iz süren stop** (`exits._update_trailing`),
* TP1 dolunca **break-even'a çekme**,
* **8 saatlik reaper** (D4 — süresi dolan pozisyonun kapatılması),
* **komisyon**, **kayma (slippage)** ve **kısmi dolum**,
* emir tipleri, likidite ve borsa reddi.

Bu yüzden çıktı "bu işlem şu kadar kazandırırdı" DEĞİL, aynı kurallarla
hesaplanmış kaba bir üst/alt sınırdır. Karşılaştırma DAİMA göreli yapılmalı
(aynı model iki kova için de aynı şekilde iyimser/karamsardır).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.strategies.scalper import intent
from src.strategies.scalper.types import Candle

#: Kayıt biçiminin sürümü. Alan eklemek/çıkarmak bunu ARTIRIR; eski satırlar
#: raporda karışmasın diye her satır kendi sürümünü taşır.
COUNTERFACTUAL_VERSION = 1

#: Simülasyon modelinin kimliği. Model değişirse (ör. TP2 eklenirse) BU AD
#: değişir — eski ve yeni ölçümler aynı ortalamaya karıştırılamasın.
MODEL_ID = "tp1_or_stop_v1"

OUTCOME_TP1 = "tp1"
OUTCOME_STOP = "stop"
OUTCOME_OPEN = "open"
OUTCOME_NO_DATA = "no_data"

KNOWN_OUTCOMES: frozenset = frozenset(
    {OUTCOME_TP1, OUTCOME_STOP, OUTCOME_OPEN, OUTCOME_NO_DATA}
)

#: `summarize()` çıktısındaki toplam satırının sözde-gerekçesi. `intent.py`'nin
#: gerçek gerekçeleriyle ÇAKIŞMAZ (alt çizgi ile başlar).
REASON_TOTAL = "_toplam_"
REASON_TOTAL_LABEL = "tüm satırlar (toplam)"

SECONDS_PER_HOUR = 3600.0

#: %95 normal yaklaşım katsayısı. Küçük örneklemde t-dağılımı daha doğrudur;
#: burada kasıtlı olarak z kullanılır (rapor bir HAKEM değil, bir işarettir)
#: ve `n` alanı satırın yanında raporlanır ki okuyan güveni kendi tartsın.
Z95 = 1.96


# --------------------------------------------------------------------------
# Savunmalı küçük yardımcılar (forensics.py / intent.py ile aynı üslup)
# --------------------------------------------------------------------------

def _f(value: Any) -> Optional[float]:
    """Sonlu float'a çevir; olmuyorsa None (savunmalı okuma)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _s(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _token(value: Any) -> Optional[str]:
    """Sabit alanlar (outcome/reason) için küçük harfli belirteç."""
    text = _s(value)
    return None if text is None else text.lower()


def _symbol(value: Any) -> Optional[str]:
    """Sembol büyük harfe indirgenir (`btcusdt` ile `BTCUSDT` aynı kova)."""
    text = _s(value)
    return None if text is None else text.upper()


def _direction(value: Any) -> Optional[str]:
    """`Direction` enum'u ya da düz metin — büyük harfli metne indirger."""
    raw = getattr(value, "value", value)
    text = _s(raw)
    return None if text is None else text.upper()


def _positive(value: Any) -> Optional[float]:
    """Sonlu ve KESİN POZİTİF float; değilse None (fiyatlar için)."""
    out = _f(value)
    return None if out is None or out <= 0 else out


def _leverage(value: Any) -> Optional[int]:
    """Pozitif tam sayı kaldıraç; bozuk/0/negatif → None.

    0 ya da negatif kaldıraç ROI'yi anlamsız kılar; sessizce 1 varsaymak
    uydurma sayı üretmek olurdu (bkz. modül başlığı, madde 4).
    """
    out = _f(value)
    if out is None or out <= 0:
        return None
    return int(out)


def _round(value: Any, digits: int) -> Optional[float]:
    out = _f(value)
    return None if out is None else round(out, digits)


def _dup_count(value: Any) -> int:
    """`dup_count` en az 1'dir (bir satır en az kendisini temsil eder)."""
    out = _f(value)
    if out is None or out < 1:
        return 1
    return int(out)


def _open_epoch(candle: Any) -> Optional[float]:
    """Mumun AÇILIŞ epoch'u (saniye). `Candle.open_time` MİLİSANİYEdir."""
    ms = _f(getattr(candle, "open_time", None))
    return None if ms is None else ms / 1000.0


def _close_epoch(candle: Any) -> Optional[float]:
    """Mumun KAPANIŞ epoch'u (saniye). `Candle.close_time` MİLİSANİYEdir."""
    ms = _f(getattr(candle, "close_time", None))
    return None if ms is None else ms / 1000.0


def _horizons(value: Any) -> List[float]:
    """Ufuk listesini normalize et: pozitif, tekil, ARTAN sıralı.

    Bozuk/negatif/sıfır ufuklar sessizce atılır: bir ölçüm ufku ancak
    gelecekte bir an olabilir.
    """
    seen: Dict[float, None] = {}
    for item in value or []:
        hours = _f(item)
        if hours is None or hours <= 0:
            continue
        seen[hours] = None
    return sorted(seen.keys())


def _move_pct(
    entry: Optional[float], exit_price: Optional[float], side: Optional[str]
) -> Optional[float]:
    """İşlem LEHİNE yüzde hareket.

    LONG: `(çıkış - giriş) / giriş * 100`; SHORT aynı büyüklüğün ters işaretlisi.
    Yön bilinmiyorsa ya da fiyatlardan biri yoksa `None` — 0 DEĞİL.
    """
    if entry is None or entry <= 0 or exit_price is None:
        return None
    if side == "LONG":
        raw = (exit_price - entry) / entry * 100.0
    elif side == "SHORT":
        raw = (entry - exit_price) / entry * 100.0
    else:
        return None
    return _round(raw, 6)


# --------------------------------------------------------------------------
# 1. Bekleyen kayıt kurucu
# --------------------------------------------------------------------------

def build_pending(
    *,
    at: str,
    at_epoch: float,
    symbol: Any,
    direction: Any,
    reason: Any,
    price: Optional[float],
    stop_price: Optional[float],
    tp1_price: Optional[float],
    leverage: Optional[int],
    strategy: Any = None,
    source: Any = None,
    horizons_h: Sequence[float],
    intent_id: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Reddedilen bir niyeti "ölçüm bekleyen" satıra çevir — SAF.

    `at` (ISO metin) ve `at_epoch` (saniye) çağıran tarafından geçilir: bu
    modül saat OKUMAZ. `at_epoch` hem olgunlaşma hem de look-ahead penceresi
    için TEK referans andır.

    Tüm okumalar savunmalıdır: bozuk bir sayı satırı düşürmez, yalnız o alan
    `None` kalır. `dup_count` 1 ile başlar — çağıran aynı sembol/yön/gerekçe
    için tekrarlayan niyetleri tek satırda toplayacaksa bu alanı artırır.
    """
    return {
        "at": _s(at),
        "at_epoch": _f(at_epoch),
        "symbol": _symbol(symbol),
        "direction": _direction(direction),
        "reason": _token(reason),
        "price": _f(price),
        "stop_price": _f(stop_price),
        "tp1_price": _f(tp1_price),
        "leverage": _leverage(leverage),
        "strategy": _s(strategy),
        "source": _s(source),
        "intent_id": _s(intent_id),
        "horizons_h": _horizons(horizons_h),
        "dup_count": 1,
        "model": MODEL_ID,
        "version": COUNTERFACTUAL_VERSION,
        "extra": dict(extra) if isinstance(extra, dict) else {},
    }


# --------------------------------------------------------------------------
# 2. Mum seçicileri
# --------------------------------------------------------------------------

def price_at(
    candles: Sequence[Candle],
    target_epoch: float,
    *,
    min_epoch: Optional[float] = None,
) -> Optional[float]:
    """`target_epoch` (SANİYE) anındaki ya da ondan ÖNCEKİ son KAPANMIŞ fiyat.

    `Candle.close_time` MİLİSANİYE olduğundan ölçüt `close_time/1000 <=
    target_epoch`'tur. Uygun mum yoksa `None` döner — en yakın mumu "yeterince
    yakın" sayıp kullanmak look-ahead'e kapı açardı.

    `min_epoch` verilirse ALT SINIR da uygulanır (`close_time/1000 >=
    min_epoch`). D27 incelemesi (O3): alt sınır yokken veri boşluğunda niyet
    anından ÖNCE kapanmış bir mumun `close`'u dönebiliyordu ve o sayı
    `measured=True` bir satırın `horizons[].roi_pct` alanına UYDURMA bir değer
    olarak giriyordu. (Bu geçmişe bakıştır, geleceğe değil — yani look-ahead
    değil; ama "o anda geçerli fiyat" iddiası yine de yanlıştır.) `resolve`
    DAİMA `min_epoch=at_epoch` geçer; alt sınır verilmezse davranış eskisiyle
    birebir aynıdır.

    Liste eski→yeni sıralı VARSAYILMAZ: en büyük `close_time` aranarak
    bulunur, böylece sırasız bir girdi sessiz bir yanlış fiyat üretemez.
    """
    target = _f(target_epoch)
    if target is None:
        return None
    floor = _f(min_epoch)

    best_epoch: Optional[float] = None
    best_close: Optional[float] = None
    for candle in candles or []:
        closed_at = _close_epoch(candle)
        if closed_at is None or closed_at > target:
            continue
        if floor is not None and closed_at < floor:
            continue
        close = _f(getattr(candle, "close", None))
        if close is None:
            continue
        if best_epoch is None or closed_at > best_epoch:
            best_epoch = closed_at
            best_close = close
    return best_close


def window(
    candles: Sequence[Candle], start_epoch: float, end_epoch: float
) -> List[Candle]:
    """`[start_epoch, end_epoch]` aralığına TAM sığan mumlar, zaman sırasıyla.

    Ölçüt: `open_time/1000 >= start_epoch` **ve** `close_time/1000 <=
    end_epoch`.

    **Look-ahead yok — `start_epoch`'tan ÖNCE açılmış mum DIŞLANIR.** Niyet
    anını İÇEREN yarım mumun `high`/`low`'u niyet anından ÖNCEKİ fiyatları da
    kapsar. O mum simülasyona girseydi, simülasyon niyet anında henüz
    OLMAMIŞ bir hareketi görebilir — daha kötüsü, niyetten ÖNCE olmuş bir
    hareketi niyetten sonra olmuş gibi sayabilirdi. Bu, karşı-olgu defterini
    sessizce yanlı hâle getirir (özellikle stop bacağını). Bu yüzden kural
    katıdır: mum tamamen niyet anından SONRA açılmış olmalıdır.

    Aralığın sonunda taşan (yani `end_epoch`'tan sonra kapanan) mum da
    DIŞLANIR: ufuk penceresinin dışındaki bir hareketi içeri sızdırmamak
    ölçümü ufka sadık tutar.
    """
    start = _f(start_epoch)
    end = _f(end_epoch)
    if start is None or end is None:
        return []

    picked: List[Any] = []
    for candle in candles or []:
        opened_at = _open_epoch(candle)
        closed_at = _close_epoch(candle)
        if opened_at is None or closed_at is None:
            continue
        if opened_at < start or closed_at > end:
            continue
        picked.append((opened_at, candle))
    picked.sort(key=lambda item: item[0])
    return [candle for _, candle in picked]


# --------------------------------------------------------------------------
# 3. Bar-bar simülasyon
# --------------------------------------------------------------------------

def _no_data(gap: Optional[str] = None) -> Dict[str, Any]:
    """Ölçülemeyen senaryonun kaydı — TÜM sayı alanları `None`.

    `gap` NEDEN ölçülemediğini söyler (`None` = mum hiç yoktu). Bugünkü tek
    değer `GAP_PARTIAL_WINDOW`'dur; rapor "hiç mum yoktu" ile "pencere ufku
    kapsamıyordu"yu ayırt edebilsin diye ayrı tutulur.
    """
    out: Dict[str, Any] = {
        "outcome": OUTCOME_NO_DATA,
        "exit_price": None,
        "bars": 0,
        "at_epoch": None,
        "price_move_pct": None,
        "model": MODEL_ID,
    }
    if gap:
        out["gap"] = gap
    return out


#: `sim.gap` — pencere VARDI ama ufkun BAŞINI kapsamıyordu (bkz. `_covers`).
GAP_PARTIAL_WINDOW = "partial_window"


def _time_ordered(rows: Sequence[Any]) -> List[Any]:
    """Mumları açılış zamanına göre KARARLI sırala (savunma).

    `window()` zaten sıralı döndürür; ama `simulate` doğrudan da çağrılabilir
    ve sırasız bir liste "önce hangi seviye vuruldu" sorusuna SESSİZCE yanlış
    cevap verirdi (D27 incelemesi-2, bulgu 12). `price_at`/`window` ile aynı
    ilke: sırasız girdi sessiz bir yanlış sonuç üretemez.

    Açılış zamanı OKUNAMAYAN mumlar sona alınır ve kendi aralarındaki sıraları
    korunur (kararlı sıralama) — atılmazlar, çünkü `simulate` onların
    `high`/`low`'unu yine de görmelidir.
    """
    keyed = []
    for index, candle in enumerate(rows):
        opened = _open_epoch(candle)
        keyed.append(
            ((0, opened, index) if opened is not None else (1, 0.0, index), candle)
        )
    keyed.sort(key=lambda item: item[0])
    return [candle for _, candle in keyed]


def _bar_seconds(candles: Sequence[Any]) -> Optional[float]:
    """Mum aralığını (saniye) VERİDEN çıkar — sabit varsayılmaz.

    Önce ardışık açılışların en küçük pozitif farkı; o yoksa tek bir mumun
    `close_time - open_time` süresi kullanılır. Hiçbiri okunamıyorsa `None`.
    """
    opens = [o for o in (_open_epoch(c) for c in candles or []) if o is not None]
    diffs = [b - a for a, b in zip(opens, opens[1:]) if b > a]
    if diffs:
        return min(diffs)
    for candle in candles or []:
        opened = _open_epoch(candle)
        closed = _close_epoch(candle)
        if opened is not None and closed is not None and closed > opened:
            return closed - opened
    return None


def _covers(candles: Sequence[Any], start_epoch: float) -> bool:
    """Pencere ufkun BAŞINI gerçekten kapsıyor mu?

    D27 incelemesi (Y1): sembol bir süre tarama evreninden çıkarsa
    `window()` ufkun yalnız KUYRUĞUNU döndürür ve simülasyon o kuyruğu tüm
    ufuk sanıp `measured=True` yazardı. Probe: 20 saat önceki niyet, 12.5
    saatlik mum penceresi → `bars: 6`, `measured: True`, `pnl_roi_pct: 0.0`.
    Yani "ölçemedik" demesi gereken satır, kendinden emin bir sayı yazıyordu.

    Ölçüt: İLK mum niyet anına EN ÇOK BİR MUM uzaklıkta açılmış olmalıdır.
    (Niyet anını içeren yarım mum look-ahead yüzünden dışlandığı için bir
    mumluk boşluk NORMALDİR; iki mum ve fazlası veri boşluğudur.)
    """
    rows = list(candles or [])
    if not rows:
        return False
    first_open = _open_epoch(rows[0])
    start = _f(start_epoch)
    if first_open is None or start is None:
        return False
    step = _bar_seconds(rows)
    if step is None or step <= 0:
        return False
    # Kayan nokta toleransı: `<=` sınırındaki mum KABUL edilir.
    return (first_open - start) <= step * 1.000001


def simulate(
    *,
    direction: Any,
    entry_price: Optional[float],
    stop_price: Optional[float],
    tp1_price: Optional[float],
    candles: Sequence[Candle],
) -> Dict[str, Any]:
    """"Girilseydi ne olurdu" — bar-bar, KASITLI OLARAK KARAMSAR simülasyon.

    Her mumda önce STOP, sonra TP1 aranır:

    * LONG  → stop: `low <= stop_price`, tp1: `high >= tp1_price`
    * SHORT → stop: `high >= stop_price`, tp1: `low <= tp1_price`

    **Aynı mumda ikisi de vurursa STOP kazanır.** Mum içi sıra (önce dip mi
    tepe mi görüldü) 1 mumluk OHLC'den bilinemez; iki olasılıktan iyi olanı
    seçmek karşı-olgu defterini "girseydik kazanırdık" yönünde sistematik
    olarak şişirir. Kapı kararlarını bu defterle sorgulayacağımız için yanlılık
    KARAMSAR tarafta bırakılır: ölçüm bir kapıyı haksız yere suçlamasın.

    Hiçbir seviye vurulmazsa sonuç `open`'dır ve `exit_price` SON mumun
    kapanışıdır (mark-to-market) — ufkun sonunda pozisyon hâlâ açık demektir.

    `stop_price` ya da `tp1_price` yoksa/≤0 ise o bacak DEVRE DIŞIdır (yalnız
    diğeri aranır); ikisi de yoksa sonuç kaçınılmaz olarak `open`'dır.

    `entry_price` yok/≤0 ise, yön çözülemiyorsa ya da mum yoksa sonuç
    `no_data`'dır — 0 ya da "başabaş" gibi bir sayı UYDURULMAZ.

    Modelin sınırları modül başlığındadır (TP2, iz süren stop, break-even,
    8h reaper, komisyon, kayma ve kısmi dolum MODELLENMEZ).
    """
    side = _direction(direction)
    entry = _positive(entry_price)
    # SIRALAMA SAVUNMASI: sırasız bir liste "önce hangi seviye vuruldu"
    # sorusuna sessizce yanlış cevap verirdi (bkz. `_time_ordered`).
    rows = _time_ordered(candles or [])
    if entry is None or side not in ("LONG", "SHORT") or not rows:
        return _no_data()

    stop = _positive(stop_price)
    tp1 = _positive(tp1_price)

    bars = 0
    last_close: Optional[float] = None
    last_epoch: Optional[float] = None

    for candle in rows:
        bars += 1
        high = _f(getattr(candle, "high", None))
        low = _f(getattr(candle, "low", None))
        close = _f(getattr(candle, "close", None))
        closed_at = _close_epoch(candle)
        if close is not None:
            last_close = close
        if closed_at is not None:
            last_epoch = closed_at

        if side == "LONG":
            hit_stop = stop is not None and low is not None and low <= stop
            hit_tp1 = tp1 is not None and high is not None and high >= tp1
        else:
            hit_stop = stop is not None and high is not None and high >= stop
            hit_tp1 = tp1 is not None and low is not None and low <= tp1

        # SIRA ÖNEMLİ: stop önce sorulur = aynı mumda beraberlikte stop kazanır.
        if hit_stop:
            return {
                "outcome": OUTCOME_STOP,
                "exit_price": stop,
                "bars": bars,
                "at_epoch": closed_at,
                "price_move_pct": _move_pct(entry, stop, side),
                "model": MODEL_ID,
            }
        if hit_tp1:
            return {
                "outcome": OUTCOME_TP1,
                "exit_price": tp1,
                "bars": bars,
                "at_epoch": closed_at,
                "price_move_pct": _move_pct(entry, tp1, side),
                "model": MODEL_ID,
            }

    return {
        "outcome": OUTCOME_OPEN,
        "exit_price": last_close,
        "bars": bars,
        "at_epoch": last_epoch,
        "price_move_pct": _move_pct(entry, last_close, side),
        "model": MODEL_ID,
    }


# --------------------------------------------------------------------------
# 4. Bekleyen kaydı çözme
# --------------------------------------------------------------------------

def resolve(
    *,
    pending: Dict[str, Any],
    candles: Sequence[Candle],
    now_epoch: float,
) -> Optional[Dict[str, Any]]:
    """Olgunlaşmış bir bekleyen kaydı çöz; olgunlaşmadıysa `None` döndür.

    Olgunlaşma ölçütü EN BÜYÜK ufka göredir: `now_epoch >= at_epoch +
    max(horizons_h) * 3600`. Erken çözmek daha kısa ufukları ölçebilirdi ama
    kayıt iki kez yazılırdı; kayıt tek ve nihai olsun diye en uzun ufuk
    beklenir. `None` dönerse çağıran kaydı `pending` bırakır.

    Kenar durumlar (uydurma sayı YASAK, ama kayıt da sonsuza dek bekleyemez):

    * `now_epoch` sayı DEĞİLSE `None` döner — bozuk bir saatle ölçüm yapmak
      yerine bir tur beklemek daha ucuzdur (değer çağıranın kendisinindir).
    * `at_epoch` sayı DEĞİLSE kayıt ölçülemez: olgunlaşmış sayılıp
      `measured=False` ile KAPATILIR. Aksi hâlde bozuk bir satır kuyrukta
      sonsuza dek birikirdi.
    * `horizons_h` boşsa ufuk 0 kabul edilir: kayıt hemen olgunlaşır, penceresi
      boştur ve `no_data` olarak kapanır.
    * Pencerede hiç mum yoksa `sim` `no_data`, `measured` `False`'tur ve TÜM
      sayı alanları `None` kalır.
    * **KISMİ pencere de `no_data`'dır** (D27 incelemesi Y1): mumlar var ama
      ilk mum niyet anından bir mumdan fazla uzaktaysa ufkun BAŞI eksiktir.
      O kuyruğu tüm ufuk sanıp ölçmek uydurma sayı üretir; satır
      `sim.gap="partial_window"` ile kapatılır. Ertelemenin faydası YOKTUR:
      mum penceresi zamanla İLERİ kayar, eksik baş geri gelmez.
    """
    row = pending if isinstance(pending, dict) else {}

    now = _f(now_epoch)
    if now is None:
        return None

    at_epoch = _f(row.get("at_epoch"))
    horizons = _horizons(row.get("horizons_h"))
    horizon_h = horizons[-1] if horizons else 0.0

    if at_epoch is not None and now < at_epoch + horizon_h * SECONDS_PER_HOUR:
        return None

    side = _direction(row.get("direction"))
    entry = _positive(row.get("price"))
    lev = _leverage(row.get("leverage"))

    horizon_rows: List[Dict[str, Any]] = []
    for hours in horizons:
        target = None if at_epoch is None else at_epoch + hours * SECONDS_PER_HOUR
        # ALT SINIR (O3): niyet anından ÖNCE kapanmış bir mumun fiyatı bu
        # ufkun fiyatı DEĞİLDİR — veri boşluğunda `None` yazılır, uydurulmaz.
        price = (
            None if target is None
            else price_at(candles, target, min_epoch=at_epoch)
        )
        move = _move_pct(entry, price, side)
        roi = None if (move is None or lev is None) else _round(move * lev, 6)
        horizon_rows.append(
            {"h": hours, "price": price, "move_pct": move, "roi_pct": roi}
        )

    if at_epoch is None:
        picked: List[Candle] = []
    else:
        picked = window(
            candles, at_epoch, at_epoch + horizon_h * SECONDS_PER_HOUR
        )
    if picked and at_epoch is not None and not _covers(picked, at_epoch):
        # KISMİ PENCERE (Y1): mumlar var ama ufkun BAŞI eksik. Bunu ölçmek,
        # ufkun yalnız kuyruğunu tüm ufuk sanmaktır — "uydurma sayı YASAK"
        # kuralının ihlali. Ertelemek ÇÖZMEZ (mum penceresi ileri kayar,
        # eksik baş bir daha GERİ GELMEZ); bu yüzden satır dürüstçe
        # `no_data` olarak KAPATILIR ve nedeni `sim.gap`te durur.
        sim = _no_data(GAP_PARTIAL_WINDOW)
    else:
        sim = simulate(
            direction=side,
            entry_price=entry,
            stop_price=row.get("stop_price"),
            tp1_price=row.get("tp1_price"),
            candles=picked,
        )
    sim["horizon_h"] = horizon_h

    sim_move = _f(sim.get("price_move_pct"))
    pnl_roi = (
        None if (sim_move is None or lev is None) else _round(sim_move * lev, 6)
    )

    return {
        "at": _s(row.get("at")),
        "at_epoch": at_epoch,
        "symbol": _symbol(row.get("symbol")),
        "direction": side,
        "reason": _token(row.get("reason")),
        "strategy": _s(row.get("strategy")),
        "source": _s(row.get("source")),
        "intent_id": _s(row.get("intent_id")),
        "dup_count": _dup_count(row.get("dup_count")),
        "price": _f(row.get("price")),
        "stop_price": _f(row.get("stop_price")),
        "tp1_price": _f(row.get("tp1_price")),
        "leverage": lev,
        "model": _s(row.get("model")) or MODEL_ID,
        "version": int(_f(row.get("version")) or COUNTERFACTUAL_VERSION),
        "resolved_at_epoch": now,
        "horizons": horizon_rows,
        "sim": sim,
        "pnl_roi_pct": pnl_roi,
        "measured": sim.get("outcome") != OUTCOME_NO_DATA,
        # D27/B: `extra` ÇÖZÜLMÜŞ satıra da taşınır. İçindeki `plan_source`
        # ("signal" mi "roi_policy" mi) modelin dürüstlük etiketidir: planı
        # olmayan niyetlerde (TV sağlaması yolu) stop/TP1 ROI politikasından
        # YAKLAŞIKLANIR ve raporu okuyan bunu ayırt edebilmelidir. Burada
        # düşürülürse etiket JSONL'e hiç ulaşmaz.
        "extra": dict(row.get("extra")) if isinstance(row.get("extra"), dict) else {},
    }


# --------------------------------------------------------------------------
# 5. Özet (ret gerekçesi × sonuç)
# --------------------------------------------------------------------------

def _label(reason: str) -> str:
    if reason == REASON_TOTAL:
        return REASON_TOTAL_LABEL
    return intent.REASON_LABELS.get(reason, "")


#: `profit_factor is None` iki ZIT anlama gelebiliyordu: "hiç ölçüm yok" ve
#: "hiç kayıp yok" (payda 0 → PF matematiksel olarak sonsuz). D27 incelemesi
#: (D4): ayrımı `profit_factor_note` taşır, böylece rapor satırı sıralanırken
#: ve okunurken karıştırılamaz.
PF_NOTE_NO_LOSS = "no_loss"        # payda 0 — kayıp yok, PF "sonsuz"
PF_NOTE_NO_SAMPLE = "no_sample"    # ölçülmüş ROI yok — hesap YAPILAMADI

#: `extra.plan_source` etiketi olmayan satırların kovası. TV kapısı yolunda
#: etiket `roi_policy`, tarama-içi retlerde `signal`dır (bkz. `summarize`).
PLAN_SOURCE_UNKNOWN = "_yok_"
PLAN_SOURCE_LABELS: Dict[str, str] = {
    "signal": "sinyal planı (gerçek yapısal stop)",
    "roi_policy": "ROI politikası yaklaşıklığı (TV kapısı yolu)",
    PLAN_SOURCE_UNKNOWN: "plan kaynağı etiketsiz",
}


def _blank(reason: str) -> Dict[str, Any]:
    """Boş kova — anahtar SIRASI rapor sırasıdır."""
    return {
        "reason": reason,
        "label": _label(reason),
        "n": 0,
        "measured": 0,
        "collapsed": 0,
        # D27 incelemesi (D5): `collapsed` toplamı sonuç bazında AYRIŞMADAN
        # raporlanıyordu, yani ağırlıklı görünüm rapordan yeniden
        # kurulamıyordu. Bu iki alan onu mümkün kılar.
        "collapsed_tp1": 0,
        "collapsed_stop": 0,
        OUTCOME_TP1: 0,
        OUTCOME_STOP: 0,
        OUTCOME_OPEN: 0,
        OUTCOME_NO_DATA: 0,
        "_rois": [],
    }


def _blank_plan_source(source: str) -> Dict[str, Any]:
    acc = _blank(REASON_TOTAL)
    acc.pop("reason")
    acc["plan_source"] = source
    acc["label"] = PLAN_SOURCE_LABELS.get(source, "")
    return acc


def _accumulate(
    acc: Dict[str, Any],
    *,
    outcome: str,
    measured: bool,
    dup: int,
    roi: Optional[float],
) -> None:
    acc["n"] += 1
    acc["collapsed"] += dup
    acc[outcome] += 1
    if outcome == OUTCOME_TP1:
        acc["collapsed_tp1"] += dup
    elif outcome == OUTCOME_STOP:
        acc["collapsed_stop"] += dup
    if measured:
        acc["measured"] += 1
    if measured and roi is not None:
        acc["_rois"].append(roi)


def _finalize(acc: Dict[str, Any]) -> Dict[str, Any]:
    """Kovayı rapor satırına çevir: ortalama, toplam, PF ve %95 GA.

    ROI istatistikleri YALNIZ `measured` ve `pnl_roi_pct` DOLU satırlardan
    hesaplanır; ölçülemeyen satırlar sayıma girer ama ortalamayı bozmaz. Kaç
    satırın hesaba girdiği `roi_n` alanında AYRICA raporlanır: `measured=True`
    olup `pnl_roi_pct` `None` kalan satır mümkündür (ör. kaldıraç okunamadı)
    ve okuyan `avg`/`PF`/`GA`'nın hangi örneklemden geldiğini bilmelidir
    (D27 incelemesi O4).
    """
    rois: List[float] = acc.pop("_rois")
    count = len(rois)
    acc["roi_n"] = count

    if count:
        mean = sum(rois) / count
        acc["avg_roi_pct"] = round(mean, 3)
        acc["sum_roi_pct"] = round(sum(rois), 3)
    else:
        mean = 0.0
        acc["avg_roi_pct"] = None
        acc["sum_roi_pct"] = None

    positive = sum(value for value in rois if value > 0)
    negative = -sum(value for value in rois if value < 0)
    if negative > 0:
        acc["profit_factor"] = round(positive / negative, 3)
        acc["profit_factor_note"] = None
    elif count:
        # Payda 0 iken PF matematiksel olarak sonsuzdur; JSON'da sonsuz yoktur
        # ve "∞" bir rapor satırında yanlış bir kesinlik hissi verir → None +
        # AÇIK bir not (hepsi kayıp iken PF `0.0`'dır; ikisi karışmamalı).
        acc["profit_factor"] = None
        acc["profit_factor_note"] = PF_NOTE_NO_LOSS
    else:
        acc["profit_factor"] = None
        acc["profit_factor_note"] = PF_NOTE_NO_SAMPLE

    variance = (
        sum((value - mean) ** 2 for value in rois) / (count - 1)
        if count >= 2 else 0.0
    )
    if count >= 2 and variance > 0:
        half = Z95 * math.sqrt(variance) / math.sqrt(count)
        acc["ci95_roi_pct"] = [round(mean - half, 3), round(mean + half, 3)]
    else:
        # Tek gözlemin güven aralığı YOKTUR (ddof=1 → 0'a bölme). SIFIR
        # varyansta da yoktur: `[0.0, 0.0]` genişliksiz bir aralıktır ve
        # "kesin biliyoruz" gibi okunur — oysa elimizde tek bir tekrar eden
        # değer vardır (D27 incelemesi D4).
        acc["ci95_roi_pct"] = None

    return acc


def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Çözülmüş satırların ret gerekçesi × sonuç tablosu — SAF.

    `n` SATIR sayısıdır; `dup_count` ile AĞIRLIKLANMAZ. Tekrarlayan niyetler
    ortalamayı domine etmesin diye ağırlık uygulanmaz; kaç ham niyetin
    toplandığı ayrıca `collapsed` alanında raporlanır (iki sayı birlikte
    okunmalıdır).

    Sözlük OLMAYAN ya da `measured=False` satırlar `no_data`/`unmeasured`
    sayılır; ortalama, PF ve güven aralığı hesabına GİRMEZLER.

    Gerekçe kovalama `intent._bucket_reason` ile AYNI kuraldır: gerekçesiz
    satır `_yok_`, tanınmayan gerekçe `_diger_` kovasına düşer (bir yazım
    hatası sınırsız kova büyütmesin).

    **`by_plan_source` (D27 incelemesi Y6) — NEDEN ZORUNLU.** Tarama-içi
    retlerin planı GERÇEK sinyalden gelir (yapısal stop); TV sağlaması
    `/tv-signal`'da reddettiğinde ortada `ScalpSignal` YOKTUR ve stop/TP1 ROI
    POLİTİKASINDAN yaklaşıklanır (`counterfactual_store._fill_plan`).
    Varsayılan configle ölçüldü: ROI politikası stopu ≈%2.5, TP1'i ≈%1.0
    fiyat mesafesine oturur (2.5:1) — yani TV kovası diğer bütün kovalara
    göre SİSTEMATİK olarak "girseydik kazanırdık" tarafına kayar. Etiket
    JSONL'de vardı ama tabloya hiç ulaşmıyordu; bu kırılım olmadan defterin
    var oluş sebebi olan soru ("TV kapısı 150+ sinyali doğru mu reddetti?")
    yanlış cevaplanır. `roi_policy` satırları AYRI okunmalıdır.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    plan_groups: Dict[str, Dict[str, Any]] = {}
    overall = _blank(REASON_TOTAL)
    total = 0
    measured_total = 0

    for row in rows or []:
        total += 1

        if not isinstance(row, dict):
            reason_key = intent._bucket_reason(None)
            plan_key = PLAN_SOURCE_UNKNOWN
            outcome = OUTCOME_NO_DATA
            measured = False
            dup = 1
            roi: Optional[float] = None
        else:
            reason_key = intent._bucket_reason(_token(row.get("reason")))
            extra = row.get("extra")
            plan_key = (
                _token(extra.get("plan_source")) if isinstance(extra, dict) else None
            ) or PLAN_SOURCE_UNKNOWN
            sim = row.get("sim")
            outcome = _token(sim.get("outcome")) if isinstance(sim, dict) else None
            if outcome not in KNOWN_OUTCOMES:
                outcome = OUTCOME_NO_DATA
            measured = bool(row.get("measured")) and outcome != OUTCOME_NO_DATA
            if not measured:
                outcome = OUTCOME_NO_DATA
            dup = _dup_count(row.get("dup_count"))
            roi = _f(row.get("pnl_roi_pct"))

        if measured:
            measured_total += 1

        bucket = groups.setdefault(reason_key, _blank(reason_key))
        _accumulate(bucket, outcome=outcome, measured=measured, dup=dup, roi=roi)
        plan_bucket = plan_groups.setdefault(plan_key, _blank_plan_source(plan_key))
        _accumulate(plan_bucket, outcome=outcome, measured=measured, dup=dup, roi=roi)
        _accumulate(overall, outcome=outcome, measured=measured, dup=dup, roi=roi)

    table = [_finalize(bucket) for bucket in groups.values()]
    # Çoktan aza; eşitlikte ada göre (kararlı sıra = kararlı rapor).
    table.sort(key=lambda item: (-item["n"], item["reason"]))

    plan_table = [_finalize(bucket) for bucket in plan_groups.values()]
    plan_table.sort(key=lambda item: (-item["n"], item["plan_source"]))

    return {
        "total": total,
        "measured": measured_total,
        "unmeasured": total - measured_total,
        "by_reason": table,
        "by_plan_source": plan_table,
        "overall": _finalize(overall),
    }
