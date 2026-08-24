"""Üç-aşamalı niyet kaydı (D24/madde 7) — YALNIZ GÖZLEM.

Sorun: bugün `scalp_trades` YALNIZ gerçekleşeni tutar. Gerçekleşmeyen bir
niyet — rejim kapısı reddetti, TV sağlaması dolmadı, kapasite doluydu, emir
hata verdi — HİÇBİR yerde iz bırakmaz. Bu yüzden "bot niye işlem açmıyor"
sorusu ancak `bot.log`'u elle tarayarak yanıtlanabiliyor ve "kaç sinyal
doğdu / kaçı hangi kapıda düştü" sorusunun SAYISAL cevabı yok.

Bu modül o izi bırakır. Üç aşama:

  * ``proposed``  — bir sinyal DOĞDU (henüz hiçbir kapıdan geçmedi).
  * ``decided``   — motor bir KARAR verdi (``allow`` / ``deny`` + gerekçe).
  * ``executed``  — borsa SONUCU (``allow`` = pozisyon açıldı, ``error`` =
                    emir hatası).

**Motor davranışı DEĞİŞMEZ.** Buradan hiçbir kapı, boyutlama ya da çıkış
kararı beslenmez; `record()` yalnız sayaç artırır ve JSONL kuyruğuna satır
koyar. Hata hâlinde sessizce yutar (bir teşhis kaydı bir girişi ASLA
engellememeli — D21'in aynı ilkesi).

Esin kaynağı ve LİSANS notu
---------------------------
Üç-aşamalı "öneri → karar → sonuç" kalıbı OpenTrade'in insan-onaylı emir
akışından **KALIP olarak** esinlenmiştir. OpenTrade **Elastic License 2.0**
(source-available; yönetilen hizmet olarak sunma ve lisans anahtarı
kaldırma yasağı içerir) altındadır — bu yüzden oradan **hiçbir satır kod
kopyalanmamıştır**; alınan tek şey aşama adlandırmasının fikridir.

Alınmayan şey: OpenTrade'in **long-poll onay** kalıbı. Park edilen bir HTTP
isteği uvicorn worker'ını onay gelene kadar tutar; bu, bu repoda kanıtlanmış
bir arıza sınıfıdır ("dashboard polling açlığı": pano `/api/status`'u zorla
tazelerken rate-limiter'ı doyurup taramayı bayatlatmıştı). Kayıt bu yüzden
tamamen **eşzamansız ve tek yönlüdür**: yazan bekler değil, bırakır.

Dürüstlük notu (sayaçlar)
-------------------------
`counters_snapshot()` **süreç-içi** sayaçlardır ve **restart'ta SIFIRLANIR**
(`"window": "process_start"`). Kalıcı tarihçe `logs/trades.jsonl`'deki
``event="intent"`` satırlarındadır; çevrimdışı dağılım için
`summarize_intents()` kullanılır.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

# --------------------------------------------------------------------------
# Aşama ve karar sabitleri
# --------------------------------------------------------------------------

STAGE_PROPOSED = "proposed"   # niyet doğdu (sinyal var, karar yok)
STAGE_DECIDED = "decided"     # motor karar verdi (kapı geçti/reddetti)
STAGE_EXECUTED = "executed"   # borsa sonucu (pozisyon açıldı / emir hatası)

KNOWN_STAGES: frozenset = frozenset(
    {STAGE_PROPOSED, STAGE_DECIDED, STAGE_EXECUTED}
)

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ERROR = "error"

KNOWN_DECISIONS: frozenset = frozenset(
    {DECISION_ALLOW, DECISION_DENY, DECISION_ERROR}
)

# --------------------------------------------------------------------------
# Ret gerekçeleri
# --------------------------------------------------------------------------

REASON_REGIME_GATE = "regime_gate"
REASON_MARKET_GATE_DAY = "market_gate_day"
REASON_MARKET_GATE_RUN = "market_gate_run"
REASON_STRUCTURE_GATE = "structure_gate"
REASON_TV_STRUCTURE_GATE = "tv_structure_gate"
REASON_TV_CONFLUENCE = "tv_confluence"
REASON_LOSS_COOLDOWN = "loss_cooldown"
REASON_CAPACITY = "capacity"
REASON_RESERVATION = "reservation"
REASON_ENTRY_HALT = "entry_halt"
REASON_KILL_SWITCH = "kill_switch"
REASON_RISK_EVENT = "risk_event"
REASON_EXCHANGE_UNVERIFIED = "exchange_unverified"
REASON_EXCHANGE_POSITION_EXISTS = "exchange_position_exists"
REASON_ALREADY_TRACKED = "already_tracked"
REASON_SYMBOL_RESERVED_BY_OTHER = "symbol_reserved_by_other"
REASON_ORDER_ERROR = "order_error"
REASON_OPENED = "opened"

#: Gerekçesi olmayan satırların (ör. `proposed`) kovası. Sayaç toplamının
#: `total` ile eşit kalması için vardır: gerekçesizleri saymamak, sayaçlar
#: arasında sessiz bir tutarsızlık yaratırdı.
REASON_NONE_BUCKET = "_yok_"

#: Bilinmeyen gerekçelerin toplandığı kova. Kova sayısı SINIRLIDIR: gerekçe
#: metni koddan gelir ama bir yazım hatası sınırsız kova büyütmemeli.
REASON_OTHER_BUCKET = "_diger_"

#: Tek satırlık Türkçe açıklama (pano/rapor için). İki özel kova da
#: buradadır ki etiket araması tek yerden yapılabilsin.
REASON_LABELS: Dict[str, str] = {
    REASON_REGIME_GATE: "rejim kapısı: 4h rejime ters yön",
    REASON_MARKET_GATE_DAY: "piyasa kapısı: liderin gün-içi sapması",
    REASON_MARKET_GATE_RUN: "piyasa kapısı: liderin çok-günlük uzaması",
    REASON_STRUCTURE_GATE: "yapı kapısı: son swing kırılımına ters yön",
    REASON_TV_STRUCTURE_GATE: "TV yapı kapısı: CHoCH/trend olayına ters yön",
    REASON_TV_CONFLUENCE: "TV sağlaması dolmadı (yeterli farklı kaynak yok)",
    REASON_LOSS_COOLDOWN: "zarar sonrası sembol cooldown'ı aktif",
    REASON_CAPACITY: "eşzamanlı pozisyon kapasitesi dolu",
    REASON_RESERVATION: "sembol rezervasyonu alınamadı",
    REASON_ENTRY_HALT: "giriş güvenlik kilidi (entry-halt) aktif",
    REASON_KILL_SWITCH: "günlük zarar kesici (kill switch) aktif",
    REASON_RISK_EVENT: "dış risk olayı duraklatması aktif",
    REASON_EXCHANGE_UNVERIFIED: "hesap pozisyonları doğrulanamadı (fail-closed)",
    REASON_EXCHANGE_POSITION_EXISTS: "borsada bu sembolde zaten pozisyon var",
    REASON_ALREADY_TRACKED: "sembol zaten izleniyor ya da emir bekliyor",
    REASON_SYMBOL_RESERVED_BY_OTHER: "sembol başka motorun yönetiminde",
    REASON_ORDER_ERROR: "emir açılırken hata alındı",
    REASON_OPENED: "pozisyon açıldı",
    REASON_NONE_BUCKET: "gerekçe yok (yalnız niyet kaydı)",
    REASON_OTHER_BUCKET: "tanınmayan gerekçe",
}

#: Kodun ürettiği GERÇEK gerekçeler — iki özel kova BURADA YOKTUR.
KNOWN_REASONS: frozenset = frozenset(
    {
        REASON_REGIME_GATE,
        REASON_MARKET_GATE_DAY,
        REASON_MARKET_GATE_RUN,
        REASON_STRUCTURE_GATE,
        REASON_TV_STRUCTURE_GATE,
        REASON_TV_CONFLUENCE,
        REASON_LOSS_COOLDOWN,
        REASON_CAPACITY,
        REASON_RESERVATION,
        REASON_ENTRY_HALT,
        REASON_KILL_SWITCH,
        REASON_RISK_EVENT,
        REASON_EXCHANGE_UNVERIFIED,
        REASON_EXCHANGE_POSITION_EXISTS,
        REASON_ALREADY_TRACKED,
        REASON_SYMBOL_RESERVED_BY_OTHER,
        REASON_ORDER_ERROR,
        REASON_OPENED,
    }
)

#: `detail` serbest metninin sınırı (JSONL satırı şişmesin).
DETAIL_MAX = 200


# --------------------------------------------------------------------------
# Savunmalı küçük yardımcılar (forensics.py ile aynı üslup)
# --------------------------------------------------------------------------

def _s(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _token(value: Any) -> Optional[str]:
    """Sabit alanlar (stage/decision/reason) için küçük harfli belirteç."""
    text = _s(value)
    return None if text is None else text.lower()


def _symbol(value: Any) -> Optional[str]:
    """Sembol büyük harfe indirgenir (`btcusdt` ile `BTCUSDT` aynı kova)."""
    text = _s(value)
    return None if text is None else text.upper()


def _direction(value: Any) -> Optional[str]:
    """`Direction` enum'u ya da düz metin — büyük harfli metne indirger."""
    raw = getattr(value, "value", value)
    text = _s(raw)
    return None if text is None else text.upper()


