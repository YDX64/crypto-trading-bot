"""Buluttan deploy workflow'u sözleşme testleri — .github/workflows/deploy.yml (D39).

NEDEN: kullanıcı geliştirmeyi Claude bulut oturumlarına taşıdı (bulutta SSH/.env yok);
deploy ise yalnız Mac'ten `scripts/deploy.sh awa` ile yapılabiliyordu. D39 buna
GitHub Actions `workflow_dispatch` yolu ekler. Bu yol mevcut güvenlik kapılarını
(entry-halt, 418 ban, BOT_MODE↔ring, sunucuda tam test, otomatik geri alma) ATLAMAMALI
ve mainnet'e dokunamamalı. Workflow dosyasındaki tek bir yanlış satır (otomatik
tetikleyici, mainnet seçeneği, `DEPLOY_SKIP_TESTS=1`, `run:` içine gömülü `${{ inputs.* }}`)
sessizce kapı atlatır — gerçek GitHub koşusu ise ancak secret'lar eklenince denenebilir.
Bu yüzden sözleşme burada, statik + kabuk davranışı olarak kilitlenir.

NEDEN STATİK + KABUK DAVRANIŞI: GitHub'a, sunucuya, ağa DOKUNULMAZ. YAML PyYAML ile
okunur (`uvicorn[standard]` bağımlılığı — requirements.txt'te daima kurulu; tests/
test_container.py de aynısını yapar, bu yüzden `importorskip` YOK ve CI'da atlanmaz).
Adımların `run:` metinleri GERÇEK `bash` ile, sahte `ssh`/`gh`/`deploy.sh` ikilileri ve
geçici git depolarıyla çalıştırılır.

NOT: PyYAML (YAML 1.1) `on:` anahtarını Python `True` olarak okur — `_triggers()` ikisini
de ele alır.

NE KİLİTLENİR (hepsi bir arıza sınıfına karşılık gelir):
  * yalnız `workflow_dispatch`      → kazara otomatik push-deploy (entry-halt/ban kararı insanındır)
  * ring ∈ {testnet, follower}      → mainnet seçeneği YOK + guard açıkça reddeder
  * `${{ }}` run: içinde YOK        → script injection (girdiler yalnız step `env:` üzerinden)
  * secret'lar yalnız SSH adımında  → en az ayrıcalık; `StrictHostKeyChecking no` / ssh-keyscan YOK
  * `DEPLOY_SKIP_TESTS=0`, input yok → sunucuda tam test kapısı atlanamaz; deploy.sh AYNEN çağrılır
  * yalnız main + hedef regex'i     → dal/ref ve komut enjeksiyonu; SHA main geçmişinde olmalı
  * CI kapısı (check-runs `test`)   → kırmızı/koşmamış commit deploy edilmez
  * anahtar temizliği `always()`    → özel anahtar runner'da kalmaz
  * doğrulama (HEAD/RUNNING/etimes) → "çalışıyor" demeden önce kanıt (CLAUDE.md #6); program/dizin
                                       eşlemesi scripts/server_deploy.sh ile AYNI (yanlış motor yok)
  * concurrency + environment       → tek deploy; required reviewer takılabilir
  * .dockerignore istisnası         → container İÇİ `pytest tests` kırılmasın
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml  # uvicorn[standard] bağımlılığı (requirements.txt) — daima kurulu

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SERVER_DEPLOY_SH = REPO_ROOT / "scripts" / "server_deploy.sh"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DECISIONS_MD = REPO_ROOT / "docs" / "DECISIONS.md"
RUNBOOK_MD = REPO_ROOT / "docs" / "RUNBOOK.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
VAULT_NOTE = REPO_ROOT / "docs" / "vault" / "40-isletme" / "deploy-ve-geri-alma.md"

SECRETS = [
    "AWA_SSH_HOST",
    "AWA_SSH_USER",
    "AWA_SSH_PORT",
    "AWA_SSH_PRIVATE_KEY",
    "AWA_SSH_KNOWN_HOSTS",
]
# Sentinel: hiçbir çıktıda GÖRÜNMEMESİ gereken sahte secret değeri.
SENTINEL = "SENTINEL-SECRET-DEGERI-9f3a"


# ── Yardımcılar ─────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    assert path.exists(), f"beklenen dosya yok: {path}"
    return path.read_text(encoding="utf-8")


def _workflow() -> dict:
    return yaml.safe_load(_read(DEPLOY_WORKFLOW))


def _triggers(wf: dict) -> dict:
    """PyYAML `on:` anahtarını `True` olarak okur; YAML 1.2 ayrıştırıcılar `"on"` bırakır."""
    if "on" in wf:
        return wf["on"]
    assert True in wf, "workflow'da tetikleyici (`on:`) bölümü yok"
    return wf[True]


def _steps(wf: dict | None = None) -> list[dict]:
    wf = wf or _workflow()
    return wf["jobs"]["deploy"]["steps"]


def _step(name_prefix: str) -> dict:
    matches = [s for s in _steps() if str(s.get("name", "")).startswith(name_prefix)]
    assert len(matches) == 1, f"'{name_prefix}' ile başlayan adım sayısı {len(matches)} (1 bekleniyordu)"
    return matches[0]


def _run_blocks() -> list[tuple[str, str]]:
    return [(str(s.get("name", "?")), s["run"]) for s in _steps() if "run" in s]


def _code_lines(text: str) -> str:
    """Yorum satırları (`#...`) atılmış metin — yorumdaki açıklamalar yasak-kalıp aramasını bozmasın."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _step_index(step_name_prefix: str) -> int:
    for i, s in enumerate(_steps()):
        if str(s.get("name", "")).startswith(step_name_prefix):
            return i
    raise AssertionError(f"adım yok: {step_name_prefix}")


