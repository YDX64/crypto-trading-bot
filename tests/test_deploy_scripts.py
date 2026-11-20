"""scripts/deploy.sh, scripts/server_deploy.sh, scripts/ring_env_diff.sh ve
scripts/preflight_server_env.sh (D43) testleri.

Sözleşme (bkz. CLAUDE.md, docs/MAINNET_PLAN.md §1, §5): iki halka — testnet (bugünkü
davranış, DEĞİŞMEDEN) ve mainnet (yalnız `vX.Y.Z` etiketi, origin'de var olmalı, açık
onay). Gerçek sunucuya/ağa DOKUNULMAZ: `ssh` ve `git` sahte (fake) ikililerle PATH'e
enjekte edilir; `deploy.sh` bu sahte ikililer dışında hiçbir dış bağımlılığa ihtiyaç
duymaz (yalnız bash yerleşikleri + gerçek `grep`/`read`/`printf`/`echo`).
"""

import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
DEPLOY_SH = SCRIPTS / "deploy.sh"
SERVER_DEPLOY_SH = SCRIPTS / "server_deploy.sh"
RING_ENV_DIFF_SH = SCRIPTS / "ring_env_diff.sh"
PREFLIGHT_SH = SCRIPTS / "preflight_server_env.sh"


# ── Sözdizimi (bash -n) ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "script",
    [DEPLOY_SH, SERVER_DEPLOY_SH, RING_ENV_DIFF_SH, PREFLIGHT_SH],
    ids=lambda p: p.name,
)
def test_bash_syntax_ok(script):
    assert script.exists(), f"beklenen script yok: {script}"
    proc = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"{script.name} sözdizimi hatası:\n{proc.stderr}"


def test_scripts_are_executable():
    for script in (DEPLOY_SH, SERVER_DEPLOY_SH, RING_ENV_DIFF_SH, PREFLIGHT_SH):
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
      FAKE_GIT_UNTRACKED=1          → ignored olmayan untracked dosya bildirir
      FAKE_GIT_HEAD=<sha>           → `git rev-parse HEAD`
      FAKE_GIT_ORIGIN_MAIN=<sha>    → `git rev-parse origin/main` (varsayılan: HEAD ile aynı)
      FAKE_GIT_REMOTE_TAGS="v1 v2"  → `git ls-remote --exit-code --tags origin refs/tags/<X>`
                                       yalnız bu listedeki etiketler için başarılı olur
    Sahte ssh her çağrıyı $SSH_CALL_LOG dosyasına satır satır ekler ve 0 ile çıkar
    (gerçek bir bağlantı KURMAZ).
    """
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir()
    git_log = tmp_path / "git_calls.log"
    git_log.write_text("")

    _write_executable(
        bin_dir / "git",
        r"""#!/usr/bin/env bash
: "${GIT_CALL_LOG:?GIT_CALL_LOG not set}"
printf 'git' >> "$GIT_CALL_LOG"
printf ' %s' "$@" >> "$GIT_CALL_LOG"
printf '\n' >> "$GIT_CALL_LOG"
if [ "${1:-}" = "-C" ]; then
  shift 2
fi
sub="$1"; shift || true
case "$sub" in
  status)
    if [ "${FAKE_GIT_DIRTY:-0}" = "1" ]; then
      echo " M fake/dirty/file"
    fi
    if [ "${FAKE_GIT_UNTRACKED:-0}" = "1" ]; then
      case " $* " in
        *" --untracked-files=no "*) ;;
        *) echo "?? fake/untracked/file" ;;
      esac
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
    fake_git_untracked: bool = False,
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
    env["GIT_CALL_LOG"] = str(tmp_path / "git_calls.log")
    env["FAKE_GIT_DIRTY"] = "1" if fake_git_dirty else "0"
    env["FAKE_GIT_UNTRACKED"] = "1" if fake_git_untracked else "0"
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

    def test_untracked_file_refuses_before_ssh(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, [], fake_git_untracked=True)
        assert proc.returncode != 0
        assert "commit'lenmemiş değişiklik" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0
        assert (
            f"git -C {REPO_ROOT} status --porcelain --untracked-files=all"
            in (tmp_path / "git_calls.log").read_text()
        )

    def test_git_checks_are_anchored_to_script_repo_not_caller_cwd(self, tmp_path):
        proc, _ = _run_deploy(tmp_path, [])
        assert proc.returncode == 0, proc.stderr
        calls = (tmp_path / "git_calls.log").read_text().splitlines()
        assert calls
        assert all(f"git -C {REPO_ROOT}" in call for call in calls)
        assert all(f"git -C {tmp_path}" not in call for call in calls)


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
        assert "HEALTH_URL=http://127.0.0.1:9092/health" in log
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
        assert "HEALTH_URL=http://127.0.0.1:9093/health" in log
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
    """Sahte supervisorctl/curl/git/date/flock/sleep + çağrı kaydı döner."""
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
{'printf \'%s\\n\' "$FAKE_HEALTH_BODY"' if healthy else 'exit 7'}
""",
    )
    _write_executable(
        bin_dir / "git",
        f"""#!/usr/bin/env bash
