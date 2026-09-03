"""Container sözleşmesi testleri — Dockerfile / docker-compose.yml / .dockerignore /
scripts/docker_run.sh.

NEDEN STATİK: bu testler docker daemon'ı GEREKTİRMEZ. Tam paket CI'da,
geliştirici makinesinde ve container'ın KENDİ İÇİNDE koşar; hiçbirinde docker
daemon'ı olduğu varsayılamaz. Sözleşme metinden okunur.

NE KİLİTLENİR (hepsi bir arıza sınıfına karşılık gelir):
  * `--workers 1`      → iki motor aynı hesapta (çift SL/TP, state yarışı)
  * `.env` MOUNT       → sır görüntüye gömülürse görüntü ihraç edildiğinde sızar
  * kalıcı volume'ler  → container silinince defter/state/log kaybolur
  * non-root           → container kaçışının etkisini sınırlar
  * healthcheck        → "başlattım" ile "çalışıyor" karışmasın (CLAUDE.md #6)
  * follower profili   → ikinci halka KAZARA açılmasın (D20b tercih edilen)
  * docker_run.sh kapıları → entry-halt / 418 ban / supervisord ile ÇİFT ÇALIŞMA

Docker gerçekten varsa (`docker info` çalışıyorsa) en sonda opsiyonel bir
duman testi build eder ve `/health`i yoklar; yoksa ATLANIR.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
import yaml  # uvicorn[standard] bağımlılığı (requirements.txt) — daima kurulu

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DOCKER_RUN_SH = REPO_ROOT / "scripts" / "docker_run.sh"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

SCALPER_PORT = 9091   # docs/RUNBOOK.md halka tablosu (testnet)
FOLLOWER_PORT = 9093  # docs/RUNBOOK.md halka tablosu (D20 takipçi)


# ── Yardımcılar ─────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    assert path.exists(), f"beklenen dosya yok: {path}"
    return path.read_text(encoding="utf-8")


def _dockerfile_instructions(text: str):
    """(TALİMAT, argüman) çiftleri — satır devamları (`\\`) birleştirilmiş,
    yorumlar atılmış hâlde."""
    joined = re.sub(r"\\\s*\n", " ", text)
    out = []
    for raw in joined.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        out.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
    return out


def _compose():
    return yaml.safe_load(_read(COMPOSE_FILE))


def _service(name: str):
    services = _compose()["services"]
    assert name in services, f"docker-compose.yml içinde '{name}' servisi yok"
    return services[name]


def _volume_pairs(service) -> list[tuple[str, str, str]]:
    """`- host:container[:mod]` girdilerini (host, container, mod) üçlüsüne çevirir."""
    pairs = []
    for entry in service.get("volumes", []):
        assert isinstance(entry, str), f"kısa biçim bekleniyordu: {entry!r}"
        bits = entry.split(":")
        host, container = bits[0], bits[1]
        mode = bits[2] if len(bits) > 2 else ""
        pairs.append((host, container, mode))
    return pairs


def _dockerignore_patterns() -> list[str]:
    out = []
    for raw in _read(DOCKERIGNORE).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


# ── 1) Dosyalar var mı ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [DOCKERFILE, COMPOSE_FILE, DOCKERIGNORE, DOCKER_RUN_SH],
    ids=lambda p: p.name,
)
def test_container_files_exist(path):
    assert path.exists(), f"container dağıtım dosyası eksik: {path}"


# ── 2) Dockerfile sözleşmesi ────────────────────────────────────────────────


def test_dockerfile_is_multi_stage():
    froms = [arg for op, arg in _dockerfile_instructions(_read(DOCKERFILE)) if op == "FROM"]
    assert len(froms) >= 2, f"çok aşamalı build bekleniyordu, bulunan FROM: {froms}"


def test_dockerfile_python_version_matches_ci_and_server():
    """Taban görüntü CI (= sunucu venv'i) ile AYNI Python minor sürümü olmalı.

    Aksi hâlde "container içinde testler geçti" ile "sunucuda testler geçti"
    aynı şey olmaz ve container yolu bir deploy kapısı olmaktan çıkar.
    """
    froms = [arg for op, arg in _dockerfile_instructions(_read(DOCKERFILE)) if op == "FROM"]
    versions = set()
    for f in froms:
        m = re.search(r"python:(\d+\.\d+)-slim", f)
        assert m, f"beklenen taban görüntü 'python:X.Y-slim' değil: {f!r}"
        versions.add(m.group(1))
    assert len(versions) == 1, f"aşamalar farklı Python sürümleri kullanıyor: {versions}"
    dockerfile_version = versions.pop()

    ci = yaml.safe_load(_read(CI_WORKFLOW))
    ci_versions = set()
    for job in ci["jobs"].values():
        for step in job.get("steps", []):
            with_ = step.get("with") or {}
            if "python-version" in with_:
                ci_versions.add(str(with_["python-version"]))
    assert ci_versions, "CI iş akışında python-version bulunamadı"
    assert dockerfile_version in ci_versions, (
        f"Dockerfile Python {dockerfile_version} kullanıyor ama CI/sunucu {ci_versions} "
        "kullanıyor — parite bozuk"
    )


def test_dockerfile_pip_installs_use_no_cache_dir():
    for op, arg in _dockerfile_instructions(_read(DOCKERFILE)):
        if op != "RUN" or "pip install" not in arg:
            continue
        assert "--no-cache-dir" in arg, f"--no-cache-dir eksik: RUN {arg[:120]}"


def test_dockerfile_copies_requirements_before_app_code():
    """Katman önbelleği: requirements.txt kod COPY'sinden ÖNCE gelmeli."""
    instrs = _dockerfile_instructions(_read(DOCKERFILE))
    req_idx = app_idx = None
    for i, (op, arg) in enumerate(instrs):
        if op == "COPY" and "requirements.txt" in arg and req_idx is None:
            req_idx = i
        if op == "COPY" and re.search(r"(^|\s)\.\s|\s/app\s*$", arg) and "requirements" not in arg:
            if app_idx is None:
                app_idx = i
    assert req_idx is not None, "requirements.txt COPY edilmiyor"
    assert app_idx is not None, "uygulama kodu COPY edilmiyor"
    assert req_idx < app_idx, "requirements.txt kod COPY'sinden SONRA geliyor (önbellek bozuk)"


def test_dockerfile_sets_required_env():
    envs = {}
    for op, arg in _dockerfile_instructions(_read(DOCKERFILE)):
        if op != "ENV":
            continue
        for m in re.finditer(r'([A-Z_][A-Z0-9_]*)=("[^"]*"|\S+)', arg):
            envs[m.group(1)] = m.group(2).strip('"')
    assert envs.get("TZ") == "UTC", f"TZ=UTC bekleniyordu, bulunan: {envs.get('TZ')!r}"
    assert envs.get("PYTHONUNBUFFERED") == "1", "PYTHONUNBUFFERED=1 eksik (log tamponlanır)"


def test_dockerfile_runs_as_non_root():
    users = [arg.strip() for op, arg in _dockerfile_instructions(_read(DOCKERFILE)) if op == "USER"]
    assert users, "USER talimatı yok — container root olarak koşar"
    final_user = users[-1]
    assert final_user not in ("root", "0", "0:0"), f"son USER root: {final_user!r}"


def test_dockerfile_has_healthcheck_on_health_endpoint():
    checks = [arg for op, arg in _dockerfile_instructions(_read(DOCKERFILE)) if op == "HEALTHCHECK"]
    assert checks, "HEALTHCHECK yok"
    assert "/health" in checks[0], f"HEALTHCHECK /health uç noktasını yoklamıyor: {checks[0]}"


def test_dockerfile_exposes_scalper_port():
    exposed = " ".join(arg for op, arg in _dockerfile_instructions(_read(DOCKERFILE)) if op == "EXPOSE")
    assert str(SCALPER_PORT) in exposed, f"EXPOSE {SCALPER_PORT} yok (bulunan: {exposed!r})"


def test_dockerfile_cmd_is_single_worker_uvicorn():
    """TEK SÜREÇ kuralı.

    Bot tek asyncio sürecidir: state/*.json kilitleri, cooldown, sembol
    rezervasyonları ve pozisyon devralma SÜREÇ-GENELİ tekil durumdur.
    worker>1 iki bağımsız motor demektir.
    """
    cmds = [arg for op, arg in _dockerfile_instructions(_read(DOCKERFILE)) if op == "CMD"]
    assert cmds, "CMD yok"
    cmd = cmds[-1]
    assert "uvicorn" in cmd, f"CMD uvicorn çalıştırmıyor: {cmd}"
    assert "gunicorn" not in cmd, "gunicorn (çok süreçli) kullanılamaz"
    assert "--reload" not in cmd, "--reload süreç çoğaltır ve motoru yeniden kurar"
    workers = re.findall(r'--workers"?,?\s*"?(\d+)', cmd)
    assert workers == ["1"], f"tam olarak --workers 1 bekleniyordu, bulunan: {workers!r} ({cmd})"


# ── 3) docker-compose.yml sözleşmesi ────────────────────────────────────────


def test_compose_has_tradingbot_service():
    svc = _service("tradingbot")
    assert "build" in svc, "servis yerel Dockerfile'dan derlenmeli"


def test_compose_mounts_env_read_only_and_does_not_bake_it():
    """`.env` MOUNT edilir, görüntüye GÖMÜLMEZ (.dockerignore onu dışlar)."""
    mounts = _volume_pairs(_service("tradingbot"))
    env_mounts = [(h, c, m) for h, c, m in mounts if c == "/app/.env"]
    assert env_mounts, f"/app/.env mount edilmiyor (mount'lar: {mounts})"
    host, _container, mode = env_mounts[0]
    assert host.endswith(".env"), f"host tarafı bir .env dosyası olmalı: {host!r}"
    assert mode == "ro", f"/app/.env salt okunur (ro) mount edilmeli, bulunan: {mode!r}"

    patterns = _dockerignore_patterns()
    assert ".env" in patterns, ".dockerignore '.env' satırını içermeli (sır görüntüye girmesin)"


def test_compose_persists_all_stateful_paths():
    """Defter + state + log + yedekler container silinse de yaşamalı."""
    containers = {c for _h, c, _m in _volume_pairs(_service("tradingbot"))}
    for required in ("/app/state", "/app/logs", "/app/backups", "/app/data"):
        assert required in containers, f"kalıcı volume eksik: {required} (bulunan: {sorted(containers)})"


def test_compose_database_lives_inside_a_persistent_volume():
    """sqlite defteri (ve WAL kardeşleri) kalıcı bir DİZİN mount'unun içinde olmalı.

    Yalnız `.db` dosyasını mount etmek yetmez: journal_mode=WAL,
    `tradingbot.db-wal`/`-shm` dosyalarını AYNI dizinde üretir ve onlar
    container katmanında kalırsa checkpoint edilmemiş kayıtlar kaybolur.
    """
    svc = _service("tradingbot")
    db_url = (svc.get("environment") or {}).get("DATABASE_URL")
    assert db_url, "DATABASE_URL compose ortamında tanımlı değil"
    assert db_url.startswith("sqlite"), f"beklenmedik DATABASE_URL: {db_url!r}"
    # sqlite:///./data/tradingbot.db → /app/data/tradingbot.db (WORKDIR=/app)
    rel = db_url.split("://", 1)[1].lstrip("/")
    rel = rel[2:] if rel.startswith("./") else rel
    db_dir = "/app/" + str(Path(rel).parent).lstrip("./")
    containers = {c for _h, c, _m in _volume_pairs(svc)}
    assert db_dir in containers, (
        f"defterin dizini ({db_dir}) kalıcı volume DEĞİL — container silinince defter kaybolur"
    )


def test_compose_restart_policy_and_healthcheck():
    svc = _service("tradingbot")
    assert svc.get("restart") == "unless-stopped", f"restart politikası: {svc.get('restart')!r}"
    hc = svc.get("healthcheck")
    assert hc, "healthcheck yok"
    assert "/health" in json.dumps(hc), f"healthcheck /health yoklamıyor: {hc}"
    assert hc.get("start_period"), "start_period yok — açılış ~90 sn sürebilir"


def test_compose_log_rotation_is_bounded():
    opts = (_service("tradingbot").get("logging") or {})
    assert opts.get("driver") == "json-file", f"log sürücüsü: {opts.get('driver')!r}"
    options = opts.get("options") or {}
    assert options.get("max-size"), "max-size yok — container logu diski doldurabilir"
    assert options.get("max-file"), "max-file yok"


def test_compose_gives_graceful_shutdown_window():
    """SIGTERM → lifespan `finally` → bekleyen MAKER girişleri İPTAL edilir.

    Bunlar ağ çağrısıdır; süre yetmezse SIGKILL gelir ve iptal edilmemiş LIMIT
    emirleri borsada asılı kalır.
    """
    svc = _service("tradingbot")
    grace = str(svc.get("stop_grace_period") or "")
    assert grace, "stop_grace_period yok (docker varsayılanı 10 sn — kapanış yarıda kesilir)"
    seconds = float(re.sub(r"[a-zA-Z]", "", grace) or 0)
    if grace.rstrip().endswith("m"):
        seconds *= 60
    assert seconds >= 60, f"stop_grace_period çok kısa: {grace}"


def test_compose_runs_non_root():
    user = str(_service("tradingbot").get("user") or "")
    assert user, "compose 'user:' vermiyor"
    uid = user.split(":")[0]
    # `${TRADINGBOT_UID:-10001}` biçimindeki varsayılan da root OLMAMALI.
    m = re.search(r":-(\d+)", uid)
    effective = m.group(1) if m else uid
    assert effective not in ("0", "root"), f"container root olarak koşuyor: {user!r}"


def test_compose_publishes_scalper_port():
    ports = " ".join(str(p) for p in _service("tradingbot").get("ports", []))
    assert f":{SCALPER_PORT}" in ports, f"port {SCALPER_PORT} yayınlanmıyor: {ports!r}"


# ── 4) İkinci halka (follower) VARSAYILAN KAPALI ────────────────────────────


def test_follower_service_is_behind_a_disabled_profile():
    """`docker compose up` ikinci halkayı BAŞLATMAMALI (D20b tercih edilen)."""
    svc = _service("tradingbot-follower")
    profiles = svc.get("profiles") or []
    assert "follower" in profiles, f"follower profili yok: {profiles!r}"
    # Ana servis profilsiz olmalı, yoksa varsayılan `up` HİÇBİR ŞEY başlatmaz.
    assert not _service("tradingbot").get("profiles"), "ana servis bir profilin arkasında olmamalı"


def test_follower_service_shares_no_state_with_scalper():
    """İki halka AYNI defteri/state'i paylaşamaz — yoksa çift yönetim."""
    scalper_hosts = {h for h, _c, _m in _volume_pairs(_service("tradingbot"))}
    follower_hosts = {h for h, _c, _m in _volume_pairs(_service("tradingbot-follower"))}
    overlap = scalper_hosts & follower_hosts
    assert not overlap, f"iki halka aynı host yolunu paylaşıyor: {overlap}"


def test_follower_service_is_also_single_worker():
    cmd = " ".join(_service("tradingbot-follower").get("command") or [])
    assert cmd, "follower servisinin command'i yok"
    workers = re.findall(r"--workers\s+(\d+)", cmd)
    assert workers == ["1"], f"follower için --workers 1 bekleniyordu: {workers!r}"
    assert str(FOLLOWER_PORT) in cmd, f"follower portu {FOLLOWER_PORT} değil: {cmd}"


# ── 5) .dockerignore sözleşmesi ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern",
    [
        ".env", ".venv/", "logs/", "backups/", "*.db", ".git/", "state/", "data/",
        # archive/ eski dokümanlarda API-anahtarı BİÇİMİNDE dizeler taşır ve
        # görüntü `docker save` ile ihraç edilebilir.
        "archive/",
    ],
)
def test_dockerignore_excludes_secrets_and_live_data(pattern):
    patterns = _dockerignore_patterns()
    assert pattern in patterns, f".dockerignore '{pattern}' satırını içermeli"


