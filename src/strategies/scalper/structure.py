"""
Piyasa yapısı (market structure) — BOS / CHoCH durum makinesi.

Amaç (kullanıcı sorunu, 2026-08-23): rejim kapısı (D5, 15m EMA50/200) dönüşleri
SAATLER geç görüyor; dönüş günlerinde düşen-bıçak LONG / rahatlama-rallisi SHORT
kayıpları buradan geliyor. Bu modül aynı soruyu ORTALAMA yerine YAPI üzerinden
sorar: "son swing kırıldı mı, kırılım trendin devamı mı (BOS) yoksa tersine ilk
kırılım mı (CHoCH)?" CHoCH bir dönüşü, EMA kesişmesinden çok daha erken ve
mekanik olarak işaretler.

Bu dosya KASITLI olarak saftır: yalnız stdlib + `types.Candle`. IO yok, saat yok,
rastgelelik yok, global durum yok. Aynı mum listesi → aynı sonuç (deterministik).
Canlı motor (`engine._evaluate_symbol`, `exits.ExitManager`) ve backtest harness'ı
(`backtest.simulate_symbol`, `backtest.manage_position`) AYNI fonksiyonları AYNI
girdiyle çağırır — parite (CLAUDE.md kural 2 / DECISIONS P1) bu modül üzerinden
kurulur.

Kavram (BOS = Break of Structure, CHoCH = Change of Character) kamuya mal olmuş
piyasa mikro-yapısı terminolojisidir; kod tamamen sıfırdan, `indicators.py` ile
aynı üslupta yazılmıştır — hiçbir üçüncü taraf ürünün (LuxAlgo dahil) kodu
kopyalanmamıştır. LuxAlgo Price Action Concepts'in YAYINLANMIŞ tanımına şu üç
noktada bilinçli olarak yakın durulmuştur:

1. **Seviye = son ONAYLANMIŞ pivot.** Yeni bir pivot high onaylandığında "üst
   seviye" o pivotun high'ı olur ve "henüz kırılmadı" işaretlenir; pivot low
   aynası. Bir seviye YALNIZ BİR KEZ kırılım üretir (aynı seviyede tekrar tekrar
   olay üretilmez).
2. **CHoCH = mevcut yapı yönüne KARŞI ilk kırılım** (yapı yönü o an tersine
   döner). **BOS = mevcut yön ile AYNI yöndeki kırılım** (devam).
3. **Yön henüz belirsizken (NONE) ilk kırılım BOS sayılır** — "değişecek bir
   karakter" yoktur. (LuxAlgo'nun `os == 0` başlangıç durumuyla aynı davranış.)

**Look-ahead YOK — ve gecikme AÇIKÇA modellenir.** Bir pivot ancak sağındaki
`pivot_right` mum kapandıktan sonra onaylanabilir: indeks `p`'deki pivot, bar
`p + pivot_right` işlenirken devreye girer, daha önce DEĞİL. Yani yapı sinyali
tanım gereği `pivot_right` mum geciktirilmiştir; `event_bar_index` kırılımın
gerçekleştiği mumdur, `event_pivot_index` kırılan seviyenin pivot mumudur —
ikisinin farkı + `pivot_right` toplam gecikmeyi verir (bkz. docs/EXPERIMENTS.md
E9 gecikme analizi).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from src.strategies.scalper.types import Candle, Direction, StrategyContext

# Olay etiketleri (JSON'a doğrudan yazılabilsin diye düz string).
BOS = "BOS"
CHOCH = "CHOCH"

# Çıkış tetikleyici modları (SCALPER_STRUCTURE_EXIT).
EXIT_OFF = "off"
EXIT_BE = "be"
EXIT_CLOSE = "close"
_EXIT_MODES = (EXIT_OFF, EXIT_BE, EXIT_CLOSE)

# SCALPER_STRUCTURE_TF: rol adı ya da doğrudan zaman dilimi metni.
# Eşleşme sırası (aynı zaman dilimi iki role atanmışsa) bilinçli olarak sabittir.
_ROLE_TO_CFG_FIELD: Tuple[Tuple[str, str], ...] = (
    ("context", "scalper_tf_context"),
    ("regime", "scalper_tf_regime"),
    ("entry", "scalper_tf_entry"),
)
# Rol → StrategyContext alanı. Alan adları TARİHSEL (5m/15m/4h) ama ROL belirtir:
# candles_5m = giriş, candles_15m = bağlam, candles_4h = rejim (bkz. types.py).
_ROLE_TO_CTX_FIELD: Dict[str, str] = {
    "entry": "candles_5m",
    "context": "candles_15m",
    "regime": "candles_4h",
}

_DEFAULT_ROLE = "context"
_DEFAULT_PIVOT = 5

# Rol → mum penceresi. Bu sayılar canlı motorun her tarama turunda çektiği
# pencerelerle (engine._evaluate_symbol: 150/100/250) VE harness'ın dilim
# boylarıyla (backtest._CTX_5M_WINDOW/_CTX_15M_WINDOW/_CTX_4H_WINDOW) BİREBİR
# aynı olmak ZORUNDADIR: yapı durum makinesi geçmişe bağımlıdır, farklı pencere
# farklı yön üretebilir (parite testi: tests/test_structure.py).
# Ayrıca canlı çıkış yolu (engine._apply_structure_exits) bu limitle mum
# istediği için KlineFetcher'ın (sembol, aralık, limit) önbelleğine düşer —
# yapı çıkışı ek REST ağırlığı getirmez.
_ROLE_WINDOW: Dict[str, int] = {"entry": 150, "context": 100, "regime": 250}


class StructureDirection(str, Enum):
    """Yapının o anki yönü. NONE = henüz hiç kırılım görülmedi."""

    BULL = "BULL"
    BEAR = "BEAR"
    NONE = "NONE"


@dataclass(frozen=True)
class StructureEvent:
    """Tek bir yapı kırılımı olayı (BOS ya da CHoCH)."""

    event: str                  # BOS | CHOCH
    direction: StructureDirection  # olaydan SONRAKİ yapı yönü
    bar_index: int              # kırılımın gerçekleştiği mumun indeksi
    close_time: int             # o mumun close_time'ı (ms)
    price: float                # KIRILAN seviye (pivotun high/low'u)
    pivot_index: int            # kırılan seviyenin pivot mumu indeksi


@dataclass(frozen=True)
class StructureState:
    """Verilen mum penceresinin SONUNDAKİ yapı durumu.

    ``event_*`` alanları SON olaya aittir (yoksa None). ``swing_high/low``
    o an geçerli (henüz kırılmamışsa kırılmayı bekleyen) seviyelerdir;
    kırılmış bir seviye burada görünmeye devam eder — "son onaylanmış pivot"
    anlamındadır, "aktif hedef" değil (``swing_high_crossed`` ayırır).
    """

    direction: StructureDirection = StructureDirection.NONE
    last_event: Optional[str] = None
    event_bar_index: Optional[int] = None
    event_price: Optional[float] = None
    event_close_time: Optional[int] = None
    event_pivot_index: Optional[int] = None
    swing_high: Optional[float] = None
    swing_low: Optional[float] = None
    swing_high_crossed: bool = False
    swing_low_crossed: bool = False
    bars: int = 0
    age_bars: Optional[int] = None   # son mum ile olay mumu arasındaki mum sayısı


@dataclass(frozen=True)
class StructureExitInput:
    """``structure_exit_action`` için pozisyonun anlık durumu (saf girdi)."""

    direction: Direction
    entry_close_time: int      # giriş mumunun close_time'ı (ms) — olay tazeliği bundan
    current_price: float
    current_stop: float
    breakeven_price: float


# --------------------------------------------------------------------------
# Saf çekirdek: pivot tespiti + durum makinesi
# --------------------------------------------------------------------------

def _is_pivot_high(candles: List[Candle], idx: int, left: int, right: int) -> bool:
    """`idx` bir fraktal swing-high mı (her iki tarafta KESİN büyük)?

    `indicators.swing_points` ile birebir aynı katılık (strict >) — aynı kod
    tabanında iki farklı swing tanımı olmamalı (bkz. tests: çapraz kontrol).
    """
    h = candles[idx].high
    for j in range(idx - left, idx):
        if not h > candles[j].high:
            return False
    for j in range(idx + 1, idx + 1 + right):
        if not h > candles[j].high:
            return False
    return True


def _is_pivot_low(candles: List[Candle], idx: int, left: int, right: int) -> bool:
    l = candles[idx].low
    for j in range(idx - left, idx):
        if not l < candles[j].low:
            return False
    for j in range(idx + 1, idx + 1 + right):
        if not l < candles[j].low:
            return False
    return True


def scan_structure(
    candles: List[Candle],
    pivot_left: int = _DEFAULT_PIVOT,
    pivot_right: int = _DEFAULT_PIVOT,
    use_close: bool = True,
) -> Tuple[List[StructureEvent], StructureState]:
    """Tek geçişte tüm yapı olaylarını ve son durumu üretir.

    `use_close=True` (varsayılan): kırılım KAPANIŞ ile onaylanır (fitil
    yetmez) — fitil-avı gürültüsünü eler. `False`: high/low fitili yeter.

    Eşitlik sınırı: kırılım KESİN aşmadır (`close > seviye` / `close <
    seviye`); tam eşitlik olay üretmez. Pivot tespiti de kesindir.

    Aynı mumda hem yukarı hem aşağı kırılım olabiliyorsa (yalnız fitil
    modunda ve seviyeler çakışıksa mümkün) sıra SABİTTİR: önce yukarı, sonra
    aşağı — son olay durumu belirler. Deterministiklik için bilinçli seçim.
    """
    left = max(1, int(pivot_left))
    right = max(1, int(pivot_right))

    events: List[StructureEvent] = []
    n = len(candles)
    if n == 0:
        return events, StructureState()

    direction = StructureDirection.NONE
    top: Optional[float] = None
    top_idx: Optional[int] = None
    top_crossed = False
    bottom: Optional[float] = None
    bottom_idx: Optional[int] = None
    bottom_crossed = False

    for j in range(n):
        # 1) Pivot onayı: `pivot_right` mum GECİKMELİ — bar j işlenirken en
        #    fazla (j - right) indeksli pivot bilinebilir. Look-ahead yok.
        p = j - right
        if p >= left:
            if _is_pivot_high(candles, p, left, right):
                top = candles[p].high
                top_idx = p
                top_crossed = False
            if _is_pivot_low(candles, p, left, right):
                bottom = candles[p].low
                bottom_idx = p
                bottom_crossed = False

        c = candles[j]
        up_probe = c.close if use_close else c.high
        down_probe = c.close if use_close else c.low

        # 2) Kırılım: her seviye YALNIZ BİR KEZ olay üretir.
        if top is not None and not top_crossed and up_probe > top:
            top_crossed = True
            event = CHOCH if direction == StructureDirection.BEAR else BOS
            direction = StructureDirection.BULL
            events.append(StructureEvent(
                event=event, direction=direction, bar_index=j,
                close_time=c.close_time, price=top,
                pivot_index=top_idx if top_idx is not None else j,
            ))

        if bottom is not None and not bottom_crossed and down_probe < bottom:
            bottom_crossed = True
            event = CHOCH if direction == StructureDirection.BULL else BOS
            direction = StructureDirection.BEAR
            events.append(StructureEvent(
                event=event, direction=direction, bar_index=j,
                close_time=c.close_time, price=bottom,
                pivot_index=bottom_idx if bottom_idx is not None else j,
            ))

    last = events[-1] if events else None
    return events, StructureState(
        direction=direction,
        last_event=last.event if last else None,
        event_bar_index=last.bar_index if last else None,
        event_price=last.price if last else None,
        event_close_time=last.close_time if last else None,
        event_pivot_index=last.pivot_index if last else None,
        swing_high=top,
        swing_low=bottom,
        swing_high_crossed=top_crossed,
        swing_low_crossed=bottom_crossed,
        bars=n,
        age_bars=(n - 1 - last.bar_index) if last else None,
    )


def detect_structure(
    candles: List[Candle],
    pivot_left: int = _DEFAULT_PIVOT,
    pivot_right: int = _DEFAULT_PIVOT,
    use_close: bool = True,
) -> StructureState:
    """`scan_structure`'ın yalnız son durumu döndüren sarmalayıcısı."""
    _, state = scan_structure(candles, pivot_left, pivot_right, use_close)
    return state


