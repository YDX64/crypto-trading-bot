# D38 — Misafir izleme erişimi

## Kapsam ve mevcut durum

- Hedef yalnız `awa:/opt/tradingbot-v2`, TESTNET, IP monitor `:9443`.
- Mevcut Nginx Basic Auth: `/etc/nginx/.htpasswd-tradingbot-ip`, `640 root:www-data`.
- Ön kontrolde yalnız `efe` hesabı vardı; ayrı `misafir` hesabı isteniyor.
- Vhost `/etc/nginx/sites-available/tradingbot-monitor-ip`: GET/HEAD izleme
  beyaz listesi, Authorization/X-API-Key upstream'e boş geçirilir.
- Uygulama yalnız `127.0.0.1:9091` dinler. Misafir ayrı uygulama rolü değildir;
  yetki sınırı aynı salt-okunur Nginx monitor girişidir.
- Misafir bakiyeyi ve tam işlem/teşhis görünümünü görür. Gizlenmiş bakiye veya
  yalnız özet bir "public" sayfa değildir.

## Hassas teşhis düzeltmesi

İncelemede `TelegramBotService.start()` ham `InvalidToken` exception metnini
`_last_error` içine koyuyordu; `/health.telegram_details` bunu dışarı veriyordu.
SDK metni token içerebilir. Gerçek kimlik bilgisi kullanılmadan sentetik
örneklerle yeniden üretildi. İzinli 12 JSON handler, `MonitorJSONResponse`
üzerinden credential redaction uygular. Sadece dışarı verilen JSON kopyası
maskelenir; motor veya önbellekteki veriyi ve emir yolunu değiştirmez.

## Kimlik bilgisi yönetimi

`scripts/dashboard_guest.py` yalnız `misafir` kullanıcısını ekler/iptal eder.
Parola yerelde rastgele oluşturulur ve yeni, `600` izinli `.env` içine yazılır;
üst klasör özeldir. Parola stdout, argv, Git veya bu rapora yazılmaz.
Sunucu yalnız SHA-512 crypt parola özetini tutar. Önceki kullanıcı kayıtları
korunur; auth dosyası kilit, yedek, atomik replace ve izin kontrolleriyle değişir.
Sadece hesap işlemi için uygulama/Nginx restart'ı gerekmez.

## Kanıt durumu

- Değişiklik öncesi tam yerel test: **3146 passed, 2 skipped**, 72.16 sn.
- Misafir aracı + JSON düzeltmesi tam yerel test: **3212 passed, 2 skipped**,
  69.17 sn; yalnız mevcut iki bağımlılık uyarısı. Fatal Ruff ve whitespace
  kontrolü geçti. Son incelemede tarihî/rotated credential'ın tırnaklı alan
  adında gizlenmesi de kapatıldı: redaksiyon **53**, auth aracı **26**, toplam
  **79 hedefli test geçti**; final tam sayım deploy logundadır.
- İki çapraz inceleme: auth aracında hedef/kullanıcı sınırı, parola taşınması,
  yedek/atomik güncelleme; JSON katmanında 12 uç, cached-object korunması,
  kod/kontrat/kimlik bilgisi varyantları. Tırnaklı eski API-key bulgusu kapatıldı.
- Ön kontrol: PID `2717762`, `/health` HTTP 200, `status=healthy`,
  `core_healthy=true`, `network=testnet`; `entry_halted=false`,
  `tracked=0`, `pending=0`, `kill_switch_active=true`.
- Düzeltme testleri, yayın ve gerçek misafir erişim kanıtı uygulama sonrası
  aşağıya eklenir. Bu bölüm tek başına hesabın açıldığını kanıtlamaz.

## Kabul kontrolleri ve geri alma

Sertifikası doğrulanmış public HTTPS üzerinde: kimliksiz/yanlış parola 401;
misafir dashboard ve veri GET'leri 200; POST 403; kontrol yolları 404
(`/signal` 403); `/scalper/counterfactual`, `.env`, OpenAPI ve docs kapalı.
Trades limit sabit 30. Gerçek tarayıcıda panel, yenilenen veriler ve işlem
ayrıntısı açılır; browser credentials hiçbir dış origin'e gönderilmez.

Erişim iptali `scripts/dashboard_guest.py revoke` ile yalnız misafiri kaldırır;
`efe` korunur. İptal, tarayıcıda daha önce indirilmiş veriyi geri alamaz.
Kod geri alma gerekirse önce erişimi iptal et, sonra
`scripts/deploy.sh awa 0d73142e05e2f7f06a9356b244dad5a90f01ff2d`.
Güvenlik kapıları atlanmaz, işlem `.env`'i/geçmiş DB sıfırlanmaz.