def test_dockerignore_keeps_ci_workflow_for_in_container_pytest():
    """`.github/` dışlanır AMA `ci.yml` istisna tutulur.

    `test_dockerfile_python_version_matches_ci_and_server` bu dosyayı okur.
    İstisna olmadan container İÇİNDE `pytest tests` KIRMIZI döner — ölçüldü:
    `AssertionError: beklenen dosya yok: /app/.github/workflows/ci.yml`
    (`1 failed, 2012 passed`), yani "container içi test = deploy kapısı"
    iddiası çökerdi.
    """
    patterns = _dockerignore_patterns()
    assert "!.github/workflows/ci.yml" in patterns, (
        ".dockerignore '.github/' dışlıyor ama ci.yml istisnası YOK — "
        "container içi tam paket kırılır"
    )


@pytest.mark.parametrize(
    "keep",
    [
        "tests",          # container içinde pytest = deploy kapısı
        "conftest.py",    # test log yönlendirmesi
        "pytest.ini",
        "examples",       # tests/test_news_bot_client.py bunu import eder
        "scripts",
        "static",
        "src",
        "Dockerfile",         # bu test dosyası onu OKUR
        "docker-compose.yml",  # bu test dosyası onu OKUR
        ".dockerignore",       # bu test dosyası onu OKUR
        "env.example",
    ],
)
def test_dockerignore_keeps_files_needed_inside_the_image(keep):
    """Bu dosyalar görüntüde KALMALI, yoksa container İÇİNDE tam paket çöker.

    Dizinin TAMAMINI dışlayan kalıplar yasaktır; dizinin İÇİNDEKİ tek bir
    artefaktı dışlamak (ör. `scripts/.scalper_env_snapshot.txt`) serbesttir —
    o dosya çalışma zamanında gerekli değildir.
    """
    for pat in _dockerignore_patterns():
        if pat.startswith("!"):
            continue
        normalized = pat.rstrip("/")
        assert normalized != keep, f".dockerignore '{keep}' dosyasını/dizinini dışlıyor ('{pat}')"
        # Dizinin tamamını süpüren kalıplar: `keep/*`, `keep/**`
        assert normalized not in (f"{keep}/*", f"{keep}/**"), (
            f".dockerignore '{keep}' dizininin TAMAMINI dışlıyor ('{pat}')"
        )


