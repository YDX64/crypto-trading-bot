"""İşlem adli kaydı (trade forensics) — SAF katman (D21).

Amaç (kullanıcı talebi, 2026-08-23): "hangi coin, hangi hareket, hangi sinyal,
hangi giriş/çıkış, neler etkiliyor" sorularının HER işlem için tek bakışta
yanıtlanması. Bu modül **yalnız gözlemlenebilirlik** içindir: hiçbir kapı,
boyutlama ya da çıkış kararı buradan beslenmez. Motor davranışı DEĞİŞMEZ.

Tasarım kuralları:

1. **SAF.** Bu dosyada IO, saat okuma (`time.time()`), rastgelelik ve global
   durum YOKTUR. Zaman damgaları çağıran tarafından geçirilir. Böylece her
   etiket kuralı tek tek test edilebilir (`tests/test_forensics.py`).
2. **Look-ahead yok.** `build_entry`/`build_exit` yalnız o ANDA bilinen
   değerleri kaydeder. Kapanıştan SONRA ölçülebilen tek büyüklük
   (`noise_stop` — "stop yedikten sonra fiyat girişe döndü mü") AYRI bir
   `postmortem` alanındadır ve `postmortem_from_candles` YALNIZ kapanış
   zamanından SONRAKİ mumlarla çağrılır (bkz. fonksiyonun docstring'i).
3. **Fail-safe.** Girdiler eksik/bozuk olabilir; tüm okumalar `_f`/`_s` ile
   savunmalıdır ve etiket kuralları eksik veride etiket ÜRETMEZ (sessiz
   yanlış-pozitif üretmektense hiç etiket üretmemek yeğdir).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

FORENSICS_VERSION = 1

# --------------------------------------------------------------------------
# Etiketler (verdict) — kural tabanlı, basit ve dürüst.
# --------------------------------------------------------------------------

TAG_COUNTER_DRIFT_LONG = "counter_drift_long"
TAG_RELIEF_RALLY_SHORT = "relief_rally_short"
TAG_LATE_ENTRY_AFTER_RUN = "late_entry_after_run"
TAG_TV_SINGLE_FAMILY = "tv_single_family"
TAG_STALE_SIGNAL = "stale_signal"
TAG_GATE_BYPASSED = "gate_bypassed"
TAG_FEE_DOMINATED = "fee_dominated"
TAG_MFE_GIVEBACK = "mfe_giveback"
TAG_NOISE_STOP = "noise_stop"

#: Panoda rozetin altına yazılan tek satırlık Türkçe açıklama.
TAG_LABELS: Dict[str, str] = {
    TAG_COUNTER_DRIFT_LONG: "lider düşerken LONG açıldı",
    TAG_RELIEF_RALLY_SHORT: "lider yükselirken SHORT açıldı",
    TAG_LATE_ENTRY_AFTER_RUN: "çok günlük koşunun ARDINDAN aynı yöne girildi",
    TAG_TV_SINGLE_FAMILY: "TV sağlaması aynı aileden iki kaynakla doldu",
    TAG_STALE_SIGNAL: "sinyal ile dolum arasında uzun gecikme",
    TAG_GATE_BYPASSED: "kapı açık ama ETKİN DEĞİL (fail-open) iken girildi",
    TAG_FEE_DOMINATED: "ücretler kârın yarısından fazlasını yedi",
    TAG_MFE_GIVEBACK: "kâr TP1 hedefini gördü ama zararla kapandı",
    TAG_NOISE_STOP: "stop sonrası fiyat pencerede girişe geri döndü (gürültü)",
}

#: Etiketin kaydın hangi bölümünde hesaplandığı (panoda gruplama için).
TAG_STAGE: Dict[str, str] = {
    TAG_COUNTER_DRIFT_LONG: "entry",
    TAG_RELIEF_RALLY_SHORT: "entry",
    TAG_LATE_ENTRY_AFTER_RUN: "entry",
    TAG_TV_SINGLE_FAMILY: "entry",
    TAG_STALE_SIGNAL: "entry",
    TAG_GATE_BYPASSED: "entry",
    TAG_FEE_DOMINATED: "exit",
    TAG_MFE_GIVEBACK: "exit",
    TAG_NOISE_STOP: "postmortem",
}

ALL_TAGS: tuple = tuple(TAG_LABELS.keys())


# --------------------------------------------------------------------------
# Eşikler
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class VerdictThresholds:
    """Etiket kurallarının eşikleri — hepsi `.env`'den ayarlanabilir."""

    #: Lider gün-içi sapması bu %'yi aşarsa ters yön girişi etiketlenir.
    counter_drift_pct: float = 1.0
    #: Lider çok-günlük koşusu bu %'yi aşarsa aynı yön girişi "geç" sayılır.
    run_pct: float = 5.0
    #: Sinyal → dolum gecikmesi bu saniyeyi aşarsa `stale_signal`.
    stale_signal_sec: float = 30.0
    #: net/brüt bu oranın altındaysa `fee_dominated`.
    fee_ratio: float = 0.5
    #: Tepe ROI bu değeri gördüğü hâlde zararla kapandıysa `mfe_giveback`.
    giveback_roi_pct: float = 20.0
    #: Kapanıştan sonra "girişe dönüş" aranan pencere (dakika).
    noise_window_min: float = 60.0