# ── 1) Dosya + tetikleyici ──────────────────────────────────────────────────


def test_workflow_exists_and_parses():
    wf = _workflow()
    assert wf["name"] == "Deploy (awa TESTNET)"
    assert "deploy" in wf["jobs"]


def test_only_workflow_dispatch_trigger():
    triggers = _triggers(_workflow())
    assert set(triggers) == {"workflow_dispatch"}, (
        f"YALNIZ workflow_dispatch olmalı (otomatik deploy bilinçli YOK): {sorted(map(str, triggers))}"
    )
    raw = _read(DEPLOY_WORKFLOW)
    for forbidden in ("pull_request_target", "pull_request:", "push:", "schedule:", "workflow_run", "repository_dispatch"):
        assert forbidden not in _code_lines(raw), f"yasak tetikleyici: {forbidden}"


def test_ring_input_is_testnet_follower_only():
    ring = _triggers(_workflow())["workflow_dispatch"]["inputs"]["ring"]
    assert ring["type"] == "choice"
    assert ring["options"] == ["testnet", "follower"], f"ring seçenekleri tam {{testnet, follower}} olmalı: {ring['options']}"
    assert ring["default"] == "testnet"
    assert "mainnet" not in [str(o).lower() for o in ring["options"]]


def test_other_inputs_and_no_skip_tests_input():
    inputs = _triggers(_workflow())["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"ring", "target", "no_restart"}, (
        f"beklenmeyen input: {set(inputs) ^ {'ring', 'target', 'no_restart'}} "
        "(DEPLOY_SKIP_TESTS asla input olarak sunulmaz)"
    )
    assert inputs["target"]["type"] == "string"
    assert inputs["target"]["default"] == "origin/main"
    assert inputs["no_restart"]["type"] == "boolean"
    assert inputs["no_restart"]["default"] is False


# ── 2) İzinler / eşzamanlılık / ortam ───────────────────────────────────────


def test_permissions_least_privilege():
    assert _workflow()["permissions"] == {"contents": "read", "checks": "read"}


def test_concurrency_serializes_without_cancelling():
    conc = _workflow()["concurrency"]
    assert conc["group"] == "deploy-awa"
    assert conc["cancel-in-progress"] is False, "çalışan deploy iptal EDİLMEZ (ssh ortada kopar)"


def test_environment_runner_and_timeout():
    job = _workflow()["jobs"]["deploy"]
    assert job["environment"] == "awa-testnet"
    assert job["runs-on"] == "ubuntu-latest"
    assert 10 <= int(job["timeout-minutes"]) <= 30


# ── 3) Script injection / secret sızıntısı ──────────────────────────────────


def test_no_expression_inside_any_run_block():
    """Girdiler/secret'lar `run:` metnine GÖMÜLMEZ; yalnız step `env:` üzerinden geçer."""
    for name, run in _run_blocks():
        assert "${{" not in run, f"'{name}' adımının run: metninde `${{{{ }}}}` ifadesi var (script injection)"


def test_inputs_reach_shell_only_through_step_env():
    """env değeri TEK bir `${{ inputs.x }}` / `${{ secrets.X }}` ifadesidir (birleştirme/şablon yok)."""
    seen = 0
    for step in _steps():
        for key, value in (step.get("env") or {}).items():
            if "inputs." in str(value) or "secrets." in str(value):
                seen += 1
                assert re.fullmatch(r"\$\{\{ (inputs|secrets)\.[A-Za-z_]+ \}\}", str(value).strip()), (
                    f"env {key}: yalnız tek ifade beklenir, bulunan: {value!r}"
                )
    assert seen >= 8, "girdiler/secret'lar env üzerinden geçmiyor gibi görünüyor"


def test_secrets_only_in_ssh_setup_step_and_all_declared():
    ssh_step = _step("SSH kurulumu")
    declared = set(re.findall(r"secrets\.([A-Z_]+)", yaml.safe_dump(ssh_step.get("env") or {})))
    assert declared == set(SECRETS), f"SSH adımı secret kümesi: {declared} (beklenen {set(SECRETS)})"
    for step in _steps():
        if step.get("name") == ssh_step["name"]:
            continue
        assert "secrets." not in yaml.safe_dump(step), (
            f"'{step.get('name')}' adımı secret'a erişiyor — yalnız SSH kurulum adımı erişir (en az ayrıcalık)"
        )


