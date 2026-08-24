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

#: D24/madde 6: "beklenti" (expectation) alanlarının serbest metin sınırı.
#: Kayıt bir teşhis satırıdır, deneme değil: 200 karakter bir insanın tek
#: bakışta okuyabileceği en uzun gerekçedir ve JSONL satırını şişirmez.
EXPECTATION_TEXT_MAX = 200

#: `build_entry`'nin döndürdüğü beklenti anahtarları — sıra RAPOR sırasıdır.
EXPECTATION_FIELDS: tuple = (
    "horizon_end_at",
    "invalid_if",
    "confidence",
    "model_version",
)

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


def _trim(value: Any, limit: int = EXPECTATION_TEXT_MAX) -> Optional[str]:
    """Serbest metni `limit` karaktere kırp (kırpıldığını İŞARETLEME).

    Kırpma sessizdir: bir teşhis alanında "…(kırpıldı)" gibi bir ek, metnin
    kendisi kadar yer kaplar ve `jq` ile eşleştirmeyi bozar.
    """
    text = _s(value)
    if text is None:
        return None
    return text[: max(0, int(limit))] or None


def _unit(value: Any) -> Optional[float]:
    """0.0–1.0 aralığına KIRP (clamp); aralık dışı değer hata DEĞİLDİR.

    Kırpıldığı AYRICA kaydedilmez (madde 6.1): kaydın tüketicisi eşik
    karşılaştırması yapar, kırpma tarihçesi değil.
    """
    out = _f(value)
    if out is None:
        return None
    if out < 0.0:
        out = 0.0
    elif out > 1.0:
        out = 1.0
    return round(out, 3)


def _direction_value(direction: Any) -> str:
    return str(getattr(direction, "value", direction) or "").upper()


# D22: çıkış nedeni ailesi. `TRAIL_MARKET`/`BE_MARKET`, koruyucu stopun
# borsaya konulamayıp (-2021) `_emergency_close` ile piyasa emrine dönüştüğü
# kapanışlardır — AYRI SAYILIR (sayıları stop kararının piyasa hızının
# gerisinde kaldığını gösterir) ama TRAIL gibi raporlanır. "SL yedi"
# DEĞİLDİRler: seviye kâr tarafındaki bir stoptu.
# D27/A1: `REAPER` = 8 saatlik yaş kesmesi (D4, `SCALPER_MAX_HOLD_HOURS`).
# KENDİ AİLESİ olarak durur — SL ailesine katmak, ölçüm borcunu kapatmak için
# ayırdığımız etiketi rapor katmanında yeniden karıştırırdı. Yan etki (kasıtlı):
# postmortem'in `losing` kuralı REAPER'ı artık yalnız NET PnL negatifse kayıplı
# sayar; ARTIDA kesilen bir pozisyonda `noise_stop` ("stop sonrası fiyat girişe
# döndü") sorusunun zaten anlamı yoktur.
_EXIT_REASON_FAMILY = {
    "SL": "SL",
    "REAPER": "REAPER",
    "TP_LADDER": "TP_LADDER",
    "TRAIL": "TRAIL",
    "TRAIL_MARKET": "TRAIL",
    "BE_MARKET": "TRAIL",
    "RISK_EVENT": "MANUAL",
    "TV_EVENT": "MANUAL",
    "MANUAL": "MANUAL",
}


