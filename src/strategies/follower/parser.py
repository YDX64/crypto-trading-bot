"""AlgoPro alarm gövdesi → ``FollowerEvent`` (SAF: IO yok, saat yok).

## Birincil biçim — AlgoPro V1.6'nın KENDİ ürettiği mesaj

TradingView'de alarm "Herhangi bir alert() fonksiyonu çağrısı" ("Any alert()
function call") modunda kurulduğunda mesajı SCRIPT üretir; kullanıcı şablon
YAZMAZ. 2026-08-23'te TV Desktop'ta sonda alarmıyla DOĞRULANAN gerçek gövdeler
(BTCUSDT 1dk)::

    🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8
        | SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54 | TP: fixed ×1.00
    🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77167.77

Beklenen diğer olaylar aynı kalıptadır: ``🟢 BUY | …`` (giriş, aynı alanlar),
``🎯 TP1 HIT``, ``🎯 TP2 HIT``, ``🏆 TP3 HIT``, ``⚪ EXIT``.

Ayrıştırma EMOJİYE DEĞİL, anahtar kelimelere ve ``|`` ayraçlı ``Anahtar: değer``
çiftlerine dayanır (emoji/boşluk/büyük-küçük harf toleranslı):

===================  =========
Gövdedeki anahtar     ``kind``
===================  =========
``BUY`` / ``SELL``    entry (yön LONG/SHORT)
``EXIT``              exit
``TP1 HIT``           tp1
``TP2 HIT``           tp2
``TP3 HIT``           tp3
``SL HIT``            sl
===================  =========

Seviyeler (``SL:``, ``TP1:``, ``TP2:``, ``TP3:``) BİRİNCİL yoldur — "AlgoPro ne
diyorsa" (bkz. ``levels.resolve_levels``). ``TQI`` ve ``Score`` telemetri/defter
için taşınır; ``Score`` opsiyonel ``FOLLOWER_MIN_SCORE`` filtresinde kullanılır.
``TP: fixed ×1.00`` gibi alanlar seviye DEĞİLDİR (yalnız ``tp1/tp2/tp3``).

## İkincil biçim — açık ``key=value`` şablonu

Elle kurulan alarmlar, curl testleri ve ileride başka bir kaynak için::

    src=algopro kind=entry buy BTCUSDT tf=1 px=77126.08 sl=… tp1=… tp2=… tp3=…

``kind=`` görülürse bu biçim kullanılır; aksi halde birincil (AlgoPro) yol.

Fail-closed: ``kind`` çözülemezse ``FollowerParseError`` (HTTP 422) — bir alarm
mesajı beklenmedik biçimdeyse "yön sinyali" sanıp işlem açmaktansa reddetmek
doğrudur.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from src.strategies.follower.types import (
    FOLLOWER_KINDS,
    KIND_ENTRY,
    KIND_EXIT,
    KIND_SL,
    KIND_TP1,
    KIND_TP2,
    KIND_TP3,
    FollowerEvent,
    FollowerParseError,
    MessageLevels,
)
from src.strategies.scalper.types import Direction

_SYMBOL_TOKEN_RE = re.compile(r"^([A-Z0-9]{2,15}USDT)(?:\.P)?$")
_SYMBOL_ANY_RE = re.compile(r"\b([A-Z0-9]{2,15}USDT)(?:\.P)?\b")
_WORD_RE = re.compile(r"[A-Z]+[0-9]*")

_LONG_WORDS = frozenset({"buy", "long", "bull", "bullish"})
_SHORT_WORDS = frozenset({"sell", "short", "bear", "bearish"})
_LONG_SUBSTRINGS = ("buy", "long", "bull")
_SHORT_SUBSTRINGS = ("sell", "short", "bear")

# Birincil (AlgoPro) yolda gövdede aranan olay anahtarları — SIRA ÖNEMLİ:
# "TP1 HIT" araması "TP1:" alanı taşıyan bir GİRİŞ mesajıyla karışmaz çünkü
# giriş mesajında "HIT" kelimesi geçmez.
_KIND_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    ("SL HIT", KIND_SL),
    ("STOP LOSS HIT", KIND_SL),
    ("TP1 HIT", KIND_TP1),
    ("TP2 HIT", KIND_TP2),
    ("TP3 HIT", KIND_TP3),
    ("EXIT", KIND_EXIT),
)

_MAX_SEGMENTS = 32
_MAX_TOKENS = 64


def _parse_positive_float(raw: Optional[str]) -> Optional[float]:
    """Sonlu ve pozitif float ise değeri, değilse None. ``.45`` kabul edilir."""
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "")
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _parse_finite_float(raw: Optional[str]) -> Optional[float]:
    """Telemetri alanları (TQI/Score) negatif/sıfır olabilir — yalnız sonluluk."""
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "")
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _normalize_symbol(token: str) -> Optional[str]:
    """``BINANCE:BTCUSDT.P`` → ``BTCUSDT``; eşleşmezse None."""
    candidate = str(token or "").upper().strip().split(":")[-1].strip()
    match = _SYMBOL_TOKEN_RE.match(candidate)
    return match.group(1) if match else None


def _resolve_direction(
    words: List[str], lowered_body: str
) -> Optional[Direction]:
    """Önce çıplak kelime, sonra alt-dize taraması. İki yön birden → hata."""
    long_hit = any(word in _LONG_WORDS for word in words)
    short_hit = any(word in _SHORT_WORDS for word in words)
    if long_hit and short_hit:
        raise FollowerParseError(
            "Yön belirsiz — mesajda hem alış hem satış kelimesi var"
        )
    if long_hit:
        return Direction.LONG
    if short_hit:
        return Direction.SHORT

    long_hit = any(word in lowered_body for word in _LONG_SUBSTRINGS)
    short_hit = any(word in lowered_body for word in _SHORT_SUBSTRINGS)
    if long_hit and short_hit:
        raise FollowerParseError(
            "Yön belirsiz — mesajda hem alış hem satış kelimesi var"
        )
    if long_hit:
        return Direction.LONG
    if short_hit:
        return Direction.SHORT
    return None


def _resolve_symbol(candidates: List[str], text: str) -> str:
    for token in candidates:
        symbol = _normalize_symbol(token)
        if symbol:
            return symbol
    match = _SYMBOL_ANY_RE.search(text.upper())
    if match:
        return match.group(1)
    raise FollowerParseError(
        "Sembol çözülemedi — mesajda BTCUSDT gibi bir USDT paritesi gerekli"
    )


def _parse_key_value_template(text: str) -> Optional[FollowerEvent]:
    """İkincil biçim: ``kind=… src=… px=…``. ``kind=`` yoksa None döner."""
    tokens = text.split()
    if len(tokens) > _MAX_TOKENS:
        raise FollowerParseError(f"Gövde çok fazla alan içeriyor (>{_MAX_TOKENS})")

    fields: Dict[str, str] = {}
    bare: List[str] = []
    for token in tokens:
        key, sep, value = token.partition("=")
        key = key.strip().lower()
        if sep and key:
            fields[key] = value.strip()
        else:
            bare.append(token)

    if "kind" not in fields:
        return None

    kind = fields.get("kind", "").strip().lower()
    if kind not in FOLLOWER_KINDS:
        raise FollowerParseError(
            f"'kind' çözülemedi ({kind!r}) — {'|'.join(FOLLOWER_KINDS)} olmalı"
        )

    explicit = fields.get("symbol") or fields.get("ticker") or ""
    symbol = _resolve_symbol([explicit, *bare], text)
    direction = _resolve_direction([w.lower() for w in bare], text.lower())
    if kind == KIND_ENTRY and direction is None:
        raise FollowerParseError(
            "Giriş olayında yön çözülemedi — 'buy'/'sell' (long/short) gerekli"
        )

    return FollowerEvent(
        kind=kind,
        symbol=symbol,
        direction=direction,
        timeframe=str(fields.get("tf", "")).strip(),
        price=_parse_positive_float(fields.get("px")),
        ts=str(fields.get("t", "")).strip()[:64],
        levels=MessageLevels(
            sl=_parse_positive_float(fields.get("sl")),
            tp1=_parse_positive_float(fields.get("tp1")),
            tp2=_parse_positive_float(fields.get("tp2")),
            tp3=_parse_positive_float(fields.get("tp3")),
        ),
        score=_parse_finite_float(fields.get("score")),
        tqi=_parse_finite_float(fields.get("tqi")),
        source=str(fields.get("src", "algopro")).strip().lower()[:32] or "algopro",
    )


def _parse_algopro_message(text: str) -> FollowerEvent:
    """Birincil biçim: AlgoPro V1.6'nın ``|`` ayraçlı kendi mesajı."""
    segments = [seg.strip() for seg in text.split("|")]
    if len(segments) > _MAX_SEGMENTS:
        raise FollowerParseError(f"Gövde çok fazla alan içeriyor (>{_MAX_SEGMENTS})")

    fields: Dict[str, str] = {}
    symbol_candidates: List[str] = []
    for segment in segments:
        if not segment:
            continue
        symbol = _normalize_symbol(segment)
        if symbol:
            symbol_candidates.append(symbol)
            continue
        key, sep, value = segment.partition(":")
        if sep:
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized:
                fields[normalized] = value.strip()

    # Çözüm SIRASI önemlidir (yanlış sınıflandırma = yanlış işlem):
    #   1) BAŞLIKTA olay anahtarı (SL HIT / TPn HIT / EXIT)
    #   2) BAŞLIKTA yön (BUY/SELL) → giriş
    #   3) GÖVDEDE olay anahtarı — başlık tanınmadıysa son çare
    #   4) GÖVDEDE yön → giriş
    # 2. adım 3. adımdan ÖNCE gelir: ileride giriş mesajına "Exit: trailing"
    # gibi bir ALAN eklenirse gövde taraması girişi "exit" sanardı.
    upper = text.upper()
    header = segments[0].upper() if segments else ""
    header_words = [word.lower() for word in _WORD_RE.findall(header)]

    kind: Optional[str] = None
    direction: Optional[Direction] = None

    for keyword, mapped in _KIND_KEYWORDS:
        if keyword in header:
            kind = mapped
            break

    if kind is None:
        direction = _resolve_direction(header_words, "")
        if direction is not None:
            kind = KIND_ENTRY
    else:
        # HIT/EXIT olaylarında yön mesajda varsa taşınır (zorunlu değil).
        direction = _resolve_direction(header_words, "")

    if kind is None:
        for keyword, mapped in _KIND_KEYWORDS:
            if keyword in upper:
                kind = mapped
                break

    if kind is None:
        direction = _resolve_direction([], text.lower())
        if direction is None:
            raise FollowerParseError(
                "Olay türü çözülemedi — gövdede BUY/SELL/EXIT/TP HIT/SL HIT yok "
                "(AlgoPro alert biçimi değişmiş olabilir)"
            )
        kind = KIND_ENTRY

    symbol = _resolve_symbol(symbol_candidates, text)

    return FollowerEvent(
        kind=kind,
        symbol=symbol,
        direction=direction,
        timeframe=str(fields.get("tf", "")).strip(),
        price=_parse_positive_float(fields.get("price")),
        ts=str(fields.get("time", "") or fields.get("t", "")).strip()[:64],
        levels=MessageLevels(
            sl=_parse_positive_float(fields.get("sl")),
            tp1=_parse_positive_float(fields.get("tp1")),
            tp2=_parse_positive_float(fields.get("tp2")),
            tp3=_parse_positive_float(fields.get("tp3")),
        ),
        score=_parse_finite_float(fields.get("score")),
        tqi=_parse_finite_float(fields.get("tqi")),
        source="algopro",
    )


def parse_follower_event(raw: str) -> FollowerEvent:
    """AlgoPro alarm gövdesini çöz. Hata → ``FollowerParseError``."""
    text = str(raw or "").strip()
    if not text:
        raise FollowerParseError("Boş gövde")

    templated = _parse_key_value_template(text)
    if templated is not None:
        return templated
    return _parse_algopro_message(text)
