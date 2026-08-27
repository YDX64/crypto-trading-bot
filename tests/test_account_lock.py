"""D28 Binance hesabı tek-yönetici süreç kilidi."""

import json

import pytest

from src.core.account_lock import AccountLockError, TradingAccountLock


def _acquire(tmp_path, key="same-account"):
    return TradingAccountLock.acquire(
        api_key=key,
        lock_dir=str(tmp_path),
        app_env="test",
        bot_mode="scalper",
        network="testnet",
    )


def test_ayni_hesabin_ikinci_yoneticisi_fail_closed_reddedilir(tmp_path):
    first = _acquire(tmp_path)
    try:
        with pytest.raises(AccountLockError, match="başka bir tradingbot"):
            _acquire(tmp_path)
    finally:
        first.release()


def test_sahip_kapaninca_kilit_yeniden_alinir(tmp_path):
    first = _acquire(tmp_path)
    path = first.path
    first.release()
    second = _acquire(tmp_path)
    try:
        assert second.held is True
        assert second.path == path
    finally:
        second.release()


def test_farkli_hesaplar_birbirini_engellemez(tmp_path):
    first = _acquire(tmp_path, "account-a")
    second = _acquire(tmp_path, "account-b")
    try:
        assert first.held and second.held
        assert first.path != second.path
    finally:
        second.release()
        first.release()


def test_kilit_dosyasi_ham_api_anahtarini_asla_yazmaz(tmp_path):
    secret_key = "never-print-this-api-key"
    lock = _acquire(tmp_path, secret_key)
    try:
        raw = lock.path.read_text(encoding="utf-8")
        metadata = json.loads(raw)
        assert secret_key not in raw
        assert secret_key not in lock.path.name
        assert metadata["pid"] > 0
        assert metadata["network"] == "testnet"
    finally:
        lock.release()


def test_goreli_kilit_dizini_reddedilir():
    with pytest.raises(AccountLockError, match="mutlak"):
        TradingAccountLock.acquire(
            api_key="x",
            lock_dir="relative/path",
            app_env="test",
            bot_mode="scalper",
            network="testnet",
        )