def test_image_contains_the_full_test_suite_paths():
    """Görüntüye giren test varlıkları gerçekten var (fixture'lar dahil)."""
    assert (REPO_ROOT / "tests" / "fixtures").is_dir(), "tests/fixtures yok (golden backtest)"
    assert (REPO_ROOT / "conftest.py").exists()
    assert (REPO_ROOT / "examples").is_dir()


# ── 6) scripts/docker_run.sh güvenlik kapıları ──────────────────────────────


def test_docker_run_sh_syntax_and_executable():
    proc = subprocess.run(["bash", "-n", str(DOCKER_RUN_SH)], capture_output=True, text=True)
    assert proc.returncode == 0, f"docker_run.sh sözdizimi hatası:\n{proc.stderr}"
    assert DOCKER_RUN_SH.stat().st_mode & stat.S_IXUSR, "docker_run.sh çalıştırılabilir değil"


def test_docker_run_sh_has_entry_halt_gate():
    text = _read(DOCKER_RUN_SH)
    assert "state/scalper_entry_halt.json" in text, "entry-halt kapısı yok"
    assert "state/follower_entry_halt.json" in text, "gömülü takipçi entry-halt kapısı yok (D20b)"


def test_docker_run_sh_has_ban_window_gate():
    text = _read(DOCKER_RUN_SH)
    assert "HTTP 418" in text, "418 ban penceresi kapısı yok (CLAUDE.md kural 3)"
    assert "15 minutes ago" in text or "-v-15M" in text, "15 dk'lık ban penceresi hesabı yok"