def _num(value: Any) -> Optional[float]:
    """D27/B: sonlu bir sayı ya da `None` — bozuk girdi kaydı düşürmez."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _int(value: Any) -> Optional[int]:
    parsed = _num(value)
    return None if parsed is None else int(parsed)


def _trim(value: Any, limit: int = DETAIL_MAX) -> Optional[str]:
    text = _s(value)
    if text is None:
        return None
    return text[: max(0, int(limit))] or None


# --------------------------------------------------------------------------
# SAF kayıt kurucu
# --------------------------------------------------------------------------

def build_intent(
    *,
    at: str,
    symbol: Any,
    direction: Any,
    stage: str,
    decision: str,
    strategy: Any = None,
    source: Any = None,
    reason: Any = None,
    detail: Any = None,
    intent_id: Any = None,
    extra: Optional[Dict[str, Any]] = None,
    price: Any = None,
    stop_price: Any = None,
    tp1_price: Any = None,
    leverage: Any = None,
) -> Dict[str, Any]:
    """Tek bir niyet satırını kur — SAF (IO yok, saat okuma YOK).

    `at` çağıran tarafından geçilir; böylece kayıt tek tek test edilebilir
    ve motor yolundaki bir kayıt için ikinci bir saat okuması yapılmaz.
    Tüm okumalar savunmalıdır: bozuk/eksik girdi kayıt üretmeyi engellemez,
    yalnız o alan `None` kalır.

    D27/B — `price`/`stop_price`/`tp1_price`/`leverage`: karşı-olgu defteri
    "girilseydi ne olurdu"yu bu dört sayıyla kurar. Niyetin KALICI izi bu
    satırdır: bekleyen karşı-olgu kuyruğu süreç-içidir ve restart'ta
    kaybolur, ama bu satır `logs/trades.jsonl`'de durur — yani bir restart
    ölçümü geciktirir, kaydı YOK ETMEZ. Dördü de `None` olabilir
    ("ölçülmedi"); uydurma değer YAZILMAZ.
    """
    return {
        "at": _s(at),
        "intent_id": _s(intent_id),
        "symbol": _symbol(symbol),
        "direction": _direction(direction),
        "stage": _token(stage),
        "decision": _token(decision),
        "strategy": _s(strategy),
        "source": _s(source),
        "reason": _token(reason),
        "detail": _trim(detail),
        "price": _num(price),
        "stop_price": _num(stop_price),
        "tp1_price": _num(tp1_price),
        "leverage": _int(leverage),
        "extra": dict(extra) if isinstance(extra, dict) else {},
    }


# --------------------------------------------------------------------------
# Süreç-içi sayaçlar
# --------------------------------------------------------------------------

_lock = threading.Lock()
_since: str = datetime.now(timezone.utc).isoformat()
_total = 0
_by_stage: Dict[str, int] = {}
_by_decision: Dict[str, int] = {}
_by_reason: Dict[str, int] = {}
_logged = 0
_log_dropped = 0


def _bucket_stage(value: Optional[str]) -> str:
    return value if value in KNOWN_STAGES else REASON_OTHER_BUCKET


def _bucket_decision(value: Optional[str]) -> str:
    return value if value in KNOWN_DECISIONS else REASON_OTHER_BUCKET


def _bucket_reason(value: Optional[str]) -> str:
    if value is None:
        return REASON_NONE_BUCKET
    return value if value in KNOWN_REASONS else REASON_OTHER_BUCKET


def record(
    *,
    at: str,
    symbol: Any,
    direction: Any,
    stage: str,
    decision: str,
    strategy: Any = None,
    source: Any = None,
    reason: Any = None,
    detail: Any = None,
    intent_id: Any = None,
    extra: Optional[Dict[str, Any]] = None,
    price: Any = None,
    stop_price: Any = None,
    tp1_price: Any = None,
    leverage: Any = None,
) -> Optional[Dict[str, Any]]:
    """Niyeti sayaçlara işle ve JSONL kuyruğuna bırak.

    ASLA istisna yükseltmez: bu bir teşhis kaydıdır, güvenlik kilidi değil.
    Kuyruğa yazım `forensics_log.append_soon` iledir — O(1), diske DOKUNMAZ
    (gerçek `write()` ayrı iş parçacığındadır, bkz. D21-R3).

    Döner: kurulan satır (testler ve çağıranın teşhisi için), hata hâlinde
    `None`.
    """
    global _total, _logged, _log_dropped
    try:
        payload = build_intent(
            at=at,
            symbol=symbol,
            direction=direction,
            stage=stage,
            decision=decision,
            strategy=strategy,
            source=source,
            reason=reason,
            detail=detail,
            intent_id=intent_id,
            extra=extra,
            price=price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            leverage=leverage,
        )
    except Exception:  # pragma: no cover - saf kurucu, yine de akış kesilmesin
        return None

    stage_key = _bucket_stage(payload.get("stage"))
    decision_key = _bucket_decision(payload.get("decision"))
    reason_key = _bucket_reason(payload.get("reason"))

    try:
        with _lock:
            _total += 1
            _by_stage[stage_key] = _by_stage.get(stage_key, 0) + 1
            _by_decision[decision_key] = _by_decision.get(decision_key, 0) + 1
            _by_reason[reason_key] = _by_reason.get(reason_key, 0) + 1
    except Exception:  # pragma: no cover - kilit/sözlük arızası akışı kesmez
        pass

    written = False
    try:
        # Yerel import: monkeypatch'lenebilir kalsın (testler modülün
        # kendisini yamalar) ve modül yükleme zinciri kısa olsun.
        from src.strategies.scalper import forensics_log

        written = bool(forensics_log.append_soon("intent", payload))
    except Exception:
        written = False

    try:
        with _lock:
            if written:
                _logged += 1
            else:
                _log_dropped += 1
    except Exception:  # pragma: no cover
        pass
    return payload


def counters_snapshot() -> Dict[str, Any]:
    """Sayaçların anlık görüntüsü — O(1), disk/DB işi YOK.

    DÜRÜSTLÜK: bu sayaçlar **süreç-içidir** ve süreç yeniden başlayınca
    SIFIRLANIR. `"window": "process_start"` tam da bunu söyler; `"since"`
    sayaçların başladığı andır (süreç başlangıcı ya da son
    `reset_counters()`). Kalıcı tarihçe için `logs/trades.jsonl`.
    """
    with _lock:
        return {
            "since": _since,
            "window": "process_start",
            "total": _total,
            "by_stage": dict(_by_stage),
            "by_decision": dict(_by_decision),
            "by_reason": dict(_by_reason),
            "logged": _logged,
            "log_dropped": _log_dropped,
        }


def reset_counters() -> None:
    """Sayaçları sıfırla — YALNIZ testler için (motor yolundan çağrılmaz)."""
    global _since, _total, _logged, _log_dropped
    with _lock:
        _since = datetime.now(timezone.utc).isoformat()
        _total = 0
        _by_stage.clear()
        _by_decision.clear()
        _by_reason.clear()
        _logged = 0
        _log_dropped = 0


# --------------------------------------------------------------------------
# Çevrimdışı özet (logs/trades.jsonl)
# --------------------------------------------------------------------------

def summarize_intents(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """`logs/trades.jsonl`'den okunmuş niyet satırlarının dağılımı — SAF.

    `rows`: `event="intent"` satırları. Dosyanın TAMAMI da verilebilir:
    `event` alanı olup `"intent"` OLMAYAN satırlar (entry/exit/postmortem)
    atlanır. Sözlük olmayan satırlar da atlanır — bozuk bir satır özeti
    düşürmemeli.

    Döner: `{"total", "by_reason": [...], "by_decision": {...},
    "by_stage": {...}}`. `by_reason` çoktan aza sıralıdır ve
    `share_pct` toplamı yuvarlama payıyla ~100'dür.
    """
    total = 0
    by_reason: Dict[str, int] = {}
    by_decision: Dict[str, int] = {}
    by_stage: Dict[str, int] = {}

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        event = row.get("event")
        if event is not None and _token(event) != "intent":
            continue
        total += 1
        reason_key = _bucket_reason(_token(row.get("reason")))
        stage_key = _bucket_stage(_token(row.get("stage")))
        decision_key = _bucket_decision(_token(row.get("decision")))
        by_reason[reason_key] = by_reason.get(reason_key, 0) + 1
        by_stage[stage_key] = by_stage.get(stage_key, 0) + 1
        by_decision[decision_key] = by_decision.get(decision_key, 0) + 1

    table: List[Dict[str, Any]] = []
    for name, count in by_reason.items():
        table.append({
            "reason": name,
            "label": REASON_LABELS.get(name, ""),
            "count": count,
            "share_pct": round(count / total * 100.0, 1) if total else 0.0,
        })
    # Çoktan aza; eşitlikte ada göre (kararlı sıra = kararlı rapor).
    table.sort(key=lambda item: (-item["count"], item["reason"]))

    return {
        "total": total,
        "by_reason": table,
        "by_decision": dict(
            sorted(by_decision.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "by_stage": dict(
            sorted(by_stage.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
    }
