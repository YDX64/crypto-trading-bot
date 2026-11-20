"""Monte-Carlo permütasyon testi — "bu sonuç şanstan ayırt edilebilir mi?" (D24)

Bugün `backtest.compute_stats` TEK bir koşunun PF'sini/PnL'ini verir; "bu PF
şanstan ayırt edilebilir mi" sorusuna cevabımız YOKTUR. D18 gibi
ADAY/REDDEDİLDİ kararları göz kararı eşiklerle veriliyor. Bu modül o kararı
bir SAYIYA çevirir. **PnL iddiası YOKTUR** — katkı yalnız kanıt kalitesine.

Atıf / lisans (Apache-2.0) — ayrıntı repo kökündeki `NOTICE` dosyasında:
    investing-algorithm-framework (coding-kitties)
    commit 1db0df3a8c04ccf9b61b565ae2830a3fd4b77e73
      * infrastructure/services/backtesting/backtest_service.py:3333-3453
        `create_ohlcv_permutation` — log uzayında göreli hareket karıştırma
      * domain/backtesting/backtest_monte_carlo_test.py:114-152
        `compute_p_values` — permütasyon null dağılımından p-değeri

Paket pip ile KURULMAZ (vendor edilir): `app/app.py` paket import ANINDA
Flask'ı zorunlu kılıyor, `finterion-charts` sabit pinli ve `ccxt>=4.2.48`
bizim `ccxt==3.1.60` pinimizle çakışıyor. Gerçek parayla çalışan bir
konteynerde gereksiz tedarik zinciri yüzeyi.

UPSTREAM'DEN AYRILAN İKİ ZORUNLU DÜZELTME
-----------------------------------------
0) (C paketi) Artık BAR BÜTÜNÜ karıştırılır (aşağıdaki kelepçe metni TARİHÇEDİR:
   ortak şemada ihlal yapısal olarak 0'dır, kelepçe yalnız emniyettir).

1) HIGH/LOW KELEPÇESİ (`clamp=True`, varsayılan) — eski bağımsız şema.
   Upstream `rel_open`/`rel_high`/`rel_low`/`rel_close`'u BAĞIMSIZ
   karıştırır. `rel_high >= 0` ve `rel_low <= 0` her zaman doğru olduğundan
   permüte barda High >= Open ve Low <= Open korunur, ama Close bağımsız
   çekildiği için `rel_close > rel_high` olduğunda **Close > High**,
   `rel_close < rel_low` olduğunda **Close < Low** çıkar. Ölçüm: permüte
   barların yaklaşık beşte biri bu iki ihlalden birini taşıyor. Harness
   stop/hedef değmesini high/low ile belirlediğinden (`backtest._is_hit`)
   bu, null dağılımı TP/trail ALEYHİNE sistematik biçimde bozar →
   kelepçe ŞARTTIR:
       High = max(High, Open, Close),  Low = min(Low, Open, Close)
   Kelepçenin null dağılımını NE KADAR kaydırdığı ayrıca ölçülebilir:
   `backtest.run_permutation_study(..., clamp_audit=True)` hem kelepçeli hem
   kelepçesiz null'u AYNI tohumlarla üretir; `clamp_shift_report` farkı verir.

2) YÖN.
   Upstream `p = mean(dist >= real)` ile yönü SABİT varsayar. `max_drawdown`
   ve `annual_volatility` gibi metriklerde KÜÇÜK olan iyidir; orada bu yön
   TERSTİR ve p-değeri anlamsız çıkar. Burada her metrik için yön açıkça
   tanımlanır (`METRIC_DIRECTION`); yönü tanımlı OLMAYAN bir metrik için
   p-değeri ÜRETİLMEZ (sessizce yanlış sayı üretmektense hiç üretmemek).

Üçüncü, küçük ama önemli sapma: p-değeri `(b+1)/(m+1)` ile hesaplanır
(Phipson & Smyth 2010). Sonlu permütasyonla p'nin TAM 0 çıkması istatistiksel
olarak imkânsızdır; upstream'in düz `mean()`'i 0 üretebiliyor ve bu okuyanda
"sıfır olasılık" yanılsaması yaratıyor. Ham oran `p_raw` alanında verilir.

NULL'UN KAPSAMI (dürüstlük notu — yorumlarken bunu oku)
------------------------------------------------------
Bu modül TEK bir seriyi permüte eder. Çağıran (`backtest.run_permutation_study`)
GİRİŞ dilimini (5m) permüte eder ve bağlam/rejim dilimlerini permüte seriden
TÜRETİR (`aggregate_from`); türetilemeyen (permüte serinin kapsamadığı) daha
eski rejim barları GERÇEK kalır. Yani üretilen null **koşulludur**:
"rejim arka planı aynıyken, giriş dilimindeki sinyalin kendisi şanstan ayırt
edilebilir mi?" Koşulsuz bir null DEĞİLDİR ve p-değeri o dar soruyu yanıtlar.

Bu modül SAFTIR: IO yok, saat okuma yok, global durum yok. Yalnız `numpy`
(requirements.txt'te zaten var) ve bu deponun `Candle` tipini kullanır.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from src.strategies.scalper.types import Candle

#: Metrik yönü: büyük olan mı iyi, küçük olan mı?
HIGHER_IS_BETTER = "higher"
LOWER_IS_BETTER = "lower"

#: Yönü BİLİNEN metrikler. Burada olmayan bir metrik için p-değeri
#: ÜRETİLMEZ (`compute_p_values` onu `skipped` listesine koyar) — yönü
#: bilinmeyen bir metriğe tek yönlü test uygulamak sessizce yanlış sayı
#: üretir; upstream'in düştüğü hata tam olarak budur.
METRIC_DIRECTION: Dict[str, str] = {
    "total_pnl": HIGHER_IS_BETTER,
    "profit_factor": HIGHER_IS_BETTER,
    "winrate": HIGHER_IS_BETTER,
    "avg_roi": HIGHER_IS_BETTER,
    "avg_mfe": HIGHER_IS_BETTER,
    # avg_mae NEGATİF bir sayıdır (girişten en kötü sapma); sıfıra yakın
    # olan iyidir → yön "büyük olan iyi".
    "avg_mae": HIGHER_IS_BETTER,
    "max_drawdown": LOWER_IS_BETTER,
    "bar_max_drawdown": LOWER_IS_BETTER,
    "max_consec_losses": LOWER_IS_BETTER,
}

#: `--permutations` ile varsayılan olarak test edilen metrikler.
DEFAULT_METRICS: Tuple[str, ...] = (
    "total_pnl", "profit_factor", "winrate", "max_drawdown", "bar_max_drawdown",
)


def direction_of(metric: str) -> Optional[str]:
    """Metriğin yönü; bilinmiyorsa None."""
    return METRIC_DIRECTION.get(str(metric))


# ==========================================================================
# Permütasyon — BAR BÜTÜNÜ (ortak) karıştırma, log uzayında
# ==========================================================================

@dataclass(frozen=True)
class PermutationIndex:
    """Bir permütasyonun çizimi: `gap` boşluk (open − önceki close) dizisinin,
    `shape` bar şekli (h−o, l−o, c−o) üçlülerinin sırasıdır. Aynı nesne birden
    çok sembole / lider seriye uygulanabilir (`apply_permutation_index`)."""
    gap: np.ndarray
    shape: np.ndarray
    block_length: int = 1

    @property
    def length(self) -> int:
        return int(len(self.shape))


def _block_permutation(rng: np.random.Generator, n: int, block_length: int) -> np.ndarray:
    """0..n-1'in blok-permütasyonu: ardışık `block_length` barlık bloklar
    (sonuncusu kısa olabilir) rastgele sırayla dizilir, blok İÇİ sıra korunur.
    block_length=1 → düz permütasyon."""
    if block_length <= 1:
        return rng.permutation(n)
    starts = list(range(0, n, block_length))
    order = rng.permutation(len(starts))
    parts = [np.arange(starts[k], min(starts[k] + block_length, n)) for k in order]
    return np.concatenate(parts) if parts else np.arange(0)


def draw_permutation_index(
    n: int, *, seed: Optional[int] = None, block_length: int = 1,
) -> PermutationIndex:
    """`n` barlık permüte pencere için TEK permütasyon çiz (gap + shape).

    Aynı (n, seed, block_length) → aynı indeks. Bir sürücü bunu birden çok
    sembole ve lider seriye uygulayarak semboller arası ilişkiyi korur.
    """
    if block_length < 1:
        raise ValueError("block_length >= 1 olmalı")
    if n < 0:
        raise ValueError("n >= 0 olmalı")
    rng = np.random.default_rng(seed)
    gap = _block_permutation(rng, n, int(block_length))
    shape = _block_permutation(rng, n, int(block_length))
    return PermutationIndex(gap=gap, shape=shape, block_length=int(block_length))


def _empty_stats(clamp: bool) -> Dict[str, Any]:
    return {
        "bars": 0, "permuted_bars": 0, "clamp_applied": bool(clamp),
        "high_violations": 0, "low_violations": 0,
        "high_violation_pct": 0.0, "low_violation_pct": 0.0,
        "violated_bars": 0, "violated_bar_pct": 0.0,
        "mean_abs_adjust_pct": 0.0, "max_abs_adjust_pct": 0.0,
    }


def _apply_index(
    candles: Sequence[Candle], index: PermutationIndex, start_index: int, clamp: bool,
) -> Tuple[List[Candle], Dict[str, Any]]:
    n = len(candles)
    stats = _empty_stats(clamp)
    if n == 0:
        return [], stats
    if start_index < 0:
        raise ValueError("start_index >= 0 olmalı")
    if start_index >= n - 1:
        stats["bars"] = n
        return list(candles), stats

    perm_index = start_index + 1
    perm_n = n - perm_index
    if index.length != perm_n or len(index.gap) != perm_n:
        raise ValueError(
            f"permütasyon indeksi uzunluk uyuşmazlığı: indeks {index.length}, "
            f"seri permüte pencere {perm_n} bar"
        )

    opens = np.asarray([c.open for c in candles], dtype=float)
    highs = np.asarray([c.high for c in candles], dtype=float)
    lows = np.asarray([c.low for c in candles], dtype=float)
    closes = np.asarray([c.close for c in candles], dtype=float)
    if not (
        np.all(opens > 0) and np.all(highs > 0)
        and np.all(lows > 0) and np.all(closes > 0)
    ):
        raise ValueError(
            "Permütasyon pozitif OHLC gerektirir (log dönüşümü); "
            "sıfır/negatif fiyat içeren seri verildi"
        )

    log_open = np.log(opens)
    log_high = np.log(highs)
    log_low = np.log(lows)
    log_close = np.log(closes)

    gaps = (log_open[1:] - log_close[:-1])[start_index:]       # perm_n
    rel_high = (log_high - log_open)[perm_index:]
    rel_low = (log_low - log_open)[perm_index:]
    rel_close = (log_close - log_open)[perm_index:]

    slice_gap = gaps[index.gap]
    slice_high = rel_high[index.shape]
    slice_low = rel_low[index.shape]
    slice_close = rel_close[index.shape]

    out: List[Candle] = list(candles[:perm_index])
    prev_close_log = log_close[start_index]

    high_violations = 0
    low_violations = 0
    violated_bars = 0
    adjust_pcts: List[float] = []

    for k in range(perm_n):
        src = candles[perm_index + k]
        o = prev_close_log + slice_gap[k]
        h = o + slice_high[k]
        low = o + slice_low[k]
        c = o + slice_close[k]

        # İhlal sayımı: ortak şemada h>=max(o,c), l<=min(o,c) yapısaldır
        # (üçlü bir bardan gelir) → sayaç 0 kalmalı; kontrol emniyet için.
        need_high = max(o, c) > h
        need_low = min(o, c) < low
        if need_high:
            high_violations += 1
        if need_low:
            low_violations += 1
        if need_high or need_low:
            violated_bars += 1

        if clamp:
            new_h = max(h, o, c)
            new_low = min(low, o, c)
            if need_high:
                adjust_pcts.append(abs(math.expm1(new_h - h)) * 100.0)
            if need_low:
                adjust_pcts.append(abs(math.expm1(new_low - low)) * 100.0)
            h, low = new_h, new_low

        prev_close_log = c
        out.append(Candle(
            open_time=src.open_time,
            open=math.exp(o),
            high=math.exp(h),
            low=math.exp(low),
            close=math.exp(c),
            volume=src.volume,
            close_time=src.close_time,
        ))

    stats.update({
        "bars": n,
        "permuted_bars": perm_n,
        "high_violations": high_violations,
        "low_violations": low_violations,
        "high_violation_pct": round(high_violations / perm_n * 100.0, 3),
        "low_violation_pct": round(low_violations / perm_n * 100.0, 3),
        "violated_bars": violated_bars,
        "violated_bar_pct": round(violated_bars / perm_n * 100.0, 3),
        "mean_abs_adjust_pct": (
            round(float(np.mean(adjust_pcts)), 6) if adjust_pcts else 0.0
        ),
        "max_abs_adjust_pct": (
            round(float(np.max(adjust_pcts)), 6) if adjust_pcts else 0.0
        ),
    })
    return out, stats


def apply_permutation_index(
    candles: Sequence[Candle], index: PermutationIndex, *, start_index: int = 0,
) -> List[Candle]:
    """Önceden çizilmiş `index`'i mum serisine uygula (permüte mumlar döner).
    Permüte pencere uzunluğu (`len(candles) - start_index - 1`) indeksinkiyle
    aynı değilse `ValueError`."""
    out, _ = _apply_index(candles, index, start_index, clamp=True)
    return out


def permute_candles(
    candles: Sequence[Candle],
    *,
    start_index: int = 0,
    seed: Optional[int] = None,
    clamp: bool = True,
    block_length: int = 1,
) -> Tuple[List[Candle], Dict[str, Any]]:
    """OHLC'yi log uzayında BAR BÜTÜNÜ olarak karıştır (standart şema).

    Her permüte bar kendi (high−open, low−open, close−open) üçlüsünü taşır;
    boşluklar (open − önceki close) AYRI karıştırılır. Bar aralığı ortalaması
    gerçek seriyle aynıdır (log uzayında birebir) ve High>=max(O,C),
    Low<=min(O,C) yapısaldır → kelepçe ihlali 0 (eski bağımsız-OHLC
    karıştırmada aralıklar ~%25 şişiyordu).

    `block_length` (bar):
      * 1 (varsayılan): bağımsız-bar null'u. H0: "getiri/şekil/boşluklar
        zamanda değiş-tokuş edilebilir"; volatilite kümelenmesi YOK edilir.
      * >1: ardışık bloklar karıştırılır; blok İÇİ yapı (volatilite
        kümelenmesi, kısa vadeli otokorelasyon) korunur, yalnız bloklar arası
        ilişki yok edilir. H0: "blok-iç yapı aynı, bloklar arası zamanlama
        şans".

    `start_index` ve ÖNCESİ değişmez; zaman damgaları ve hacim korunur. Boşluk
    ve close−open toplamları korunduğundan SON kapanış orijinalle aynıdır.

    Döner: (permüte mumlar, kelepçe istatistikleri). `clamp` yalnız emniyet:
    ortak şemada hiçbir şey düzeltmez.
    """
    n = len(candles)
    if block_length < 1:
        raise ValueError("block_length >= 1 olmalı")
    if n == 0 or start_index >= n - 1:
        return _apply_index(candles, PermutationIndex(
            gap=np.arange(0), shape=np.arange(0)), start_index, clamp)
    if start_index < 0:
        raise ValueError("start_index >= 0 olmalı")
    index = draw_permutation_index(n - start_index - 1, seed=seed,
                                   block_length=block_length)
    return _apply_index(candles, index, start_index, clamp)


def aggregate_from(source: Sequence[Candle], target: Sequence[Candle]) -> List[Candle]:
    """`target` barlarını, onları TAM olarak kapsayan `source` barlarından
    yeniden kur (5m → 15m/4h toplama).

    Kapsanmayan hedef bar AYNEN korunur. Böylece permüte edilmiş giriş
    dilimiyle bağlam/rejim dilimleri TUTARLI kalır (`setups.passes_equilibrium`
    5m fiyatını 15m dealing-range ortasıyla KARŞILAŞTIRIR — iki seri farklı
    dünyalardan gelirse bu kapı anlamsızlaşır), permüte serinin kapsamadığı
    daha eski rejim bağlamı ise gerçek veriyle kalır.

    SAF: girdi listeleri değişmez, yeni liste döner.
    """
    if not source or not target:
        return list(target)

    by_close: Dict[int, Candle] = {c.close_time: c for c in source}
    starts: Dict[int, Candle] = {c.open_time: c for c in source}
    src_sorted = sorted(source, key=lambda c: c.open_time)
    src_times = [c.open_time for c in src_sorted]

    out: List[Candle] = []
    for bar in target:
        first = starts.get(bar.open_time)
        last = by_close.get(bar.close_time)
        if first is None or last is None:
            out.append(bar)
            continue
        lo = bisect.bisect_left(src_times, bar.open_time)
        hi = bisect.bisect_right(src_times, last.open_time)
        chunk = src_sorted[lo:hi]
        if (
            not chunk
            or chunk[0].open_time != bar.open_time
            or chunk[-1].close_time != bar.close_time
        ):
            out.append(bar)
            continue
        out.append(Candle(
            open_time=bar.open_time,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
            volume=sum(c.volume for c in chunk),
            close_time=bar.close_time,
        ))
    return out


# ==========================================================================
# p-değeri — metrik BAŞINA yön
# ==========================================================================

def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


#: Sıfır işlemli null koşuda TANIMSIZ olan metrikler (compute_stats sıfır
#: döndürür; bu 0 "kötü sonuç" değil "ölçülmedi"dir) → bu null'lardan dışlanır.
#: PnL / drawdown null'larında 0 gerçek değerdir ve KALIR.
UNDEFINED_ON_ZERO_TRADES = frozenset({
    "winrate", "profit_factor", "avg_roi", "avg_mfe", "avg_mae",
})
TRADE_COUNT_KEY = "trades"
PRIMARY_METRIC = "total_pnl"
MULTIPLICITY_NOTE = (
    "Metrik başına p-değerleri çoklu teste göre düzeltilmemiş; birincil "
    "metrik total_pnl'dir, diğerleri bilgi içindir. Tarama/çok-varyant "
    "seçimi için multitest.py (Benjamini-Hochberg) kullanılmalı."
)


def _num(value: Any) -> Optional[float]:
    """float'a çevir; NaN/None/çevrilemeyen → None. +/-inf KORUNUR."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def compute_p_values(
    real_metrics: Dict[str, Any],
    permuted_metrics: Sequence[Dict[str, Any]],
    metrics: Optional[Iterable[str]] = None,
    *,
    seed: Optional[int] = None,
    block_length: int = 1,
) -> Dict[str, Any]:
    """Permütasyon null dağılımından metrik başına p-değeri.

    Yön `METRIC_DIRECTION`'dan gelir:
      * `higher`: p = P(null >= gerçek) — "bu kadar İYİ bir sonuç şansla ne
        sıklıkla çıkar?"
      * `lower` : p = P(null <= gerçek)

    p-değeri `(b+1)/(m+1)` (Phipson & Smyth 2010); ham oran `p_raw`.

    Sonsuz null değerler ATILMAZ: hiç kayıpsız null koşunun
    `profit_factor = inf`'i en iyi null sonuçtur ve `>= gerçek` sayılır
    (lower-is-better metrikte +inf en kötüdür, `<= gerçek` olmaz). Yalnız
    NaN/None atılır. Gerçek değer NaN/-inf ise p-değeri üretilmez; higher-is-better
    metrikte gerçek +inf geçerlidir (p tanımlı).

    Sıfır işlemli null koşular (`trades == 0`, satırda `trades` anahtarı
    varsa) winrate/PF/avg_* null'larından DIŞLANIR (tanımsız), PnL ve
    drawdown null'larında 0 olarak KALIR. Oranları sonuçta
    `zero_trade_null_fraction` verir.

    Sonuç ayrıca `permutations`, `seed`, `block_length`,
    `infinite_pf_nulls`, `primary_metric` ve çoklu-test notunu taşır.
    """
    wanted = list(metrics) if metrics is not None else list(DEFAULT_METRICS)
    out: Dict[str, Any] = {}
    skipped: List[Dict[str, str]] = []

    m_total = len(permuted_metrics)
    zero_rows = sum(
        1 for m in permuted_metrics
        if TRADE_COUNT_KEY in m and _num(m.get(TRADE_COUNT_KEY)) == 0.0
    )
    inf_pf = sum(
        1 for m in permuted_metrics
        if (v := _num(m.get("profit_factor"))) is not None and v == math.inf
    )

    for metric in wanted:
        direction = direction_of(metric)
        if direction is None:
            skipped.append({"metric": metric, "reason": "yön tanımlı değil"})
            continue
        if metric not in real_metrics:
            skipped.append({"metric": metric, "reason": "gerçek koşuda yok"})
            continue

        real = _finite(real_metrics.get(metric))
        if real is None and direction == HIGHER_IS_BETTER:
            # +inf (ör. hiç kayıpsız PF) iyi yönde tanımlı bir sonuçtur; p üretilir.
            raw_real = _num(real_metrics.get(metric))
            if raw_real == math.inf:
                real = raw_real
        undefined_on_zero = metric in UNDEFINED_ON_ZERO_TRADES
        raw_dist: List[Any] = []
        zero_excluded = 0
        for m in permuted_metrics:
            if metric not in m:
                continue
            if (undefined_on_zero and TRADE_COUNT_KEY in m
                    and _num(m.get(TRADE_COUNT_KEY)) == 0.0):
                zero_excluded += 1
                continue
            raw_dist.append(m.get(metric))
        dist = [v for v in (_num(x) for x in raw_dist) if v is not None]
        dropped = len(raw_dist) - len(dist)

        row: Dict[str, Any] = {
            "metric": metric,
            "direction": direction,
            "real": None if real is None else round(real, 6),
            "n": len(dist),
            "non_finite_dropped": dropped,
            "zero_trade_excluded": zero_excluded,
        }
        if not dist:
            row["note"] = "null dağılımı boş (sonlu değer yok)"
            out[metric] = row
            continue

        arr = np.asarray(dist, dtype=float)
        fin = arr[np.isfinite(arr)]
        row["infinite_in_null"] = int(len(arr) - len(fin))
        if len(fin):
            row.update({
                "null_mean": round(float(np.mean(fin)), 6),
                "null_median": round(float(np.median(fin)), 6),
                "null_p05": round(float(np.percentile(fin, 5)), 6),
                "null_p95": round(float(np.percentile(fin, 95)), 6),
                "null_min": round(float(np.min(fin)), 6),
                "null_max": round(float(np.max(fin)), 6),
            })
        if real is None:
            row["note"] = (
                "gerçek değer sonlu değil (ör. profit_factor=inf) — p-değeri "
                "üretilmedi"
            )
            out[metric] = row
            continue

        if direction == HIGHER_IS_BETTER:
            at_least_as_extreme = int(np.sum(arr >= real))
        else:
            at_least_as_extreme = int(np.sum(arr <= real))
        n = len(dist)
        row["count_at_least_as_extreme"] = at_least_as_extreme
        row["p_raw"] = round(at_least_as_extreme / n, 6)
        row["p_value"] = round((at_least_as_extreme + 1) / (n + 1), 6)
        out[metric] = row

    return {
        "metrics": out,
        "skipped": skipped,
        "permutations": m_total,
        "seed": seed,
        "block_length": int(block_length),
        "infinite_pf_nulls": inf_pf,
        "zero_trade_null_fraction": (zero_rows / m_total) if m_total else 0.0,
        "zero_trade_note": (
            "sıfır işlemli null koşular winrate/PF/avg_* null'larından "
            "dışlandı (tanımsız); PnL ve drawdown null'larında 0 olarak kaldı"
        ),
        "primary_metric": PRIMARY_METRIC,
        "multiplicity_note": MULTIPLICITY_NOTE,
    }