def test_docker_run_sh_ban_window_uses_utc():
    """Ban penceresi UTC hesaplanmalı — container logları UTC yazar.

    `Dockerfile` `TZ=UTC` sabitler, yani `logs/bot.log` damgaları UTC'dir.
    Kesim noktası host'un YEREL saatiyle üretilseydi (restart_safe.sh'daki
    gibi) UTC+2 bir host'ta pencere "15 dk − 2 saat" olurdu ve AKTİF bir ban
    SESSİZCE görülmezdi. Ölçüldü: CEST host'ta taze bir `HTTP 418` satırı
    filtreden 0 satır geçti → kapı sessizce devre dışıydı.
    """
    text = _read(DOCKER_RUN_SH)
    ban_lines = [ln for ln in text.splitlines() if "BAN_SINCE=" in ln and "date" in ln]
    assert ban_lines, "BAN_SINCE hesabı bulunamadı"
    for line in ban_lines:
        assert "date -u" in line, (
            f"ban penceresi UTC değil (container logları UTC yazar): {line.strip()}"
        )


def test_docker_run_sh_engine_gate_is_not_fail_open():
    """supervisorctl yoksa/erişilemezse İKİNCİ bir sinyal olmalı.

    Tek başına `command -v supervisorctl || return 0` FAIL-OPEN'dır:
    supervisorctl PATH'te yoksa, sokete yetki yoksa, supervisord başka
    kullanıcıdaysa ya da motor systemd/nohup ile koşuyorsa kapı "TEMİZ" der.
    """
    text = _read(DOCKER_RUN_SH)
    assert "pgrep" in text, "ikinci motor sinyali (pgrep) yok — kapı fail-open"
    assert "src\\.main:app" in text or "src.main:app" in text, (
        "pgrep gerçek uvicorn süreç imzasını aramıyor"
    )


