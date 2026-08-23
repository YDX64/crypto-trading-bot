#!/usr/bin/env python3
"""Lider piyasa kapısı (D15) koşularını AYRIŞTIR: engelleme vs yeniden tahsis.

Neden var: E7'nin ΔPnL'i İKİ etkinin toplamıdır ve bunlar karıştırılırsa
kapıya hak etmediği bir güç atfedilir.

  (a) engelleme      — kapının vetoladığı işlemlerin GERÇEKLEŞMEMESİ
  (b) ikinci-derece  — vetolanan sinyalin boşalttığı yere giren YENİ işlemler
                       ("yeniden tahsis"). `--mechanism` bu terimi ÜÇ olası
                       mekanizmaya ayırır: sembol-içi İŞGAL PENCERESİ,
                       kayıp-COOLDOWN'u ve küresel KAPASİTE. Hangisinin
                       gerçekten çalıştığı ölçülmeden "slot boşaldı" demek
                       atıf hatasıdır (kapasite fiilen bağlayıcı olmayabilir).

Yöntem: iki koşunun JSON raporundaki işlem listeleri `(symbol, entry_time,
direction)` üçlüsüyle eşleştirilir. Kaybolanlar = engellenenler, yenilerse
yeniden tahsis. Ortak işlemlerin PnL'i iki koşuda AYNI olmalıdır — değilse
eşleştirme kirlidir ve betik bunu UYARI olarak basar (atıfın ön koşulu).
**Yeni backtest koşulmaz**; yalnız var olan raporlar okunur.

Rapor yolları elle girilmez: her `logs/market_gate/<varyant>_<pencere>.log`
dosyasının içinde o koşunun yazdığı JSON rapor yolu geçer, oradan türetilir.
(`logs/` commit'lenmez — bu yüzden kanıt dosyaları koşuyu yapan makinededir;
betik commit'lenir ki yöntem yeniden üretilebilsin.)

Kullanım:
    python3 scripts/decompose_gate_runs.py                 # V1 ve V1c
    python3 scripts/decompose_gate_runs.py --variants V1c
    python3 scripts/decompose_gate_runs.py --reaper        # + reaper maruziyeti
    python3 scripts/decompose_gate_runs.py --mechanism     # + ikinci-derece mekanizması
    python3 scripts/decompose_gate_runs.py --logs-dir logs/market_gate
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPORT_RE = re.compile(r"logs/backtest_\d{8}_\d{6}\.json")

# Pencere kodu → rapordaki insan adı.
_WINDOWS: Tuple[Tuple[str, str], ...] = (
    ("BEAR", "AYI"), ("FLAT", "YATAY"), ("BULL", "BOĞA"),
)

# D4 reaper şartı: TP1 GÖRMEMİŞ (trailing_active muaf) ve yaş limitini aşmış.
_REAPER_MINUTES = 8 * 60

TradeKey = Tuple[str, int, str]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _report_path(logs_dir: Path, variant: str, window: str) -> Optional[Path]:
    log = logs_dir / f"{variant}_{window}.log"
    if not log.exists():
        return None
    found = _REPORT_RE.findall(log.read_text(encoding="utf-8", errors="replace"))
    if not found:
        return None
    return _repo_root() / found[-1]


def _load(logs_dir: Path, variant: str, window: str) -> Optional[Dict[str, Any]]:
    path = _report_path(logs_dir, variant, window)
    if path is None or not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _key(trade: Dict[str, Any]) -> TradeKey:
    return (trade["symbol"], int(trade["entry_time"]), trade["direction"])


def _pf(trades: List[Dict[str, Any]]) -> float:
    gross = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    loss = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    return gross / loss if loss else float("inf")


def _max_drawdown(trades: List[Dict[str, Any]]) -> float:
    """Kapanış sırasına göre eşitlik eğrisinin en derin düşüşü."""
    equity = peak = drawdown = 0.0
    for trade in sorted(trades, key=lambda t: t["exit_time"]):
        equity += trade["pnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _is_reaper_candidate(trade: Dict[str, Any]) -> bool:
    """Canlıda reaper'ın kapatacağı işlem: >8 saat VE TP1 görmemiş."""
    if float(trade.get("duration_minutes", 0.0)) <= _REAPER_MINUTES:
        return False
    for leg in trade.get("legs") or []:
        if (leg.get("label") or leg.get("reason")) == "TP1":
            return False
    return True