def test_no_secret_is_ever_echoed_or_traced():
    run = _step("SSH kurulumu")["run"]
    assert "set -x" not in run and "xtrace" not in run
    for secret in ("AWA_SSH_PRIVATE_KEY", "AWA_SSH_KNOWN_HOSTS"):
        for line in run.splitlines():
            if re.search(rf"\$\{{?{secret}\b", line) and re.match(r"\s*(echo|printf)\b", line):
                assert re.search(r'>\s*"\$HOME/\.ssh/', line), (
                    f"{secret} ekrana basılıyor olabilir (yalnız dosyaya yazılabilir): {line.strip()}"
                )
    # Hata mesajları secret DEĞİŞKENİNİ enterpole etmez; yalnız adını düz metin söyler.
    for line in run.splitlines():
        if re.match(r"\s*(\|\| )?fail\s+\"", line) or line.lstrip().startswith("|| fail"):
            assert not re.search(r"\$\{?AWA_SSH_", line), f"fail mesajı secret değeri taşıyor: {line.strip()}"


# ── 4) SSH sertleştirmesi ───────────────────────────────────────────────────


def test_ssh_is_strict_no_tofu():
    code = _code_lines(_read(DEPLOY_WORKFLOW))
    assert not re.search(r"StrictHostKeyChecking[ =]+(no|accept-new|off)", code, re.I), "StrictHostKeyChecking gevşetilemez"
    assert "ssh-keyscan" not in code, "ssh-keyscan/TOFU yok: host anahtarı secret'tan gelir"
    assert "UserKnownHostsFile /dev/null" not in code
    run = _step("SSH kurulumu")["run"]
    assert "StrictHostKeyChecking yes" in run
    assert "BatchMode yes" in run
    assert "IdentitiesOnly yes" in run
    assert "Host awa" in run, "deploy.sh `awa` takma adını kullanır"
    assert "id_deploy" in run and "chmod 600" in run


def test_no_file_copy_deploy_paths():
    code = _code_lines(_read(DEPLOY_WORKFLOW))
    for forbidden in (r"\bscp\b", r"\brsync\b", r"supervisorctl\s+restart", r"git\s+push"):
        assert not re.search(forbidden, code), f"deploy yalnız scripts/deploy.sh üzerinden olmalı: {forbidden}"


# ── 5) deploy.sh AYNEN çağrılır; testler atlanamaz ──────────────────────────


def test_deploy_calls_existing_script_with_tests_on():
    raw = _read(DEPLOY_WORKFLOW)
    assert "DEPLOY_SKIP_TESTS=1" not in raw
    assert not re.search(r"DEPLOY_SKIP_TESTS\s*:\s*[\"']?1", raw)
    run = _step("Deploy (scripts/deploy.sh")["run"]
    assert "DEPLOY_SKIP_TESTS=0 bash scripts/deploy.sh awa" in run
    assert '--ring "$RING"' in run
    assert "DEPLOY_CONFIRM" not in raw, "mainnet onay atlatma bayrağı bu workflow'da YOK"


# ── 6) Guard / hedef çözümü / CI kapısı ─────────────────────────────────────


def test_guard_is_first_and_has_main_and_target_regex():
    steps = _steps()
    assert str(steps[0]["name"]).startswith("Guard"), "guard ilk adım olmalı (hiçbir şeye dokunmadan reddet)"
    run = steps[0]["run"]
    assert "refs/heads/main" in run
    assert "^(origin/main|[0-9a-f]{7,40})$" in run
    assert "mainnet" in run, "mainnet açıkça reddedilmeli (mesajıyla)"
    assert "--ring mainnet" in run, "mesaj yerel yolu göstermeli"


