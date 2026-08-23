"""TradingView ÇIKIŞ + YAPI/DÖNÜŞ olay kanalı (D19, 2026-08-23).

Kullanıcı vizyonu: bugüne kadar TradingView'den bota YALNIZ "gir" oyu
geliyordu (`/tv-signal` → `TvConfluence` → `engine.external_signal`).
Göstergelerin asıl bilgisi ise çoğu zaman ÇIKIŞTA ve YAPIDA: LuxAlgo
S&O "Exit Signal" (mavi X), S&O "Trend Catcher/Tracer Up|Down", Price
Action Concepts "Bullish/Bearish S-CHOCH", AlgoPro "TP1 Hit". Bu modül o
olayları saklar; motor onları (a) rejime ters giriş kapısı ve (b) açık
pozisyonda BE/kapatma tetikleyicisi olarak kullanır.

TASARIM SÖZLEŞMESİ (bozulması "motor değişikliği"dir):
- Olaylar **sağlamaya (TvConfluence) HİÇ girmez**. `kind != entry` olan bir
  webhook isteği oy DEĞİLDİR; mevcut 49 giriş alarmının davranışı
  değişmeden korunur.
- Bu modül **karar vermez**, yalnız durum tutar. Kapı/çıkış kararı
  `ScalperEngine`'dedir ve `SCALPER_TV_EVENTS_MODE` ile üç kademelidir
  (off / shadow / active).
- Yön semantiği iki farklı şeydir:
    * `choch` / `trend` → olayın yönü YAPININ yönüdür
      (bullish/up → BULL, bearish/down → BEAR).
    * `exit` / `tp1`   → olayın yönü (VARSA) KAPATILACAK POZİSYONUN
      yönüdür; "yapı yukarı döndü" demek DEĞİLDİR. LuxAlgo S&O ve AlgoPro'nun
      gerçek alarm koşulları ("Exit Signal", "🎯 TP1 Hit") YÖNSÜZDÜR — bu
      yüzden `direction=None` normaldir ve "sembolde ne varsa ona uygulanır"
      anlamına gelir.

Kalıcılık: `state/tv_events.json` (atomik yazım, `risk_event_halt` ile aynı
desen). Bozuk/parse edilemeyen dosya **boş durum + WARNING**'dir — burada
fail-closed'un karşılığı yoktur: olay defterinin kaybı yeni bir risk
almaz, yalnız kapıyı/çıkış tetiğini sessizleştirir (mevcut davranış).

Eşzamanlılık: FastAPI tek event-loop'ta çağırır ve `ingest()` içinde await
yoktur (TvConfluence ile aynı gerekçe) — kilit gerekmez.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.config import settings
from src.core.logger import app_logger

# Yapı durumunu güncelleyen olaylar (yön ZORUNLU — yön yapının yönüdür).
STRUCTURE_KINDS = frozenset({"choch", "trend"})
# Açık pozisyonu kapatma/koruma tetikleyen olaylar (yön OPSİYONEL).
EXIT_KINDS = frozenset({"exit", "tp1"})
# `/tv-signal` gövdesinde kabul edilen tüm `kind` değerleri. "entry" bu
# modüle HİÇ uğramaz (mevcut sağlama hattına gider) ama sözleşmenin
# tamamı tek yerde görünsün diye burada listelenir.
EVENT_KINDS = frozenset({"entry"}) | STRUCTURE_KINDS | EXIT_KINDS

_STATE_VERSION = 1


class TvEvents:
    """Sembol başına TV yapı durumu + son çıkış olayı (RAM + disk)."""

    def __init__(
        self,
        cfg: Any = None,
        *,
        state_path: Optional[str] = None,
        logger: Any = None,
    ) -> None:
        self.cfg = cfg if cfg is not None else settings
        self.logger = logger if logger is not None else app_logger
        configured = (
            state_path
            if state_path is not None
            else getattr(self.cfg, "tv_events_state_path", "") or ""
        )
        self._state_path: Optional[Path] = (
            Path(configured).expanduser() if configured else None
        )
        # SEMBOL -> {"structures": {kaynak: {...}}, "last_exit": {...}|None,
        #            "last_event": {...}|None}
        self._symbols: Dict[str, Dict[str, Any]] = {}
        # Süreç ömrü boyunca monoton artan olay sırası. Motor "bu olayı zaten
        # tükettim mi" sorusunu bununla yanıtlar (aynı olay her safety
        # turunda yeniden tetiklemesin).
        self._seq: int = 0
        self._counters: Dict[str, int] = {
            "ingested": 0,
            "would_block": 0,
            "would_exit": 0,
            "blocked": 0,
            "exits_applied": 0,
        }
        self._load()

    # ------------------------------------------------------------------
    # Ayar okuma (canlı `settings` üzerinden — testler monkeypatch edebilir)
    # ------------------------------------------------------------------

    def max_age_seconds(self) -> float:
        try:
            minutes = float(
                getattr(self.cfg, "scalper_tv_events_max_age_min", 240.0) or 0.0
            )
        except (TypeError, ValueError):
            minutes = 240.0
        return max(0.0, minutes) * 60.0

    def gate_sources(self) -> set:
        """Giriş kapısında (ve yapı-dönüşü çıkışında) SAYILAN kaynaklar.

        S&O trend'i ile PAC CHoCH'u ayrı `src` etiketleriyle tutulur ve
        ikisi de `/scalper/status`'ta görünür; hangisinin KARAR verdiğini
        `SCALPER_TV_EVENTS_GATE_SOURCES` seçer (kullanıcı kararı).
        """
        raw = str(getattr(self.cfg, "scalper_tv_events_gate_sources", "") or "")
        return {s.strip().lower() for s in raw.split(",") if s.strip()}

    def mode(self) -> str:
        return str(
            getattr(self.cfg, "scalper_tv_events_mode", "shadow") or "shadow"
        ).strip().lower()

    def exit_action(self) -> str:
        return str(
            getattr(self.cfg, "scalper_tv_events_exit", "be") or "be"
        ).strip().lower()

    # ------------------------------------------------------------------
    # Olay alma
    # ------------------------------------------------------------------

    def ingest(
        self,
        symbol: str,
        kind: str,
        direction: Any = None,
        source: str = "tv",
        ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Bir TV olayını kaydet ve sembolün GÜNCEL durumunu döndür.

        `direction`: `Direction` enum'u, "LONG"/"SHORT" metni ya da None.
        `exit`/`tp1` için None normaldir (yönsüz alarm koşulu).
        """
        symbol = str(symbol or "").upper().strip()
        kind = str(kind or "").lower().strip()
        source = str(source or "tv").lower().strip()
        raw_direction = getattr(direction, "value", direction)
        direction_value = str(raw_direction).upper().strip() if raw_direction else ""
        if direction_value not in ("LONG", "SHORT"):
            direction_value = ""

        now = float(ts) if ts is not None else time.time()
        self._seq += 1
        event = {
            "kind": kind,
            "direction": direction_value or None,
            "source": source,
            "ts": now,
            "seq": self._seq,
        }

        state = self._symbols.setdefault(
            symbol, {"structures": {}, "last_exit": None, "last_event": None}
        )
        state["last_event"] = dict(event)

        if kind in STRUCTURE_KINDS and direction_value:
            state["structures"][source] = {
                "structure": "BULL" if direction_value == "LONG" else "BEAR",
                "kind": kind,
                "ts": now,
                "seq": self._seq,
            }
        elif kind in EXIT_KINDS:
            state["last_exit"] = dict(event)

        self._counters["ingested"] = self._counters.get("ingested", 0) + 1
        self._persist()
        return self.symbol_state(symbol, now=now)

    def note(self, counter: str, amount: int = 1) -> None:
        """Telemetri sayacı (would_block/would_exit/blocked/exits_applied)."""
        self._counters[counter] = self._counters.get(counter, 0) + int(amount)

    # ------------------------------------------------------------------
    # Sorgular (motor bunları okur)
    # ------------------------------------------------------------------

    def fresh_gate_structures(
        self, symbol: str, *, now: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Kapı kaynaklarından gelen ve HÂLÂ TAZE olan yapı durumları.

        Boş liste = "bu sembol hakkında karar verilebilir yapı bilgisi yok"
        (kapı çalışmaz — olay kanalı hiçbir zaman fail-closed davranmaz;
        veri yoksa bugünkü davranış aynen sürer).
        """
        now = time.time() if now is None else now
        max_age = self.max_age_seconds()
        allowed = self.gate_sources()
        rows: List[Dict[str, Any]] = []
        state = self._symbols.get(str(symbol or "").upper().strip())
        if not state:
            return rows
        for source, row in state.get("structures", {}).items():
            if allowed and source not in allowed:
                continue
            age = now - float(row.get("ts") or 0.0)
            if max_age > 0 and age > max_age:
                continue
            rows.append(
                {
                    "source": source,
                    "structure": row.get("structure"),
                    "kind": row.get("kind"),
                    "age_s": round(age, 1),
                    "seq": int(row.get("seq") or 0),
                }
            )
        rows.sort(key=lambda r: r["age_s"])
        return rows

    def pending_exit(
        self, symbol: str, *, now: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """Sembolün son `exit`/`tp1` olayı (taze ise), yoksa None."""
        now = time.time() if now is None else now
        state = self._symbols.get(str(symbol or "").upper().strip())
        if not state:
            return None
        row = state.get("last_exit")
        if not row:
            return None
        max_age = self.max_age_seconds()
        age = now - float(row.get("ts") or 0.0)
        if max_age > 0 and age > max_age:
            return None
        out = dict(row)
        out["age_s"] = round(age, 1)
        return out

    def latest_seq(self, symbol: str) -> Dict[str, int]:
        """Sembolün son olay sıraları — motor imleç ilerletmede kullanır."""
        state = self._symbols.get(str(symbol or "").upper().strip()) or {}
        exit_row = state.get("last_exit") or {}
        structures = state.get("structures", {})
        struct_seq = max(
            [int(r.get("seq") or 0) for r in structures.values()] or [0]
        )
        return {
            "exit": int(exit_row.get("seq") or 0),
            "structure": struct_seq,
        }

    def symbols(self) -> List[str]:
        return sorted(self._symbols.keys())

    # ------------------------------------------------------------------
    # Telemetri
    # ------------------------------------------------------------------

    def symbol_state(
        self, symbol: str, *, now: Optional[float] = None
    ) -> Dict[str, Any]:
        now = time.time() if now is None else now
        symbol = str(symbol or "").upper().strip()
        state = self._symbols.get(symbol)
        if not state:
            return {
                "structure": "NONE",
                "structure_source": None,
                "structure_age_s": None,
                "structures": {},
                "last_event": None,
                "last_exit": None,
            }

        structures = {}
        for source, row in sorted(state.get("structures", {}).items()):
            structures[source] = {
                "structure": row.get("structure"),
                "kind": row.get("kind"),
                "age_s": round(now - float(row.get("ts") or 0.0), 1),
            }

        fresh = self.fresh_gate_structures(symbol, now=now)
        values = {r["structure"] for r in fresh}
        if not values:
            aggregate: str = "NONE"
            agg_source = None
            agg_age = None
        elif len(values) == 1:
            aggregate = next(iter(values))
            agg_source = fresh[0]["source"]
            agg_age = fresh[0]["age_s"]
        else:
            # Kapı kaynakları çelişiyor (ör. PAC BULL, S&O trend BEAR).
            # Kapı tarafında "herhangi bir ters kaynak engeller" kuralı
            # geçerlidir; telemetride bu durum gizlenmez.
            aggregate = "MIXED"
            agg_source = ",".join(sorted(r["source"] for r in fresh))
            agg_age = fresh[0]["age_s"]

        def _aged(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not row:
                return None
            out = {
                "kind": row.get("kind"),
                "direction": row.get("direction"),
                "source": row.get("source"),
                "age_s": round(now - float(row.get("ts") or 0.0), 1),
            }
            return out

        return {
            "structure": aggregate,
            "structure_source": agg_source,
            "structure_age_s": agg_age,
            "structures": structures,
            "last_event": _aged(state.get("last_event")),
            "last_exit": _aged(state.get("last_exit")),
        }

    def snapshot(self) -> Dict[str, Any]:
        """`/scalper/status` için tam telemetri. Secret İÇERMEZ."""
        now = time.time()
        return {
            "mode": self.mode(),
            "exit_action": self.exit_action(),
            "max_age_minutes": round(self.max_age_seconds() / 60.0, 2),
            "gate_sources": sorted(self.gate_sources()),
            "counters": dict(self._counters),
            "symbols": {
                symbol: self.symbol_state(symbol, now=now)
                for symbol in sorted(self._symbols.keys())
            },
        }

    def reset(self) -> None:
        """Süreç durumunu temizle (yalnız testler/kapanış)."""
        self._symbols.clear()
        self._seq = 0
        for key in list(self._counters.keys()):
            self._counters[key] = 0

    # ------------------------------------------------------------------
    # Kalıcılık
    # ------------------------------------------------------------------

    def _load(self) -> None:
        path = self._state_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION:
                raise ValueError("tv_events state version/root geçersiz")
            symbols = payload.get("symbols")
            if not isinstance(symbols, dict):
                raise ValueError("tv_events state 'symbols' sözlük değil")
            restored: Dict[str, Dict[str, Any]] = {}
            max_seq = 0
            for symbol, state in symbols.items():
                if not isinstance(state, dict):
                    raise ValueError("tv_events state sembol kaydı sözlük değil")
                structures = state.get("structures") or {}
                if not isinstance(structures, dict):
                    raise ValueError("tv_events state 'structures' sözlük değil")
                clean_structures = {}
                for source, row in structures.items():
                    if not isinstance(row, dict):
                        raise ValueError("tv_events state yapı satırı sözlük değil")
                    clean_structures[str(source)] = {
                        "structure": str(row.get("structure") or ""),
                        "kind": str(row.get("kind") or ""),
                        "ts": float(row.get("ts") or 0.0),
                        "seq": int(row.get("seq") or 0),
                    }
                    max_seq = max(max_seq, int(row.get("seq") or 0))

                def _event(row: Any) -> Optional[Dict[str, Any]]:
                    nonlocal max_seq
                    if row is None:
                        return None
                    if not isinstance(row, dict):
                        raise ValueError("tv_events state olay satırı sözlük değil")
                    max_seq = max(max_seq, int(row.get("seq") or 0))
                    return {
                        "kind": str(row.get("kind") or ""),
                        "direction": row.get("direction") or None,
                        "source": str(row.get("source") or "tv"),
                        "ts": float(row.get("ts") or 0.0),
                        "seq": int(row.get("seq") or 0),
                    }

                restored[str(symbol).upper()] = {
                    "structures": clean_structures,
                    "last_exit": _event(state.get("last_exit")),
                    "last_event": _event(state.get("last_event")),
                }
            self._symbols = restored
            self._seq = max(max_seq, int(payload.get("seq") or 0))
        except Exception as e:
            # Bozuk olay defteri fail-closed OLAMAZ: bu dosyanın kaybı yeni
            # bir risk almaz, yalnız kapıyı/çıkış tetiğini susturur (bugünkü
            # davranış). Sessiz kalmasın diye WARNING.
            self._symbols = {}
            self._seq = 0
            self.logger.warning(
                f"⚠️ TV olay durumu okunamadı ({path}): {type(e).__name__}: {e} — "
                "boş durumla devam ediliyor (kapı/çıkış tetiği veri gelene kadar sessiz)"
            )

    def _persist(self) -> bool:
        path = self._state_path
        if path is None:
            return False
        tmp_path: Optional[Path] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            payload = {
                "version": _STATE_VERSION,
                "seq": self._seq,
                "symbols": self._symbols,
            }
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            return True
        except Exception as e:
            self.logger.warning(
                f"⚠️ TV olay durumu kalıcılaştırılamadı ({path}): {e} — "
                "olaylar RAM'de tutuluyor, restart'ta kaybolur"
            )
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return False


# Süreç-tekili: `/tv-signal` (main.py) yazar, `ScalperEngine` okur —
# `symbol_reservations` ile aynı desen.
tv_events = TvEvents()