# --------------------------------------------------------------------------
# Konfigürasyon çözümü (canlı motor ve harness AYNI fonksiyonları kullanır)
# --------------------------------------------------------------------------

def structure_gate_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "scalper_structure_gate", False))


def structure_exit_mode(cfg: Any) -> str:
    mode = str(getattr(cfg, "scalper_structure_exit", EXIT_OFF) or EXIT_OFF).strip().lower()
    return mode if mode in _EXIT_MODES else EXIT_OFF


def structure_enabled(cfg: Any) -> bool:
    """Yapı hesabının HERHANGİ bir karar yolunda kullanılıp kullanılmadığı."""
    return structure_gate_enabled(cfg) or structure_exit_mode(cfg) != EXIT_OFF


def structure_pivot(cfg: Any) -> Tuple[int, int]:
    """(left, right) pivot uzunluğu. Tek ayar iki tarafı da belirler (simetrik)."""
    try:
        n = int(getattr(cfg, "scalper_structure_pivot", _DEFAULT_PIVOT) or _DEFAULT_PIVOT)
    except (TypeError, ValueError):
        n = _DEFAULT_PIVOT
    n = max(1, n)
    return n, n


def structure_use_close(cfg: Any) -> bool:
    return bool(getattr(cfg, "scalper_structure_use_close", True))