echo "git $*" >> "{call_log}"
case "$1" in
  rev-parse) [ "$2" = "HEAD" ] && echo "aaaaaaa" ;;
  status)
    case " $* " in
      *" --untracked-files=no "*) ;;
      *) [ -n "${{FAKE_SERVER_GIT_STATUS:-}}" ] && printf '%s\n' "$FAKE_SERVER_GIT_STATUS" ;;
    esac
    ;;
  log) echo "sahte commit" ;;
esac
exit 0
""",
    )
    _write_executable(
        bin_dir / "flock",
        f"""#!/usr/bin/env bash
echo "flock $*" >> "{call_log}"
[ "${{FAKE_FLOCK_BUSY:-0}}" != "1" ]
""",
    )
    _write_executable(
        bin_dir / "sleep",
        f"""#!/usr/bin/env bash
echo "sleep $*" >> "{call_log}"
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


def _make_ring_dir(
    tmp_path: Path, *, bot_mode=None, halt_file=None, follower_embedded=None
):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True, exist_ok=True)
    (repo / "logs").mkdir(parents=True, exist_ok=True)
    (repo / "backups").mkdir(parents=True, exist_ok=True)
    env_lines = ["BINANCE_API_KEY=x\n"]
    if bot_mode:
        env_lines.append(f"BOT_MODE={bot_mode}\n")
    if follower_embedded is not None:
        env_lines.append(f"FOLLOWER_EMBEDDED={follower_embedded}\n")
    (repo / ".env").write_text("".join(env_lines))
    if halt_file:
        (repo / halt_file).parent.mkdir(parents=True, exist_ok=True)
        (repo / halt_file).write_text("{}")
    # Ayar doğrulaması sahte; /health JSON kontrolü ise gerçek Python'la
    # koşar ki bozuk/eksik JSON testleri grep benzeri bir kapıya dönüşmesin.
    venv_python = repo / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    real_python = shlex.quote(sys.executable)
    _write_executable(
        venv_python,
        f"""#!/usr/bin/env bash
if [[ "${{2:-}}" == *"json.load"* ]]; then
  exec {real_python} "$@"
fi
echo 'env ok'
""",
    )
    return repo


def _run_server_deploy(tmp_path, repo, bin_dir, env_extra=None, args=()):
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    env["REPO_DIR"] = str(repo)
    env["DEPLOY_REPO_DIR_OVERRIDE"] = "1"
    env["DEPLOY_SKIP_TESTS"] = "1"
    env["DEPLOY_NO_RESTART"] = "1"
    env["ROLLBACK_SETTLE_SECONDS"] = "0"
    env["FAKE_HEALTH_BODY"] = '{"status":"healthy","core_healthy":true}'
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
                "HEALTH_URL": "http://127.0.0.1:9092/health",
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
                "HEALTH_URL": "http://127.0.0.1:9093/health",
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
                "HEALTH_URL": "http://127.0.0.1:9093/health",
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
            env_extra={"HEALTH_URL": "http://127.0.0.1:9093/health"},
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
                "HEALTH_URL": "http://127.0.0.1:9093/health",
            },
        )
        assert proc.returncode != 0
        assert "entry-halt aktif" in (proc.stdout + proc.stderr)

    def test_embedded_follower_halt_blocks_testnet_deploy(self, tmp_path):
        """D20b: gömülü takipçinin kilidi scalper halkasında da GÖRÜNÜR.

        Düşmanca inceleme: `RING=testnet` yalnız `state/scalper_entry_halt.json`e
        bakıyordu; gömülü takipçi korumasız pozisyon yüzünden kilitliyken deploy
        ve restart SERBESTTİ (CLAUDE.md yasak #3 ihlali).
        """
        repo = _make_ring_dir(
            tmp_path,
            follower_embedded="true",
            halt_file="state/follower_entry_halt.json",
        )
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(tmp_path, repo, bin_dir)
        assert proc.returncode != 0
        assert "gömülü takipçi entry-halt aktif" in (proc.stdout + proc.stderr)

    def test_follower_halt_does_not_block_when_embedded_is_off(self, tmp_path):
        """Bayrak KAPALIYKEN dosya deploy'u engellemez (bugünkü davranış)."""
        repo = _make_ring_dir(
            tmp_path, halt_file="state/follower_entry_halt.json"
        )
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = _run_server_deploy(tmp_path, repo, bin_dir)
        assert "gömülü takipçi entry-halt aktif" not in (proc.stdout + proc.stderr)