def test_docker_run_sh_refuses_to_run_in_live_ring_dirs():
    """Canlı halka dizinlerinde çalışmak `chown -R` ile canlı state'i ele geçirir."""
    text = _read(DOCKER_RUN_SH)
    for ring_dir in ("/opt/tradingbot-v2", "/opt/tradingbot-ap", "/opt/tradingbot-main"):
        assert ring_dir in text, f"canlı halka dizini kapısında {ring_dir} yok"


def test_docker_run_sh_makes_env_readable_by_container_uid():
    """`chmod 600` + root sahipliği = container uid okuyamaz = sonsuz çökme döngüsü."""
    text = _read(DOCKER_RUN_SH)
    assert "chmod 640 .env" in text, ".env izni container uid'si için ayarlanmıyor"


def test_docker_run_sh_probes_health_from_inside_container():
    """Sağlık host portundan yoklanırsa BAŞKA bir motor testi geçirebilir."""
    text = _read(DOCKER_RUN_SH)
    assert "probe_health" in text, "sağlık yoklaması fonksiyonu yok"
    assert "exec -T" in text, (
        "sağlık container'ın İÇİNDEN (docker exec) yoklanmıyor — host portunda "
        "duran başka bir motor yanlış 'TAMAM' verebilir"
    )


def test_docker_run_sh_down_covers_follower_profile():
    """Çıplak `down` follower container'ını 10 sn'lik varsayılanla öldürür."""
    text = _read(DOCKER_RUN_SH)
    down_block = text[text.index('if [ "$MODE" = "down" ]'):]
    down_block = down_block[: down_block.index("exit 0")]
    assert "--profile follower" in down_block, (
        "--down follower profilini kapsamıyor → stop_grace_period devre dışı kalır"
    )


