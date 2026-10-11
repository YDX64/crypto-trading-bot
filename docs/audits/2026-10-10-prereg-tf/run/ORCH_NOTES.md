# E17 orkestratör kaydı
- Kilit (2026-10-10T18:58Z): L=f962d4867b83e2c92478b12ea21a9f06bf0cfc38 (origin/main-18w3qz'ye itildi)
- H=14d3b17c53b18ef5f2eac5029f69fbff1b4119e5
- sha256(LOCK.json)=3f7da5dd207cb14878cc1ee83e6bfff70c5787a014d795f6016db9a575a16513
- Not: lock commit'i 6346bfa idi; itilmeden önce yalnız mesajına atıf satırları eklendi (ağaç aynı), LOCK_REF güncellendi.
- E17_ROOT=/root/e17_root ; E17_SNAPSHOT=/root/e17_root/server-scalper-env.txt
- Onaylanan sapmalar: PLAN §14.1.
- L2 (2026-10-10 ~19:45Z): L=c00772ae32bd817c1703f7e97e2199acff445606, H=a3168cda7fcaa7e3a6d5276be03ee5bb96cec27a,
  sha256(LOCK.json)=6aa9f3a15bd18c67388bebea111370b99271e5a35cb8d6866134697577270c33.
  Sebep: duman 'manifest smoke' ValidationError (analizci H klonunda sahte gizlisiz). Yalnız lib17.sh + test değişti.
- 2026-10-10 ~21:17-22:17Z: konteyner yeniden başladı (uptime 0 dk @22:18). DEV zinciri DEV_B0_Q3 koşusunun ortasında öldü
  (rapor yok, ledger'a girmedi). Ledger 29 kayıt (13 duman + 16 DEV, son DEV_B0_Q2 21:17:20Z). PLAN §5.2: rapor üretilmeden
  düşen koşu altyapı hatasıdır, yeniden denenir. Zincir 22:2xZ'de yeniden başlatıldı; tamamlananlar ledger'dan atlanır.
- 2026-10-10 ~22:3x-23:05Z: konteyner İKİNCİ kez geri alındı (uptime 0 dk @23:05). Zincir fill doğrulaması + 16 SKIP sonrası
  DEV_B0_Q3'ün ortasında öldü (rapor yok). Kök neden: oturumda harness-takipli arka plan işi yokken boşta konteyner geri
  alınıyor (setsid/nohup süreçleri ölüyor, disk kalıyor). Düzeltme: zincir run_in_background Bash işi olarak koşulur.