class TestServerDeployWorkspaceAndOperationLock:
    def test_untracked_server_file_refuses_before_checkout(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path,
            repo,
            bin_dir,
            env_extra={"FAKE_SERVER_GIT_STATUS": "?? stray-not-ignored.py"},
        )
        assert proc.returncode != 0
        assert "untracked" in (proc.stdout + proc.stderr)
        calls = call_log.read_text()
        assert "git status --porcelain --untracked-files=all" in calls
        assert "git checkout" not in calls

    def test_busy_nonblocking_lock_refuses_deploy(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path, repo, bin_dir, env_extra={"FAKE_FLOCK_BUSY": "1"}
        )
        assert proc.returncode != 0
        assert "başka bir deploy/restart işlemi aktif" in (proc.stdout + proc.stderr)
        assert "flock -n 9" in call_log.read_text()
        assert "supervisorctl restart" not in call_log.read_text()
        assert (repo / "logs" / "deploy-restart.lock").exists()


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
        assert "curl -fsS -m 5 http://127.0.0.1:9091/health" in call_log.read_text()

    @pytest.mark.parametrize(
        "health_body",
        [
            '{"status":"healthy"}',
            '{"status":"healthy","core_healthy":false}',
            '{"status":"degraded","core_healthy":true}',
            '{"status":"healthy","core_healthy":1}',
            "not-json",
        ],
    )
    def test_http_success_without_strict_healthy_json_rolls_back(
        self, tmp_path, health_body
    ):
        repo = _make_ring_dir(tmp_path)
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = _run_server_deploy(
            tmp_path,
            repo,
            bin_dir,
            env_extra={
                "DEPLOY_NO_RESTART": "0",
                "HEALTH_TIMEOUT": "5",
                "FAKE_HEALTH_BODY": health_body,
            },
        )
        assert proc.returncode != 0
        assert "status=healthy ve core_healthy=true vermedi" in (
            proc.stdout + proc.stderr
        )
        assert "GERİ ALINIYOR" in proc.stdout
        assert call_log.read_text().count("supervisorctl restart") == 2


class TestRestartSafe:
    """Ana oturum eki (D): `.env` reçeteleri için korumalı restart."""

    def _run(self, tmp_path, repo, bin_dir, args=(), env_extra=None):
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        env["REPO_DIR"] = str(repo)
        env["DEPLOY_REPO_DIR_OVERRIDE"] = "1"
        env["FAKE_HEALTH_BODY"] = '{"status":"healthy","core_healthy":true}'
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
        assert "curl -fsS -m 5 http://127.0.0.1:9091/health" in call_log.read_text()
        assert "TAMAM" in proc.stdout

    def test_busy_nonblocking_lock_refuses_restart(self, tmp_path):
        repo = _make_ring_dir(tmp_path)
        bin_dir, call_log = _make_server_bin(tmp_path)
        proc = self._run(
            tmp_path, repo, bin_dir, env_extra={"FAKE_FLOCK_BUSY": "1"}
        )
        assert proc.returncode != 0
        assert "başka bir deploy/restart işlemi aktif" in (proc.stdout + proc.stderr)
        assert "flock -n 9" in call_log.read_text()
        assert "supervisorctl restart" not in call_log.read_text()
        assert (repo / "logs" / "deploy-restart.lock").exists()

    def test_embedded_follower_halt_blocks_the_restart(self, tmp_path):
        """D20b: gömülü takipçinin kilidi testnet halkasında restart'ı durdurur."""
        repo = _make_ring_dir(
            tmp_path,
            follower_embedded="true",
            halt_file="state/follower_entry_halt.json",
        )
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = self._run(tmp_path, repo, bin_dir)
        assert proc.returncode != 0
        assert "gömülü takipçi entry-halt aktif" in (proc.stdout + proc.stderr)

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
        assert "status=healthy ve core_healthy=true vermedi" in proc.stdout

    @pytest.mark.parametrize(
        "health_body",
        [
            '{"status":"healthy"}',
            '{"status":"healthy","core_healthy":false}',
            '{"status":"degraded","core_healthy":true}',
            "not-json",
        ],
    )
    def test_http_success_without_strict_healthy_json_fails(self, tmp_path, health_body):
        repo = _make_ring_dir(tmp_path)
        bin_dir, _ = _make_server_bin(tmp_path)
        proc = self._run(
            tmp_path,
            repo,
            bin_dir,
            env_extra={"HEALTH_TIMEOUT": "5", "FAKE_HEALTH_BODY": health_body},
        )
        assert proc.returncode != 0
        assert "status=healthy ve core_healthy=true vermedi" in proc.stdout

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
            "logs/deploy-restart.lock",
            "flock -n 9",
            'body.get("status") == "healthy"',
            'body.get("core_healthy") is True',
        ):
            assert needle in restart_text, needle
            assert needle in deploy_text, needle