def test_docker_run_sh_blocks_running_alongside_supervisord():
    text = _read(DOCKER_RUN_SH)
    assert "supervisorctl" in text, "supervisord yoklaması yok"
    assert "tradingbot_v2" in text, "supervisord programı adı yoklanmıyor"
    assert "DOCKER_ALLOW_ALONGSIDE" in text, "bilinçli geçiş anahtarı yok"


def test_docker_run_sh_redacts_secrets_in_log_dump():
    text = _read(DOCKER_RUN_SH)
    assert "redact" in text, "log dökümünde secret redaksiyonu yok"
    assert "SECRET" in text.upper(), "redaksiyon SECRET desenini kapsamıyor"


def test_docker_run_sh_waits_for_health():
    text = _read(DOCKER_RUN_SH)
    assert "/health" in text, "sağlık yoklaması yok"
    assert "HEALTH_TIMEOUT" in text, "sağlık zaman aşımı yok"


def test_docker_run_sh_redaction_actually_masks_secrets():
    """Redaksiyon fonksiyonunu GERÇEKTEN çalıştır — desen bozulursa yakalansın."""
    if shutil.which("sed") is None:  # pragma: no cover
        pytest.skip("sed yok")
    script = _read(DOCKER_RUN_SH)
    m = re.search(r"^redact\(\) \{\n(.*?)^\}", script, re.MULTILINE | re.DOTALL)
    assert m, "redact() fonksiyonu bulunamadı"
    body = m.group(1)
    # Düşmanca incelemenin maskelenmediğini ÖLÇTÜĞÜ sekiz sınıf + iki temel.
    cases = [
        "GET /tv-webhook?secret=LEAKA&x=1 HTTP/1.1",
        '{"secret": "LEAKB"}',
        'BINANCE_API_SECRET="LEAKC"',
        "secret='LEAKD'",
        "x-mbx-apikey: LEAKE",
        "POST /order?symbol=BTC&signature=LEAKF",
        "password=LEAKG",
        "Authorization: Bearer LEAKH",
        "BINANCE_API_KEY=LEAKI",
        "TELEGRAM_BOT_TOKEN=123456:LEAKJ",
    ]
    proc = subprocess.run(
        ["bash", "-c", f"redact() {{\n{body}}}\nredact"],
        input="\n".join(cases) + "\n", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    leaked = re.findall(r"LEAK[A-J]", proc.stdout)
    assert not leaked, f"redaksiyon şu sızıntıları kaçırdı: {leaked}\n{proc.stdout}"


# ── 7) Halkalar arası port paritesi ─────────────────────────────────────────


def test_container_ports_match_the_ring_table():
    """Container portları `scripts/restart_safe.sh` halka tablosuyla AYNI olmalı.

    D32: halka tablosu sağlık yoklamasını `/api/status` yerine `/health` ile yapar
    (force-fresh `/api/status` REST ağırlığını yiyordu); port paritesi aynen aranır.
    """
    restart_sh = _read(REPO_ROOT / "scripts" / "restart_safe.sh")
    assert f"127.0.0.1:{SCALPER_PORT}/health" in restart_sh, "testnet halkası portu değişmiş"
    assert f"127.0.0.1:{FOLLOWER_PORT}/health" in restart_sh, "follower halkası portu değişmiş"
    assert "/api/status" not in restart_sh, "sağlık yoklaması /health olmalı (D32), /api/status kaldı"


# ── 8) Opsiyonel duman testi (docker daemon GEREKİR; yoksa ATLANIR) ─────────


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=30
        ).returncode == 0
    except Exception:  # pragma: no cover
        return False


