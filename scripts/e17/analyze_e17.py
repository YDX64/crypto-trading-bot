#!/usr/bin/env python3
"""E17 ÖN-KAYITLI DENEY ANALİZCİSİ (docs/audits/2026-10-10-prereg-tf/PLAN.md §5.3, §7, §8, §9, Ek A).

Bu betik planın PARÇASIDIR: kilit commit'i L'ye girer (H blob'u olarak hash'lenir) ve
kilitten sonra yalnız Ek-A/§9.1 yama usulüyle değişir. E16 `analyze_prereg.py` zayıflıkları
(merkezlenmemiş yüzdelik bootstrap, 1 günlük blok, kilit uyuşmazlığının yalnız
raporlanması, istatistik bayrakları, 2 haneli PF, bayat JSON) burada KAPALIDIR.

Kullanım:  analyze_e17.py <komut> [hedef] [--root DIR]
  Başka bayrak YOKTUR (istatistik parametreleri CLI'dan değiştirilemez; env yalnız YOL için).
  Komutlar: freeze, lock, verify-lock, manifest <set>, smoke, check-run <etiket>,
            dev-screen, dev-decide, diag, val, final, decide.
  Çıkış kodları: 0 başarılı | 2 kullanım hatası | 3 kilit uyuşmazlığı (HİÇBİR dosya yazılmaz)
                 | 4 aşama ön koşulu ihlali (sıra, ikinci yazım, `lock` kapalı)
                 | 5 koşu/veri bütünlüğü.  Kapı BAŞARISIZLIĞI çıkış kodu DEĞİLDİR (sonuç dosyada).

Yol değişkenleri (YALNIZ yol): E17_ROOT (varsayılan $HOME/e17_root), E17_REPO (depo kökü;
varsayılan bu betiğin deposu), E17_SNAPSHOT (sunucu SCALPER_* özetinin YEREL yolu; içeriği
asla basılmaz/kopyalanmaz, yalnız sha256'sı kilide girer; varsayılan $E17_ROOT/server-scalper-env.txt),
E17_PUSH_BRANCH (varsayılan main-18w3qz; yalnız `lock`ın bastığı push komutu için).
L'nin uzakta kayıtlı olduğu (§9.1) ağ varsa `git fetch origin <L>` ile her alt komutta (check-run hariç)
doğrulanır; ağ yoksa/uzak tanımsızsa atlanır, uzak "L yok" derse exit 3. Bu bir env değişkeniyle açılıp kapanmaz.

§9.1 yama usulü (patch-N) analizcide YOKTUR: DEV sonuçları varken `lock` kapalıdır (exit 4) ve analizcinin kendi
dosyası/eşikleri değişirse exit 3 verir; yama kaydı, iki sütunlu rapor ve diferansiyel test orkestratör işidir
(PLAN §9.1) ve ayrı, yeni bir analizci sürümü + yeni kilit referansı gerektirir (Ek A'da şeması tanımlı değildir).
`smoke` kapısı başarısız olursa çıkış kodu 5'tir (A.3 yalnız PASS/FAIL der; kod 5 "koşu/veri bütünlüğü").

Aşama sırası makinece zorlanır (exit 4): DEV check-run/dev-screen için bu kilide ait PASS'lı SMOKE.json; G6 kaplamaları
için dev-screen'de g6_needed; DIAG kaplamaları için DEV_DECISION (DIAG_FEE15 yalnız FEE2'yi kaçıran/ilerleyen arm);
VAL check-run için DEV_DECISION.advanced; FINAL check-run için VAL kazananı (yalnız kazanan ve B0).
`lock`: kilitli dosyalar depo .gitignore'u `*.json`ı yok saysa da `git add -f` ile AÇIKÇA eklenir ve commit ağacında
doğrulanır (eksikse commit geri alınır, push komutu basılmaz). Orkestratör `run/*.json`ı (Ek A.1) commit'lerken `-f` kullanmalıdır.

Etiket dili (Ek A.2):  <SET>_<varyant>[_<kaplama>][_Q<n>]   SET ∈ DEV|VAL|FINAL
  ve duman koşuları  SMOKE_<varyant>_<etiket>  (etiket: AYI|V1 = S1 parite [B0], h0|h1 = S2
  belirlenimcilik [S15, PYTHONHASHSEED 0/1], W = S3 SMOKE_W 30 gün, M1 = S3 R1 SMOKE_1M).

Yalnız stdlib. Gizli dosya (snapshot) içeriği hiçbir çıktıya yazılmaz.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import platform
import random
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ==========================================================================
# 0. SABİTLER (HEPSİ ÖNCEDEN SABİT; THRESH `thresholds_sha256` ile kilide girer)
# ==========================================================================
INF = float("inf")

#: Tüm sayısal eşikler ve istatistik sabitleri. Kapı fonksiyonları BU sözlüğü ÇAĞRI
#: ANINDA okur (yerel kopya/alias YOK): `tests/test_e17_analyzer.py::test_mutation_*`
#: her anahtarı gevşetip ilgili kapının FAIL→PASS döndüğünü doğrular.
THRESH: Dict[str, Any] = {
    # --- istatistik (§7) ---
    "BOOT_N": 19999,            # birincil bootstrap yineleme sayısı B
    "BOOT_N_HI": 199999,        # Monte-Carlo hata kuralı yeniden hesabı (§7.2)
    "MC_SE_MULT": 3.0,          # p ± 3·SE(p) aralığı
    "BLOCK_PRIMARY": 7,         # b = 7 gün (birincil)
    "BLOCK_SECONDARY": 28,      # b = 28 gün (G9 ikincil)
    "BLOCK_INFO": 14,           # b = 14 gün (yalnız bilgi)
    "PF_CAP": 5.0,              # PF = inf -> 5
    "PF_CI_LEVEL": 0.90,        # PF güven aralığı düzeyi (bilgi)
    "BORDERLINE_ABS": 0.005,    # eşiğe bu kadar yakın değer "SINIRDA" işaretlenir
    # --- rejim etiketi kuralı (§2.3; freeze) ---
    "REGIME_UP_PCT": 5.0,       # ay getirisi >= bu => UP
    "REGIME_DOWN_PCT": -5.0,    # ay getirisi <= bu => DOWN
    "REGIME_SHOCK_PCT": 6.0,    # günlük |kapanış/açılış-1| >= bu => ay "shock" (yalnız rapor)
    # --- kaldıraç payları / yoğunlaşma / maliyet çarpanları (önceden sabit) ---
    "LEV_LOW": 3,               # "3x payı" için kaldıraç (G0c1/G14c1)
    "LEV_HIGH": 20,             # "20x payı" için kaldıraç (G0c2/G14c2)
    "TOPN_G4": 5,               # G4b: en iyi N işlem hariç Σ R
    "TOPN_G12": 5,              # G12d: en iyi N işlem / T
    "FEE2_MULT": 2.0,           # ROB_FEE2 ücret+kayma çarpanı (check-run beklentisi)
    "FEE15_MULT": 1.5,          # DIAG_FEE15 çarpanı
    "BULL_PNL_FRAC": 0.8,       # CLAUDE.md kural #1: boğa PnL kaybı <= %20 => UP PnL >= 0.8 x B0
    "Z0_NET_PF": 1.0,           # Z0 okuması: net PF bu eşiğin altındaysa "ücret yiyor"
    # --- G0 / G14 geçerlilik (bütünlük kapısı) ---
    "G0_SILENT_DROP_MAX": 0.10,
    "G0_EOD_MAX": 8,
    "G0_LEV3_MAX": 0.10,
    "G0_LEV20_MAX": 0.50,       # YALNIZ DEV'de kapı; VAL'de rapor (kelepçe-yönetimli)
    "G0_RISK_BAND": 0.05,       # sabit-risk kontrolü: medyan riskin ±%5'i
    "G0_RISK_SHARE_MIN": 0.95,  # işlemlerin >= %95'i bu bantta
    # --- DEV (§8) ---
    "G1_N_MIN": 200,
    "G2_PF_MIN": 1.15,
    "G3_Q_MAX": 0.20,           # BH q (m = 6)
    "BH_M": 6,                  # aile büyüklüğü (sabit; G0/G1 başarısız p := 1.0)
    "G4_PF_EX_MONTH_MIN": 1.00,
    "G4_SUMR_EX_TOP5_MIN": 0.0,  # Σ R > 0 (kesin)
    "G5_GROUP_N_MIN": 40,
    "G5_GROUP_PF_MIN": 0.80,
    "G6_FEE2_PF_MIN": 1.00,
    "G6_DLY1_SUMR_MIN": 0.0,    # Σ R > 0 (kesin)
    "G6_TAKER_PF_MIN": 1.00,
    "DEV_MAX_ADVANCE": 2,
    "DEV_MAX_PER_TF": 1,
    # --- VAL (§8) ---
    "G7_N_MIN": 250,
    "G8_PF_MIN": 1.15,
    "G9_ALPHA_P7": 0.05,        # / k
    "G9_ALPHA_P28": 0.10,       # / k
    "G10A_GROUP_N_MIN": 40,
    "G10A_GROUP_PF_MIN": 0.90,
    "G10A_MIN_GROUPS": 2,
    "G10B_PF_MIN": 1.10,        # DEV+VAL DOWN ayları havuzlu
    "G10B_N_MIN": 100,
    "G11_MONTH_FRAC": 0.55,
    "G12_BEST_SYMBOL_SHARE_MAX": 0.50,
    "G12_BEST_MONTH_SHARE_MAX": 0.30,
    "G12_BEST_TRADE_SHARE_MAX": 0.15,
    "G12_BEST5_SHARE_MAX": 0.40,
    "G12_PF_EX_SYMBOL_MIN": 1.05,
    "G12_PF_EX_MONTH_MIN": 1.05,
    "G12_LOO_SYMBOL_PF_MIN": 1.00,
    "G13_CALMAR_MIN": 0.75,
    "G15_FEE2_PF_MIN": 1.00,
    "G15_DLY1_SUMR_MIN": 0.0,
    "G15_TAKER_PF_MIN": 1.00,
    "G15_WALL_SUMR_MIN": 0.0,
    "G16_HALF_N_MIN": 100,
    "G16_HALF_PF_MIN": 1.00,
    "VAL_MIN_DAYS": 365,        # §9.5: kalan VAL < 365 gün => VAL-YAPILAMADI
    # --- FINAL (§8) ---
    "G17_N_MIN": 20,
    "G17_DD_R_MAX": 25.0,
    "G17_VETO_PF": 0.90,
    "G17_CONSISTENT_PF": 1.00,
    "B0_ANCHOR_TRADES": 320,
    "B0_ANCHOR_PF_LO": 0.895,   # iki hanede 0.90 <=> 0.895 <= PF < 0.905
    "B0_ANCHOR_PF_HI": 0.905,
    # --- teşhis etiketleri (karar vermez) ---
    "BAR_ORDER_OPT_PF_MIN": 1.15,
    "FEE15_PF_MIN": 1.00,
    "Z0_GROSS_PF_EDGE": 1.05,
}

# --- pencereler (UTC, bitiş HARİÇ). `lock` variants.json "sets" ile eşitliği doğrular. ---
WINDOWS: Dict[str, Tuple[str, str]] = {
    "CAL": ("2026-03-01", "2026-06-01"),
    "DEV": ("2025-01-01", "2026-01-01"),
    "VAL": ("2023-01-01", "2024-11-18"),
    "FINAL": ("2026-09-03", "2026-10-08"),
    "SMOKE_W": ("2026-04-01", "2026-05-01"),
    "SMOKE_1M": ("2026-04-01", "2026-04-08"),
    "S1_AYI": ("2026-01-23", "2026-02-13"),
    "S1_V1": ("2025-01-06", "2025-01-27"),
}
DEV_QUARTERS: Tuple[Tuple[str, str], ...] = (
    ("2025-01-01", "2025-04-01"), ("2025-04-01", "2025-07-01"),
    ("2025-07-01", "2025-10-01"), ("2025-10-01", "2026-01-01"),
)
VAL_END = "2024-11-18"

FAMILY: Tuple[str, ...] = ("S5", "T5", "S15", "T15", "S30", "T30")
DESCRIPTIVE: Tuple[str, ...] = ("S60", "T60")
REFS_GATED: Tuple[str, ...] = ("S5G", "T5G")
QUARTER_REFS: Tuple[str, ...] = ("R1", "B0")
SYMBOLS_PINNED = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT"

G6_OVERLAYS: Tuple[str, ...] = ("ROB_FEE2", "ROB_DLY1", "ROB_TAKER")
VAL_OVERLAYS: Tuple[str, ...] = ("ROB_FEE2", "ROB_DLY1", "ROB_TAKER", "ROB_WALL")
DIAG_OVERLAYS: Tuple[str, ...] = ("DIAG_Z0", "DIAG_SBO", "DIAG_FEE15")
ALL_OVERLAYS: Tuple[str, ...] = G6_OVERLAYS + ("ROB_WALL",) + DIAG_OVERLAYS

SMOKE_TAGS = {"AYI": "S1_AYI", "V1": "S1_V1", "h0": "SMOKE_W", "h1": "SMOKE_W",
              "W": "SMOKE_W", "M1": "SMOKE_1M"}

#: S1 parite beklentileri (E16 results.csv; dürüstlük notu §5.2). (işlem, WR, PF, PnL, bar_dd)
SMOKE_S1_EXPECT: Dict[str, Tuple[int, float, float, float, Optional[float]]] = {
    "AYI": (149, 87.25, 2.0284, 5703.2322, 1428.4953),
    "V1": (194, 79.38, 0.8630, -1868.1712, None),
}

#: Dondurulmuş rejim etiketi sayımları (§2.3); `freeze` üretmezse aşama durur.
EXPECTED_LABEL_COUNTS: Dict[str, Dict[str, int]] = {
    "DEV": {"UP": 5, "FLAT": 4, "DOWN": 3},
    "VAL": {"UP": 11, "FLAT": 6, "DOWN": 5},
}

CANDLES_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24, "4h": 6, "1d": 1}
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
               "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
MS_PER_DAY = 86_400_000

LOCKDIR_REL = "docs/audits/2026-10-10-prereg-tf"
LOCK_FILES = ("PLAN.md", "variants.json", "labels_frozen.json", "cal_constants.json",
              "archive_probe.json")
SELF_REL = "scripts/e17/analyze_e17.py"
REQ_REL = "requirements.txt"
VARIANTS_SCHEMA = "e17-variants/2"

REPO: Path = Path(os.environ.get("E17_REPO") or Path(__file__).resolve().parents[2])
SELF_PATH: Path = Path(__file__).resolve()
#: Harness fonksiyonları (resolve_backtest_window, _WARMUP_BY_ROLE, ...) betiğin kendi deposundan alınır.
HARNESS_ROOT: Path = Path(__file__).resolve().parents[2]

STAGES = ("freeze", "lock", "verify-lock", "manifest", "smoke", "check-run",
          "dev-screen", "dev-decide", "diag", "val", "final", "decide")

NO_WINNER_SENTENCE = "Büyük grafikte C'nin para kazandırdığına dair kanıt yok."
SENTENCES = {
    "KAZANAN-HARNESS": "Testnet adayı (kod değişikliği ve §10 koşulları sonrası), gerçek para için kanıtlanmış değil.",
    "ADAY-FINAL-SONUÇSUZ": "Onay döneminde geçti, son kontrol istatistiksel olarak yetersiz; kazanan sayılmaz.",
    "DEV-YOK": NO_WINNER_SENTENCE, "VAL-YOK": NO_WINNER_SENTENCE,
    "FINAL-VETO": NO_WINNER_SENTENCE, "VAL-YAPILAMADI": "VAL altyapı hatası: karar verilemedi; FINAL'e dokunulmadı.",
}


# ==========================================================================
# 1. HATALAR ve KÜÇÜK YARDIMCILAR
# ==========================================================================
class E17Error(Exception):
    """Çıkış kodu taşıyan hata."""
    code = 1


class UsageError(E17Error):
    code = 2


class LockMismatch(E17Error):
    code = 3


class StageError(E17Error):
    code = 4


class IntegrityError(E17Error):
    code = 5


def need(x: Any, what: str) -> Any:
    """`assert`in yerine (python -O ile silinmez): None ise bütünlük hatası (exit 5)."""
    if x is None:
        raise IntegrityError(f"beklenen seri/değer yok: {what}")
    return x


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canon(obj: Any) -> str:
    """Kanonik JSON (sıralı anahtar, boşluksuz) — hash'ler için."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def jsonable(x: Any) -> Any:
    if isinstance(x, float):
        if x == INF:
            return "inf"
        if x == -INF:
            return "-inf"
        if x != x:
            return "nan"
        return x
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    return x


