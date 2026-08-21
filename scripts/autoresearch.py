#!/usr/bin/env python3
"""
scripts/autoresearch.py — Karpathy tarzi "bir degisiklik oner -> sabit
degerlendirmeyle kos -> tut/at -> logla" dongusu, strateji C (scalper)
parametreleri icin.

NE YAPMAZ (bilincli tasarim siniri):
- Sunucuya ASLA dokunmaz: ssh/scp yok, sadece yerel `--env-file`'i OKUR.
- `.env` dosyasini ASLA yazmaz/degistirmez.
- ASLA deploy etmez, ASLA sureci yeniden baslatmaz.
- Sadece `results/`, `logs/autoresearch/<tarih>/` altina ve
  `docs/EXPERIMENTS.md`'ye (ek olarak) yazar.

Kullanim:
    python3 scripts/autoresearch.py --env-file scalper_env.txt --limit 3
    python3 scripts/autoresearch.py --env-file scalper_env.txt --dry-run
    python3 scripts/autoresearch.py --env-file scalper_env.txt --only E4a,E4b

`--env-file` uretimi (KULLANICI elle calistirir, bu script ASLA ssh cagirmaz):
    ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env > scalper_env.txt

Bir ADAY canliya nasil gecer (insan karari, bu script otomatik yapmaz):
    backtest (bu script) -> testnet soak >=5 gun -> mainnet (docs/CLAUDE.md).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# docs/DECISIONS.md P2 + CLAUDE.md "Yasaklar #1" ile ayni pencereler.
WINDOWS: List[Tuple[str, str, str]] = [
    ("AYI", "2026-01-23", "2026-02-13"),
    ("YATAY", "2026-07-01", "2026-07-21"),
    ("BOGA", "2026-08-07", "2026-08-21"),
]
WINDOW_IDS: List[str] = [w[0] for w in WINDOWS]

DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT"
DEFAULT_CANDIDATES_PATH = Path(__file__).resolve().parent / "autoresearch_candidates.json"
DEFAULT_CACHE_DIR = "data/klines_cache"

# docs/DECISIONS.md #P2 karar kurali sabitleri.
PF_THRESHOLD = 1.1
MAX_BOGA_DROP_PCT = 0.20
MIN_TRADES_PER_WINDOW = 60


# --------------------------------------------------------------------------
# Veri tipleri
# --------------------------------------------------------------------------


@dataclass
class Candidate:
    id: str
    hypothesis: str
    overrides: Dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------
# env-file / candidates okuma (AG YOK, saf)
# --------------------------------------------------------------------------


def parse_env_file(path: str) -> Dict[str, str]:
    """`KEY=VALUE` satirlarindan olusan bir dosyayi sozluge cevirir.

    Bos satirlar ve `#` ile baslayan yorumlar atlanir. `ssh ... | xargs`
    ciktisinda bazen tek/cift tirnak tasan degerler temizlenir. Bicimsiz
    (`=` icermeyen) satirlar sessizce atlanir.
    """
    env: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                env[key] = value
    return env


def load_candidates(path: Path) -> List[Candidate]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    candidates: List[Candidate] = []
    seen_ids: set = set()
    for item in raw:
        cid = str(item["id"])
        if cid in seen_ids:
            raise ValueError(f"aday listesi tekrarlayan id iceriyor: {cid}")
        seen_ids.add(cid)
        candidates.append(
            Candidate(
                id=cid,
                hypothesis=str(item.get("hypothesis", "")),
                overrides={str(k): str(v) for k, v in item.get("overrides", {}).items()},
            )
        )
    return candidates


# --------------------------------------------------------------------------
# Guvenlik: sadece commit'lenmis koda karsi degerlendir
# --------------------------------------------------------------------------


def dirty_src_status(repo_root: Path = REPO_ROOT) -> str:
    """`git status --porcelain -- src` ciktisini dondurur (bos = temiz)."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "src"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout


def get_git_sha(repo_root: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def ensure_clean_src(repo_root: Path = REPO_ROOT) -> None:
    """`src/` icinde commit'lenmemis degisiklik varsa reddet.

    Degerlendirme HER ZAMAN commit'lenmis koda karsi yapilmali (aksi halde
    hangi kod versiyonunun olculdugu belirsiz kalir).
    """
    status = dirty_src_status(repo_root)
    if status.strip():
        print(
            "HATA: src/ icinde commit'lenmemis degisiklik var — degerlendirme "
            "yalnizca commit'lenmis kod uzerinde yapilabilir.\n" + status,
            file=sys.stderr,
        )
        sys.exit(1)


# --------------------------------------------------------------------------
# Harness ciktisi ayristirma (JSON tercih edilir, metin tablo yedek)
# --------------------------------------------------------------------------


def extract_metrics_from_json(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """`write_json_report`'un yazdigi raporun `overall` blogundan metrik
    cikarir. `--strategies C` ile kosuldugu icin `overall` == strateji C'nin
    kendisidir; ayrica dogrulama olarak `by_strategy.C` da varsa karsilastirilir
    (yoksa `overall`e guvenilir — bos-C durumunda `by_strategy`de anahtar hic
    olusmayabilir, bkz. `_group_by_strategy`)."""
    overall = payload.get("overall")
    if not isinstance(overall, dict):
        return None
    try:
        return {
            "trades": int(overall["trades"]),
            "winrate": float(overall["winrate"]),
            "total_pnl": float(overall["total_pnl"]),
            "profit_factor": float(overall["profit_factor"]),
            "max_drawdown": float(overall["max_drawdown"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def parse_report_text(text: str) -> Optional[Dict[str, Any]]:
    """Yedek ayristirici: `print_report`'un konsol tablosundaki "TOPLAM"
    satirini okur (`backtest.py::print_report::_row` bicimi: label solda,
    " | " ile ayrilmis, her deger `ljust` ile doldurulmus).

    Sutun sirasi (bkz. `print_report` `cols`): Strateji, Islem, Kazanma%,
    Toplam PnL, Ort ROI%, P.Faktor, Mks Ardş.Kyp, Mks DD, Ort Sure(dk),
    Ort MAE%, Ort MFE%.

    SINIRLAMA: bu, `print_report`'un kozmetik bicimine (sutun sirasi/etiket
    metni) kirilgan bagimlidir — JSON raporu her zaman tercih edilmeli, bu
    yalnizca JSON dosyasi bulunamazsa kullanilir.
    """
    for line in text.splitlines():
        if "|" not in line:
            continue
        fields = [f.strip() for f in line.split("|")]
        if not fields or fields[0] != "TOPLAM":
            continue
        if len(fields) < 8:
            return None
        try:
            pf_raw = fields[5]
            pf = float("inf") if pf_raw == "inf" else float(pf_raw)
            return {
                "trades": int(fields[1]),
                "winrate": float(fields[2]),
                "total_pnl": float(fields[3]),
                "profit_factor": pf,
                "max_drawdown": float(fields[7]),
            }
        except (ValueError, IndexError):
            return None
    return None


# --------------------------------------------------------------------------
# Tek bir (varyant, pencere) backtest kosumu
# --------------------------------------------------------------------------


def run_single_backtest(
    env_vars: Dict[str, str],
    symbols: str,
    start: str,
    end: str,
    cache_dir: str,
    log_path: Path,
    bot_log_dir: Path,
    repo_root: Path = REPO_ROOT,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """`python3 -m src.strategies.scalper.backtest`'i alt surec olarak
    calistirir; ham ciktiyi `log_path`'e yazar, metrikleri doner.

    `TRADINGBOT_LOG_DIR` ile uygulama loglari (bot.log/trades.log/errors.log)
    izole bir dizine yonlendirilir — gercek `logs/bot.log` kirletilmez
    (`src/core/logger.py` bu degiskeni zaten destekliyor, testler de ayni
    deseni kullaniyor).
    """
    logs_dir = repo_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in logs_dir.glob("backtest_*.json")}

    cmd = [
        sys.executable, "-m", "src.strategies.scalper.backtest",
        "--strategies", "C",
        "--symbols", symbols,
        "--start", start,
        "--end", end,
        "--cache-dir", cache_dir,
    ]
    full_env = dict(os.environ)
    full_env.update(env_vars)
    bot_log_dir.mkdir(parents=True, exist_ok=True)
    full_env["TRADINGBOT_LOG_DIR"] = str(bot_log_dir)

    proc = subprocess.run(
        cmd, cwd=repo_root, env=full_env, capture_output=True, text=True,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(combined, encoding="utf-8")

    if proc.returncode != 0:
        return False, None

    after = {p.name for p in logs_dir.glob("backtest_*.json")}
    new_files = after - before
    metrics: Optional[Dict[str, Any]] = None
    if new_files:
        newest = max((logs_dir / n for n in new_files), key=lambda p: p.stat().st_mtime)
        try:
            payload = json.loads(newest.read_text(encoding="utf-8"))
            metrics = extract_metrics_from_json(payload)
        except (OSError, json.JSONDecodeError):
            metrics = None

    if metrics is None:
        metrics = parse_report_text(combined)

    if metrics is None:
        return False, None
    return True, metrics


def run_variant(
    variant_id: str,
    overrides: Dict[str, str],
    baseline_env: Dict[str, str],
    symbols: str,
    cache_dir: str,
    out_dir: Path,
    repo_root: Path = REPO_ROOT,
) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """Bir varyanti 3 pencerede SIRALI kosar (paralel = Binance 429, bkz.
    CLAUDE.md). Ilk basarisiz pencerede DURUR ve "hata" doner.

    Doner: (status, {pencere_id: metrikler}) — status "ok" | "hata".
    """
    env_vars = dict(baseline_env)
    env_vars.update(overrides)
    bot_log_dir = out_dir / "_botlog"

    windows_result: Dict[str, Dict[str, Any]] = {}
    for window_id, start, end in WINDOWS:
        log_path = out_dir / f"{variant_id}_{window_id}.log"
        ok, metrics = run_single_backtest(
            env_vars=env_vars, symbols=symbols, start=start, end=end,
            cache_dir=cache_dir, log_path=log_path, bot_log_dir=bot_log_dir,
            repo_root=repo_root,
        )
        if not ok:
            print(f"  [{variant_id}] {window_id}: HATA — bkz. {log_path}", file=sys.stderr)
            return "hata", windows_result
        windows_result[window_id] = metrics
        pf = metrics["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"  [{variant_id}] {window_id}: islem={metrics['trades']} "
            f"PnL={metrics['total_pnl']:.2f} PF={pf_str}"
        )
    return "ok", windows_result


# --------------------------------------------------------------------------
# Karar kurali — docs/DECISIONS.md #P2
# --------------------------------------------------------------------------


def decide(
    baseline: Dict[str, Dict[str, Any]], variant: Dict[str, Dict[str, Any]],
) -> Tuple[str, str, float]:
    """P2 karar kurali.

    Aday = AYI PF >= 1.1 (VEYA AYI ve YATAY PnL birlikte iyilesir) VE BOGA
    PnL kaybi <= %20; ayrica her pencerede >=60 islem yoksa "asiri
    filtreleme" ile reddedilir (tek pencerede parlayan aday, dusuk ornekle
    yanlis pozitif verebilir — bkz. reddedilen E2ab kaydi).

    Doner: (karar, sebep, skor). karar in {"ADAY", "REDDEDILDI", "HATA"}.
    skor = 3 pencerenin PnL delta'larinin toplami (siralama icin).
    """
    missing = [w for w in WINDOW_IDS if w not in baseline or w not in variant]
    if missing:
        return "HATA", f"eksik pencere sonucu: {missing}", 0.0

    low_sample = [w for w in WINDOW_IDS if variant[w]["trades"] < MIN_TRADES_PER_WINDOW]
    if low_sample:
        return (
            "REDDEDILDI",
            f"asiri filtreleme (islem<{MIN_TRADES_PER_WINDOW}: {low_sample})",
            0.0,
        )

    pf_ayi = variant["AYI"]["profit_factor"]
    pnl_ayi_delta = variant["AYI"]["total_pnl"] - baseline["AYI"]["total_pnl"]
    pnl_yatay_delta = variant["YATAY"]["total_pnl"] - baseline["YATAY"]["total_pnl"]
    pnl_boga_delta = variant["BOGA"]["total_pnl"] - baseline["BOGA"]["total_pnl"]
    score = pnl_ayi_delta + pnl_yatay_delta + pnl_boga_delta

    cond_pf = pf_ayi >= PF_THRESHOLD
    cond_both_improve = pnl_ayi_delta > 0 and pnl_yatay_delta > 0
    gate1 = cond_pf or cond_both_improve

    baseline_boga_pnl = baseline["BOGA"]["total_pnl"]
    variant_boga_pnl = variant["BOGA"]["total_pnl"]
    if baseline_boga_pnl > 0:
        boga_drop_pct = (baseline_boga_pnl - variant_boga_pnl) / baseline_boga_pnl
        cond_boga = boga_drop_pct <= MAX_BOGA_DROP_PCT
    else:
        # Taban <=0 iken "%kayip" tanimsiz: gerilememis olmasi yeterli sayilir.
        cond_boga = variant_boga_pnl >= baseline_boga_pnl

    if gate1 and cond_boga:
        reason = f"AYI PF={pf_ayi:.2f}" if cond_pf else "AYI&YATAY PnL birlikte iyilesti"
        return "ADAY", reason, score

    reasons = []
    if not gate1:
        reasons.append(f"AYI PF {pf_ayi:.2f}<{PF_THRESHOLD} ve AYI/YATAY birlikte iyilesmedi")
    if not cond_boga:
        reasons.append("BOGA PnL kaybi >%20")
    return "REDDEDILDI", "; ".join(reasons), score


# --------------------------------------------------------------------------
# docs/EXPERIMENTS.md — dated Autoresearch bolumune ekleme (saf metin fonksiyonu)
# --------------------------------------------------------------------------


def experiments_heading(date_str: str) -> str:
    return f"## {date_str} — Autoresearch (scripts/autoresearch.py)"


def format_variant_rows(
    variant_id: str, hypothesis: str, windows_result: Dict[str, Dict[str, Any]],
    decision: str, reason: str, score: float,
) -> List[str]:
    rows: List[str] = []
    for window_id, _, _ in WINDOWS:
        m = windows_result.get(window_id)
        if m is None:
            rows.append(f"| {variant_id} | {window_id} | - | - | - | - | - | HATA |")
            continue
        pf = m["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        rows.append(
            f"| {variant_id} | {window_id} | {m['trades']} | {m['winrate']:.1f} | "
            f"{m['total_pnl']:+.2f} | {pf_str} | {m['max_drawdown']:.2f} | - |"
        )
    karar_hucre = f"{decision} ({reason})" if reason else decision
    rows.append(
        f"| {variant_id} | KARAR | - | - | {score:+.2f} | - | - | {karar_hucre} |"
    )
    if hypothesis:
        rows.append(f"| {variant_id} | hipotez | {hypothesis} | | | | | |")
    return rows


def upsert_experiments_section(content: str, date_str: str, new_rows: List[str]) -> str:
    """`content` icindeki tarihli Autoresearch bolumune `new_rows`'u ekler.

    Bolum yoksa dosya sonuna basliksiyla birlikte yeni bir bolum eklenir.
    Bolum varsa yeni satirlar, bir sonraki "## " basligindan (ya da dosya
    sonundan) hemen once, mevcut tablonun altina eklenir — baslik/ayirici
    satir TEKRARLANMAZ."""
    heading = experiments_heading(date_str)
    header_lines = [
        heading,
        "Kaynak: `python3 scripts/autoresearch.py` — otomatik uretildi, elle duzenlemeyin.",
        "| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines = content.splitlines()
    heading_idx = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            heading_idx = i
            break

    if heading_idx is None:
        block = "\n".join(header_lines + new_rows)
        sep = "" if (content == "" or content.endswith("\n")) else "\n"
        return content + sep + "\n" + block + "\n"

    end_idx = len(lines)
    for i in range(heading_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            end_idx = i
            break
    insertion_point = end_idx
    while insertion_point > heading_idx + 1 and lines[insertion_point - 1].strip() == "":
        insertion_point -= 1

    new_lines = lines[:insertion_point] + new_rows + lines[insertion_point:]
    return "\n".join(new_lines) + "\n"


def append_experiments_rows(date_str: str, rows: List[str], repo_root: Path = REPO_ROOT) -> None:
    path = repo_root / "docs" / "EXPERIMENTS.md"
    content = path.read_text(encoding="utf-8") if path.exists() else "# Deney defteri\n"
    updated = upsert_experiments_section(content, date_str, rows)
    path.write_text(updated, encoding="utf-8")


# --------------------------------------------------------------------------
# summary.json / resumability
# --------------------------------------------------------------------------


def load_summary(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_summary(path: Path, summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def new_summary(git_sha: str, date_str: str) -> Dict[str, Any]:
    return {
        "date": date_str,
        "git_sha": git_sha,
        "baseline": {"status": "beklemede"},
        "candidates": {},
    }


def should_skip_candidate(summary: Dict[str, Any], candidate_id: str) -> bool:
    """Yalniz BASARIYLA tamamlanmis (status == 'ok') kayitlar atlanir —
    daha onceki bir 'hata' yeniden denenir (resumable, ama fail-open degil)."""
    entry = summary.get("candidates", {}).get(candidate_id)
    if entry is None:
        return False
    return entry.get("status") == "ok"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Strateji C parametreleri icin Karpathy-tarzi oner->kos->tut/at "
            "arastirma dongusu. Sunucuya/`.env`'e ASLA dokunmaz, ASLA deploy "
            "etmez."
        )
    )
    parser.add_argument(
        "--env-file", type=str, required=True,
        help=(
            "SCALPER_* KEY=VALUE satirlari iceren dosya (taban env). Uretim: "
            "`ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env > scalper_env.txt`"
        ),
    )
    parser.add_argument(
        "--candidates", type=str, default=str(DEFAULT_CANDIDATES_PATH),
        help=f"Aday listesi JSON dosyasi (varsayilan: {DEFAULT_CANDIDATES_PATH.name})",
    )
    parser.add_argument("--limit", type=int, default=None, help="Kosulacak azami aday sayisi")
    parser.add_argument(
        "--only", type=str, default=None,
        help="Virgulle ayrilmis aday id listesi (yalniz bunlar kosulur)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Hicbir backtest kosmadan planı yazdirir (AG YOK, dosya YAZMAZ)",
    )
    parser.add_argument(
        "--baseline-json", type=str, default=None,
        help=(
            "Onceden hesaplanmis taban metrikleri JSON dosyasi "
            '({"AYI": {...}, "YATAY": {...}, "BOGA": {...}}) — verilirse taban '
            "kosusu ATLANIR (cache warm-up yapilmaz)."
        ),
    )
    parser.add_argument(
        "--cache-dir", type=str, default=DEFAULT_CACHE_DIR,
        help=f"backtest.py --cache-dir (varsayilan: {DEFAULT_CACHE_DIR})",
    )
    return parser


def print_dry_run_plan(
    candidates: List[Candidate], symbols: str, baseline_env: Dict[str, str], args: argparse.Namespace,
) -> None:
    print("[DRY-RUN] scripts/autoresearch.py plani — HICBIR backtest KOSULMAYACAK\n")
    print(f"env-file: {args.env_file} ({len(baseline_env)} degisken okundu)")
    print(f"semboller: {symbols}")
    print("pencereler: " + ", ".join(f"{w}({s}->{e})" for w, s, e in WINDOWS))
    try:
        status = dirty_src_status()
        clean_note = "temiz" if not status.strip() else "KIRLI — gercek kosumda REDDEDILECEK"
        print(f"git src/ durumu: {clean_note}")
    except (subprocess.SubprocessError, OSError):
        print("git src/ durumu: kontrol edilemedi (git bulunamadi?)")
    if args.baseline_json:
        print(f"taban: --baseline-json'dan okunacak ({args.baseline_json})")
    else:
        print("taban: 3 pencerede kosulacak (cache warm-up)")
    print(f"\nadaylar (limit={args.limit if args.limit is not None else 'yok'}):")
    for i, c in enumerate(candidates, start=1):
        print(f"  {i}. {c.id} — {c.hypothesis}")
        print(f"     overrides: {c.overrides}")
    n_variant_runs = len(candidates) * len(WINDOWS)
    n_baseline_runs = 0 if args.baseline_json else len(WINDOWS)
    total = n_variant_runs + n_baseline_runs
    print(
        f"\ntoplam kosum: baseline({n_baseline_runs} pencere) + "
        f"{len(candidates)} aday x {len(WINDOWS)} pencere = {total} backtest cagrisi "
        f"(~4dk/kosu => ~{total * 4}dk tahmini, sirali)"
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    baseline_env = parse_env_file(args.env_file)
    symbols = baseline_env.get("SCALPER_SYMBOL_ALLOWLIST", "").strip() or DEFAULT_SYMBOLS

    candidates = load_candidates(Path(args.candidates))
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = wanted - {c.id for c in candidates}
        if unknown:
            print(f"HATA: --only bilinmeyen id iceriyor: {sorted(unknown)}", file=sys.stderr)
            return 1
        candidates = [c for c in candidates if c.id in wanted]
    if args.limit is not None:
        candidates = candidates[: args.limit]

    if args.dry_run:
        print_dry_run_plan(candidates, symbols, baseline_env, args)
        return 0

    ensure_clean_src(REPO_ROOT)
    git_sha = get_git_sha(REPO_ROOT)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = REPO_ROOT / "logs" / "autoresearch" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"

    summary = load_summary(summary_path) or new_summary(git_sha, date_str)
    summary["git_sha"] = git_sha

    any_failure = False

    # --- Taban ---
    if args.baseline_json:
        baseline_metrics = json.loads(Path(args.baseline_json).read_text(encoding="utf-8"))
        summary["baseline"] = {"status": "ok", "source": args.baseline_json, "windows": baseline_metrics}
        save_summary(summary_path, summary)
        print(f"Taban --baseline-json'dan okundu: {args.baseline_json}")
    elif summary.get("baseline", {}).get("status") == "ok":
        baseline_metrics = summary["baseline"]["windows"]
        print("Taban zaten kosulmus (bugunun summary.json'undan devam), atlaniyor.")
    else:
        print("Taban kosuluyor (cache warm-up, 3 pencere)...")
        status, baseline_metrics = run_variant(
            "BASELINE", {}, baseline_env, symbols, args.cache_dir, out_dir, REPO_ROOT,
        )
        summary["baseline"] = {"status": status, "windows": baseline_metrics}
        save_summary(summary_path, summary)
        if status != "ok":
            print("HATA: taban kosumu basarisiz oldu, devam edilemiyor.", file=sys.stderr)
            return 1

    # --- Adaylar ---
    decided: List[Tuple[str, str, str, float]] = []  # (id, hypothesis, decision, score)
    for cand in candidates:
        if should_skip_candidate(summary, cand.id):
            print(f"[{cand.id}] daha once basariyla kosulmus, atlaniyor (resumable).")
            entry = summary["candidates"][cand.id]
            decided.append((cand.id, cand.hypothesis, entry.get("decision", "?"), entry.get("score", 0.0)))
            continue

        print(f"\n[{cand.id}] {cand.hypothesis}")
        print(f"  overrides: {cand.overrides}")
        status, windows_result = run_variant(
            cand.id, cand.overrides, baseline_env, symbols, args.cache_dir, out_dir, REPO_ROOT,
        )
        if status != "ok":
            any_failure = True
            summary["candidates"][cand.id] = {
                "hypothesis": cand.hypothesis, "overrides": cand.overrides,
                "status": "hata", "windows": windows_result,
            }
            save_summary(summary_path, summary)
            rows = [f"| {cand.id} | HATA | - | - | - | - | - | harness kosumu basarisiz |"]
            append_experiments_rows(date_str, rows, REPO_ROOT)
            continue

        decision, reason, score = decide(baseline_metrics, windows_result)
        summary["candidates"][cand.id] = {
            "hypothesis": cand.hypothesis, "overrides": cand.overrides,
            "status": "ok", "windows": windows_result,
            "decision": decision, "reason": reason, "score": score,
        }
        save_summary(summary_path, summary)
        rows = format_variant_rows(cand.id, cand.hypothesis, windows_result, decision, reason, score)
        append_experiments_rows(date_str, rows, REPO_ROOT)
        print(f"  -> {decision}: {reason} (skor={score:+.2f})")
        decided.append((cand.id, cand.hypothesis, decision, score))

    # --- Ozet ---
    print("\n" + "=" * 72)
    print("AUTORESEARCH OZETI")
    print("=" * 72)
    adaylar = sorted(
        (d for d in decided if d[2] == "ADAY"), key=lambda d: d[3], reverse=True,
    )
    if adaylar:
        print("ADAY (canliya aday, testnet soak >=5 gun onerilir):")
        for cid, hyp, _, score in adaylar:
            print(f"  {cid}  skor={score:+.2f}  — {hyp}")
    else:
        print("ADAY bulunamadi.")
    reddedilenler = [d for d in decided if d[2] == "REDDEDILDI"]
    if reddedilenler:
        print("\nREDDEDILDI:")
        for cid, hyp, _, score in reddedilenler:
            print(f"  {cid}  skor={score:+.2f}  — {hyp}")
    hatalilar = [d for d in decided if d[2] not in ("ADAY", "REDDEDILDI")]
    if hatalilar or any_failure:
        print("\nHATA (harness kosumu basarisiz oldu, log'lara bakin):")
        for cid, hyp, dec, _ in hatalilar:
            print(f"  {cid} — {dec}")

    print(f"\nOzet dosyasi: {summary_path}")
    print(f"Ham loglar: {out_dir}")
    print(f"docs/EXPERIMENTS.md guncellendi (baslik: {experiments_heading(date_str)})")

    return 1 if any_failure else 0


if __name__ == "__main__":
    sys.exit(main())