def _smoke_enabled() -> bool:
    """Duman testi YALNIZ açıkça istendiğinde koşar (`TRADINGBOT_DOCKER_SMOKE=1`).

    NEDEN OPT-IN (ölçülmüş gerekçeler, `test_testnet_integration.py`
    `RUN_TESTNET_INTEGRATION=1` kalıbının aynısı):
      * CLAUDE.md her değişiklikten ÖNCE tam paketi koşmayı ZORUNLU kılar.
        Duman testi ~715 MB'lık bir görüntü derler; soğuk önbellekte bu,
        zorunlu kapıya dakikalar ekler.
      * `server_deploy.sh` test adımını `timeout 300` ile sarar — sunucuda
        tetiklenen bir derleme o bütçeyi yiyip deploy'u GERİ ALDIRIR.
      * CI'da `.github/workflows/ci.yml` içindeki AYRI `docker-build` işi
        görüntüyü zaten derler (gha önbelleğiyle); tekrarı gereksizdir.
      * Bayrak sorgusu docker'dan ÖNCE gelir: kapalıyken `docker info`
        hiç çağrılmaz, yani docker'ı kurulu ama daemon'ı kapalı bir makinede
        her test koşusuna 30 sn'lik zaman aşımı EKLENMEZ.

    Elle koşmak için:  TRADINGBOT_DOCKER_SMOKE=1 python3 -m pytest \\
        tests/test_container.py::test_smoke_build_and_health -q
    """
    if os.environ.get("TRADINGBOT_DOCKER_SMOKE") != "1":
        return False
    return _docker_available()


