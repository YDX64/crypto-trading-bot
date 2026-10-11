"""E17 variants.json (şema e17-variants/2) üretici. Ortam değerleri BURADA tam çözülmüş yazılır (koşu anında bileşim YOK)."""
import json, hashlib
SP="/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad"
snap_sha=hashlib.sha256(open(f"{SP}/server-scalper-env.txt","rb").read()).hexdigest()

BG={"SCALPER_REGIME_FILTER":"false","SCALPER_MARKET_GATE":"false","SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC":""}
TB={5:("40","300"),15:("120","900"),30:("240","1800"),60:("480","3600")}   # (MAX_HOLD_HOURS, LOSS_COOLDOWN_MINUTES) = 480 bar / 60 bar
TRIPLE={5:("5m","30m","1h"),15:("15m","1h","4h"),30:("30m","4h","4h"),60:("1h","4h","4h")}
S_CONST={5:5,15:9,30:13,60:16}

def tf_env(m):
    e,c,r=TRIPLE[m]
    return {"SCALPER_TF_ENTRY":e,"SCALPER_TF_CONTEXT":c,"SCALPER_TF_REGIME":r}
def tb_env(m):
    h,c=TB[m]; return {"SCALPER_MAX_HOLD_HOURS":h,"SCALPER_LOSS_COOLDOWN_MINUTES":c}
def geom_s(m):
    S=S_CONST[m]
    return {"SCALPER_DYN_LEV_STOP_ATR_MULT":"2","SCALPER_FIXED_STOP_ROI_PCT":str(S),"SCALPER_TP1_ROI":str(S),"SCALPER_TP2_ROI":str(2*S)}

def arm(id_,m,geom,gates,role,sets,note,extra=None):
    env={}
    env.update(tf_env(m))
    if not gates: env.update(BG)
    env.update(tb_env(m))
    if geom=="S": env.update(geom_s(m))
    out={"id":id_,"role":role,"entry_tf":TRIPLE[m][0],"triple":list(TRIPLE[m]),
         "geometry":"scaled_S%d"%S_CONST[m] if geom=="S" else "server_geometry",
         "S_arm":S_CONST[m] if geom=="S" else 50,
         "gates":"BG_off" if not gates else "snapshot_kept",
         "sets":sets,"env":env,"note":note}
    if extra: out.update(extra)
    return out

