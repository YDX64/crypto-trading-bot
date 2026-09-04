"""
Genel deterministik giriş kapıları — SAF fonksiyonlar, IO/duvar saati YOK.

Amaç (2026-09-03): bir post-hoc tarama, canlı defter + harness üzerinde
hangi BASİT kuralların kayıp yoğunlaşmasını kestiğini ölçüyor. Aday kurallar
üç ailedir ve hangisi kazanırsa kazansın YALNIZ env değeriyle açılabilmeli,
canlı motor ile harness AYNI kuralı AYNI girdiyle uygulamalıdır
(CLAUDE.md kural 2 / DECISIONS #P1):

  1. **Rejim×yön hücresi yasağı** — `SCALPER_C_BLOCKED_CELLS`
     (ör. "RANGE:SHORT,UP:LONG"). Rejim ∈ {UP, DOWN, RANGE, UNKNOWN},
     yön ∈ {LONG, SHORT}. Mevcut rejim kapısı (`scalper_regime_filter`:
     DOWN+LONG / UP+SHORT) ZATEN bloklar; bu kapı onun ÜSTÜNE ek yasak koyar.
     `scalper_c_allowed_regimes` (setups.py, sinyal doğmadan ÖNCE, sessiz)
     ile de farklıdır: bu kapı sinyal DOĞDUKTAN sonra, diğer kapıların
     yanında çalışır ve niyet defterine (intent) yazar.
  2. **UTC saat penceresi yasağı** — `SCALPER_ENTRY_BLOCK_HOURS_UTC`
     (ör. "0-6,22-24"; başlangıç DAHİL, bitiş HARİÇ; gece yarısını saran
     "22-3" kabul). Zaman kaynağı PARİTE için sabittir: son KAPANMIŞ giriş
     mumunun `close_time`'ı (motor: `ctx.candles_5m[-1].close_time`;
     harness: `close_times_5m[i]`) — DUVAR SAATİ DEĞİL. Not: bir 5m mumu
     04:55–05:00 aralığını kapsar ve `close_time`'ı 04:59:59.999'dur → saat
     **4** sayılır; yani "5-6" yasağı, kapanışı [05:00, 06:00) içinde olan
     mumlardan doğan sinyalleri keser. Post-hoc taramanın da aynı `close_time`
     tabanını kullanması ŞARTTIR.
  3. **ATR% bandı** — `SCALPER_MIN_ATR_PCT` / `SCALPER_MAX_ATR_PCT`
     (0 = o uç kapalı). ATR% = `atr_5m / entry_price * 100`
     (setups.apply_stop_policy ile aynı formül) ve HAM sinyal değerleriyle,
     `apply_stop_policy`'den ÖNCE hesaplanır (dinamik kaldıraç kararından
     etkilenmesin). ATR yoksa/≤0 ise kapı UYGULANMAZ (fail-open).

Sıra sabittir: hücre → saat → ATR (ilk tetiklenen gerekçe döner).

**Kapalıyken (varsayılanlar: "", "", 0, 0) hiçbir kod yolu davranış
değiştirmez** — `evaluate_entry_gates` üç ucuz alan okumasıyla `None` döner,
harness'ın `missed_counter` sözlüğüne anahtar bile eklenmez
(tests/test_golden_backtest.py byte-for-byte aynı kalır).

Hata politikası iki tarafta bilinçli olarak FARKLIDIR (structure.py ile aynı
kalıp): canlı motor fail-OPEN (istisna tarama turunu düşürmez, kapı o
sinyalde uygulanmaz, bir kez WARNING); harness fail-CLOSED (hatalı ayar
sessizce "kapı kapalı" ölçümü üretmektense gürültüyle patlar). Geçersiz bir
env değeri zaten `Settings` doğrulayıcısında (config._validate_entry_gates)
fail-fast'tır — ama yalnız alan DOLUYKEN.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Any, Dict, FrozenSet, Optional, Tuple

# missed_counter / intent / log anahtarları — harness ve motor AYNI dizeleri
# kullanır. intent.REASON_*_GATE ile birebir aynı olmaları test edilir
# (tests/test_entry_gates.py::TestConstants).
REASON_CELL = "cell_gate"
REASON_HOUR = "hour_gate"
REASON_ATR = "atr_gate"

# Hangi env değişkeni(leri) hangi gerekçeyi üretir — log/pano metni için.
REASON_ENV_VARS: Dict[str, str] = {
    REASON_CELL: "SCALPER_C_BLOCKED_CELLS",
    REASON_HOUR: "SCALPER_ENTRY_BLOCK_HOURS_UTC",
    REASON_ATR: "SCALPER_MIN_ATR_PCT/SCALPER_MAX_ATR_PCT",
}

# cfg alan adları (Settings alanları; harness dataclass'larında getattr ile
# okunur — alan yoksa kapı KAPALI sayılır).
CFG_BLOCKED_CELLS = "scalper_c_blocked_cells"
CFG_BLOCK_HOURS = "scalper_entry_block_hours_utc"
CFG_MIN_ATR_PCT = "scalper_min_atr_pct"
CFG_MAX_ATR_PCT = "scalper_max_atr_pct"

VALID_REGIMES: FrozenSet[str] = frozenset({"UP", "DOWN", "RANGE", "UNKNOWN"})
VALID_DIRECTIONS: FrozenSet[str] = frozenset({"LONG", "SHORT"})

HOUR_MS = 3_600_000
HOURS_PER_DAY = 24

_HOUR_RANGE_RE = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$")


# --------------------------------------------------------------------------
# Küçük savunmalı yardımcılar (market_gate.py ile aynı üslup)
# --------------------------------------------------------------------------

def _norm_token(value: Any) -> str:
    """Enum (`.value`) ya da düz dize → kırpılmış BÜYÜK harf belirteç."""
    return str(getattr(value, "value", value)).strip().upper()


def _as_float(value: Any) -> Optional[float]:
    """Sonlu bir float'a çevir; olmuyorsa None (sessiz 0.0 YOK)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _cfg_str(cfg: Any, name: str) -> str:
    raw = getattr(cfg, name, "")
    if raw is None:
        return ""
    return str(raw).strip()


