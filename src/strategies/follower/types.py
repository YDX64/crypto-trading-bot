"""Takipçi halkasının ortak veri sözleşmesi — bağımlılıksız (yalnız stdlib).

``scalper/types.py`` ile aynı ilke: parser/levels/plan saf fonksiyonlar
olarak test edilebilir kalsın diye IO katmanları (brackets/executor/exits/
engine) bu tipleri üretir/tüketir.

Yön (``Direction``) scalper tiplerinden YENİDEN KULLANILIR — iki halka aynı
LONG/SHORT sözleşmesini paylaşır, ikinci bir enum tanımı kayma riski yaratır.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.strategies.scalper.types import Direction

# AlgoPro alarm türleri. TV koşul adlarıyla eşleme docs/RUNBOOK.md
# "AlgoPro takipçi halkası" bölümündeki tablodadır.
KIND_ENTRY = "entry"
KIND_EXIT = "exit"
KIND_TP1 = "tp1"
KIND_TP2 = "tp2"
KIND_TP3 = "tp3"
KIND_SL = "sl"

FOLLOWER_KINDS: Tuple[str, ...] = (
    KIND_ENTRY,
    KIND_EXIT,
    KIND_TP1,
    KIND_TP2,
    KIND_TP3,
    KIND_SL,
)

# Seviye kaynağı — kalibrasyon defterinde (state/follower_levels.jsonl) ve
# /follower/status'ta görünür.
LEVEL_SOURCE_MESSAGE = "message"   # tüm seviyeler AlgoPro mesajından
LEVEL_SOURCE_MIXED = "mixed"       # SL mesajdan, eksik TP'ler RR ile türetildi
LEVEL_SOURCE_ATR = "atr"           # mesajda seviye yok → k×ATR kuralı


class FollowerParseError(ValueError):
    """AlgoPro alarm gövdesi çözülemedi (HTTP 422 karşılığı)."""


class FollowerRejected(Exception):
    """Olay kabul edildi ama bir kapıda reddedildi (HTTP 200, accepted=False).

    ``reason`` insan-okur Türkçe gerekçedir ve API yanıtına + loga aynen
    yazılır; sessiz ret YOKTUR (bkz. engine'in ret sayaçları).
    """

    def __init__(self, reason: str, *, code: str = "rejected"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class MessageLevels:
    """AlgoPro mesajının taşıdığı (opsiyonel) mutlak seviyeler."""

    sl: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None

    @property
    def has_sl(self) -> bool:
        return self.sl is not None and self.sl > 0

    @property
    def has_any(self) -> bool:
        return any(v is not None for v in (self.sl, self.tp1, self.tp2, self.tp3))

    def as_dict(self) -> Dict[str, Optional[float]]:
        return {"sl": self.sl, "tp1": self.tp1, "tp2": self.tp2, "tp3": self.tp3}


@dataclass(frozen=True)
class FollowerEvent:
    """Çözümlenmiş AlgoPro alarmı.

    ``direction`` yalnız ``entry`` için ZORUNLUDUR; exit/tp/sl olaylarında
    AlgoPro mesajı yön taşımayabilir (o zaman None) ve motor açık pozisyonun
    yönünü kullanır.
    """

    kind: str
    symbol: str
    direction: Optional[Direction]
    timeframe: str
    price: Optional[float]
    ts: str
    levels: MessageLevels
    # AlgoPro'nun mesajda taşıdığı sinyal kalitesi alanları (``TQI: .45``,
    # ``Score: 8``) — deftere/telemetriye yazılır; ``score`` opsiyonel
    # ``FOLLOWER_MIN_SCORE`` filtresinde kullanılır (0 = kapalı).
    score: Optional[float] = None
    tqi: Optional[float] = None
    source: str = "algopro"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "direction": self.direction.value if self.direction else None,
            "tf": self.timeframe,
            "px": self.price,
            "t": self.ts,
            "levels": self.levels.as_dict(),
            "score": self.score,
            "tqi": self.tqi,
            "source": self.source,
        }


@dataclass(frozen=True)
class FollowerLevels:
    """Bir giriş için çözülmüş mutlak SL/TP seviyeleri.

    ``stop_distance`` mutlak fiyat birimidir; ``sl_pct`` giriş fiyatına göre
    yüzdedir ve kaldıraç formülünün PAYDASIDIR (bkz. plan.resolve_leverage).
    """

    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    stop_distance: float
    sl_pct: float
    source: str
    atr_value: Optional[float] = None
    message_levels: MessageLevels = MessageLevels()
    # Mesaj seviyesi kullanılamadığında (ters taraf, sıralama bozuk) neden
    # hesaplanana düşüldüğü — kalibrasyon defterine ve loga yazılır.
    warnings: Tuple[str, ...] = ()

    @property
    def tps(self) -> Tuple[float, float, float]:
        return (self.tp1, self.tp2, self.tp3)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "entry": self.entry,
            "sl": self.stop,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "stop_distance": self.stop_distance,
            "sl_pct": self.sl_pct,
            "source": self.source,
            "atr": self.atr_value,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class LeverageBracket:
    """Borsanın sembol/notional bazlı kaldıraç dilimi (/fapi/v1/leverageBracket)."""

    max_leverage: int
    maint_margin_ratio: float
    notional_floor: float = 0.0
    notional_cap: float = float("inf")


@dataclass(frozen=True)
class FollowerPlan:
    """Tek bir girişin tam planı — deftere ve /follower/status'a aynen yazılır."""

    symbol: str
    direction: Direction
    levels: FollowerLevels
    leverage: int
    leverage_target: int
    leverage_cap_reason: str
    sl_pct: float
    sl_roi_pct: float
    tp_roi_pct: Tuple[float, float, float]
    margin_usdt: float
    notional_usdt: float
    quantity: float
    tp_quantities: Tuple[float, float, float]
    equity_usdt: float
    maint_margin_ratio: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction.value,
            "leverage": self.leverage,
            "leverage_target": self.leverage_target,
            "leverage_cap_reason": self.leverage_cap_reason,
            "sl_pct": self.sl_pct,
            "sl_roi_pct": self.sl_roi_pct,
            "tp_roi_pct": list(self.tp_roi_pct),
            "margin_usdt": self.margin_usdt,
            "notional_usdt": self.notional_usdt,
            "quantity": self.quantity,
            "tp_quantities": list(self.tp_quantities),
            "equity_usdt": self.equity_usdt,
            "mmr": self.maint_margin_ratio,
            "levels": self.levels.as_dict(),
        }

    def ledger_note(self) -> str:
        """`scalp_trades.notes` için makinece okunabilir boyutlama özeti.

        Kullanıcı kararı (2026-08-23): her işlem için lev, sl_pct, sl_roi ve
        margin deftere YAZILIR. Ayrı bir şema alanı açmadan tracker'ın mevcut
        ``k=v;k=v`` notes sözleşmesiyle uyumlu tutulur.
        """
        return (
            f"follower;lev={self.leverage};sl_pct={self.sl_pct:.4f};"
            f"sl_roi={self.sl_roi_pct:.2f};margin={self.margin_usdt:.4f};"
            f"lev_target={self.leverage_target};lev_cap={self.leverage_cap_reason};"
            f"levels={self.levels.source}"
        )
