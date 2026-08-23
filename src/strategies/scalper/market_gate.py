"""
Lider piyasa kapısı ("ters-gün kapısı") — SAF fonksiyonlar, IO YOK.

Tasarım: docs/superpowers/specs/2026-08-22-reversal-day-loss-design.md §C.

Gerekçe (canlı defter 12–22 Ağu, `scripts/ledger_report.py`): ödeme
asimetrisi başabaş kazanma oranını ≈%81.5'e çıkarıyor; defterin DOWN
günlerindeki kaybı (−128, 15 işlem) 22 Ağu'daki 4 LONG SL'de yoğunlaşıyor.
O dört kaybın ikisi BTC'nin gün-açılışına göre −%1.33/−%1.68 sapmasıyla,
ikisi de 3 günlük +%21'lik uzamayla işaretlenebiliyordu. Bu modül o iki
gözlemi iki BAĞIMSIZ alt-kapıya çevirir:

  * gün-içi ("day"):  lider gün açılışının ≥X% ALTINDAYSA yeni LONG yok,
                      ≥X% ÜSTÜNDEYSE yeni SHORT yok.
  * uzama ("run"):    lider son N tamamlanmış günde ≥+Y% koştuysa LONG yok,
                      ≤−Y% düştüyse SHORT yok.

Her alt-kapı ayrı ayrı kapatılabilir (ilgili yüzde = 0 → o alt-kapı yok).
Mevcut rejim kapısıyla (D5) KARIŞTIRILMAMALI: rejim kapısı sembolün KENDİ
EMA50/200 trendine bakar, bu kapı yalnız LİDER sembole (varsayılan BTCUSDT)
bakar ve tüm evrene aynı kararı uygular.

**Parite (CLAUDE.md kural 2):** bu modülü hem canlı motor
(`engine._market_gate_reason`) hem backtest harness'ı
(`backtest.simulate_symbol`) çağırır. Girdi türetme de ortak: "gün açılışı"
her iki tarafta da `day_open_from_daily_closes` ile SON TAMAMLANMIŞ GÜNLÜK
KAPANIŞ'tan türetilir (bkz. o fonksiyonun docstring'i — canlıda oluşmakta
olan günlük mum `KlineFetcher._drop_unclosed` tarafından zaten atıldığı
için "bugünün open'ı" canlıda ELDE EDİLEMEZ; 7/24 açık bir piyasada
önceki kapanış ile açılış aynı tik mertebesindedir).

Veri eksikse kapı UYGULANMAZ (fail-open): lider verisinin gelmemesi bir
risk OLAYI değildir; giriş hattı mevcut davranışını korur ve çağıran katman
WARNING loglar (spec §C: "fail-closed DEĞİL").
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

# missed_counter / log anahtarları — harness ve motor AYNI dizeleri kullanır.
REASON_DAY = "market_gate_day"
REASON_RUN = "market_gate_run"


def _as_float(value: Any) -> Optional[float]:
    """Sonlu bir float'a çevir; olmuyorsa None (sessiz 0.0 YOK)."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _cfg_float(cfg: Any, name: str, default: float) -> float:
    """cfg alanını float olarak oku; okunamazsa `default` (kapıyı sessizce
    açıp kapatan gizli davranış olmasın diye açık varsayılan)."""
    raw = getattr(cfg, name, default)
    out = _as_float(raw)
    return default if out is None else out