# ── preflight_server_env.sh (D43) ────────────────────────────────────────────
# Gerçek ağ/ssh YOK: sunucu ayarları PREFLIGHT_ENV_FILE ile ya da sahte ssh ile gelir,
# pytest yerine sahte yorumlayıcı (PREFLIGHT_PYTHON) çalışır, depo olarak küçük bir
# sahte git deposu (PREFLIGHT_REPO) kullanılır. Gerçek paket koşulmaz.

PREFLIGHT_ENV_EXAMPLE = "# env.example ilk satir\nSCALPER_LEVERAGE=10\nAPI_PORT=9091\n"
PREFLIGHT_REPO_ENV_MARKER = "REPO_OWN_ENV_MARKER_9f3a"

PREFLIGHT_SETTINGS = (
    "SCALPER_STOP_MODE=atr\n"  # izinli aile, secret değil
    "SCALPER_DYNAMIC_LEVERAGE=true\n"  # izinli aile, secret değil
    "TV_WEBHOOK_SECRET=real-tv-secret-1\n"  # aile içi ama SECRET
    "BINANCE_API_KEY=real-binance-key-2\n"  # aile içi ama KEY
    "BINANCE_API_SECRET=real-binance-secret-3\n"
    "BINANCE_BIND_IP=203.0.113.7\n"  # BIND_IP
    "RISK_EVENT_TOKEN=real-risk-token-4\n"  # TOKEN
    "FOLLOWER_DB_PASS=real-pass-5\n"  # PASS
    "TELEGRAM_CHAT_ID=out-of-family-6\n"  # aile dışı
    "DATABASE_URL=sqlite:///out-of-family-7\n"  # aile dışı
)
PREFLIGHT_SECRET_VALUES = (
    "real-tv-secret-1",
    "real-binance-key-2",
    "real-binance-secret-3",
    "203.0.113.7",
    "real-risk-token-4",
    "real-pass-5",
)
PREFLIGHT_OUT_OF_FAMILY_VALUES = ("out-of-family-6", "out-of-family-7")


def _preflight_make_repo(tmp_path: Path) -> Path:
    """Küçük sahte git deposu: izlenen + izlenmeyen + silinmiş + ignore edilen dosyalar."""
    repo = tmp_path / "fake_repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "logs").mkdir()
    (repo / "env.example").write_text(PREFLIGHT_ENV_EXAMPLE)
    (repo / ".gitignore").write_text(".env\nlogs/\n.venv/\n")
    (repo / "src" / "tracked.py").write_text("tracked = 1\n")
    (repo / "src" / "will_be_deleted.py").write_text("gone = 1\n")
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    # çalışma ağacında silinmiş ama indekste olan izlenen dosya → atlanmalı
    (repo / "src" / "will_be_deleted.py").unlink()
    # izlenmeyen ama ignore EDİLMEYEN dosya → kopyalanmalı
    (repo / "src" / "untracked_new.py").write_text("new = 1\n")
    # ignore edilenler → kopyalanMAMALI
    (repo / ".env").write_text(f"SCALPER_LEVERAGE=99\nSOME_SECRET={PREFLIGHT_REPO_ENV_MARKER}\n")
    (repo / "logs" / "bot.log").write_text(f"log {PREFLIGHT_REPO_ENV_MARKER}\n")
    return repo


def _preflight_fake_python(tmp_path: Path) -> Path:
    """Kopyanın .env'ini, argv'sini ve cwd'sini kaydeden sahte yorumlayıcı."""
    py = tmp_path / "fake_python"
    _write_executable(
        py,
        r"""#!/usr/bin/env bash
: "${FAKE_PY_OUT:?}"
printf '%s\n' "$@" > "$FAKE_PY_OUT/argv"
cp .env "$FAKE_PY_OUT/dotenv"
pwd -P > "$FAKE_PY_OUT/cwd"
env | sort > "$FAKE_PY_OUT/environ"
exit "${FAKE_PY_RC:-0}"
""",
    )
    return py


def _preflight_setup(tmp_path: Path, settings: str = PREFLIGHT_SETTINGS):
    repo = _preflight_make_repo(tmp_path)
    py = _preflight_fake_python(tmp_path)
    env_file = tmp_path / "server_env.txt"
    env_file.write_text(settings)
    out = tmp_path / "py_out"
    out.mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    return repo, py, env_file, out, tmpdir


