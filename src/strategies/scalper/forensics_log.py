"""`logs/trades.jsonl` — adli kaydın append-only olay akışı (D21).

Neden loguru DEĞİL: `logs/trades.log` insan-okur bir denetim izidir ve
biçimi (zaman | seviye | mesaj) makine ayrıştırması için sözleşme değildir.
Adli kayıt satır başına TEK ve TAM bir JSON nesnesidir; `jq` ile doğrudan
işlenebilir:

    jq -c 'select(.event=="exit" and (.verdict|index("noise_stop")))' logs/trades.jsonl

Kurallar:
  * **Append-only.** Satır asla güncellenmez; `entry` / `exit` / `postmortem`
    aynı `trade_id` için ARDIŞIK üç satırdır. Kaydın "son hâli" DB'deki
    `scalp_trades.forensics` sütunudur (bkz. `tracker.py`).
  * **Günlük rotasyon, 30 gün saklama.** Dosyanın son yazım günü değiştiyse
    mevcut dosya `trades-<YYYY-MM-DD>.jsonl` olarak arşivlenir ve 30 günden
    eski arşivler silinir.
  * **Secret YOK.** Buraya yalnız `forensics.build_*` çıktıları ve sembol/
    işlem kimlikleri yazılır; `.env` değerleri, anahtarlar ve webhook
    secret'ları ASLA.
  * **Fail-safe.** Yazım hatası çağıranı ASLA etkilemez; hata tek sefer
    WARNING olarak loglanır (disk dolu bir işlemi engellememeli).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.logger import app_logger

FILE_NAME = "trades.jsonl"
ARCHIVE_PREFIX = "trades-"
RETENTION_DAYS = 30

_error_logged = False


def log_dir() -> Path:
    """Log dizini — `src/core/logger.py` ile AYNI kural (testler kirletmesin)."""
    return Path(os.environ.get("TRADINGBOT_LOG_DIR") or "logs")


def log_path() -> Path:
    return log_dir() / FILE_NAME


def _utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _rotate(path: Path, now: float) -> None:
    """Dosyanın son yazımı DÜNDEN önceyse arşivle; eski arşivleri buda."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return
    last_day = _utc_day(stat.st_mtime)
    today = _utc_day(now)
    if last_day == today:
        return
    archive = path.with_name(f"{ARCHIVE_PREFIX}{last_day}.jsonl")
    if archive.exists():
        # Aynı gün için ikinci bir arşiv: içeriği kaybetme, sona ekle.
        with archive.open("a", encoding="utf-8") as dst, path.open(
            "r", encoding="utf-8"
        ) as src:
            dst.write(src.read())
        path.unlink()
    else:
        path.replace(archive)
    _prune(path.parent, now)


def _prune(directory: Path, now: float) -> None:
    cutoff = (
        datetime.fromtimestamp(now, tz=timezone.utc) - timedelta(days=RETENTION_DAYS)
    ).strftime("%Y-%m-%d")
    for item in directory.glob(f"{ARCHIVE_PREFIX}*.jsonl"):
        day = item.name[len(ARCHIVE_PREFIX):-len(".jsonl")]
        if len(day) == 10 and day < cutoff:
            try:
                item.unlink()
            except OSError:
                pass


def append(event: str, payload: Dict[str, Any], *, now: Optional[float] = None) -> bool:
    """Tek bir adli olayı JSONL'e ekle. Hata çağıranı ASLA etkilemez.

    Döner: yazıldıysa True (testler için), aksi hâlde False.
    """
    global _error_logged
    now = time.time() if now is None else now
    record = {
        "ts": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "event": str(event),
    }
    record.update(payload or {})
    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / FILE_NAME
        _rotate(path, now)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True
    except Exception as e:  # pragma: no cover - disk/izin arızası
        if not _error_logged:
            _error_logged = True
            app_logger.warning(
                f"⚠️ Adli kayıt akışı ({FILE_NAME}) yazılamadı: {e} — "
                f"bu uyarı bir kez loglanır, işlem akışı ETKİLENMEZ"
            )
        return False


def reset_error_state() -> None:
    """Testler için: "bir kez uyar" bayrağını sıfırla."""
    global _error_logged
    _error_logged = False
