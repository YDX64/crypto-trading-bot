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
  değişmeden korunur. Tersi de yasaktır: bir OLAY KAYNAĞI (`tv_event_sources`)
  `kind=entry` ile giriş oyu VEREMEZ — 422 (bkz. D19a bulgu A).
- Olay yolu **allowlist'ten bağımsızdır**: istek `TV_WEBHOOK_SECRET` ile
  kimliklenmiştir ve sağlamaya girmediği için bir "hayalet kaynak" giriş
  açtıramaz. `TV_SOURCE_ALLOWLIST` dışındaki bir olay kaynağı adı yalnız
  WARNING üretir, kaynak etiketi GÖVDEDEKİ değer olarak KALIR (aksi halde
  eski `?src=luxso`ya düşer ve kapı sessizce ölürdü — D19a bulgu E).
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

SIFIR/BOŞ = KAPALI (D19a bulgu G5): `SCALPER_TV_EVENTS_MAX_AGE_MIN=0`
"süresiz taze" DEĞİL, "pencere kapalı"dır; `SCALPER_TV_EVENTS_GATE_SOURCES`
boş ise "tüm kaynaklar" DEĞİL, "hiçbir kaynak karar vermez"dir. Bir sinyal
kanalının yanlışlıkla boş bırakılan ayarı, sonsuz ömürlü/kaynaksız bir kapı
değil SESSİZ bir kanal üretmelidir.

Tüketim (D19a bulgu D): "bu olayı zaten uyguladım mı" bilgisi RAM'de değil
DEFTERDE tutulur ve `state/tv_events.json`'a atomik yazılır — restart
tüketilmiş bir çıkış olayını YENİDEN tetiklemez. Aksiyon BAŞARISIZ olursa
olay tüketilmiş sayılmaz; `_MAX_EXIT_ATTEMPTS` denemeye kadar sonraki safety
turlarında yeniden denenir (olay `max_age` içinde kaldığı sürece).

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# Tüketim imleci grupları (kalıcı durumda anahtar olarak kullanılır).
CONSUME_GROUPS = ("exit", "structure")

_STATE_VERSION = 2
# v1 (ilk D19 commit'i) tüketim imleci taşımıyordu; olayları okuyup
# `consumed`i boş kabul ederek yükseltiyoruz — dosyayı atmak, restart'ta
# eski bir çıkış olayının yeniden tetiklenmesi demek olurdu.
_SUPPORTED_STATE_VERSIONS = (1, _STATE_VERSION)

# Defter budama (D19a bulgu G3): sembol sayısı ve sembol başına kaynak
# sayısı sınırlıdır — yanlış yapılandırılmış/kötü niyetli bir alarm seli
# durum dosyasını süresiz büyütemesin.
_MAX_SYMBOLS = 64
_MAX_STRUCTURE_SOURCES = 16
_MAX_ATTEMPT_KEYS = 16

# Kalıcılık hatası log seli olmasın (D19a bulgu G7): aynı hata en fazla bu
# aralıkla WARNING'e yazılır, sayacı `/scalper/status`'ta görünür.
_PERSIST_WARN_INTERVAL_S = 60.0

# Bir çıkış olayı için azami uygulama denemesi. Aşılırsa olay "tüketildi"
# sayılır (sonsuz yeniden deneme, safety turunu bir borsa hatasına kilitler).
_MAX_EXIT_ATTEMPTS = 3

# Telemetri sayacı yazımının asgari aralığı (D19a-2): `_persist` tam JSON +
# iki fsync'tir (~2.5 ms) ve `note()` event-loop üzerinde SENKRON çalışır.
# Olay/tüketim yazımları ANINDA kalıcıdır (durability gerekir); sayaçlar ise
# telemetridir — saniyede bir yazmak yeterli, kalanı bir sonraki `ingest`/
# `mark_consumed` yazımına binerek diske iner.
_COUNTER_PERSIST_MIN_INTERVAL_S = 1.0

# Defter budamasında korunacak azami sembol (motorun izlediği açık
# pozisyonlar). `scalper_max_positions` bunun çok altındadır.
_MAX_PROTECTED_SYMBOLS = 32

