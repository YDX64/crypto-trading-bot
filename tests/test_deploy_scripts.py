"""scripts/deploy.sh, scripts/server_deploy.sh, scripts/ring_env_diff.sh testleri.

Sözleşme (bkz. CLAUDE.md, docs/MAINNET_PLAN.md §1, §5): iki halka — testnet (bugünkü
davranış, DEĞİŞMEDEN) ve mainnet (yalnız `vX.Y.Z` etiketi, origin'de var olmalı, açık
onay). Gerçek sunucuya/ağa DOKUNULMAZ: `ssh` ve `git` sahte (fake) ikililerle PATH'e
enjekte edilir; `deploy.sh` bu sahte ikililer dışında hiçbir dış bağımlılığa ihtiyaç
duymaz (yalnız bash yerleşikleri + gerçek `grep`/`read`/`printf`/`echo`).
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
DEPLOY_SH = SCRIPTS / "deploy.sh"
SERVER_DEPLOY_SH = SCRIPTS / "server_deploy.sh"
RING_ENV_DIFF_SH = SCRIPTS / "ring_env_diff.sh"


# ── Sözdizimi (bash -n) ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "script", [DEPLOY_SH, SERVER_DEPLOY_SH, RING_ENV_DIFF_SH], ids=lambda p: p.name
)
def test_bash_syntax_ok(script):
    assert script.exists(), f"beklenen script yok: {script}"
    proc = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"{script.name} sözdizimi hatası:\n{proc.stderr}"


def test_scripts_are_executable():
    for script in (DEPLOY_SH, SERVER_DEPLOY_SH, RING_ENV_DIFF_SH):
        mode = script.stat().st_mode
        assert mode & stat.S_IXUSR, f"{script.name} çalıştırılabilir değil"


# ── deploy.sh fonksiyonel testleri (sahte ssh + sahte git) ──────────────────


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_fake_bin(tmp_path: Path) -> tuple[Path, Path]:
    """Sahte `git` ve `ssh` içeren bir bin dizini + ssh çağrı kaydı dosyası döner.

    Sahte git davranışı env değişkenleriyle yönlendirilir:
      FAKE_GIT_DIRTY=1              → `git status --porcelain` kirli ağaç bildirir
      FAKE_GIT_HEAD=<sha>           → `git rev-parse HEAD`
      FAKE_GIT_ORIGIN_MAIN=<sha>    → `git rev-parse origin/main` (varsayılan: HEAD ile aynı)
      FAKE_GIT_REMOTE_TAGS="v1 v2"  → `git ls-remote --exit-code --tags origin refs/tags/<X>`
                                       yalnız bu listedeki etiketler için başarılı olur
    Sahte ssh her çağrıyı $SSH_CALL_LOG dosyasına satır satır ekler ve 0 ile çıkar
    (gerçek bir bağlantı KURMAZ).
    """
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir()

    _write_executable(
        bin_dir / "git",
        r"""#!/usr/bin/env bash
