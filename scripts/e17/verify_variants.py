#!/usr/bin/env python3
"""E17 variants.json doğrulayıcısı (PLAN §3.1, §2.1, §5.2; lock ÖNCESİ çalıştırılır).

`scratchpad/e17/fix/verify_variants2.py`'nin depoya taşınmış, genişletilmiş hali.
Her varyant VE her varyant x kaplama bileşimi için, GERÇEK `Settings(_env_file=None)`
doğrulayıcılarından (taze cwd + sahte gizliler, `.env` YOK) geçirilir:

  * Çözülmüş değer, variants.json'daki her override ile karşılaştırılır
    (bool/int/float/liste normalleştirmesiyle; boş `ENTRY_BLOCK_WEEKDAYS_UTC` dahil);
  * TF üçlüsü == `triple`; rol çakışması yok (giriş != bağlam, giriş != rejim);
  * S arm'ları: stop bandı `S/DYN_LEV_MAX >= MIN_STOP` ve `S/DYN_LEV_MIN <= MAX_STOP`,
    TP1 == S, TP2 == 2S, k == 2, S == constants.S_by_entry_minutes;
  * TB süre tablosu ve BG kapı paketi; yapı sayıları (6 aile / 2 açıklayıcı / 4 referans /
    7 kaplama); ortak --symbols sırası; snapshot sha256;
  * Analitik en-erken-bar: backtest'in KENDİ `resolve_backtest_window` ve
    `_WARMUP_BY_ROLE` fonksiyonlarıyla, DEV'in en erken barı VAL bitişinden sonra olmalı
    (PLAN §2 sızıntı duvarı); FINAL'inki DEV bitişinden sonra.

Snapshot içeriği ASLA basılmaz; uyuşmazlıkta yalnız anahtar adı + beklenen (variants.json)
değer yazılır. Çıkış: 0 hepsi geçti, 1 başarısız kontrol var, 2 kullanım/çevre hatası.

Kullanım:
  verify_variants.py --snapshot PATH [--variants PATH] [--repo DIR] [--python PY]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envtool as ET  # noqa: E402

REPO_DEFAULT = Path(__file__).resolve().parents[2]
VARIANTS_DEFAULT = REPO_DEFAULT / ET.PLAN_DIR / "variants.json"

EXPECTED_COUNTS = {"family": 6, "descriptive": 2, "references": 4, "overlays": 7}
STRICT_KEYS = (
    "scalper_tf_entry", "scalper_tf_context", "scalper_tf_regime",
    "scalper_regime_filter", "scalper_market_gate", "scalper_entry_block_weekdays_utc",
    "scalper_max_hold_hours", "scalper_loss_cooldown_minutes",
    "scalper_dyn_lev_stop_atr_mult", "scalper_fixed_stop_roi_pct",
    "scalper_tp1_roi", "scalper_tp2_roi", "scalper_entry_mode",
    "scalper_maker_fill_timeout_candles", "scalper_stale_tp_hours",
    "scalper_min_stop_pct", "scalper_max_stop_pct",
    "scalper_dyn_lev_min", "scalper_dyn_lev_max", "scalper_market_gate_run_days",
    "scalper_maker_fee_pct", "scalper_taker_fee_pct", "scalper_slippage_rate",
)

# PLAN §2.1 tablosu (UTC; TF üçlüsü -> set -> en erken bar). 1m üçlüsü lider/çeyrek
# yüzünden burada DEĞİL; duvar kontrolü onu ayrıca kapsar.
# NOT: PLAN §2.1 4h-rejimli üçlüler için FINAL'i "2026-07-22 08:00" yazar; backtest'in
# KENDİ fonksiyonlarıyla (35 gün: 35*6+250 bar * 4h geriye) doğru değer 2026-07-23 08:00'dır
# (DEV/VAL hesapları planla birebir tutuyor). Aşağıda hesaplanan (doğru) değer kilitlidir.
PLAN_WINDOWS = {
    "DEV": ("2025-01-01", "2026-01-01"),
    "VAL": ("2023-01-01", "2024-11-18"),
    "FINAL": ("2026-09-03", "2026-10-08"),
}
PLAN_EARLIEST = {
    ("5m", "30m", "1h"): {"DEV": "2024-12-21 14:00", "VAL": "2022-12-21 14:00", "FINAL": "2026-08-23 14:00"},
    ("15m", "1h", "4h"): {"DEV": "2024-11-20 08:00", "VAL": "2022-11-20 08:00", "FINAL": "2026-07-23 08:00"},
    ("30m", "4h", "4h"): {"DEV": "2024-11-20 08:00", "VAL": "2022-11-20 08:00", "FINAL": "2026-07-23 08:00"},
    ("1h", "4h", "4h"): {"DEV": "2024-11-20 08:00", "VAL": "2022-11-20 08:00", "FINAL": "2026-07-23 08:00"},
}

SETTINGS_CODE = r'''
import json, os, sys
req = json.load(sys.stdin)
cases, keys = req["cases"], req["keys"]
def _apply(env):
    for k in [k for k in os.environ if k.startswith("SCALPER_")]:
        del os.environ[k]
    os.environ.update(env)
_apply({})  # import anında modül düzeyi Settings() VARSAYILANLARLA kurulsun
from src.core.config import Settings
out = []
for c in cases:
    _apply(c["env"])
    try:
        s = Settings(_env_file=None)
        out.append({"label": c["label"], "values": {k: getattr(s, k, "<<MISSING>>") for k in keys}})
    except Exception as exc:  # girdi değerleri hata metnine SIZDIRILMAZ
        errs = getattr(exc, "errors", None)
        if callable(errs):
            try:
                detail = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', ()))}:{e.get('type')}" for e in errs())
            except Exception:
                detail = type(exc).__name__
        else:
            detail = type(exc).__name__
        out.append({"label": c["label"], "error": detail})
print(json.dumps(out, default=str))
sys.stdout.flush()
'''


class Reporter:
    def __init__(self, quiet: bool = False) -> None:
        self.total = 0
        self.failed = 0
        self.quiet = quiet

    def ok(self, name: str, detail: str = "") -> None:
        self.total += 1
        if self.quiet:
            return
        print(f"[OK]   {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name: str, detail: str = "") -> None:
        self.total += 1
        self.failed += 1
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))

    def check(self, cond: bool, name: str, detail_fail: str = "", detail_ok: str = "") -> bool:
        (self.ok(name, detail_ok) if cond else self.fail(name, detail_fail))
        return cond


def values_equal(got: Any, expected: str) -> bool:
    """Çözülmüş Settings değeri == variants.json dizgesi (tür-duyarlı)."""
    if isinstance(got, bool):
        return got == ET.parse_bool(expected)
    if isinstance(got, (int, float)):
        try:
            return float(got) == float(expected)
        except ValueError:
            return False
    if isinstance(got, (list, tuple)):
        return [str(x) for x in got] == [x.strip() for x in expected.split(",") if x.strip()]
    return str(got) == expected


def minutes_of(tf: str) -> int:
    unit = tf[-1]
    n = int(tf[:-1])
    return n * {"m": 1, "h": 60, "d": 1440}[unit]


def build_cases(variants: Dict[str, Any], snapshot: Dict[str, str]) -> List[Dict[str, Any]]:
    cases = []
    for v in ET.all_variants(variants):
        cases.append({"label": v["id"], "variant": v["id"], "overlay": None,
                      "env": ET.resolve_env(variants, snapshot, v["id"])})
    # Kaplama bileşimleri: her aile/açıklayıcı arm x her kaplama (PLAN §3.2 uygulanabilirlik
    # tablosundan GENİŞ: gelecekte tabloda olmayan bir bileşim de patlamasın).
    for v in variants.get("family", []) + variants.get("descriptive", []):
        for o in variants.get("overlays", []):
            cases.append({"label": f"{v['id']}+{o['id']}", "variant": v["id"], "overlay": o["id"],
                          "env": ET.resolve_env(variants, snapshot, v["id"], [o["id"]])})
    return cases


def check_structure(rep: Reporter, variants: Dict[str, Any], snapshot_path: str) -> None:
    rep.check(variants.get("schema") == ET.SCHEMA, "schema == e17-variants/2")
    for k, n in EXPECTED_COUNTS.items():
        got = len(variants.get(k, []))
        rep.check(got == n, f"{k} sayısı == {n}", f"{got} bulundu")
    ids = [v["id"] for v in ET.all_variants(variants)]
    rep.check(len(ids) == len(set(ids)), "varyant id'leri tekil")
    oids = [o["id"] for o in variants.get("overlays", [])]
    rep.check(len(oids) == len(set(oids)), "kaplama id'leri tekil")
    try:
        ET.cli_args(variants)
        rep.ok("common_cli --symbols sabit sırada, --tf-* yok")
    except ET.ToolError as exc:
        rep.fail("common_cli", str(exc))
    rep.check(variants.get("symbols_order_pinned", ET.PINNED_SYMBOLS) == ET.PINNED_SYMBOLS,
              "symbols_order_pinned == sabit sıra")
    want = (variants.get("snapshot") or {}).get("sha256")
    got = ET.sha256_file(snapshot_path)
    rep.check(want == got, "snapshot sha256 == variants.json snapshot.sha256",
              "uyuşmuyor (snapshot dosyası değişmiş veya yanlış dosya)")


def check_semantics(
    rep: Reporter, variants: Dict[str, Any], snapshot: Dict[str, str],
    resolved: Dict[str, Dict[str, Any]],
) -> None:
    consts = variants.get("constants", {})
    s_by_min = consts.get("S_by_entry_minutes", {})
    tb = consts.get("TB_table", {})
    bg = consts.get("BG", {})
    for v in ET.all_variants(variants):
        vid = v["id"]
        res = resolved.get(vid)
        if not res or "values" not in res:
            continue
        r = res["values"]
        entry, ctx, regime = r["scalper_tf_entry"], r["scalper_tf_context"], r["scalper_tf_regime"]
        # --- rol üçlüsü
        rep.check([entry, ctx, regime] == list(v.get("triple", [])), f"{vid}: TF üçlüsü == triple",
                  f"çözülen {entry}/{ctx}/{regime}")
        rep.check(entry != ctx and entry != regime, f"{vid}: rol çakışması yok (giriş!=bağlam, giriş!=rejim)",
                  f"{entry}/{ctx}/{regime}")
        # --- S arm'ı çözülmüş S ile aynı
        s_res = float(r["scalper_fixed_stop_roi_pct"])
        rep.check(s_res == float(v.get("S_arm", -1)), f"{vid}: FIXED_STOP_ROI_PCT == S_arm",
                  f"S_arm={v.get('S_arm')}")
        geometry = str(v.get("geometry", ""))
        if geometry.startswith("scaled"):
            s = float(v["S_arm"])
            k = float(r["scalper_dyn_lev_stop_atr_mult"])
            lmin, lmax = float(r["scalper_dyn_lev_min"]), float(r["scalper_dyn_lev_max"])
            lo, hi = s / lmax, s / lmin
            min_stop, max_stop = float(r["scalper_min_stop_pct"]), float(r["scalper_max_stop_pct"])
            rep.check(lo >= min_stop and hi <= max_stop, f"{vid}: stop bandı [{lo:.3f},{hi:.3f}] "
                      f"MIN_STOP/MAX_STOP içinde", f"[{lo:.3f},{hi:.3f}] vs [{min_stop},{max_stop}]",
                      f"[{lo:.3f},{hi:.3f}] ⊂ [{min_stop},{max_stop}]")
            rep.check(k == 2.0, f"{vid}: k (DYN_LEV_STOP_ATR_MULT) == 2")
            rep.check(float(r["scalper_tp1_roi"]) == s and float(r["scalper_tp2_roi"]) == 2 * s,
                      f"{vid}: TP1_ROI == S ve TP2_ROI == 2S")
            want_s = s_by_min.get(str(minutes_of(entry)))
            rep.check(want_s is not None and float(want_s) == s, f"{vid}: S == constants.S_by_entry_minutes[{minutes_of(entry)}]",
                      f"tabloda {want_s}")
        # --- TB süre kuralı (bar sayısı): yalnız TB taşıyan arm'lar
        if v["id"] not in ("B0", "R1"):
            m = minutes_of(entry)
            row = tb.get(str(m), {})
            hold = float(r["scalper_max_hold_hours"])
            cool = float(r["scalper_loss_cooldown_minutes"])
            ok = (row and hold == float(row.get("SCALPER_MAX_HOLD_HOURS", -1))
                  and cool == float(row.get("SCALPER_LOSS_COOLDOWN_MINUTES", -1))
                  and hold == 480 * m / 60 and cool == 60 * m
                  and int(r["scalper_maker_fill_timeout_candles"]) == 3
                  and float(r["scalper_stale_tp_hours"]) == 0)
            rep.check(bool(ok), f"{vid}: TB süre kuralı (480 bar / 60 bar / 3 bar / stale 0)",
                      f"giriş {entry}")
        # --- kapı paketi
        gated = v.get("role") in ("reference_gated", "reference_live")
        if gated:
            rep.check(bool(r["scalper_market_gate"]) and bool(r["scalper_regime_filter"]),
                      f"{vid}: kapılar korunmuş (market gate + rejim filtresi açık)")
        else:
            ok = (r["scalper_regime_filter"] is False and r["scalper_market_gate"] is False
                  and str(r["scalper_entry_block_weekdays_utc"]) == "")
            rep.check(ok, f"{vid}: BG kapı paketi (rejim/market/hafta sonu KAPALI)")
        if bg:
            rep.check(set(bg) <= set(ET.resolve_env(variants, snapshot, vid)) or gated,
                      f"{vid}: BG anahtarları çözülmüş env'de")
    # Kaplama bileşimleri: arm'ın TF üçlüsü/kapıları kaplamayla DEĞİŞMEZ.
    for label, res in resolved.items():
        if "+" not in label or "values" not in res:
            continue
        base = resolved.get(label.split("+")[0], {}).get("values")
        if base is None:
            continue
        keep = ("scalper_tf_entry", "scalper_tf_context", "scalper_tf_regime", "scalper_market_gate",
                "scalper_regime_filter", "scalper_fixed_stop_roi_pct", "scalper_tp1_roi", "scalper_tp2_roi")
        same = all(res["values"][k] == base[k] for k in keep)
        o = label.split("+")[1]
        if o == "ROB_WALL":  # yalnız süre düğmeleri değişir
            same = same and float(res["values"]["scalper_max_hold_hours"]) == 8 and float(res["values"]["scalper_loss_cooldown_minutes"]) == 60
        rep.check(same, f"{label}: kaplama yalnız kendi alanlarını değiştirir")


def check_settings(
    rep: Reporter, variants: Dict[str, Any], snapshot: Dict[str, str], repo: Path,
    python: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    cases = build_cases(variants, snapshot)
    # Sorgulanan alanlar = STRICT_KEYS ∪ variants.json'daki HER override anahtarı (varyant, referans,
    # kaplama): yazım hatası / plan dışı fazladan anahtar Settings'te alan olarak bulunamazsa FAIL.
    override_attrs = {k.lower() for v in ET.all_variants(variants) for k in v.get("env", {})}
    override_attrs |= {k.lower() for o in variants.get("overlays", []) for k in o.get("env", {})}
    keys = sorted(set(STRICT_KEYS) | override_attrs)
    # Plan (§3.1) override kümesi = STRICT_KEYS: dışındaki her anahtar (yazım hatası YA DA plan dışı
    # fazladan override, örn. SCALPER_MAX_POSITIONS) kilit öncesi hata sayılır.
    extra = sorted(k.upper() for k in override_attrs - set(STRICT_KEYS))
    rep.check(not extra, "variants.json override anahtarları planlı kümede (STRICT_KEYS)",
              "planda olmayan: " + ",".join(extra))
    payload = {"cases": [{"label": c["label"], "env": c["env"]} for c in cases], "keys": keys}
    results = ET.run_repo_child(SETTINGS_CODE, repo, {}, payload, python)
    by_label = {r["label"]: r for r in results}
    for c in cases:
        res = by_label.get(c["label"], {"error": "sonuç yok"})
        if "error" in res:
            rep.fail(f"Settings({c['label']})", f"doğrulayıcı reddetti: {res['error']}")
            continue
        vals = res["values"]
        # Beklenen: variants.json override'ları (varyant + kaplama), anahtar adı bazında.
        expected = dict(find_env(variants, c["variant"]))
        if c["overlay"]:
            expected.update({k: str(x) for k, x in ET.find_overlay(variants, c["overlay"]).get("env", {}).items()})
        bad = []
        for k, exp in expected.items():
            attr = k.lower()
            # Her override anahtarı sorgulanır (yukarıda); eksik alan = yazım hatası/plan dışı anahtar.
            if attr not in vals or vals[attr] == "<<MISSING>>":
                bad.append(f"{k}(Settings alanı değil)")
            elif not values_equal(vals[attr], exp):
                bad.append(k)
        if bad:
            rep.fail(f"Settings({c['label']}) çözülmüş değerler", "uyuşmayan anahtarlar: " + ",".join(bad))
        else:
            rep.ok(f"Settings({c['label']}) çözülmüş değerler == variants.json ({len(expected)} override)")
    return by_label


def find_env(variants: Dict[str, Any], vid: str) -> Dict[str, str]:
    return {k: str(v) for k, v in ET.find_variant(variants, vid).get("env", {}).items()}


def check_earliest(
    rep: Reporter, variants: Dict[str, Any], resolved: Dict[str, Dict[str, Any]],
    repo: Path, python: Optional[str],
) -> None:
    try:
        dev_s, dev_e = ET.get_window(variants, "DEV")
        val_s, val_e = ET.get_window(variants, "VAL")
        fin_s, fin_e = ET.get_window(variants, "FINAL")
    except ET.ToolError as exc:
        rep.fail("pencereler", str(exc))
        return
    queries: List[Dict[str, Any]] = []
    meta: List[Tuple[str, str, Tuple[str, str, str], bool]] = []

    def add(set_name: str, vid: str, start: str, end: str, leader: bool) -> None:
        r = resolved.get(vid, {}).get("values")
        if not r:
            return
        triple = (r["scalper_tf_entry"], r["scalper_tf_context"], r["scalper_tf_regime"])
        queries.append({"triple": list(triple), "start": start, "end": end, "leader": leader,
                        "run_days": int(r.get("scalper_market_gate_run_days") or 3)})
        meta.append((set_name, vid, triple, leader))

    def gate_of(vid: str) -> bool:
        return bool(resolved.get(vid, {}).get("values", {}).get("scalper_market_gate"))

    for v in ET.all_variants(variants):
        vid, sets_of = v["id"], v.get("sets", [])
        if "DEV" in sets_of:
            add("DEV", vid, dev_s, dev_e, gate_of(vid))
        if "VAL" in sets_of or "VAL_if_reached" in sets_of:
            add("VAL", vid, val_s, val_e, gate_of(vid))
        # Herhangi bir aile arm'ı FINAL'e ulaşabilir (kazanan) — pencereleri de duvara tabi.
        if "FINAL" in sets_of or v.get("role") == "family":
            add("FINAL", vid, fin_s, fin_e, gate_of(vid))
        for qs, qe in v.get("dev_quarters", []) if "DEV_quarterly" in sets_of else []:
            add("DEV", vid, qs, qe, gate_of(vid))
    if not queries:
        rep.fail("en erken bar", "hesaplanacak sorgu yok")
        return
    res = ET.earliest_bars(repo, queries, python)
    earliest: Dict[str, Dict[str, int]] = {"DEV": {}, "VAL": {}, "FINAL": {}}
    plan_seen: Dict[Tuple[Tuple[str, str, str], str], str] = {}
    for (set_name, vid, triple, leader), q, r in zip(meta, queries, res):
        key = f"{vid}@{q['start']}"
        earliest[set_name][key] = r["earliest_ms"]
        if not leader and (q["start"], q["end"]) == PLAN_WINDOWS.get(set_name):
            plan_seen[(triple, set_name)] = ET.ms_to_iso(r["earliest_ms"])
    cross_dev_val = min(earliest["DEV"].values()) > ET._date_ms(val_e)
    rep.check(cross_dev_val, f"sızıntı duvarı: min(DEV en erken bar) > VAL bitişi ({val_e})",
              f"min DEV en erken bar {ET.ms_to_iso(min(earliest['DEV'].values()))}",
              f"min DEV {ET.ms_to_iso(min(earliest['DEV'].values()))}")
    rep.check(min(earliest["FINAL"].values()) > ET._date_ms(dev_e) if earliest["FINAL"] else True,
              f"FINAL en erken bar > DEV bitişi ({dev_e})")
    # PLAN §2.1 tablosu ile birebir (yalnız plan pencereleriyle koşuyorsak)
    for (triple, set_name), got in sorted(plan_seen.items()):
        want = PLAN_EARLIEST.get(triple, {}).get(set_name)
        if want is None:
            continue
        # PLAN §2.1 tablosu 4h-rejimli üçlülerin FINAL'i için 07-22 yazar; tablodaki değer backtest'in
        # KENDİ fonksiyonlarıyla 07-23'tür (yukarıdaki not). Ad planı değil, kilitli beklentiyi anar.
        rep.check(got == want, f"kilitli en erken bar (backtest fonksiyonlarıyla): "
                  f"{'/'.join(triple)} {set_name} == {want}", f"hesaplanan {got}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--snapshot", required=True, help="sunucu SCALPER_* env dosyası (içeriği basılmaz)")
    p.add_argument("--variants", default=str(VARIANTS_DEFAULT))
    p.add_argument("--repo", default=str(REPO_DEFAULT), help="src/ içeren depo kökü (Settings/backtest için)")
    p.add_argument("--python", default=None, help="çocuk süreç python'u (varsayılan bu yorumlayıcı)")
    p.add_argument("--quiet", action="store_true", help="yalnız başarısız kontrolleri ve özeti yaz")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        variants = ET.load_variants(args.variants)
        snapshot = ET.load_snapshot(args.snapshot)
    except ET.ToolError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2
    repo = Path(args.repo)
    rep = Reporter(quiet=args.quiet)
    check_structure(rep, variants, args.snapshot)
    try:
        resolved = check_settings(rep, variants, snapshot, repo, args.python)
        check_semantics(rep, variants, snapshot, resolved)
        check_earliest(rep, variants, resolved, repo, args.python)
    except ET.ToolError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2
    print(f"verify_variants: {rep.total} kontrol, {rep.failed} başarısız")
    return 0 if rep.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