# Olay kaynağı varsayılanı — `tv_event_sources` ayarı boşsa bu kullanılır.
DEFAULT_EVENT_SOURCES = ("luxso_exit", "luxso_trend", "pac_choch", "algopro_tp1")

_COUNTER_KEYS = (
    "ingested",
    "gate_hits",
    "would_block",
    "blocked",
    "mixed_skipped",
    "exit_hits",
    "would_exit",
    "would_exit_noop",
    "exits_attempted",
    "exits_applied",
    "exits_noop",
    "exits_failed",
    "exits_skipped_losing",
    "exits_closed_losing",
    "rejected_entry_from_event_source",
    "rejected_symbol_allowlist",
)


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _attempt_sort_key(key: str) -> Tuple[str, int]:
    """`"exit:12"` → `("exit", 12)`. Leksikografik sıralama tuzağına karşı."""
    group, _, raw_seq = str(key).partition(":")
    try:
        return (group, int(raw_seq))
    except ValueError:
        return (group, 0)


def _csv_set(raw: Any) -> set:
    return {s.strip().lower() for s in str(raw or "").split(",") if s.strip()}


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
        #            "last_event": {...}|None, "consumed": {grup: seq},
        #            "attempts": {"grup:seq": n}, "updated_ts": epoch}
        self._symbols: Dict[str, Dict[str, Any]] = {}
        # Süreç ömrü boyunca monoton artan olay sırası. Motor "bu olayı zaten
        # tükettim mi" sorusunu bununla yanıtlar (aynı olay her safety
        # turunda yeniden tetiklemesin).
        self._seq: int = 0
        self._counters: Dict[str, int] = {key: 0 for key in _COUNTER_KEYS}
        self._counters_since: float = time.time()
        self._persist_errors: int = 0
        self._persist_last_error: Optional[str] = None
        self._persist_last_error_ts: Optional[float] = None
        self._persist_last_warn_monotonic: float = -1e9
        self._counters_last_persist_monotonic: float = -1e9
        # Budamadan MUAF semboller (motor her safety turunda tazeler).
        # RAM-only: restart'ta motor ilk turda yeniden bildirir.
        self._protected: set = set()
        self._load()

    # ------------------------------------------------------------------
    # Ayar okuma (canlı `settings` üzerinden — testler monkeypatch edebilir)
    # ------------------------------------------------------------------

    def max_age_seconds(self) -> float:
        """Tazelik penceresi (saniye). **0 = pencere KAPALI** (bkz. modül başlığı)."""
        try:
            minutes = float(
                getattr(self.cfg, "scalper_tv_events_max_age_min", 240.0) or 0.0
            )
        except (TypeError, ValueError):
            minutes = 240.0
        return max(0.0, minutes) * 60.0

    def window_open(self) -> bool:
        return self.max_age_seconds() > 0.0

    def gate_sources(self) -> set:
        """Giriş kapısında (ve yapı-dönüşü çıkışında) SAYILAN kaynaklar.

        S&O trend'i ile PAC CHoCH'u ayrı `src` etiketleriyle tutulur ve
        ikisi de `/scalper/status`'ta görünür; hangisinin KARAR verdiğini
        `SCALPER_TV_EVENTS_GATE_SOURCES` seçer (kullanıcı kararı).
        **Boş liste = hiçbir kaynak karar vermez** (kapı kapalı).
        """
        return _csv_set(getattr(self.cfg, "scalper_tv_events_gate_sources", ""))

    def event_sources(self) -> set:
        """"Olay kaynağı" olarak işaretlenmiş `src` etiketleri.

        Bu kümedeki bir kaynak `kind=entry` ile GİRİŞ OYU VEREMEZ (422).
        Yeni bir çıkış/yapı entegrasyonu eklerken adı buraya yazılır; kod
        değişmez (`TV_EVENT_SOURCES`).
        """
        configured = _csv_set(getattr(self.cfg, "tv_event_sources", ""))
        return configured or set(DEFAULT_EVENT_SOURCES)

    def mode(self) -> str:
        mode = str(
            getattr(self.cfg, "scalper_tv_events_mode", "shadow") or "shadow"
        ).strip().lower()
        return mode if mode in ("off", "shadow", "active") else "shadow"

    def exit_action(self) -> str:
        action = str(
            getattr(self.cfg, "scalper_tv_events_exit", "be") or "be"
        ).strip().lower()
        return action if action in ("off", "be", "close") else "be"

    def exit_losing_action(self) -> str:
        """Pozisyon ZARARDAYKEN çıkış olayı gelirse ne yapılır (D19a bulgu B).

        `skip`  = hiçbir şey (logla + say). BE'ye çekmek stopu piyasanın TERS
                  tarafına koymak demektir → Binance -2021 → acil kapanış.
        `close` = reduce-only MARKET kapanış (bilinçli, geri alınamaz karar).
        """
        # `or "skip"` / geçersiz-değer fallback'i canlı `Settings` ile
        # ULAŞILAMAZ bir daldır (`_validate_tv_events_settings` boş ve
        # geçersiz değeri startup'ta reddeder); yalnız test çiftleri ve
        # `SimpleNamespace` cfg'leri için vardır.
        action = str(
            getattr(self.cfg, "scalper_tv_events_exit_losing", "skip") or "skip"
        ).strip().lower()
        return action if action in ("skip", "close") else "skip"

    def be_margin_pct(self) -> float:
        """BE hedefinin piyasadan güvenli uzaklığı (yüzde, tek yönlü pay)."""
        try:
            value = float(
                getattr(self.cfg, "scalper_tv_events_be_margin_pct", 0.05) or 0.0
            )
        except (TypeError, ValueError):
            value = 0.05
        return max(0.0, value)

    def symbol_allowlist(self) -> set:
        """TV sembol allowlist'i (D7) — olay yolunda da uygulanır."""
        raw = str(getattr(self.cfg, "scalper_tv_symbol_allowlist", "") or "").strip()
        return {s.strip().upper() for s in raw.split(",") if s.strip()}

    def symbol_allowed(self, symbol: str) -> bool:
        allowed = self.symbol_allowlist()
        if not allowed:
            return True
        return str(symbol or "").upper().strip() in allowed

    # ------------------------------------------------------------------
    # Yapılandırma sağlığı (startup uyarısı + /scalper/status)
    # ------------------------------------------------------------------

    def config_health(self) -> Dict[str, Any]:
        """Sessiz ölü kanal teşhisi (D19a bulgu E).

        `TV_SOURCE_ALLOWLIST` sunucu `.env`'inde AÇIKÇA set edilmişse kod
        varsayılanı devreye girmez; olay kaynakları listede yoksa `?src=`
        etiketi doğru görünse bile kapı kaynak eşleşmesi tutmayabilir.
        Yönlendirme buna rağmen çalışır (olay yolu allowlist'ten bağımsız),
        ama operatör bunu GÖRMELİ.
        """
        allow = _csv_set(getattr(self.cfg, "tv_source_allowlist", ""))
        event_srcs = self.event_sources()
        missing = sorted(event_srcs - allow) if allow else sorted(event_srcs)
        gate_srcs = self.gate_sources()
        warnings: List[str] = []
        if self.mode() != "off":
            if missing:
                warnings.append(
                    "TV_SOURCE_ALLOWLIST olay kaynaklarını içermiyor: "
                    f"{missing} — olay yolu yine çalışır (secret ile kimlikli) ama "
                    "giriş yolundaki aynı etiket 'tv'ye eşlenir; docs/RUNBOOK.md "
                    "'TV olay kanalı' adım 2"
                )
            if not self.window_open():
                warnings.append(
                    "SCALPER_TV_EVENTS_MAX_AGE_MIN=0 → tazelik penceresi KAPALI: "
                    "hiçbir olay taze sayılmaz, kapı/çıkış tetiği sessizdir"
                )
            if not gate_srcs:
                warnings.append(
                    "SCALPER_TV_EVENTS_GATE_SOURCES boş → hiçbir yapı kaynağı karar "
                    "vermez (giriş kapısı ve yapı-dönüşü çıkışı sessizdir)"
                )
        return {
            "allowlist_ok": not missing,
            "allowlist_missing": missing,
            "gate_enabled": bool(gate_srcs) and self.window_open(),
            "window_open": self.window_open(),
            "warnings": warnings,
        }

    def log_config_health(self) -> Dict[str, Any]:
        """Startup'ta bir kez çağrılır: sorun varsa WARNING, yoksa sessiz."""
        health = self.config_health()
        for line in health["warnings"]:
            self.logger.warning(f"⚠️ TV olay kanalı: {line}")
        return health

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
        via: Any = None,
    ) -> Dict[str, Any]:
        """Bir TV olayını kaydet ve sembolün GÜNCEL durumunu döndür.

        `direction`: `Direction` enum'u, "LONG"/"SHORT" metni ya da None.
        `exit`/`tp1` için None normaldir (yönsüz alarm koşulu).
        `via`: aynı `src` etiketini paylaşan alt-kaynak (ör. Trend Catcher
        ile Trend Tracer `luxso_trend`i paylaşır). YALNIZ TELEMETRİDİR:
        durum anahtarı `src`tir, dolayısıyla iki alt-kaynak birbirini
        MIXED'e düşürmez — **son olay kazanır** (bkz. INTEGRATIONS §7.3).
        """
        symbol = str(symbol or "").upper().strip()
        kind = str(kind or "").lower().strip()
        source = str(source or "tv").lower().strip()
        via_value = str(via or "").lower().strip()[:32] or None
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
            "via": via_value,
            "ts": now,
            "seq": self._seq,
        }

        state = self._symbols.setdefault(symbol, self._empty_symbol_state())
        state["last_event"] = dict(event)
        state["updated_ts"] = max(float(state.get("updated_ts") or 0.0), now)

        if kind in STRUCTURE_KINDS and direction_value:
            state["structures"][source] = {
                "structure": "BULL" if direction_value == "LONG" else "BEAR",
                "kind": kind,
                "via": via_value,
                "ts": now,
                "seq": self._seq,
            }
        elif kind in EXIT_KINDS:
            state["last_exit"] = dict(event)

        self._counters["ingested"] = self._counters.get("ingested", 0) + 1
        self._prune(symbol)
        self._persist()
        return self.symbol_state(symbol, now=now)

    def note(self, counter: str, amount: int = 1) -> None:
        """Telemetri sayacı (would_block/blocked/would_exit/...).

        Disk yazımı saniyede bir ile sınırlıdır
        (`_COUNTER_PERSIST_MIN_INTERVAL_S`): RAM değeri ANINDA doğrudur (`/scalper/status` oradan
        okur), yalnız diske inişi ertelenir. Çökme durumunda en fazla son
        saniyenin sayaçları kaybolur — telemetri için kabul edilebilir,
        olay/tüketim yazımları bu ertelemeye TABİ DEĞİLDİR.
        """
        self._counters[counter] = self._counters.get(counter, 0) + int(amount)
        now = time.monotonic()
        if now - self._counters_last_persist_monotonic < _COUNTER_PERSIST_MIN_INTERVAL_S:
            return
        self._counters_last_persist_monotonic = now
        self._persist()

    def protect(self, symbols: Any) -> None:
        """Budamadan muaf tutulacak semboller (açık pozisyonlar, D19a-2).

        Motor her safety turunda `exits.tracked_symbols()`'ı bildirir.
        Bekleyen bir çıkış olayı ve tüketim imleci taşıyan AKTİF bir sembol,
        bir alarm selinde `_MAX_SYMBOLS` eviction'ına kurban gitmemeli.
        """
        try:
            values = {str(sym or "").upper().strip() for sym in symbols}
        except TypeError:
            return
        self._protected = {v for v in values if v}
        if len(self._protected) > _MAX_PROTECTED_SYMBOLS:
            self._protected = set(sorted(self._protected)[:_MAX_PROTECTED_SYMBOLS])

    @staticmethod
    def _empty_symbol_state() -> Dict[str, Any]:
        return {
            "structures": {},
            "last_exit": None,
            "last_event": None,
            "consumed": {group: 0 for group in CONSUME_GROUPS},
            "attempts": {},
            "updated_ts": 0.0,
        }

    def _prune(self, keep: str = "") -> None:
        """Defteri sınırlar içinde tut (sembol ve kaynak sayısı).

        Korunan semboller (`protect`, açık pozisyonlar) kurban SEÇİLMEZ; bu
        yüzden üst sınır fiilen `_MAX_SYMBOLS + korunan sayısı`dır
        (≤ 64 + 32). Korunan küme `scalper_max_positions` ile sınırlıdır,
        yani pratikte 5-8 sembol — sınırsız büyüme yolu yoktur.
        """
        state = self._symbols.get(keep)
        if state is not None:
            structures = state.get("structures", {})
            if len(structures) > _MAX_STRUCTURE_SOURCES:
                for source, _ in sorted(
                    structures.items(), key=lambda kv: float(kv[1].get("ts") or 0.0)
                )[: len(structures) - _MAX_STRUCTURE_SOURCES]:
                    structures.pop(source, None)
            attempts = state.get("attempts", {})
            if len(attempts) > _MAX_ATTEMPT_KEYS:
                # SAYISAL sıralama: `sorted(str)` "exit:10" < "exit:2" der ve
                # EN YENİ denemenin sayacını düşürebilirdi (D19a-2 bulgu 9).
                for key in sorted(attempts.keys(), key=_attempt_sort_key)[
                    : len(attempts) - _MAX_ATTEMPT_KEYS
                ]:
                    attempts.pop(key, None)

        if len(self._symbols) > _MAX_SYMBOLS:
            candidates = [
                s
                for s in self._symbols
                if s != keep and s not in self._protected
            ]
            victims = sorted(
                candidates,
                key=lambda s: float(self._symbols[s].get("updated_ts") or 0.0),
            )[: len(self._symbols) - _MAX_SYMBOLS]
            for symbol in victims:
                self._symbols.pop(symbol, None)

    # ------------------------------------------------------------------
    # Tüketim imleçleri (kalıcı — D19a bulgu D)
    # ------------------------------------------------------------------

    def consumed_seq(self, symbol: str) -> Dict[str, int]:
        state = self._symbols.get(str(symbol or "").upper().strip()) or {}
        consumed = state.get("consumed") or {}
        return {group: int(consumed.get(group) or 0) for group in CONSUME_GROUPS}

    def mark_consumed(self, symbol: str, group: str, seq: int) -> None:
        """`group` imlecini `seq`e taşı (yalnız ileri). Değişiklikte kalıcılaştırır."""
        symbol = str(symbol or "").upper().strip()
        if group not in CONSUME_GROUPS or not symbol:
            return
        state = self._symbols.setdefault(symbol, self._empty_symbol_state())
        consumed = state.setdefault(
            "consumed", {g: 0 for g in CONSUME_GROUPS}
        )
        current = int(consumed.get(group) or 0)
        seq = int(seq or 0)
        if seq <= current:
            return
        consumed[group] = seq
        # Tüketilen olayın deneme sayacı artık gereksiz.
        state.get("attempts", {}).pop(f"{group}:{seq}", None)
        self._persist()

    def attempt_count(self, symbol: str, group: str, seq: int) -> int:
        state = self._symbols.get(str(symbol or "").upper().strip()) or {}
        return int((state.get("attempts") or {}).get(f"{group}:{int(seq or 0)}") or 0)

    def note_attempt(self, symbol: str, group: str, seq: int) -> int:
        """Başarısız aksiyon denemesini kaydet ve toplam deneme sayısını döndür."""
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            return 0
        state = self._symbols.setdefault(symbol, self._empty_symbol_state())
        attempts = state.setdefault("attempts", {})
        key = f"{group}:{int(seq or 0)}"
        attempts[key] = int(attempts.get(key) or 0) + 1
        self._prune(symbol)
        self._persist()
        return attempts[key]

    @staticmethod
    def max_attempts() -> int:
        return _MAX_EXIT_ATTEMPTS

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

        `max_age == 0` (pencere kapalı) veya `gate_sources` boş ise **her
        zaman boş liste** döner (bkz. modül başlığı "SIFIR/BOŞ = KAPALI").
        """
        now = time.time() if now is None else now
        max_age = self.max_age_seconds()
        allowed = self.gate_sources()
        rows: List[Dict[str, Any]] = []
        if max_age <= 0 or not allowed:
            return rows
        state = self._symbols.get(str(symbol or "").upper().strip())
        if not state:
            return rows
        for source, row in state.get("structures", {}).items():
            if source not in allowed:
                continue
            age = now - float(row.get("ts") or 0.0)
            if age > max_age:
                continue
            rows.append(
                {
                    "source": source,
                    "structure": row.get("structure"),
                    "kind": row.get("kind"),
                    "via": row.get("via"),
                    "age_s": round(age, 1),
                    "seq": int(row.get("seq") or 0),
                }
            )
        rows.sort(key=lambda r: r["age_s"])
        return rows

    def structure_verdict(
        self, symbol: str, *, now: Optional[float] = None
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """(BULL|BEAR|MIXED|NONE, taze satırlar).

        MIXED = kapı kaynakları ÇELİŞİYOR. D19a bulgu F: çelişki "bilinmiyor"
        demektir, "her iki yön de yasak" DEĞİL — MIXED'te kapı UYGULANMAZ.
        """
        rows = self.fresh_gate_structures(symbol, now=now)
        values = {r.get("structure") for r in rows}
        if not values:
            return "NONE", rows
        if len(values) == 1:
            return next(iter(values)), rows
        return "MIXED", rows

    def pending_exit(
        self, symbol: str, *, now: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """Sembolün son `exit`/`tp1` olayı (taze ise), yoksa None."""
        now = time.time() if now is None else now
        max_age = self.max_age_seconds()
        if max_age <= 0:
            return None
        state = self._symbols.get(str(symbol or "").upper().strip())
        if not state:
            return None
        row = state.get("last_exit")
        if not row:
            return None
        age = now - float(row.get("ts") or 0.0)
        if age > max_age:
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
                "consumed": {group: 0 for group in CONSUME_GROUPS},
            }

        structures = {}
        for source, row in sorted(state.get("structures", {}).items()):
            structures[source] = {
                "structure": row.get("structure"),
                "kind": row.get("kind"),
                "via": row.get("via"),
                "age_s": round(now - float(row.get("ts") or 0.0), 1),
            }

        aggregate, fresh = self.structure_verdict(symbol, now=now)
        if aggregate == "NONE":
            agg_source = None
            agg_age = None
        elif aggregate == "MIXED":
            # Kapı kaynakları çelişiyor (ör. PAC BULL, S&O trend BEAR).
            # Kapı UYGULANMAZ (bilinmiyor = serbest); telemetride gizlenmez.
            agg_source = ",".join(sorted(r["source"] for r in fresh))
            agg_age = fresh[0]["age_s"]
        else:
            agg_source = fresh[0]["source"]
            agg_age = fresh[0]["age_s"]

        def _aged(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not row:
                return None
            return {
                "kind": row.get("kind"),
                "direction": row.get("direction"),
                "source": row.get("source"),
                "via": row.get("via"),
                "age_s": round(now - float(row.get("ts") or 0.0), 1),
            }

        return {
            "structure": aggregate,
            "structure_source": agg_source,
            "structure_age_s": agg_age,
            "structures": structures,
            "last_event": _aged(state.get("last_event")),
            "last_exit": _aged(state.get("last_exit")),
            "consumed": self.consumed_seq(symbol),
        }

    def snapshot(self) -> Dict[str, Any]:
        """`/scalper/status` için tam telemetri. Secret İÇERMEZ."""
        now = time.time()
        health = self.config_health()
        return {
            "mode": self.mode(),
            "exit_action": self.exit_action(),
            "exit_losing": self.exit_losing_action(),
            "max_age_minutes": round(self.max_age_seconds() / 60.0, 2),
            "window_open": health["window_open"],
            "gate_enabled": health["gate_enabled"],
            "gate_sources": sorted(self.gate_sources()),
            "event_sources": sorted(self.event_sources()),
            "allowlist_ok": health["allowlist_ok"],
            "allowlist_missing": health["allowlist_missing"],
            "symbol_allowlist": sorted(self.symbol_allowlist()),
            "counters": dict(self._counters),
            "counters_since": _iso(self._counters_since),
            "persist": {
                "ok": self._persist_errors == 0,
                "errors": self._persist_errors,
                "last_error": self._persist_last_error,
                "last_error_at": _iso(self._persist_last_error_ts),
                "path": str(self._state_path) if self._state_path else None,
            },
            "symbols": {
                symbol: self.symbol_state(symbol, now=now)
                for symbol in sorted(self._symbols.keys())
            },
        }

    def reset(self) -> Dict[str, Any]:
        """Süreç durumunu temizle (POST /tv-events/reset ve testler).

        Dosyayı SİLMEK yetmez: çalışan süreç durumu RAM'de tutar ve bir
        sonraki yazımda geri yazar (D19a bulgu G7 / RUNBOOK reçetesi).
        """
        cleared = len(self._symbols)
        self._symbols.clear()
        self._seq = 0
        for key in list(self._counters.keys()):
            self._counters[key] = 0
        self._counters_since = time.time()
        persisted = self._persist(force=True)
        return {"cleared_symbols": cleared, "persisted": persisted}

    # ------------------------------------------------------------------
    # Kalıcılık
    # ------------------------------------------------------------------

    def _load(self) -> None:
        path = self._state_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("tv_events state kök nesnesi sözlük değil")
            if payload.get("version") not in _SUPPORTED_STATE_VERSIONS:
                raise ValueError(
                    f"tv_events state sürümü desteklenmiyor: {payload.get('version')!r}"
                )
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
                    structure = str(row.get("structure") or "").upper()
                    if structure not in ("BULL", "BEAR"):
                        # Sözleşme dışı değer (bozuk/eksik alan) telemetride
                        # `structure: ""` gibi tip dışı bir hüküm üretirdi.
                        continue
                    clean_structures[str(source)] = {
                        "structure": structure,
                        "kind": str(row.get("kind") or ""),
                        "via": row.get("via") or None,
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
                        "via": row.get("via") or None,
                        "ts": float(row.get("ts") or 0.0),
                        "seq": int(row.get("seq") or 0),
                    }

                raw_consumed = state.get("consumed") or {}
                if not isinstance(raw_consumed, dict):
                    raise ValueError("tv_events state 'consumed' sözlük değil")
                consumed = {
                    group: int(raw_consumed.get(group) or 0)
                    for group in CONSUME_GROUPS
                }
                max_seq = max([max_seq] + list(consumed.values()))

                raw_attempts = state.get("attempts") or {}
                if not isinstance(raw_attempts, dict):
                    raise ValueError("tv_events state 'attempts' sözlük değil")
                attempts = {
                    str(key): int(value or 0)
                    for key, value in sorted(
                        raw_attempts.items(), key=lambda kv: _attempt_sort_key(kv[0])
                    )[-_MAX_ATTEMPT_KEYS:]
                }

                restored[str(symbol).upper()] = {
                    "structures": clean_structures,
                    "last_exit": _event(state.get("last_exit")),
                    "last_event": _event(state.get("last_event")),
                    "consumed": consumed,
                    "attempts": attempts,
                    "updated_ts": float(state.get("updated_ts") or 0.0),
                }
            self._symbols = restored
            self._seq = max(max_seq, int(payload.get("seq") or 0))
            counters = payload.get("counters")
            if isinstance(counters, dict):
                for key, value in counters.items():
                    try:
                        self._counters[str(key)] = int(value or 0)
                    except (TypeError, ValueError):
                        continue
            since = payload.get("counters_since")
            if since:
                try:
                    self._counters_since = float(since)
                except (TypeError, ValueError):
                    pass
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

    def _persist(self, *, force: bool = False) -> bool:
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
                "counters": self._counters,
                "counters_since": self._counters_since,
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
            self._persist_errors += 1
            self._persist_last_error = f"{type(e).__name__}: {e}"
            self._persist_last_error_ts = time.time()
            # Log seli koruması (D19a bulgu G7): sağlık `/scalper/status`
            # `tv_events.persist` alanından okunur, log'a dakikada bir yazılır.
            now_monotonic = time.monotonic()
            if force or (
                now_monotonic - self._persist_last_warn_monotonic
                >= _PERSIST_WARN_INTERVAL_S
            ):
                self._persist_last_warn_monotonic = now_monotonic
                self.logger.warning(
                    f"⚠️ TV olay durumu kalıcılaştırılamadı ({path}): {e} — "
                    f"olaylar RAM'de tutuluyor, restart'ta kaybolur "
                    f"(toplam hata: {self._persist_errors})"
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
