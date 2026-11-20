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
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Yalniz stdlib'e bagli iki modul (config/.env YUKLEMEZ): P2-v2 istatistikleri.
from src.strategies.scalper import montecarlo, multitest  # noqa: E402

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
DEFAULT_FUNDING_CACHE_DIR = REPO_ROOT / "data" / "funding_cache"

# docs/DECISIONS.md #P2 karar kurali sabitleri.
PF_THRESHOLD = 1.1
MAX_BOGA_DROP_PCT = 0.20
MIN_TRADES_PER_WINDOW = 60

# P2-v2 (saglam kabul/red kurali) sabitleri.
P2V2_MIN_TRADES = MIN_TRADES_PER_WINDOW
P2V2_Q_MAX = 0.10               # BH sonrasi q esigi
P2V2_BLOCK_DAYS = 3             # gun-blok bootstrap blok uzunlugu
P2V2_N_PATHS = 5000
P2V2_SEED = 12345
P2V2_HARM_FRAC = 0.20           # pencere basina izin verilen zarar: scale_w'nin %20'si
P2V2_SCALE_FLOOR_FRAC = 0.25    # scale_w tabani: brut hareketin %25'i
P2V2_DD_TOLERANCE = 0.20        # havuz DD taban DD'den en fazla %20 kotu
# Dayaniklilik (stres) kosulari: ad -> backtest.py bayraklari.
STRESS_SCENARIOS: Dict[str, List[str]] = {
    "maliyet2x": ["--fee-stress"],
    "gecikme1": ["--entry-delay-candles", "1"],
}

BACKTEST_MODULE = "src.strategies.scalper.backtest"

# Alt surece AKTARILAN (izin listesi) ortam degiskenleri: yalniz calismak icin
# gerekenler. SCALPER_* ve BINANCE_* ASLA buradan gecmez (env-file + aday
# override'lari + asagidaki sahte degerler disinda hicbir ayar sizmaz).
_PASSTHROUGH_ENV = (
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
    "PYTHONPATH", "VIRTUAL_ENV", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
)
# Settings'in baslayabilmesi icin zorunlu, GERCEK OLMAYAN degerler (backtest
# yalniz herkese acik kline verisi ceker; anahtar kullanmaz).
_DUMMY_REQUIRED_ENV = {
    "BINANCE_API_KEY": "autoresearch-dummy-key",
    "BINANCE_API_SECRET": "autoresearch-dummy-secret",
    "TELEGRAM_BOT_TOKEN": "autoresearch-dummy",
    "TELEGRAM_CHAT_ID": "0",
    "OPENAI_API_KEY": "autoresearch-dummy",
    "GEMINI_API_KEY": "autoresearch-dummy",
    "DEEPSEEK_API_KEY": "autoresearch-dummy",
    "JWT_SECRET": "autoresearch-dummy-jwt-secret",
}

# `Settings` sinifi `model_config(env_file=".env")` ile cwd'deki yerel .env'i
# okur (src/core/config.py). Alt surecte config import EDILMEDEN once
# `SettingsConfigDict`i, env_file'i her zaman None yapacak sekilde sararak
# yerel .env'i devre disi birakiriz (config.py'ye dokunmadan).
NO_DOTENV_PRELUDE = (
    "import pydantic_settings as _ps\n"
    "_orig_scd = _ps.SettingsConfigDict\n"
    "def _scd_no_env_file(*a, **kw):\n"
    "    kw['env_file'] = None\n"
    "    return _orig_scd(*a, **kw)\n"
    "_ps.SettingsConfigDict = _scd_no_env_file\n"
)

_REPORT_PATH_RE = re.compile(r"Backtest raporu yaz\S+:\s*(\S+\.json)")


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


class EnvFileError(ValueError):
    """`--env-file` kullanilamaz (yok / SCALPER_ anahtari icermiyor)."""


def require_scalper_env(env: Dict[str, str], path: str) -> None:
    """Taban env'de en az bir `SCALPER_*` anahtari yoksa REDDET: sunucu
    snapshot'i olmadan koşu, baska bir stratejiyi olcer (bkz. parity-7)."""
    if not any(k.upper().startswith("SCALPER_") for k in env):
        raise EnvFileError(
            f"env-file '{path}' hic SCALPER_* anahtari icermiyor — sunucu "
            "snapshot'i gerekli: ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env > scalper_env.txt"
        )