def dump_json(obj: Any) -> str:
    return json.dumps(jsonable(obj), ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_ms(d: str) -> int:
    x = dt.date.fromisoformat(d)
    return int(dt.datetime(x.year, x.month, x.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def ms_to_iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_to_date(ms: int) -> dt.date:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).date()


def month_of(ms: int) -> str:
    d = ms_to_date(ms)
    return f"{d.year:04d}-{d.month:02d}"


def atomic_write(path: Path, text: str) -> None:
    """Geçici dosya + os.replace (yarım dosya görünmez)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_once(path: Path, text: str) -> None:
    """TEK KEZ yazılan dosya: varsa StageError (ikinci yazım reddedilir). Atomik + münhasır."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise StageError(f"{path.name} zaten var; tek kez yazılır (ikinci yazım reddedildi)")
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.link(tmp, path)          # hedef varsa FileExistsError -> münhasır
    except FileExistsError:
        raise StageError(f"{path.name} zaten var; tek kez yazılır (ikinci yazım reddedildi)")
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def fmt_pf(x: float) -> str:
    """PF 4 ondalık; kırpma sınırına (PF_CAP) ulaşan/aşan değer "5.0000+" gösterilir (PF >= 5)."""
    if x == INF or x >= THRESH["PF_CAP"]:
        return f"{THRESH['PF_CAP']:.4f}+"
    return f"{x:.4f}"


def is_borderline(value: Any, threshold: Any) -> bool:
    if isinstance(value, bool) or isinstance(threshold, bool):
        return False
    if not isinstance(value, (int, float)) or not isinstance(threshold, (int, float)):
        return False
    if value in (INF, -INF) or value != value:
        return False
    return abs(value - threshold) <= THRESH["BORDERLINE_ABS"]


def row(gate: str, name: str, value: Any, threshold: Any, passed: bool, op: str = ">=",
        note: str = "") -> Dict[str, Any]:
    """Kapı satırı: değer yuvarlanmadan saklanır (basımda 6 hane), SINIRDA işareti hesaplanır."""
    return {"gate": gate, "name": name, "value": value, "threshold": threshold, "op": op,
            "pass": bool(passed), "borderline": is_borderline(value, threshold), "note": note}


def fmt_row(r: Dict[str, Any]) -> str:
    v, t = r["value"], r["threshold"]
    sv = f"{v:.6f}" if isinstance(v, float) and v not in (INF, -INF) else str(v)
    st = f"{t:.6f}" if isinstance(t, float) else str(t)
    mark = " SINIRDA" if r.get("borderline") else ""
    return f"{r['gate']} {r['name']}: {sv} {r['op']} {st} -> {'GEÇTİ' if r['pass'] else 'KALDI'}{mark}"


# ==========================================================================
# 2. GIT (kilit doğrulaması H/L blob'larından yapılır, çalışma ağacından değil)
# ==========================================================================
def git(args: Sequence[str], *, repo: Optional[Path] = None, check: bool = True,
        env: Optional[Dict[str, str]] = None) -> bytes:
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    p = subprocess.run(["git", "-C", str(repo or REPO), *args], capture_output=True, env=full_env)
    if check and p.returncode != 0:
        raise IntegrityError(f"git {' '.join(args)} başarısız: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return p.stdout


def git_text(args: Sequence[str], **kw: Any) -> str:
    return git(args, **kw).decode("utf-8", "replace").strip()


def git_show(rev: str, path: str) -> bytes:
    """`git show <rev>:<path>`; yoksa kilit uyuşmazlığı."""
    p = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{path}"], capture_output=True)
    if p.returncode != 0:
        raise LockMismatch(f"{rev[:12]}:{path} okunamadı (kilit referansı bozuk/eksik)")
    return p.stdout


def _commit_exists(rev: str) -> bool:
    return subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{rev}^{{commit}}"],
                          capture_output=True).returncode == 0


def git_tree_paths(rev: str) -> List[str]:
    out = git(["ls-tree", "-r", "-z", "--name-only", rev])
    return [x for x in out.decode("utf-8").split("\0") if x]


def code_paths(rev: str) -> List[str]:
    """Kilide giren H blob yolları (§5.3): scalper paketinin TÜM .py'si (içe aktarılan her modül),
    config.py, doldurucu, scripts/e17/*, tests/test_e17_*.py."""
    out = []
    for p in git_tree_paths(rev):
        if p.startswith("src/strategies/scalper/") and p.endswith(".py"):
            out.append(p)
        elif p in ("src/core/config.py", FILL_SCRIPT_REL):
            out.append(p)
        elif p.startswith("scripts/e17/"):
            out.append(p)
        elif p.startswith("tests/test_e17_") and p.endswith(".py"):
            out.append(p)
    return sorted(out)


FILL_SCRIPT_REL = "scripts/fill_klines_cache_from_vision.py"
REQUIRED_CODE = ("src/strategies/scalper/backtest.py", "src/core/config.py", FILL_SCRIPT_REL, SELF_REL)
REMOTE_TIMEOUT_S = 20
NOT_ON_REMOTE_MARKERS = ("not our ref", "couldn't find remote ref", "no such remote ref", "unadvertised object")


# ==========================================================================
# 3. LOCK (§9.1): LOCK.json + L commit'i + LOCK_REF.json; her alt komut yeniden doğrular
# ==========================================================================
def default_root() -> Path:
    return Path(os.environ.get("E17_ROOT") or (Path.home() / "e17_root"))


def snapshot_path(root: Path) -> Path:
    return Path(os.environ.get("E17_SNAPSHOT") or (root / "server-scalper-env.txt"))


def lockdir() -> Path:
    return REPO / LOCKDIR_REL


def thresholds_sha256() -> str:
    return sha256_bytes(canon(THRESH).encode("utf-8"))


def lockdir_files() -> Dict[str, str]:
    """Çalışma kopyasındaki kilitli dosyaların sha256'sı (yol: lockdir'e göre göreli)."""
    ld = lockdir()
    out: Dict[str, str] = {}
    for name in LOCK_FILES:
        p = ld / name
        if not p.is_file():
            raise StageError(f"{LOCKDIR_REL}/{name} yok; kilitlemeden önce hazırlanmalı")
        out[name] = sha256_file(p)
    for name in evidence_names():
        out[name] = sha256_file(ld / name)
    return out


def evidence_names() -> List[str]:
    """lockdir/evidence altındaki dosyalar (lockdir'e göre göreli; __pycache__/.pyc hariç)."""
    ld = lockdir()
    ev = ld / "evidence"
    if not ev.is_dir():
        return []
    return sorted(p.relative_to(ld).as_posix() for p in ev.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def earliest_bar_ms(triple: Sequence[str], start: str, end: str) -> int:
    """Harness'in kendi fonksiyonlarıyla (resolve_backtest_window, _WARMUP_BY_ROLE,
    _CANDLES_PER_DAY, kline_cache.window_start_ms) bir (üçlü x pencere) için en erken mum
    open_time'ı (ms) — ANALİTİK, ağsız."""
    return min(st for _iv, st, _e in role_windows(triple, start, end))


def role_windows(triple: Sequence[str], start: str, end: str) -> List[Tuple[str, int, int]]:
    """Bir üçlünün rol başına (interval, önbellek_başlangıç_ms, bitiş_ms) anahtarları — harness'in
    `gather_symbol_data` formülüyle (needed = gün x gün-içi-mum + rol ısınması). Bağlam == rejim
    (30m/4h/4h) olduğunda iki AYRI 4h anahtarı (+100 ve +250) döner (§2.1)."""
    if str(HARNESS_ROOT) not in sys.path:
        sys.path.insert(0, str(HARNESS_ROOT))
    from src.strategies.scalper import backtest as bt          # noqa: WPS433 (tembel içe aktarma)
    from src.strategies.scalper import kline_cache as kc
    eff_days, _s, end_ms = bt.resolve_backtest_window(0, start, end)
    out = []
    for interval, warm in zip(triple, bt._WARMUP_BY_ROLE):
        needed = eff_days * bt._CANDLES_PER_DAY[interval] + warm
        out.append((interval, kc.window_start_ms(interval, needed, end_ms), end_ms))
    return out


def all_triples(variants: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    seen: List[Tuple[str, str, str]] = []
    for v in variants["family"] + variants["descriptive"] + variants["references"]:
        t = tuple(v["triple"])
        if t not in seen:
            seen.append(t)         # type: ignore[arg-type]
    return seen


def earliest_bar_check(variants: Dict[str, Any]) -> Dict[str, str]:
    """{"<SET>:<e>/<c>/<r>": ISO}. DEV ve FINAL en erken barı VAL bitişinden SONRA olmalı."""
    sets = variants["sets"]
    out: Dict[str, str] = {}
    for st in ("DEV", "VAL", "FINAL"):
        s, e = sets[st]
        for tri in all_triples(variants):
            out[f"{st}:{'/'.join(tri)}"] = ms_to_iso(earliest_bar_ms(tri, s, e))
    return out


def earliest_bar_violations(check: Dict[str, str]) -> List[str]:
    limit = date_ms(VAL_END)
    bad = []
    for k, iso in check.items():
        if k.split(":")[0] in ("DEV", "FINAL"):
            ms = int(dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
            if ms <= limit:
                bad.append(f"{k}={iso}")
    return bad


def validate_variants_doc(v: Dict[str, Any]) -> None:
    if v.get("schema") != VARIANTS_SCHEMA:
        raise IntegrityError(f"variants.json şeması {v.get('schema')!r} != {VARIANTS_SCHEMA!r}")
    ids = {x["id"] for x in v["family"]}
    if tuple(x["id"] for x in v["family"]) != FAMILY:
        raise IntegrityError(f"variants.json family {sorted(ids)} != {FAMILY}")
    if tuple(x["id"] for x in v["descriptive"]) != DESCRIPTIVE:
        raise IntegrityError("variants.json descriptive beklenenden farklı")
    refs = {x["id"] for x in v["references"]}
    if refs != set(REFS_GATED) | set(QUARTER_REFS):
        raise IntegrityError(f"variants.json references {sorted(refs)} beklenenden farklı")
    if {o["id"] for o in v["overlays"]} != set(ALL_OVERLAYS):
        raise IntegrityError("variants.json overlays beklenenden farklı")
    for k, (s, e) in WINDOWS.items():
        if list(v["sets"].get(k, [])) != [s, e]:
            raise IntegrityError(f"variants.json sets[{k}] {v['sets'].get(k)} != {[s, e]}")
    if ",".join(v["symbols_order_pinned"].split(",")) != SYMBOLS_PINNED:
        raise IntegrityError("variants.json sembol sırası sabit değil")
    for x in v["family"] + v["descriptive"]:
        e, c, r = x["triple"]
        if e == c or e == r:
            raise IntegrityError(f"{x['id']}: rol-anahtarı çakışması (giriş = bağlam/rejim)")


def lock_ref_path(root: Path) -> Path:
    return root / "LOCK_REF.json"


def ledger_path(root: Path) -> Path:
    return root / "ledger.jsonl"


def ledger_labels(root: Path) -> List[str]:
    p = ledger_path(root)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line)["label"])
    return out


@dataclass
class Ctx:
    """Doğrulanmış kilit bağlamı. Tüm veri L blob'larından okunur (çalışma kopyası = L doğrulandı)."""
    root: Path
    l: str
    h: str
    lock: Dict[str, Any]
    variants: Dict[str, Any]
    labels: Dict[str, Any]
    vid: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for v in self.variants["family"] + self.variants["descriptive"] + self.variants["references"]:
            self.vid[v["id"]] = v

    def window(self, name: str) -> Tuple[str, str]:
        s, e = self.variants["sets"][name]
        if name == "VAL":
            cut = self.root / "VAL_WINDOW_CUT.json"
            if cut.exists():
                s = json.loads(cut.read_text(encoding="utf-8"))["start"]
        return s, e

    def window_days(self, name: str) -> int:
        s, e = self.window(name)
        return (date_ms(e) - date_ms(s)) // MS_PER_DAY


def snapshot_env_lines(root: Path) -> Dict[str, str]:
    """Yerel sunucu SCALPER_* özetinden KEY=VALUE çiftleri. İÇERİK hiçbir çıktıya yazılmaz; yalnız bellekte
    check-run karşılaştırması için kullanılır. Dosya yoksa {} (kilit doğrulaması varlığı ayrıca zorlar)."""
    sp = snapshot_path(root)
    out: Dict[str, str] = {}
    if not sp.is_file():
        return out
    for ln in sp.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def remote_check(l_commit: str) -> None:
    """§9.1: ağ varsa L'nin uzakta kayıtlı olduğunu doğrular (best-effort). Yerel depoda L zaten var olduğundan düz
    `git fetch origin <L>` ağa HİÇ gitmez (nesne yerelde: no-op) — bu yüzden L, BOŞ geçici bir bare depoya
    `origin` URL'sinden çekilir. Uzak "not our ref" derse LockMismatch; uzak tanımsızlığı/ağ hatası/zaman aşımı
    atlanır (kilidin kendisi yerelde zaten L blob'larından doğrulanır)."""
    try:
        r = subprocess.run(["git", "-C", str(REPO), "remote", "get-url", "origin"], capture_output=True,
                           timeout=REMOTE_TIMEOUT_S)
        if r.returncode != 0:
            return
        url = r.stdout.decode("utf-8", "replace").strip()
        with tempfile.TemporaryDirectory(prefix="e17remote") as td:
            if subprocess.run(["git", "init", "-q", "--bare", td], capture_output=True,
                              timeout=REMOTE_TIMEOUT_S).returncode != 0:
                return
            f = subprocess.run(["git", "-C", td, "fetch", "--quiet", "--no-tags", "--depth=1", url, l_commit],
                               capture_output=True, timeout=REMOTE_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return
    if f.returncode == 0:
        return
    err = f.stderr.decode("utf-8", "replace").lower()
    if any(m in err for m in NOT_ON_REMOTE_MARKERS):
        raise LockMismatch(f"L commit'i {l_commit[:12]} uzakta kayıtlı değil (push edilmedi mi?)")


def verify_lock(root: Path, remote: bool = True) -> Ctx:
    """Her alt komutun İLK işi (`freeze`/`lock` hariç). Hiçbir şey yazmaz. Uyuşmazlık -> LockMismatch (3).
    LOCK.json/LOCK_REF.json'un bozuk/eksik alanları da (KeyError/ValueError/...) kilit uyuşmazlığıdır (exit 3)."""
    try:
        return _verify_lock(root, remote)
    except E17Error:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, OSError) as exc:
        raise LockMismatch(f"kilit kaydı okunamadı/bozuk: {type(exc).__name__}: {exc}")


def _verify_lock(root: Path, remote: bool) -> Ctx:
    ref_p = lock_ref_path(root)
    if not ref_p.exists():
        raise LockMismatch(f"{ref_p} yok: kilit kaydı bulunmuyor (önce `lock`)")
    try:
        ref = json.loads(ref_p.read_text(encoding="utf-8"))
        l_commit = ref["l"]
        want_sha = ref["lock_sha256"]
    except (ValueError, KeyError) as exc:
        raise LockMismatch(f"LOCK_REF.json bozuk: {exc}")
    if not _commit_exists(l_commit):
        raise LockMismatch(f"L commit'i {l_commit[:12]} depoda yok")
    raw = git_show(l_commit, f"{LOCKDIR_REL}/LOCK.json")
    if sha256_bytes(raw) != want_sha:
        raise LockMismatch("L'deki LOCK.json'ın sha256'sı LOCK_REF.json kaydıyla eşleşmiyor")
    # çalışma kopyasındaki LOCK.json de L'deki blob'la bayt-bayt aynı olmalı (§9.1: farklıysa exit 3)
    wl = lockdir() / "LOCK.json"
    if not wl.is_file() or sha256_file(wl) != want_sha:
        raise LockMismatch("çalışma kopyasındaki LOCK.json, L'deki blob'dan farklı/yok")
    lock = json.loads(raw.decode("utf-8"))
    h = lock["h_commit"]
    if not _commit_exists(h):
        raise LockMismatch(f"H commit'i {h[:12]} depoda yok")
    if git_text(["rev-parse", f"{h}^{{tree}}"]) != lock["h_tree"]:
        raise LockMismatch("H ağaç hash'i kilitle eşleşmiyor")
    # H blob'ları (kod): çalışma ağacı DEĞİL
    cur = code_paths(h)
    if sorted(lock["code_blobs"]) != cur:
        extra = sorted(set(cur) ^ set(lock["code_blobs"]))
        raise LockMismatch(f"H'deki kod dosya kümesi kilitten farklı: {extra[:6]}")
    for p, want in lock["code_blobs"].items():
        if sha256_bytes(git_show(h, p)) != want:
            raise LockMismatch(f"H blob hash uyuşmazlığı: {p}")
    # kilitli veri dosyaları: hem L blob'u hem çalışma kopyası
    ld = lockdir()
    present = {n for n in LOCK_FILES if (ld / n).is_file()} | set(evidence_names())
    if present != set(lock["files"]):
        raise LockMismatch(f"lockdir dosya kümesi kilitten farklı: {sorted(present ^ set(lock['files']))[:6]}")
    for name, want in lock["files"].items():
        if sha256_bytes(git_show(l_commit, f"{LOCKDIR_REL}/{name}")) != want:
            raise LockMismatch(f"L blob hash uyuşmazlığı: {name}")
        wp = ld / name
        if not wp.is_file() or sha256_file(wp) != want:
            raise LockMismatch(f"çalışma kopyası kilitten farklı: {LOCKDIR_REL}/{name}")
    # analizcinin KENDİ dosyası
    want_self = lock["code_blobs"].get(SELF_REL)
    if want_self is None or sha256_file(SELF_PATH) != want_self:
        raise LockMismatch("çalışan analyze_e17.py, H'deki blob'la eşleşmiyor")
    if thresholds_sha256() != lock["thresholds_sha256"]:
        raise LockMismatch("THRESH serileştirmesi kilitle eşleşmiyor")
    if sha256_bytes(git_show(h, REQ_REL)) != lock["requirements_sha256"]:
        raise LockMismatch("requirements.txt hash'i kilitle eşleşmiyor")
    cur_py = platform.python_version().split(".")[:2]
    if cur_py != lock["python_version"].split(".")[:2]:
        raise LockMismatch(f"Python sürümü {platform.python_version()} != kilit {lock['python_version']}")
    sp = snapshot_path(root)
    if not sp.is_file() or sha256_file(sp) != lock["snapshot_sha256"]:
        raise LockMismatch("sunucu SCALPER_* özeti (yerel) yok veya sha256'sı kilitle eşleşmiyor")
    variants = json.loads(git_show(l_commit, f"{LOCKDIR_REL}/variants.json").decode("utf-8"))
    vs_sha = (variants.get("snapshot") or {}).get("sha256")
    if vs_sha is not None and vs_sha != lock["snapshot_sha256"]:
        raise LockMismatch("variants.json snapshot.sha256 yerel sunucu özetinin kilitli hash'iyle eşleşmiyor")
    labels = json.loads(git_show(l_commit, f"{LOCKDIR_REL}/labels_frozen.json").decode("utf-8"))
    if remote:
        remote_check(l_commit)
    return Ctx(root=root, l=l_commit, h=h, lock=lock, variants=variants, labels=labels)


def month_labels(ctx_or_labels: Any) -> Dict[str, str]:
    labels = ctx_or_labels.labels if isinstance(ctx_or_labels, Ctx) else ctx_or_labels
    return {k: v["label"] for k, v in labels.items() if re.fullmatch(r"\d{4}-\d{2}", k)}


def cmd_lock(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("lock hedef almaz")
    if any(lb.startswith("DEV_") for lb in ledger_labels(root)):
        raise StageError("ledger'da DEV koşusu var: `lock` kapalı (§9.1; yama usulü kullanılır)")
    # çalışma ağacı (lockdir ve gitignore'lu dosyalar hariç) temiz olmalı
    dirty = []
    for line in git(["status", "--porcelain", "--untracked-files=all"]).decode("utf-8", "replace").splitlines():
        path = line[3:].strip().strip('"')
        if not path.startswith(LOCKDIR_REL + "/"):
            dirty.append(path)
    if dirty:
        raise StageError(f"çalışma ağacı kirli (lockdir dışı): {dirty[:5]}")
    h = git_text(["rev-parse", "HEAD"])
    files = lockdir_files()
    variants = json.loads((lockdir() / "variants.json").read_text(encoding="utf-8"))
    validate_variants_doc(variants)
    lab = json.loads((lockdir() / "labels_frozen.json").read_text(encoding="utf-8"))
    for set_name, want in EXPECTED_LABEL_COUNTS.items():
        got = {k: (lab.get("counts", {}).get(set_name) or {}).get(k) for k in want}
        if got != want:
            raise IntegrityError("labels_frozen.json sayımları DEV 5/4/3 ve VAL 11/6/5 ile eşleşmiyor")
    check = earliest_bar_check(variants)
    bad = earliest_bar_violations(check)
    if bad:
        raise IntegrityError(f"sızıntı duvarı: en erken bar VAL bitişinden ({VAL_END}) sonra değil: {bad}")
    paths = code_paths(h)
    for need in REQUIRED_CODE:
        if need not in paths:
            raise StageError(f"H ({h[:12]}) ağacında zorunlu kod dosyası yok: {need}")
    blobs = {p: sha256_bytes(git_show(h, p)) for p in paths}
    sp = snapshot_path(root)
    if not sp.is_file():
        raise StageError("sunucu SCALPER_* özeti yerelde yok (E17_SNAPSHOT / $E17_ROOT/server-scalper-env.txt)")
    snap_sha = sha256_file(sp)
    vs_sha = (variants.get("snapshot") or {}).get("sha256")
    if vs_sha is not None and vs_sha != snap_sha:
        raise IntegrityError("variants.json snapshot.sha256 yerel sunucu özetinin sha256'sıyla eşleşmiyor")
    doc = {
        "h_commit": h, "h_tree": git_text(["rev-parse", f"{h}^{{tree}}"]),
        "l_note": "L = bu LOCK.json'u içeren, uzak dala itilmiş kilit commit'i; sha LOCK_REF.json'dadır (§9.1)",
        "python_version": platform.python_version(),
        "requirements_sha256": sha256_bytes(git_show(h, REQ_REL)),
        "files": files, "code_blobs": blobs,
        "snapshot_sha256": snap_sha,
        "thresholds_sha256": thresholds_sha256(),
        "earliest_bar_check": check,
        "created_utc": utc_now(),
    }
    lock_text = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (lockdir() / "LOCK.json").write_text(lock_text, encoding="utf-8")
    # Depo .gitignore'u `*.json`ı yok sayar: düz `git add` LOCK.json/variants.json/... dosyalarını SESSİZCE atlar
    # ve eksik bir kilit commit'i üretir. Bu yüzden kilitli dosyalar AÇIKÇA ve `-f` ile eklenir; eklenenler
    # indekste ve commit ağacında DOĞRULANIR (eksikse push komutu basılmadan hata).
    add_paths = [f"{LOCKDIR_REL}/{n}" for n in [*files, "LOCK.json"]]
    git(["add", "-f", "--", *add_paths])
    staged = {x for x in git(["ls-files", "-z", "--", *add_paths]).decode("utf-8").split("\0") if x}
    absent = [x for x in add_paths if x not in staged]
    if absent:
        raise IntegrityError(f"kilitli dosyalar indekse girmedi (gitignore?): {absent[:4]}")
    lock_sha = sha256_bytes(lock_text.encode("utf-8"))
    ident = {"GIT_AUTHOR_NAME": "e17-lock", "GIT_AUTHOR_EMAIL": "e17-lock@localhost",
             "GIT_COMMITTER_NAME": "e17-lock", "GIT_COMMITTER_EMAIL": "e17-lock@localhost"}
    have_ident = subprocess.run(["git", "-C", str(REPO), "config", "user.email"], capture_output=True).stdout.strip()
    git(["commit", "-q", "-m", f"E17 kilit: H={h} sha256(LOCK.json)={lock_sha}", "--", *add_paths],
        env=None if have_ident else ident)
    l_commit = git_text(["rev-parse", "HEAD"])
    in_tree = set(git_tree_paths(l_commit))
    lost = [x for x in add_paths if x not in in_tree]
    if lost:
        git(["reset", "-q", "--soft", "HEAD~1"])
        raise IntegrityError(f"kilit commit'inde eksik dosya (commit geri alındı): {lost[:4]}")
    ref_p = lock_ref_path(root)
    hist: List[Dict[str, Any]] = []
    if ref_p.exists():
        hist = json.loads(ref_p.read_text(encoding="utf-8")).get("history", [])
    hist.append({"kind": "lock" if not hist else f"L{len(hist) + 1}", "l": l_commit, "h": h,
                 "lock_sha256": lock_sha, "utc": utc_now()})
    atomic_write(ref_p, dump_json({"l": l_commit, "h": h, "lock_sha256": lock_sha,
                                   "lock_path": f"{LOCKDIR_REL}/LOCK.json", "pushed": False,
                                   "history": hist}))
    branch = os.environ.get("E17_PUSH_BRANCH", "main-18w3qz")
    print(f"KİLİT OLUŞTURULDU (yerel commit): L={l_commit} H={h} sha256(LOCK.json)={lock_sha}")
    print("PUSH ORKESTRATÖRÜN İŞİDİR; itilemezse kilit YOKTUR. Komut:")
    print(f"  git -C {shlex.quote(str(REPO))} push origin {l_commit}:refs/heads/{shlex.quote(branch)}")
    return 0


def cmd_verify_lock(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("verify-lock hedef almaz")
    ctx = verify_lock(root)
    print(f"KİLİT DOĞRULANDI: L={ctx.l[:12]} H={ctx.h[:12]} dosya={len(ctx.lock['files'])} kod={len(ctx.lock['code_blobs'])}")
    return 0


# ==========================================================================
# 4. FREEZE: rejim etiketleri (§2.3) — strateji koşulmadan, yalnız BTCUSDT 1d piyasa verisinden
# ==========================================================================
def regime_label(ret_pct: float) -> str:
    return ("UP" if ret_pct >= THRESH["REGIME_UP_PCT"]
            else ("DOWN" if ret_pct <= THRESH["REGIME_DOWN_PCT"] else "FLAT"))


def load_btc_1d(cache_dir: Path) -> Dict[int, Dict[str, Any]]:
    """`BTCUSDT_1d_<başlangıç>_<bitiş>.json.gz` dosyalarını birleştirir (open_time -> mum)."""
    by_open: Dict[int, Dict[str, Any]] = {}
    files = sorted(Path(cache_dir).glob("BTCUSDT_1d_*_*.json.gz"))
    if not files:
        raise IntegrityError(f"{cache_dir} altında BTCUSDT 1d önbelleği yok")
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for c in json.load(f):
                t = int(c["open_time"])
                if t in by_open and (float(by_open[t]["open"]), float(by_open[t]["close"])) != \
                        (float(c["open"]), float(c["close"])):
                    raise IntegrityError(f"BTC 1d mum çakışması farklı değerlerle: {ms_to_iso(t)}")
                by_open[t] = c
    return by_open


def _months_in(start: str, end: str) -> List[Tuple[str, dt.date, dt.date, bool]]:
    """[start,end) içindeki takvim ayları: (YYYY-MM, ilk gün, son gün+1, kısmi mi)."""
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    out = []
    cur = dt.date(s.year, s.month, 1)
    while cur < e:
        nxt = dt.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        a, b = max(cur, s), min(nxt, e)
        out.append((f"{cur.year:04d}-{cur.month:02d}", a, b, not (a == cur and b == nxt)))
        cur = nxt
    return out


def build_labels(by_open: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Ay etiketleri + sayımlar. DEV 5/4/3 ve VAL 11/6/5 üretilmezse IntegrityError (dosya yazılmaz)."""
    doc: Dict[str, Any] = {}
    counts: Dict[str, Dict[str, int]] = {}
    for set_name in ("DEV", "VAL"):
        s, e = WINDOWS[set_name]
        cnt = {"UP": 0, "FLAT": 0, "DOWN": 0, "months": 0, "partial_months": 0}
        for key, a, b, partial in _months_in(s, e):
            days = [date_ms(a.isoformat()) + i * MS_PER_DAY for i in range((b - a).days)]
            miss = [t for t in days if t not in by_open]
            if miss:
                raise IntegrityError(f"BTC 1d: {key} için {len(miss)} gün eksik (ilk: {ms_to_iso(miss[0])})")
            cs = [by_open[t] for t in days]
            ret = (float(cs[-1]["close"]) / float(cs[0]["open"]) - 1.0) * 100.0
            shock = any(abs(float(c["close"]) / float(c["open"]) - 1.0) * 100.0 >= THRESH["REGIME_SHOCK_PCT"] for c in cs)
            doc[key] = {"ret_pct": ret, "label": regime_label(ret), "shock": shock,
                        "partial": partial, "set": set_name}
            if partial:
                cnt["partial_months"] += 1       # kısmi ay sayıma GİRMEZ (§2.3)
            else:
                cnt[doc[key]["label"]] += 1
                cnt["months"] += 1
        counts[set_name] = cnt
    for set_name, want in EXPECTED_LABEL_COUNTS.items():
        got = {k: counts[set_name][k] for k in want}
        if got != want:
            raise IntegrityError(f"{set_name} rejim sayımı {got} != beklenen {want}; aşama durur")
    doc["counts"] = counts
    doc["rule"] = {
        "series": "BTCUSDT 1d, takvim ayı; getiri = son gün kapanış / ilk gün açılış - 1",
        "UP": f">= +{THRESH['REGIME_UP_PCT']}%", "DOWN": f"<= {THRESH['REGIME_DOWN_PCT']}%", "FLAT": "aksi",
        "shock": f"ayda herhangi günlük |kapanış/açılış-1| >= {THRESH['REGIME_SHOCK_PCT']}% (raporlanır, kullanılmaz)",
        "assignment": "işlem, GİRİŞ (dolum) barı open_time'ının UTC ayının etiketini alır",
        "partial_months": "kısmi ay (VAL Kasım 1-17) kendi getirisiyle etiketlenir; G10 gruplarına girer, M'ye girmez",
    }
    return doc


def cmd_freeze(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("freeze hedef almaz")
    out = lockdir() / "labels_frozen.json"
    if out.exists():
        raise StageError(f"{out.name} zaten var; dondurma tek seferliktir")
    doc = build_labels(load_btc_1d(root / "cache"))
    doc["frozen_utc"] = utc_now()
    write_once(out, dump_json(doc))
    c = doc["counts"]
    print(f"YAZILDI {out}  DEV {c['DEV']['UP']}/{c['DEV']['FLAT']}/{c['DEV']['DOWN']} (UP/FLAT/DOWN)  "
          f"VAL {c['VAL']['UP']}/{c['VAL']['FLAT']}/{c['VAL']['DOWN']}")
    return 0


# ==========================================================================
# 5. İSTATİSTİK (§7): PF, BH, günlük vektör, null-merkezli studentize dairesel blok bootstrap
# ==========================================================================
def pf_of(values: Sequence[float]) -> float:
    """Profit factor = pozitif toplam / |negatif toplam|. Kayıp yok + kâr var => inf; boş/0 => 0."""
    pos = sum(v for v in values if v > 0)
    neg = -sum(v for v in values if v < 0)
    if neg > 0:
        return pos / neg
    return INF if pos > 0 else 0.0


def cap_pf(x: float) -> float:
    return THRESH["PF_CAP"] if x == INF else x


def bh_qvalues(p_values: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg q-değerleri (girdi sırası korunur). `multitest.benjamini_hochberg` ile
    3 sabit vektörde 6 hane yuvarlamasıyla birebir aynı olmalıdır (parite testi)."""
    vals = []
    for x in p_values:
        try:
            v = float(x)
        except (TypeError, ValueError):
            v = 1.0
        if v != v:
            v = 1.0
        vals.append(min(1.0, max(0.0, v)))
    m = len(vals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: (vals[i], i))
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, vals[i] * m / rank)
        q[i] = min(running, 1.0)
    return q


def seed_for(stage: str, arm: str, block: int, hi: bool = False) -> int:
    """Tohum = sha256("E17|<aşama>|<arm>|b<b>"[|hi]) ilk 8 bayt."""
    key = f"E17|{stage}|{arm}|b{block}" + ("|hi" if hi else "")
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


def _tstat(s1: float, s2: float, n: int) -> float:
    """Studentize ortalama (ddof=1) için toplam ve kareler toplamından t. sd≈0 -> 0 / ±inf."""
    mean = s1 / n
    var = (s2 - s1 * s1 / n) / (n - 1)
    if var <= 1e-12 * (s2 / n) or var <= 0.0:
        return 0.0 if abs(mean) <= 1e-12 else math.copysign(INF, mean)
    return mean / math.sqrt(var / n)


def t_obs_of(x: Sequence[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    s1 = sum(x)
    s2 = sum(v * v for v in x)
    return _tstat(s1, s2, n)


def _prefix2(xs: Sequence[float]) -> Tuple[List[float], List[float]]:
    """Çift uzunlukta (dairesel) kümülatif toplam ve kareler toplamı."""
    p1, p2 = [0.0], [0.0]
    a = a2 = 0.0
    for v in list(xs) + list(xs):
        a += v
        a2 += v * v
        p1.append(a)
        p2.append(a2)
    return p1, p2


def bootstrap_p(x: Sequence[float], block: int, seed: int, boot_n: Optional[int] = None) -> Tuple[float, float]:
    """Null-merkezli, tek-yönlü, studentize dairesel blok bootstrap p-değeri (§7.2).

    x̃ = x − mean(x); ceil(D/b) blok başlangıcı 0..D−1 düzgün (sarmalı), birleştir, D'ye kırp;
    t* = mean*/(sd*/√D); p = (1 + #{t* ≥ t_obs}) / (B + 1). RNG stdlib random.Random(seed),
    indeks int(random()*D). Blok toplamları kümülatif toplamla O(1) hesaplanır (naif birleştirmeyle
    AYNI sonuç; paritesi test edilir). sd=0 veya işlem yok -> p := 1.0.
    Döner: (p, t_obs)."""
    boot_n = int(boot_n or THRESH["BOOT_N"])
    d = len(x)
    if d < 2:
        return 1.0, 0.0
    mean = sum(x) / d
    sd2 = sum((v - mean) ** 2 for v in x) / (d - 1)
    if sd2 <= 0.0 or all(v == x[0] for v in x):
        return 1.0, 0.0
    t_obs = t_obs_of(x)
    xc = [v - mean for v in x]
    b = max(1, min(int(block), d))
    nblocks = -(-d // b)
    p1, p2 = _prefix2(xc)
    rnd = random.Random(seed).random
    cnt = 0
    for _ in range(boot_n):
        s1 = s2 = 0.0
        remaining = d
        for _k in range(nblocks):
            st = int(rnd() * d)
            ln = b if remaining >= b else remaining
            s1 += p1[st + ln] - p1[st]
            s2 += p2[st + ln] - p2[st]
            remaining -= ln
        if _tstat(s1, s2, d) >= t_obs:
            cnt += 1
    return (1 + cnt) / (boot_n + 1), t_obs


def bootstrap_p_naive(x: Sequence[float], block: int, seed: int, boot_n: int) -> float:
    """Referans (yavaş) uygulama: gerçekten birleştirir ve kırpar. YALNIZ parite testi içindir."""
    d = len(x)
    mean = sum(x) / d
    xc = [v - mean for v in x]
    t_obs = t_obs_of(x)
    nblocks = -(-d // block)
    rnd = random.Random(seed).random
    cnt = 0
    for _ in range(boot_n):
        s: List[float] = []
        for _k in range(nblocks):
            st = int(rnd() * d)
            s.extend(xc[(st + j) % d] for j in range(block))
        s = s[:d]
        if _tstat(sum(s), sum(v * v for v in s), d) >= t_obs:
            cnt += 1
    return (1 + cnt) / (boot_n + 1)


def pf_confidence(pos: Sequence[float], neg: Sequence[float], block: int, seed: int,
                  boot_n: Optional[int] = None) -> Tuple[float, float]:
    """Aynı dairesel blok yeniden örneklemesiyle PF %90 GA ((günlük pozitif, günlük negatif) çiftleri).
    BİLGİ amaçlı; karar vermez. Kayıp yok -> PF_CAP."""
    boot_n = int(boot_n or THRESH["BOOT_N"])
    d = len(pos)
    if d == 0:
        return 0.0, 0.0
    b = max(1, min(int(block), d))
    nblocks = -(-d // b)
    pp, _ = _prefix2(pos)
    pn, _ = _prefix2(neg)
    rnd = random.Random(seed).random
    cap = THRESH["PF_CAP"]
    vals = []
    for _ in range(boot_n):
        sp = sn = 0.0
        remaining = d
        for _k in range(nblocks):
            st = int(rnd() * d)
            ln = b if remaining >= b else remaining
            sp += pp[st + ln] - pp[st]
            sn += pn[st + ln] - pn[st]
            remaining -= ln
        if sn < 0:
            vals.append(min(cap, sp / -sn))
        else:
            vals.append(cap if sp > 0 else 0.0)
    vals.sort()
    a = (1.0 - THRESH["PF_CI_LEVEL"]) / 2.0
    lo = vals[int(a * boot_n)]
    hi = vals[min(boot_n - 1, int((1.0 - a) * boot_n))]
    return lo, hi


def refine_p(p: float, decide: Callable[[float], Any], recompute: Callable[[], float],
             boot_n: Optional[int] = None) -> Tuple[float, bool]:
    """Monte-Carlo hata kuralı (§7.2): karar p ± 3·SE(p) içinde ters dönebiliyorsa `recompute()`
    (B = BOOT_N_HI, `…|hi` tohumu) ile p yeniden hesaplanır. Döner (p, yeniden_hesaplandı_mı)."""
    boot_n = int(boot_n or THRESH["BOOT_N"])
    se = math.sqrt(max(0.0, p * (1.0 - p) / boot_n))
    lo, hi = max(0.0, p - THRESH["MC_SE_MULT"] * se), min(1.0, p + THRESH["MC_SE_MULT"] * se)
    if decide(lo) != decide(hi):
        return recompute(), True
    return p, False


# ==========================================================================
# 6. İŞLEM / SERİ MODELİ (R birimi, giriş ayı, çıkış günü)
# ==========================================================================
@dataclass(frozen=True)
class Tr:
    symbol: str
    direction: str
    entry_ms: int
    exit_ms: int
    r: float              # net R = pnl / (margin_usdt * S / 100)
    pnl: float            # USD net
    lev: int
    reason: str
    month: str            # GİRİŞ (dolum) barı open_time'ının UTC ayı
    exit_day: dt.date     # UTC çıkış günü
    risk: float = 0.0     # margin_usdt * S / 100 (USD)


@dataclass
class Series:
    """Bir (varyant x kaplama x set) için işlem serisi; B0/R1 çeyrek koşuları tek seriye birleşir."""
    label: str
    arm: str
    set: str
    overlay: str
    s_arm: float
    trades: List[Tr]
    windows: List[Tuple[dt.date, int]]            # (başlangıç, gün) — koşu başına
    missed: Dict[str, int]
    dd_usd: float                                  # bar_max_drawdown (çeyrekte maksimum)
    risk_median: float
    diagnostics: Optional[Dict[str, Any]] = None
    run_labels: List[str] = field(default_factory=list)
    reported_trades: int = 0

    @property
    def days(self) -> int:
        return sum(d for _s, d in self.windows)

    @property
    def n(self) -> int:
        return len(self.trades)

    def rs(self) -> List[float]:
        return [t.r for t in self.trades]


def daily_vector(trades: Sequence[Tr], windows: Sequence[Tuple[dt.date, int]],
                 value: Callable[[Tr], float] = lambda t: t.r) -> List[float]:
    """Günlük net-R vektörü, ÇIKIŞ gününe göre (UTC); işlemsiz gün = 0; D = pencere gün sayısı."""
    offs, tot = [], 0
    for s, d in windows:
        offs.append((s, d, tot))
        tot += d
    vec = [0.0] * tot
    for t in trades:
        for s, d, off in offs:
            k = (t.exit_day - s).days
            if 0 <= k < d:
                vec[off + k] += value(t)
                break
        else:
            raise IntegrityError(f"{t.symbol}: çıkış günü {t.exit_day} hiçbir pencerede değil (EOD dahil sayılmalı)")
    return vec


def daily_pos_neg(trades: Sequence[Tr], windows: Sequence[Tuple[dt.date, int]]) -> Tuple[List[float], List[float]]:
    pos = daily_vector([t for t in trades if t.r > 0], windows)
    neg = daily_vector([t for t in trades if t.r < 0], windows)
    return pos, neg


def p_for(series: Series, stage: str, block: int, hi: bool = False,
          boot_n: Optional[int] = None) -> Tuple[float, float]:
    x = daily_vector(series.trades, series.windows)
    n = boot_n or (THRESH["BOOT_N_HI"] if hi else THRESH["BOOT_N"])
    return bootstrap_p(x, block, seed_for(stage, series.arm, block, hi), n)


# --- yoğunlaşma / rejim / kaldırma fonksiyonları (G4, G5, G10, G11, G12) -----------------
def pf_r(trades: Iterable[Tr]) -> float:
    """R üzerinden PF; kayıpsız seri (inf) PF_CAP'e KIRPILIR (§5.3). Ham inf yalnız check-run çapraz
    kontrolünde (pf_of) kullanılır; JSON/CSV/DECISION.md'ye sonlu değer girer."""
    return cap_pf(pf_of([t.r for t in trades]))


def sum_r(trades: Iterable[Tr]) -> float:
    return sum(t.r for t in trades)


def group_sums(trades: Iterable[Tr], key: Callable[[Tr], str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for t in trades:
        out[key(t)] = out.get(key(t), 0.0) + t.r
    return out


def argmax_key(d: Dict[str, float]) -> Optional[str]:
    """En büyük değerin anahtarı; eşitlikte alfabetik ilk (belirlenimci)."""
    if not d:
        return None
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def best_month(trades: Sequence[Tr]) -> Optional[str]:
    return argmax_key(group_sums(trades, lambda t: t.month))


def best_symbol(trades: Sequence[Tr]) -> Optional[str]:
    return argmax_key(group_sums(trades, lambda t: t.symbol))


def top_trades(trades: Sequence[Tr], k: int) -> List[Tr]:
    """En iyi k işlem (R'ye göre); eşitlikte erken giriş önce (belirlenimci)."""
    return sorted(trades, key=lambda t: (-t.r, t.entry_ms, t.symbol))[:k]


def pf_excluding_month(trades: Sequence[Tr], month: Optional[str]) -> float:
    return pf_r(t for t in trades if t.month != month)


def pf_excluding_symbol(trades: Sequence[Tr], symbol: Optional[str]) -> float:
    return pf_r(t for t in trades if t.symbol != symbol)


def sum_excluding_top(trades: Sequence[Tr], k: int) -> float:
    ex = {id(t) for t in top_trades(trades, k)}
    return sum(t.r for t in trades if id(t) not in ex)


def loo_symbol_pfs(trades: Sequence[Tr]) -> Dict[str, float]:
    return {s: pf_excluding_symbol(trades, s) for s in sorted({t.symbol for t in trades})}


def regime_groups(trades: Iterable[Tr], labels: Dict[str, str]) -> Dict[str, List[Tr]]:
    g: Dict[str, List[Tr]] = {"UP": [], "FLAT": [], "DOWN": []}
    for t in trades:
        lb = labels.get(t.month)
        if lb in g:
            g[lb].append(t)
    return g


def full_months_of(window: Tuple[str, str]) -> List[str]:
    return [k for k, _a, _b, partial in _months_in(*window) if not partial]


# ==========================================================================
# 7. ETİKET DİLİ, LEDGER, KOŞU YÜKLEME
# ==========================================================================
@dataclass(frozen=True)
class LabelInfo:
    label: str
    set: str                 # DEV|VAL|FINAL|SMOKE
    variant: str
    overlay: str             # "" = taban
    quarter: int             # 0 = yok
    tag: str = ""            # yalnız SMOKE
    window_set: str = ""     # variants["sets"] anahtarı (çeyrek koşuları için DEV)


def _all_variants(v: Dict[str, Any]) -> List[Dict[str, Any]]:
    return v["family"] + v["descriptive"] + v["references"]


def parse_label(label: str, variants: Dict[str, Any]) -> LabelInfo:
    allv = _all_variants(variants)
    ids = sorted([v["id"] for v in allv], key=lambda s: -len(s))
    m = re.fullmatch(r"(DEV|VAL|FINAL|SMOKE)_(.+)", label)
    if not m:
        raise UsageError(f"etiket ayrıştırılamadı: {label!r}")
    st, rest = m.group(1), m.group(2)
    variant = next((i for i in ids if rest == i or rest.startswith(i + "_")), None)
    if variant is None:
        raise UsageError(f"bilinmeyen varyant: {label!r}")
    rest = rest[len(variant):].lstrip("_")
    role = next(v["role"] for v in allv if v["id"] == variant)
    if st == "SMOKE":
        if rest not in SMOKE_TAGS:
            raise UsageError(f"SMOKE etiketi {sorted(SMOKE_TAGS)} içinden olmalı: {label!r}")
        ok = {"AYI": {"B0"}, "V1": {"B0"}, "h0": {"S15"}, "h1": {"S15"},
              "W": set(FAMILY) | set(DESCRIPTIVE), "M1": {"R1"}}[rest]
        if variant not in ok:
            raise UsageError(f"SMOKE_{variant}_{rest} geçerli bir duman koşusu değil")
        return LabelInfo(label, "SMOKE", variant, "", 0, rest, SMOKE_TAGS[rest])
    quarter = 0
    qm = re.search(r"(^|_)Q([1-4])$", rest)
    if qm:
        quarter = int(qm.group(2))
        rest = rest[: qm.start()]
    overlay = rest
    if overlay and overlay not in ALL_OVERLAYS:
        raise UsageError(f"bilinmeyen kaplama: {label!r}")
    is_q = variant in QUARTER_REFS
    if st == "DEV" and is_q != bool(quarter):
        raise UsageError(f"{variant} DEV'de çeyrek (_Q1..Q4) koşulur; diğerleri çeyreksiz: {label!r}")
    if st != "DEV" and quarter:
        raise UsageError(f"yalnız DEV çeyrek koşusu alır: {label!r}")
    allowed: Dict[Tuple[str, str], Tuple[str, ...]] = {
        ("DEV", "family"): ("",) + G6_OVERLAYS + DIAG_OVERLAYS,
        ("DEV", "descriptive"): ("",), ("DEV", "reference_gated"): ("",),
        ("DEV", "reference_live"): ("",), ("DEV", "reference_bg_1m"): ("",),
        ("VAL", "family"): ("",) + VAL_OVERLAYS, ("VAL", "descriptive"): ("",),
        ("FINAL", "family"): ("",), ("FINAL", "reference_live"): ("",),
    }
    if overlay not in allowed.get((st, role), ()):
        raise UsageError(f"{label!r}: {st} setinde {role} için '{overlay}' kaplaması/koşusu tanımsız")
    return LabelInfo(label, st, variant, overlay, quarter, "", st)


class Ledger:
    """ledger.jsonl: check-run'dan geçmiş koşular. Ledger'da olmayan sealed/runs/*.json REDDEDİLİR."""

    def __init__(self, root: Path):
        self.root = root
        self.entries: Dict[str, Dict[str, Any]] = {}
        p = ledger_path(root)
        if p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    e = json.loads(ln)
                    if e["label"] in self.entries:
                        raise IntegrityError(f"ledger'da çift etiket: {e['label']} (tamamlanmış koşu tekrarlanmaz)")
                    self.entries[e["label"]] = e

    def has(self, label: str) -> bool:
        return label in self.entries

    def run_file(self, label: str) -> Path:
        return self.root / "sealed" / "runs" / f"{label}.json"

    def assert_clean(self) -> None:
        d = self.root / "sealed" / "runs"
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                if p.stem not in self.entries:
                    raise IntegrityError(f"sealed/runs/{p.name} ledger'da yok (check-run'dan geçmemiş) — reddedildi")
        for lb, e in self.entries.items():
            rf = self.run_file(lb)
            if not rf.is_file():
                raise IntegrityError(f"ledger etiketi {lb} için rapor dosyası yok")
            if sha256_file(rf) != e["sha256"]:
                raise IntegrityError(f"{lb}: rapor sha256'sı ledger kaydıyla eşleşmiyor (bayat/değişmiş)")

    def append(self, entry: Dict[str, Any]) -> None:
        p = ledger_path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        self.entries[entry["label"]] = entry


def s_arm_of(ctx: Ctx, variant: str) -> float:
    return float(ctx.vid[variant]["S_arm"])


def trades_from_report(doc: Dict[str, Any], s_arm: float, label: str) -> Tuple[List[Tr], float]:
    """Rapor işlemlerini R'ye çevirir: R = pnl / (margin_usdt * S / 100). Medyan riski de döner."""
    out: List[Tr] = []
    risks: List[float] = []
    for i, t in enumerate(doc["trades"]):
        try:
            margin = float(t["margin_usdt"])
            pnl = float(t["pnl"])
            risk = margin * s_arm / 100.0
            if not risk > 0:
                raise IntegrityError(f"{label}: işlem {i} riski <= 0 (margin_usdt={margin})")
            out.append(Tr(symbol=str(t["symbol"]), direction=str(t["direction"]),
                          entry_ms=int(t["entry_time"]), exit_ms=int(t["exit_time"]),
                          r=pnl / risk, pnl=pnl, lev=int(t["leverage"]), reason=str(t["exit_reason"]),
                          month=month_of(int(t["entry_time"])), exit_day=ms_to_date(int(t["exit_time"])),
                          risk=risk))
            risks.append(risk)
        except KeyError as exc:
            raise IntegrityError(f"{label}: işlem {i} alanı eksik: {exc}")
    return out, (statistics.median(risks) if risks else 0.0)


def run_window(ctx: Ctx, info: LabelInfo) -> Tuple[str, str]:
    """Bir etiketin kilitli test penceresi (başlangıç, bitiş) — UTC tarih, bitiş hariç."""
    if info.quarter:
        refs = {r["id"]: r for r in ctx.variants["references"]}
        s, e = refs[info.variant]["dev_quarters"][info.quarter - 1]
        return s, e
    return ctx.window(info.window_set)


def _load_label(ctx: Ctx, led: Ledger, label: str) -> Series:
    """TEK koşuyu (etiket) ledger'dan doğrulayarak yükler."""
    if not led.has(label):
        raise StageError(f"ledger'da {label} yok (koşu/check-run eksik)")
    rf = led.run_file(label)
    if not rf.is_file() or sha256_file(rf) != led.entries[label]["sha256"]:
        raise IntegrityError(f"{label}: rapor yok veya sha256'sı ledger kaydıyla eşleşmiyor")
    doc = json.loads(rf.read_text(encoding="utf-8"))
    info = parse_label(label, ctx.variants)
    s, e = run_window(ctx, info)
    trs, med = trades_from_report(doc, s_arm_of(ctx, info.variant), label)
    return Series(label=label, arm=info.variant, set=info.set, overlay=info.overlay,
                  s_arm=s_arm_of(ctx, info.variant), trades=trs,
                  windows=[(dt.date.fromisoformat(s), (date_ms(e) - date_ms(s)) // MS_PER_DAY)],
                  missed={k: int(v) for k, v in (doc.get("missed_signals") or {}).items()},
                  dd_usd=float(doc["overall"]["bar_max_drawdown"]), risk_median=med,
                  diagnostics=dict(doc["diagnostics"]) if doc.get("diagnostics") else None,
                  run_labels=[label], reported_trades=int(doc["overall"]["trades"]))


def load_series(ctx: Ctx, set_name: str, variant: str, overlay: str = "", ledger: Optional[Ledger] = None,
                required: bool = True) -> Optional[Series]:
    """Ledger'dan doğrulanmış koşu(lar)ı yükler. B0/R1 DEV'de 4 çeyrek tek seriye birleşir."""
    led = ledger or Ledger(ctx.root)
    quarters = [1, 2, 3, 4] if (set_name == "DEV" and variant in QUARTER_REFS) else [0]
    parts: List[Series] = []
    for q in quarters:
        label = f"{set_name}_{variant}" + (f"_{overlay}" if overlay else "") + (f"_Q{q}" if q else "")
        if not led.has(label):
            if required:
                raise StageError(f"ledger'da {label} yok (koşu/check-run eksik)")
            return None
        parts.append(_load_label(ctx, led, label))
    if len(parts) == 1:
        return parts[0]
    diag: Optional[Dict[str, Any]] = None
    missed: Dict[str, int] = {}
    for p in parts:
        for k, v in p.missed.items():
            missed[k] = missed.get(k, 0) + v
        if p.diagnostics:
            diag = diag or {}
            for k, v in p.diagnostics.items():
                diag[k] = diag.get(k, 0) + int(v)
    risks = [p.risk_median for p in parts if p.trades]
    return Series(label=f"{set_name}_{variant}" + (f"_{overlay}" if overlay else ""), arm=variant, set=set_name,
                  overlay=overlay, s_arm=parts[0].s_arm, trades=[t for p in parts for t in p.trades],
                  windows=[w for p in parts for w in p.windows], missed=missed,
                  dd_usd=max(p.dd_usd for p in parts), risk_median=statistics.median(risks) if risks else 0.0,
                  diagnostics=diag, run_labels=[lb for p in parts for lb in p.run_labels],
                  reported_trades=sum(p.reported_trades for p in parts))


# ==========================================================================
# 8. CHECK-RUN (§5.2) — koşu raporunun kilitli sözleşmeye uygunluğu; ledger'a ekler
# ==========================================================================
SILENT_KEYS = ("stop_band_low", "stop_band_high", "price_distance_zero", "qty_zero", "no_next_candle")
DIAG_KEYS = ("tp1_fills", "sl_tp1_same_bar", "tp1_then_stop_same_bar", "tp2_then_floor_same_bar",
             "fill_bar_exit", "fill_bar_total")
FETCH_LINE = "Binance'ten çekiliyor"
#: Sunucu özetinde olup simülasyonu ETKİLEMEYEN / CLI ile belirlenen anahtarlar (check-run özet karşılaştırması bunları atlar):
#: evren (--symbols/--strategies ile), tarama/koruma zamanlayıcıları, sanal sermaye defteri, canlı-kanal anahtarları.
SNAPSHOT_SKIP_KEYS = frozenset({
    "SCALPER_MARKET_DATA_BASE_URL", "SCALPER_ENTRY_HALT_ENABLED", "SCALPER_COUNTERFACTUAL_ENABLED",
    "SCALPER_SAFETY_INTERVAL_SECONDS", "SCALPER_SCAN_INTERVAL_SECONDS", "SCALPER_PROTECTION_FAILURE_COOLDOWN_MINUTES",
    "SCALPER_VIRTUAL_CAPITAL_START_TRADE_ID", "SCALPER_VIRTUAL_CAPITAL_USDT", "SCALPER_TV_SYMBOL_ALLOWLIST",
    "SCALPER_SYMBOL_ALLOWLIST", "SCALPER_TOP_N", "SCALPER_STRATEGIES"})
#: Ücret stres kaplamalarında harness maliyet alanlarını çarpar (cost_model.stress_multiplier ayrıca doğrulanır).
FEE_STRESS_KEYS = frozenset({"SCALPER_MAKER_FEE_PCT", "SCALPER_TAKER_FEE_PCT", "SCALPER_SLIPPAGE_RATE"})


def values_equal(expected: str, actual: Any) -> bool:
    """variants.json çözülmüş env değeri (metin) == rapordaki yazılı Settings değeri."""
    if isinstance(actual, bool):
        return expected.strip().lower() in (("true", "1", "yes", "on") if actual else ("false", "0", "no", "off"))
    if isinstance(actual, (int, float)):
        try:
            return float(expected) == float(actual)
        except ValueError:
            return False
    if actual is None:
        return expected == ""
    if isinstance(actual, (list, tuple)):
        return expected == ",".join(str(x) for x in actual)
    return str(actual) == expected


def expected_env(ctx: Ctx, info: LabelInfo) -> Dict[str, str]:
    """Arm env'i üstüne kaplama env'i (variants.json çözülmüş değerleri; koşu anında bileşim yok)."""
    env = dict(ctx.vid[info.variant]["env"])
    if info.overlay:
        ov = next(o for o in ctx.variants["overlays"] if o["id"] == info.overlay)
        env.update(ov["env"])
    return env


def expected_cost(info: LabelInfo) -> Tuple[float, int]:
    stress = {"ROB_FEE2": THRESH["FEE2_MULT"], "DIAG_FEE15": THRESH["FEE15_MULT"]}.get(info.overlay, 1.0)
    delay = 1 if info.overlay == "ROB_DLY1" else 0
    return stress, delay


def check_report(ctx: Ctx, info: LabelInfo, doc: Dict[str, Any], log_text: Optional[str]) -> List[str]:
    """Başarısızlık nedenleri listesi (boş = geçti). Hiçbir PnL/PF değeri neden metnine yazılmaz."""
    f: List[str] = []
    prov = doc.get("provenance") or {}
    v = ctx.vid[info.variant]
    start, end = run_window(ctx, info)
    s_ms, e_ms = date_ms(start), date_ms(end)
    days = (e_ms - s_ms) // MS_PER_DAY
    triple = list(v["triple"])
    # --- sürüm/temizlik ---
    if prov.get("git_sha") != ctx.h:
        f.append("provenance.git_sha != H")
    if prov.get("git_dirty") is not False:
        f.append("provenance.git_dirty false değil")
    # --- pencere / üçlü / sembol ---
    tw = prov.get("test_window") or {}
    if tw.get("start_ms") != s_ms or tw.get("end_ms") != e_ms:
        f.append("test penceresi kilitli pencereyle eşleşmiyor")
    cfg = prov.get("scalper_config") or {}
    got = [cfg.get("scalper_tf_entry"), cfg.get("scalper_tf_context"), cfg.get("scalper_tf_regime")]
    if got != triple:
        f.append(f"TF üçlüsü {got} != kilitli {triple}")
    if triple[0] == triple[1] or triple[0] == triple[2]:
        f.append("rol-anahtarı çakışması (giriş = bağlam/rejim)")
    params = doc.get("params") or {}
    if ",".join(params.get("symbols") or []) != SYMBOLS_PINNED or params.get("strategies") != "C":
        f.append("params.symbols sırası/strategies sabit değil")
    us = (prov.get("universe_snapshot") or {}).get("symbols")
    if us is not None and ",".join(us) != SYMBOLS_PINNED:
        f.append("universe_snapshot.symbols sırası sabit değil")
    # --- whitelist: variants.json'daki HER anahtarın çözülmüş değeri ---
    for k, want in sorted(expected_env(ctx, info).items()):
        key = k.lower()
        if key not in cfg:
            f.append(f"scalper_config'te {key} yok")
        elif not values_equal(want, cfg[key]):
            f.append(f"scalper_config.{key} beklenenle eşleşmiyor")
    # --- R biriminin dayanağı: S_arm (SCALPER_FIXED_STOP_ROI_PCT) HER arm için raporda DOĞRULANIR. T arm'ları ve
    #     B0/R1 env'inde bu anahtar listelenmez (sunucu değeri 50); yanlış/eksik özetle koşulmuş bir rapor
    #     yanlış S_arm ile R üretirdi. ---
    s_key = "scalper_fixed_stop_roi_pct"
    if s_key not in cfg:
        f.append(f"scalper_config'te {s_key} yok (R birimi doğrulanamaz)")
    elif not values_equal(str(v["S_arm"]), cfg[s_key]):
        f.append(f"scalper_config.{s_key} S_arm ({v['S_arm']}) ile eşleşmiyor (R birimi yanlış olurdu)")
    # --- sunucu özeti (yerel; içerik basılmaz): variants.json'da listelenmeyen TÜM SCALPER_* anahtarları, özetteki
    #     değerle eşit olmalı (yalnız anahtar adı raporlanır). Ücret stresinde maliyet alanları atlanır. ---
    listed = {k.upper() for k in expected_env(ctx, info)}
    stressed = info.overlay in ("ROB_FEE2", "DIAG_FEE15")
    for k, want in sorted(snapshot_env_lines(ctx.root).items()):
        ku = k.upper()
        if ku in listed or ku in SNAPSHOT_SKIP_KEYS or (stressed and ku in FEE_STRESS_KEYS) or not ku.startswith("SCALPER_"):
            continue
        if ku.lower() in cfg and not values_equal(want, cfg[ku.lower()]):
            f.append(f"scalper_config.{ku.lower()} sunucu özetiyle eşleşmiyor")
    # --- maliyet modeli / sim seçenekleri ---
    cm = prov.get("cost_model") or {}
    stress, delay = expected_cost(info)
    if float(cm.get("stress_multiplier", -1)) != stress:
        f.append("cost_model.stress_multiplier beklenenden farklı")
    if int(cm.get("entry_delay_candles", -1)) != delay:
        f.append("cost_model.entry_delay_candles beklenenden farklı")
    so = prov.get("sim_options") or {}
    want_sbo = "optimistic" if info.overlay == "DIAG_SBO" else "pessimistic"
    if so.get("diagnostics") is not True or so.get("same_bar_order") != want_sbo:
        f.append("provenance.sim_options beklenenden farklı")
    diag = doc.get("diagnostics")
    if not isinstance(diag, dict) or any(k not in diag for k in DIAG_KEYS):
        f.append("diagnostics bloğu eksik/yanlış")
    if not isinstance(doc.get("missed_signals"), dict):
        f.append("missed_signals yok")
    # --- toplamlar ---
    ov = doc.get("overall") or {}
    trades = doc.get("trades")
    if not isinstance(trades, list):
        f.append("trades listesi yok")
        return f
    try:
        dd = float(ov.get("bar_max_drawdown"))
        if not math.isfinite(dd):
            raise ValueError
    except (TypeError, ValueError):
        f.append("overall.bar_max_drawdown yok/sonlu değil")
    if int(ov.get("trades", -1)) != len(trades):
        f.append("overall.trades != len(trades)")
    try:
        pnls = [float(t["pnl"]) for t in trades]
        tot = float(ov.get("total_pnl"))
        if abs(tot - sum(pnls)) > 1e-6 * max(1.0, abs(tot)):
            f.append("overall.total_pnl != Σ pnl")
        calc = pf_of(pnls)
        pfj = ov.get("profit_factor")
        if pfj is None:
            if not (calc == INF or not trades):
                f.append("profit_factor null ama işlemlerden sonlu hesaplandı")
        elif calc != INF and abs(float(pfj) - calc) > 1e-6 * max(1.0, abs(calc)):
            f.append("overall.profit_factor != işlemlerden hesaplanan")
        for t in trades:
            ed = ms_to_date(int(t["exit_time"]))
            if not (dt.date.fromisoformat(start) <= ed < dt.date.fromisoformat(end)):
                f.append("çıkış günü test penceresinin dışında")
                break
    except (KeyError, TypeError, ValueError):
        f.append("trades[] alanları bozuk")
    # --- bar boşluğu / en erken bar ---
    dw = prov.get("data_windows") or {}
    if set(dw) != set(SYMBOLS_PINNED.split(",")):
        f.append("data_windows sembol kümesi eksik")
    earliest = None
    for sym, per in dw.items():
        if set(per) != set(triple):
            f.append(f"{sym}: data_windows TF kümesi {sorted(per)} != {sorted(set(triple))}")
            continue
        for iv, w in per.items():
            want_n = days * CANDLES_PER_DAY[iv]
            if w.get("candles_in_test_window") != want_n:
                f.append(f"{sym} {iv}: test penceresinde {w.get('candles_in_test_window')} mum != {want_n} (boşluk)")
            fs = w.get("fetched_start_ms")
            if fs is not None:
                earliest = fs if earliest is None else min(earliest, fs)
    if info.set != "VAL" and earliest is not None and earliest <= date_ms(VAL_END):
        f.append("en erken bar VAL bitişinden sonra değil (sızıntı duvarı)")
    # --- log ---
    if log_text is None:
        f.append("log dosyası yok")
    elif FETCH_LINE in log_text:
        f.append("log'da 'Binance'ten çekiliyor' var (önbellek ıskası)")
    return f


def manifest_runs(root: Path, led: Ledger) -> None:
    rows = [{"etiket": e["label"], "utc": e["utc"], "saniye": e.get("seconds"), "sha256": e["sha256"],
             "git_sha": e.get("git_sha")} for e in led.entries.values()]
    atomic_write(root / "MANIFEST.runs.json", dump_json(rows))


def smoke_passed(ctx: Ctx) -> bool:
    """SMOKE.json (bu kilit L için) PASS mi? S1-S3 duman testleri DEV'den önce ENGELLEYİCİDİR (§5.2)."""
    p = ctx.root / "SMOKE.json"
    if not p.is_file():
        return False
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return doc.get("pass") is True and doc.get("l") == ctx.l


def require_smoke(ctx: Ctx, what: str) -> None:
    if not smoke_passed(ctx):
        raise StageError(f"{what}: SMOKE.json yok/PASS değil (bu kilit için): önce duman testleri (`smoke`) geçmeli")


def run_stage_preconditions(ctx: Ctx, info: LabelInfo) -> None:
    """Etiketin ait olduğu aşamanın ön koşulları (PLAN §6 sıra, §9.4/§9.5). İhlal -> StageError (exit 4):
    DEV: SMOKE PASS; G6 kaplamaları: DEV_SCREEN'de g6_needed; DIAG: DEV_DECISION (DIAG_FEE15 yalnız FEE2'yi
    kaçıran/ilerleyen arm); VAL: DEV_DECISION.advanced (+ açıklayıcılar); FINAL: VAL kazananı (B0 ya da kazanan)."""
    if info.set == "SMOKE":
        return
    root = ctx.root
    if info.set == "DEV":
        require_smoke(ctx, f"{info.label}")
        if info.overlay in G6_OVERLAYS:
            sc = _read_json_stage(root, "DEV_SCREEN.json", f"{info.label} için dev-screen gerekir")
            if info.variant not in sc.get("_meta", {}).get("g6_needed", []):
                raise StageError(f"{info.label}: {info.variant} G0-G5'i geçmedi; G6 kaplama koşusu yapılmaz")
        elif info.overlay in DIAG_OVERLAYS:
            dd = _read_json_stage(root, "DEV_DECISION.json", f"{info.label} teşhis koşusu DEV kararından sonra çalışır")
            if info.overlay == "DIAG_FEE15":
                fee_failed = info.variant in dd.get("g6", {}) and not dd["g6"][info.variant]["fee2"]["pass"]
                if not (fee_failed or info.variant in dd.get("advanced", [])):
                    raise StageError(f"{info.label}: DIAG_FEE15 yalnız FEE2'yi kaçıran veya VAL'e ilerleyen arm içindir")
    elif info.set == "VAL":
        dd = _read_json_stage(root, "DEV_DECISION.json", f"{info.label} için DEV kararı gerekir")
        if not dd.get("advanced"):
            raise StageError(f"{info.label}: DEV'de ilerleyen arm yok; VAL koşulmaz")
        if info.variant not in dd["advanced"] and info.variant not in DESCRIPTIVE:
            raise StageError(f"{info.label}: {info.variant} DEV'de ilerlemedi (açıklayıcı da değil); VAL koşulmaz")
    elif info.set == "FINAL":
        vd = _read_json_stage(root, "VAL_DECISION.json", f"{info.label} için VAL kararı gerekir")
        win = val_winner(vd)
        if not win:
            raise StageError(f"{info.label}: VAL kazananı yok; FINAL'e dokunulmaz")
        if info.variant not in (win, "B0"):
            raise StageError(f"{info.label}: FINAL yalnız VAL kazananı ({win}) ve B0 içindir")


def cmd_check_run(root: Path, label: Optional[str]) -> int:
    if not label:
        raise UsageError("check-run <etiket> ister")
    ctx = verify_lock(root, remote=False)
    info = parse_label(label, ctx.variants)
    led = Ledger(root)
    rf = led.run_file(label)
    lf = root / "sealed" / "logs" / f"{label}.log"
    secf = root / "sealed" / "logs" / f"{label}.sec"
    if led.has(label):
        if rf.is_file() and sha256_file(rf) == led.entries[label]["sha256"]:
            print(f"CHECK OK {label} (zaten ledger'da)")
            return 0
        raise IntegrityError(f"{label} ledger'da farklı sha256 ile kayıtlı: tamamlanmış koşu tekrarlanmaz")
    run_stage_preconditions(ctx, info)
    if not rf.is_file():
        raise IntegrityError(f"{rf} yok")
    doc: Dict[str, Any] = {}
    try:
        doc = json.loads(rf.read_text(encoding="utf-8"))
        reasons = check_report(ctx, info, doc, lf.read_text(encoding="utf-8", errors="replace") if lf.is_file() else None)
    except ValueError as exc:
        reasons = [f"rapor JSON'u okunamadı: {exc}"]
    except (TypeError, KeyError, AttributeError) as exc:      # null/yanlış tipli alan: bozuk rapor = başarısızlık
        reasons = [f"rapor biçimi bozuk: {type(exc).__name__}: {exc}"]
    if not reasons and not isinstance(doc.get("provenance"), dict):
        reasons = ["provenance yok"]
    if reasons:
        for p in (rf, lf, secf):                 # bayat çıktı kalamaz (§5.2); süre dosyası da silinir
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        print(f"CHECK FAIL {label}: " + "; ".join(reasons), file=sys.stderr)
        return IntegrityError.code
    sec = None
    sp = secf
    if sp.is_file():
        try:
            sec = float(sp.read_text().strip())
        except ValueError:
            sec = None
    led.append({"label": label, "utc": utc_now(), "seconds": sec, "sha256": sha256_file(rf),
                "git_sha": doc["provenance"]["git_sha"], "bytes": rf.stat().st_size})
    manifest_runs(root, led)
    print(f"CHECK OK {label}")
    return 0


# ==========================================================================
# 9. MANIFEST.data (§4.8): önbellek dosyalarının sha256'sı; VAL/FINAL aşama kapısına kadar YOK olmalı
# ==========================================================================
FILL_PHASE = {"smoke": "P1b", "dev": "P1", "val": "P6", "final": "P8"}
CACHE_RE = re.compile(r"^(?P<sym>[A-Z0-9]+)_(?P<iv>[0-9a-z]+)_(?P<s>\d+)_(?P<e>\d+)\.json\.gz$")


def _read_json_stage(root: Path, name: str, what: str) -> Dict[str, Any]:
    p = root / name
    if not p.exists():
        raise StageError(f"{name} yok: {what} (aşama sırası)")
    return json.loads(p.read_text(encoding="utf-8"))


def manifest_scope(ctx: Ctx, set_name: str) -> List[Tuple[Tuple[str, ...], str, str]]:
    """Aşamanın beklenen (üçlü, başlangıç, bitiş) pencereleri."""
    vs = ctx.variants
    t1m = tuple(ctx.vid["B0"]["triple"])
    fam = {x["id"]: tuple(x["triple"]) for x in vs["family"] + vs["descriptive"]}
    out: List[Tuple[Tuple[str, ...], str, str]] = []
    if set_name == "smoke":
        for k in ("S1_AYI", "S1_V1"):
            out.append((t1m, *ctx.window(k)))
        for tri in dict.fromkeys(fam.values()):
            out.append((tri, *ctx.window("SMOKE_W")))
        out.append((t1m, *ctx.window("SMOKE_1M")))
    elif set_name == "dev":
        for tri in dict.fromkeys(fam.values()):
            out.append((tri, *ctx.window("DEV")))
        for s, e in ctx.vid["B0"]["dev_quarters"]:
            out.append((t1m, s, e))
    elif set_name == "val":
        dd = _read_json_stage(ctx.root, "DEV_DECISION.json", "VAL verisi için DEV kararı gerekir")
        if not dd.get("advanced"):
            raise StageError("DEV_DECISION.advanced boş: VAL verisi indirilmez")
        tris = [fam[a] for a in dd["advanced"]] + [fam["S60"]]
        for tri in dict.fromkeys(tris):
            out.append((tri, *ctx.window("VAL")))
    elif set_name == "final":
        vd = _read_json_stage(ctx.root, "VAL_DECISION.json", "FINAL verisi için VAL kararı gerekir")
        if not val_winner(vd):
            raise StageError("VAL kazananı yok: FINAL verisi indirilmez")
        out.append((fam[val_winner(vd)], *ctx.window("FINAL")))
        out.append((t1m, *ctx.window("FINAL")))
    else:
        raise UsageError(f"manifest hedefi {sorted(FILL_PHASE) + ['runs']} içinden olmalı")
    return out


def cmd_manifest(root: Path, target: Optional[str]) -> int:
    if not target:
        raise UsageError("manifest <smoke|dev|val|final|runs> ister")
    ctx = verify_lock(root)
    if target == "runs":
        led = Ledger(root)
        led.assert_clean()
        manifest_runs(root, led)
        print(f"MANIFEST.runs.json yazıldı ({len(led.entries)} koşu)")
        return 0
    scope = manifest_scope(ctx, target)
    cache = root / "cache"
    files = {p.name: p for p in cache.glob("*.json.gz")} if cache.is_dir() else {}
    # aşama kapısı: VAL/FINAL çok-sembollü verisi kapıdan önce fiziksel olarak yok olmalı
    forbidden = {"smoke": (VAL_END, WINDOWS["FINAL"][1]), "dev": (VAL_END, WINDOWS["FINAL"][1]),
                 "val": (WINDOWS["FINAL"][1],), "final": ()}[target]
    f_ms = {date_ms(d) for d in forbidden}
    for name in files:
        m = CACHE_RE.match(name)
        if m and int(m["e"]) in f_ms and not (m["sym"] == "BTCUSDT" and m["iv"] == "1d"):
            raise IntegrityError(f"{name}: bu aşamaya (manifest {target}) ait olmayan VAL/FINAL verisi mevcut")
    expected: List[str] = []
    for tri, s, e in scope:
        for sym in SYMBOLS_PINNED.split(","):
            for iv, st, en in role_windows(tri, s, e):
                expected.append(f"{sym}_{iv}_{st}_{en}.json.gz")
    expected = sorted(set(expected))
    missing = [n for n in expected if n not in files]
    if missing:
        raise IntegrityError(f"{len(missing)} beklenen önbellek dosyası yok (örn. {missing[:3]})")
    end_set = {date_ms(e) for _t, _s, e in scope}
    path = root / "MANIFEST.data.json"
    cur: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    n_new = 0
    for name in sorted(files):
        m = CACHE_RE.match(name)
        if not m or int(m["e"]) not in end_set:
            continue
        sha = sha256_file(files[name])
        if name in cur:
            if cur[name]["sha256"] != sha:
                raise IntegrityError(f"{name}: önbellek dosyası manifest kaydından sonra DEĞİŞMİŞ")
            continue
        with gzip.open(files[name], "rt", encoding="utf-8") as fh:
            rows = json.load(fh)
        cur[name] = {"sha256": sha, "bytes": files[name].stat().st_size, "symbol": m["sym"],
                     "interval": m["iv"], "n_candles": len(rows),
                     "first_open_time": rows[0]["open_time"] if rows else None,
                     "last_open_time": rows[-1]["open_time"] if rows else None,
                     "set": target, "fill_phase": FILL_PHASE[target]}
        n_new += 1
    atomic_write(path, dump_json(cur))
    print(f"MANIFEST.data.json: {n_new} yeni kayıt, toplam {len(cur)} (beklenen {len(expected)} rol dosyası tamam)")
    return 0


# ==========================================================================
# 10. SMOKE (§5.2): S1 parite / S2 belirlenimcilik / S3 konfigürasyon — yalnız PASS/FAIL
# ==========================================================================
def _report(led: Ledger, label: str) -> Dict[str, Any]:
    if not led.has(label):
        raise StageError(f"ledger'da {label} yok (duman koşusu/check-run eksik)")
    rf = led.run_file(label)
    if sha256_file(rf) != led.entries[label]["sha256"]:
        raise IntegrityError(f"{label}: rapor ledger kaydıyla eşleşmiyor")
    return json.loads(rf.read_text(encoding="utf-8"))


def smoke_s1(doc: Dict[str, Any], tag: str) -> List[str]:
    n, wr, pf, pnl, dd = SMOKE_S1_EXPECT[tag]
    ov = doc["overall"]
    fails = []
    if int(ov["trades"]) != n:
        fails.append("işlem sayısı")
    if round(float(ov["winrate"]), 2) != wr:
        fails.append("WR")
    pf_got = ov.get("profit_factor")
    if pf_got is None or round(float(pf_got), 4) != pf:
        fails.append("PF")
    if round(float(ov["total_pnl"]), 4) != pnl:
        fails.append("PnL")
    if dd is not None and round(float(ov["bar_max_drawdown"]), 4) != dd:
        fails.append("bar_max_drawdown")
    return fails


def lev_shares(trades: Sequence[Tr]) -> Tuple[float, float]:
    n = len(trades)
    if not n:
        return 0.0, 0.0
    return (sum(1 for t in trades if t.lev == THRESH["LEV_LOW"]) / n,
            sum(1 for t in trades if t.lev == THRESH["LEV_HIGH"]) / n)


def cmd_smoke(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("smoke hedef almaz")
    ctx = verify_lock(root)
    led = Ledger(root)
    led.assert_clean()
    res: Dict[str, Any] = {}
    for tag in ("AYI", "V1"):
        fails = smoke_s1(_report(led, f"SMOKE_B0_{tag}"), tag)
        res[f"S1_{tag}"] = {"pass": not fails, "mismatch": fails}
    a, b = _report(led, "SMOKE_S15_h0"), _report(led, "SMOKE_S15_h1")
    same = sha256_bytes(canon(a["trades"]).encode()) == sha256_bytes(canon(b["trades"]).encode())
    res["S2"] = {"pass": same, "n_trades": len(a["trades"])}
    s3: Dict[str, Any] = {}
    for arm in FAMILY + DESCRIPTIVE + ("R1",):
        label = f"SMOKE_{arm}_" + ("M1" if arm == "R1" else "W")
        doc = _report(led, label)
        problems = []
        if not isinstance(doc.get("diagnostics"), dict) or any(k not in doc["diagnostics"] for k in DIAG_KEYS):
            problems.append("diagnostics")
        if not isinstance(doc.get("missed_signals"), dict):
            problems.append("missed_signals")
        if len(doc["trades"]) < 1:
            problems.append("işlem yok")
        trs, _ = trades_from_report(doc, s_arm_of(ctx, arm), label)
        l3, l20 = lev_shares(trs)
        s3[arm] = {"pass": not problems, "problems": problems, "n": len(trs),
                   "lev3_share": l3, "lev20_share": l20,
                   "h1_keys_present": sorted(k for k in SILENT_KEYS if k in doc.get("missed_signals", {}))}
    res["S3"] = s3
    ok = res["S1_AYI"]["pass"] and res["S1_V1"]["pass"] and res["S2"]["pass"] and all(v["pass"] for v in s3.values())
    res["pass"] = ok
    res["l"] = ctx.l
    atomic_write(root / "SMOKE.json", dump_json(res))
    print(f"S1 B0 AYI: {'PASS' if res['S1_AYI']['pass'] else 'FAIL'}; S1 B0 V1: {'PASS' if res['S1_V1']['pass'] else 'FAIL'}; "
          f"S2 belirlenimcilik: {'PASS' if same else 'FAIL'}; S3 konfigürasyon: {'PASS' if all(v['pass'] for v in s3.values()) else 'FAIL'}")
    for arm, v in s3.items():       # 20x/3x payları YALNIZ basılır, kapı DEĞİLDİR (CAL penceresi)
        print(f"  S3 {arm}: n={v['n']} 3x payı={v['lev3_share']:.3f} 20x payı={v['lev20_share']:.3f}")
    return 0 if ok else IntegrityError.code


# ==========================================================================
# 11. KAPILAR (§8) — her kural tek bir saf fonksiyon; eşikler THRESH'ten ÇAĞRI ANINDA okunur
# ==========================================================================
def limiting_row(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Birleşik kapıyı belirleyen satır: ilk KALAN satır; hepsi geçtiyse ilk SINIRDA satır; yoksa ilk satır."""
    for r in rows:
        if not r["pass"]:
            return r
    for r in rows:
        if r["borderline"]:
            return r
    return rows[0]


def entry(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Birleşik kapı girdisi (Ek A.4 biçimi: {value, threshold, pass, borderline} + alt satırlar).
    Tüm alt satırlar geçerse geçer; value/threshold/op/limiting = kapıyı belirleyen satırdan."""
    lim = limiting_row(rows)
    return {"value": lim["value"], "threshold": lim["threshold"], "op": lim["op"], "limiting": lim["gate"],
            "pass": all(r["pass"] for r in rows), "borderline": any(r["borderline"] for r in rows), "rows": rows}


def gate_ok(g: Any) -> bool:
    return bool(g["pass"])


def is_scaled(ctx: Ctx, arm: str) -> bool:
    return str(ctx.vid[arm]["geometry"]).startswith("scaled")


def silent_ratio(missed: Dict[str, int], n: int) -> Tuple[float, int, int]:
    """sessiz_düşüş / ham_sinyal (§5.1 H1 tanımı)."""
    silent = sum(int(missed.get(k, 0)) for k in SILENT_KEYS)
    ham = n + sum(int(missed.get(k, 0)) for k in ("capacity", "maker_missed", "min_rr_rejected")) + silent
    return (silent / ham if ham else 0.0), silent, ham


def risk_band_share(trades: Sequence[Tr]) -> float:
    """Sabit-risk kontrolü: riski medyan riskin ±G0_RISK_BAND'ında olan işlem payı."""
    if not trades:
        return 1.0
    med = statistics.median(t.risk for t in trades)
    band = THRESH["G0_RISK_BAND"]
    return sum(1 for t in trades if abs(t.risk - med) <= band * med) / len(trades)


def eod_count(trades: Sequence[Tr]) -> int:
    return sum(1 for t in trades if t.reason == "EOD")


def rule_validity(prefix: str, trades: Sequence[Tr], missed: Dict[str, int], scaled: bool,
                  dev: bool) -> List[Dict[str, Any]]:
    """G0 (DEV) / G14 (VAL) geçerlilik satırları. VAL'de 20x payı yalnız rapordur."""
    ratio, silent, ham = silent_ratio(missed, len(trades))
    rows = [row(f"{prefix}a", "sessiz düşüş / ham sinyal", ratio, THRESH["G0_SILENT_DROP_MAX"],
                ratio <= THRESH["G0_SILENT_DROP_MAX"], "<=", f"{silent}/{ham}"),
            row(f"{prefix}b", "EOD işlem sayısı", eod_count(trades), THRESH["G0_EOD_MAX"],
                eod_count(trades) <= THRESH["G0_EOD_MAX"], "<=")]
    if scaled:
        l3, l20 = lev_shares(trades)
        rows.append(row(f"{prefix}c1", "3x kaldıraç payı", l3, THRESH["G0_LEV3_MAX"],
                        l3 <= THRESH["G0_LEV3_MAX"], "<="))
        if dev:
            rows.append(row(f"{prefix}c2", "20x kaldıraç payı", l20, THRESH["G0_LEV20_MAX"],
                            l20 <= THRESH["G0_LEV20_MAX"], "<="))
        else:
            rows.append(row(f"{prefix}c2", "20x kaldıraç payı (VAL: yalnız rapor)", l20, THRESH["G0_LEV20_MAX"],
                            True, "<=", "kelepçe-yönetimli" if l20 > THRESH["G0_LEV20_MAX"] else ""))
    sh = risk_band_share(trades)
    rows.append(row(f"{prefix}d", "sabit-risk payı", sh, THRESH["G0_RISK_SHARE_MIN"],
                    sh >= THRESH["G0_RISK_SHARE_MIN"]))
    return rows


def rule_g1(n: int) -> Dict[str, Any]:
    return row("G1", "N (kapasite sonrası)", n, THRESH["G1_N_MIN"], n >= THRESH["G1_N_MIN"])


def rule_g2(pf: float) -> Dict[str, Any]:
    return row("G2", "PF", pf, THRESH["G2_PF_MIN"], pf >= THRESH["G2_PF_MIN"])


def rule_g3(q: float) -> Dict[str, Any]:
    return row("G3", "BH q (m=6, p[b=7])", q, THRESH["G3_Q_MAX"], q <= THRESH["G3_Q_MAX"], "<=")


def rule_g4(trades: Sequence[Tr]) -> Dict[str, Any]:
    bm = best_month(trades)
    pf_x = pf_excluding_month(trades, bm)
    s_x = sum_excluding_top(trades, int(THRESH["TOPN_G4"]))
    return entry([
        row("G4a", f"PF, en iyi ay ({bm}) hariç", pf_x, THRESH["G4_PF_EX_MONTH_MIN"],
            pf_x >= THRESH["G4_PF_EX_MONTH_MIN"]),
        row("G4b", f"Σ R, en iyi {int(THRESH['TOPN_G4'])} işlem hariç", s_x, THRESH["G4_SUMR_EX_TOP5_MIN"],
            s_x > THRESH["G4_SUMR_EX_TOP5_MIN"], ">")])


def group_table(trades: Sequence[Tr], labels: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for g, ts in regime_groups(trades, labels).items():
        out[g] = {"n": len(ts), "pf": pf_r(ts), "sum_r": sum_r(ts)}
    return out


def rule_g5(trades: Sequence[Tr], labels: Dict[str, str]) -> Dict[str, Any]:
    tbl = group_table(trades, labels)
    elig = {g: v for g, v in tbl.items() if v["n"] >= THRESH["G5_GROUP_N_MIN"]}
    worst = min((v["pf"] for v in elig.values()), default=None)
    ok = all(v["pf"] >= THRESH["G5_GROUP_PF_MIN"] for v in elig.values())
    r = row("G5", "rejim tabanı: uygun (n>=40) grupların en düşük PF'i", worst, THRESH["G5_GROUP_PF_MIN"], ok,
            ">=", "uygun grup yok" if not elig else "")
    r["groups"] = tbl
    return r


def rule_g6(fee2_pf: float, dly1_sumr: float, taker_pf: float) -> Dict[str, Any]:
    return entry([
        row("G6a", "ROB_FEE2 PF", fee2_pf, THRESH["G6_FEE2_PF_MIN"], fee2_pf >= THRESH["G6_FEE2_PF_MIN"]),
        row("G6b", "ROB_DLY1 Σ R", dly1_sumr, THRESH["G6_DLY1_SUMR_MIN"], dly1_sumr > THRESH["G6_DLY1_SUMR_MIN"], ">"),
        row("G6c", "ROB_TAKER PF", taker_pf, THRESH["G6_TAKER_PF_MIN"], taker_pf >= THRESH["G6_TAKER_PF_MIN"])])


def rule_g7(n: int) -> Dict[str, Any]:
    return row("G7", "N (kapasite sonrası)", n, THRESH["G7_N_MIN"], n >= THRESH["G7_N_MIN"])


def rule_g8(pf: float) -> Dict[str, Any]:
    return row("G8", "PF", pf, THRESH["G8_PF_MIN"], pf >= THRESH["G8_PF_MIN"])


def rule_g9(p7: float, p28: float, k: int) -> Dict[str, Any]:
    a7, a28 = THRESH["G9_ALPHA_P7"] / k, THRESH["G9_ALPHA_P28"] / k
    return entry([row("G9a", f"p(b=7) tek-yönlü (α/k, k={k})", p7, a7, p7 <= a7, "<="),
                  row("G9b", f"p(b=28) tek-yönlü (α/k, k={k})", p28, a28, p28 <= a28, "<=")])


def rule_g10a(trades: Sequence[Tr], labels: Dict[str, str]) -> Dict[str, Any]:
    tbl = group_table(trades, labels)
    elig = {g: v for g, v in tbl.items() if v["n"] >= THRESH["G10A_GROUP_N_MIN"]}
    worst = min((v["pf"] for v in elig.values()), default=None)
    ok = len(elig) >= THRESH["G10A_MIN_GROUPS"] and all(v["pf"] >= THRESH["G10A_GROUP_PF_MIN"] for v in elig.values())
    r = row("G10a", "VAL grupları: uygun (n>=40) grupların en düşük PF'i", worst, THRESH["G10A_GROUP_PF_MIN"], ok, ">=",
            f"uygun grup sayısı {len(elig)} (>= {THRESH['G10A_MIN_GROUPS']} gerekir)")
    r["groups"] = tbl
    return r


def rule_g10b(dev_trades: Sequence[Tr], val_trades: Sequence[Tr], labels: Dict[str, str]) -> Dict[str, Any]:
    """CLAUDE.md yasak #1 (ayı rejimi PF >= 1.1): DEV+VAL DOWN ayları havuzlu."""
    pool = [t for t in list(dev_trades) + list(val_trades) if labels.get(t.month) == "DOWN"]
    n, pf = len(pool), pf_r(pool)
    if n < THRESH["G10B_N_MIN"]:
        r = row("G10b", "DOWN havuzlu PF", pf, THRESH["G10B_PF_MIN"], False, ">=", f"TEST EDİLEMEDİ (n={n} < {THRESH['G10B_N_MIN']})")
    else:
        r = row("G10b", "DOWN havuzlu PF", pf, THRESH["G10B_PF_MIN"], pf >= THRESH["G10B_PF_MIN"], ">=", f"n={n}")
    r["n"] = n
    return r


def month_table(trades: Sequence[Tr], labels: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for m in sorted({t.month for t in trades}):
        ts = [t for t in trades if t.month == m]
        out[m] = {"n": len(ts), "sum_r": sum_r(ts), "pf": pf_r(ts), "label": labels.get(m, "NA")}
    return out


def rule_g11(trades: Sequence[Tr], full_months: Sequence[str]) -> Dict[str, Any]:
    sums = group_sums(trades, lambda t: t.month)
    pos = sum(1 for m in full_months if sums.get(m, 0.0) > 0)       # işlemsiz ay pozitif sayılmaz
    need = math.ceil(round(THRESH["G11_MONTH_FRAC"] * len(full_months), 9))
    return row("G11", f"Σ R > 0 olan tam ay (M={len(full_months)})", pos, need, pos >= need)


def rule_g12(trades: Sequence[Tr]) -> Dict[str, Any]:
    total = sum_r(trades)
    if not total > 0:
        return entry([row("G12", "T = Σ R > 0", total, 0.0, False, ">", "T <= 0")])
    sym = group_sums(trades, lambda t: t.symbol)
    mon = group_sums(trades, lambda t: t.month)
    bs, bm = best_symbol(trades), best_month(trades)
    top1 = top_trades(trades, 1)
    topn = int(THRESH["TOPN_G12"])
    top5 = sum(t.r for t in top_trades(trades, topn))
    loo = loo_symbol_pfs(trades)
    worst_loo_sym = min(loo, key=lambda s: (loo[s], s)) if loo else None
    pf_xs, pf_xm = pf_excluding_symbol(trades, bs), pf_excluding_month(trades, bm)
    rows = [
        row("G12a", f"en iyi sembol ({bs}) / T", sym[bs] / total, THRESH["G12_BEST_SYMBOL_SHARE_MAX"],
            sym[bs] / total <= THRESH["G12_BEST_SYMBOL_SHARE_MAX"], "<="),
        row("G12b", f"en iyi ay ({bm}) / T", mon[bm] / total, THRESH["G12_BEST_MONTH_SHARE_MAX"],
            mon[bm] / total <= THRESH["G12_BEST_MONTH_SHARE_MAX"], "<="),
        row("G12c", "en iyi işlem / T", top1[0].r / total, THRESH["G12_BEST_TRADE_SHARE_MAX"],
            top1[0].r / total <= THRESH["G12_BEST_TRADE_SHARE_MAX"], "<="),
        row("G12d", f"en iyi {topn} işlem / T", top5 / total, THRESH["G12_BEST5_SHARE_MAX"],
            top5 / total <= THRESH["G12_BEST5_SHARE_MAX"], "<="),
        row("G12e", "PF, en iyi sembol hariç", pf_xs, THRESH["G12_PF_EX_SYMBOL_MIN"], pf_xs >= THRESH["G12_PF_EX_SYMBOL_MIN"]),
        row("G12f", "PF, en iyi ay hariç", pf_xm, THRESH["G12_PF_EX_MONTH_MIN"], pf_xm >= THRESH["G12_PF_EX_MONTH_MIN"]),
        row("G12g", f"LOO-sembol en düşük PF ({worst_loo_sym})", loo[worst_loo_sym] if worst_loo_sym else 0.0,
            THRESH["G12_LOO_SYMBOL_PF_MIN"],
            bool(loo) and all(v >= THRESH["G12_LOO_SYMBOL_PF_MIN"] for v in loo.values())),
    ]
    return entry(rows)


def dd_r_of(series: Series) -> float:
    """DD_R = bar_max_drawdown (USD) / medyan(margin x S/100)."""
    if series.risk_median > 0:
        return series.dd_usd / series.risk_median
    return INF if series.dd_usd > 0 else 0.0


def calmar_r(sum_r_: float, days: int, dd_r: float) -> float:
    ann = sum_r_ * 365.0 / days if days else 0.0
    if dd_r > 0:
        return ann / dd_r
    return INF if ann > 0 else (0.0 if ann == 0 else -INF)


def rule_g13(series: Series) -> Dict[str, Any]:
    c = calmar_r(sum_r(series.trades), series.days, dd_r_of(series))
    return row("G13", "Calmar_R", c, THRESH["G13_CALMAR_MIN"], c >= THRESH["G13_CALMAR_MIN"])


def rule_g15(fee2_pf: float, dly1_sumr: float, taker_pf: float, wall_sumr: float) -> Dict[str, Any]:
    return entry([
        row("G15a", "ROB_FEE2 PF", fee2_pf, THRESH["G15_FEE2_PF_MIN"], fee2_pf >= THRESH["G15_FEE2_PF_MIN"]),
        row("G15b", "ROB_DLY1 Σ R", dly1_sumr, THRESH["G15_DLY1_SUMR_MIN"], dly1_sumr > THRESH["G15_DLY1_SUMR_MIN"], ">"),
        row("G15c", "ROB_TAKER PF", taker_pf, THRESH["G15_TAKER_PF_MIN"], taker_pf >= THRESH["G15_TAKER_PF_MIN"]),
        row("G15d", "ROB_WALL Σ R", wall_sumr, THRESH["G15_WALL_SUMR_MIN"], wall_sumr > THRESH["G15_WALL_SUMR_MIN"], ">")])


def rule_g16(trades: Sequence[Tr], full_months: Sequence[str]) -> Dict[str, Any]:
    """VAL'in tam ayları zaman sırasıyla iki eşit yarıya (11+11); her yarıda N ve PF."""
    ms = sorted(full_months)
    half = len(ms) // 2
    parts = (ms[:half], ms[half:])
    rows = []
    for i, part in enumerate(parts, 1):
        ts = [t for t in trades if t.month in set(part)]
        n, pf = len(ts), pf_r(ts)
        rows.append(row(f"G16.{i}n", f"yarı {i} işlem sayısı", n, THRESH["G16_HALF_N_MIN"], n >= THRESH["G16_HALF_N_MIN"]))
        rows.append(row(f"G16.{i}pf", f"yarı {i} PF", pf, THRESH["G16_HALF_PF_MIN"], pf >= THRESH["G16_HALF_PF_MIN"]))
    return entry(rows)


def rule_g17(n: int, pf: float, dd_r: float, sum_r_: float) -> Dict[str, Any]:
    """FINAL veto kapısı. status: SONUÇSUZ | VETO | FINAL-BELİRSİZ | FINAL-TUTARLI."""
    if n < THRESH["G17_N_MIN"]:
        status = "SONUÇSUZ"
    elif dd_r > THRESH["G17_DD_R_MAX"] or pf < THRESH["G17_VETO_PF"]:
        status = "VETO"
    elif pf >= THRESH["G17_CONSISTENT_PF"] and sum_r_ > 0:
        status = "FINAL-TUTARLI"
    else:
        status = "FINAL-BELİRSİZ"
    r = row("G17", "FINAL veto (N>=20, DD_R<=25, PF>=0.90)", pf, THRESH["G17_VETO_PF"],
            status in ("FINAL-TUTARLI", "FINAL-BELİRSİZ"), ">=", status)
    r["status"] = status
    r.update({"n": n, "dd_r": dd_r, "sum_r": sum_r_})
    return r


def b0_pf_usd(trades: Sequence[Tr]) -> float:
    """FINAL B0 çapası E16'nın USD PnL PF'idir (R-PF DEĞİL; marj farklıysa ikisi ayrışır). Kırpılmaz."""
    return pf_of([t.pnl for t in trades])


def b0_anchor(n: int, pf: float) -> Dict[str, Any]:
    """B0 FINAL çapası: 320 işlem birebir ve 0.895 <= PF < 0.905 (iki hanede 0.90)."""
    ok = n == THRESH["B0_ANCHOR_TRADES"] and THRESH["B0_ANCHOR_PF_LO"] <= pf < THRESH["B0_ANCHOR_PF_HI"]
    return {"trades": n, "pf": pf, "ok": bool(ok)}


# ==========================================================================
# 12. DEV AŞAMASI: dev-screen (G0-G5) -> G6 kaplama koşuları -> dev-decide
# ==========================================================================
DEV_REQUIRED_BASE = list(FAMILY) + list(DESCRIPTIVE) + list(REFS_GATED)


def dev_required_labels() -> List[str]:
    labs = [f"DEV_{a}" for a in DEV_REQUIRED_BASE]
    for ref in QUARTER_REFS:
        labs += [f"DEV_{ref}_Q{q}" for q in (1, 2, 3, 4)]
    return labs


def tf_minutes(ctx: Ctx, arm: str) -> int:
    return int(str(ctx.vid[arm]["entry_tf"]).rstrip("mh")) * (60 if str(ctx.vid[arm]["entry_tf"]).endswith("h") else 1)


def bh_decisions(ps: Sequence[float]) -> Tuple[Tuple[bool, ...], List[float]]:
    q = bh_qvalues(ps)
    return tuple(x <= THRESH["G3_Q_MAX"] for x in q), q


def refine_bh(ps: List[float], valid: Sequence[bool], recompute: Callable[[int], float]) -> Tuple[List[float], List[bool]]:
    """Her geçerli arm'ın p'si için Monte-Carlo hata kuralı: p ± 3·SE içinde HERHANGİ bir arm'ın
    BH kararı ters dönebiliyorsa o p B=199999 ile yeniden hesaplanır."""
    ps = list(ps)
    refined = [False] * len(ps)
    for i in range(len(ps)):
        if not valid[i]:
            continue

        def decide(pi: float, i: int = i) -> Tuple[bool, ...]:
            v = list(ps)
            v[i] = pi
            return bh_decisions(v)[0]

        ps[i], refined[i] = refine_p(ps[i], decide, lambda i=i: recompute(i))
    return ps, refined


def cmd_dev_screen(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("dev-screen hedef almaz")
    ctx = verify_lock(root)
    if (root / "DEV_DECISION.json").exists():
        raise StageError("DEV_DECISION.json yazıldı: dev-screen artık çalıştırılamaz")
    require_smoke(ctx, "dev-screen")
    led = Ledger(root)
    led.assert_clean()
    missing = [lb for lb in dev_required_labels() if not led.has(lb)]
    if missing:
        raise StageError(f"DEV taban koşuları tamamlanmadan analiz yok; eksik: {missing[:4]}{'...' if len(missing) > 4 else ''}")
    labels = month_labels(ctx)
    series = {a: load_series(ctx, "DEV", a, "", led) for a in FAMILY}
    arms: Dict[str, Any] = {}
    valid: List[bool] = []
    ps: List[float] = []
    tobs: Dict[str, float] = {}
    for a in FAMILY:
        s = need(series[a], f"DEV_{a}")
        g0 = entry(rule_validity("G0", s.trades, s.missed, is_scaled(ctx, a), dev=True))
        g1 = rule_g1(s.n)
        ok = gate_ok(g0) and gate_ok(g1)
        valid.append(ok)
        if ok:
            p, t = p_for(s, "dev", THRESH["BLOCK_PRIMARY"])
        else:
            p, t = 1.0, t_obs_of(daily_vector(s.trades, s.windows))    # G0/G1 başarısız: p := 1.0 (m=6 sabit)
        ps.append(p)
        tobs[a] = t
        arms[a] = {"G0": g0, "G1": g1, "n": s.n, "pf": pf_r(s.trades), "t_obs": t, "p7": p,
                   "validity": {"scaled": is_scaled(ctx, a), "sessiz_dusus": silent_ratio(s.missed, s.n)[0]}}
    if len(FAMILY) != THRESH["BH_M"]:
        raise IntegrityError("aile büyüklüğü BH_M ile eşleşmiyor")
    ps, refined = refine_bh(ps, valid, lambda i: p_for(series[FAMILY[i]], "dev", THRESH["BLOCK_PRIMARY"], hi=True)[0])
    dec, qs = bh_decisions(ps)
    g6_needed = []
    for i, a in enumerate(FAMILY):
        s = series[a]
        arms[a]["p7"] = ps[i]
        arms[a]["mc_refined"] = refined[i]
        arms[a]["q"] = qs[i]
        arms[a]["G2"] = rule_g2(arms[a]["pf"])
        arms[a]["G3"] = rule_g3(qs[i])
        arms[a]["G4"] = rule_g4(s.trades)
        arms[a]["G5"] = rule_g5(s.trades, labels)
        arms[a]["pass_g0_g5"] = all(gate_ok(arms[a][g]) for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
        if arms[a]["pass_g0_g5"]:
            g6_needed.append(a)
    out = dict(arms)
    out["_meta"] = {"l": ctx.l, "h": ctx.h, "created_utc": utc_now(), "g6_needed": g6_needed,
                    "bh": {"p": dict(zip(FAMILY, ps)), "q": dict(zip(FAMILY, qs)), "m": THRESH["BH_M"]}}
    atomic_write(root / "DEV_SCREEN.json", dump_json(out))
    # MÜHÜR: yalnız G6 koşusu gereken arm adları basılır (PF/p/işlem sayısı yankılanmaz)
    if g6_needed:
        print("G6 kaplama koşuları gereken arm'lar: " + " ".join(g6_needed))
    else:
        print("G0-G5'i geçen arm yok: DUR (VAL verisi indirilmez, FINAL dokunulmaz)")
    return 0


def select_advancing(passing: Sequence[str], t_obs: Dict[str, float], tf_min: Dict[str, int]) -> Tuple[List[str], List[str]]:
    """İlerleme seçimi (§8): aile sırasıyla; her giriş TF'den en çok biri (t_obs yüksek olan; eşitlikte
    aile sırasındaki ilk), sonra t_obs'a göre (eşitlikte BÜYÜK TF), toplamda en çok DEV_MAX_ADVANCE.
    Döner (hepsinin sıralaması, ilerleyenler)."""
    def order(a: str) -> Tuple[float, int, int]:
        return (-t_obs[a], -tf_min[a], list(FAMILY).index(a))

    ranked_all = sorted(passing, key=order)
    per_tf: Dict[int, List[str]] = {}
    for a in ranked_all:
        per_tf.setdefault(tf_min[a], []).append(a)
    reps = [a for lst in per_tf.values() for a in lst[: int(THRESH["DEV_MAX_PER_TF"])]]
    chosen = sorted(reps, key=order)
    return ranked_all, chosen[: int(THRESH["DEV_MAX_ADVANCE"])]


def cmd_dev_decide(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("dev-decide hedef almaz")
    ctx = verify_lock(root)
    if (root / "DEV_DECISION.json").exists():
        raise StageError("DEV_DECISION.json zaten var; tek kez yazılır (ikinci yazım reddedildi)")
    scr_p = root / "DEV_SCREEN.json"
    if not scr_p.exists():
        raise StageError("DEV_SCREEN.json yok: önce dev-screen")
    screen = json.loads(scr_p.read_text(encoding="utf-8"))
    if screen["_meta"]["l"] != ctx.l:
        raise StageError("DEV_SCREEN.json başka bir kilitle üretilmiş")
    led = Ledger(root)
    led.assert_clean()
    needed = screen["_meta"]["g6_needed"]
    g6: Dict[str, Any] = {}
    passing6: List[str] = []
    for a in needed:
        pf = {}
        sums = {}
        for ov in G6_OVERLAYS:
            s = need(load_series(ctx, "DEV", a, ov, led), f"DEV_{a}_{ov}")       # eksikse StageError (4)
            pf[ov], sums[ov] = pf_r(s.trades), sum_r(s.trades)
        g = rule_g6(pf["ROB_FEE2"], sums["ROB_DLY1"], pf["ROB_TAKER"])
        g6[a] = {"fee2": g["rows"][0], "dly1": g["rows"][1], "taker": g["rows"][2], "pass": g["pass"]}
        if g["pass"]:
            passing6.append(a)
    t_obs = {a: float(screen[a]["t_obs"]) for a in FAMILY}
    tf_min = {a: tf_minutes(ctx, a) for a in FAMILY}
    ranked_all, advanced = select_advancing(passing6, t_obs, tf_min)
    doc = {
        "g6": g6,
        "ranked": [{"arm": a, "t_obs": t_obs[a], "entry_tf": ctx.vid[a]["entry_tf"]} for a in ranked_all],
        "advanced": advanced, "k": len(advanced),
        "bh": screen["_meta"]["bh"],
        "stop": not advanced,
        "g0_g5_pass": needed,
        "l": ctx.l, "created_utc": utc_now(),
    }
    write_once(root / "DEV_DECISION.json", dump_json(doc))
    print("DEV_DECISION.json yazıldı (tek kez). İlerleyen: " + (" ".join(advanced) if advanced else "YOK -> DUR")
          + ". Mühür kalkar.")
    return 0


# ==========================================================================
# 13. TEŞHİS (karar vermez): Z0 brüt-vs-net, PF [kötümser, iyimser], FEE1.5 etiketi, BAR-ORDER-BELİRSİZ
# ==========================================================================
def z0_reading(gross_pf: float, net_pf: float) -> str:
    if gross_pf >= THRESH["Z0_GROSS_PF_EDGE"] and net_pf < THRESH["Z0_NET_PF"]:
        return "sinyalin ham kenarı var, ücret yiyor"
    if gross_pf < THRESH["Z0_GROSS_PF_EDGE"]:
        return "ücretten önce de kenar yok"
    return "ücret sonrası da pozitif"


def fee15_flag(fee2_failed: bool, pf15: float) -> bool:
    """MARJİNAL-ÜCRETE-DUYARLI: FEE2'yi geçemeyip FEE1.5'i geçen arm."""
    return bool(fee2_failed and pf15 >= THRESH["FEE15_PF_MIN"])


def bar_order_flag(pf_pess: float, pf_opt: float, q_opt: float, advanced: bool) -> bool:
    """BAR-ORDER-BELİRSİZ: kötümser PF G2'yi geçemiyor, iyimser PF >= eşik ve p kapısını (BH q) geçiyor;
    zaten ilerleyen arm etiketlenmez. Karar vermez, arm İLERLEMEZ."""
    return bool(pf_pess < THRESH["G2_PF_MIN"] and pf_opt >= THRESH["BAR_ORDER_OPT_PF_MIN"]
                and q_opt <= THRESH["G3_Q_MAX"] and not advanced)


def cmd_diag(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("diag hedef almaz")
    ctx = verify_lock(root)
    dd = _read_json_stage(root, "DEV_DECISION.json", "teşhis DEV kararından sonra çalışır")
    screen = json.loads((root / "DEV_SCREEN.json").read_text(encoding="utf-8"))
    led = Ledger(root)
    led.assert_clean()
    p_dev = [float(screen[a]["p7"]) for a in FAMILY]
    arms: Dict[str, Any] = {}
    for a in FAMILY:
        base = load_series(ctx, "DEV", a, "", led)
        z0 = load_series(ctx, "DEV", a, "DIAG_Z0", led)
        sbo = load_series(ctx, "DEV", a, "DIAG_SBO", led)
        need(base, f"DEV_{a}")
        need(z0, f"DEV_{a}_DIAG_Z0")
        need(sbo, f"DEV_{a}_DIAG_SBO")
        pf_net, pf_opt, pf_z0 = pf_r(base.trades), pf_r(sbo.trades), pf_r(z0.trades)
        g6a_fail = a in dd["g6"] and not dd["g6"][a]["fee2"]["pass"]
        need15 = g6a_fail or a in dd["advanced"]
        fee15 = load_series(ctx, "DEV", a, "DIAG_FEE15", led, required=need15)
        labs: List[str] = []
        info: Dict[str, Any] = {"pf_pess": pf_net, "pf_opt": pf_opt, "pf_z0": pf_z0,
                                "z0_reading": z0_reading(pf_z0, pf_net), "n_pess": base.n, "n_opt": sbo.n}
        if fee15 is not None:
            pf15 = pf_r(fee15.trades)
            info["pf_fee15"] = pf15
            if fee15_flag(g6a_fail, pf15):
                labs.append("MARJİNAL-ÜCRETE-DUYARLI")
        if pf_net < THRESH["G2_PF_MIN"] and pf_opt >= THRESH["BAR_ORDER_OPT_PF_MIN"]:
            p_opt, _t = p_for(sbo, "diag", THRESH["BLOCK_PRIMARY"])
            ps = list(p_dev)
            ps[FAMILY.index(a)] = p_opt
            q_opt = bh_qvalues(ps)[FAMILY.index(a)]
            info["p_opt"], info["q_opt"] = p_opt, q_opt
            if bar_order_flag(pf_net, pf_opt, q_opt, a in dd["advanced"]):
                labs.append("BAR-ORDER-BELİRSİZ")
        info["labels"] = labs
        arms[a] = info
    out = {"arms": arms, "note": "teşhis; karar vermez", "created_utc": utc_now()}
    atomic_write(root / "DIAG.json", dump_json(out))
    flagged = sorted(a for a, v in arms.items() if v["labels"])
    print("DIAG.json yazıldı. Etiketli arm'lar: " + (" ".join(f"{a}[{','.join(arms[a]['labels'])}]" for a in flagged) or "yok"))
    return 0


# ==========================================================================
# 14. VAL AŞAMASI (G7-G16) ve FINAL (G17)
# ==========================================================================
def val_winner(vd: Dict[str, Any]) -> Optional[str]:
    return (vd.get("_meta") or {}).get("winner")


def best_by_t(cands: Sequence[str], t_obs: Dict[str, float], tf_min: Dict[str, int]) -> Optional[str]:
    """En yüksek t_obs; eşitlikte BÜYÜK TF; sonra aile sırası."""
    if not cands:
        return None
    return sorted(cands, key=lambda a: (-t_obs[a], -tf_min[a], list(FAMILY).index(a)))[0]


def val_p(series: Series, block: int, alpha_over_k: float) -> Tuple[float, float, bool]:
    """VAL p-değeri + Monte-Carlo hata kuralı: karar eşiği α/k (Bonferroni) p ± 3·SE içinde dönebiliyorsa
    p, B=BOOT_N_HI ile (`…|hi` tohumu) yeniden hesaplanır. Döner (p, t_obs, yeniden_hesaplandı_mı)."""
    p, t = p_for(series, "val", block)
    p, refined = refine_p(p, lambda q: q <= alpha_over_k, lambda: p_for(series, "val", block, hi=True)[0])
    return p, t, refined


def cmd_val(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("val hedef almaz")
    ctx = verify_lock(root)
    dd = _read_json_stage(root, "DEV_DECISION.json", "VAL, DEV kararından sonra çalışır")
    if not dd.get("advanced"):
        raise StageError("DEV'de ilerleyen arm yok: VAL çalıştırılmaz")
    if (root / "VAL_DECISION.json").exists():
        raise StageError("VAL_DECISION.json zaten var; tek kez yazılır (ikinci yazım reddedildi)")
    days = ctx.window_days("VAL")
    if days < THRESH["VAL_MIN_DAYS"]:        # §9.5: kalan VAL < 365 gün
        doc = {"_meta": {"winner": None, "status": "VAL-YAPILAMADI", "days": days, "l": ctx.l,
                         "created_utc": utc_now()}}
        write_once(root / "VAL_DECISION.json", dump_json(doc))
        print("VAL-YAPILAMADI (altyapı hatası; FINAL'e dokunulmaz)")
        return 0
    led = Ledger(root)
    led.assert_clean()
    labels = month_labels(ctx)
    adv: List[str] = list(dd["advanced"])
    k = len(adv)
    full = full_months_of(ctx.window("VAL"))
    tf_min = {a: tf_minutes(ctx, a) for a in FAMILY}
    out: Dict[str, Any] = {}
    t_obs: Dict[str, float] = {}
    passing: List[str] = []
    a7, a28 = THRESH["G9_ALPHA_P7"] / k, THRESH["G9_ALPHA_P28"] / k
    for a in adv:
        base = load_series(ctx, "VAL", a, "", led)
        dev = load_series(ctx, "DEV", a, "", led)
        ov = {o: load_series(ctx, "VAL", a, o, led) for o in VAL_OVERLAYS}
        need(base, f"VAL_{a}")
        need(dev, f"DEV_{a}")
        for o, ser in ov.items():
            need(ser, f"VAL_{a}_{o}")
        p7, t, r7 = val_p(base, THRESH["BLOCK_PRIMARY"], a7)
        p28, _t28, r28 = val_p(base, THRESH["BLOCK_SECONDARY"], a28)
        t_obs[a] = t
        pf = pf_r(base.trades)
        gates = {
            "G7": rule_g7(base.n), "G8": rule_g8(pf), "G9": rule_g9(p7, p28, k),
            "G10": entry([rule_g10a(base.trades, labels), rule_g10b(dev.trades, base.trades, labels)]),
            "G11": rule_g11(base.trades, full), "G12": rule_g12(base.trades), "G13": rule_g13(base),
            "G14": entry(rule_validity("G14", base.trades, base.missed, is_scaled(ctx, a), dev=False)),
            "G15": rule_g15(pf_r(ov["ROB_FEE2"].trades), sum_r(ov["ROB_DLY1"].trades),
                            pf_r(ov["ROB_TAKER"].trades), sum_r(ov["ROB_WALL"].trades)),
            "G16": rule_g16(base.trades, full),
        }
        all_pass = all(gate_ok(g) for g in gates.values())
        clamp = any(r.get("note") == "kelepçe-yönetimli" for r in gates["G14"]["rows"])
        out[a] = dict(gates)
        out[a].update({"p7": p7, "p28": p28, "t_obs": t, "n": base.n, "pf": pf, "mc_refined": [r7, r28],
                       "kelepce_yonetimli": clamp, "pass_all": all_pass, "winner": False})
        if all_pass:
            passing.append(a)
    winner = best_by_t(passing, t_obs, tf_min)
    if winner:
        out[winner]["winner"] = True
    out["_meta"] = {"winner": winner, "k": k, "advanced": adv, "status": "TAMAM", "days": days,
                    "full_months": len(full), "l": ctx.l, "created_utc": utc_now()}
    write_once(root / "VAL_DECISION.json", dump_json(out))
    print("VAL_DECISION.json yazıldı (tek kez). Doğrulanan: " + (" ".join(passing) if passing else "YOK -> DUR")
          + (f"; kazanan {winner}" if winner else ""))
    return 0


def final_outcome(anchor_ok: bool, status: str) -> str:
    if not anchor_ok or status == "SONUÇSUZ":
        return "ADAY-FINAL-SONUÇSUZ"
    if status == "VETO":
        return "FINAL-VETO"
    return "KAZANAN-HARNESS"


def cmd_final(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("final hedef almaz")
    ctx = verify_lock(root)
    vd = _read_json_stage(root, "VAL_DECISION.json", "FINAL, VAL kararından sonra çalışır")
    winner = val_winner(vd)
    if not winner:
        raise StageError("VAL kazananı yok: FINAL'e dokunulmaz")
    if (root / "FINAL.json").exists():
        raise StageError("FINAL.json zaten var; tek atış (ikinci yazım reddedildi)")
    led = Ledger(root)
    led.assert_clean()
    b0 = load_series(ctx, "FINAL", "B0", "", led)
    arm = load_series(ctx, "FINAL", winner, "", led)
    need(b0, "FINAL_B0")
    need(arm, f"FINAL_{winner}")
    anchor = b0_anchor(b0.n, b0_pf_usd(b0.trades))      # E16 PF'i USD PnL üzerindendi
    pf = pf_r(arm.trades)
    g17 = rule_g17(arm.n, pf, dd_r_of(arm), sum_r(arm.trades))
    outcome = final_outcome(anchor["ok"], g17["status"])
    doc = {"b0_anchor": anchor,
           "arm": {"name": winner, "status": g17["status"], "n": arm.n, "pf": pf, "dd_r": dd_r_of(arm),
                   "sum_r": sum_r(arm.trades), "gate": g17},
           "valid": anchor["ok"], "outcome": outcome,
           "note": "KAZANAN (FINAL doğrulamadı)" if outcome == "KAZANAN-HARNESS" and g17["status"] == "FINAL-BELİRSİZ" else "",
           "l": ctx.l, "created_utc": utc_now()}
    write_once(root / "FINAL.json", dump_json(doc))
    print(f"FINAL.json yazıldı (tek atış). B0 çapası: {'TAMAM' if anchor['ok'] else 'TUTMADI -> FINAL GEÇERSİZ'}; sonuç: {outcome}")
    return 0


# ==========================================================================
# 15. DECIDE: DECISION.md + results.csv
# ==========================================================================
def md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def f4(x: Any) -> str:
    """4 ondalık; PF dışı büyüklükte (DD_R, Calmar) sonsuz "inf" basılır. PF'ler pf_r'de zaten kırpılıdır."""
    if isinstance(x, float):
        return "inf" if x == INF else (f"{x:.4f}" if x == x else "nan")
    return str(x)


def _betacf(a_: float, b_: float, x: float) -> float:
    """Eksik beta fonksiyonu için sürekli kesir (Lentz; Numerical Recipes betacf)."""
    tiny = 1e-300
    qab, qap, qam = a_ + b_, a_ + 1.0, a_ - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b_ - m) * x / ((qam + m2) * (a_ + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a_ + m) * (qab + m) * x / ((a_ + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _betai(a_: float, b_: float, x: float) -> float:
    """Düzgeleştirilmiş eksik beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a_ + b_) - math.lgamma(a_) - math.lgamma(b_) + a_ * math.log(x) + b_ * math.log1p(-x))
    if x < (a_ + 1.0) / (a_ + b_ + 2.0):
        return bt * _betacf(a_, b_, x) / a_
    return 1.0 - bt * _betacf(b_, a_, 1.0 - x) / b_


def t_sf(t: float, df: int) -> float:
    """Student t üst kuyruk olasılığı P(T >= t), df serbestlik derecesi (tek-yönlü p)."""
    if t == INF:
        return 0.0
    if t == -INF:
        return 1.0
    tail = 0.5 * _betai(df / 2.0, 0.5, df / (df + t * t))
    return tail if t > 0 else 1.0 - tail


def month_level_t(trades: Sequence[Tr], full_months: Sequence[str]) -> Optional[Dict[str, Any]]:
    """BİLGİ (§7.2, karar vermez): tam ayların giriş-ayı Σ R'si üzerinde tek-yönlü tek-örneklem t testi
    (12 DEV / 22 VAL gözlemi). 3'ten az tam ay -> None. Gözlem bağımsızlığı varsayar (ay etkisi için kaba bir bakış)."""
    m = len(full_months)
    if m < 3:
        return None
    sums = group_sums(trades, lambda t: t.month)
    x = [sums.get(k, 0.0) for k in full_months]
    mean = sum(x) / m
    sd = statistics.stdev(x)
    if sd <= 0.0:
        t = 0.0 if mean == 0.0 else math.copysign(INF, mean)
    else:
        t = mean / (sd / math.sqrt(m))
    return {"m": m, "t": t, "p": t_sf(t, m - 1) if sd > 0.0 else (1.0 if mean <= 0.0 else 0.0)}


def tf_trend(values: Sequence[float]) -> str:
    """Üç TF (5m -> 15m -> 30m) üzerinde PF eğilimi etiketi (yalnız rapor)."""
    d = [b - a_ for a_, b in zip(values, values[1:])]
    if all(x > 0 for x in d):
        return "monoton artan"
    if all(x < 0 for x in d):
        return "monoton azalan"
    return "monoton değil"


def bull_check(up_pnl: float, b0_up_pnl: float) -> Optional[bool]:
    """CLAUDE.md kural #1 (boğa): UP-ayı USD PnL >= BULL_PNL_FRAC x B0'ın UP PnL'i. B0 UP PnL <= 0 -> madde boş (None)."""
    if b0_up_pnl <= 0:
        return None
    return up_pnl >= THRESH["BULL_PNL_FRAC"] * b0_up_pnl


NO_WINNER_LABELS = ("DEV-YOK", "VAL-YOK", "FINAL-VETO")


def ci_upper_sentence(reports: Dict[str, Dict[str, Any]], label: str) -> str:
    """Kazanan YOK sonuçlarında "güç sınırı" cümlesi: DEV/VAL arm'larının PF %90 GA üst sınırlarının en büyüğü."""
    if label not in NO_WINNER_LABELS:
        return ""
    best: Optional[Tuple[float, str]] = None
    for key, rp in reports.items():
        base = key[4:] if key.startswith("VAL_") else key
        ci = rp.get("pf_ci90")
        if base in FAMILY and ci:
            if best is None or ci[1] > best[0]:
                best = (float(ci[1]), key)
    if best is None:
        return ""
    return (f"PF %90 GA üst sınırı: aile arm'ları arasında en yükseği {best[0]:.4f} ({best[1]}); bu veriyle bunun üstünde bir "
            f"gerçek PF savunulamaz (kenar yokluğunun kanıtı değil, güç sınırıdır).")


def arm_report(ctx: Ctx, s: Series, labels: Dict[str, str], stage: str, extra_p: bool = True) -> Dict[str, Any]:
    """Bir (arm x set) için Ek-A çıktı alanları (§5.3 'Çıktılar')."""
    ts = s.trades
    n = len(ts)
    dd_r = dd_r_of(s)
    sr = sum_r(ts)
    d: Dict[str, Any] = {
        "n": n, "wr": (sum(1 for t in ts if t.pnl > 0) / n * 100.0) if n else 0.0,
        "pf": pf_r(ts), "net_r": sr, "pnl_usd": sum(t.pnl for t in ts), "dd_usd": s.dd_usd, "dd_r": dd_r,
        "calmar_r": calmar_r(sr, s.days, dd_r), "days": s.days,
        "months": month_table(ts, labels), "groups": group_table(ts, labels),
        "symbols": {sym: {"n": len([t for t in ts if t.symbol == sym]), "sum_r": sum_r([t for t in ts if t.symbol == sym]),
                          "pf": pf_r([t for t in ts if t.symbol == sym])} for sym in sorted({t.symbol for t in ts})},
        "exits": {r: sum(1 for t in ts if t.reason == r) for r in sorted({t.reason for t in ts})},
        "lev_hist": {str(lv): sum(1 for t in ts if t.lev == lv) for lv in sorted({t.lev for t in ts})},
        "eod": eod_count(ts), "diagnostics": s.diagnostics,
    }
    l3, l20 = lev_shares(ts)
    d["lev3_share"], d["lev20_share"] = l3, l20
    ratio, silent, ham = silent_ratio(s.missed, n)
    d["signals"] = {"ham_sinyal": ham, "sessiz_dusus": silent, "sessiz_oran": ratio, "islem": n, "missed": dict(s.missed)}
    dxr: Dict[str, Any] = {}
    for dr in ("LONG", "SHORT"):
        for g in ("UP", "FLAT", "DOWN"):
            sub = [t for t in ts if t.direction == dr and labels.get(t.month) == g]
            dxr[f"{dr}/{g}"] = {"n": len(sub), "pf": pf_r(sub), "sum_r": sum_r(sub)}
    d["dir_x_regime"] = dxr
    d["dir"] = {dr: {"n": len([t for t in ts if t.direction == dr]), "sum_r": sum_r([t for t in ts if t.direction == dr])}
                for dr in ("LONG", "SHORT")}
    if extra_p and n:
        x = daily_vector(ts, s.windows)
        d["t_obs"] = t_obs_of(x)
        d["p7"] = bootstrap_p(x, THRESH["BLOCK_PRIMARY"], seed_for(stage, s.arm, 7))[0]
        d["p28"] = bootstrap_p(x, THRESH["BLOCK_SECONDARY"], seed_for(stage, s.arm, 28))[0]
        d["p14_info"] = bootstrap_p(x, THRESH["BLOCK_INFO"], seed_for(stage, s.arm, 14))[0]
        pos, neg = daily_pos_neg(ts, s.windows)
        d["pf_ci90"] = pf_confidence(pos, neg, THRESH["BLOCK_PRIMARY"], seed_for(stage, s.arm, 7) ^ 1)
        try:
            fm = full_months_of(ctx.window(s.set))
        except KeyError:
            fm = []
        d["month_t"] = month_level_t(ts, fm)
    return d


def _pf_pool(trades: Sequence[Tr]) -> float:
    return pf_r(trades)


def results_rows(ctx: Ctx, led: Ledger) -> List[Dict[str, Any]]:
    rows = []
    cache: Dict[str, Series] = {}

    def one(label: str) -> Series:
        if label not in cache:
            cache[label] = _load_label(ctx, led, label)
        return cache[label]

    for label in sorted(led.entries):
        info = parse_label(label, ctx.variants)
        if info.set == "SMOKE":
            continue
        s = one(label)
        n = s.n
        pf = pf_r(s.trades)
        pf_pess: Any = ""
        pf_opt: Any = ""
        if info.set == "DEV" and info.variant in FAMILY and info.overlay in ("", "DIAG_SBO"):
            base_label, sbo_label = f"DEV_{info.variant}", f"DEV_{info.variant}_DIAG_SBO"
            if info.overlay == "" and led.has(sbo_label):
                pf_pess, pf_opt = pf, pf_r(one(sbo_label).trades)
            elif info.overlay == "DIAG_SBO" and led.has(base_label):
                pf_pess, pf_opt = pf_r(one(base_label).trades), pf
        rows.append({
            "label": label, "variant": info.variant, "overlay": info.overlay, "set": info.set,
            "quarter": info.quarter or "", "trades": n,
            "wr": f"{(sum(1 for t in s.trades if t.pnl > 0) / n * 100.0) if n else 0.0:.4f}",
            "pf": f"{pf:.6f}",
            "pf_pess": f"{pf_pess:.6f}" if pf_pess != "" else "",
            "pf_opt": f"{pf_opt:.6f}" if pf_opt != "" else "",
            "net_r": f"{sum_r(s.trades):.6f}", "total_pnl_usd": f"{sum(t.pnl for t in s.trades):.4f}",
            "bar_max_dd_usd": f"{s.dd_usd:.4f}", "dd_r": f"{dd_r_of(s):.6f}" if dd_r_of(s) != INF else "inf",
            "s_arm": f"{s.s_arm:g}"})
    return rows


CSV_COLS = ["label", "variant", "overlay", "set", "quarter", "trades", "wr", "pf", "pf_pess", "pf_opt", "net_r",
            "total_pnl_usd", "bar_max_dd_usd", "dd_r", "s_arm"]


def decide_outcome(root: Path) -> Tuple[str, str, Dict[str, Any]]:
    """(etiket, ek not, belgeler). Eksik aşama dosyası -> StageError."""
    dd = _read_json_stage(root, "DEV_DECISION.json", "decide, DEV kararından sonra çalışır")
    docs: Dict[str, Any] = {"dev": dd}
    if not dd.get("advanced"):
        return "DEV-YOK", "", docs
    vd = _read_json_stage(root, "VAL_DECISION.json", "DEV'de ilerleyen var: VAL kararı gerekir")
    docs["val"] = vd
    if (vd.get("_meta") or {}).get("status") == "VAL-YAPILAMADI":
        return "VAL-YAPILAMADI", "", docs
    if not val_winner(vd):
        return "VAL-YOK", "", docs
    fd = _read_json_stage(root, "FINAL.json", "VAL kazananı var: FINAL kararı gerekir")
    docs["final"] = fd
    return fd["outcome"], fd.get("note", ""), docs


def render_decision(ctx: Ctx, root: Path, label: str, note: str, docs: Dict[str, Any], led: Ledger) -> str:
    labels = month_labels(ctx)
    L: List[str] = []
    L.append("# E17 KARAR RAPORU (otomatik; analyze_e17.py decide)")
    L.append("")
    L.append(f"- Kilit: L=`{ctx.l}` H=`{ctx.h}`")
    L.append(f"- **Sonuç etiketi: {label}**" + (f" — {note}" if note else ""))
    L.append(f"- {SENTENCES.get(label, NO_WINNER_SENTENCE)}")
    L.append("- 1h (S60/T60): bu veriyle karara bağlanamaz (YETERSİZ GÜÇ: PF ≈ 1.9'un altı tespit edilemez).")
    L.append("")
    dd = docs["dev"]
    screen = json.loads((root / "DEV_SCREEN.json").read_text(encoding="utf-8")) if (root / "DEV_SCREEN.json").exists() else {}
    diag = json.loads((root / "DIAG.json").read_text(encoding="utf-8")) if (root / "DIAG.json").exists() else {"arms": {}}
    L.append("## DEV (2025) — altı aile arm'ı")
    rows = []
    reports: Dict[str, Dict[str, Any]] = {}
    for a in FAMILY:
        s = need(load_series(ctx, "DEV", a, "", led), f"DEV_{a}")
        rp = arm_report(ctx, s, labels, "dev")
        reports[a] = rp
        sc = screen.get(a, {})
        dg = diag["arms"].get(a, {})
        gstat = "".join(("+" if sc.get(g, {}).get("pass") else "-") for g in ("G0", "G1", "G2", "G3", "G4", "G5")) if sc else "?"
        g6 = dd["g6"].get(a)
        rows.append([a, rp["n"], f"{rp['wr']:.2f}", f4(rp["pf"]),
                     f"[{f4(dg['pf_pess'])}, {f4(dg['pf_opt'])}]" if dg else "n/a",
                     f"{rp['net_r']:.2f}", f4(sc.get("p7", rp.get("p7", 1.0))), f4(sc.get("q", 1.0)) if sc else "n/a",
                     f4(rp.get("t_obs", 0.0)), f"[{f4(rp['pf_ci90'][0])}, {f4(rp['pf_ci90'][1])}]" if "pf_ci90" in rp else "n/a",
                     gstat, ("G6 " + ("+" if g6["pass"] else "-")) if g6 else "G6 -", "İLERLER" if a in dd["advanced"] else ""])
    L.append(md_table(["arm", "N", "WR%", "PF", "PF[kötümser,iyimser]", "ΣR", "p(b=7)", "q", "t_obs", "PF %90 GA",
                       "G0-G5", "G6", "karar"], rows))
    L.append("")
    L.append("### Kapı satırları (değer vs eşik, 6 hane; SINIRDA = eşiğe 0.005 içinde)")
    for a in FAMILY:
        sc = screen.get(a)
        if not sc:
            continue
        L.append(f"**{a}**")
        for g in ("G0", "G1", "G2", "G3", "G4", "G5"):
            for r in (sc[g]["rows"] if "rows" in sc[g] else [sc[g]]):
                L.append("- " + fmt_row(r))
        if a in dd["g6"]:
            for kx in ("fee2", "dly1", "taker"):
                L.append("- " + fmt_row(dd["g6"][a][kx]))
        L.append("")
    # --- S-vs-T ve TF eğilimi (yalnız rapor; karar vermez) ---
    L.append("### S – T karşılaştırması ve TF eğilimi (yalnız rapor; karar vermez; PF 5'te kırpılı)")
    cmp_rows = []
    for tf in ("5", "15", "30"):
        rs_, rt_ = reports[f"S{tf}"], reports[f"T{tf}"]
        cmp_rows.append([f"{tf}m", f4(rs_["pf"]), f4(rt_["pf"]), f"{rs_['pf'] - rt_['pf']:+.4f}",
                         f"{rs_['net_r']:.2f}", f"{rt_['net_r']:.2f}"])
    L.append(md_table(["giriş TF", "PF S", "PF T", "PF S−T", "ΣR S", "ΣR T"], cmp_rows))
    L.append("")
    for fam, nm in (("S", "ölçekli (S)"), ("T", "yalnız zamanlama (T)")):
        vals = [reports[f"{fam}{tf}"]["pf"] for tf in ("5", "15", "30")]
        L.append(f"- TF eğilimi {nm}: PF 5m→15m→30m = " + " → ".join(f4(v) for v in vals) + f" ({tf_trend(vals)})")
    L.append("")
    # --- açıklayıcı ve referanslar ---
    L.append("## Açıklayıcı (S60/T60) ve referanslar (B0, R1, S5G, T5G)")
    rows = []
    for a in list(DESCRIPTIVE) + list(REFS_GATED) + list(QUARTER_REFS):
        s = load_series(ctx, "DEV", a, "", led, required=False)
        if s is None:
            continue
        rp = arm_report(ctx, s, labels, "dev", extra_p=(a in DESCRIPTIVE))
        reports[a] = rp
        ci = rp.get("pf_ci90")
        rows.append([a, rp["n"], f4(rp["pf"]), f"{rp['net_r']:.2f}", f"{rp['pnl_usd']:.2f}",
                     f"[{f4(ci[0])}, {f4(ci[1])}]" if ci else "—"])
    L.append(md_table(["arm", "N", "PF", "ΣR", "PnL USD", "PF %90 GA"], rows))
    L.append("")
    for a, g in (("S5", "S5G"), ("T5", "T5G")):
        if a in reports and g in reports:
            L.append(f"- Kapı etkisi {a} → {g}: PF {f4(reports[a]['pf'])} → {f4(reports[g]['pf'])}; N {reports[a]['n']} → {reports[g]['n']}")
    if "B0" in reports and "R1" in reports:
        L.append(f"- B0 (canlı 1m) PF {f4(reports['B0']['pf'])} (N {reports['B0']['n']}); R1 (B0+BG) PF {f4(reports['R1']['pf'])} (N {reports['R1']['n']})")
    for a in FAMILY:
        dg = diag["arms"].get(a)
        if dg:
            L.append(f"- Z0 {a}: brüt PF {f4(dg['pf_z0'])} vs net {f4(dg['pf_pess'])} → {dg['z0_reading']}"
                     + (f"; etiketler: {', '.join(dg['labels'])}" if dg["labels"] else ""))
    L.append("")
    # --- CLAUDE.md yasak #1 satırları ---
    L.append("## CLAUDE.md kural #1 satırları")
    for a in dd["advanced"]:
        s = need(load_series(ctx, "DEV", a, "", led), f"DEV_{a}")
        grp = reports[a]["groups"] if a in reports else group_table(s.trades, labels)
        b0s = load_series(ctx, "DEV", "B0", "", led, required=False)
        up = lambda ser: sum(t.pnl for t in ser.trades if labels.get(t.month) == "UP")        # noqa: E731
        bull = ""
        if b0s is not None:
            b0_up = up(b0s)
            chk = bull_check(up(s), b0_up)
            bull = ("B0 UP PnL ≤ 0: madde boş" if chk is None else
                    f"UP-ayı USD PnL {up(s):.2f} vs {THRESH['BULL_PNL_FRAC']:g} × B0 "
                    f"{THRESH['BULL_PNL_FRAC'] * b0_up:.2f} → {'GEÇTİ' if chk else 'KALDI'}")
        L.append(f"- {a}: DEV DOWN PF {f4(grp['DOWN']['pf'])} (n={grp['DOWN']['n']}); boğa maddesi: {bull or 'B0 yok'}")
        if "val" in docs and a in docs["val"]:
            g10 = docs["val"][a]["G10"]["rows"][1]
            L.append("  - " + fmt_row(g10))
    L.append("")
    # --- VAL ---
    if "val" in docs and (docs["val"].get("_meta") or {}).get("status") == "TAMAM":
        L.append("## VAL (2023-01-01 → 2024-11-18)")
        vd = docs["val"]
        for a in vd["_meta"]["advanced"]:
            L.append(f"**{a}** — p7={f4(vd[a]['p7'])} p28={f4(vd[a]['p28'])} t_obs={f4(vd[a]['t_obs'])} "
                     f"{'(kelepçe-yönetimli) ' if vd[a]['kelepce_yonetimli'] else ''}{'KAZANAN' if vd[a]['winner'] else ('DOĞRULANDI' if vd[a]['pass_all'] else 'KALDI')}")
            for g in ("G7", "G8", "G9", "G10", "G11", "G12", "G13", "G14", "G15", "G16"):
                for r in (vd[a][g]["rows"] if "rows" in vd[a][g] else [vd[a][g]]):
                    L.append("- " + fmt_row(r))
            s = need(load_series(ctx, "VAL", a, "", led), f"VAL_{a}")
            reports["VAL_" + a] = arm_report(ctx, s, labels, "val")
            L.append("")
        drows = []
        for a in DESCRIPTIVE:
            sd_ = load_series(ctx, "VAL", a, "", led, required=False)
            if sd_ is None:
                continue
            rp = arm_report(ctx, sd_, labels, "val")
            reports["VAL_" + a] = rp
            ci = rp.get("pf_ci90")
            drows.append([a, rp["n"], f4(rp["pf"]), f"{rp['net_r']:.2f}", f"{rp['pnl_usd']:.2f}",
                          f"[{f4(ci[0])}, {f4(ci[1])}]" if ci else "—"])
        if drows:
            L.append("**Açıklayıcı arm'lar, VAL (yalnız rapor; kazanamaz, YETERSİZ GÜÇ):**")
            L.append(md_table(["arm", "N", "PF", "ΣR", "PnL USD", "PF %90 GA"], drows))
            L.append("")
    if "final" in docs:
        fd = docs["final"]
        L.append("## FINAL (2026-09-03 → 2026-10-08)")
        L.append(f"- B0 çapası: işlem {fd['b0_anchor']['trades']} (beklenen {THRESH['B0_ANCHOR_TRADES']}), PF {f4(fd['b0_anchor']['pf'])} → {'TAMAM' if fd['b0_anchor']['ok'] else 'TUTMADI (FINAL GEÇERSİZ)'}")
        a = fd["arm"]
        L.append(f"- {a['name']}: N={a['n']} PF={f4(a['pf'])} DD_R={f4(a['dd_r'])} → {a['status']}")
        L.append("")
    # --- ayrıntı tabloları ---
    L.append("## Ayrıntı tabloları (giriş ayı ataması; rejim = donmuş BTC 1d etiketi)")
    for key, rp in reports.items():
        L.append(f"### {key}")
        L.append(f"N={rp['n']} WR={rp['wr']:.2f}% PF={f4(rp['pf'])} ΣR={rp['net_r']:.4f} DD_R={f4(rp['dd_r'])} "
                 f"(USD DD {rp['dd_usd']:.2f}) Calmar_R={f4(rp['calmar_r'])} EOD={rp['eod']} "
                 f"3x={rp['lev3_share']:.3f} 20x={rp['lev20_share']:.3f}")
        L.append("- çıkış karışımı: " + ", ".join(f"{k}={v}" for k, v in rp["exits"].items()))
        L.append("- kaldıraç histogramı: " + ", ".join(f"{k}x={v}" for k, v in rp["lev_hist"].items()))
        L.append(f"- sinyal vs işlem: ham={rp['signals']['ham_sinyal']} işlem={rp['signals']['islem']} "
                 f"sessiz düşüş={rp['signals']['sessiz_dusus']} ({rp['signals']['sessiz_oran']:.4f}); missed={rp['signals']['missed']}")
        if rp.get("diagnostics"):
            L.append(f"- diagnostics: {rp['diagnostics']}")
        L.append("- yön x rejim: " + "; ".join(f"{k}: n={v['n']} PF={f4(v['pf'])} ΣR={v['sum_r']:.2f}" for k, v in rp["dir_x_regime"].items()))
        L.append("- rejim grupları: " + "; ".join(f"{k}: n={v['n']} PF={f4(v['pf'])} ΣR={v['sum_r']:.2f}" for k, v in rp["groups"].items()))
        L.append("- semboller: " + "; ".join(f"{k}: n={v['n']} ΣR={v['sum_r']:.2f}" for k, v in rp["symbols"].items()))
        L.append(md_table(["ay", "etiket", "N", "ΣR", "PF"],
                          [[m, v["label"], v["n"], f"{v['sum_r']:.3f}", f4(v["pf"])] for m, v in rp["months"].items()]))
        if "p7" in rp:
            L.append(f"- p(b=7)={f4(rp['p7'])} p(b=28)={f4(rp['p28'])} p(b=14, bilgi)={f4(rp['p14_info'])} t={f4(rp['t_obs'])}")
        mt = rp.get("month_t")
        if mt:
            L.append(f"- ay düzeyinde tek-yönlü t (bilgi, M={mt['m']} tam ay): t={f4(mt['t'])} p={f4(mt['p'])}")
        L.append("")
    ci_s = ci_upper_sentence(reports, label)
    if ci_s:
        L.append(f"_Güç notu:_ {ci_s}")
    L.append(f"_Önceden yazılmış cümle:_ {SENTENCES.get(label, NO_WINNER_SENTENCE)}")
    return "\n".join(L) + "\n"


def cmd_decide(root: Path, target: Optional[str]) -> int:
    if target:
        raise UsageError("decide hedef almaz")
    ctx = verify_lock(root)
    label, note, docs = decide_outcome(root)
    led = Ledger(root)
    led.assert_clean()
    rows = results_rows(ctx, led)
    tmp_csv = root / "results.csv.tmp"
    with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=CSV_COLS)
        wr.writeheader()
        wr.writerows(rows)
    os.replace(tmp_csv, root / "results.csv")
    atomic_write(root / "DECISION.md", render_decision(ctx, root, label, note, docs, led))
    print(f"DECISION.md ve results.csv yazıldı ({len(rows)} koşu). Sonuç: {label}" + (f" ({note})" if note else ""))
    return 0


# ==========================================================================
# 16. CLI — YALNIZ --root ve aşama adı (+ isteğe bağlı hedef); istatistik bayrağı YOK
# ==========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analyze_e17.py", allow_abbrev=False,
        description="E17 ön-kayıtlı analizci (PLAN §5.3/§7/§8/§9, Ek A). Başka bayrak yoktur.")
    p.add_argument("stage", choices=STAGES, help="aşama/komut")
    p.add_argument("target", nargs="?", default=None, help="manifest için set (smoke|dev|val|final|runs); check-run için etiket")
    p.add_argument("--root", default=None, help="E17_ROOT (varsayılan $E17_ROOT veya $HOME/e17_root)")
    return p


DISPATCH: Dict[str, Callable[[Path, Optional[str]], int]] = {
    "freeze": cmd_freeze, "lock": cmd_lock, "verify-lock": cmd_verify_lock, "manifest": cmd_manifest,
    "smoke": cmd_smoke, "check-run": cmd_check_run, "dev-screen": cmd_dev_screen, "dev-decide": cmd_dev_decide,
    "diag": cmd_diag, "val": cmd_val, "final": cmd_final, "decide": cmd_decide,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = build_parser().parse_args(argv)           # kullanım hatası -> SystemExit(2)
    root = Path(ns.root) if ns.root else default_root()
    try:
        return DISPATCH[ns.stage](root, ns.target)
    except E17Error as exc:
        print(f"HATA[{exc.code}] {ns.stage}: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:        # noqa: BLE001 — bozuk girdi/beklenmeyen hata = veri bütünlüğü (çıkış 0/2/3/4/5 dışı yok)
        print(f"HATA[5] {ns.stage}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return IntegrityError.code


if __name__ == "__main__":
    sys.exit(main())
