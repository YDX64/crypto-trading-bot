"""Guest auth tooling: temporary files only; no network or real credentials."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest


SPEC = importlib.util.spec_from_file_location(
    "dashboard_guest", Path(__file__).parents[1] / "scripts/dashboard_guest.py"
)
guest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guest)

PASSWORD = "P" * 32
HASH = b"$6$testSalt$" + b"A" * 86
OWNER = b"efe:$6$owner$ownerhash\nother:otherhash:comment\n"


@pytest.fixture
def files(tmp_path, monkeypatch):
    target = tmp_path / "auth"
    target.write_bytes(OWNER)
    target.chmod(0o640)
    monkeypatch.setattr(guest, "_hash_password", lambda password: HASH)
    return dict(target=target, backups=tmp_path / "backups", uid=os.getuid(), gid=os.getgid())


def test_add_preserves_users_and_permissions_and_private_backup(files):
    result = guest.change_guest("add", PASSWORD.encode(), **files)
    assert result["status"] == "added"
    assert files["target"].read_bytes() == OWNER + b"misafir:" + HASH + b"\n"
    assert stat.S_IMODE(files["target"].stat().st_mode) == 0o640
    backup = Path(result["backup"])
    assert backup.read_bytes() == OWNER
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.parent.stat().st_mode) == 0o700


def test_refuses_existing_guest_without_rotating_or_backup(files):
    original = OWNER + b"misafir:existinghash\n"
    files["target"].write_bytes(original)
    with pytest.raises(guest.GuestError, match="already_exists"):
        guest.change_guest("add", PASSWORD.encode(), **files)
    assert files["target"].read_bytes() == original
    assert not files["backups"].exists()


def test_revoke_only_guest_and_is_idempotent(files):
    files["target"].write_bytes(OWNER + b"misafir:existinghash\n")
    assert guest.change_guest("revoke", None, **files)["status"] == "revoked"
    assert files["target"].read_bytes() == OWNER
    assert guest.change_guest("revoke", None, **files)["status"] == "absent"
    assert len(list(files["backups"].iterdir())) == 1


def test_add_handles_missing_final_newline(files):
    files["target"].write_bytes(OWNER.rstrip(b"\n"))
    guest.change_guest("add", PASSWORD.encode(), **files)
    assert files["target"].read_bytes() == OWNER + b"misafir:" + HASH + b"\n"


@pytest.mark.parametrize("original", [b"other:hash\n", b"efe:hash\nefe:duplicate\n", b"efe:\n"])
def test_rejects_invalid_or_missing_owner(files, original):
    files["target"].write_bytes(original)
    with pytest.raises(guest.GuestError):
        guest.change_guest("add", PASSWORD.encode(), **files)
    assert files["target"].read_bytes() == original


def test_refuses_target_symlink(files, tmp_path):
    original = files["target"]
    linked = tmp_path / "linked"
    linked.symlink_to(original)
    files["target"] = linked
    with pytest.raises(OSError):
        guest.change_guest("add", PASSWORD.encode(), **files)
    assert original.read_bytes() == OWNER


def test_refuses_writable_auth_directory(files):
    files["target"].parent.chmod(0o777)
    with pytest.raises(guest.GuestError, match="auth_directory"):
        guest.change_guest("add", PASSWORD.encode(), **files)
    assert files["target"].read_bytes() == OWNER


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o666])
def test_refuses_wrong_target_permissions(files, mode):
    files["target"].chmod(mode)
    with pytest.raises(guest.GuestError, match="metadata"):
        guest.change_guest("add", PASSWORD.encode(), **files)


def test_refuses_hardlinked_target(files, tmp_path):
    os.link(files["target"], tmp_path / "linked")
    with pytest.raises(guest.GuestError, match="metadata"):
        guest.change_guest("add", PASSWORD.encode(), **files)


def test_concurrent_cooperating_writer_fails_fast(files):
    lock = files["target"].parent / ".tradingbot-monitor-guest.lock"
    with guest._lock(lock, files["uid"]):
        with pytest.raises(guest.GuestError, match="already_in_progress"):
            guest.change_guest("add", PASSWORD.encode(), **files)
    assert files["target"].read_bytes() == OWNER


def test_refuses_symlink_lock(files, tmp_path):
    lock = files["target"].parent / ".tradingbot-monitor-guest.lock"
    lock.symlink_to(files["target"])
    with pytest.raises(OSError):
        guest.change_guest("add", PASSWORD.encode(), **files)
    assert files["target"].read_bytes() == OWNER


def test_detects_noncooperating_concurrent_writer(files, monkeypatch):
    read = guest._read_target
    count = 0

    def concurrent_read(*args):
        nonlocal count
        count += 1
        if count == 2:
            files["target"].write_bytes(OWNER + b"new_owner:otherhash\n")
        return read(*args)

    monkeypatch.setattr(guest, "_read_target", concurrent_read)
    with pytest.raises(guest.GuestError, match="concurrently"):
        guest.change_guest("add", PASSWORD.encode(), **files)
    assert files["target"].read_bytes() == OWNER + b"new_owner:otherhash\n"
    assert not list(files["target"].parent.glob(".tradingbot-guest-*"))


def test_hash_password_only_stdin(monkeypatch):
    def run(command, **kwargs):
        assert PASSWORD not in " ".join(command)
        assert kwargs["input"] == PASSWORD.encode() + b"\n"
        assert command == ["/usr/bin/openssl", "passwd", "-6", "-stdin"]
        return subprocess.CompletedProcess(command, 0, HASH + b"\n", b"")

    monkeypatch.setattr(guest.subprocess, "run", run)
    assert guest._hash_password(PASSWORD.encode()) == HASH


@pytest.mark.parametrize("value", [b"short", b"P" * 33, b"P" * 31 + b"\n", b"P" * 31 + b":"])
def test_hash_password_rejects_bad_input(value):
    with pytest.raises(guest.GuestError, match="password_format"):
        guest._hash_password(value)


def test_credentials_private_no_overwrite(tmp_path):
    output = tmp_path / "private" / "access.env"
    guest.save_credentials(output, PASSWORD)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    assert "DASHBOARD_PASSWORD=" + PASSWORD in output.read_text()
    with pytest.raises(FileExistsError):
        guest.save_credentials(output, "different")
    assert "different" not in output.read_text()


def test_credentials_refuse_public_parent(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(guest.GuestError, match="private_directory"):
        guest.save_credentials(tmp_path / "access.env", PASSWORD)


def test_remote_secret_only_stdin_and_ack_sanitized(monkeypatch):
    def run(command, **kwargs):
        assert PASSWORD not in " ".join(command)
        assert kwargs["input"] == PASSWORD.encode() + b"\n"
        assert command[-1] == "_server-add"
        return subprocess.CompletedProcess(command, 0, json.dumps(
            {"status": "added", "user": "misafir", "unexpected": PASSWORD}
        ).encode(), b"")

    monkeypatch.setattr(guest.subprocess, "run", run)
    assert guest.remote_change("add", PASSWORD) == {"status": "added", "user": "misafir"}


def test_failed_remote_never_prints_raw_output_keeps_credentials(tmp_path, monkeypatch, capsys):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, PASSWORD.encode(), PASSWORD.encode())

    monkeypatch.setattr(guest.subprocess, "run", run)
    monkeypatch.setattr(guest.secrets, "token_urlsafe", lambda count: PASSWORD)
    output = tmp_path / "private" / "access.env"
    assert guest.main(["add", "--output", str(output)]) == 1
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    assert "verify_state_before_retry" in captured.err
    assert output.exists()


def test_cli_success_prints_metadata_never_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guest.secrets, "token_urlsafe", lambda count: PASSWORD)
    monkeypatch.setattr(guest, "remote_change", lambda *args: {"status": "added", "user": "misafir"})
    output = tmp_path / "private" / "access.env"
    assert guest.main(["add", "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    assert json.loads(captured.out)["credential_file"] == str(output)
