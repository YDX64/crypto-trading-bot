"""Fresh installs must have one owner of the telegram import namespace."""

from pathlib import Path


def test_telegram_namespace_has_one_distribution():
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
    names = {line.split("==")[0].strip().lower() for line in requirements.splitlines()}
    assert "python-telegram-bot" in names
    assert "telegram" not in names
    from telegram import Update
    from telegram.ext import Application

    assert Update is not None and Application is not None