def build_subprocess_env(env_vars: Dict[str, str], bot_log_dir: Path) -> Dict[str, str]:
    """Backtest alt sureci icin IZOLE ortam: izin listesi + env-file/override
    degerleri + sahte zorunlu degerler (anahtar/token). Ebeveyn ortamindaki SCALPER_* ve
    gercek anahtarlar gecmez; sahte anahtarlar env-file'dakini de ezer."""
    full_env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ}
    full_env.update(env_vars)
    full_env.update(_DUMMY_REQUIRED_ENV)
    full_env["TRADINGBOT_LOG_DIR"] = str(bot_log_dir)
    return full_env


def env_content_hash(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def backtest_flags(symbols: str) -> List[str]:
    """Sonucu etkileyen sabit backtest bayraklari (pencere ve onbellek dizini
    haric: pencere anahtarda ayri, cache-dir sonucu degistirmez)."""
    return ["--strategies", "C", "--symbols", symbols]


def funding_cache_fingerprint(cache_dir: Optional[Path] = None) -> str:
    """Funding onbellegi (<SEMBOL>.json) iceriginin ozeti: backtest bu dosyalari okur ve sonucu
    degistirir, bu yuzden sonuc anahtarinin parcasidir. Dizin yok/bos = bos-ozet."""
    d = Path(cache_dir) if cache_dir is not None else DEFAULT_FUNDING_CACHE_DIR
    h = hashlib.sha256()
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            h.update(f.name.encode("utf-8") + b"\0")
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def compute_result_key(
    git_sha: str,
    dirty_fingerprint: str,
    env_hash: str,
    overrides: Dict[str, str],
    windows: List[Tuple[str, str, str]],
    flags: List[str],
    baseline_source: str = "run",
    funding_hash: str = "",
) -> Dict[str, Any]:
    """Bir sonucun yeniden kullanilabilirligini belirleyen anahtar. Bilesenler
    denetim icin acik tutulur; `digest` hepsinin ozetidir."""
    components: Dict[str, Any] = {
        "git_sha": git_sha,
        "dirty_fingerprint": dirty_fingerprint,
        "env_hash": env_hash,
        "overrides": {str(k): str(v) for k, v in sorted(overrides.items())},
        "windows": [list(w) for w in windows],
        "flags": list(flags),
        "baseline_source": baseline_source,
        "funding_hash": funding_hash,
    }
    blob = json.dumps(components, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return dict(components, digest=hashlib.sha256(blob).hexdigest())


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


def git_dirty_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    """`git diff HEAD -- src scripts` + izlenmeyen (untracked) src/scripts dosyalarinin sha256'si
    (temiz = bos diff = sha256(b""))."""
    result = subprocess.run(
        ["git", "diff", "HEAD", "--", "src", "scripts"],
        cwd=repo_root, capture_output=True, check=True,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "src", "scripts"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    h = hashlib.sha256(result.stdout)
    for name in sorted(untracked):          # git diff HEAD izlenmeyen dosyayi GORMEZ
        h.update(b"\0untracked\0" + name.encode("utf-8") + b"\0")
        try:
            h.update((Path(repo_root) / name).read_bytes())
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()


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
        # backtest.py::_strict_json_value sonsuz PF'yi JSON null yazar
        # (kayip yok + kar var). Anahtar VAR ama null/"inf" -> +inf.
        pf_raw = overall["profit_factor"]
        profit_factor = float("inf") if pf_raw is None else float(pf_raw)
        return {
            "trades": int(overall["trades"]),
            "winrate": float(overall["winrate"]),
            "total_pnl": float(overall["total_pnl"]),
            "profit_factor": profit_factor,
            "max_drawdown": float(overall["max_drawdown"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def extract_trade_series(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Rapordan P2-v2 icin gereken ek alanlar: islem basina [exit_time_ms,
    net_pnl] listesi ve (varsa) bar-bazli max cokus. `trades` yoksa None."""
    trades = payload.get("trades")
    if not isinstance(trades, list):
        return None
    try:
        pnls = [[int(t["exit_time"]), float(t["pnl"])] for t in trades]
    except (KeyError, TypeError, ValueError):
        return None
    out: Dict[str, Any] = {"trade_pnls": pnls}
    # Yon (LONG/SHORT) yalniz TUM islemlerde varsa eklenir (walk_forward.py yon kirilimi).
    sides = [t.get("direction") for t in trades]
    if sides and all(isinstance(x, str) for x in sides):
        out["trade_sides"] = [x.upper() for x in sides]
    bar_dd = (payload.get("overall") or {}).get("bar_max_drawdown")
    if isinstance(bar_dd, (int, float)):
        out["bar_max_drawdown"] = float(bar_dd)
    return out


def extract_report_path(output: str, repo_root: Path) -> Optional[Path]:
    """Alt surecin YAZDIRDIGI "Backtest raporu yazildi: <yol>" satirindan bu
    kosunun rapor yolunu doner (son eslesme). Goreli yol `repo_root`a gore."""
    matches = _REPORT_PATH_RE.findall(output)
    if not matches:
        return None
    path = Path(matches[-1])
    return path if path.is_absolute() else repo_root / path


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
    extra_flags: Optional[List[str]] = None,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """`python3 -m src.strategies.scalper.backtest`'i alt surec olarak
    calistirir; ham ciktiyi `log_path`'e yazar, metrikleri doner.

    `TRADINGBOT_LOG_DIR` ile uygulama loglari (bot.log/trades.log/errors.log)
    izole bir dizine yonlendirilir — gercek `logs/bot.log` kirletilmez
    (`src/core/logger.py` bu degiskeni zaten destekliyor, testler de ayni
    deseni kullaniyor).
    """
    # `-c` sarmalayici: yerel .env'i devre disi birakir, sonra backtest'i
    # `-m` ile ayni sekilde calistirir (bkz. NO_DOTENV_PRELUDE).
    runner = (
        NO_DOTENV_PRELUDE
        + "import runpy\n"
        + f"runpy.run_module({BACKTEST_MODULE!r}, run_name='__main__', alter_sys=True)\n"
    )
    flags = list(extra_flags or [])
    if "--funding-cache-dir" not in flags:
        # MUTLAK yol: backtest'in varsayilani cwd-goreceli ('data/funding_cache'); walk_forward is
        # dizininde (cwd = <out>/_jobs/<id>) hic cozulmez ve funding sessizce sabit orana duserdi.
        flags += ["--funding-cache-dir", str(DEFAULT_FUNDING_CACHE_DIR)]
    cmd = [
        sys.executable, "-c", runner,
        "--strategies", "C",
        "--symbols", symbols,
        "--start", start,
        "--end", end,
        "--cache-dir", cache_dir,
    ] + flags
    bot_log_dir.mkdir(parents=True, exist_ok=True)
    full_env = build_subprocess_env(env_vars, bot_log_dir)

    proc = subprocess.run(
        cmd, cwd=repo_root, env=full_env, capture_output=True, text=True,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(combined, encoding="utf-8")

    if proc.returncode != 0:
        return False, None

    # Rapor, "en yeni dosya" (mtime) diye DEGIL, bu kosunun yazdirdigi yoldan
    # alinir: es zamanli/eski bir rapor yanlis atfedilemez.
    metrics: Optional[Dict[str, Any]] = None
    report_path = extract_report_path(combined, repo_root)
    if report_path is not None:
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            metrics = extract_metrics_from_json(payload)
            series = extract_trade_series(payload)
            if metrics is not None and series is not None:
                metrics.update(series)
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
    extra_flags: Optional[List[str]] = None,
    tag: str = "",
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
        log_path = out_dir / f"{variant_id}{tag}_{window_id}.log"
        ok, metrics = run_single_backtest(
            env_vars=env_vars, symbols=symbols, start=start, end=end,
            cache_dir=cache_dir, log_path=log_path, bot_log_dir=bot_log_dir,
            repo_root=repo_root, extra_flags=extra_flags,
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
# P2-v2 — saglam kabul/red kurali (eski P2 `decide` korunur, "P2-eski" gorunur)
#
# Aday ancak HEPSI saglanirsa: (1) her pencerede yeterli islem (taban + aday),
# (2) eslesmis gun-blok bootstrap ile havuzlanmis delta>0 ve BH sonrasi q<=0.10,
# (3) hicbir pencerede aday, tabandan scale_w'nin %20'sinden fazla geride degil,
# (4) havuz max drawdown tabandan en fazla %20 kotu, (5) maliyet 2x ve giris
# gecikmesi 1 mum stresinde de havuz delta > 0 (esit stresli tabana karsi).
# --------------------------------------------------------------------------

LABEL_ADAY = "ADAY"
LABEL_REJECT = "REDDEDILDI"
LABEL_LOW_SAMPLE = "YETERSIZ-ORNEK"
LABEL_ERROR = "HATA"


def _window_calendar() -> List[date]:
    cal: List[date] = []
    for _, start, end in WINDOWS:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        # WINDOWS bitisleri HARIC ([start, end)); +1 her pencereye sahte bir sifir gun eklerdi
        cal.extend(d0 + timedelta(days=i) for i in range((d1 - d0).days))
    return cal


def _series_trades(
    metrics_by_window: Dict[str, Dict[str, Any]],
) -> List[Tuple[datetime, float]]:
    out: List[Tuple[datetime, float]] = []
    for wid in WINDOW_IDS:
        for exit_ms, pnl in metrics_by_window[wid]["trade_pnls"]:
            out.append((datetime.fromtimestamp(exit_ms / 1000.0, tz=timezone.utc), float(pnl)))
    out.sort(key=lambda x: x[0])
    return out


def _has_series(metrics_by_window: Dict[str, Dict[str, Any]]) -> bool:
    return all(
        isinstance(metrics_by_window.get(w), dict)
        and isinstance(metrics_by_window[w].get("trade_pnls"), list)
        for w in WINDOW_IDS
    )


def _trade_level_drawdown(trades: List[Tuple[datetime, float]]) -> float:
    cum = peak = dd = 0.0
    for _, pnl in trades:
        cum += pnl
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def _pooled_drawdown(
    metrics_by_window: Dict[str, Dict[str, Any]], bar_level: bool,
) -> float:
    if bar_level:
        return sum(float(metrics_by_window[w]["bar_max_drawdown"]) for w in WINDOW_IDS)
    return _trade_level_drawdown(_series_trades(metrics_by_window))


def _pooled_net(metrics_by_window: Dict[str, Dict[str, Any]]) -> float:
    return sum(
        sum(float(p) for _, p in metrics_by_window[w]["trade_pnls"]) for w in WINDOW_IDS
    )


def p2v2_bh_qvalues(p_values: List[float], alpha: float = P2V2_Q_MAX) -> List[float]:
    """Benjamini-Hochberg q-degerleri (multitest modulu), girdi sirasinda."""
    return [r["q_value"] for r in multitest.benjamini_hochberg(p_values, alpha=alpha)]


def p2v2_evaluate(
    baseline: Dict[str, Dict[str, Any]], variant: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Tek aday icin BH-oncesi olcumler. Eski kuralin karari da eklenir."""
    ev: Dict[str, Any] = {}
    if not (_has_series(baseline) and _has_series(variant)):
        ev["error"] = "islem serisi (trade_pnls) eksik — taban/aday yeniden kosulmali"
        return ev
    old_decision, old_reason, old_score = decide(baseline, variant)
    ev.update(old_decision=old_decision, old_reason=old_reason, old_score=old_score)

    ev["low_sample"] = [
        w for w in WINDOW_IDS
        if baseline[w]["trades"] < P2V2_MIN_TRADES or variant[w]["trades"] < P2V2_MIN_TRADES
    ]

    ab = montecarlo.paired_ab_test(
        _series_trades(variant), _series_trades(baseline), _window_calendar(),
        n_paths=P2V2_N_PATHS, block_days=P2V2_BLOCK_DAYS, seed=P2V2_SEED,
    )
    delta, se = float(ab["delta"]), float(ab["std_error"])
    ev.update(delta=delta, se=se, p=float(ab["p_value"]))
    if se > 0:
        ev["score"] = delta / se
    else:
        ev["score"] = 0.0 if delta == 0 else (1e9 if delta > 0 else -1e9)

    harm: Dict[str, Dict[str, float]] = {}
    harmed: List[str] = []
    for w in WINDOW_IDS:
        pnls = [float(p) for _, p in baseline[w]["trade_pnls"]]
        gp = sum(x for x in pnls if x > 0)
        gl = abs(sum(x for x in pnls if x < 0))
        scale = max(abs(sum(pnls)), P2V2_SCALE_FLOOR_FRAC * (gp + gl))
        d = sum(float(p) for _, p in variant[w]["trade_pnls"]) - sum(pnls)
        harm[w] = {"delta": d, "scale": scale, "limit": -P2V2_HARM_FRAC * scale}
        if d < -P2V2_HARM_FRAC * scale - 1e-9:
            harmed.append(w)
    ev["window_harm"] = harm
    ev["harmed_windows"] = harmed

    bar = all(
        "bar_max_drawdown" in m for m in list(baseline.values()) + list(variant.values())
        if isinstance(m, dict)
    )
    ev["dd_source"] = "bar" if bar else "trade"
    ev["base_dd"] = _pooled_drawdown(baseline, bar)
    ev["cand_dd"] = _pooled_drawdown(variant, bar)
    return ev


def p2v2_stage1(evals: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """BH'yi TUM sweep adaylari uzerinden uygular, asama 1-4 kararini verir.
    ADAY donenler `needs_stress=True` tasir (asama 5 `p2v2_apply_stress`)."""
    family = [cid for cid, e in evals.items() if "error" not in e]
    qs = p2v2_bh_qvalues([evals[c]["p"] for c in family])
    q_by_id = dict(zip(family, qs))

    out: Dict[str, Dict[str, Any]] = {}
    for cid, e in evals.items():
        if "error" in e:
            out[cid] = {"label": LABEL_ERROR, "reason": e["error"], "score": 0.0,
                        "needs_stress": False}
            continue
        r = dict(e)
        q = q_by_id[cid]
        r["q"] = q
        r["needs_stress"] = False
        checks = {
            "sample": not e["low_sample"],
            "significance": e["delta"] > 0 and q <= P2V2_Q_MAX,
            "no_harm": not e["harmed_windows"],
            "drawdown": e["cand_dd"] <= e["base_dd"] * (1.0 + P2V2_DD_TOLERANCE) + 1e-9,
        }
        r["checks"] = checks
        if not checks["sample"]:
            r["label"] = LABEL_LOW_SAMPLE
            r["reason"] = f"yetersiz ornek (islem<{P2V2_MIN_TRADES}: {e['low_sample']})"
            r["score"] = e["score"]
            out[cid] = r
            continue
        fails: List[str] = []
        if not checks["significance"]:
            fails.append(
                f"anlamli degil (delta={e['delta']:+.2f}, p={e['p']:.4f}, "
                f"q={q:.4f}>{P2V2_Q_MAX})"
            )
        if not checks["no_harm"]:
            fails.append(
                "pencere zarari: " + ", ".join(
                    f"{w} {e['window_harm'][w]['delta']:+.2f}<{e['window_harm'][w]['limit']:.2f}"
                    for w in e["harmed_windows"]
                )
            )
        if not checks["drawdown"]:
            fails.append(
                f"DD ({e['dd_source']}) {e['cand_dd']:.2f} > taban {e['base_dd']:.2f} x "
                f"{1 + P2V2_DD_TOLERANCE:.2f}"
            )
        if fails:
            r["label"], r["reason"] = LABEL_REJECT, "; ".join(fails)
        else:
            r["label"] = LABEL_ADAY
            r["reason"] = (
                f"delta={e['delta']:+.2f} p={e['p']:.4f} q={q:.4f} (stres bekleniyor)"
            )
            r["needs_stress"] = True
        r["score"] = e["score"]
        out[cid] = r
    return out


def p2v2_apply_stress(
    result: Dict[str, Any],
    base_stress: Dict[str, Dict[str, Dict[str, Any]]],
    cand_stress: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """Asama 5: her stres senaryosunda havuz net PnL farki (aday - esit stresli
    taban) > 0 olmali. Eksik/okunamayan stres sonucu = DUSURULUR (fail-closed)."""
    r = dict(result)
    if r.get("label") != LABEL_ADAY:
        return r
    r["needs_stress"] = False
    deltas: Dict[str, Optional[float]] = {}
    fails: List[str] = []
    for name in STRESS_SCENARIOS:
        b, c = base_stress.get(name), cand_stress.get(name)
        if not b or not c or not _has_series(b) or not _has_series(c):
            deltas[name] = None
            fails.append(f"stres {name}: sonuc yok")
            continue
        d = _pooled_net(c) - _pooled_net(b)
        deltas[name] = d
        if not d > 0:
            fails.append(f"stres {name}: delta {d:+.2f}<=0")
    r["stress"] = deltas
    if fails:
        r["label"] = LABEL_REJECT
        r["reason"] = "; ".join(fails)
    else:
        r["reason"] = (
            f"delta={r['delta']:+.2f} p={r['p']:.4f} q={r['q']:.4f}; stres "
            + ", ".join(f"{n} {v:+.2f}" for n, v in deltas.items())
        )
    return r



# --------------------------------------------------------------------------
# docs/EXPERIMENTS.md — dated Autoresearch bolumune ekleme (saf metin fonksiyonu)
# --------------------------------------------------------------------------


def experiments_heading(date_str: str) -> str:
    return f"## {date_str} — Autoresearch (scripts/autoresearch.py)"


def format_variant_rows(
    variant_id: str, hypothesis: str, windows_result: Dict[str, Dict[str, Any]],
    decision: str, reason: str, score: float,
    old_decision: Optional[str] = None,
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
    if old_decision:
        karar_hucre += f" / P2-eski: {old_decision}"
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


def should_skip_candidate(
    summary: Dict[str, Any], candidate_id: str, key: Optional[Dict[str, Any]] = None,
) -> bool:
    """Yalniz BASARIYLA tamamlanmis (status == 'ok') ve `key` ile BIREBIR ayni
    anahtarla kosmus kayitlar atlanir — daha onceki bir 'hata' ya da farkli
    sha / kirli-agac / env / override / pencere ile kosmus sonuc yeniden
    kosulur (resumable, ama fail-open degil). `key` verilmezse (eski cagri)
    yalniz durum bakilir."""
    entry = summary.get("candidates", {}).get(candidate_id)
    if entry is None:
        return False
    if entry.get("status") != "ok":
        return False
    if key is None:
        return True
    stored = entry.get("key")
    return isinstance(stored, dict) and stored.get("digest") == key["digest"]


def baseline_reusable(summary: Dict[str, Any], key: Dict[str, Any]) -> bool:
    """Taban sonucu yalniz status 'ok' ve anahtari BIREBIR ayniysa yeniden kullanilir."""
    entry = summary.get("baseline", {})
    if entry.get("status") != "ok":
        return False
    stored = entry.get("key")
    return isinstance(stored, dict) and stored.get("digest") == key["digest"]


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
    parser.add_argument(
        "--registry", type=str, default=None,
        help="saklı pencere kaydi (varsayilan: docs/holdout_registry.json)",
    )
    parser.add_argument(
        "--open-sealed", type=str, default=None, metavar="AMAC",
        help="MUHURLU bir pencereye degen kosuyu bilincli ac (amac kayda yazilir)",
    )
    return parser


def registry_gate(args: argparse.Namespace, record: bool) -> Optional[int]:
    """WINDOWS'u saklı pencere kaydina karsi denetler (her pencere AYRI; aradaki bosluklar
    sayilmaz). Muhurlu pencereye degiyorsa ve --open-sealed yoksa 2 doner (ve mesaji basar);
    aksi halde dokunulan pencerelerin acilis sayisini basar ve None doner."""
    import walk_forward as wf

    path = Path(args.registry) if args.registry else wf.DEFAULT_REGISTRY
    try:
        touched = wf.guard_windows(
            path, [(s, e) for _, s, e in WINDOWS], by="autoresearch",
            open_sealed=args.open_sealed, record=record,
        )
    except wf.RegistryError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2
    print(wf.format_touched(touched))
    print(f"  kayit: {path} sha256={wf.registry_fingerprint(path)}")
    return None


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

    try:
        if not args.env_file or not Path(args.env_file).is_file():
            raise EnvFileError(f"env-file bulunamadi: '{args.env_file}' (--env-file zorunlu)")
        baseline_env = parse_env_file(args.env_file)
        require_scalper_env(baseline_env, args.env_file)
    except EnvFileError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1
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
        rc = registry_gate(args, record=False)
        if rc is not None:
            return rc
        print_dry_run_plan(candidates, symbols, baseline_env, args)
        return 0

    ensure_clean_src(REPO_ROOT)
    rc = registry_gate(args, record=True)
    if rc is not None:
        return rc
    git_sha = get_git_sha(REPO_ROOT)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = REPO_ROOT / "logs" / "autoresearch" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"

    # Ust duzey git_sha yalniz ilk olusturmada yazilir; her sonucun KENDI
    # anahtari (sha, kirli-agac, env hash, override...) kayitla birlikte saklanir
    # ve eski bir sonucun sha'si asla ezilmez.
    summary = load_summary(summary_path) or new_summary(git_sha, date_str)

    dirty_fp = git_dirty_fingerprint(REPO_ROOT)
    env_hash = env_content_hash(args.env_file)
    flags = backtest_flags(symbols)
    funding_hash = funding_cache_fingerprint()
    baseline_source = (
        "baseline-json:" + hashlib.sha256(Path(args.baseline_json).read_bytes()).hexdigest()
        if args.baseline_json else "run"
    )

    def key_for(overrides: Dict[str, str]) -> Dict[str, Any]:
        return compute_result_key(
            git_sha, dirty_fp, env_hash, overrides, WINDOWS, flags, baseline_source,
            funding_hash,
        )

    any_failure = False

    # --- Taban ---
    if args.baseline_json:
        baseline_metrics = json.loads(Path(args.baseline_json).read_text(encoding="utf-8"))
        summary["baseline"] = {
            "status": "ok", "source": args.baseline_json, "windows": baseline_metrics,
            "key": key_for({}),
        }
        save_summary(summary_path, summary)
        print(f"Taban --baseline-json'dan okundu: {args.baseline_json}")
    elif baseline_reusable(summary, key_for({})) and _has_series(summary["baseline"]["windows"]):
        baseline_metrics = summary["baseline"]["windows"]
        print("Taban ayni anahtarla (sha/env/pencere) zaten kosulmus, atlaniyor.")
    else:
        print("Taban kosuluyor (cache warm-up, 3 pencere)...")
        status, baseline_metrics = run_variant(
            "BASELINE", {}, baseline_env, symbols, args.cache_dir, out_dir, REPO_ROOT,
        )
        summary["baseline"] = {"status": status, "windows": baseline_metrics, "key": key_for({})}
        save_summary(summary_path, summary)
        if status != "ok":
            print("HATA: taban kosumu basarisiz oldu, devam edilemiyor.", file=sys.stderr)
            return 1

    # --- Adaylar, faz A: kosular ---
    newly_run: set = set()
    family: List[Candidate] = []
    for cand in candidates:
        if (
            should_skip_candidate(summary, cand.id, key_for(cand.overrides))
            and _has_series(summary["candidates"][cand.id].get("windows", {}))
        ):
            print(f"[{cand.id}] daha once basariyla kosulmus, atlaniyor (resumable).")
            family.append(cand)
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
                "key": key_for(cand.overrides),
            }
            save_summary(summary_path, summary)
            rows = [f"| {cand.id} | HATA | - | - | - | - | - | harness kosumu basarisiz |"]
            append_experiments_rows(date_str, rows, REPO_ROOT)
            continue

        summary["candidates"][cand.id] = {
            "hypothesis": cand.hypothesis, "overrides": cand.overrides,
            "status": "ok", "windows": windows_result,
            "key": key_for(cand.overrides),
        }
        save_summary(summary_path, summary)
        newly_run.add(cand.id)
        family.append(cand)

    # --- faz B: P2-v2 (BH tum sweep uzerinden) ---
    evals = {
        c.id: p2v2_evaluate(baseline_metrics, summary["candidates"][c.id]["windows"])
        for c in family
    }
    results = p2v2_stage1(evals)

    # --- faz C: dayaniklilik (yalniz 1-4'u gecenler) ---
    passers = [cid for cid, r in results.items() if r["label"] == LABEL_ADAY]
    if passers:
        print(f"\nStres kosulari: {passers} (maliyet 2x + giris gecikmesi 1 mum)")
        base_entry = summary["baseline"]
        base_stress = base_entry.setdefault("stress", {})
        for name, flags_ in STRESS_SCENARIOS.items():
            if name in base_stress and _has_series(base_stress[name]):
                continue
            st, res_ = run_variant(
                "BASELINE", {}, baseline_env, symbols, args.cache_dir, out_dir,
                REPO_ROOT, extra_flags=flags_, tag=f"_{name}",
            )
            if st == "ok":
                base_stress[name] = res_
            else:
                any_failure = True
        save_summary(summary_path, summary)
        for cid in passers:
            entry = summary["candidates"][cid]
            cand_stress = entry.setdefault("stress", {})
            for name, flags_ in STRESS_SCENARIOS.items():
                if name in cand_stress and _has_series(cand_stress[name]):
                    continue
                st, res_ = run_variant(
                    cid, entry["overrides"], baseline_env, symbols, args.cache_dir,
                    out_dir, REPO_ROOT, extra_flags=flags_, tag=f"_{name}",
                )
                if st == "ok":
                    cand_stress[name] = res_
                else:
                    any_failure = True
            results[cid] = p2v2_apply_stress(results[cid], base_stress, cand_stress)
        save_summary(summary_path, summary)

    # --- faz D: kayit ---
    decided: List[Tuple[str, str, str, float]] = []  # (id, hypothesis, decision, score)
    for cand in family:
        r = results[cand.id]
        entry = summary["candidates"][cand.id]
        entry.update({
            "decision": r["label"], "reason": r["reason"], "score": r["score"],
            "rule": "P2-v2",
            "p_value": r.get("p"), "q_value": r.get("q"), "delta": r.get("delta"),
            "std_error": r.get("se"), "checks": r.get("checks"),
            "window_harm": r.get("window_harm"), "stress": entry.get("stress", {}),
            "stress_delta": r.get("stress"),
            "old_decision": r.get("old_decision"), "old_reason": r.get("old_reason"),
            "old_score": r.get("old_score"),
        })
        if cand.id in newly_run:
            rows = format_variant_rows(
                cand.id, cand.hypothesis, entry["windows"], r["label"], r["reason"],
                r["score"], old_decision=r.get("old_decision"),
            )
            append_experiments_rows(date_str, rows, REPO_ROOT)
        print(
            f"  [{cand.id}] -> {r['label']}: {r['reason']} (skor={r['score']:+.2f}) "
            f"| P2-eski: {r.get('old_decision', '-')}"
        )
        decided.append((cand.id, cand.hypothesis, r["label"], r["score"]))
    save_summary(summary_path, summary)

    # --- Ozet ---
    print("\n" + "=" * 72)
    print("AUTORESEARCH OZETI")
    print("=" * 72)
    adaylar = sorted(
        (d for d in decided if d[2] == "ADAY"), key=lambda d: d[3], reverse=True,
    )
    if adaylar:
        print("ADAY [P2-v2] (canliya aday, testnet soak >=5 gun onerilir):")
        for cid, hyp, _, score in adaylar:
            print(f"  {cid}  skor={score:+.2f}  — {hyp}")
    else:
        print("ADAY bulunamadi.")
    reddedilenler = [d for d in decided if d[2] == "REDDEDILDI"]
    if reddedilenler:
        print("\nREDDEDILDI:")
        for cid, hyp, _, score in reddedilenler:
            print(f"  {cid}  skor={score:+.2f}  — {hyp}")
    yetersiz = [d for d in decided if d[2] == LABEL_LOW_SAMPLE]
    if yetersiz:
        print("\nYETERSIZ-ORNEK (reddedilmedi; islem sayisi yetmedi):")
        for cid, hyp, _, score in yetersiz:
            print(f"  {cid}  skor={score:+.2f}  — {hyp}")
    hatalilar = [d for d in decided if d[2] not in (LABEL_ADAY, LABEL_REJECT, LABEL_LOW_SAMPLE)]
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