def _cfg_float(cfg: Any, name: str, default: float = 0.0) -> float:
    out = _as_float(getattr(cfg, name, default))
    return default if out is None else out


# --------------------------------------------------------------------------
# 1) Rejim×yön hücresi
# --------------------------------------------------------------------------

@lru_cache(maxsize=64)
def parse_blocked_cells(raw: str) -> FrozenSet[Tuple[str, str]]:
    """"RANGE:SHORT,UP:LONG" → {("RANGE","SHORT"), ("UP","LONG")}.

    Boş/yalnız boşluk → boş küme (kapı kapalı). Boş token'lar (çift virgül,
    sondaki virgül) atlanır. Geçersiz biçim / bilinmeyen rejim / bilinmeyen
    yön → ValueError (config doğrulayıcısı bunu fail-fast'a çevirir).
    Büyük/küçük harf ve boşluk duyarsız.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return frozenset()
    cells = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if token.count(":") != 1:
            raise ValueError(
                f"{REASON_ENV_VARS[REASON_CELL]}: geçersiz hücre {token!r} "
                f"(beklenen biçim REJIM:YON, ör. RANGE:SHORT)"
            )
        regime_raw, direction_raw = token.split(":")
        regime = regime_raw.strip().upper()
        direction = direction_raw.strip().upper()
        if regime not in VALID_REGIMES:
            raise ValueError(
                f"{REASON_ENV_VARS[REASON_CELL]}: bilinmeyen rejim {regime_raw.strip()!r} "
                f"(geçerli: {', '.join(sorted(VALID_REGIMES))})"
            )
        if direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"{REASON_ENV_VARS[REASON_CELL]}: bilinmeyen yön {direction_raw.strip()!r} "
                f"(geçerli: LONG, SHORT)"
            )
        cells.add((regime, direction))
    return frozenset(cells)


def cell_gate_enabled(cfg: Any) -> bool:
    return bool(_cfg_str(cfg, CFG_BLOCKED_CELLS))


def cell_gate_blocks(regime: Any, direction: Any, cfg: Any) -> bool:
    """(rejim, yön) çifti yasak listesindeyse True. Kapalıyken ASLA True değil."""
    raw = _cfg_str(cfg, CFG_BLOCKED_CELLS)
    if not raw:
        return False
    cells = parse_blocked_cells(raw)
    if not cells:
        return False
    return (_norm_token(regime), _norm_token(direction)) in cells


# --------------------------------------------------------------------------
# 2) UTC saat penceresi
# --------------------------------------------------------------------------

def utc_hour_of(timestamp_ms: int) -> int:
    """`timestamp_ms`'in (epoch ms) UTC saati, 0-23.

    Binance mumları UTC'ye hizalıdır; basit tamsayı bölmesi yeterlidir
    (yerel saat/DST YOK) — `market_gate.utc_day_start_ms` ile aynı üslup.
    """
    return (int(timestamp_ms) // HOUR_MS) % HOURS_PER_DAY


@lru_cache(maxsize=64)
def parse_hour_ranges(raw: str) -> Tuple[Tuple[int, int], ...]:
    """"0-6,22-24" → ((0, 6), (22, 24)). Her aralık [başlangıç, bitiş).

    Kurallar: başlangıç 0-23, bitiş 0-24, ikisi eşit OLAMAZ (boş mu tam gün
    mü belirsiz). başlangıç > bitiş ise aralık gece yarısını SARAR
    ("22-3" = 22, 23, 0, 1, 2). Tek saat ("3") ya da başka biçim → ValueError.
    Boş/yalnız boşluk → boş demet (kapı kapalı).
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return ()
    out = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        m = _HOUR_RANGE_RE.match(token)
        if m is None:
            raise ValueError(
                f"{REASON_ENV_VARS[REASON_HOUR]}: geçersiz aralık {token!r} "
                f"(beklenen biçim BASLANGIC-BITIS, ör. 0-6 ya da 22-3)"
            )
        start, end = int(m.group(1)), int(m.group(2))
        if not (0 <= start <= HOURS_PER_DAY - 1):
            raise ValueError(
                f"{REASON_ENV_VARS[REASON_HOUR]}: başlangıç saati 0-23 olmalı ({token!r})"
            )
        if not (0 <= end <= HOURS_PER_DAY):
            raise ValueError(
                f"{REASON_ENV_VARS[REASON_HOUR]}: bitiş saati 0-24 olmalı ({token!r})"
            )
        if start == end:
            raise ValueError(
                f"{REASON_ENV_VARS[REASON_HOUR]}: başlangıç ve bitiş eşit olamaz ({token!r}); "
                f"tam gün için 0-24 yazın"
            )
        out.append((start, end))
    return tuple(out)