def exit_reason_family(reason: Any) -> str:
    """Çıkış nedeninin ailesi; bilinmeyen değer kendisi olarak döner."""
    text = str(reason or "").strip().upper()
    if not text:
        return "UNKNOWN"
    return _EXIT_REASON_FAMILY.get(text, text)


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
    # ---- D24/madde 6: "ne BEKLEDİK" (expectation) -----------------------
    # Şema fikri AI-Trader'ın sinyal-kalitesi kaydından alınmıştır (YALNIZ
    # ALAN ADLARI; hiçbir satır kopyalanmadı — o repoda LİSANS METNİ YOKTUR,
    # README rozeti MIT der ama dosya yoktur, bu yüzden kod alınmaz).
    # Bugün bu alanları DOLDURAN bir çağıran YOKTUR: giriş yolundaki kanca
    # AYRI bir kararın konusudur. Doldurulmayan alan raporda "ÖLÇÜLMEDİ"
    # olarak görünür — bu DOĞRU ve beklenen sonuçtur.
    horizon_end_at: Optional[str] = None,
    invalid_if: Optional[str] = None,
    confidence: Optional[float] = None,
    model_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Giriş ANINDA bilinen her şeyi tek sözlükte topla (look-ahead yok).

    Beklenti alanları (`horizon_end_at`, `invalid_if`, `confidence`,
    `model_version`) OPSİYONELDİR ve dönen sözlükte HER ZAMAN bulunur
    (doldurulmadıysa `None`).

    `FORENSICS_VERSION` BUMP EDİLMEZ: alanlar yalnız EKLEMELİDİR, hiçbir
    mevcut alanın adı/anlamı değişmedi. Eski satırlar (v1) yeni okuyucuyla
    okunabilir — okuyucu eksik anahtarı `None` sayar — ve yeni satırlar eski
    okuyucuyla okunabilir (fazla anahtarı yok sayar). Sürümü artırmak,
    aslında uyumlu olan bir şemayı "kırıldı" diye işaretlerdi.
    """
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
        # Beklenti alanları: anahtarlar HER ZAMAN vardır ki tüketici
        # (`expectation_from_entry`, pano, rapor) "alan yok" ile "beklenti
        # yoktu"yu karıştırmasın.
        "horizon_end_at": _s(horizon_end_at),
        "invalid_if": _trim(invalid_if),
        "confidence": _unit(confidence),
        "model_version": _s(model_version),
    }


# --------------------------------------------------------------------------
# Beklenti (expectation) — "ne BEKLEDİK" kaydı (D24/madde 6)
# --------------------------------------------------------------------------

def expectation_from_entry(
    entry: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Giriş kaydından beklenti bloğunu çıkar; hiç doldurulmadıysa `None`.

    Sözleşme:
      * DÖRT alandan EN AZ BİRİ dolu ise dördü de (eksikler `None`) döner —
        kısmen doldurulmuş bir beklenti de bir beklentidir.
      * Hepsi boş/eksikse `None` döner. `None` = **ÖLÇÜLMEDİ**; "beklenti
        yoktu" DEĞİL. Ayrım önemlidir: kaydın kapalı olduğu bir dönemi
        "beklentisiz işlem" diye raporlamak ölçüm yokluğunu bulguya çevirir.
      * `entry` None/bozuk/sözlük-değil olabilir — ASLA patlamaz.
    """
    if not isinstance(entry, dict):
        return None
    out = {
        "horizon_end_at": _s(entry.get("horizon_end_at")),
        "invalid_if": _trim(entry.get("invalid_if")),
        "confidence": _unit(entry.get("confidence")),
        "model_version": _s(entry.get("model_version")),
    }
    if all(value is None for value in out.values()):
        return None
    return out


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
    gross_source: Optional[str] = None,
    mae_samples: Optional[int] = None,
) -> Dict[str, Any]:
    """Kapanış ANINDA bilinen her şeyi tek sözlükte topla.

    D27/A2 — `gross_source` ve `fee_estimate_source`. Komisyon tahmini
    `brüt − net`tir; brüt YANLIŞSA tahmin de yanlıştır. Ölçüldü (2026-08-24):
    22 işlemin 8'inde tahmin teorik komisyonun 2 katından fazla, **5'inde
    NEGATİF** çıkmıştı — çünkü merdiven (TP1/TP2/runner) üç ayrı fiyattan
    dolarken brüt TEK fiyatla hesaplanıyordu. Artık:

      * brüt ölçülemediyse (`gross_pnl=None`) `fee_estimate` de `None`'dır ve
        `fee_estimate_source="unmeasured"` der — **uydurma sayı YASAK**;
      * brüt−net NEGATİF çıkarsa (fiziksel olarak imkânsız komisyon) değer
        yine `None` bırakılır ve kaynak `"inconsistent"` olur. Sessizce
        negatif bir "komisyon" yazmak, `fee_dominated` etiketini geçersiz
        kılan asıl kusurdu.

    ⚠️ ADLANDIRMA SINIRI: `fee_estimate` = brüt − net'tir ve `pnl_source`
    `"binance_income_net"` iken net, `exits.NET_INCOME_TYPES` gereği
    **FUNDING_FEE'yi de** içerir; `gross_pnl` ise Σ`realizedPnl`dir (funding
    HARİÇ). Yani bu alan "komisyon" değil **"komisyon + funding"**dir. Uzun
    tutulan pozisyonlarda `fee_dominated` eşiği bu yüzden hafifçe kayabilir.
    Ayrıştırmak ayrı bir income kırılımı ister; D27 kapsamında YAPILMADI.

    D27/A3 — MAE YOKLAMA KUSURU. `mae_roi_pct` safety turunda ÖRNEKLENİR
    (≈2 sn); iki yoklama arasındaki fitil görülmez. Fiziksel kelepçe şudur:
    **çıkış fiyatı fiilen DOKUNULMUŞ bir fiyattır**, dolayısıyla en kötü uç
    en az çıkış kadar kötü olmalıdır — yani `mae_roi_pct <= çıkış ROI'si`
    (fiyat tabanlı, komisyon HARİÇ: `price_move_pct × kaldıraç`). Komisyon
    dahil net ROI ile kıyaslamak yanlış-pozitif üretirdi (başabaşa yakın bir
    kapanış komisyon yüzünden eksi görünür ama fiyat hiç aleyhe gitmemiş
    olabilir). İhlalde MAE çıkış ROI'siyle DÜZELTİLİR ve
    `mae_source="corrected"` yazılır; ham örneklem `mae_roi_pct_sampled`
    alanında AYNEN durur — **sessiz düzeltme YOKTUR**.
    """
    lev = int(leverage or 0) or 1
    net = _f(realized_pnl)
    gross = _f(gross_pnl)
    fee_estimate = None
    fee_estimate_source = "unmeasured"
    if net is not None and gross is not None:
        raw_fee = round(gross - net, 6)
        if raw_fee >= 0:
            fee_estimate = raw_fee
            fee_estimate_source = _s(gross_source) or "unknown"
        else:
            # Negatif komisyon fiziksel olarak imkânsız: brüt ya da net
            # ölçümü tutarsız. Sayı YAZILMAZ.
            fee_estimate_source = "inconsistent"

    mfe = _f(mfe_roi_pct)
    move_pct = _pct_move(direction, entry_price, exit_price)
    mae_sampled = _f(mae_roi_pct)
    mae, mae_source = reconcile_mae(
        mae_roi_pct=mae_sampled, price_move_pct=move_pct, leverage=lev
    )
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
        "gross_source": _s(gross_source),
        "fee_estimate": fee_estimate,
        "fee_estimate_source": fee_estimate_source,
        "pnl_source": _s(pnl_source),
        "mae_roi_pct": _round(mae, 3),
        "mae_roi_pct_sampled": _round(mae_sampled, 3),
        "mae_source": mae_source,
        "mae_samples": None if mae_samples is None else max(0, int(mae_samples)),
        "mfe_roi_pct": _round(mfe, 3),
        "mae_price_pct": None if mae is None else round(mae / lev, 4),
        "mfe_price_pct": None if mfe is None else round(mfe / lev, 4),
        "price_move_pct": move_pct,
        "duration_sec": _round(duration_sec, 1),
        "path": dict(path or {}),
        "leader_day_drift_pct": _round(leader_day_drift_pct, 4),
        "regime": _s(regime),
        "btc_price": _round(btc_price, 8),
        "verification_notes": list(verification_notes or []),
    }