def test_checkout_full_history():
    checkout = next(s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"]["fetch-depth"] == 0


def test_resolve_checks_ancestry_of_main():
    run = _step("Hedef SHA")["run"]
    assert "git rev-parse HEAD" in run
    assert 'git rev-parse --verify "$TARGET^{commit}"' in run
    assert 'git merge-base --is-ancestor "$SHA" origin/main' in run
    assert "GITHUB_OUTPUT" in run


def test_ci_gate_uses_check_runs_of_ci_test_job():
    step = _step("CI kapısı")
    run = step["run"]
    assert "check-runs" in run and "check_name=test" in run
    assert "conclusion" in run and "success" in run
    assert (step.get("env") or {}).get("GH_TOKEN") == "${{ github.token }}"
    ci = yaml.safe_load(_read(CI_WORKFLOW))
    assert "test" in ci["jobs"], "ci.yml'de `test` işi yok — CI kapısı hiçbir zaman yeşil olmaz"
    assert ci["jobs"]["test"].get("name", "test") == "test", "iş adı değişirse check-run adı da değişir"
    ci_on = ci.get("on", ci.get(True))
    assert ci_on["push"]["branches"] == ["main"], "main'e push'ta CI koşmazsa check-run bulunamaz"


def test_step_order_gates_before_ssh_and_deploy():
    guard = _step_index("Guard")
    resolve = _step_index("Hedef SHA")
    ci_gate = _step_index("CI kapısı")
    ssh = _step_index("SSH kurulumu")
    deploy = _step_index("Deploy (scripts/deploy.sh")
    verify = _step_index("Doğrulama")
    cleanup = _step_index("Anahtar temizliği")
    assert guard < resolve < ci_gate < ssh < deploy < verify < cleanup


def test_key_cleanup_runs_always_after_deploy():
    step = _step("Anahtar temizliği")
    assert step.get("if") == "always()"
    assert re.search(r"rm -f .*id_deploy", step["run"])
    assert _step_index("Anahtar temizliği") > _step_index("Doğrulama")
    summary = _step("Özet")
    assert summary.get("if") == "always()"


# ── 7) Doğrulama adımı: halka → program/dizin eşlemesi server_deploy.sh ile AYNI ──


def _server_ring_table() -> dict[str, tuple[str, str]]:
    text = _read(SERVER_DEPLOY_SH)
    table = {}
    for ring in ("testnet", "follower", "mainnet"):
        m = re.search(
            rf'{ring}\)\s*\n\s*RING_REPO_DIR="([^"]+)";\s*RING_PROGRAM="([^"]+)"',
            text,
        )
        assert m, f"server_deploy.sh'de {ring} halka tablosu okunamadı"
        table[ring] = (m.group(1), m.group(2))
    return table


def test_verify_step_ring_mapping_matches_server_deploy():
    table = _server_ring_table()
    run = _step("Doğrulama")["run"]
    for ring in ("testnet", "follower"):
        repo_dir, program = table[ring]
        assert re.search(rf'{ring}\)\s+PROGRAM="{program}";\s+REPO_DIR="{re.escape(repo_dir)}"', run), (
            f"doğrulama adımındaki {ring} eşlemesi server_deploy.sh ile ({repo_dir}, {program}) AYNI olmalı"
        )
    mainnet_dir, mainnet_prog = table["mainnet"]
    code = _code_lines(_read(DEPLOY_WORKFLOW))
    assert mainnet_dir not in code and mainnet_prog not in code, "mainnet dizini/programı workflow'a girmez"


def test_verify_step_checks_head_running_and_etimes():
    run = _step("Doğrulama")["run"]
    assert "rev-parse HEAD" in run
    assert "supervisorctl status" in run and "RUNNING" in run
    assert "ps -o etimes=" in run and "supervisorctl pid" in run


# ── 8) Container içi pytest + dokümantasyon işaretçileri ────────────────────


def test_dockerignore_keeps_deploy_workflow_for_in_container_pytest():
    patterns = [ln.strip() for ln in _read(DOCKERIGNORE).splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert "!.github/workflows/deploy.yml" in patterns, (
        ".dockerignore '.github/' dışlıyor ama deploy.yml istisnası YOK — container içi tam paket kırılır"
    )


def test_docs_point_to_d39():
    decisions = _read(DECISIONS_MD)
    assert "### D39 — Buluttan elle tetiklenen deploy" in decisions
    assert decisions.index("### D39") < decisions.index("### D38"), "D39, D38'in ÜSTÜNDE olmalı (en yeni en üstte)"
    assert "### Buluttan deploy (GitHub Actions)" in _read(RUNBOOK_MD)
    claude = _read(CLAUDE_MD)
    assert "Deploy (awa TESTNET)" in claude and "D39" in claude
    assert "D39" in _read(VAULT_NOTE)


# ── 9) Gömülü kabuk betiklerinin sözdizimi ──────────────────────────────────


@pytest.mark.parametrize(
    "name,run",
    [pytest.param(n, r, id=f"adim{i}") for i, (n, r) in enumerate(_run_blocks())],
)
def test_run_blocks_bash_syntax(name, run):
    proc = subprocess.run(["bash", "-n"], input=run, capture_output=True, text=True)
    assert proc.returncode == 0, f"'{name}' bash sözdizimi hatası:\n{proc.stderr}"


# ═══════════════════════════════════════════════════════════════════════════
# KABUK DAVRANIŞI — adımların gerçek `run:` metinleri sahte ikililerle koşturulur
# ═══════════════════════════════════════════════════════════════════════════


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _fake_bin(tmp_path: Path) -> Path:
    """Sahte `ssh` ve `gh` (idempotent). Tüm çağrılar $FAKE_LOG'a satır satır yazılır."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_exec(
        bin_dir / "ssh",
        """#!/usr/bin/env bash
printf 'ssh %s\\n' "$*" >> "${FAKE_LOG:-/dev/null}"
case "$*" in
  "awa true") exit "${FAKE_SSH_RC:-0}" ;;
  *"rev-parse HEAD"*) printf '%s\\n' "${FAKE_SERVER_SHA:-}" ;;
  *"ps -o etimes="*) printf '  %s\\n' "${FAKE_ETIMES:-}" ;;
  *"supervisorctl status"*) printf '%s\\n' "${FAKE_STATUS:-}" ;;
esac
exit 0
""",
    )
    _write_exec(
        bin_dir / "gh",
        """#!/usr/bin/env bash
printf 'gh %s\\n' "$*" >> "${FAKE_LOG:-/dev/null}"
if [ "${FAKE_GH_RC:-0}" != "0" ]; then echo "gh: hata" >&2; exit "$FAKE_GH_RC"; fi
printf '%s' "${FAKE_GH_OUT:-}"
""",
    )
    return bin_dir


def _run_step(step: dict, tmp_path: Path, env: dict, cwd: Path | None = None, with_fake_bin: bool = True):
    """Adımın `run:` metnini GitHub'ın kabuğuyla (`bash --noprofile --norc -eo pipefail`)
    TEMİZ bir ortamda koşturur — CI'ın kendi GITHUB_* değişkenleri sızmaz."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    path = os.environ.get("PATH", "/usr/bin:/bin")
    if with_fake_bin:
        path = f"{_fake_bin(tmp_path)}:{path}"
    full_env = {"PATH": path, "HOME": str(home), "LC_ALL": "C.UTF-8", "FAKE_LOG": str(tmp_path / "calls.log")}
    full_env.update(env)
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=str(cwd or tmp_path),
        env=full_env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _calls(tmp_path: Path) -> str:
    log = tmp_path / "calls.log"
    return log.read_text() if log.exists() else ""