def thresholds_from_cfg(cfg: Any) -> VerdictThresholds:
    """Ayarlardan eşikleri çöz; eksik/bozuk alanlarda varsayılana düşer."""
    base = VerdictThresholds()
    return VerdictThresholds(
        counter_drift_pct=_cfg_float(
            cfg, "scalper_forensics_counter_drift_pct", base.counter_drift_pct
        ),
        run_pct=_cfg_float(cfg, "scalper_forensics_run_pct", base.run_pct),
        stale_signal_sec=_cfg_float(
            cfg, "scalper_forensics_stale_signal_sec", base.stale_signal_sec
        ),
        fee_ratio=_cfg_float(cfg, "scalper_forensics_fee_ratio", base.fee_ratio),
        giveback_roi_pct=_cfg_float(
            cfg, "scalper_tp1_roi", base.giveback_roi_pct
        ),
        noise_window_min=_cfg_float(
            cfg, "scalper_forensics_postmortem_min", base.noise_window_min
        ),
    )


# --------------------------------------------------------------------------
# Küçük savunmalı yardımcılar
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


def _cfg_float(cfg: Any, name: str, default: float) -> float:
    value = _f(getattr(cfg, name, None))
    return default if value is None else value


def _direction_value(direction: Any) -> str:
    return str(getattr(direction, "value", direction) or "").upper()


def source_family(source: Any) -> str:
    """TV kaynak etiketini "aile"sine indirger.

    `luxso_osc` ve `luxso_trend` AYNI göstergenin (LuxAlgo S&O) iki farklı
    alarmıdır: sağlama sayacı 2 gösterse de bu BİR görüştür. Aile eşlemesi
    tam da bunu görünür kılar (`tv_single_family`).
    """
    text = str(source or "").strip().lower()
    if not text:
        return "?"
    if text.startswith("lux"):
        return "luxalgo"
    if text.startswith("algopro"):
        return "algopro"
    if text.startswith("pac"):
        return "pac"
    return text.split("_", 1)[0]


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


# --------------------------------------------------------------------------
# Gösterge anlık görüntüsü (giriş anı)
# --------------------------------------------------------------------------

