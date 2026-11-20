#!/usr/bin/env python3
"""E17 pre-lock: write docs/audits/2026-10-10-prereg-tf/cal_constants.json (PLAN §3.1 R3/R4).
Constants are WRITTEN AS FIXED NUMBERS (not recomputed at runtime); formula is provenance only.
P10 values: the PLAN-quoted numbers are the binding record; `recomputed_*` are a cross-check done from the
CAL measurement files (atr_pct field only, atrsig.py quantile method) — no PnL field is read."""
import json, math, sys
from pathlib import Path
SP = Path("/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad")
ST = SP / "e17/stat"
OUT = Path("/home/user/crypto-trading-bot/docs/audits/2026-10-10-prereg-tf/cal_constants.json")

def q(v, p):
    v = sorted(v); k = (len(v) - 1) * p; f = int(k); c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)

def p10(fname):
    d = json.load(open(ST / fname))
    return round(q([x["atr_pct"] for x in d["rec"]], 0.10), 3), len(d["rec"])

K = 2
MAX_STOP_PCT = 5.5
CAP = math.floor(3 * MAX_STOP_PCT)       # 16
S_FIXED = {"5m": 5, "15m": 9, "30m": 13, "1h": 16}
TB = {"5m": ("40", "300"), "15m": ("120", "900"), "30m": ("240", "1800"), "1h": ("480", "3600")}

setA_files = {"5m": "m_S5b_BG.json", "15m": "m_S15b_BG.json", "30m": "m_S30b_BG.json", "1h": "m_S1hb_BG.json"}
setB_files = {"5m": "m_T5b_BG.json", "15m": "m_T15b_BG.json", "30m": "m_T30b_BG.json", "1h": "m_T1h_T_noreg_nogate.json"}
setA_plan = {"5m": 0.113, "15m": 0.226, "30m": 0.319, "1h": 0.503}
setB_plan = {"5m": 0.121, "15m": 0.228, "30m": 0.327, "1h": 0.508}
review_plan = {"5m": 0.112}

def formula(p):
    raw = 20 * K * p
    return {"raw": round(raw, 3), "rounded": round(raw), "S": min(round(raw), CAP)}

doc = {
    "schema": "e17-cal-constants/1",
    "plan": "docs/audits/2026-10-10-prereg-tf/PLAN.md §3.1 R3, R4",
    "note": ("Sabit sayilar kilide girer; CAL yeniden okunmaz. Formul yalniz kokendir (provenance). "
             "S5=5 bicak sirtidir: formul 4.48-4.84 verir, S=4 de gecerliydi (4/20=0.20 >= MIN_STOP 0.15); "
             "5 donmus bir secimdir (PLAN R4 durustluk notu)."),
    "k": K,
    "S_by_entry_tf": S_FIXED,
    "S_by_entry_minutes": {"5": 5, "15": 9, "30": 13, "60": 16},
    "S_derived_env": {tf: {"SCALPER_DYN_LEV_STOP_ATR_MULT": str(K), "SCALPER_FIXED_STOP_ROI_PCT": str(s),
                           "SCALPER_TP1_ROI": str(s), "SCALPER_TP2_ROI": str(2 * s)} for tf, s in S_FIXED.items()},
    "T_geometry_server": {"S": 50, "k": 3.0, "TP1_ROI": 10, "TP2_ROI": 25,
                          "note": "sunucu geometrisi: T arm'lari snapshot degerlerini aynen kullanir (override yok)"},
    "TB_rule": "bar sayisi: MAX_HOLD = 480 bar x TF; LOSS_COOLDOWN = 60 bar x TF; MAKER_FILL_TIMEOUT_CANDLES=3; STALE_TP_HOURS=0",
    "TB_bars": {"MAX_HOLD_bars": 480, "LOSS_COOLDOWN_bars": 60, "MAKER_FILL_TIMEOUT_CANDLES": 3, "STALE_TP_HOURS": 0},
    "TB_table": {tf: {"SCALPER_MAX_HOLD_HOURS": h, "SCALPER_LOSS_COOLDOWN_MINUTES": c} for tf, (h, c) in TB.items()},
    "formula": {
        "text": "S = min(round(20 * k * P10), floor(3 * MAX_STOP_PCT)) ; k=2 ; MAX_STOP_PCT=5.5 => cap 16 ; "
                "P10 = CAL (2026-03-01..2026-06-01) sinyal-ani ATR% (8 sembol havuzlu) onuncu yuzdelik. "
                "Mekanik: L=clamp(round(S/(k*ATR%)),3,20); stop%=S/L; TP1 (R) = TP1_ROI/S = 1.",
        "k": K, "MAX_STOP_PCT": MAX_STOP_PCT, "S_cap": CAP, "MIN_STOP_PCT": 0.15,
        "band_check": {tf: {"stop_pct_min": round(s / 20, 3), "stop_pct_max": round(s / 3, 3)} for tf, s in S_FIXED.items()},
    },
    "p10_measurement_set_A": {
        "desc": "S-arm CAL olcumleri (BG kapilari kapali), PLAN R4'te alintilanan ilk set",
        "plan_quoted_P10_atr_pct": setA_plan,
        "formula_result": {tf: formula(p) for tf, p in setA_plan.items()},
        "recomputed_from_files": {tf: {"file": f, "P10": p10(f)[0], "n_signal_calls": p10(f)[1]} for tf, f in setA_files.items()},
    },
    "p10_measurement_set_B": {
        "desc": "T-arm CAL olcumleri (BG kapilari kapali), PLAN R4'te alintilanan ikinci bagimsiz set",
        "plan_quoted_P10_atr_pct": setB_plan,
        "formula_result": {tf: formula(p) for tf, p in setB_plan.items()},
        "recomputed_from_files": {tf: {"file": f, "P10": p10(f)[0], "n_signal_calls": p10(f)[1]} for tf, f in setB_files.items()},
        "note_1h": ("PLAN 1h=0.508 degeri m_T1h_T_noreg_nogate.json'dan gelir; m_T1hb_BG.json (BG, b-kosusu) "
                    f"P10={p10('m_T1hb_BG.json')[0]} verir; iki durumda da round(20*2*P10)=20 -> 16'ya kirpilir."),
    },
    "p10_review_measurement": {"plan_quoted_P10_atr_pct": review_plan,
                               "formula_result": {tf: formula(p) for tf, p in review_plan.items()},
                               "note": "inceleme turu 5m olcumu (dosyasi arsivlenmedi); S5 yine de 5'te dondurulmustur"},
    "source": {
        "measurement_scripts": ["e17/stat/measure.py", "e17/stat/atrsig.py", "e17/stat/run_measure.sh"],
        "measurement_outputs": sorted({*setA_files.values(), *setB_files.values()}),
        "variant_generator": ["e17/fix/gen_variants.py", "e17/fix/verify_variants2.py"],
        "path_root": "scratchpad kokune goreli (SP); kilitte kopyalari docs/audits/2026-10-10-prereg-tf/evidence/ altindadir (script) ve evidence/ekb_bulk.tgz (m_*.json)",
        "evidence_dir": "docs/audits/2026-10-10-prereg-tf/evidence/",
    },
}
OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
print("wrote", OUT)
for tf in S_FIXED:
    a = doc["p10_measurement_set_A"]["formula_result"][tf]["S"]; b = doc["p10_measurement_set_B"]["formula_result"][tf]["S"]
    print(tf, "fixed", S_FIXED[tf], "setA formula", a, "setB formula", b)