def _cfg_int(cfg: Any, name: str, default: int) -> int:
    raw = getattr(cfg, name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _direction_value(direction: Any) -> str:
    """Direction enum'u ya da düz dizeden 'LONG'/'SHORT' üret."""
    return str(getattr(direction, "value", direction)).strip().upper()


def day_open_from_daily_closes(daily_closes: Optional[Sequence[float]]) -> Optional[float]:
    """Gün açılışı vekili = SON TAMAMLANMIŞ günlük kapanış.

    Neden gerçek "bugünün open'ı" değil: `KlineFetcher._drop_unclosed` henüz
    kapanmamış mumu HER ZAMAN atar (repaint koruması), bu yüzden canlı motor
    oluşmakta olan GÜNLÜK mumu hiç göremez — "bugünün open'ı" canlıda
    türetilemez. Harness'ta türetilebilirdi, ama o zaman iki taraf FARKLI
    bir büyüklük hesaplar ve parite (CLAUDE.md kural 2) bozulurdu. 7/24 açık
    bir piyasada günlük mumun open'ı ile bir önceki mumun close'u arasındaki
    fark tik mertebesindedir (eşik %1); bu yüzden ortak, deterministik ve
    her iki tarafta AYNI olan vekil bilinçli olarak tercih edildi.

    Boş liste / sonlu-olmayan / ≤0 kapanış → None (kapı uygulanmaz).
    """
    if not daily_closes:
        return None
    last = _as_float(daily_closes[-1])
    if last is None or last <= 0.0:
        return None
    return last


def evaluate_market_gate(
    direction: Any,
    leader_day_open: Optional[float],
    leader_last_close: Optional[float],
    leader_daily_closes: Optional[Sequence[float]],
    cfg: Any,
) -> Optional[str]:
    """Lider piyasa kapısı: engellenirse NEDEN dizesi, serbestse None.

    Dönen değerler: `REASON_DAY` ("market_gate_day"), `REASON_RUN`
    ("market_gate_run") veya None.

    Argümanlar:
      * `direction` — `Direction` enum'u veya "LONG"/"SHORT" dizesi.
      * `leader_day_open` — lider gün açılışı (bkz. `day_open_from_daily_closes`).
      * `leader_last_close` — liderin giriş zaman dilimindeki SON KAPANMIŞ
        mumunun kapanışı (canlıda son kapanan mum; harness'ta o anki mumun
        kapanışı — ikisi de "karar anında bilinen son kapanış").
      * `leader_daily_closes` — liderin TAMAMLANMIŞ günlük kapanışları
        (eski→yeni). Uzama alt-kapısı `closes[-1] / closes[-1-N] - 1` kullanır,
        yani N günlük koşu için N+1 kapanış gerekir.
      * `cfg` — `SCALPER_MARKET_GATE*` alanlarını taşıyan ayar nesnesi.

    Veri yetersizliği ilgili alt-kapıyı ATLAR (fail-open, spec §C). Kapı
    kapalıysa (varsayılan) hiçbir hesap yapılmaz — sıcak yolda maliyet yok.
    """
    if not bool(getattr(cfg, "scalper_market_gate", False)):
        return None

    yon = _direction_value(direction)
    if yon not in ("LONG", "SHORT"):
        return None

    # --- 1) Gün-içi alt-kapısı -------------------------------------------
    day_pct = _cfg_float(cfg, "scalper_market_gate_day_pct", 0.0)
    if day_pct > 0.0:
        day_open = _as_float(leader_day_open)
        last_close = _as_float(leader_last_close)
        if day_open is not None and day_open > 0.0 and last_close is not None:
            drift_pct = (last_close / day_open - 1.0) * 100.0
            if yon == "LONG" and drift_pct <= -day_pct:
                return REASON_DAY
            if yon == "SHORT" and drift_pct >= day_pct:
                return REASON_DAY

    # --- 2) Uzama (çok-günlük koşu) alt-kapısı ---------------------------
    run_pct = _cfg_float(cfg, "scalper_market_gate_run_pct", 0.0)
    run_days = _cfg_int(cfg, "scalper_market_gate_run_days", 0)
    if run_pct > 0.0 and run_days >= 1 and leader_daily_closes:
        run = _run_pct_from_closes(leader_daily_closes, run_days)
        if run is not None:
            if yon == "LONG" and run >= run_pct:
                return REASON_RUN
            if yon == "SHORT" and run <= -run_pct:
                return REASON_RUN

    return None


def _run_pct_from_closes(
    daily_closes: Sequence[float], run_days: int
) -> Optional[float]:
    """(son kapanış / N gün önceki kapanış − 1) × 100; veri yetersizse None.

    N günlük koşu N+1 kapanış ister (`closes[-1]` ve `closes[-1-N]`).
    """
    if run_days < 1 or len(daily_closes) < run_days + 1:
        return None
    last = _as_float(daily_closes[-1])
    base = _as_float(daily_closes[-1 - run_days])
    if last is None or base is None or base <= 0.0:
        return None
    return (last / base - 1.0) * 100.0


def market_gate_metrics(
    leader_day_open: Optional[float],
    leader_last_close: Optional[float],
    leader_daily_closes: Optional[Sequence[float]],
    cfg: Any,
) -> Dict[str, Optional[float]]:
    """`/scalper/status` teşhisi: kapının ŞU ANDA gördüğü iki büyüklük.

    Kapı kapalıyken de hesaplanabilir (yalnız gözlem). Hesaplanamayan
    büyüklük None döner — 0.0 ile karıştırılmamalı.
    """
    day_open = _as_float(leader_day_open)
    last_close = _as_float(leader_last_close)
    day_drift: Optional[float] = None
    if day_open is not None and day_open > 0.0 and last_close is not None:
        day_drift = (last_close / day_open - 1.0) * 100.0

    run_days = _cfg_int(cfg, "scalper_market_gate_run_days", 0)
    run: Optional[float] = None
    if leader_daily_closes and run_days >= 1:
        run = _run_pct_from_closes(leader_daily_closes, run_days)

    return {"day_drift_pct": day_drift, "run_pct": run}
