"""Seviye motoru — SAF (IO yok, saat yok, rastgelelik yok).

ÖNCELİK SIRASI (kullanıcı kararı: "AlgoPro ne diyorsa"):
  (a) **BİRİNCİL** — mesajdaki mutlak seviyeler (``SL:``, ``TP1:``, ``TP2:``,
      ``TP3:``). 2026-08-23'te TV Desktop sonda alarmıyla DOĞRULANDI: AlgoPro
      V1.6 "any alert() call" modunda mesajı kendisi üretir ve seviyeleri
      İÇERİR (ör. ``🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 |
      TQI: .45 | Score: 8 | SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 |
      TP3: 77063.54``). Bu seviyeler AYNEN kullanılır.
  (b) **YEDEK** — mesajda SL yoksa (alert biçimi değişmiş ya da alarm başka
      modda kurulmuşsa) hesaplanan kural devreye girer ve bu durum
      ``warnings`` ile yüzeye çıkarılır (motor WARNING loglar):
          SL  = giriş ∓ FOLLOWER_SL_ATR_MULT × ATR(FOLLOWER_ATR_LEN)   [1m]
          TPk = giriş ± FOLLOWER_TP_RR_k × SL_mesafesi
      Varsayılanlar 3.0 / 14 ve 0.5 / 1.0 / 1.5 — TV Desktop'tan ölçülen BTC
      1m SELL örneğiyle uyumludur (ENTRY 77195.38, SL 77255.41 → mesafe 60.03;
      TP1/TP2/TP3 = 0.5/1.0/1.5 × mesafe; panel "Live RR .5/1.0/1.5").
      LTC'de RR 1/2/3 görüldüğü için çarpanlar env ile değiştirilebilir.
      Script girdilerindeki "Strict ATR, 3" bu k=3 hipotezinin kaynağıdır —
      DOĞRULANMADI; kalibrasyon defteri (state/follower_levels.jsonl) mesaj
      seviyeleriyle hesaplananların sapmasını ölçmek için vardır.

Fail-closed: seviye üretilemiyorsa (ATR yok/sıfır, stop bandı dışında, stop
girişin yanlış tarafında) ``FollowerRejected`` yükseltilir — giriş YAPILMAZ.
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Tuple

from src.strategies.follower.types import (
    LEVEL_SOURCE_ATR,
    LEVEL_SOURCE_MESSAGE,
    LEVEL_SOURCE_MIXED,
    FollowerLevels,
    FollowerRejected,
    MessageLevels,
)
from src.strategies.scalper.types import Direction


def _cfg_float(cfg: Any, name: str, default: float) -> float:
    try:
        value = float(getattr(cfg, name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _cfg_int(cfg: Any, name: str, default: int) -> int:
    try:
        return int(getattr(cfg, name, default))
    except (TypeError, ValueError):
        return default


def _is_finite_positive(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def _stop_on_correct_side(direction: Direction, entry: float, stop: float) -> bool:
    """LONG'da stop girişin ALTINDA, SHORT'ta ÜSTÜNDE olmalıdır."""
    return stop < entry if direction == Direction.LONG else stop > entry


def _tp_on_correct_side(direction: Direction, entry: float, tp: float) -> bool:
    return tp > entry if direction == Direction.LONG else tp < entry


# Public sarmalayıcılar: aynı taraf kuralını executor/engine de kullanır —
# ikinci bir kopya, iki farklı "hangi taraf?" tanımı demektir.
def stop_on_correct_side(
    direction: Direction, reference: float, stop: float
) -> bool:
    """Stop, ``reference`` fiyatının KORUYUCU tarafında mı?

    LONG'da stop ALTINDA, SHORT'ta ÜSTÜNDE olmalıdır. ``reference`` sinyal
    fiyatı DEĞİL, kararın verildiği andaki fiyattır (canlı fiyat ya da gerçek
    dolum): AlgoPro'nun stopu, dolum o seviyeyi GEÇTİKTEN sonra artık bir
    stop değil, "zaten vurulmuş" bir seviyedir.
    """
    if not _is_finite_positive(reference) or not _is_finite_positive(stop):
        return False
    return _stop_on_correct_side(direction, float(reference), float(stop))


def tp_on_correct_side(direction: Direction, reference: float, tp: float) -> bool:
    """TP, ``reference`` fiyatının KÂR tarafında mı? (LONG: üstünde)"""
    if not _is_finite_positive(reference) or not _is_finite_positive(tp):
        return False
    return _tp_on_correct_side(direction, float(reference), float(tp))


def signal_drift_limit_pct(sl_pct: float, cfg: Any) -> float:
    """Alarm fiyatı ile canlı fiyat arasında izin verilen azami sapma (%).

    ``FOLLOWER_MAX_SIGNAL_DRIFT_PCT`` > 0 ise o; aksi halde TÜRETİLMİŞ
    varsayılan: stop mesafesinin YARISI. Gerekçe: AlgoPro'nun SL/TP'leri
    alarm fiyatına göre çizilir; fiyat stop mesafesinin yarısını geçmişse
    RR merdiveni artık mesajdaki merdiven değildir (TP1 = 0.5×SL mesafesi
    olduğu için TP1 fiilen ARKAMIZDA kalabilir).
    """
    configured = _cfg_float(cfg, "follower_max_signal_drift_pct", 0.0)
    if configured > 0:
        return configured
    if not _is_finite_positive(sl_pct):
        return 0.0
    return float(sl_pct) * 0.5


def rr_multipliers(cfg: Any) -> Tuple[float, float, float]:
    """TP'lerin SL mesafesine oranı (varsayılan 0.5 / 1.0 / 1.5)."""
    return (
        _cfg_float(cfg, "follower_tp_rr1", 0.5),
        _cfg_float(cfg, "follower_tp_rr2", 1.0),
        _cfg_float(cfg, "follower_tp_rr3", 1.5),
    )