sub="$1"; shift || true
case "$sub" in
  status)
    if [ "${FAKE_GIT_DIRTY:-0}" = "1" ]; then
      echo " M fake/dirty/file"
    fi
    exit 0
    ;;
  fetch)
    exit 0
    ;;
  rev-parse)
    ref="$1"
    if [ "$ref" = "origin/main" ]; then
      echo "${FAKE_GIT_ORIGIN_MAIN:-${FAKE_GIT_HEAD:-headsha}}"
    else
      echo "${FAKE_GIT_HEAD:-headsha}"
    fi
    exit 0
    ;;
  ls-remote)
    found=""
    for a in "$@"; do
      case "$a" in
        refs/tags/*) found="${a#refs/tags/}" ;;
      esac
    done
    for t in ${FAKE_GIT_REMOTE_TAGS:-}; do
      if [ "$t" = "$found" ]; then exit 0; fi
    done
    exit 2
    ;;
  *)
    exit 0
    ;;
esac
""",
    )

    _write_executable(
        bin_dir / "ssh",
        r"""#!/usr/bin/env bash
: "${SSH_CALL_LOG:?SSH_CALL_LOG not set}"
{
  echo "=== ssh call ==="
  for a in "$@"; do printf '%s\n' "$a"; done
} >> "$SSH_CALL_LOG"
exit 0
""",
    )

    ssh_log = tmp_path / "ssh_calls.log"
    ssh_log.write_text("")
    return bin_dir, ssh_log


def _run_deploy(
    tmp_path: Path,
    args: list[str],
    *,
    env_extra: dict | None = None,
    stdin_text: str = "",
    fake_git_dirty: bool = False,
    fake_git_head: str = "headsha",
    fake_git_origin_main: str | None = None,
    fake_git_remote_tags: str = "",
) -> tuple[subprocess.CompletedProcess, Path]:
    bin_dir, ssh_log = _make_fake_bin(tmp_path)

    env = os.environ.copy()
    # Halka varsayılanlarını gözlemlemek için testin kendi env override'larını temizle.
    for key in (
        "REPO_DIR",
        "PROGRAM",
        "HEALTH_URL",
        "DEPLOY_CONFIRM",
        "DEPLOY_SKIP_TESTS",
        "DEPLOY_NO_RESTART",
    ):
        env.pop(key, None)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    env["SSH_CALL_LOG"] = str(ssh_log)
    env["FAKE_GIT_DIRTY"] = "1" if fake_git_dirty else "0"
    env["FAKE_GIT_HEAD"] = fake_git_head
    env["FAKE_GIT_ORIGIN_MAIN"] = fake_git_origin_main or fake_git_head
    env["FAKE_GIT_REMOTE_TAGS"] = fake_git_remote_tags
    if env_extra:
        env.update(env_extra)

    proc = subprocess.run(
        ["bash", str(DEPLOY_SH), *args],
        cwd=tmp_path,
        env=env,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc, ssh_log


def _ssh_call_count(ssh_log: Path) -> int:
    if not ssh_log.exists():
        return 0
    return ssh_log.read_text().count("=== ssh call ===")


class TestDefaultRingTestnetUnchanged:
    """--ring verilmezse davranış bugünküyle (testnet) birebir aynı olmalı."""

    def test_no_args_defaults_to_awa_origin_main_testnet(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, [])
        assert proc.returncode == 0, proc.stderr
        assert _ssh_call_count(ssh_log) == 1
        log = ssh_log.read_text()
        assert "-o" in log.splitlines()
        assert "BatchMode=yes" in log
        assert "awa" in log.splitlines()
        # REPO_DIR/PROGRAM/HEALTH_URL/RING env'i testnet için ssh komutuna EKLENMEMELİ.
        assert "cd /opt/tradingbot-v2 &&" in log
        assert "REPO_DIR=" not in log
        assert "RING=" not in log
        assert "PROGRAM=" not in log
        assert "HEALTH_URL=" not in log
        # Bayraklar bugünküyle birebir aynı sırada/biçimde.
        assert "DEPLOY_SKIP_TESTS=0 DEPLOY_NO_RESTART=0 bash /tmp/server_deploy.sh 'origin/main'" in log

    def test_explicit_host_and_tag_testnet(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, ["myhost", "v9.9.9"])
        assert proc.returncode == 0, proc.stderr
        log = ssh_log.read_text()
        assert "myhost" in log.splitlines()
        assert "cd /opt/tradingbot-v2 &&" in log
        assert "bash /tmp/server_deploy.sh 'v9.9.9'" in log
        assert "RING=" not in log

    def test_explicit_ring_testnet_matches_default(self, tmp_path):
        """`--ring testnet` açıkça verilmiş olsa bile çıktı varsayılanla aynı olmalı."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        proc_default, log_default = _run_deploy(dir_a, [])
        proc_explicit, log_explicit = _run_deploy(dir_b, ["--ring", "testnet"])
        assert proc_default.returncode == 0, proc_default.stderr
        assert proc_explicit.returncode == 0, proc_explicit.stderr
        assert log_default.read_text() == log_explicit.read_text()

    def test_dirty_tree_refuses_before_ssh(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, [], fake_git_dirty=True)
        assert proc.returncode != 0
        assert "commit'lenmemiş değişiklik" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0


class TestMainnetRefusesOriginMain:
    def test_default_target_is_origin_main_and_is_refused(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, ["awa", "--ring", "mainnet"])
        assert proc.returncode != 0
        assert "vX.Y.Z" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0

    def test_explicit_origin_main_is_refused(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path, ["awa", "origin/main", "--ring", "mainnet"]
        )
        assert proc.returncode != 0
        assert "origin/main" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0


class TestMainnetRefusesNonSemver:
    @pytest.mark.parametrize(
        "bad_target", ["a1b2c3d", "main", "1.2.3", "v1.2", "v1.2.3-rc1"]
    )
    def test_bare_or_malformed_target_refused(self, tmp_path, bad_target):
        proc, ssh_log = _run_deploy(
            tmp_path, ["awa", bad_target, "--ring", "mainnet"]
        )
        assert proc.returncode != 0, f"'{bad_target}' kabul edilmemeliydi"
        assert "vX.Y.Z" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0

    def test_tag_not_on_origin_refused(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path,
            ["awa", "v1.2.0", "--ring", "mainnet"],
            fake_git_remote_tags="v1.0.0 v1.1.0",  # v1.2.0 YOK
        )
        assert proc.returncode != 0
        assert "origin'de yok" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0


class TestMainnetRequiresConfirmationWord:
    def test_wrong_word_refuses(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path,
            ["awa", "v1.2.0", "--ring", "mainnet"],
            fake_git_remote_tags="v1.2.0",
            stdin_text="hayır\n",
        )
        assert proc.returncode != 0
        assert "onay verilmedi" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0

    def test_empty_stdin_refuses(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path,
            ["awa", "v1.2.0", "--ring", "mainnet"],
            fake_git_remote_tags="v1.2.0",
            stdin_text="",
        )
        assert proc.returncode != 0
        assert _ssh_call_count(ssh_log) == 0

    def test_correct_word_proceeds(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path,
            ["awa", "v1.2.0", "--ring", "mainnet"],
            fake_git_remote_tags="v1.2.0",
            stdin_text="MAINNET\n",
        )
        assert proc.returncode == 0, proc.stderr
        assert _ssh_call_count(ssh_log) == 1
        log = ssh_log.read_text()
        assert "cd /opt/tradingbot-main &&" in log
        assert "RING=mainnet" in log
        assert "PROGRAM=tradingbot_main" in log
        assert "HEALTH_URL=http://127.0.0.1:9092/api/status" in log
        assert "bash /tmp/server_deploy.sh 'v1.2.0'" in log


class TestDeployConfirmBypass:
    def test_deploy_confirm_env_skips_prompt(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path,
            ["awa", "v1.2.0", "--ring", "mainnet"],
            fake_git_remote_tags="v1.2.0",
            env_extra={"DEPLOY_CONFIRM": "MAINNET"},
            stdin_text="",  # istem hiç sorulmamalı — boş stdin'le bile geçmeli
        )
        assert proc.returncode == 0, proc.stderr
        assert "onay istemi atlandı" in proc.stdout
        assert _ssh_call_count(ssh_log) == 1
        assert "RING=mainnet" in ssh_log.read_text()

    def test_deploy_confirm_wrong_value_does_not_bypass(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path,
            ["awa", "v1.2.0", "--ring", "mainnet"],
            fake_git_remote_tags="v1.2.0",
            env_extra={"DEPLOY_CONFIRM": "yes-please"},
            stdin_text="",
        )
        assert proc.returncode != 0
        assert _ssh_call_count(ssh_log) == 0


class TestFollowerRing:
    """AlgoPro takipçi halkası (D20): ayrı dizin/program/port, mainnet onayı YOK."""

    def test_follower_ring_targets_ap_directory(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, ["awa", "--ring", "follower"])
        assert proc.returncode == 0, proc.stderr
        assert _ssh_call_count(ssh_log) == 1
        log = ssh_log.read_text()
        assert "cd /opt/tradingbot-ap &&" in log
        assert "RING=follower" in log
        assert "PROGRAM=tradingbot_ap" in log
        assert "HEALTH_URL=http://127.0.0.1:9093/api/status" in log
        assert "bash /tmp/server_deploy.sh 'origin/main'" in log

    def test_follower_ring_accepts_origin_main_without_confirmation(self, tmp_path):
        """Takipçi halkası TESTNET'tir: 'MAINNET' onay istemi SORULMAZ."""
        proc, ssh_log = _run_deploy(
            tmp_path, ["awa", "--ring", "follower"], stdin_text=""
        )
        assert proc.returncode == 0, proc.stderr
        assert "MAİNNET DEPLOY ONAYI" not in proc.stdout
        assert _ssh_call_count(ssh_log) == 1

    def test_follower_ring_accepts_explicit_target(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, ["awa", "abc1234", "--ring", "follower"])
        assert proc.returncode == 0, proc.stderr
        assert "bash /tmp/server_deploy.sh 'abc1234'" in ssh_log.read_text()

    def test_follower_ring_refuses_dirty_tree(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path, ["awa", "--ring", "follower"], fake_git_dirty=True
        )
        assert proc.returncode != 0
        assert _ssh_call_count(ssh_log) == 0

    def test_env_overrides_win_over_ring_defaults(self, tmp_path):
        proc, ssh_log = _run_deploy(
            tmp_path,
            ["awa", "--ring", "follower"],
            env_extra={"REPO_DIR": "/opt/custom-ap", "PROGRAM": "custom_ap"},
        )
        assert proc.returncode == 0, proc.stderr
        log = ssh_log.read_text()
        assert "cd /opt/custom-ap &&" in log
        assert "PROGRAM=custom_ap" in log


class TestInvalidRingValue:
    def test_unknown_ring_refused(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, ["awa", "v1.2.0", "--ring", "prod"])
        assert proc.returncode != 0
        assert "geçersiz --ring" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0


# ── server_deploy.sh: mainnet .env ön kontrolü (statik/metin doğrulaması) ───


def test_server_deploy_supports_follower_ring():
    """RING=follower kabul edilmeli ve takipçinin AYRI entry-halt dosyasını
    kontrol etmeli (scalper/mainnet yolları DEĞİŞMEDEN)."""
    text = SERVER_DEPLOY_SH.read_text()
    assert "testnet|follower|mainnet" in text
    assert "state/follower_entry_halt.json" in text
    assert 'HALT_FILE="state/scalper_entry_halt.json"' in text


def test_server_deploy_mentions_mainnet_precheck_keys():
    """server_deploy.sh RING=mainnet iken RISK_EVENT_SECRET/TV_WEBHOOK_SECRET/
    SCALPER_ENTRY_HALT_ENABLED kontrolünü içermeli (CLAUDE.md gereği canlı sunucuya
    dokunmadan yalnız script metnini doğruluyoruz — gerçek çalıştırma sunucu deploy'unun
    işidir)."""
    text = SERVER_DEPLOY_SH.read_text()
    assert 'RING="${RING:-testnet}"' in text
    assert "RISK_EVENT_SECRET" in text
    assert "TV_WEBHOOK_SECRET" in text
    assert "SCALPER_ENTRY_HALT_ENABLED=true" in text
    assert 'if [ "$RING" = "mainnet" ]; then' in text


def test_ring_env_diff_masks_secret_like_keys():
    text = RING_ENV_DIFF_SH.read_text()
    for needle in ("SECRET", "KEY", "TOKEN", "PASS"):
        assert needle in text
    assert "***" in text


# ── server_deploy.sh / restart_safe.sh: YÜRÜTME testleri (D20a) ─────────────
# Sahte `supervisorctl`, `curl`, `git`, `date` ile gerçek sunucuya
# DOKUNMADAN çalıştırılır. Metin grep'i yeterli değildir: kapıların
# GERÇEKTEN çalıştığı (ve doğru sırada olduğu) kanıtlanmalıdır.

RESTART_SAFE_SH = SCRIPTS / "restart_safe.sh"


def _make_server_bin(tmp_path: Path, *, restart_fails=False, healthy=True):
    """Sahte supervisorctl/curl/git/date + çağrı kaydı döner."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    call_log = tmp_path / "calls.log"
    call_log.write_text("")

    _write_executable(
        bin_dir / "supervisorctl",
        f"""#!/usr/bin/env bash
echo "supervisorctl $*" >> "{call_log}"
case "$1" in
  restart) {"exit 3" if restart_fails else 'echo "$2: started"'} ;;
  status)  echo "$2                          RUNNING   pid 123, uptime 0:00:05" ;;
  pid)     echo 123 ;;
esac
""",
    )
    _write_executable(
        bin_dir / "curl",
        f"""#!/usr/bin/env bash
echo "curl $*" >> "{call_log}"
{'echo \'{"status":"ok"}\'' if healthy else 'exit 7'}
""",
    )
    _write_executable(
        bin_dir / "git",
        f"""#!/usr/bin/env bash
echo "git $*" >> "{call_log}"
case "$1 $2" in
  "rev-parse HEAD") echo "aaaaaaa" ;;
  "status --porcelain") ;;
  "log -1") echo "sahte commit" ;;
  *) ;;
esac
exit 0
""",
    )
    # macOS `date -d` desteklemez; ban penceresi hesabı fail-closed ölür.
    _write_executable(
        bin_dir / "date",
        """#!/usr/bin/env bash
if [ "$1" = "-d" ]; then echo "2000-01-01 00:00"; exit 0; fi
exec /bin/date "$@"
""",
    )
    return bin_dir, call_log


def _make_ring_dir(tmp_path: Path, *, bot_mode=None, halt_file=None):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True, exist_ok=True)
    (repo / "logs").mkdir(parents=True, exist_ok=True)
    (repo / "backups").mkdir(parents=True, exist_ok=True)
    env_lines = ["BINANCE_API_KEY=x\n"]
    if bot_mode:
        env_lines.append(f"BOT_MODE={bot_mode}\n")
    (repo / ".env").write_text("".join(env_lines))
    if halt_file:
        (repo / halt_file).parent.mkdir(parents=True, exist_ok=True)
        (repo / halt_file).write_text("{}")
    # Ayar doğrulaması adımı için sahte venv python'u.
    venv_python = repo / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    _write_executable(venv_python, "#!/usr/bin/env bash\necho 'env ok'\n")
    return repo


def _run_server_deploy(tmp_path, repo, bin_dir, env_extra=None, args=()):
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    env["REPO_DIR"] = str(repo)
    env["DEPLOY_REPO_DIR_OVERRIDE"] = "1"
    env["DEPLOY_SKIP_TESTS"] = "1"
    env["DEPLOY_NO_RESTART"] = "1"
    env["ROLLBACK_SETTLE_SECONDS"] = "0"
    for key in ("RING", "PROGRAM", "HEALTH_URL"):
        env.pop(key, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SERVER_DEPLOY_SH), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestServerDeployRingBotModeBinding:
    """D20a bulgu 4: halka ↔ `.env` BOT_MODE bağı (fail-closed).

    Düzeltme olmadan KIRMIZI: `RING` ile `.env` arasında HİÇBİR bağ yoktu;
    takipçi `.env`'i olan bir dizine `RING=testnet` ile deploy edilebiliyor
    (ve scalper sanılan halkada TAKİPÇİ motoru yeniden başlatılıyordu).
    """

    def test_testnet_ring_refuses_a_follower_env(self, tmp_path):
        repo = _make_ring_dir(tmp_path, bot_mode="follower")
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(tmp_path, repo, bin_dir)
        assert proc.returncode != 0
        assert "BOT_MODE=follower" in (proc.stdout + proc.stderr)

    def test_mainnet_ring_refuses_a_follower_env(self, tmp_path):
        repo = _make_ring_dir(tmp_path, bot_mode="follower")
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path,
            repo,
            bin_dir,
            env_extra={
                "RING": "mainnet",
                "PROGRAM": "tradingbot_main",
                "HEALTH_URL": "http://127.0.0.1:9092/api/status",
            },
        )
        assert proc.returncode != 0
        assert "BOT_MODE=follower" in (proc.stdout + proc.stderr)

    def test_follower_ring_requires_a_follower_env(self, tmp_path):
        repo = _make_ring_dir(tmp_path, bot_mode="scalper")
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path,
            repo,
            bin_dir,
            env_extra={
                "RING": "follower",
                "PROGRAM": "tradingbot_ap",
                "HEALTH_URL": "http://127.0.0.1:9093/api/status",
            },
        )
        assert proc.returncode != 0
        assert "BOT_MODE=follower yok" in (proc.stdout + proc.stderr)

    def test_follower_ring_accepts_a_follower_env(self, tmp_path):
        repo = _make_ring_dir(tmp_path, bot_mode="follower")
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path,
            repo,
            bin_dir,
            env_extra={
                "RING": "follower",
                "PROGRAM": "tradingbot_ap",
                "HEALTH_URL": "http://127.0.0.1:9093/api/status",
            },
        )
        # BOT_MODE kapısını GEÇTİ (sonraki adımlarda sahte git ile ilerler).
        assert "BOT_MODE" not in (proc.stdout + proc.stderr)

    def test_testnet_ring_accepts_a_missing_bot_mode(self, tmp_path):
        """Bugünkü scalper `.env`'inde BOT_MODE satırı YOK — davranış korunur."""
        repo = _make_ring_dir(tmp_path)
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(tmp_path, repo, bin_dir)
        assert "BOT_MODE" not in (proc.stdout + proc.stderr)


class TestServerDeployRingBinding:
    """Ana oturum eki (A): RING ↔ REPO_DIR/PROGRAM/HEALTH_URL bağı."""

    def test_program_must_match_the_ring(self, tmp_path):
        repo = _make_ring_dir(tmp_path, bot_mode="follower")
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path,
            repo,
            bin_dir,
            env_extra={"RING": "follower", "PROGRAM": "tradingbot_v2"},
        )
        assert proc.returncode != 0
        assert "PROGRAM" in proc.stderr

    def test_health_url_must_match_the_ring(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path,
            repo,
            bin_dir,
            env_extra={"HEALTH_URL": "http://127.0.0.1:9093/api/status"},
        )
        assert proc.returncode != 0
        assert "HEALTH_URL" in proc.stderr

    def test_repo_dir_override_needs_explicit_consent(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path, repo, bin_dir, env_extra={"DEPLOY_REPO_DIR_OVERRIDE": "0"}
        )
        assert proc.returncode != 0
        assert "REPO_DIR" in proc.stderr

    def test_entry_halt_is_ring_specific(self, tmp_path):
        """Takipçi halkasında SCALPER halt dosyası deploy'u ENGELLEMEZ."""
        repo = _make_ring_dir(
            tmp_path,
            bot_mode="follower",
            halt_file="state/follower_entry_halt.json",
        )
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path,
            repo,
            bin_dir,
            env_extra={
                "RING": "follower",
                "PROGRAM": "tradingbot_ap",
                "HEALTH_URL": "http://127.0.0.1:9093/api/status",
            },
        )
        assert proc.returncode != 0
        assert "entry-halt aktif" in (proc.stdout + proc.stderr)


class TestServerDeployRestartFailureRollsBack:
    """Ana oturum eki (B): `supervisorctl restart` hatası GERİ ALINIR."""

    def test_restart_failure_triggers_rollback(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, call_log = _make_server_bin(tmp_path, restart_fails=True)
        proc = _run_server_deploy(
            tmp_path, repo, bin_dir, env_extra={"DEPLOY_NO_RESTART": "0"}
        )
        assert proc.returncode != 0
        output = proc.stdout + proc.stderr
        assert "restart BAŞARISIZ" in output
        assert "GERİ ALINIYOR" in output
        assert "deploy başarısız" in output
        # Geri alma .env yedeğini geri koyar ve süreci TEKRAR başlatmayı dener.
        assert call_log.read_text().count("supervisorctl restart") == 2

    def test_successful_restart_reports_ok(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path, repo, bin_dir, env_extra={"DEPLOY_NO_RESTART": "0"}
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "TAMAM" in proc.stdout
        assert "curl" in call_log.read_text()


class TestRestartSafe:
    """Ana oturum eki (D): `.env` reçeteleri için korumalı restart."""

    def _run(self, tmp_path, repo, bin_dir, args=(), env_extra=None):
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        env["REPO_DIR"] = str(repo)
        env["DEPLOY_REPO_DIR_OVERRIDE"] = "1"
        for key in ("RING", "PROGRAM", "HEALTH_URL", "RESTART_LABEL"):
            env.pop(key, None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", str(RESTART_SAFE_SH), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_script_exists_and_is_executable(self):
        assert RESTART_SAFE_SH.exists()
        assert RESTART_SAFE_SH.stat().st_mode & stat.S_IXUSR
        proc = subprocess.run(
            ["bash", "-n", str(RESTART_SAFE_SH)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr

    def test_happy_path_backs_up_env_and_checks_health(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = self._run(
            tmp_path, repo, bin_dir, env_extra={"RESTART_LABEL": "tv-events"}
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        backups = list((repo / "backups").glob("env.bak-*-tv-events"))
        assert len(backups) == 1
        # Saniye damgası: aynı gün ikinci uygulama temiz yedeği EZMEZ.
        assert len(backups[0].name.split("-")[2]) == 6
        assert "supervisorctl restart tradingbot_v2" in call_log.read_text()
        assert "TAMAM" in proc.stdout

    def test_entry_halt_blocks_the_restart(self, tmp_path):
        repo = _make_ring_dir(
            tmp_path, halt_file="state/scalper_entry_halt.json"
        )
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = self._run(tmp_path, repo, bin_dir)
        assert proc.returncode != 0
        assert "entry-halt aktif" in proc.stdout
        assert "supervisorctl restart" not in call_log.read_text()

    def test_ban_window_blocks_the_restart(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        (repo / "logs" / "bot.log").write_text(
            "2999-01-01 00:00:00 | ERROR | Binance HTTP 418 banned\n"
        )
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = self._run(tmp_path, repo, bin_dir)
        assert proc.returncode != 0
        assert "ban izi" in proc.stdout
        assert "supervisorctl restart" not in call_log.read_text()

    def test_ring_bot_mode_mismatch_blocks_the_restart(self, tmp_path):
        repo = _make_ring_dir(tmp_path, bot_mode="follower")
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = self._run(tmp_path, repo, bin_dir)
        assert proc.returncode != 0
        assert "BOT_MODE=follower" in proc.stdout
        assert "supervisorctl restart" not in call_log.read_text()

    def test_unhealthy_process_fails_loudly(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, _ = _make_server_bin(tmp_path, healthy=False)
        proc = self._run(
            tmp_path, repo, bin_dir, env_extra={"HEALTH_TIMEOUT": "5"}
        )
        assert proc.returncode != 0
        assert "sağlık uç noktası" in proc.stdout

    def test_ring_mapping_matches_server_deploy(self):
        """İki script AYNI halka eşlemesini taşımalı (kopya kayması yok)."""
        restart_text = RESTART_SAFE_SH.read_text()
        deploy_text = SERVER_DEPLOY_SH.read_text()
        for needle in (
            "/opt/tradingbot-v2",
            "/opt/tradingbot-ap",
            "/opt/tradingbot-main",
            "tradingbot_v2",
            "tradingbot_ap",
            "tradingbot_main",
            "state/follower_entry_halt.json",
            "state/scalper_entry_halt.json",
            "HTTP 418|banned",
        ):
            assert needle in restart_text, needle
            assert needle in deploy_text, needle