def resolve_structure_role(cfg: Any) -> str:
    """SCALPER_STRUCTURE_TF → rol adı ("entry" | "context" | "regime").

    Değer ya doğrudan rol adıdır ya da bir zaman dilimi metnidir ("5m") —
    ikincisi cfg'deki rol zaman dilimleriyle eşleştirilir. Eşleşme yoksa
    ValueError (sessizce yanlış seriyle çalışmaktansa gürültülü hata).
    """
    raw = str(getattr(cfg, "scalper_structure_tf", _DEFAULT_ROLE) or _DEFAULT_ROLE).strip().lower()
    if not raw:
        return _DEFAULT_ROLE
    for role, _field in _ROLE_TO_CFG_FIELD:
        if raw == role:
            return role
    for role, field in _ROLE_TO_CFG_FIELD:
        tf = str(getattr(cfg, field, "") or "").strip().lower()
        if tf and raw == tf:
            return role
    known = ", ".join(
        f"{role}={str(getattr(cfg, field, '') or '?')}" for role, field in _ROLE_TO_CFG_FIELD
    )
    raise ValueError(
        f"SCALPER_STRUCTURE_TF={raw!r} çözülemedi — rol adı (entry/context/regime) "
        f"ya da tanımlı bir zaman dilimi olmalı ({known})."
    )