def indicator_snapshot(ctx: Any, cfg: Any = None) -> Dict[str, Any]:
    """Giriş anındaki strateji C girdileri — `StrategyContext`'ten türetilir.

    YENİ VERİ ÇEKİLMEZ: yalnız ctx'te ZATEN bulunan seriler kullanılır
    (aynı ilke: `structure.structure_series`). Hesap saf ve ucuzdur.
    """
    # Yerel import: `types.py` gibi bu modül de bağımlılık zincirini
    # olabildiğince kısa tutar; indicators sadece burada gerekir.
    from src.strategies.scalper.indicators import (
        bearish_divergence,
        bollinger,
        bullish_divergence,
        ema,
        rsi_series,
    )

    out: Dict[str, Any] = {}
    candles_entry = list(getattr(ctx, "candles_5m", None) or [])
    candles_ctx = list(getattr(ctx, "candles_15m", None) or [])
    candles_regime = list(getattr(ctx, "candles_4h", None) or [])

    if candles_entry:
        closes = [c.close for c in candles_entry]
        rsi_vals = rsi_series(closes, 14)
        out["rsi_entry"] = _round(rsi_vals[-1] if rsi_vals else None, 2)
        upper, mid, lower = bollinger(closes, 20, 2.0)
        last_close = closes[-1]
        out["bb_upper"] = _round(upper, 8)
        out["bb_mid"] = _round(mid, 8)
        out["bb_lower"] = _round(lower, 8)
        width = upper - lower
        out["bb_percent_b"] = _round(
            ((last_close - lower) / width * 100.0) if width > 0 else None, 2
        )
        try:
            out["bullish_divergence"] = bool(
                bullish_divergence(candles_entry, rsi_vals, 40)
            )
            out["bearish_divergence"] = bool(
                bearish_divergence(candles_entry, rsi_vals, 40)
            )
        except Exception:  # pragma: no cover - saf hesap, yine de kayıt düşmesin
            out["bullish_divergence"] = None
            out["bearish_divergence"] = None
        price = _f(getattr(ctx, "current_price", None)) or last_close
        atr_value = _f(getattr(ctx, "atr_5m", None))
        out["atr"] = _round(atr_value, 8)
        out["atr_pct"] = _round(
            (atr_value / price * 100.0) if (atr_value and price) else None, 3
        )

    if candles_ctx:
        rsi_ctx = rsi_series([c.close for c in candles_ctx], 14)
        out["rsi_context"] = _round(rsi_ctx[-1] if rsi_ctx else None, 2)

    if len(candles_regime) >= 200:
        regime_closes = [c.close for c in candles_regime]
        out["ema50"] = _round(ema(regime_closes, 50)[-1], 8)
        out["ema200"] = _round(ema(regime_closes, 200)[-1], 8)
        out["regime_close"] = _round(regime_closes[-1], 8)

    out["tf_entry"] = _s(getattr(cfg, "scalper_tf_entry", None)) if cfg else None
    out["tf_context"] = _s(getattr(cfg, "scalper_tf_context", None)) if cfg else None
    out["tf_regime"] = _s(getattr(cfg, "scalper_tf_regime", None)) if cfg else None
    return out


def _round(value: Any, digits: int) -> Optional[float]:
    out = _f(value)
    return None if out is None else round(out, digits)


# --------------------------------------------------------------------------
# Giriş kaydı
# --------------------------------------------------------------------------

