"""Çoklu-karşılaştırma düzeltmesi — Benjamini-Hochberg FDR (D24).

NEDEN: `docs/EXPERIMENTS.md`'de E2…E9 arasında **35 varyant** tarandı. "N
varyant denedik, biri anlamlı çıktı" cümlesi düzeltilmeden YANLIŞ POZİTİF
üretir: bağımsız 20 varyantta %5 eşiğiyle beklenen sahte "anlamlı" sayısı 1'dir.
Benjamini-Hochberg (1995) adım-aşağı yordamı, ailedeki YANLIŞ KEŞİF ORANINI
(false discovery rate) kontrol eder ve her p-değerini bir q-değerine çevirir.

Atıf (kaynak kaydı — kod kopyalanmadı, bkz. repo kökündeki `NOTICE`):
    AI-Trader (HKUDS), commit d03ff6c056b32ced735adf7c19ed8175adb1c8df,
    `research/scripts/research_common.py:150` — `benjamini_hochberg`.
    O depoda **LICENSE DOSYASI YOKTUR** (README rozeti MIT diyor ama lisans
    metni yok); lisans metni olmadan MIT iddiası hukuken zayıftır. Bu yüzden
    burada kod kopyalanmamış, kamuya mal olmuş BH yordamı bağımsız olarak
    yeniden yazılmıştır. Atıf, fikrin ilk nerede görüldüğünün kaydıdır.

AYNI DOSYANIN GERİSİ BİLİNÇLİ OLARAK ALINMAMIŞTIR: oradaki `bootstrap_ci`
gerçek bir bootstrap değildir (rastgele örnekleme yerine sabit adımlı
`values[(offset + i*7) % n]`; gcd(7,n)=1 iken dizinin bir permütasyonu olduğu
için güven aralığı genişliği n=5,10,13,50,100 için TAM 0.000000 çıkar) ve
`analyze_experiments.py`'de DiD p-değeri sabit 1.0'dır. Kopyalanırsa ZARAR
verir.

DÜRÜSTLÜK ÇEKİNCESİ (okumadan uygulama): BH, testlerin bağımsız ya da pozitif
bağımlı (PRDS) olmasını varsayar. Bizim 35 varyantımız yüksek KORELASYONLUDUR
(aynı 8 sembol, aynı üç pencere, çoğu aynı stratejinin komşu parametreleri) →
ETKİN deneme sayısı muhtemelen 5-10'dur. Bu yüzden BH burada bir "geçti/kaldı"
hakemi değil, **kaba bir kırpma** olarak kullanılır: q-değeri, ham p-değerinden
DAHA DÜRÜST bir sayıdır, ama hâlâ iyimserdir. `effective_tests` alanıyla bu
çekince rapora da yazılır.

SAF: IO yok, saat okuma yok, global durum yok, yalnız stdlib.

CLI (çok-varyant taramasını dosyadan okuyup tablo basar):
    python3 -m src.strategies.scalper.multitest --json tarama.json --alpha 0.10
    echo '[{"name":"E9a","p_value":0.01}]' | python3 -m src.strategies.scalper.multitest
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: Varsayılan yanlış keşif oranı eşiği. 0.10, keşif (tarama) aşamasında
#: yaygın ve savunulabilir bir seçimdir; canlıya alma kararı zaten üç
#: pencerede ayrıca doğrulanır (CLAUDE.md "Yasaklar" #1).
DEFAULT_ALPHA = 0.10


def _p(value: Any) -> float:
    """p-değerini savunmalı oku: çözülemeyen/aralık dışı değer 1.0 sayılır
    (en muhafazakâr varsayım — "anlamsız")."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 1.0
    if out != out:  # NaN
        return 1.0
    return min(1.0, max(0.0, out))


def benjamini_hochberg(
    p_values: Sequence[Any], alpha: float = DEFAULT_ALPHA
) -> List[Dict[str, Any]]:
    """BH adım-aşağı yordamı: p-değeri listesinden q-değeri listesi.

    Yordam:
      1. p-değerleri küçükten büyüğe sıralanır, rank i = 1..m.
      2. Ham q = p_i * m / i.
      3. Monotonluk için BÜYÜKTEN KÜÇÜĞE geriye doğru gidilerek kümülatif
         minimum alınır (aksi halde q sıralı olmaz), 1.0'a kırpılır.
      4. `rejected` = q <= alpha.

    Döner: GİRDİ SIRASINI KORUYAN sözlük listesi
      {"index", "p_value", "rank", "q_value", "rejected"}
    Boş girdide boş liste. Eşit p-değerlerinde sıralama girdi indeksine göre
    KARARLIDIR (aynı girdi → aynı çıktı; rapor tekrarlanabilir olmalı).
    """
    values = [_p(x) for x in (p_values or [])]
    m = len(values)
    if m == 0:
        return []

    order = sorted(range(m), key=lambda i: (values[i], i))
    q_by_index: List[float] = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        original = order[rank - 1]
        raw_q = values[original] * m / rank
        running = min(running, raw_q)
        q_by_index[original] = min(running, 1.0)

    rank_by_index: List[int] = [0] * m
    for rank, original in enumerate(order, start=1):
        rank_by_index[original] = rank

    threshold = float(alpha)
    return [
        {
            "index": i,
            "p_value": round(values[i], 6),
            "rank": rank_by_index[i],
            "q_value": round(q_by_index[i], 6),
            "rejected": q_by_index[i] <= threshold,
        }
        for i in range(m)
    ]


