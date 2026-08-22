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
import sys
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


class TestInvalidRingValue:
    def test_unknown_ring_refused(self, tmp_path):
        proc, ssh_log = _run_deploy(tmp_path, ["awa", "v1.2.0", "--ring", "prod"])
        assert proc.returncode != 0
        assert "geçersiz --ring" in proc.stderr
        assert _ssh_call_count(ssh_log) == 0


# ── server_deploy.sh: mainnet .env ön kontrolü (statik/metin doğrulaması) ───


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