def _loss_cooldown_ms(report: Dict[str, Any]) -> int:
    """Koşunun kayıp-cooldown'u (ms) — rapor provenance'ından; yoksa 0."""
    cfg = (report.get("provenance") or {}).get("scalper_config") or {}
    try:
        return int(float(cfg.get("scalper_loss_cooldown_minutes") or 0) * 60_000)
    except (TypeError, ValueError):
        return 0


def _classify_realloc(
    added: List[Dict[str, Any]],
    blocked: List[Dict[str, Any]],
    cooldown_ms: int,
) -> Dict[str, List[Dict[str, Any]]]:
    """İkinci-derece işlemleri MEKANİZMAYA göre ayır.

    Sıra ÖNEMLİ ve dar-olandan geniş-olana doğrudur:

    1. `occupancy` — yeni işlemin girişi, kapının engellediği AYNI SEMBOLDEKİ
       bir işlemin [giriş, çıkış] penceresinin İÇİNDE. `simulate_symbol` bir
       sembolde aynı anda tek pozisyon tutar (`i = trade.exit_idx + 1`), yani
       bu işlem taban koşuda ZATEN imkânsızdı — kapasiteye hiç sıra gelmez.
    2. `cooldown` — pencere içinde değil ama engellenen bir KAYBEDENİN
       çıkışından sonraki `SCALPER_LOSS_COOLDOWN_MINUTES` içinde.
    3. `capacity_or_other` — kalan; küresel `scalper_max_positions` kapısının
       (ya da başka bir ikinci-derece etkinin) serbest bıraktığı işlemler.

    Bir işlem birden fazla mekanizmaya UYABİLİR; bu yüzden ilk (en dar)
    açıklama atanır ve sayıların toplamı `added` uzunluğuna eşittir.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "occupancy": [], "cooldown": [], "capacity_or_other": []
    }
    for trade in added:
        entry = int(trade["entry_time"])
        same = [b for b in blocked if b["symbol"] == trade["symbol"]]
        if any(int(b["entry_time"]) <= entry <= int(b["exit_time"]) for b in same):
            buckets["occupancy"].append(trade)
            continue
        if cooldown_ms > 0 and any(
            b["pnl"] < 0.0
            and int(b["exit_time"]) <= entry < int(b["exit_time"]) + cooldown_ms
            for b in same
        ):
            buckets["cooldown"].append(trade)
            continue
        buckets["capacity_or_other"].append(trade)
    return buckets


def _decompose(
    base: Dict[str, Any], gated: Dict[str, Any]
) -> Dict[str, Any]:
    by_base = {_key(t): t for t in base["trades"]}
    by_gated = {_key(t): t for t in gated["trades"]}
    blocked = [by_base[k] for k in by_base if k not in by_gated]
    added = [by_gated[k] for k in by_gated if k not in by_base]
    common = [k for k in by_base if k in by_gated]
    mismatched = [
        k for k in common if abs(by_base[k]["pnl"] - by_gated[k]["pnl"]) > 1e-6
    ]
    kept = [by_base[k] for k in common]
    return {
        "mechanism": _classify_realloc(added, blocked, _loss_cooldown_ms(base)),
        "delta": gated["overall"]["total_pnl"] - base["overall"]["total_pnl"],
        # Engellemenin katkısı: gerçekleşmeyen PnL'in TERSİ.
        "block_only": -sum(t["pnl"] for t in blocked),
        "realloc": sum(t["pnl"] for t in added),
        "blocked": blocked,
        "added": added,
        "kept": kept,
        "mismatched": mismatched,
        "pf_base": _pf(base["trades"]),
        "pf_block_only": _pf(kept),
        "dd_base": _max_drawdown(base["trades"]),
        "dd_block_only": _max_drawdown(kept),
        "dd_full": _max_drawdown(gated["trades"]),
    }


def _print_window(
    label: str, result: Dict[str, Any], reaper: bool, mechanism: bool = False
) -> None:
    print(
        f"  {label:6} Δ={result['delta']:+9.2f} | engelleme={result['block_only']:+9.2f}"
        f"  yeniden-tahsis={result['realloc']:+9.2f}"
        f" | engellenen={len(result['blocked'])} yeni={len(result['added'])}"
        f" ortak={len(result['kept'])}"
    )
    print(
        f"         yalnız-engelleme PF {result['pf_base']:.3f}→{result['pf_block_only']:.3f}"
        f" · maxDD {result['dd_base']:.2f}→{result['dd_block_only']:.2f}"
        f" (tam koşu {result['dd_full']:.2f})"
    )
    if result["mismatched"]:
        print(
            f"         ⚠️ ORTAK {len(result['mismatched'])} işlemin PnL'i iki koşuda "
            f"FARKLI — atıf kirli, sonuçlara güvenme!"
        )
    for direction in ("LONG", "SHORT"):
        blk = [t for t in result["blocked"] if t["direction"] == direction]
        new = [t for t in result["added"] if t["direction"] == direction]
        if not blk and not new:
            continue
        print(
            f"         {direction:5} engelleme={-sum(t['pnl'] for t in blk):+9.2f}"
            f" (n={len(blk)})  yeniden-tahsis={sum(t['pnl'] for t in new):+9.2f}"
            f" (n={len(new)})"
        )
    stops = [t for t in result["blocked"] if t.get("exit_reason") == "SL"]
    if stops:
        print(
            f"         engellenen SL: n={len(stops)} "
            f"pnl={sum(t['pnl'] for t in stops):+.2f}"
        )
    if mechanism:
        parts = []
        for name in ("occupancy", "cooldown", "capacity_or_other"):
            trades = result["mechanism"][name]
            parts.append(
                f"{name}={len(trades)}/{sum(t['pnl'] for t in trades):+.2f}"
            )
        print(f"         ikinci-derece mekanizması: {'  '.join(parts)}")
    if reaper:
        cands = [t for t in result["blocked"] if _is_reaper_candidate(t)]
        exposed = sum(t["pnl"] for t in cands)
        share = (
            f" → Δ'nın %{abs(exposed) / result['delta'] * 100:.0f}'ı"
            if result["delta"]
            else ""
        )
        print(
            f"         REAPER maruziyeti: engellenenlerin {len(cands)}'i >8sa & "
            f"TP1 görmemiş, pnl={exposed:+.2f}{share}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logs-dir", default="logs/market_gate")
    parser.add_argument("--base", default="V0", help="taban varyant (kapı kapalı)")
    parser.add_argument(
        "--variants", default="V1,V1c", help="virgülle ayrık karşılaştırma varyantları"
    )
    parser.add_argument(
        "--reaper", action="store_true",
        help="engellenen işlemler içindeki reaper (D4) maruziyetini de raporla",
    )
    parser.add_argument(
        "--mechanism", action="store_true",
        help="ikinci-derece (yeniden tahsis) terimini mekanizmasına ayır",
    )
    args = parser.parse_args(argv)

    logs_dir = Path(args.logs_dir)
    if not logs_dir.is_absolute():
        logs_dir = _repo_root() / logs_dir
    if not logs_dir.is_dir():
        print(f"❌ Log dizini yok: {logs_dir}", file=sys.stderr)
        return 2

    exit_code = 0
    for variant in [v.strip() for v in args.variants.split(",") if v.strip()]:
        print(f"\n===== {args.base} → {variant} =====")
        total_delta = total_block = 0.0
        for window, label in _WINDOWS:
            base = _load(logs_dir, args.base, window)
            gated = _load(logs_dir, variant, window)
            if base is None or gated is None:
                print(f"  {label:6} — rapor bulunamadı (atlandı)")
                exit_code = 1
                continue
            result = _decompose(base, gated)
            _print_window(label, result, args.reaper, args.mechanism)
            total_delta += result["delta"]
            total_block += result["block_only"]
        if total_delta:
            print(
                f"  TOPLAM Δ={total_delta:+.2f} | yalnız-engelleme={total_block:+.2f}"
                f" | yeniden tahsis payı=%"
                f"{(total_delta - total_block) / total_delta * 100:.1f}"
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