# ── Guard ───────────────────────────────────────────────────────────────────


def _guard(tmp_path, **env):
    base = {"GITHUB_REF": "refs/heads/main", "RING": "testnet", "TARGET": "origin/main"}
    base.update(env)
    return _run_step(_steps()[0], tmp_path, base, with_fake_bin=False)


@pytest.mark.parametrize(
    "ring,target",
    [
        ("testnet", "origin/main"),
        ("follower", "origin/main"),
        ("testnet", "abc1234"),
        ("follower", "0123456789abcdef0123456789abcdef01234567"),
    ],
)
def test_guard_accepts_valid(tmp_path, ring, target):
    proc = _guard(tmp_path, RING=ring, TARGET=target)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_guard_rejects_non_main_ref(tmp_path):
    proc = _guard(tmp_path, GITHUB_REF="refs/heads/feature-x")
    assert proc.returncode != 0
    assert "yalnız main" in proc.stdout


def test_guard_rejects_mainnet_with_local_path_hint(tmp_path):
    proc = _guard(tmp_path, RING="mainnet")
    assert proc.returncode != 0
    assert "mainnet" in proc.stdout and "--ring mainnet" in proc.stdout and "MAINNET" in proc.stdout


@pytest.mark.parametrize("ring", ["", "prod", "TESTNET", "testnet;id", "main net"])
def test_guard_rejects_unknown_ring(tmp_path, ring):
    assert _guard(tmp_path, RING=ring).returncode != 0


@pytest.mark.parametrize(
    "target",
    [
        "",
        "main",
        "origin/dev",
        "HEAD",
        "ABC1234",  # büyük harf
        "abc123",  # 6 hane
        "a" * 41,  # 41 hane
        "abc1234; rm -rf /",
        "$(id)",
        "abc1234\nrm",
        "origin/main\nextra",
        "--upload-pack=x",
        "refs/heads/feature",
    ],
)
def test_guard_rejects_bad_target(tmp_path, target):
    proc = _guard(tmp_path, TARGET=target)
    assert proc.returncode != 0, f"kabul edilmemeliydi: {target!r}"


def test_guard_never_echoes_raw_hostile_target(tmp_path):
    """Reddedilen değer ham basılırsa satır başı `::` workflow komutu enjekte edebilir."""
    proc = _guard(tmp_path, TARGET="x\n::add-mask::oops\n::set-output name=a::b")
    assert proc.returncode != 0
    for line in (proc.stdout + proc.stderr).splitlines():
        assert not line.startswith("::add-mask") and not line.startswith("::set-output"), line


# ── Hedef çözümü (gerçek git, geçici depolar) ───────────────────────────────

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git yok")


def _git_env(tmp_path: Path) -> dict:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }


def _git(tmp_path: Path, cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), env=_git_env(tmp_path), capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)} başarısız: {proc.stderr}"
    return proc.stdout.strip()


def _commit(tmp_path: Path, repo: Path, name: str) -> str:
    (repo / f"{name}.txt").write_text(name)
    _git(tmp_path, repo, "add", "-A")
    _git(tmp_path, repo, "commit", "-q", "-m", name)
    return _git(tmp_path, repo, "rev-parse", "HEAD")


def _make_repos(tmp_path: Path) -> dict:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(tmp_path, origin, "init", "-q", "--bare")
    _git(tmp_path, origin, "symbolic-ref", "HEAD", "refs/heads/main")
    work = tmp_path / "work"
    _git(tmp_path, tmp_path, "clone", "-q", str(origin), str(work))
    _git(tmp_path, work, "symbolic-ref", "HEAD", "refs/heads/main")
    a = _commit(tmp_path, work, "a")
    b = _commit(tmp_path, work, "b")
    _git(tmp_path, work, "push", "-q", "-u", "origin", "main")
    _git(tmp_path, work, "checkout", "-q", "-b", "feat")
    c = _commit(tmp_path, work, "c")
    _git(tmp_path, work, "push", "-q", "origin", "feat")
    _git(tmp_path, work, "checkout", "-q", "main")
    return {"origin": origin, "work": work, "a": a, "b": b, "c": c}


def _resolve(tmp_path: Path, repos: dict, target: str):
    out = tmp_path / "gh_output"
    out.write_text("")
    env = {"TARGET": target, "GITHUB_OUTPUT": str(out)}
    proc = _run_step(_step("Hedef SHA"), tmp_path, env, cwd=repos["work"], with_fake_bin=False)
    outputs = dict(ln.split("=", 1) for ln in out.read_text().splitlines() if "=" in ln)
    return proc, outputs