def clamp_shift_report(
    clamped: Dict[str, Any], unclamped: Dict[str, Any]
) -> Dict[str, Any]:
    """Kelepçenin null dağılımını NE KADAR kaydırdığını raporla.

    Girdiler `compute_p_values` çıktılarıdır (AYNI tohumlarla üretilmiş iki
    null). Her metrik için null ortalamasındaki ve p-değerindeki farkı verir.
    Bu tablo, kelepçenin "kozmetik bir düzeltme" değil ÖLÇÜLEBİLİR bir
    düzeltme olduğunu (ya da olmadığını) gösterir.
    """
    rows: List[Dict[str, Any]] = []
    for metric, clamped_row in (clamped.get("metrics") or {}).items():
        raw_row = (unclamped.get("metrics") or {}).get(metric)
        if not raw_row:
            continue

        def _delta(key: str) -> Optional[float]:
            a, b = clamped_row.get(key), raw_row.get(key)
            if a is None or b is None:
                return None
            return round(float(a) - float(b), 6)

        rows.append({
            "metric": metric,
            "direction": clamped_row.get("direction"),
            "null_mean_clamped": clamped_row.get("null_mean"),
            "null_mean_unclamped": raw_row.get("null_mean"),
            "null_mean_delta": _delta("null_mean"),
            "p_value_clamped": clamped_row.get("p_value"),
            "p_value_unclamped": raw_row.get("p_value"),
            "p_value_delta": _delta("p_value"),
        })
    rows.sort(key=lambda r: r["metric"])
    return {"rows": rows}