def leader_gate_snapshot(gate_status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`engine._market_gate_status()` çıktısını adli kayıt biçimine indirger.

    `verdict` üç durumludur ve `enabled` ile KARIŞTIRILMAMALIDIR (D15 dersi):
      * `"kapalı"`      — kapı hiç açık değil, hiçbir şeyi engellemiyor.
      * `"geçti"`       — kapı ETKİN ve bu giriş kapıdan geçti.
      * `"etkin_değil"` — kapı açık ama fail-open (lider verisi yok/bayat ya
        da tüm eşikler 0) → giriş fiilen KAPISIZ yapıldı (`gate_bypassed`).
    """
    status = dict(gate_status or {})
    enabled = bool(status.get("enabled"))
    effective = bool(status.get("gate_effective"))
    if not enabled:
        verdict = "kapalı"
    elif effective:
        verdict = "geçti"
    else:
        verdict = "etkin_değil"
    return {
        "enabled": enabled,
        "gate_effective": effective,
        "verdict": verdict,
        "leader": _s(status.get("leader")),
        "day_drift_pct": _round(status.get("day_drift_pct"), 4),
        "run_drift_pct": _round(status.get("run_drift_pct"), 4),
        "thresholds": dict(status.get("thresholds") or {}),
        "stale": bool(status.get("stale")),
        "day_open_source": _s(status.get("day_open_source")),
    }


def build_entry(
    *,
    at: str,
    signal: Any,
    ctx: Any,
    cfg: Any,
    fill_price: float,
    quantity: float,
    leverage: int,
    margin_usdt: float,
    stop_price: float,
    tp1_price: float,
    tp2_price: float,
    breakeven_price: Optional[float] = None,
    signal_at: Optional[str] = None,
    fill_latency_sec: Optional[float] = None,
    entry_mode: Optional[str] = None,
    indicators: Optional[Dict[str, Any]] = None,
    regime_info: Optional[Dict[str, Any]] = None,
    leader_gate: Optional[Dict[str, Any]] = None,
    structure: Optional[Dict[str, Any]] = None,
    tv_structure: Optional[Dict[str, Any]] = None,
    gates: Optional[Dict[str, Any]] = None,
    tv: Optional[Dict[str, Any]] = None,
    source: str = "C",
    kline_source: Optional[str] = None,
    open_positions: Optional[int] = None,
    daily_pnl: Optional[float] = None,
    btc_price: Optional[float] = None,
    rr: Optional[float] = None,
) -> Dict[str, Any]:
    """Giriş ANINDA bilinen her şeyi tek sözlükte topla (look-ahead yok)."""
    signal_price = _f(getattr(signal, "entry_price", None))
    fill = _f(fill_price)
    stop = _f(stop_price)
    lev = int(leverage or 0) or 1

    slippage_pct = None
    if signal_price and fill:
        raw = (fill - signal_price) / signal_price * 100.0
        # İşaret YÖNE göre normalize edilir: pozitif = ALEYHTE kayma.
        if _direction_value(getattr(signal, "direction", None)) == "SHORT":
            raw = -raw
        slippage_pct = round(raw, 4)

    stop_distance_pct = None
    if fill and stop:
        stop_distance_pct = round(abs(fill - stop) / fill * 100.0, 4)

    notional = None
    if fill and _f(quantity) is not None:
        notional = round(fill * float(quantity), 4)

    return {
        "at": at,
        "symbol": _s(getattr(signal, "symbol", None)),
        "direction": _direction_value(getattr(signal, "direction", None)),
        "strategy": _s(getattr(signal, "strategy", None)),
        "source": source,
        "signal_reason": _s(getattr(signal, "reason", None)),
        "signal_price": _round(signal_price, 8),
        "fill_price": _round(fill, 8),
        "slippage_pct": slippage_pct,
        "signal_at": signal_at,
        "fill_latency_sec": _round(fill_latency_sec, 3),
        "entry_mode": _s(entry_mode),
        "quantity": _round(quantity, 8),
        "leverage": lev,
        "margin_usdt": _round(margin_usdt, 4),
        "notional_usdt": notional,
        "stop_price": _round(stop, 8),
        "stop_distance_pct": stop_distance_pct,
        "stop_roi_pct": (
            None if stop_distance_pct is None else round(stop_distance_pct * lev, 2)
        ),
        "tp1_price": _round(tp1_price, 8),
        "tp2_price": _round(tp2_price, 8),
        "breakeven_price": _round(breakeven_price, 8),
        "tp1_roi_pct": _round(getattr(cfg, "scalper_tp1_roi", None), 2),
        "tp2_roi_pct": _round(getattr(cfg, "scalper_tp2_roi", None), 2),
        "rr": _round(rr, 3),
        "min_rr": _round(getattr(cfg, "scalper_min_rr", None), 3),
        "risk_multiplier": _round(getattr(signal, "risk_multiplier", None), 3),
        "indicators": dict(indicators or (indicator_snapshot(ctx, cfg) if ctx else {})),
        "regime": dict(regime_info or {}),
        "leader_gate": dict(leader_gate or {}),
        "structure": dict(structure) if structure else None,
        "tv_structure": dict(tv_structure) if tv_structure else None,
        "tv": dict(tv) if tv else None,
        "gates": dict(gates or {}),
        "kline_source": _s(kline_source),
        "open_positions": None if open_positions is None else int(open_positions),
        "daily_pnl": _round(daily_pnl, 4),
        "btc_price": _round(btc_price, 8),
    }


# --------------------------------------------------------------------------
# Çıkış kaydı
# --------------------------------------------------------------------------

def build_exit(
    *,
    at: str,
    reason: str,
    exit_price: Optional[float],
    entry_price: Optional[float],
    quantity: Optional[float],
    leverage: Optional[int],
    direction: Any,
    realized_pnl: Optional[float],
    gross_pnl: Optional[float],
    pnl_source: Optional[str],
    mae_roi_pct: Optional[float],
    mfe_roi_pct: Optional[float],
    duration_sec: Optional[float],
    path: Optional[Dict[str, Any]] = None,
    leader_day_drift_pct: Optional[float] = None,
    regime: Optional[str] = None,
    btc_price: Optional[float] = None,
    verification_notes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Kapanış ANINDA bilinen her şeyi tek sözlükte topla."""
    lev = int(leverage or 0) or 1
    net = _f(realized_pnl)
    gross = _f(gross_pnl)
    fee_estimate = None
    if net is not None and gross is not None:
        fee_estimate = round(gross - net, 6)

    mae = _f(mae_roi_pct)
    mfe = _f(mfe_roi_pct)
    return {
        "at": at,
        "reason": _s(reason),
        "exit_price": _round(exit_price, 8),
        "entry_price": _round(entry_price, 8),
        "quantity": _round(quantity, 8),
        "leverage": lev,
        "direction": _direction_value(direction),
        "realized_pnl": _round(net, 6),
        "gross_pnl": _round(gross, 6),
        "fee_estimate": fee_estimate,
        "pnl_source": _s(pnl_source),
        "mae_roi_pct": _round(mae, 3),
        "mfe_roi_pct": _round(mfe, 3),
        "mae_price_pct": None if mae is None else round(mae / lev, 4),
        "mfe_price_pct": None if mfe is None else round(mfe / lev, 4),
        "price_move_pct": _pct_move(direction, entry_price, exit_price),
        "duration_sec": _round(duration_sec, 1),
        "path": dict(path or {}),
        "leader_day_drift_pct": _round(leader_day_drift_pct, 4),
        "regime": _s(regime),
        "btc_price": _round(btc_price, 8),
        "verification_notes": list(verification_notes or []),
    }


def _pct_move(direction: Any, entry: Any, exit_price: Any) -> Optional[float]:
    """Fiyatın işlem LEHİNE yüzde hareketi (pozitif = lehte)."""
    e = _f(entry)
    x = _f(exit_price)
    if not e or x is None:
        return None
    raw = (x - e) / e * 100.0
    if _direction_value(direction) == "SHORT":
        raw = -raw
    return round(raw, 4)


# --------------------------------------------------------------------------
# Etiket kuralları (SAF — her biri tek tek test edilir)
# --------------------------------------------------------------------------

def classify_entry(
    entry: Optional[Dict[str, Any]], th: Optional[VerdictThresholds] = None
) -> List[str]:
    """Giriş ANINDA bilinen bilgiyle hesaplanabilen etiketler."""
    th = th or VerdictThresholds()
    entry = entry or {}
    tags: List[str] = []
    direction = str(entry.get("direction") or "").upper()
    gate = entry.get("leader_gate") or {}
    day_drift = _f(gate.get("day_drift_pct"))
    run_drift = _f(gate.get("run_drift_pct"))

    if day_drift is not None and th.counter_drift_pct > 0:
        if direction == "LONG" and day_drift <= -th.counter_drift_pct:
            tags.append(TAG_COUNTER_DRIFT_LONG)
        elif direction == "SHORT" and day_drift >= th.counter_drift_pct:
            tags.append(TAG_RELIEF_RALLY_SHORT)

    if run_drift is not None and th.run_pct > 0:
        # "Geç giriş": lider zaten aynı yöne uzamışken aynı yöne girmek.
        if direction == "LONG" and run_drift >= th.run_pct:
            tags.append(TAG_LATE_ENTRY_AFTER_RUN)
        elif direction == "SHORT" and run_drift <= -th.run_pct:
            tags.append(TAG_LATE_ENTRY_AFTER_RUN)

    tv = entry.get("tv") or {}
    sources = [s for s in (tv.get("sources") or []) if s]
    if len(sources) >= 2 and len({source_family(s) for s in sources}) == 1:
        tags.append(TAG_TV_SINGLE_FAMILY)

    latency = _f(entry.get("fill_latency_sec"))
    if latency is not None and th.stale_signal_sec > 0 and latency > th.stale_signal_sec:
        tags.append(TAG_STALE_SIGNAL)

    if gate.get("verdict") == "etkin_değil":
        tags.append(TAG_GATE_BYPASSED)

    return tags


def classify_exit(
    entry: Optional[Dict[str, Any]],
    exit_: Optional[Dict[str, Any]],
    th: Optional[VerdictThresholds] = None,
) -> List[str]:
    """Kapanış ANINDA bilinen bilgiyle hesaplanabilen etiketler."""
    th = th or VerdictThresholds()
    exit_ = exit_ or {}
    tags: List[str] = []

    net = _f(exit_.get("realized_pnl"))
    gross = _f(exit_.get("gross_pnl"))
    if net is not None and gross is not None and gross > 0:
        if net < th.fee_ratio * gross:
            tags.append(TAG_FEE_DOMINATED)

    mfe = _f(exit_.get("mfe_roi_pct"))
    if (
        mfe is not None
        and th.giveback_roi_pct > 0
        and mfe >= th.giveback_roi_pct
        and net is not None
        and net < 0
    ):
        tags.append(TAG_MFE_GIVEBACK)

    return tags


def classify(
    entry: Optional[Dict[str, Any]],
    exit_: Optional[Dict[str, Any]] = None,
    th: Optional[VerdictThresholds] = None,
) -> List[str]:
    """Giriş + çıkış etiketlerinin sırası korunmuş, tekilleştirilmiş birleşimi."""
    return _dedup(classify_entry(entry, th) + classify_exit(entry, exit_, th))


def _dedup(items: Iterable[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------
# Post-mortem (kapanıştan SONRA — look-ahead DEĞİL)
# --------------------------------------------------------------------------

def postmortem_from_candles(
    *,
    entry: Optional[Dict[str, Any]],
    exit_: Optional[Dict[str, Any]],
    candles: Sequence[Any],
    closed_at_ms: int,
    th: Optional[VerdictThresholds] = None,
) -> Dict[str, Any]:
    """Kapanıştan SONRAKİ pencerede fiyat girişe döndü mü?

    **Look-ahead yoktur ve OLAMAZ:** bu fonksiyon yalnız `closed_at_ms`'ten
    SONRA kapanmış mumlara bakar (daha eski mumlar açıkça elenir) ve sonucu
    kaydın AYRI `postmortem` alanına yazar. Hiçbir kapı, boyutlama veya çıkış
    kararı bu alanı okumaz — okusaydı gelecekten bilgi sızardı. Kaydın
    `entry`/`exit` bölümleri bu çağrıdan ETKİLENMEZ.

    `noise_stop` = stop yenildi (ya da zararla kapandı) VE pencere içinde
    fiyat giriş seviyesine LEHTE geri döndü → çıkış, trendin değil gürültünün
    sonucuydu.
    """
    th = th or VerdictThresholds()
    entry = entry or {}
    exit_ = exit_ or {}
    window_min = max(0.0, th.noise_window_min)
    window_ms = int(window_min * 60_000)
    end_ms = closed_at_ms + window_ms

    direction = str(entry.get("direction") or exit_.get("direction") or "").upper()
    entry_price = _f(entry.get("fill_price")) or _f(exit_.get("entry_price"))

    out: Dict[str, Any] = {
        "window_minutes": window_min,
        "candles_seen": 0,
        "returned_to_entry": None,
        "minutes_to_return": None,
        "max_favorable_pct": None,
        "tags": [],
        "note": "kapanıştan SONRAKİ mumlar; karar yoluna GİRMEZ (look-ahead değil)",
    }
    if not entry_price or direction not in ("LONG", "SHORT") or window_ms <= 0:
        return out

    best: Optional[float] = None
    returned_at: Optional[int] = None
    seen = 0
    for candle in candles or []:
        close_time = _f(getattr(candle, "close_time", None))
        if close_time is None or close_time <= closed_at_ms or close_time > end_ms:
            continue
        seen += 1
        if direction == "LONG":
            extreme = _f(getattr(candle, "high", None))
            if extreme is None:
                continue
            favorable = (extreme - entry_price) / entry_price * 100.0
            hit = extreme >= entry_price
        else:
            extreme = _f(getattr(candle, "low", None))
            if extreme is None:
                continue
            favorable = (entry_price - extreme) / entry_price * 100.0
            hit = extreme <= entry_price
        if best is None or favorable > best:
            best = favorable
        if hit and returned_at is None:
            returned_at = int(close_time)

    out["candles_seen"] = seen
    if seen == 0:
        return out
    out["max_favorable_pct"] = None if best is None else round(best, 4)
    out["returned_to_entry"] = returned_at is not None
    if returned_at is not None:
        out["minutes_to_return"] = round((returned_at - closed_at_ms) / 60_000.0, 1)

    reason = str(exit_.get("reason") or "").upper()
    net = _f(exit_.get("realized_pnl"))
    losing = reason == "SL" or (net is not None and net < 0)
    if losing and returned_at is not None:
        out["tags"] = [TAG_NOISE_STOP]
    return out


# --------------------------------------------------------------------------
# Özet (etiket × sonuç)
# --------------------------------------------------------------------------

def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Etiket × sonuç tablosu — "neler etkiliyor" sorusunun cevabı.

    `rows`: `{"tags": [...], "pnl": float}` sözlükleri. Bir işlem birden çok
    etiket taşıyabilir; her etiket kendi satırında sayılır (satırların toplamı
    işlem sayısını AŞABİLİR — bu bir hata değil, kasıtlıdır).
    `_etiketsiz_` satırı hiç etiket almamış işlemleri gösterir: kıyas tabanı
    olmadan "şu etiket kötü" demek anlamsızdır.
    """
    buckets: Dict[str, Dict[str, float]] = {}
    total_trades = 0
    total_pnl = 0.0

    def _bucket(name: str) -> Dict[str, float]:
        return buckets.setdefault(
            name,
            {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
             "gross_win": 0.0, "gross_loss": 0.0},
        )

    for row in rows or []:
        pnl = _f(row.get("pnl")) or 0.0
        tags = [t for t in (row.get("tags") or []) if t]
        total_trades += 1
        total_pnl += pnl
        for name in _dedup(tags) or ["_etiketsiz_"]:
            bucket = _bucket(name)
            bucket["trades"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
                bucket["gross_win"] += pnl
            elif pnl < 0:
                bucket["losses"] += 1
                bucket["gross_loss"] += abs(pnl)

    table: List[Dict[str, Any]] = []
    for name, bucket in buckets.items():
        trades = int(bucket["trades"])
        wins = int(bucket["wins"])
        gross_win = bucket["gross_win"]
        gross_loss = bucket["gross_loss"]
        if gross_loss > 0:
            profit_factor: Optional[float] = round(gross_win / gross_loss, 3)
        else:
            profit_factor = None
        table.append({
            "tag": name,
            "label": TAG_LABELS.get(name, ""),
            "stage": TAG_STAGE.get(name, "—"),
            "trades": trades,
            "wins": wins,
            "losses": int(bucket["losses"]),
            "winrate": round(wins / trades * 100.0, 1) if trades else 0.0,
            "pnl": round(bucket["pnl"], 4),
            "avg_pnl": round(bucket["pnl"] / trades, 4) if trades else 0.0,
            "profit_factor": profit_factor,
        })

    # En zararlıdan en kârlıya: "neyi düzeltmeliyim" listesi tepede başlar.
    table.sort(key=lambda row: (row["pnl"], -row["trades"]))
    return {
        "trades": total_trades,
        "total_pnl": round(total_pnl, 4),
        "tags": table,
    }