def hour_in_ranges(hour: int, ranges: Tuple[Tuple[int, int], ...]) -> bool:
    """`hour` (0-23) verilen [başlangıç, bitiş) aralıklarından birinde mi?"""
    for start, end in ranges:
        if start < end:
            if start <= hour < end:
                return True
        else:  # gece yarısını saran aralık
            if hour >= start or hour < end:
                return True
    return False


def hour_gate_enabled(cfg: Any) -> bool:
    return bool(_cfg_str(cfg, CFG_BLOCK_HOURS))


def hour_gate_blocks(close_time_ms: Any, cfg: Any) -> bool:
    """Karar mumunun UTC saati yasak penceredeyse True. Kapalıyken ASLA True
    değil. `close_time_ms` çözülemezse (None/bozuk) kapı UYGULANMAZ
    (fail-open) — zaman bilgisinin yokluğu bir risk olayı değildir."""
    raw = _cfg_str(cfg, CFG_BLOCK_HOURS)
    if not raw:
        return False
    ranges = parse_hour_ranges(raw)
    if not ranges:
        return False
    if close_time_ms is None or isinstance(close_time_ms, bool):
        return False
    try:
        ms = int(close_time_ms)
    except (TypeError, ValueError):
        return False
    return hour_in_ranges(utc_hour_of(ms), ranges)


