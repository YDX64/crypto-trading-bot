"""Risk-olayı halt deposu (D10 semantiği, takipçi halkası için).

``POST /risk-event`` kanalı iki halkada da AYNI davranmalıdır: TTL'li,
fail-closed, dosya + RAM latch. Scalper motorundaki uygulama
(``engine._risk_event_halt_snapshot`` / ``_persist_risk_event_halt``) bu
görevde BYTE-FOR-BYTE korunması gerektiği için oradan çıkarılıp paylaşılmadı;
burada AYNI sözleşmeyi uygulayan bağımsız bir sınıf vardır.

Sözleşme (docs/INTEGRATIONS.md §3 ile birebir):
  * ``state/...json``: ``{version, reason, source, until_ts, created_at}``
    atomik yazılır (tmp + fsync + os.replace + dizin fsync).
  * Bozuk/okunamayan dosya **fail-closed HALT AKTİF** sayılır.
  * TTL dolunca halt kendiliğinden kalkar.
  * Diske yazılamayan halt RAM'de OTORİTER kalır (``persisted=False`` ile
    raporlanır) — persist edilemeyen bir halt "aktif değil" gibi davranamaz.
  * ``resume`` yalnız bu dosyayı siler.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.core.logger import app_logger

_CACHE_TTL_SECONDS = 1.0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RiskEventHaltStore:
    """Dosya + RAM latch tabanlı, TTL'li giriş kilidi."""

    def __init__(self, path: Optional[str], logger: Any = None):
        self.path: Optional[Path] = Path(path).expanduser() if path else None
        self.logger = logger or app_logger
        self._cache: Optional[Tuple[float, Dict[str, Any]]] = None
        self._ram: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------

    def snapshot(self, *, force: bool = False) -> Dict[str, Any]:
        """Halt durumunu oku (kısa TTL önbellekli), fail-closed."""
        now_mono = time.monotonic()
        if (
            not force
            and self._cache is not None
            and (now_mono - self._cache[0]) < _CACHE_TTL_SECONDS
        ):
            return self._cache[1]

        if self.path is None or not self.path.exists():
            snapshot: Dict[str, Any] = {
                "active": False,
                "reason": None,
                "source": None,
                "until_ts": None,
            }
        else:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("risk_event_halt şeması geçersiz (dict değil)")
                until_ts = float(payload["until_ts"])
                reason = str(payload.get("reason") or "risk olayı")
                raw_source = payload.get("source")
                source = str(raw_source) if raw_source not in (None, "") else None
            except Exception as exc:
                snapshot = {
                    "active": True,
                    "reason": (
                        f"risk_event_halt dosyası okunamadı: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "source": None,
                    "until_ts": None,
                }
            else:
                if until_ts <= time.time():
                    snapshot = {
                        "active": False,
                        "reason": None,
                        "source": None,
                        "until_ts": until_ts,
                    }
                else:
                    snapshot = {
                        "active": True,
                        "reason": reason,
                        "source": source,
                        "until_ts": until_ts,
                    }

        ram = self._ram
        if ram is not None:
            ram_until = float(ram.get("until_ts") or 0.0)
            if ram_until <= time.time():
                self._ram = None
            else:
                ram_snapshot = {
                    "active": True,
                    "reason": ram.get("reason"),
                    "source": ram.get("source"),
                    "until_ts": ram_until,
                }
                if not snapshot.get("active"):
                    snapshot = ram_snapshot
                elif (
                    snapshot.get("until_ts") is not None
                    and float(snapshot["until_ts"]) < ram_until
                ):
                    snapshot = ram_snapshot

        self._cache = (now_mono, snapshot)
        return snapshot

    @property
    def active(self) -> bool:
        return bool(self.snapshot().get("active"))

    # ------------------------------------------------------------------

    def halt(
        self, *, reason: str, source: Optional[str], ttl_minutes: int
    ) -> Dict[str, Any]:
        """Halt kur. RAM latch ÖNCE, sonra disk (yazılamazsa persisted=False)."""
        ttl_minutes = max(1, min(int(ttl_minutes), 1440))
        until_ts = time.time() + ttl_minutes * 60.0
        self._ram = {"reason": reason, "source": source, "until_ts": until_ts}
        self._cache = None
        persisted = self._persist(reason=reason, source=source, until_ts=until_ts)
        self.logger.bind(trade=True).critical(
            f"🚨 RİSK-OLAYI HALT (takipçi): yeni girişler durduruldu — "
            f"neden='{reason}' kaynak={source or '-'} ttl={ttl_minutes}dk "
            f"kalıcı={persisted}"
        )
        snapshot = dict(self.snapshot(force=True))
        snapshot["persisted"] = persisted
        return snapshot

    def resume(self) -> Dict[str, Any]:
        if self.path is not None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                self.logger.error(
                    f"⚠️ risk-event halt dosyası silinemedi ({self.path}): {exc}"
                )
        self._ram = None
        self._cache = None
        self.logger.warning(
            "✅ RİSK-OLAYI RESUME (takipçi): giriş kilidi kaldırıldı",
            extra={"trade": True},
        )
        return self.snapshot(force=True)

    # ------------------------------------------------------------------

    def _persist(
        self, *, reason: str, source: Optional[str], until_ts: float
    ) -> bool:
        if self.path is None:
            return False
        tmp_path: Optional[Path] = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            payload = {
                "version": 1,
                "reason": reason,
                "source": source,
                "until_ts": until_ts,
                "created_at": _utcnow_iso(),
            }
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
            try:
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            return True
        except Exception as exc:
            self.logger.critical(
                f"🚨 risk-event halt RAM'de aktif ancak kalıcılaştırılamadı — "
                f"halt yalnız RAM'de ({self.path}): {exc}",
                extra={"trade": True},
            )
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        finally:
            self._cache = None