def structure_timeframe(cfg: Any) -> str:
    """Yapı serisinin GERÇEK zaman dilimi metni ("5m" gibi) — canlı çıkış
    yolunun mum çekerken kullanacağı aralık."""
    role = resolve_structure_role(cfg)
    field = dict(_ROLE_TO_CFG_FIELD)[role]
    return str(getattr(cfg, field, "") or "")


def structure_window_bars(cfg: Any) -> int:
    """Yapı serisinin mum sayısı (rol penceresi) — bkz. `_ROLE_WINDOW`."""
    return _ROLE_WINDOW[resolve_structure_role(cfg)]


def structure_series(ctx: StrategyContext, cfg: Any) -> List[Candle]:
    """Yapı hesabının okuyacağı mum serisi — ctx'te ZATEN VAR olan seriler.

    YENİ REST çağrısı YOKTUR: motor bu üç seriyi her tarama turunda zaten
    çekiyor (engine._evaluate_symbol), harness ise build_context ile aynı
    pencereleri diliyor.
    """
    role = resolve_structure_role(cfg)
    return list(getattr(ctx, _ROLE_TO_CTX_FIELD[role], []) or [])


def structure_state_for(ctx: StrategyContext, cfg: Any) -> StructureState:
    """ctx + cfg → StructureState. Canlı motor ve harness'ın ORTAK giriş noktası."""
    left, right = structure_pivot(cfg)
    return detect_structure(
        structure_series(ctx, cfg),
        pivot_left=left,
        pivot_right=right,
        use_close=structure_use_close(cfg),
    )