# --------------------------------------------------------------------------
# 3) ATR% bandı
# --------------------------------------------------------------------------

def atr_pct_of(atr_5m: Any, entry_price: Any) -> Optional[float]:
    """ATR% = atr / fiyat × 100 (setups.apply_stop_policy ile aynı formül).

    Hesaplanamıyorsa (None/≤0/sonlu değil) None — 0.0 ile KARIŞTIRILMAMALI:
    None "ölçülemedi" demektir ve kapı bu durumda uygulanmaz.
    """
    atr = _as_float(atr_5m)
    price = _as_float(entry_price)
    if atr is None or price is None or atr <= 0.0 or price <= 0.0:
        return None
    return atr / price * 100.0


def atr_gate_enabled(cfg: Any) -> bool:
    return (
        _cfg_float(cfg, CFG_MIN_ATR_PCT) > 0.0
        or _cfg_float(cfg, CFG_MAX_ATR_PCT) > 0.0
    )


def atr_gate_blocks(atr_5m: Any, entry_price: Any, cfg: Any) -> bool:
    """min>0 ve ATR% < min → True; max>0 ve ATR% > max → True.

    Eşitlik SERBESTTİR (tam min ya da tam max geçer). ATR ölçülemiyorsa
    fail-open (False) — çağıran katman loglar.
    """
    min_pct = _cfg_float(cfg, CFG_MIN_ATR_PCT)
    max_pct = _cfg_float(cfg, CFG_MAX_ATR_PCT)
    if min_pct <= 0.0 and max_pct <= 0.0:
        return False
    pct = atr_pct_of(atr_5m, entry_price)
    if pct is None:
        return False
    if min_pct > 0.0 and pct < min_pct:
        return True
    if max_pct > 0.0 and pct > max_pct:
        return True
    return False


# --------------------------------------------------------------------------
# Birleşik değerlendirme + doğrulama + teşhis
# --------------------------------------------------------------------------

def entry_gates_enabled(cfg: Any) -> bool:
    """Üç kapıdan HERHANGİ biri açık mı? (Yalnız teşhis/rapor için;
    `evaluate_entry_gates` kendi kısa devresini yapar.)"""
    return cell_gate_enabled(cfg) or hour_gate_enabled(cfg) or atr_gate_enabled(cfg)


def evaluate_entry_gates(
    regime: Any,
    direction: Any,
    close_time_ms: Any,
    atr_5m: Any,
    entry_price: Any,
    cfg: Any,
) -> Optional[str]:
    """Üç kapıyı SABİT sırayla (hücre → saat → ATR) uygula; engelleyen ilk
    kapının gerekçesi (`REASON_CELL` / `REASON_HOUR` / `REASON_ATR`) ya da
    None (serbest).

    Argümanlar — motor ve harness BİREBİR aynı kaynaklardan geçer
    (parite testi: tests/test_entry_gates.py::TestEngineHarnessParity):
      * `regime`        — `ctx.regime` (Regime enum'u ya da "UP"/… dizesi).
      * `direction`     — sinyal yönü (`Direction` ya da "LONG"/"SHORT").
      * `close_time_ms` — son KAPANMIŞ giriş mumunun `close_time`'ı (epoch ms
                          UTC). Motor: `ctx.candles_5m[-1].close_time`;
                          harness: `close_times_5m[i]`.
      * `atr_5m`, `entry_price` — HAM sinyalin değerleri
                          (`apply_stop_policy` ÖNCESİ).
      * `cfg`           — `SCALPER_C_BLOCKED_CELLS` / `_ENTRY_BLOCK_HOURS_UTC`
                          / `_MIN_ATR_PCT` / `_MAX_ATR_PCT` alanlarını taşıyan
                          ayar nesnesi (alan yoksa o kapı KAPALI).

    Kapalıyken (varsayılan) hiçbir hesap yapılmaz ve None döner.
    """
    if cell_gate_blocks(regime, direction, cfg):
        return REASON_CELL
    if hour_gate_blocks(close_time_ms, cfg):
        return REASON_HOUR
    if atr_gate_blocks(atr_5m, entry_price, cfg):
        return REASON_ATR
    return None


