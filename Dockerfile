# TRADINGBOT — tek container görüntüsü (EK dağıtım yolu; supervisord yolu AYNEN durur)
# =============================================================================
# Bu görüntü, botun TAMAMINI (FastAPI + scalper motoru + gömülü AlgoPro
# takipçisi + pano + testler) tek bir süreçte taşır. Amaç taşınabilirliktir:
# `docker compose up -d` ile başka bir sunucuda aynı bot ayağa kalkar.
#
# ⛔ AYNI ANDA İKİ MOTOR ÇALIŞTIRMAK YASAKTIR. Sunucudaki supervisord programı
#    (`tradingbot_v2`) ile bu container AYNI Binance hesabına bağlanırsa aynı
#    pozisyonları İKİ motor yönetir (çift SL/TP, çift kapanış, yarışan
#    devralma). Kapı `scripts/docker_run.sh` içindedir; bkz. docs/RUNBOOK.md
#    "Container ile çalıştırma / başka sunucuya taşıma".
#
# TABAN SÜRÜM SEÇİMİ (ölçülmüş, tahmin değil):
#   * .github/workflows/ci.yml: "Sunucu venv'i Python 3.12 — CI aynı sürümü
#     kullanır (parite)" ve `python-version: "3.12"`.
#   * INSTALL.md "Python 3.11 veya üzeri" der; yani 3.12 hem dokümana hem
#     sunucuya uyar.
#   Container 3.11 seçseydi "container'da testler geçti" ile "sunucuda testler
#   geçti" AYNI ŞEY OLMAZDI (deploy kapısı zayıflardı). Bu yüzden 3.12.
# =============================================================================

# ── 1) Derleme aşaması: tekerlekler burada kurulur, runtime'a taşınır ────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# psycopg2-binary/cryptography vb. için derleme başlıkları. Runtime aşamasına
# TAŞINMAZ — nihai görüntüde derleyici bulunmaz (saldırı yüzeyi + boyut).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Bağımlılıklar KODDAN ÖNCE: requirements.txt değişmedikçe bu katman
# önbellekten gelir (kod değişikliğinde yeniden pip install YAPILMAZ).
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# ── 2) Çalışma aşaması ───────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# TZ: loguru damgaları YEREL saati kullanır (src/core/logger.py). Container'ı
# UTC'ye sabitlemek, damgaların hangi makinede koştuğundan bağımsız aynı
# ölçekte kalmasını sağlar (modellerde naive `utcnow` var; günlük PnL
# rollover'ı ve cooldown pencereleri TZ'ye duyarlıdır).
# ⚠️ BUNUN SONUCU: bot.log damgaları UTC'dir; `scripts/docker_run.sh` 418 ban
# penceresini bu yüzden UTC ile hesaplar (`restart_safe.sh` YEREL kullanır —
# orada yazan da okuyan da aynı TZ'dedir). Ayrıntı: docker_run.sh "Kapı 3".
#
# HOME=/tmp (BİLİNÇLİ, /app DEĞİL): /app root:root 0755'tir — kod katmanı
# salt-okunur kalsın diye. Non-root `bot` oraya yazamaz; HOME=/app olsaydı
# `~` kullanan herhangi bir kütüphane (pip/matplotlib/keyring önbellekleri)
# EACCES alırdı. Bugünkü kod `~` kullanmıyor; bu bir emniyet payıdır.
ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp

# libpq5: psycopg2-binary'nin çalışma zamanı kütüphanesi.
# tzdata: TZ=UTC'nin gerçekten çözülebilmesi için.
# curl: HEALTHCHECK + docker_run.sh sağlık yoklaması (slim'de yoktur).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 tzdata curl \
 && rm -rf /var/lib/apt/lists/* \
 && ln -snf /usr/share/zoneinfo/UTC /etc/localtime && echo UTC > /etc/timezone

# Non-root: motor root olarak koşmaz. UID sabit (10001) çünkü kalıcı
# bind-mount dizinlerinin (state/logs/backups/data) sahipliği bu UID'ye
# ayarlanır — bkz. scripts/docker_run.sh.
RUN groupadd --gid 10001 bot \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin bot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Kod EN SON kopyalanır (üstteki katmanlar önbellekte kalsın).
# `.dockerignore` `.env`i, `*.db`yi, `logs/`, `state/`, `backups/`, `data/` ve
# `.git`i DIŞARIDA bırakır — `tests/`, `conftest.py`, `pytest.ini`,
# `Dockerfile`, `docker-compose.yml` ise İÇERİDE kalır (container içinde
# `python -m pytest tests` bir deploy kapısıdır ve tests/test_container.py bu
# dosyaları okur).
COPY --chown=bot:bot . /app

# Kalıcı veri bağlanma noktaları + test log dizini. Bind-mount edildiklerinde
# host'un sahipliği geçerli olur; edilmezlerse container içinde bot yazabilir.
RUN mkdir -p /app/logs /app/state /app/backups /app/data /app/data/klines_cache /app/.test-logs \
 && chown -R bot:bot /app/logs /app/state /app/backups /app/data /app/.test-logs \
 && chmod 1777 /app/.test-logs

USER bot

# Scalper halkasının portu. Takipçi halkası (:9093) compose'daki `follower`
# profilinde `command:` ile geçersiz kılınır.
EXPOSE 9091

# Sağlık: /health gerçek durumu yansıtır (degraded iken 503 döner), bu yüzden
# `curl -f` doğru kapıdır. `start-period` cömerttir: açılış (Binance init +
# pozisyon devralma) ~90 sn sürebilir; docs/RUNBOOK.md bu yüzden 240 sn'ye
# kadar yoklar. NOT: "unhealthy" damgası container'ı KENDİLİĞİNDEN YENİDEN
# BAŞLATMAZ (docker'ın davranışı) — bu KASITLIDIR: açık pozisyonu olan bir
# trading motorunu sağlık yoklaması yüzünden restart döngüsüne sokmak,
# çözdüğünden çok sorun yaratır. Damga yalnız teşhis içindir.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9091/health || exit 1

# ⛔ TEK SÜREÇ — `--workers 1` PAZARLIK KONUSU DEĞİLDİR.
# Bot tek bir asyncio sürecidir: scalper motoru, güvenlik döngüleri, cooldown
# ve entry-halt dosyaları, sembol rezervasyonları ve Telegram supervisor'ı
# SÜREÇ-GENELİ tekil durumdur. `--workers 2` iki BAĞIMSIZ motor başlatır:
#   * ikisi de aynı Binance hesabında aynı pozisyonu devralmaya çalışır,
#   * ikisi de state/*.json dosyalarına yazar (son yazan kazanır → cooldown ve
#     entry-halt kilitleri sessizce kaybolur),
#   * ikisi de tarama yapar → çift giriş, çift REST ağırlığı (418 ban riski).
# Aynı gerekçeyle `--reload` da kullanılmaz.
# tests/test_container.py bu satırı kilitler.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "9091", "--workers", "1"]