family=[
 arm("S5",5,"S",False,"family",["DEV","VAL"],"5m/30m/1h, olceklenmis: stop=2*ATR(5m), TP1=1R, TP2=2R; L=clamp(round(5/(2*ATR%)),3,20). S=5 DONDURULMUS secim (formul 4.5+-0.05 verir; 4 da gecerliydi). Beklenen N: DEV~1760, VAL~3300; CAL 444/92g.",{"cal_trades_92d":444}),
 arm("T5",5,"T",False,"family",["DEV","VAL"],"5m/30m/1h, yalniz zamanlama: sunucu geometrisi (S=50,k=3,TP1_ROI=10,TP2_ROI=25) => ~%100 islem 20x. Beklenen N: DEV~1560, VAL~2940; CAL 394/92g.",{"cal_trades_92d":394}),
 arm("S15",15,"S",False,"family",["DEV","VAL"],"15m/1h/4h, olceklenmis S=9. Beklenen N: DEV~640, VAL~1210; CAL 162/92g.",{"cal_trades_92d":162}),
 arm("T15",15,"T",False,"family",["DEV","VAL"],"15m/1h/4h, sunucu geometrisi (%96 islem 20x). Beklenen N: DEV~670, VAL~1260; CAL 169/92g.",{"cal_trades_92d":169}),
 arm("S30",30,"S",False,"family",["DEV","VAL"],"30m/4h/4h, olceklenmis S=13. context==regime==4h: rejim serisi (+250) baglam serisini (+100) ezer (zararsiz fazla isinma), H3 testiyle kilitli. Beklenen N: DEV~335, VAL~630; CAL 84/92g.",{"cal_trades_92d":84}),
 arm("T30",30,"T",False,"family",["DEV","VAL"],"30m/4h/4h, sunucu geometrisi (%85 islem 20x; ATR%>0.83 olunca L<20). Beklenen N: DEV~375, VAL~700; CAL 94/92g.",{"cal_trades_92d":94}),
]
descriptive=[
 arm("S60",60,"S",False,"descriptive",["DEV","VAL_if_reached"],"1h/4h/4h, olceklenmis; S=min(round(20*2*0.503)=20, floor(3*MAX_STOP_PCT)=16)=16 => L=3'te stop%<=5.33<5.5, sessiz max-stop dususu imkansiz. BH DISI, kazanamaz, p yok; ~140 islem/yil: PF<~1.9 tespit edilemez. CAL 35/92g.",{"cal_trades_92d":35}),
 arm("T60",60,"T",False,"descriptive",["DEV","VAL_if_reached"],"1h/4h/4h, sunucu geometrisi (%68 islem 20x). BH DISI, kazanamaz.",{"cal_trades_92d":None}),
]
refs=[
 arm("S5G",5,"S",True,"reference_gated",["DEV"],"S5 ile ayni ama kapilar KORUNMUS (rejim filtresi VARSAYILAN acik = 1h EMA50/200 [sunucudaki 15m DEGIL], market gate+hafta sonu LONG blogu snapshot'tan). 'Kapilari kaldirmak' etkisini olcer. BH disi, kazanamaz. CAL 168/92g.",{"cal_trades_92d":168}),
 arm("T5G",5,"T",True,"reference_gated",["DEV"],"Kullanicinin harfi harfine sorusu: canli bot, sadece 5m grafik (sunucu geometrisi, kapilar korunmus; rejim TF'si 1h). BH disi, kazanamaz. CAL 153/92g.",{"cal_trades_92d":153}),
]
b0={"id":"B0","role":"reference_live","entry_tf":"1m","triple":["1m","5m","15m"],"geometry":"server_geometry","S_arm":50,"gates":"snapshot_kept","sets":["DEV_quarterly","FINAL"],"env":{},
    "note":"Sunucu snapshot'i AYNEN (kod varsayilani + snapshot). DEV: 4 takvim ceyregi kosusu; FINAL: tek kosu, E16 ile BIREBIR 320 islem ve PF 0.90 (iki hane) uretmeli (parite capasi).",
    "dev_quarters":[["2025-01-01","2025-04-01"],["2025-04-01","2025-07-01"],["2025-07-01","2025-10-01"],["2025-10-01","2026-01-01"]]}
r1={"id":"R1","role":"reference_bg_1m","entry_tf":"1m","triple":["1m","5m","15m"],"geometry":"server_geometry","S_arm":50,"gates":"BG_off","sets":["DEV_quarterly"],"env":dict(BG),
    "note":"B0 + yalniz BG (kapilar kapali), 1m. Kapi degisikligini TF degisikliginden ayiran birebir kontrol.",
    "dev_quarters":b0["dev_quarters"]}