def validate_entry_gate_settings(cfg: Any) -> None:
    """Ayarları doğrula; sorun varsa ValueError (Settings doğrulayıcısı
    çağırır). Yalnız DOLU alanlar denetlenir — kapalı bir özelliğin ayarı
    botu başlatmamazlık etmemeli."""
    cells_raw = _cfg_str(cfg, CFG_BLOCKED_CELLS)
    if cells_raw:
        parse_blocked_cells(cells_raw)  # geçersizse ValueError
    hours_raw = _cfg_str(cfg, CFG_BLOCK_HOURS)
    if hours_raw:
        parse_hour_ranges(hours_raw)  # geçersizse ValueError
    min_pct = _as_float(getattr(cfg, CFG_MIN_ATR_PCT, 0.0))
    max_pct = _as_float(getattr(cfg, CFG_MAX_ATR_PCT, 0.0))
    if min_pct is None or max_pct is None:
        raise ValueError(
            f"{REASON_ENV_VARS[REASON_ATR]}: sayısal olmalı "
            f"(verilen: {getattr(cfg, CFG_MIN_ATR_PCT, None)!r} / "
            f"{getattr(cfg, CFG_MAX_ATR_PCT, None)!r})"
        )
    if min_pct < 0.0 or max_pct < 0.0:
        raise ValueError(
            f"{REASON_ENV_VARS[REASON_ATR]}: negatif olamaz (0 = kapalı; "
            f"verilen: {min_pct} / {max_pct})"
        )
    if min_pct > 0.0 and max_pct > 0.0 and not (min_pct < max_pct):
        raise ValueError(
            f"SCALPER_MIN_ATR_PCT ({min_pct}) < SCALPER_MAX_ATR_PCT ({max_pct}) olmalı "
            f"(ikisi de açıkken)"
        )


def entry_gate_detail(
    reason: Optional[str],
    regime: Any,
    direction: Any,
    close_time_ms: Any,
    atr_5m: Any,
    entry_price: Any,
    cfg: Any,
) -> Dict[str, Any]:
    """Log / niyet kaydı (`extra`) için o karara ait büyüklükler — SAF.

    Hesaplanamayan büyüklük None'dır (0.0 DEĞİL). Kapı kararına GİRMEZ.
    """
    detail: Dict[str, Any] = {"gate": reason}
    if reason == REASON_CELL:
        detail["regime"] = _norm_token(regime)
        detail["direction"] = _norm_token(direction)
    elif reason == REASON_HOUR:
        try:
            detail["hour_utc"] = utc_hour_of(int(close_time_ms))
        except (TypeError, ValueError):
            detail["hour_utc"] = None
        detail["block_hours"] = _cfg_str(cfg, CFG_BLOCK_HOURS)
    elif reason == REASON_ATR:
        pct = atr_pct_of(atr_5m, entry_price)
        detail["atr_pct"] = None if pct is None else round(pct, 4)
        detail["min_atr_pct"] = _cfg_float(cfg, CFG_MIN_ATR_PCT)
        detail["max_atr_pct"] = _cfg_float(cfg, CFG_MAX_ATR_PCT)
    return detail


def format_entry_gate_detail(detail: Dict[str, Any]) -> str:
    """`entry_gate_detail` sözlüğünün tek satırlık log metni — SAF."""
    gate = detail.get("gate") if isinstance(detail, dict) else None
    if gate == REASON_CELL:
        return f"hücre {detail.get('regime')}:{detail.get('direction')}"
    if gate == REASON_HOUR:
        return f"UTC saat {detail.get('hour_utc')} ∈ {detail.get('block_hours')!r}"
    if gate == REASON_ATR:
        pct = detail.get("atr_pct")
        pct_text = "?" if pct is None else f"{pct:.3f}"
        return (
            f"ATR% {pct_text} bant dışı "
            f"[min {detail.get('min_atr_pct')}, max {detail.get('max_atr_pct')}]"
        )
    return str(gate)