def _preflight_env(tmp_path, repo, py, out, tmpdir, **extra):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "TMPDIR": str(tmpdir),
        "PREFLIGHT_REPO": str(repo),
        "PREFLIGHT_PYTHON": str(py),
        "FAKE_PY_OUT": str(out),
    }
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _run_preflight(tmp_path, env, args=(), cwd=None):
    return subprocess.run(
        ["bash", str(PREFLIGHT_SH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or tmp_path),
        timeout=60,
    )


class TestPreflightServerEnv:
    def test_filters_secrets_and_builds_env_from_file(self, tmp_path):
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir, PREFLIGHT_ENV_FILE=env_file
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        dotenv = (out / "dotenv").read_text()
        # env.example'ın içeriği EN BAŞTA ve bütün
        assert dotenv.startswith(PREFLIGHT_ENV_EXAMPLE)
        lines = dotenv.splitlines()
        # izinli satırlar var
        assert "SCALPER_STOP_MODE=atr" in lines
        assert "SCALPER_DYNAMIC_LEVERAGE=true" in lines
        # sahte secret satırları ikisi de var ve EN SONDA
        assert lines[-2:] == [
            "TV_WEBHOOK_SECRET=dummy-preflight",
            "RISK_EVENT_SECRET=dummy-preflight",
        ]
        # ayar satırları env.example'dan SONRA geliyor
        assert dotenv.index("SCALPER_STOP_MODE=atr") > dotenv.index(PREFLIGHT_ENV_EXAMPLE)
        # gerçek secret değerleri ve aile dışı satırlar kopyanın .env'inde YOK
        for val in PREFLIGHT_SECRET_VALUES + PREFLIGHT_OUT_OF_FAMILY_VALUES:
            assert val not in dotenv, val
        assert "TELEGRAM_CHAT_ID" not in dotenv
        assert "DATABASE_URL" not in dotenv
        assert "BINANCE_API_KEY" not in dotenv
        # çıktıya hiçbir değer basılmaz
        for val in PREFLIGHT_SECRET_VALUES + PREFLIGHT_OUT_OF_FAMILY_VALUES:
            assert val not in proc.stdout + proc.stderr, val
        assert "2 ayar satırı" in proc.stdout

    def test_mixed_case_secret_names_are_filtered(self, tmp_path):
        """Bot .env'i büyük/küçük harf farksız okur: 'SCALPER_api_key' da secret'tır."""
        leaks = ("leak-mc-1", "leak-mc-2", "leak-mc-3", "leak-mc-4", "leak-mc-5")
        settings = (
            "SCALPER_STOP_MODE=atr\n"
            "scalper_dynamic_leverage=true\n"  # küçük harfli ama secret değil → alınır
            f"SCALPER_api_key={leaks[0]}\n"
            f"BINANCE_Secret={leaks[1]}\n"
            f"SCALPER_Token={leaks[2]}\n"
            f"tv_webhook_secret={leaks[3]}\n"
            f"FOLLOWER_db_Pass={leaks[4]}\n"
        )
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path, settings=settings)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir, PREFLIGHT_ENV_FILE=env_file
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        dotenv = (out / "dotenv").read_text()
        for val in leaks:
            assert val not in dotenv, val
            assert val not in proc.stdout + proc.stderr, val
        lines = dotenv.splitlines()
        assert "SCALPER_STOP_MODE=atr" in lines
        assert "scalper_dynamic_leverage=true" in lines
        assert "2 ayar satırı" in proc.stdout

    def test_pytest_arguments_exact_and_extra_args_passed(self, tmp_path):
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir, PREFLIGHT_ENV_FILE=env_file
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (out / "argv").read_text().splitlines() == [
            "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider",
        ]
        # '--' sonrası ek argümanlar aynen eklenir (host verilmeden de olur)
        proc = _run_preflight(tmp_path, env, ["--", "-k", "maker fills", "-vv"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (out / "argv").read_text().splitlines() == [
            "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider",
            "-k", "maker fills", "-vv",
        ]
        # host + '--' birlikte
        proc = _run_preflight(tmp_path, env, ["somehost", "--", "-k", "x"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (out / "argv").read_text().splitlines()[-2:] == ["-k", "x"]

    def test_runs_inside_copy_not_in_repo_or_cwd(self, tmp_path):
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir,
            PREFLIGHT_ENV_FILE=env_file, PREFLIGHT_KEEP="1",
        )
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        proc = _run_preflight(tmp_path, env, cwd=elsewhere)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cwd = Path((out / "cwd").read_text().strip())
        assert cwd.name == "copy"
        assert str(cwd).startswith(str(tmpdir.resolve()))
        assert cwd != repo.resolve() and cwd != elsewhere.resolve()
        # çalışma ağacı kopyalandı: izlenen + izlenmeyen var, silinmiş izlenen atlandı
        assert (cwd / "src" / "tracked.py").exists()
        assert (cwd / "src" / "untracked_new.py").exists()
        assert not (cwd / "src" / "will_be_deleted.py").exists()
        assert (cwd / "tests" / "test_x.py").exists()
        # repoya bir şey yazılmadı (yeni dosya yok)
        assert not (repo / "src" / "will_be_deleted.py").exists()
        assert sorted(p.name for p in repo.iterdir() if p.name != ".git") == sorted(
            [".env", ".gitignore", "env.example", "logs", "src", "tests"]
        )

    def test_exit_status_is_pytest_status(self, tmp_path):
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        for rc in (0, 1, 5):
            env = _preflight_env(
                tmp_path, repo, py, out, tmpdir,
                PREFLIGHT_ENV_FILE=env_file, FAKE_PY_RC=rc,
            )
            proc = _run_preflight(tmp_path, env)
            assert proc.returncode == rc, proc.stdout + proc.stderr
            if rc == 0:
                assert "ÖN SINAV GEÇTİ" in proc.stdout
            else:
                assert "ÖN SINAV KALDI" in proc.stdout
                assert f"çıkış kodu {rc}" in proc.stdout

    def test_zero_settings_fails_without_running_python(self, tmp_path):
        # yalnız secret ve aile-dışı satırlar → süzgeçten sonra sıfır satır
        repo, py, env_file, out, tmpdir = _preflight_setup(
            tmp_path,
            settings="TV_WEBHOOK_SECRET=x\nTELEGRAM_CHAT_ID=y\n# yorum\n",
        )
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir, PREFLIGHT_ENV_FILE=env_file
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode != 0
        assert "pytest ÇALIŞTIRILMADI" in proc.stderr
        assert not (out / "argv").exists(), "yorumlayıcı çağrılmamalıydı"
        # boş dosya da aynı
        env_file.write_text("")
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode != 0
        assert not (out / "argv").exists()
        # hata yolunda da geçici dizin temizlenir
        assert list(tmpdir.iterdir()) == []

    def test_missing_env_file_fails(self, tmp_path):
        repo, py, _env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir,
            PREFLIGHT_ENV_FILE=tmp_path / "yok.txt",
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode != 0
        assert not (out / "argv").exists()

    def test_tmp_removed_by_default_and_kept_with_keep(self, tmp_path):
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir, PREFLIGHT_ENV_FILE=env_file
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert list(tmpdir.iterdir()) == [], "geçici kopya silinmeliydi"

        env["PREFLIGHT_KEEP"] = "1"
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        kept = list(tmpdir.iterdir())
        assert len(kept) == 1
        assert str(kept[0] / "copy") in proc.stdout
        assert (kept[0] / "copy" / ".env").is_file()
        # başarısız pytest'te de (varsayılan) temizlenir
        env.pop("PREFLIGHT_KEEP")
        for p in kept:
            subprocess.run(["rm", "-rf", str(p)], check=True)
        env["FAKE_PY_RC"] = "1"
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 1
        assert list(tmpdir.iterdir()) == []

    def test_cleanup_only_removes_own_directory(self, tmp_path):
        """TMPDIR'deki başka içerik ve boş/ayarsız yol hedef alınamaz."""
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        bystander = tmpdir / "baska-dizin"
        bystander.mkdir()
        (bystander / "dosya.txt").write_text("dokunma")
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir, PREFLIGHT_ENV_FILE=env_file
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (bystander / "dosya.txt").read_text() == "dokunma"
        text = PREFLIGHT_SH.read_text()
        # rm yalnız sahiplik işareti + boş olmayan TMP korumasıyla çağrılır
        assert '[ -n "${TMP:-}" ]' in text
        assert ".preflight-owned" in text

    def test_ssh_filters_server_side_and_uses_batchmode(self, tmp_path):
        repo, py, _env_file, out, tmpdir = _preflight_setup(tmp_path)
        bin_dir = tmp_path / "fake_ssh_bin"
        bin_dir.mkdir()
        ssh_log = tmp_path / "ssh_args.log"
        canned = tmp_path / "canned_remote_output.txt"
        # sahte sunucu süzmeyi YAPMAZ (kötü niyetli/hatalı sunucu): yerel savunma da sınanır
        canned.write_text(PREFLIGHT_SETTINGS)
        _write_executable(
            bin_dir / "ssh",
            r"""#!/usr/bin/env bash
{ echo "=== ssh call ==="; for a in "$@"; do printf '%s\n' "$a"; done; } >> "$SSH_CALL_LOG"
cat "$CANNED_REMOTE_OUTPUT"
exit 0
""",
        )
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir,
            SSH_CALL_LOG=ssh_log, CANNED_REMOTE_OUTPUT=canned,
            REMOTE_ENV="/srv/test/.env",
        )
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        proc = _run_preflight(tmp_path, env, ["myhost"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        calls = ssh_log.read_text().splitlines()
        assert calls[0] == "=== ssh call ==="
        assert calls[1:3] == ["-o", "BatchMode=yes"]
        assert calls[3] == "myhost"
        remote_cmd = "\n".join(calls[4:])
        # anahtar-aile süzgeci ve secret-adı süzgeci UZAK komutun içinde
        assert "(BINANCE_|SCALPER_|TV_|RISK_|FOLLOWER_|BOT_MODE)" in remote_cmd
        assert "(SECRET|KEY|TOKEN|PASS|BIND_IP)" in remote_cmd
        # büyük/küçük harf farksız süzgeç (-i) sunucu tarafında da var
        assert "grep -iE" in remote_cmd and "grep -viE" in remote_cmd
        assert "/srv/test/.env" in remote_cmd
        # salt okunur: uzak komut yazma/silme içermez
        for bad in (" rm ", " mv ", " cp ", " tee ", ">>", " > "):
            assert bad not in remote_cmd, bad
        # sunucu süzmese bile yerel ikinci süzgeç secret'ları atar
        dotenv = (out / "dotenv").read_text()
        for val in PREFLIGHT_SECRET_VALUES + PREFLIGHT_OUT_OF_FAMILY_VALUES:
            assert val not in dotenv, val
        assert "SCALPER_STOP_MODE=atr" in dotenv
        assert "host myhost" in proc.stdout

    def test_ssh_default_host_is_awa_and_failure_stops(self, tmp_path):
        repo, py, _env_file, out, tmpdir = _preflight_setup(tmp_path)
        bin_dir = tmp_path / "fake_ssh_bin"
        bin_dir.mkdir()
        ssh_log = tmp_path / "ssh_args.log"
        _write_executable(
            bin_dir / "ssh",
            r"""#!/usr/bin/env bash
{ echo "=== ssh call ==="; for a in "$@"; do printf '%s\n' "$a"; done; } >> "$SSH_CALL_LOG"
exit 255
""",
        )
        env = _preflight_env(tmp_path, repo, py, out, tmpdir, SSH_CALL_LOG=ssh_log)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode != 0
        assert ssh_log.read_text().splitlines()[3] == "awa"
        assert not (out / "argv").exists(), "ssh hatasında pytest koşmamalı"
        assert list(tmpdir.iterdir()) == []

    def test_ssh_not_called_when_env_file_given(self, tmp_path):
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        bin_dir = tmp_path / "fake_ssh_bin"
        bin_dir.mkdir()
        ssh_log = tmp_path / "ssh_args.log"
        ssh_log.write_text("")
        _write_executable(
            bin_dir / "ssh",
            "#!/usr/bin/env bash\necho called >> \"$SSH_CALL_LOG\"\nexit 0\n",
        )
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir,
            PREFLIGHT_ENV_FILE=env_file, SSH_CALL_LOG=ssh_log,
        )
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert ssh_log.read_text() == ""

    def test_repo_own_env_and_ignored_dirs_never_copied(self, tmp_path):
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir,
            PREFLIGHT_ENV_FILE=env_file, PREFLIGHT_KEEP="1",
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        copy = Path((out / "cwd").read_text().strip())
        assert PREFLIGHT_REPO_ENV_MARKER not in (out / "dotenv").read_text()
        assert not (copy / "logs").exists()
        for p in copy.rglob("*"):
            if p.is_file():
                assert PREFLIGHT_REPO_ENV_MARKER not in p.read_text(errors="ignore"), p
        # kopyanın .env'i repo .env'inin değil, script'in kurduğu dosyadır
        assert "SCALPER_LEVERAGE=99" not in (out / "dotenv").read_text()

    def test_repo_env_not_copied_even_if_tracked(self, tmp_path):
        """Biri .env'i yanlışlıkla izlese bile içeriği kopyaya GİRMEZ."""
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        subprocess.run(["git", "-C", str(repo), "add", "-f", ".env"], check=True)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir, PREFLIGHT_ENV_FILE=env_file
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert PREFLIGHT_REPO_ENV_MARKER not in (out / "dotenv").read_text()

    def test_caller_environment_settings_are_scrubbed(self, tmp_path):
        """Çağıranın kabuğundaki SCALPER_* vb. .env'i ezmemeli (sunucu adımı çıplak oturum)."""
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir,
            PREFLIGHT_ENV_FILE=env_file,
            SCALPER_LEVERAGE="77", BINANCE_API_KEY="callerkey", BOT_MODE="follower",
            KEEP_ME="yes",
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        environ = (out / "environ").read_text()
        for name in ("SCALPER_LEVERAGE=", "BINANCE_API_KEY=", "BOT_MODE="):
            assert name not in environ, name
        assert "KEEP_ME=yes" in environ
        assert "PYTHONDONTWRITEBYTECODE=1" in environ

    def test_caller_log_dir_is_scrubbed(self, tmp_path):
        """TRADINGBOT_LOG_DIR dışa aktarılmışsa testler çağıranın dizinine log yazardı."""
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir,
            PREFLIGHT_ENV_FILE=env_file,
            TRADINGBOT_LOG_DIR=tmp_path / "caller_logs",
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "TRADINGBOT_LOG_DIR=" not in (out / "environ").read_text()

    def test_host_starting_with_dash_is_rejected_before_ssh(self, tmp_path):
        """'-oProxyCommand=…' gibi bir host ssh'a SEÇENEK olarak geçmemeli."""
        repo, py, _env_file, out, tmpdir = _preflight_setup(tmp_path)
        bin_dir = tmp_path / "fake_ssh_bin"
        bin_dir.mkdir()
        ssh_log = tmp_path / "ssh_args.log"
        _write_executable(
            bin_dir / "ssh",
            '#!/usr/bin/env bash\necho called >> "$SSH_CALL_LOG"\nexit 0\n',
        )
        env = _preflight_env(tmp_path, repo, py, out, tmpdir, SSH_CALL_LOG=ssh_log)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        proc = _run_preflight(tmp_path, env, ["-oProxyCommand=true"])
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "host" in proc.stderr
        assert not ssh_log.exists(), "ssh çağrılmamalıydı"
        assert not (out / "argv").exists(), "yorumlayıcı çalışmamalıydı"

    def test_env_file_source_is_never_copied_raw_nor_deleted(self, tmp_path):
        """PREFLIGHT_ENV_FILE süzülmeden geçici dizine yazılmaz ve kaynağın kendisi silinmez."""
        repo, py, env_file, out, tmpdir = _preflight_setup(tmp_path)
        env = _preflight_env(
            tmp_path, repo, py, out, tmpdir,
            PREFLIGHT_ENV_FILE=env_file, PREFLIGHT_KEEP="1",
        )
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        kept = [p for p in tmpdir.rglob("*") if p.is_file()]
        assert kept, "KEEP=1 iken geçici kopya kalmalı"
        for path in kept:
            text = path.read_text(errors="replace")
            for val in PREFLIGHT_SECRET_VALUES + PREFLIGHT_OUT_OF_FAMILY_VALUES:
                assert val not in text, (str(path), val)
        assert env_file.read_text() == PREFLIGHT_SETTINGS

    def test_real_repo_copy_has_no_env_venv_data_logs(self, tmp_path):
        """Gerçek ağaç (PREFLIGHT_REPO'suz): kopyada .venv/data/logs yok; .env script'in kurduğu."""
        # Ön sınavın kendi temiz kopyasında .git YOKTUR (kasıtlı); orada bu test atlanır,
        # sunucudaki deploy testinde ve yerelde koşar.
        inside = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        if inside.returncode != 0:
            pytest.skip("repo kökü git çalışma ağacı değil (ön sınav kopyası)")
        py = _preflight_fake_python(tmp_path)
        env_file = tmp_path / "server_env.txt"
        env_file.write_text("SCALPER_STOP_MODE=atr\n")
        out = tmp_path / "py_out"
        out.mkdir()
        tmpdir = tmp_path / "tmp"
        tmpdir.mkdir()
        env = _preflight_env(
            tmp_path, REPO_ROOT, py, out, tmpdir,
            PREFLIGHT_ENV_FILE=env_file, PREFLIGHT_KEEP="1",
        )
        env.pop("PREFLIGHT_REPO")  # gerçek varsayılan yol
        proc = _run_preflight(tmp_path, env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        copy = Path((out / "cwd").read_text().strip())
        for name in (".venv", "data", "logs", ".git"):
            assert not (copy / name).exists(), name
        assert (copy / "scripts" / "preflight_server_env.sh").exists()
        dotenv = (out / "dotenv").read_text()
        assert dotenv.startswith((REPO_ROOT / "env.example").read_text())
        assert dotenv.splitlines()[-2:] == [
            "TV_WEBHOOK_SECRET=dummy-preflight",
            "RISK_EVENT_SECRET=dummy-preflight",
        ]
        assert "SCALPER_STOP_MODE=atr" in dotenv.splitlines()