#: D27/A3 MAE kaynakları.
MAE_SOURCE_SAMPLED = "sampled"        # yoklamanın gördüğü değer geçerli
MAE_SOURCE_CORRECTED = "corrected"    # yoklama fiziksel kelepçeyi ihlal etti
MAE_SOURCE_UNMEASURED = "unmeasured"  # hiç ölçülemedi (veri yok)


def reconcile_mae(
    *,
    mae_roi_pct: Optional[float],
    price_move_pct: Optional[float],
    leverage: Optional[int],
) -> tuple:
    """MAE'yi fiziksel kelepçeyle uzlaştır — SAF. `(değer, kaynak)` döner.

    Kelepçe: çıkış fiyatına FİİLEN dokunuldu, dolayısıyla en kötü uç
    (`mae_roi_pct`) çıkış ROI'sinden (`price_move_pct × kaldıraç`) daha iyi
    OLAMAZ. İhlal, örneklemenin (safety turu ≈2 sn) fitili kaçırdığını
    gösterir — ölçüldü: 6 stop-out'ta MAE fiziksel olarak imkânsızdı.

    Kıyas FİYAT tabanlıdır (komisyon HARİÇ): net PnL ile kıyaslamak,
    komisyon yüzünden eksiye düşen başabaş kapanışlarda yanlış-pozitif
    üretirdi.

    Düzeltme sessiz DEĞİLDİR: çağıran ham örneklemi ayrı alanda saklar ve
    kaynak `corrected` olur.
    """
    if mae_roi_pct is None:
        return None, MAE_SOURCE_UNMEASURED
    if price_move_pct is None:
        return mae_roi_pct, MAE_SOURCE_SAMPLED
    lev = int(leverage or 0) or 1
    exit_roi = price_move_pct * lev
    if mae_roi_pct > exit_roi:
        return exit_roi, MAE_SOURCE_CORRECTED
    return mae_roi_pct, MAE_SOURCE_SAMPLED


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
    # D27/A2: brüt ÖLÇÜLEMEDİYSE (merdiven çıkışı + ledger yok) ya da
    # brüt−net negatif çıktıysa `fee_estimate` `None`'dır — etiket ATILMAZ.
    # Eskiden tek çıkış fiyatıyla hesaplanan yanlış brüt bu etiketi 22
    # işlemin 8'inde geçersiz kılıyordu; "ölçemedik" demek yanlış etiketten
    # iyidir.
    fee_measured = exit_.get("fee_estimate") is not None
    if fee_measured and net is not None and gross is not None and gross > 0:
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
    # D22: `TRAIL_MARKET`/`BE_MARKET` TRAIL ailesindendir (stop kararının
    # piyasa emriyle uygulanmış hâli) — "stop yedi" sayılmaz; kayıplı olup
    # olmadığına net PnL karar verir, tıpkı TRAIL gibi.
    losing = exit_reason_family(reason) == "SL" or (net is not None and net < 0)
    if losing and returned_at is not None:
        out["tags"] = [TAG_NOISE_STOP]
    return out


