# analyze_prereg.py değişiklik kaydı (PLAN §0: "hata düzeltmesi serbesttir, parametre değişikliği değildir")

## v1 → v2 · 2026-10-10 04:56 UTC · ana oturum
Kilit: b8e5527f4efc4356b3689f7061b368cbfda23f28578f5ffd3f2d3eef1e5b6ea3 → 32df0bc1c62233a796001984d6fc905ea6970f4dcdb43c5a220c019d33a497e7
Zaman: HİÇBİR doğrulama (V1–V8) koşusu yokken; runs/ yalnız F0 duman/dev koşularını içeriyor.
Kaynak: analiz denetçisi (prereg-analysis-and-code-variants iş akışı) bulguları.
1. (ORTA) decide: G1–G10 geçen bir aday varken G9/G10'u EKSİK (BEKLİYOR) bir aday daha yüksek Calmar'la sonradan
   kazanabilirdi ama betik erken KAZANAN ilan ediyordu. Artık bekleyen varken karar = PENDING (PLAN §3 "tüm kapıları
   geçenler arasından en yüksek Calmar"). Test: test_decide_pending_beats_premature_winner (mutasyonla kırmızı).
2. (DÜŞÜK) Run.load: eksik/null overall.bar_max_drawdown sessizce 0.0 oluyordu (G5'i geçirir, Calmar'ı şişirirdi).
   Artık INF + uyarı. Test: test_missing_bar_dd_is_inf_not_zero.
3. (DÜŞÜK) final: tek atış zorlanmıyordu. Artık yalnız gates.json'daki WINNER ile ve FINAL.md yoksa koşar.
   Test: test_final_is_single_shot_and_winner_only.
Ek (gerçeğe uyum, karar mantığı değil): lib.sh VAR tablosuna C1/C2 eklendiği için test_parse_lib beklentisi ve
DECISION.md eşitlik metni güncellendi. Eşik, pencere, varyant, bootstrap tanımı DEĞİŞMEDİ.
Test: 38 passed.