def computed_stop(
    *, entry: float, direction: Direction, atr_value: Optional[float], cfg: Any
) -> Optional[float]:
    """k×ATR kuralıyla stop fiyatı; ATR yoksa/sıfırsa None."""
    if not _is_finite_positive(entry) or not _is_finite_positive(atr_value):
        return None
    mult = _cfg_float(cfg, "follower_sl_atr_mult", 3.0)
    if mult <= 0:
        return None
    distance = float(atr_value) * mult
    stop = entry - distance if direction == Direction.LONG else entry + distance
    return stop if stop > 0 else None


def resolve_levels(
    *,
    entry: float,
    direction: Direction,
    message: MessageLevels,
    atr_value: Optional[float],
    cfg: Any,
) -> FollowerLevels:
    """Mutlak SL/TP seviyelerini çöz (öncelik: mesaj → k×ATR kuralı)."""
    if not _is_finite_positive(entry):
        raise FollowerRejected(
            f"Geçersiz giriş fiyatı ({entry})", code="invalid_entry"
        )

    warnings: List[str] = []
    stop: Optional[float] = None
    stop_from_message = False

    if message.has_sl:
        candidate = float(message.sl)  # type: ignore[arg-type]
        if _stop_on_correct_side(direction, entry, candidate):
            stop = candidate
            stop_from_message = True
        else:
            warnings.append(
                f"mesaj sl={candidate:g} girişin yanlış tarafında — ATR kuralına düşüldü"
            )

    if stop is None:
        if not message.has_sl:
            # Birincil yol AlgoPro'nun kendi seviyeleridir; buraya düşmek
            # "alert biçimi beklenenden farklı" demektir ve GÖRÜNÜR olmalıdır.
            warnings.append(
                "mesajda SL yok — k×ATR yedek kuralı kullanıldı "
                "(AlgoPro alert biçimini doğrula)"
            )
        stop = computed_stop(
            entry=entry, direction=direction, atr_value=atr_value, cfg=cfg
        )
    if stop is None:
        raise FollowerRejected(
            "Seviye üretilemedi: mesajda SL yok ve ATR hesaplanamadı "
            "(1m mum verisi yetersiz) — giriş yapılmadı",
            code="no_levels",
        )

    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        raise FollowerRejected(
            "Stop mesafesi sıfır — giriş yapılmadı", code="zero_stop"
        )
    sl_pct = stop_distance / entry * 100.0

    min_pct = _cfg_float(cfg, "follower_min_sl_pct", 0.02)
    max_pct = _cfg_float(cfg, "follower_max_sl_pct", 5.0)
    if not (min_pct <= sl_pct <= max_pct):
        raise FollowerRejected(
            f"Stop mesafesi bant dışı (%{sl_pct:.4f}, izin verilen "
            f"[%{min_pct}-%{max_pct}]) — giriş yapılmadı",
            code="stop_band",
        )

    rr = rr_multipliers(cfg)
    computed_tps = tuple(
        entry + rr_k * stop_distance
        if direction == Direction.LONG
        else entry - rr_k * stop_distance
        for rr_k in rr
    )

    message_tps = (message.tp1, message.tp2, message.tp3)
    resolved: List[float] = []
    tp_from_message = [False, False, False]
    for index, (msg_tp, calc_tp) in enumerate(zip(message_tps, computed_tps)):
        label = f"tp{index + 1}"
        if not _is_finite_positive(msg_tp):
            resolved.append(calc_tp)
            continue
        value = float(msg_tp)  # type: ignore[arg-type]
        if not _tp_on_correct_side(direction, entry, value):
            warnings.append(
                f"mesaj {label}={value:g} girişin yanlış tarafında — hesaplanan kullanıldı"
            )
            resolved.append(calc_tp)
            continue
        resolved.append(value)
        tp_from_message[index] = True

    # Sıralama: LONG'da tp1 < tp2 < tp3, SHORT'ta tersi. Bozuk sıralama
    # (ör. şablonda tp2/tp3 yer değiştirmiş) 3 parça çıkışın anlamını yok
    # eder — o seviye hesaplanana düşürülür.
    for index in range(1, 3):
        previous, current = resolved[index - 1], resolved[index]
        ordered = current > previous if direction == Direction.LONG else current < previous
        if not ordered:
            warnings.append(
                f"mesaj tp{index + 1}={current:g} sıralamayı bozuyor — hesaplanan kullanıldı"
            )
            resolved[index] = computed_tps[index]
            tp_from_message[index] = False

    # ONARIM DOĞRULAMASI (fail-closed). Hesaplanan değeri koymak sıralamayı
    # GARANTİ ETMEZ: mesajdan gelen bir önceki TP hesaplananın ötesinde
    # olabilir (ör. tp1=101 mesajdan, computed tp2=101 → İKİSİ AYNI FİYAT).
    # Aynı tetikte iki TAKE_PROFIT_MARKET, 3 kademeli çıkışı yok eder ve
    # `_check_tp_telemetry`'nin `consumed` aritmetiğini bozar; ters sıralı
    # merdiven ise en büyük dilimi en yakın hedeften çıkarır. Onarılamayan
    # merdivenle GİRİŞ YAPILMAZ — para riske girmeden reddedilir.
    for index in range(1, 3):
        previous, current = resolved[index - 1], resolved[index]
        ordered = current > previous if direction == Direction.LONG else current < previous
        if not ordered:
            raise FollowerRejected(
                f"TP merdiveni onarılamadı (tp{index}={previous:g}, "
                f"tp{index + 1}={current:g}, yön={direction.value}) — "
                f"RR çarpanları ({', '.join(f'{r:g}' for r in rr)}) artan olmalı; "
                f"giriş yapılmadı",
                code="tp_order",
            )

    if stop_from_message and all(tp_from_message):
        source = LEVEL_SOURCE_MESSAGE
    elif stop_from_message or any(tp_from_message):
        source = LEVEL_SOURCE_MIXED
    else:
        source = LEVEL_SOURCE_ATR

    return FollowerLevels(
        entry=float(entry),
        stop=float(stop),
        tp1=float(resolved[0]),
        tp2=float(resolved[1]),
        tp3=float(resolved[2]),
        stop_distance=float(stop_distance),
        sl_pct=float(sl_pct),
        source=source,
        atr_value=float(atr_value) if _is_finite_positive(atr_value) else None,
        message_levels=message,
        warnings=tuple(warnings),
    )