# --------------------------------------------------------------------------
# Karar fonksiyonları (saf) — giriş kapısı ve çıkış tetikleyicisi
# --------------------------------------------------------------------------

def structure_gate_blocks(
    state: Optional[StructureState], direction: Direction, cfg: Any
) -> bool:
    """Giriş kapısı: yapı BEAR iken LONG, BULL iken SHORT açılmaz.

    Kapı kapalıysa (`SCALPER_STRUCTURE_GATE=false`, varsayılan) ya da yapı yönü
    henüz NONE ise (yeterli pivot yok) ASLA bloklamaz — yeni bir "sessizce
    işlem açmama" kaynağı olmamalı.
    """
    if not structure_gate_enabled(cfg):
        return False
    if not bool(getattr(cfg, "scalper_structure_block_counter", True)):
        return False
    if state is None or state.direction == StructureDirection.NONE:
        return False
    yon = getattr(direction, "value", str(direction))
    if state.direction == StructureDirection.BEAR and yon == "LONG":
        return True
    if state.direction == StructureDirection.BULL and yon == "SHORT":
        return True
    return False


def structure_exit_action(
    state: Optional[StructureState], inp: StructureExitInput, cfg: Any
) -> str:
    """Açık pozisyon için yapı-tabanlı çıkış kararı: "none" | "be" | "close".

    Tetik: pozisyonun TERSİNE bir **CHoCH** (BOS değil — BOS trendin devamıdır,
    ters yönde bir BOS zaten pozisyona ters bir trendin sürmesi demektir ama
    "karakter değişimi" anı değildir; dönüş tespiti CHoCH'tur).

    Tazelik şartı: olay GİRİŞTEN SONRA kapanmış bir mumda olmalı
    (`event_close_time > entry_close_time`). Aksi halde girişten önceki bir
    CHoCH yeni açılan her pozisyonu anında kapatırdı.

    `be` modunda ek KORUMA: BE seviyesi stopu iyileştirmiyorsa ya da piyasa
    fiyatının YANLIŞ tarafındaysa "none" döner. Borsada, LONG için piyasanın
    üstüne konan STOP_MARKET anında tetiklenir (-2021 "would immediately
    trigger") — harness'ta ise bu, bir sonraki mumda sahte bir "SL kârı"
    üretirdi. İki tarafta da aynı kural = parite.
    """
    mode = structure_exit_mode(cfg)
    if mode == EXIT_OFF:
        return "none"
    if state is None or state.last_event != CHOCH:
        return "none"
    if state.event_close_time is None or state.event_close_time <= inp.entry_close_time:
        return "none"

    yon = getattr(inp.direction, "value", str(inp.direction))
    opposed = (
        (yon == "LONG" and state.direction == StructureDirection.BEAR)
        or (yon == "SHORT" and state.direction == StructureDirection.BULL)
    )
    if not opposed:
        return "none"

    if mode == EXIT_CLOSE:
        return "close"

    be = float(inp.breakeven_price or 0.0)
    if be <= 0:
        return "none"
    if yon == "LONG":
        if be < inp.current_price and be > inp.current_stop:
            return "be"
        return "none"
    if be > inp.current_price and (inp.current_stop <= 0 or be < inp.current_stop):
        return "be"
    return "none"


def structure_snapshot(state: Optional[StructureState]) -> Dict[str, Any]:
    """/scalper/status için JSON-güvenli özet."""
    if state is None:
        return {}
    return {
        "direction": state.direction.value,
        "last_event": state.last_event,
        "age_bars": state.age_bars,
        "event_price": state.event_price,
        "swing_high": state.swing_high,
        "swing_low": state.swing_low,
        "bars": state.bars,
    }