@needs_git
def test_resolve_origin_main_uses_dispatched_sha(tmp_path):
    repos = _make_repos(tmp_path)
    proc, outputs = _resolve(tmp_path, repos, "origin/main")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs == {"sha": repos["b"], "deploy_ref": "origin/main"}


@needs_git
def test_resolve_short_sha_becomes_full_sha_for_rollback(tmp_path):
    repos = _make_repos(tmp_path)
    proc, outputs = _resolve(tmp_path, repos, repos["a"][:7])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs == {"sha": repos["a"], "deploy_ref": repos["a"]}, "sunucuya KISA değil TAM SHA gider"


@needs_git
def test_resolve_rejects_commit_outside_main_history(tmp_path):
    repos = _make_repos(tmp_path)
    proc, outputs = _resolve(tmp_path, repos, repos["c"])
    assert proc.returncode != 0
    assert "main geçmişinde DEĞİL" in proc.stdout
    assert outputs == {}


@needs_git
def test_resolve_rejects_unknown_sha(tmp_path):
    repos = _make_repos(tmp_path)
    proc, outputs = _resolve(tmp_path, repos, "deadbee")
    assert proc.returncode != 0 and outputs == {}


@needs_git
def test_resolve_stops_when_main_advanced_after_dispatch(tmp_path):
    repos = _make_repos(tmp_path)
    other = tmp_path / "other"
    _git(tmp_path, tmp_path, "clone", "-q", str(repos["origin"]), str(other))
    _commit(tmp_path, other, "d")
    _git(tmp_path, other, "push", "-q", "origin", "main")
    proc, outputs = _resolve(tmp_path, repos, "origin/main")
    assert proc.returncode != 0
    assert "ilerledi" in proc.stdout
    assert outputs == {}


# ── CI kapısı (sahte gh) ────────────────────────────────────────────────────

SHA40 = "0123456789abcdef0123456789abcdef01234567"


def _ci_gate(tmp_path, out="", rc="0"):
    env = {
        "GH_TOKEN": "x",
        "SHA": SHA40,
        "GITHUB_REPOSITORY": "owner/repo",
        "FAKE_GH_OUT": out,
        "FAKE_GH_RC": rc,
    }
    _fake_bin(tmp_path)
    return _run_step(_step("CI kapısı"), tmp_path, env)


def test_ci_gate_passes_on_success_and_queries_right_endpoint(tmp_path):
    proc = _ci_gate(tmp_path, out="completed/success")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"repos/owner/repo/commits/{SHA40}/check-runs?check_name=test" in _calls(tmp_path)


@pytest.mark.parametrize(
    "out",
    ["", "completed/failure", "in_progress/none", "completed/cancelled", "queued/none", "completed/skipped"],
)
def test_ci_gate_blocks_when_not_green(tmp_path, out):
    proc = _ci_gate(tmp_path, out=out)
    assert proc.returncode != 0, f"geçmemeliydi: {out!r}"
    assert "CI" in proc.stdout


def test_ci_gate_blocks_when_api_fails(tmp_path):
    proc = _ci_gate(tmp_path, rc="1")
    assert proc.returncode != 0


# ── SSH kurulumu ────────────────────────────────────────────────────────────


needs_keygen = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen yok")


def _keypair(tmp_path: Path) -> tuple[str, str]:
    key = tmp_path / "gen_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True, capture_output=True)
    pub = (tmp_path / "gen_key.pub").read_text().split()
    return key.read_text(), f"{pub[0]} {pub[1]}"


def _ssh_env(tmp_path: Path, port: str = "22", host: str = "awa.example.net", **over) -> dict:
    private, pub = _keypair(tmp_path)
    lookup = host if port in ("", "22") else f"[{host}]:{port}"
    env = {
        "AWA_SSH_HOST": host,
        "AWA_SSH_USER": "deploy",
        "AWA_SSH_PORT": port,
        "AWA_SSH_PRIVATE_KEY": private,
        "AWA_SSH_KNOWN_HOSTS": f"{lookup} {pub}",
    }
    env.update(over)
    return env