# --------------------------------------------------------------------------
# Özet (etiket × sonuç)
# --------------------------------------------------------------------------

#: `summarize` içindeki `by_model_version` kova üst sınırı — en çok bu kadar
#: GERÇEK sürüm kovası tutulur, fazlası `OTHER_BUCKET`ta toplanır (yani sözlük
#: en çok N+1 anahtar taşır). Sürüm etiketi serbest metindir; sınırsız kova
#: sınırsız RAM/JSON demektir.
MODEL_VERSION_BUCKET_MAX = 20

#: Üst sınırı aşan sürümlerin toplandığı kova.
OTHER_BUCKET = "_diger_"

#: D27/A1: `summarize` içindeki çıkış nedeni kova üst sınırı. Etiket koddan
#: gelir (SL/REAPER/TP_LADDER/TRAIL/…) ama bir yazım hatası ya da ileride
#: eklenecek bir etiket sınırsız kova büyütmemeli.
EXIT_REASON_BUCKET_MAX = 20


def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Etiket × sonuç tablosu — "neler etkiliyor" sorusunun cevabı.

    `rows`: `{"tags": [...], "pnl": float}` sözlükleri. Bir işlem birden çok
    etiket taşıyabilir; her etiket kendi satırında sayılır (satırların toplamı
    işlem sayısını AŞABİLİR — bu bir hata değil, kasıtlıdır).
    `_etiketsiz_` satırı hiç etiket almamış işlemleri gösterir: kıyas tabanı
    olmadan "şu etiket kötü" demek anlamsızdır.

    D24/madde 6.3: satırlar OPSİYONEL bir `"expectation"` anahtarı taşıyabilir
    (bkz. `expectation_from_entry`). Dönen sözlükteki `"expectation"` bloğu
    HER ZAMAN bulunur — hiç beklenti kaydı yoksa bile — ki raporun şekli
    sabit kalsın ve pano "alan yok" hâlini ayrıca ele almak zorunda kalmasın.

    `without_expectation` sayacının anlamı **ÖLÇÜLMEDİ**'dir: o işlemde bir
    beklenti kaydı YOKTU demek, "o işleme girerken beklenti kurulmamıştı"
    demek DEĞİLDİR (kanal kapalı olabilir, kayıt sonradan eklenmiş olabilir).
    Bu ayrım korunmazsa ölçüm yokluğu sessizce bulguya dönüşür.
    """
    buckets: Dict[str, Dict[str, float]] = {}
    exit_buckets: Dict[str, Dict[str, float]] = {}
    total_trades = 0
    total_pnl = 0.0
    with_expectation = 0
    by_model_version: Dict[str, int] = {}

    def _bucket(name: str) -> Dict[str, float]:
        return buckets.setdefault(
            name,
            {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
             "gross_win": 0.0, "gross_loss": 0.0},
        )

    def _exit_bucket(name: str) -> Dict[str, float]:
        return exit_buckets.setdefault(
            name,
            {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
             "gross_win": 0.0, "gross_loss": 0.0},
        )

    for row in rows or []:
        pnl = _f(row.get("pnl")) or 0.0
        tags = [t for t in (row.get("tags") or []) if t]
        total_trades += 1
        total_pnl += pnl
        # D27/A1: çıkış nedeni × sonuç. `REAPER` (8 saatlik yaş kesmesi) artık
        # `SL`den AYRI görünür — eskiden ikisi tek kovadaydı ve brüt zararın
        # %27'si yanlış etiketliydi. Kova sayısı SINIRLI: etiket koddan gelir
        # ama bir yazım hatası sınırsız kova büyütmemeli.
        reason = _s(row.get("exit_reason")) or "_bilinmiyor_"
        reason = reason.upper() if reason != "_bilinmiyor_" else reason
        if (
            reason not in exit_buckets
            and len(exit_buckets) >= EXIT_REASON_BUCKET_MAX
        ):
            reason = OTHER_BUCKET
        exit_bucket = _exit_bucket(reason)
        exit_bucket["trades"] += 1
        exit_bucket["pnl"] += pnl
        if pnl > 0:
            exit_bucket["wins"] += 1
            exit_bucket["gross_win"] += pnl
        elif pnl < 0:
            exit_bucket["losses"] += 1
            exit_bucket["gross_loss"] += abs(pnl)
        expectation = row.get("expectation")
        if isinstance(expectation, dict) and any(
            expectation.get(field) is not None for field in EXPECTATION_FIELDS
        ):
            with_expectation += 1
            version = _s(expectation.get("model_version")) or "_bilinmiyor_"
            if (
                version not in by_model_version
                and len(by_model_version) >= MODEL_VERSION_BUCKET_MAX
            ):
                version = OTHER_BUCKET
            by_model_version[version] = by_model_version.get(version, 0) + 1
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

    exit_table: List[Dict[str, Any]] = []
    for name, bucket in exit_buckets.items():
        trades = int(bucket["trades"])
        wins = int(bucket["wins"])
        gross_loss = bucket["gross_loss"]
        exit_table.append({
            "reason": name,
            "family": exit_reason_family(name) if name.isupper() else name,
            "trades": trades,
            "wins": wins,
            "losses": int(bucket["losses"]),
            "winrate": round(wins / trades * 100.0, 1) if trades else 0.0,
            "pnl": round(bucket["pnl"], 4),
            "avg_pnl": round(bucket["pnl"] / trades, 4) if trades else 0.0,
            "profit_factor": (
                round(bucket["gross_win"] / gross_loss, 3)
                if gross_loss > 0 else None
            ),
        })
    exit_table.sort(key=lambda row: (row["pnl"], -row["trades"]))

    return {
        "trades": total_trades,
        "total_pnl": round(total_pnl, 4),
        "tags": table,
        # D27/A1: çıkış nedeni × sonuç. `REAPER` ayrı satırdır; ama
        # **2026-08-24 ÖNCESİ** kapanan yaş kesmeleri defterde hâlâ "SL"dir
        # (geriye dönük veri düzeltmesi YAPILMADI) — pencereyi buna göre böl.
        "exit_reasons": exit_table,
        "expectation": {
            "with_expectation": with_expectation,
            # null = ÖLÇÜLMEDİ (beklenti kurulmamıştı DEĞİL).
            "without_expectation": total_trades - with_expectation,
            "by_model_version": dict(
                sorted(
                    by_model_version.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
        },
    }