def fdr_report(
    rows: Iterable[Dict[str, Any]],
    *,
    p_key: str = "p_value",
    name_key: str = "name",
    alpha: float = DEFAULT_ALPHA,
    effective_tests: Optional[int] = None,
) -> Dict[str, Any]:
    """Çok-varyant taraması için BH raporu (rapor çıktısı, JSON uyumlu).

    `rows`: en az `{name, p_value}` taşıyan sözlükler (fazladan alanlar
    korunur — ör. `pnl`, `pf`, `window`).
    `effective_tests`: verilirse "korelasyon nedeniyle etkin deneme sayısı"
    çekincesi rapora yazılır ve o sayıyla ALTERNATİF (daha muhafazakâr
    DEĞİL — yalnız KIYAS amaçlı) bir q-değeri de hesaplanır. Karar kuralı
    DEĞİŞMEZ; bu sütun okuyucuya "en iyi/en kötü hâl" aralığını gösterir.
    """
    items = [dict(r) for r in (rows or [])]
    verdicts = benjamini_hochberg([r.get(p_key) for r in items], alpha=alpha)

    out_rows: List[Dict[str, Any]] = []
    for item, verdict in zip(items, verdicts):
        merged = dict(item)
        merged["name"] = str(item.get(name_key) or f"#{verdict['index']}")
        merged["p_value"] = verdict["p_value"]
        merged["rank"] = verdict["rank"]
        merged["q_value"] = verdict["q_value"]
        merged["rejected"] = verdict["rejected"]
        out_rows.append(merged)

    alt: Optional[List[Dict[str, Any]]] = None
    if effective_tests and int(effective_tests) > 0 and out_rows:
        m_eff = int(effective_tests)
        # Etkin deneme sayısı ile: q_eff = p * m_eff / rank (aynı monotonluk).
        running = 1.0
        order = sorted(range(len(out_rows)), key=lambda i: (out_rows[i]["p_value"], i))
        q_eff = [1.0] * len(out_rows)
        for rank in range(len(out_rows), 0, -1):
            original = order[rank - 1]
            running = min(running, out_rows[original]["p_value"] * m_eff / rank)
            q_eff[original] = min(running, 1.0)
        alt = [
            {
                "name": out_rows[i]["name"],
                "q_value_effective": round(q_eff[i], 6),
                "rejected_effective": q_eff[i] <= alpha,
            }
            for i in range(len(out_rows))
        ]

    out_rows.sort(key=lambda r: (r["rank"], r["name"]))
    return {
        "alpha": alpha,
        "tests": len(out_rows),
        "rejected": sum(1 for r in out_rows if r["rejected"]),
        "rows": out_rows,
        "effective_tests": int(effective_tests) if effective_tests else None,
        "effective": alt,
        "caveat": (
            "BH bağımsız/pozitif-bağımlı testleri varsayar; bizim varyantlarımız "
            "yüksek korelasyonludur (aynı semboller, aynı üç pencere) → q-değeri "
            "ham p'den dürüst ama hâlâ İYİMSERDİR. Karar hakemi değil, kaba bir "
            "kırpmadır."
        ),
    }


def render_report(report: Dict[str, Any]) -> str:
    """`fdr_report` çıktısını konsol tablosuna çevir."""
    rows = report.get("rows") or []
    alpha = report.get("alpha", DEFAULT_ALPHA)
    eff = {r["name"]: r for r in (report.get("effective") or [])}

    cols = [("Sıra", 5), ("Varyant", 28), ("p", 10), ("q (BH)", 10), ("Karar", 12)]
    if eff:
        cols.append(("q (etkin)", 11))
    header = " | ".join(name.ljust(width) for name, width in cols)
    lines = [
        "=" * len(header),
        f"ÇOKLU-KARŞILAŞTIRMA DÜZELTMESİ (Benjamini-Hochberg, alpha={alpha})",
        "=" * len(header),
        header,
        "-" * len(header),
    ]
    for row in rows:
        values = [
            str(row["rank"]),
            str(row["name"])[:28],
            f"{row['p_value']:.4f}",
            f"{row['q_value']:.4f}",
            "ANLAMLI" if row["rejected"] else "—",
        ]
        if eff:
            hit = eff.get(row["name"])
            values.append(f"{hit['q_value_effective']:.4f}" if hit else "—")
        lines.append(" | ".join(v.ljust(w) for v, (_, w) in zip(values, cols)))
    lines.append("-" * len(header))
    lines.append(
        f"{report.get('rejected', 0)}/{report.get('tests', 0)} varyant "
        f"q <= {alpha} eşiğini geçti."
    )
    lines.append(f"ÇEKİNCE: {report.get('caveat', '')}")
    lines.append("=" * len(header))
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Çok-varyant taramalarına Benjamini-Hochberg FDR düzeltmesi "
            "uygular (docs/EXPERIMENTS.md akışı)."
        )
    )
    parser.add_argument(
        "--json", type=str, default="-",
        help=(
            "En az {name, p_value} taşıyan sözlük listesi içeren JSON dosyası "
            "('-' = stdin, varsayılan)"
        ),
    )
    parser.add_argument(
        "--alpha", type=float, default=DEFAULT_ALPHA,
        help=f"Yanlış keşif oranı eşiği (varsayılan {DEFAULT_ALPHA})",
    )
    parser.add_argument(
        "--effective-tests", type=int, default=None,
        help=(
            "Korelasyon nedeniyle ETKİN deneme sayısı (verilirse kıyas sütunu "
            "eklenir; karar kuralını DEĞİŞTİRMEZ)"
        ),
    )
    parser.add_argument(
        "--format", type=str, default="text", choices=("text", "json"),
        help="Çıktı biçimi (varsayılan text)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.json == "-":
        payload = json.load(sys.stdin)
    else:
        with open(args.json, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if isinstance(payload, dict):
        payload = payload.get("rows") or payload.get("variants") or []
    report = fdr_report(
        payload, alpha=args.alpha, effective_tests=args.effective_tests
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