def merge_clamp_stats(all_stats: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Sembol × permütasyon başına üretilen kelepçe istatistiklerini topla."""
    total_bars = 0
    high = 0
    low = 0
    violated = 0
    weighted_mean = 0.0
    max_adjust = 0.0
    runs = 0
    for stats in all_stats:
        bars = int(stats.get("permuted_bars") or 0)
        if bars <= 0:
            continue
        runs += 1
        total_bars += bars
        high += int(stats.get("high_violations") or 0)
        low += int(stats.get("low_violations") or 0)
        violated += int(stats.get("violated_bars") or 0)
        weighted_mean += float(stats.get("mean_abs_adjust_pct") or 0.0) * bars
        max_adjust = max(max_adjust, float(stats.get("max_abs_adjust_pct") or 0.0))
    if total_bars == 0:
        return {
            "runs": runs, "permuted_bars": 0, "high_violations": 0,
            "low_violations": 0, "violated_bars": 0,
            "high_violation_pct": 0.0, "low_violation_pct": 0.0,
            "violated_bar_pct": 0.0, "mean_abs_adjust_pct": 0.0,
            "max_abs_adjust_pct": 0.0,
        }
    return {
        "runs": runs,
        "permuted_bars": total_bars,
        "high_violations": high,
        "low_violations": low,
        "violated_bars": violated,
        "high_violation_pct": round(high / total_bars * 100.0, 3),
        "low_violation_pct": round(low / total_bars * 100.0, 3),
        "violated_bar_pct": round(violated / total_bars * 100.0, 3),
        "mean_abs_adjust_pct": round(weighted_mean / total_bars, 6),
        "max_abs_adjust_pct": round(max_adjust, 6),
    }
