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
  * **Olay döngüsü diske DOKUNMAZ** (D21-R3, düşmanca inceleme bulgusu 3).
    Motor yolundan çağrılan `append_soon` yalnız bir kuyruğa yazar (O(1),
    IO yok) ve gerçek `write()` AYRI bir arka plan iş parçacığında olur.
    Böylece `engine._entry_lock` altında ya da safety turunda yapılan bir
    kayıt, yavaş/dolu bir diskte TP1→BE, trailing ve kill-switch işlerini
    geciktiremez. Senkron `append` yalnız test/araç yollarında ve yazıcı iş
    parçacığının kendi içinde kullanılır.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.core.logger import app_logger

FILE_NAME = "trades.jsonl"
ARCHIVE_PREFIX = "trades-"
RETENTION_DAYS = 30

#: Kuyruk üst sınırı. Dolarsa YENİ satır düşürülür (eskisi korunur) ve durum
#: tek sefer WARNING'e yazılır: bir teşhis kaydı için sınırsız RAM tutmak,
#: kaydın kendisinden daha pahalıdır.
QUEUE_MAX = 2000

_error_logged = False
_overflow_logged = False
_queue: "queue.Queue[Tuple[str, Dict[str, Any], float]]" = queue.Queue(QUEUE_MAX)
_writer: Optional[threading.Thread] = None
_writer_lock = threading.Lock()
_dropped = 0


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


# --------------------------------------------------------------------------
# Kuyruk + ayrı yazıcı iş parçacığı (D21-R3)
# --------------------------------------------------------------------------

def append_soon(event: str, payload: Dict[str, Any], *, now: Optional[float] = None) -> bool:
    """Adli olayı KUYRUĞA koy; diske yazımı arka plan iş parçacığı yapar.

    Motor yolundan (executor/exits/engine) DAİMA bu çağrılır. Neden:
    `_finalize_position` `engine._entry_lock` altında koşar ve `_finalize_close`
    safety turunun ortasındadır; senkron bir `write()` orada diskin insafına
    kalmak demektir (D21-R3). Kuyruğa koymak sabit zamanlıdır ve ASLA hata
    yükseltmez.

    Döner: kuyruğa alındıysa True; kuyruk taştıysa False (satır düşer, çağıran
    ETKİLENMEZ). Yazımın diske indiğini görmek gerekiyorsa `drain()`.
    """
    now = time.time() if now is None else now
    try:
        # Kopyala: satır yazılana kadar geçen sürede çağıranın sözlüğü
        # değişirse kayıt onu DEĞİL, olay anını yansıtmalı.
        item = (str(event), dict(payload or {}), float(now))
        _queue.put_nowait(item)
    except queue.Full:
        _note_overflow()
        return False
    except Exception:  # pragma: no cover - kuyruk kurulumu bozulsa bile akış sürer
        return False
    _ensure_writer()
    return True


def _note_overflow() -> None:
    global _dropped, _overflow_logged
    _dropped += 1
    if not _overflow_logged:
        _overflow_logged = True
        app_logger.warning(
            f"⚠️ Adli kayıt kuyruğu doldu ({QUEUE_MAX}); yeni satırlar "
            f"düşürülüyor — bu uyarı bir kez loglanır, işlem akışı ETKİLENMEZ"
        )


def _ensure_writer() -> None:
    """Yazıcı iş parçacığını tembel başlat (daemon: süreçle birlikte ölür)."""
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    with _writer_lock:
        if _writer is not None and _writer.is_alive():
            return
        _writer = threading.Thread(
            target=_writer_loop, name="forensics-jsonl-writer", daemon=True
        )
        _writer.start()


def _writer_loop() -> None:  # pragma: no cover - iş parçacığı gövdesi
    while True:
        event, payload, now = _queue.get()
        try:
            append(event, payload, now=now)
        except Exception:
            # `append` zaten yutar; buraya yalnız monkeypatch'lenmiş bir
            # çift patlarsa düşeriz — yazıcı iş parçacığı ÖLMEMELİ.
            pass
        finally:
            _queue.task_done()


def drain(timeout: float = 5.0) -> bool:
    """Kuyruk boşalana kadar bekle (testler ve temiz kapanış için).

    Döner: kuyruk boşaldıysa True, süre dolduysa False. Olay döngüsünü
    bloklar — yalnız test/kapanış yollarında çağrılmalıdır.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while _pending_count() and time.monotonic() < deadline:
        time.sleep(0.005)
    return not _pending_count()


def _pending_count() -> int:
    """İşlenmeyi bekleyen (kuyrukta + yazılmakta olan) satır sayısı."""
    pending = getattr(_queue, "unfinished_tasks", None)
    if pending is None:  # pragma: no cover - CPython dışı yorumlayıcı
        return _queue.qsize()
    return int(pending)


def queue_snapshot() -> Dict[str, Any]:
    """Teşhis: kuyrukta bekleyen ve taşma yüzünden düşen satır sayısı."""
    return {
        "pending": _pending_count(),
        "dropped": int(_dropped),
        "writer_alive": bool(_writer is not None and _writer.is_alive()),
    }


def reset_error_state() -> None:
    """Testler için: "bir kez uyar" bayrağını ve kuyruğu sıfırla."""
    global _error_logged, _overflow_logged, _dropped
    _error_logged = False
    _overflow_logged = False
    _dropped = 0
    while True:
        try:
            _queue.get_nowait()
        except queue.Empty:
            break
        else:
            _queue.task_done()