def _docker(*args, timeout=120, check=True):
    proc = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if check:
        assert proc.returncode == 0, (
            f"docker {' '.join(args)} başarısız (rc={proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc


@pytest.mark.integration
@pytest.mark.skipif(
    not _smoke_enabled(),
    reason="TRADINGBOT_DOCKER_SMOKE=1 verilmedi (ya da docker daemon yok) — duman testi atlandı",
)
def test_smoke_build_and_health():  # pragma: no cover - ortama bağlı
    """Görüntü GERÇEKTEN derleniyor ve container `/health` cevaplıyor mu?

    ÖRNEK ayarlar (`env.example`) kullanılır: gerçek Binance anahtarı YOKTUR,
    bu yüzden `/health` **503 "degraded"** döner. ÖNEMLİ OLAN uygulamanın
    AYAĞA KALKIP HTTP cevaplamasıdır — bu tek başına import zincirini,
    bağımlılıkları, statik dosyaları, sqlite şemasını ve lifespan'i kanıtlar.

    TASARIM NOTU (ölçülmüş bir YANLIŞ-GEÇİŞ sonrası yazıldı):
      * **Host bind-mount KULLANILMAZ.** Uzak/VM tabanlı docker daemon'larında
        (colima, Docker Desktop, uzak DOCKER_HOST) host'un HER dizini VM'e
        paylaşılmaz; paylaşılmayan bir yol bind edilirse docker sessizce BOŞ
        BİR DİZİN yaratır — `.env` "dizin" olur ve container açılışta çöker.
        Bunun yerine `docker create` + `docker cp` + **isimli volume** kullanılır
        (isimli volume görüntüdeki dizin sahipliğini devralır → non-root `bot`
        kullanıcısı yazabilir; host izinleriyle uğraşmak gerekmez).
      * **Host portu YAYINLANMAZ.** Sağlık `docker exec ... curl` ile
        container'ın İÇİNDEN yoklanır. Yayınlanan bir port kullanılsaydı aynı
        portta duran BAŞKA bir container'ın cevabı testi yanlışlıkla
        geçirebilirdi — bu tam olarak BİR KEZ OLDU ve bu yüzden değiştirildi.

    Canlıya HİÇ dokunulmaz: ayrı görüntü etiketi, ayrı container adı, ayrı
    isimli volume'ler, host ağına açılan port YOK.
    """
    image = "tradingbot-smoketest:pytest"
    name = "tradingbot-smoketest-pytest"
    volumes = {
        sub: f"tradingbot-smoketest-pytest-{sub}"
        for sub in ("state", "logs", "backups", "data")
    }

    def cleanup():
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=120)
        for vol in volumes.values():
            subprocess.run(
                ["docker", "volume", "rm", "-f", vol], capture_output=True, timeout=60
            )

    cleanup()
    _docker("build", "-t", image, str(REPO_ROOT), timeout=1800)

    create_args = ["create", "--name", name]
    for sub, vol in volumes.items():
        create_args += ["-v", f"{vol}:/app/{sub}"]
    create_args += ["-e", "DATABASE_URL=sqlite:///./data/tradingbot.db", image]
    _docker(*create_args)

    try:
        # `.env` container'a KOPYALANIR (mount edilmez) — bkz. tasarım notu.
        _docker("cp", str(REPO_ROOT / "env.example"), f"{name}:/app/.env")
        _docker("start", name)

        status = None
        body = ""
        deadline = time.time() + 300
        while time.time() < deadline:
            state = _docker(
                "inspect", "-f", "{{.State.Status}}", name, check=False
            ).stdout.strip()
            if state != "running":
                break
            probe = _docker(
                "exec", name, "curl", "-sS", "-m", "5",
                "-w", "HTTP_CODE=%{http_code}",
                "http://127.0.0.1:9091/health",
                timeout=30, check=False,
            )
            found = re.search(r"HTTP_CODE=(\d+)", probe.stdout)
            if found and found.group(1) != "000":
                status = int(found.group(1))
                body = probe.stdout
                break
            time.sleep(5)

        logs = _docker("logs", "--tail", "60", name, check=False, timeout=60)
        diag = f"\n--- container logu ---\n{(logs.stdout + logs.stderr)[-4000:]}"
        assert status is not None, f"/health hiç cevap vermedi.{diag}"
        # 200 = sağlıklı (gerçek anahtar varsa), 503 = degraded ama AYAKTA.
        assert status in (200, 503), f"beklenmedik HTTP {status}: {body[:600]}{diag}"
        assert '"status"' in body, f"/health gövdesi beklenmedik: {body[:600]}{diag}"

        # Defter KALICI volume'ün içinde mi oluştu? (WAL kardeşleri gerekçesi)
        listing = _docker("exec", name, "ls", "/app/data", check=False, timeout=30)
        assert "tradingbot.db" in listing.stdout, (
            f"defter /app/data (kalıcı volume) içinde oluşmadı: {listing.stdout!r}{diag}"
        )

        # Zarif kapanış: SIGTERM, lifespan `finally` bloğunu çalıştırmalı.
        _docker("stop", "-t", "120", name, timeout=200)
        shutdown = _docker("logs", "--tail", "40", name, check=False, timeout=60)
        combined = shutdown.stdout + shutdown.stderr
        assert (
            "Uygulama kapatıldı" in combined
            or "Application shutdown complete" in combined
        ), f"SIGTERM'de zarif kapanış izi yok:\n{combined[-2000:]}"
    finally:
        cleanup()