def calibration_record(
    *,
    symbol: str,
    direction: Direction,
    kind: str,
    ts: str,
    levels: FollowerLevels,
    cfg: Any,
) -> dict:
    """``state/follower_levels.jsonl`` satırı: hesaplanan vs mesaj sapması.

    Amaç (kullanıcı isteği): AlgoPro'nun çizdiği seviyeler ile k×ATR kuralının
    ürettikleri arasındaki farkı ÖLÇMEK — "Strict ATR, 3" hipotezi ancak bu
    defterle doğrulanabilir/çürütülebilir. Rapor aracı yok; ham veri yeter.
    """
    calc_stop = computed_stop(
        entry=levels.entry,
        direction=direction,
        atr_value=levels.atr_value,
        cfg=cfg,
    )
    calc_distance = (
        abs(levels.entry - calc_stop) if calc_stop is not None else None
    )
    message_sl = levels.message_levels.sl
    deviation_pct = None
    if calc_distance and _is_finite_positive(message_sl):
        message_distance = abs(levels.entry - float(message_sl))
        if calc_distance > 0:
            deviation_pct = (message_distance - calc_distance) / calc_distance * 100.0

    return {
        "ts": ts,
        "symbol": symbol,
        "direction": direction.value,
        "kind": kind,
        "entry": levels.entry,
        "used": levels.as_dict(),
        "message": levels.message_levels.as_dict(),
        "computed": {
            "sl": calc_stop,
            "stop_distance": calc_distance,
            "atr": levels.atr_value,
            "atr_mult": _cfg_float(cfg, "follower_sl_atr_mult", 3.0),
            "atr_len": _cfg_int(cfg, "follower_atr_len", 14),
            "rr": list(rr_multipliers(cfg)),
        },
        "sl_distance_deviation_pct": deviation_pct,
    }
