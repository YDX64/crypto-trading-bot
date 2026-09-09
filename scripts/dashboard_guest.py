#!/usr/bin/env python3
"""Provision only the `misafir` account on AWA's existing read-only monitor.

Deploy this file normally before use. No bot/Nginx restart, proxy change, or
trading API is involved. Passwords travel through stdin and a private .env only.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import grp
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile

USER = "misafir"
URL = "https://157.180.97.188:9443/dashboard"
TARGET = Path("/etc/nginx/.htpasswd-tradingbot-ip")
BACKUPS = Path("/root/tradingbot-dashboard-auth-backups")
REMOTE_SCRIPT = "/opt/tradingbot-v2/scripts/dashboard_guest.py"


class GuestError(Exception):
    """Messages are fixed, safe codes; never include subprocess output/secrets."""


def _check_file(info: os.stat_result, uid: int, gid: int, mode: int) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode):
        raise GuestError("unsafe_file_metadata")


def _read_target(path: Path, uid: int, gid: int) -> tuple[bytes, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        _check_file(info, uid, gid, 0o640)
        data = handle.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise GuestError("auth_file_too_large")
    return data, info


def _users(data: bytes) -> set[bytes]:
    users: set[bytes] = set()
    for line in data.splitlines():
        if not line or line.startswith(b"#"):
            continue
        fields = line.split(b":")
        if len(fields) < 2 or not fields[0] or not fields[1] or fields[0] in users:
            raise GuestError("invalid_auth_file")
        users.add(fields[0])
    if b"efe" not in users:
        raise GuestError("owner_account_missing")
    return users


def _private_directory(path: Path, uid: int) -> None:
    # Only create the exact final directory, not an arbitrary parent hierarchy.
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise GuestError("unsafe_private_directory")


@contextmanager
def _lock(path: Path, uid: int):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600):
            raise GuestError("unsafe_lock_file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GuestError("auth_update_already_in_progress") from None
        yield
    finally:
        os.close(fd)


def _hash_password(password: bytes) -> bytes:
    if re.fullmatch(rb"[A-Za-z0-9_-]{32}", password) is None:
        raise GuestError("invalid_password_format")
    result = subprocess.run(
        ["/usr/bin/openssl", "passwd", "-6", "-stdin"],
        input=password + b"\n", capture_output=True, timeout=10, check=False,
    )
    hashed = result.stdout.strip()
    if result.returncode or re.fullmatch(rb"\$6\$[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}", hashed) is None:
        raise GuestError("password_hash_failed")
    return hashed


def _exclusive_write(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def change_guest(action: str, password: bytes | None, *, target: Path,
                 backups: Path, uid: int, gid: int) -> dict[str, str]:
    """Internal testable file operation. CLI supplies fixed production paths only."""
    if action not in {"add", "revoke"}:
        raise GuestError("invalid_action")
    parent_info = target.parent.lstat()
    if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != uid
            or stat.S_IMODE(parent_info.st_mode) & 0o022):
        raise GuestError("unsafe_auth_directory")
    with _lock(target.parent / ".tradingbot-monitor-guest.lock", uid):
        original, before = _read_target(target, uid, gid)
        users = _users(original)
        if action == "add":
            if USER.encode() in users:
                raise GuestError("guest_already_exists_no_rotation")
            hashed = _hash_password(password or b"")
            separator = b"" if original.endswith(b"\n") else b"\n"
            updated = original + separator + USER.encode() + b":" + hashed + b"\n"
        else:
            if USER.encode() not in users:
                return {"status": "absent", "user": USER}
            updated = b"".join(line for line in original.splitlines(keepends=True)
                               if line.split(b":", 1)[0] != USER.encode())
        _users(updated)  # Never publish a file without the existing owner.
        _private_directory(backups, uid)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backups / f"htpasswd-{stamp}-{secrets.token_hex(4)}.bak"
        _exclusive_write(backup, original, 0o600)
        fd, temporary = tempfile.mkstemp(prefix=".tradingbot-guest-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchown(handle.fileno(), uid, gid)
                os.fchmod(handle.fileno(), 0o640)
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            current, now = _read_target(target, uid, gid)
            if current != original or _identity(now) != _identity(before):
                raise GuestError("auth_file_changed_concurrently")
            os.replace(temporary, target)
            _sync_directory(target.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"status": "added" if action == "add" else "revoked", "user": USER,
            "backup": str(backup)}


def save_credentials(path: Path, password: str) -> None:
    if not path.is_absolute() or path.suffix != ".env":
        raise GuestError("output_requires_absolute_env_path")
    _private_directory(path.parent, os.getuid())
    data = (f"DASHBOARD_URL={URL}\nDASHBOARD_USERNAME={USER}\n"
            f"DASHBOARD_PASSWORD={password}\n").encode()
    _exclusive_write(path, data, 0o600)


def remote_change(action: str, password: str | None = None) -> dict[str, str]:
    if action not in {"add", "revoke"}:
        raise GuestError("invalid_action")
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "awa",
         "/usr/bin/python3", REMOTE_SCRIPT, f"_server-{action}"],
        input=((password + "\n") if password else "").encode(),
        capture_output=True, timeout=45, check=False,
    )
    # Never relay raw remote output, including unexpected diagnostics.
    if result.returncode:
        raise GuestError("remote_failed_verify_state_before_retry")
    try:
        response = json.loads(result.stdout)
        allowed = {"added"} if action == "add" else {"revoked", "absent"}
        if response.get("user") != USER or response.get("status") not in allowed:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise GuestError("invalid_remote_response_verify_state_before_retry") from None
    return {"status": response["status"], "user": USER}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="Create guest; save a NEW private local .env")
    add.add_argument("--output", required=True, type=Path)
    commands.add_parser("revoke", help="Remove only misafir, preserving every other user")
    commands.add_parser("_server-add", help=argparse.SUPPRESS)
    commands.add_parser("_server-revoke", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.command.startswith("_server-"):
            if os.geteuid() != 0:
                raise GuestError("server_requires_root")
            action = args.command.removeprefix("_server-")
            password = sys.stdin.buffer.read(65).rstrip(b"\n") if action == "add" else None
            result = change_guest(action, password, target=TARGET, backups=BACKUPS,
                                  uid=0, gid=grp.getgrnam("www-data").gr_gid)
        elif args.command == "add":
            password_text = secrets.token_urlsafe(24)
            save_credentials(args.output, password_text)
            # Keep the private .env even if SSH response is lost after success.
            result = remote_change("add", password_text)
            result["credential_file"] = str(args.output)
            result["url"] = URL
        else:
            result = remote_change("revoke")
        print(json.dumps(result))
        return 0
    except GuestError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    except (OSError, subprocess.SubprocessError, KeyError):
        print(json.dumps({"error": "operation_failed_verify_state_before_retry"}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