def test_ssh_setup_names_missing_secret_but_never_its_value(tmp_path):
    env = {
        "AWA_SSH_HOST": SENTINEL,
        "AWA_SSH_USER": "",
        "AWA_SSH_PORT": "",
        "AWA_SSH_PRIVATE_KEY": SENTINEL,
        "AWA_SSH_KNOWN_HOSTS": "",
    }
    proc = _run_step(_step("SSH kurulumu"), tmp_path, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "eksik GitHub secret: AWA_SSH_USER AWA_SSH_KNOWN_HOSTS" in out, "yalnız EKSİK olanların adı sayılmalı"
    assert SENTINEL not in out


@pytest.mark.parametrize(
    "over",
    [
        {"AWA_SSH_PORT": "abc"},
        {"AWA_SSH_PORT": "0"},
        {"AWA_SSH_PORT": "70000"},
        {"AWA_SSH_PORT": "22\nProxyCommand id"},
        {"AWA_SSH_HOST": "evil.example\n  ProxyCommand id"},
        {"AWA_SSH_HOST": "-oProxyCommand=id"},
        {"AWA_SSH_HOST": "a b"},
        {"AWA_SSH_USER": "root\n  ProxyCommand id"},
        {"AWA_SSH_USER": "a b"},
    ],
)
@needs_keygen
def test_ssh_setup_rejects_config_injection(tmp_path, over):
    proc = _run_step(_step("SSH kurulumu"), tmp_path, _ssh_env(tmp_path, **over))
    assert proc.returncode != 0, f"kabul edilmemeliydi: {over!r}"
    assert not (tmp_path / "home" / ".ssh" / "config").exists(), "doğrulama config yazılmadan ÖNCE reddetmeli"


@needs_keygen
def test_ssh_setup_rejects_garbage_private_key_without_leaking_it(tmp_path):
    proc = _run_step(_step("SSH kurulumu"), tmp_path, _ssh_env(tmp_path, AWA_SSH_PRIVATE_KEY=SENTINEL))
    assert proc.returncode != 0
    assert SENTINEL not in proc.stdout + proc.stderr


@needs_keygen
def test_ssh_setup_rejects_known_hosts_without_matching_host(tmp_path):
    env = _ssh_env(tmp_path)
    env["AWA_SSH_KNOWN_HOSTS"] = env["AWA_SSH_KNOWN_HOSTS"].replace("awa.example.net", "baska.example.net")
    proc = _run_step(_step("SSH kurulumu"), tmp_path, env)
    assert proc.returncode != 0
    assert "known_hosts" in proc.stdout.lower() or "KNOWN_HOSTS" in proc.stdout


@pytest.mark.parametrize("port", ["22", "2222"])
@needs_keygen
def test_ssh_setup_writes_strict_config_and_private_key_modes(tmp_path, port):
    proc = _run_step(_step("SSH kurulumu"), tmp_path, _ssh_env(tmp_path, port=port))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ssh_dir = tmp_path / "home" / ".ssh"
    config = (ssh_dir / "config").read_text()
    assert "Host awa" in config
    assert "HostName awa.example.net" in config and "User deploy" in config and f"Port {port}" in config
    assert "StrictHostKeyChecking yes" in config and "BatchMode yes" in config and "IdentitiesOnly yes" in config
    assert not re.search(r"StrictHostKeyChecking\s+(no|accept-new)", config)
    assert "ServerAliveInterval" in config
    assert (ssh_dir / "id_deploy").stat().st_mode & 0o077 == 0, "özel anahtar yalnız sahibince okunabilir olmalı"
    assert "ssh awa true" in _calls(tmp_path)
    assert "BEGIN OPENSSH PRIVATE KEY" not in proc.stdout + proc.stderr


@needs_keygen
def test_ssh_setup_default_port_is_22_when_secret_missing(tmp_path):
    env = _ssh_env(tmp_path, port="22")
    env["AWA_SSH_PORT"] = ""
    proc = _run_step(_step("SSH kurulumu"), tmp_path, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Port 22" in (tmp_path / "home" / ".ssh" / "config").read_text()


@needs_keygen
def test_ssh_setup_fails_clearly_when_preflight_connection_fails(tmp_path):
    env = _ssh_env(tmp_path)
    env["FAKE_SSH_RC"] = "255"
    proc = _run_step(_step("SSH kurulumu"), tmp_path, env)
    assert proc.returncode != 0
    assert "GitHub Actions IP" in proc.stdout


# ── Deploy adımı (sahte scripts/deploy.sh) ──────────────────────────────────


def _deploy(tmp_path, ring="testnet", no_restart="false", ref="origin/main", rc="0"):
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    _write_exec(
        scripts / "deploy.sh",
        """#!/usr/bin/env bash
{ echo "ARGS:$*"; echo "NO_RESTART=${DEPLOY_NO_RESTART-unset}"; echo "SKIP_TESTS=${DEPLOY_SKIP_TESTS-unset}"; } >> "$FAKE_LOG"
exit "${FAKE_DEPLOY_RC:-0}"
""",
    )
    out = tmp_path / "gh_output"
    out.write_text("")
    env = {
        "RING": ring,
        "NO_RESTART": no_restart,
        "DEPLOY_REF": ref,
        "GITHUB_OUTPUT": str(out),
        "FAKE_DEPLOY_RC": rc,
    }
    proc = _run_step(_step("Deploy (scripts/deploy.sh"), tmp_path, env, with_fake_bin=False)
    return proc, out.read_text()


@pytest.mark.parametrize("ring", ["testnet", "follower"])
def test_deploy_step_invokes_deploy_sh_with_tests_always_on(tmp_path, ring):
    proc, outputs = _deploy(tmp_path, ring=ring)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = _calls(tmp_path)
    assert f"ARGS:awa origin/main --ring {ring}" in calls
    assert "SKIP_TESTS=0" in calls and "NO_RESTART=0" in calls
    assert re.search(r"started=\d+", outputs)


def test_deploy_step_no_restart_flag_and_sha_target(tmp_path):
    proc, _ = _deploy(tmp_path, no_restart="true", ref=SHA40)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = _calls(tmp_path)
    assert f"ARGS:awa {SHA40} --ring testnet" in calls
    assert "NO_RESTART=1" in calls and "SKIP_TESTS=0" in calls


def test_deploy_step_propagates_failure(tmp_path):
    proc, _ = _deploy(tmp_path, rc="1")
    assert proc.returncode != 0


# ── Doğrulama adımı (sahte ssh) ─────────────────────────────────────────────

RUNNING = "{prog}                     RUNNING   pid 4242, uptime 0:01:30"


def _verify(tmp_path, ring="testnet", no_restart="false", server_sha=SHA40, status=None, etimes="90", elapsed=150):
    prog = {"testnet": "tradingbot_v2", "follower": "tradingbot_ap"}[ring]
    summary = tmp_path / "summary.md"
    summary.write_text("")
    env = {
        "RING": ring,
        "NO_RESTART": no_restart,
        "SHA": SHA40,
        "DEPLOY_STARTED": str(int(time.time()) - elapsed),
        "GITHUB_STEP_SUMMARY": str(summary),
        "FAKE_SERVER_SHA": server_sha,
        "FAKE_STATUS": RUNNING.format(prog=prog) if status is None else status,
        "FAKE_ETIMES": etimes,
    }
    _fake_bin(tmp_path)
    proc = _run_step(_step("Doğrulama"), tmp_path, env)
    return proc, summary.read_text()


@pytest.mark.parametrize(
    "ring,prog,repo_dir",
    [("testnet", "tradingbot_v2", "/opt/tradingbot-v2"), ("follower", "tradingbot_ap", "/opt/tradingbot-ap")],
)
def test_verify_passes_and_queries_right_program_and_dir(tmp_path, ring, prog, repo_dir):
    proc, summary = _verify(tmp_path, ring=ring)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = _calls(tmp_path)
    assert f"git -C {repo_dir} rev-parse HEAD" in calls
    assert f"supervisorctl status {prog}" in calls
    assert f"supervisorctl pid {prog}" in calls
    other = "tradingbot_ap" if prog == "tradingbot_v2" else "tradingbot_v2"
    assert other not in calls, "YANLIŞ halkanın programı sorgulanmamalı"
    assert "RUNNING" in summary


def test_verify_fails_when_server_head_differs(tmp_path):
    proc, _ = _verify(tmp_path, server_sha="f" * 40)
    assert proc.returncode != 0
    assert "AYNI DEĞİL" in proc.stdout


def test_verify_fails_when_head_unreadable(tmp_path):
    proc, _ = _verify(tmp_path, server_sha="")
    assert proc.returncode != 0


@pytest.mark.parametrize(
    "status",
    [
        "tradingbot_v2                    STOPPED   Oct 10 01:00 AM",
        "tradingbot_v2                    FATAL     Exited too quickly",
        "tradingbot_v2                    BACKOFF   Exited too quickly",
        "",
    ],
)
def test_verify_fails_when_not_running(tmp_path, status):
    proc, _ = _verify(tmp_path, status=status)
    assert proc.returncode != 0
    assert "RUNNING değil" in proc.stdout


def test_verify_fails_when_process_older_than_deploy(tmp_path):
    """Restart gerçekleşmediyse süreç yaşı deploy süresinden BÜYÜK olur."""
    proc, _ = _verify(tmp_path, etimes="5000", elapsed=120)
    assert proc.returncode != 0
    assert "deploy'dan ÖNCE" in proc.stdout


def test_verify_fails_on_non_numeric_etimes(tmp_path):
    proc, _ = _verify(tmp_path, etimes="abc")
    assert proc.returncode != 0


def test_verify_no_restart_checks_only_head(tmp_path):
    proc, _ = _verify(tmp_path, no_restart="true", status="", etimes="999999")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = _calls(tmp_path)
    assert "rev-parse HEAD" in calls
    assert "supervisorctl" not in calls, "no_restart iken süreç sorgulanmaz"


def test_verify_no_restart_still_fails_on_head_mismatch(tmp_path):
    proc, _ = _verify(tmp_path, no_restart="true", server_sha="e" * 40)
    assert proc.returncode != 0


# ── Temizlik + özet ─────────────────────────────────────────────────────────


def test_cleanup_removes_private_key_even_if_config_missing(tmp_path):
    ssh_dir = tmp_path / "home" / ".ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "id_deploy").write_text("PRIVATE")
    (ssh_dir / "config").write_text("Host awa")
    proc = _run_step(_step("Anahtar temizliği"), tmp_path, {}, with_fake_bin=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (ssh_dir / "id_deploy").exists()
    # ikinci koşu (dosya yok) da hata vermemeli: `rm -f`
    assert _run_step(_step("Anahtar temizliği"), tmp_path, {}, with_fake_bin=False).returncode == 0


def test_summary_has_context_but_no_secrets(tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("")
    env = {
        "RING": "follower",
        "TARGET": "abc1234\n::add-mask::x",
        "NO_RESTART": "false",
        "SHA": SHA40,
        "JOB_STATUS": "success",
        "ACTOR": "someone",
        "GITHUB_STEP_SUMMARY": str(summary),
        # Özet adımı bu değişkenleri HİÇ görmemeli; görse bile yazmamalı.
        "AWA_SSH_PRIVATE_KEY": SENTINEL,
    }
    proc = _run_step(_step("Özet"), tmp_path, env, with_fake_bin=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = summary.read_text()
    assert "follower" in text and SHA40 in text and "success" in text
    assert SENTINEL not in text
    assert "\n::add-mask::" not in text, "ham hedef metni özete girmemeli"
