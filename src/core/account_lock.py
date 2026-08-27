"""Binance hesabı için süreçler-arası tek-yönetici kilidi.

Aynı API anahtarıyla iki motor ayrı DB ve süreç-içi rezervasyonlar kullanırsa
birbirini göremez; aynı sembolde ters yön açıp aynı pozisyonu iki kez yönetebilir.
Bu kilit emir gönderebilen uygulama yaşam döngüsünü hesap başına tek süreçle
sınırlar. Dosya kilidi süreç ölürse kernel tarafından otomatik bırakılır.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class AccountLockError(RuntimeError):
    """Aynı Binance hesabını başka bir emir motoru zaten yönetiyor."""


@dataclass
class TradingAccountLock:
    """`flock(2)` tabanlı, idempotent bırakılabilen hesap kilidi."""

    path: Path
    _fd: Optional[int]
    metadata: Dict[str, Any]

    @classmethod
    def acquire(
        cls,
        *,
        api_key: str,
        lock_dir: str,
        app_env: str,
        bot_mode: str,
        network: str,
    ) -> "TradingAccountLock":
        key = str(api_key or "").strip()
        if not key:
            raise AccountLockError("Binance API anahtarı boş; hesap kilidi kurulamadı")

        directory = Path(str(lock_dir or "")).expanduser()
        if not directory.is_absolute():
            raise AccountLockError("TRADING_ACCOUNT_LOCK_DIR mutlak bir yol olmalı")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Ham anahtar dosya adına/loga ASLA girmez. API anahtarı aynı hesabın
        # kararlı kimliğidir; base URL'yi anahtara katmıyoruz çünkü Binance
        # testnet/demo hostları aynı hesabı gösterebilir.
        fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
        path = directory / f"binance-account-{fingerprint}.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = ""
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                holder = os.read(fd, 4096).decode("utf-8", errors="replace").strip()
            except OSError:
                holder = ""
            os.close(fd)
            detail = f" Mevcut yönetici: {holder}" if holder else ""
            raise AccountLockError(
                "AYNI Binance hesabını başka bir tradingbot süreci yönetiyor; "
                f"ikinci motor fail-closed durduruldu.{detail}"
            ) from exc
        except Exception:
            os.close(fd)
            raise

        metadata: Dict[str, Any] = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "app_env": str(app_env or ""),
            "bot_mode": str(bot_mode or ""),
            "network": str(network or ""),
        }
        encoded = (json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, encoded)
        os.fsync(fd)
        return cls(path=path, _fd=fd, metadata=metadata)

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        # Dosyayı BİLİNÇLİ olarak silmiyoruz. Unlink + yeniden create, bekleyen
        # süreçlerin farklı inode'ları kilitleyip ikisinin de içeri girmesine
        # yol açabilir. İçerik yalnız son sahip hakkında secretsız teşhistir.

    def __enter__(self) -> "TradingAccountLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
