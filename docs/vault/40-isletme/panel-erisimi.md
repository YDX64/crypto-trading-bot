---
tags: [isletme, pano, nginx, erisim]
guncelleme: 2026-08-24
kaynak: docs/RUNBOOK.md "Pano erisimi (nginx monitor proxy)" (satir 215-241)
---
# Panel (pano) erisimi ve nginx beyaz listesi

## Iki erisim yolu

| Yol | Nasil |
|---|---|
| **SSH tuneli** (gelistirici) | `ssh -L 9091:127.0.0.1:9091 awa` → http://127.0.0.1:9091/dashboard |
| **nginx proxy** (kullanici) | `https://<sunucu-ip>:9443/dashboard`, HTTP Basic auth |

Bot **yalniz `127.0.0.1:9091`'e baglidir** — proxy disinda disari ACIK
DEGILDIR.

## ⚠️ BEYAZ LISTE KURALI (en sik dusulen tuzak)

Proxy **YALNIZ sayilan salt-okuma GET uclarini** gecirir; listelenmeyen her
yol **404** doner (catch-all).

> **Yeni bir pano karti yeni bir uc cagiriyorsa, proxy'ye EKLENMEDEN
> kullanicida CALISMAZ** — localhost'ta calisir.
> Bu tuzaga **2026-08-24'te dusuldu**: D21 adli kayit karti proxy'de 404
> aliyordu.

**Izinli (2026-08-24 itibariyla):** `/dashboard`, `/health`, `/api/status`,
`/positions`, `/config`, `/waiting-mode/active`, `/scalper/status`,
`/scalper/stats`, `/scalper/trades` (query sabit `limit=30`),
`/scalper/forensics/(summary|recent)`, `/scalper/trades/<id>/forensics`,
`/follower/status`.

**ASLA eklenmez:** `/tv-signal`, `/risk-event`, `/follower/event`,
`/tv-events/reset` ve tum POST/kontrol uclari (secret tasirlar / durum
degistirir).

ℹ️ **D23 (AI kapisi) karti YENI UC ACMAZ** — verisini `/api/status` govdesindeki
`ai_gate` blogundan okur; nginx'e bakmak **gerekmez**.

## Degisiklikten sonra

```
nginx -t → systemctl reload nginx
curl -k <izinli-uc>   # kimliksiz → 401 beklenir
curl -k <kontrol-ucu> # kimliksiz → 404 beklenir
```
Yedek: `sites-available/tradingbot-monitor-ip.bak-<tarih>`.

## Pano maliyeti

`/api/status` ve `/scalper/status` sunucuda **5 sn onbelleklenir** ve pano
yolundan `force_fresh` **ISTENMEZ** (2026-08-18 rate-limiter acligi dersi).
Yanittaki `as_of` govdenin KURULDUGU andir — pano "son guncelleme"yi ondan
yazmalidir.

ILGILI: [[40-isletme/sorun-giderme]] · [[50-veri/metrikler]] · [[90-ai-icin/sik-yapilan-hatalar]] · [[10-mimari/gozlem-katmanlari]]