overlays=[
 {"id":"ROB_FEE2","env":{},"cli":["--fee-stress"],"gate":"G6 (DEV), G15a (VAL)","note":"komisyon+kayma 2x; --fee-stress-multiplier GECILMEZ (varsayilan 2.0 kilitli)."},
 {"id":"ROB_DLY1","env":{},"cli":["--entry-delay-candles","1"],"gate":"G6 (DEV), G15b (VAL)","note":"Maker dolum penceresini 1 bar kaydirir (limit fiyati ayni). Canli 'ayni kapanmis mumu her taramada yeniden degerlendirme' farkini YOKLAMAZ."},
 {"id":"ROB_TAKER","env":{"SCALPER_ENTRY_MODE":"taker"},"cli":[],"gate":"G6 (DEV), G15c (VAL)","note":"Maker dolum modelinin iyimserligini siniri: sonraki mum acilisinda kayma+taker ucret."},
 {"id":"ROB_WALL","env":{"SCALPER_MAX_HOLD_HOURS":"8","SCALPER_LOSS_COOLDOWN_MINUTES":"60"},"cli":[],"gate":"G15d (yalniz VAL; yalniz Σ R>0)","note":"Sure kuralinin duvar-saati kutbu (canli degerler). Arm'in diger env'i aynen korunur."},
 {"id":"DIAG_Z0","env":{"SCALPER_MAKER_FEE_PCT":"0","SCALPER_TAKER_FEE_PCT":"0","SCALPER_SLIPPAGE_RATE":"0"},"cli":[],"gate":"teshis (karar vermez)","note":"Ucretsiz brut kenar; DEV_DECISION.json yazildiktan SONRA."},
 {"id":"DIAG_SBO","env":{},"cli":["--same-bar-order","optimistic"],"gate":"teshis (karar vermez)","note":"Ayni-bar sirasi iyimser sinir (H2): TP1 sonrasi BE stopu, TP2 sonrasi taban ve dolum-bari cikislari SONRAKI bardan itibaren aktif. Kapilar KOTUMSER sirada kalir; fark 'BAR-ORDER-BELIRSIZ' etiketi uretebilir, arm ILERLETMEZ."},
 {"id":"DIAG_FEE15","env":{},"cli":["--fee-stress","--fee-stress-multiplier","1.5"],"gate":"teshis (karar vermez)","note":"Ucrete-duyarlilik: FEE2'yi gecemeyip FEE1.5'i gecen arm 'MARJINAL-UCRETE-DUYARLI' diye etiketlenir."},
]
common_cli=["--strategies","C","--symbols","BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT","--diagnostics"]
doc={
 "schema":"e17-variants/2",
 "plan":"PLAN.md v2 (duzeltilmis taslak; KILITLENMEMISTIR)",
 "lock":None,
 "snapshot":{"policy":"Sunucu SCALPER_* snapshot'i YALNIZ yerelde tutulur, ICERIGI BURAYA/HICBIR YERE KOPYALANMAZ; yalniz sha256'si kilide girer. Her varyant = snapshot + 'env' alani (cozulmus, bilesim yok).","sha256":snap_sha,"sha256_note":"bilgi amacli; kilit adimi yeniden hesaplar"},
 "common_cli":common_cli,
 "symbols_order_pinned":"BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT",
 "constants":{"S_by_entry_minutes":S_CONST,"TB_bars":{"MAX_HOLD_bars":480,"LOSS_COOLDOWN_bars":60,"MAKER_FILL_TIMEOUT_CANDLES":3,"STALE_TP_HOURS":0},
              "TB_table":{str(k):{"SCALPER_MAX_HOLD_HOURS":v[0],"SCALPER_LOSS_COOLDOWN_MINUTES":v[1]} for k,v in TB.items()},
              "BG":BG},
 "family":family,"descriptive":descriptive,"references":[*refs,b0,r1],"overlays":overlays,
 "overlay_applicability":{
   "G6_DEV":{"overlays":["ROB_FEE2","ROB_DLY1","ROB_TAKER"],"applies_to":"every family arm that passes G1-G5 (and validity); never to references"},
   "G15_VAL":{"overlays":["ROB_FEE2","ROB_DLY1","ROB_TAKER","ROB_WALL"],"applies_to":"arms advanced to VAL"},
   "DIAG":{"overlays":["DIAG_Z0","DIAG_SBO","DIAG_FEE15"],"applies_to":"all 6 family arms on DEV, after DEV_DECISION.json is written; DIAG_FEE15 only on arms that failed FEE2 in G6 or reached VAL"}},
 "sets":{"CAL":["2026-03-01","2026-06-01"],"DEV":["2025-01-01","2026-01-01"],"VAL":["2023-01-01","2024-11-18"],"FINAL":["2026-09-03","2026-10-08"],
         "SMOKE_W":["2026-04-01","2026-05-01"],"SMOKE_1M":["2026-04-01","2026-04-08"],"S1_AYI":["2026-01-23","2026-02-13"],"S1_V1":["2025-01-06","2025-01-27"]},
 "run_matrix_counts":{"DEV":{"family_base":6,"descriptive":2,"gated_refs":2,"B0_quarters":4,"R1_quarters":4,"G6_overlays_max":18,"DIAG_max":18},"VAL_max_per_advanced_arm":5,"FINAL":2}
}
json.dump(doc,open(f"{SP}/e17/variants.json","w"),indent=1,ensure_ascii=False)
print("ok",snap_sha[:16])
