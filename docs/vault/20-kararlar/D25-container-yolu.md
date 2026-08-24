---
tags: [karar, aktif, container, docker, tasima]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D25 (satir 2560), docs/RUNBOOK.md "Container ile calistirma"
---
# D25 — Tek container dagitim yolu · AKTIF (**EK YOL** — canli supervisord DEGISMEDI)

**Karar.** Bot tek bir `python:3.12-slim` goruntusune paketlendi: `Dockerfile`,
`docker-compose.yml`, `.dockerignore`, `scripts/docker_run.sh`,
`tests/test_container.py`, CI'da `docker-build` isi (build-only, push YOK).
**Neden.** Kullanici: *"hepsi ayni container'da olsun, sonra baska sunucuya
tasiyacagim."*

⛔ **EN KRITIK KURAL: supervisord ile container AYNI ANDA CALISAMAZ** — ayni
Binance hesabi, ayni pozisyonlar → cift SL/TP, yarisan devralma,
`state/*.json`'da son-yazan-kazanir. `scripts/docker_run.sh` bunu IKI bagimsiz
sinyalle yoklar (supervisorctl + `pgrep -af 'uvicorn.*src\.main:app'`);
bilincli istisna `DOCKER_ALLOW_ALONGSIDE=1`.

⛔ **Ciplak `docker compose up` KULLANMAYIN** — entry-halt kilidini, 418 ban
penceresini ve cakisma kapisini ATLAR.

**Kanit (olculdu, yerel docker 27.4.0 / linux-aarch64, 2026-08-24).**

| Iddia | Olcum |
|---|---|
| Goruntu derleniyor | 715 MB, ~2 dk; `user=bot` (uid 10001), Python 3.12.14, `TZ=UTC` |
| Ayaga kalkiyor | `env.example` ile `/health` **503 degraded** (beklenen: API anahtari gecersiz), `/dashboard` 200 |
| Defter kalici | sqlite `data/` mount'unda (WAL kardesleri ayni dizinde) |
| Zarif kapanis | `docker stop -t 120` → **1 sn, exit 0** |
| entry-halt kalici + fail-closed | bozuk dosyada `entry_halted=true` |
| Secret sizintisi yok | `docker logs`'ta `secret=***` |
| Test paketi container ICINDE | 2021 passed, 2 skipped (o tarihte host ile AYNI) |

**Durum.** AKTIF, ek yol. `scripts/deploy.sh` / `server_deploy.sh` /
`restart_safe.sh` dosyalarina **DOKUNULMADI**.
**Geri alma.** Container'i durdur; canli yol zaten degismedi.
**Tuzak.** `docker compose down -v` **KULLANMAYIN** — isimli volume kullanan
bir kurulumda islem defterini siler.

ILGILI: [[40-isletme/halka-yonetimi]] · [[40-isletme/deploy-ve-geri-alma]] · [[90-ai-icin/sik-yapilan-hatalar]]
