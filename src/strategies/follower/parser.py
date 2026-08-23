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

## Seviye SIRASI doğrulaması (giriş olayları)

2026-08-23'te TV'de yakalanan gerçek gövdelerde sıra DAİMA şudur::

    LONG  (🟢 BUY):  SL < Price < TP1 < TP2 < TP3
    SHORT (🔴 SELL): SL > Price > TP1 > TP2 > TP3

Bu sıra bozuksa mesaj AlgoPro V1.6'nın ürettiği bir giriş DEĞİLDİR (alert
biçimi değişmiş, alanlar yer değiştirmiş ya da gövde başka bir kaynaktan
gelmiştir). Bir "SL"yi TP sanıp ters tarafa emir koymaktansa REDDETMEK
doğrudur → ``FollowerParseError`` (HTTP 422). Doğrulama yalnız ``entry``
olaylarında ve yalnız mesajda VAR OLAN alanlar üzerinde yapılır; eşitlik de
tutarsızlıktır (sıfır mesafeli stop/TP emir olarak konulamaz).

## İkincil biçim — açık ``key=value`` şablonu

Elle kurulan alarmlar, curl testleri ve ileride başka bir kaynak için::

    src=algopro kind=entry buy BTCUSDT tf=1 px=77126.08 sl=… tp1=… tp2=… tp3=…

``kind=`` görülürse bu biçim kullanılır; aksi halde birincil (AlgoPro) yol.
Bu yol yalnız ELLE (secret ile) kurulan isteklerde geçerlidir — köprü asla
bu biçimde bir gövde İLETMEZ. ``kind=entry`` için alan şartı birincil yolla
AYNIDIR: ``px`` + ``sl`` + ``tp1`` + ``tp2`` + ``tp3`` zorunludur.

## KATI TANIYICI (düşmanca inceleme, 2026-08-23 — bulgu 2 ve 5)

Eski davranış FAIL-OPEN'dı: tanınmayan bir gövdede yalnızca bir yön kelimesi
("bullish", "long", …) geçmesi GİRİŞ olayı üretiyordu. Serbest metin, LuxAlgo
şablonu ya da bozulmuş bir AlgoPro mesajı böylece POZİSYON açtırabilirdi.

Artık bir gövde ancak AŞAĞIDAKİLERİN HEPSİNİ taşıyorsa AlgoPro V1.6 alarmı
sayılır (``algopro_alert_kind`` — köprü de AYNI tanıyıcıyı kullanır):

1. **BAŞLIKTA** (ilk ``|`` bölümünde) olay anahtarı: ``BUY``/``SELL`` (giriş)
   ya da ``EXIT`` / ``TP1|TP2|TP3 HIT`` / ``SL HIT``;
2. borsa nitelikli sembol bölümü: ``| BINANCE:<SEMBOL>USDT[.P] |``;
3. ``| TF: <değer>`` alanı;
4. ``| Price: <pozitif sayı>`` alanı;
5. **giriş olaylarında ayrıca** dört seviyenin HEPSİ: ``SL:``, ``TP1:``,
   ``TP2:``, ``TP3:`` (pozitif ve sonlu) + yöne uygun sıralama.

Eksik alan → ``FollowerParseError`` (HTTP 422) + WARNING. "Bir seviyeyi
yanlış yorumlayıp ters tarafa emir koymaktansa işlemi kaçırmak doğrudur."

⚠️ SONUÇ: ``levels.py``'deki k×ATR yedek kuralı GİRİŞLER İÇİN ARTIK
ULAŞILAMAZ (mesajda SL olmayan bir giriş 422 alır). Kural, mesajdaki bir
seviyenin girişin yanlış tarafında kalması hâlinde ikinci savunma katmanı
olarak KORUNUR (bkz. ``levels.resolve_levels``).

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
# Katı AlgoPro parmak izi: borsa nitelikli sembol bölümü (`| BINANCE:BTCUSDT |`).
# `{{exchange}}:{{ticker}}` AlgoPro V1.6'nın kendi alert() metninden gelir;
# elle yazılmış LuxAlgo/BotV3 şablonlarında ve serbest metinde BULUNMAZ.
_ALGOPRO_SYMBOL_RE = re.compile(
    r"(?:^|\|)\s*BINANCE\s*:\s*([A-Z0-9]{2,15}USDT)(?:\.P)?\s*(?=\||$)",
    re.IGNORECASE,
)
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


def _validate_entry_level_order(
    direction: Optional[Direction],
    price: Optional[float],
    levels: MessageLevels,
) -> None:
    """Giriş mesajındaki seviyelerin YÖNE göre sırasını doğrula (fail-closed).

    LONG: ``SL < Price < TP1 < TP2 < TP3`` — SHORT tersi. Yalnız mesajda VAR
    OLAN alanlar zincire girer; eksik alan doğrulamayı atlatmaz, sadece o
    halkayı düşürür. Tutarsızlık ``FollowerParseError`` (HTTP 422) demektir:
    seviyeleri yanlış yorumlamak ters tarafa emir koymak olurdu.
    """
    if direction is None:
        return

    # Zincir: artan (LONG) ya da azalan (SHORT) olması beklenen etiketli
    # değerler. ``None`` olanlar çıkarılır.
    chain: List[Tuple[str, float]] = []
    for label, value in (
        ("SL", levels.sl),
        ("Price", price),
        ("TP1", levels.tp1),
        ("TP2", levels.tp2),
        ("TP3", levels.tp3),
    ):
        if value is not None and math.isfinite(value) and value > 0:
            chain.append((label, float(value)))
    if len(chain) < 2:
        return

    ascending = direction == Direction.LONG
    expected = "SL < Price < TP1 < TP2 < TP3" if ascending else (
        "SL > Price > TP1 > TP2 > TP3"
    )
    for (prev_label, prev_value), (label, value) in zip(chain, chain[1:]):
        ordered = value > prev_value if ascending else value < prev_value
        if not ordered:
            raise FollowerParseError(
                f"Seviye sırası {direction.value} yönüyle tutarsız: "
                f"{prev_label}={prev_value:g}, {label}={value:g} "
                f"(beklenen {expected}) — giriş reddedildi"
            )


def _require_entry_levels(levels: MessageLevels) -> None:
    """Giriş olayında DÖRT seviyenin de bulunmasını şart koş (fail-closed).

    Eksik seviye = eksik merdiven. Eskiden eksik TP'ler RR kuralıyla,
    eksik SL k×ATR ile TÜRETİLİYORDU; bu, biçimi bozulmuş (ya da AlgoPro'ya
    ait olmayan) bir gövdeyle POZİSYON açmanın kapısıydı.
    """
    missing = [
        label
        for label, value in (
            ("SL", levels.sl),
            ("TP1", levels.tp1),
            ("TP2", levels.tp2),
            ("TP3", levels.tp3),
        )
        if value is None
    ]
    if missing:
        raise FollowerParseError(
            f"Giriş olayında zorunlu seviye(ler) eksik: {', '.join(missing)} "
            f"— AlgoPro V1.6 girişleri DÖRT seviyeyi de taşır; giriş reddedildi"
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

    price = _parse_positive_float(fields.get("px"))
    levels = MessageLevels(
        sl=_parse_positive_float(fields.get("sl")),
        tp1=_parse_positive_float(fields.get("tp1")),
        tp2=_parse_positive_float(fields.get("tp2")),
        tp3=_parse_positive_float(fields.get("tp3")),
    )
    if kind == KIND_ENTRY:
        if price is None:
            raise FollowerParseError(
                "Giriş olayında 'px=' alanı yok ya da geçersiz — giriş reddedildi"
            )
        _require_entry_levels(levels)
        _validate_entry_level_order(direction, price, levels)

    return FollowerEvent(
        kind=kind,
        symbol=symbol,
        direction=direction,
        timeframe=str(fields.get("tf", "")).strip(),
        price=price,
        ts=str(fields.get("t", "")).strip()[:64],
        levels=levels,
        score=_parse_finite_float(fields.get("score")),
        tqi=_parse_finite_float(fields.get("tqi")),
        source=str(fields.get("src", "algopro")).strip().lower()[:32] or "algopro",
    )


def _parse_algopro_message(text: str) -> FollowerEvent:
    """Birincil biçim: AlgoPro V1.6'nın ``|`` ayraçlı kendi mesajı (KATI).

    Modül başlığındaki 5 koşulun hepsi aranır; biri eksikse gövde AlgoPro
    V1.6 alarmı DEĞİLDİR ve ``FollowerParseError`` ile reddedilir.
    """
    segments = [seg.strip() for seg in text.split("|")]
    if len(segments) > _MAX_SEGMENTS:
        raise FollowerParseError(f"Gövde çok fazla alan içeriyor (>{_MAX_SEGMENTS})")

    fields: Dict[str, str] = {}
    for segment in segments:
        if not segment:
            continue
        if _normalize_symbol(segment):
            continue
        key, sep, value = segment.partition(":")
        if sep:
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized:
                fields[normalized] = value.strip()

    # (2) Borsa nitelikli sembol — parmak izinin ANA direği. Çıplak "BTCUSDT"
    # geçen serbest metin bu kapıdan geçemez.
    symbol_match = _ALGOPRO_SYMBOL_RE.search(text)
    if symbol_match is None:
        raise FollowerParseError(
            "AlgoPro V1.6 biçimi değil: '| BINANCE:<SEMBOL>USDT |' bölümü yok "
            "— gövde iletilmedi/işlenmedi (alert biçimini doğrula)"
        )
    symbol = symbol_match.group(1).upper()

    # (1) Olay anahtarı YALNIZ BAŞLIKTAN okunur. Gövde taraması KALDIRILDI:
    # "Exit: trailing" gibi bir ALAN ya da serbest metindeki bir yön kelimesi
    # olay türünü sessizce değiştiremesin (fail-open kapatıldı).
    header = segments[0].upper() if segments else ""
    header_words = [word.lower() for word in _WORD_RE.findall(header)]

    kind: Optional[str] = None
    for keyword, mapped in _KIND_KEYWORDS:
        if keyword in header:
            kind = mapped
            break
    # HIT/EXIT olaylarında yön mesajda varsa taşınır (zorunlu değil).
    direction = _resolve_direction(header_words, "")
    if kind is None:
        if direction is None:
            raise FollowerParseError(
                "AlgoPro V1.6 biçimi değil: başlıkta olay anahtarı yok "
                "(BUY/SELL/EXIT/TP1|TP2|TP3 HIT/SL HIT) — alert biçimi "
                "değişmiş olabilir"
            )
        kind = KIND_ENTRY

    # (3) ve (4): TF + Price her AlgoPro olayında vardır (ölçüldü).
    timeframe = str(fields.get("tf", "")).strip()
    if not timeframe:
        raise FollowerParseError(
            "AlgoPro V1.6 biçimi değil: '| TF: …' alanı yok"
        )
    price = _parse_positive_float(fields.get("price"))
    if price is None:
        raise FollowerParseError(
            "AlgoPro V1.6 biçimi değil: '| Price: …' alanı yok ya da geçersiz"
        )

    levels = MessageLevels(
        sl=_parse_positive_float(fields.get("sl")),
        tp1=_parse_positive_float(fields.get("tp1")),
        tp2=_parse_positive_float(fields.get("tp2")),
        tp3=_parse_positive_float(fields.get("tp3")),
    )
    if kind == KIND_ENTRY:
        _require_entry_levels(levels)
        _validate_entry_level_order(direction, price, levels)

    return FollowerEvent(
        kind=kind,
        symbol=symbol,
        direction=direction,
        timeframe=timeframe,
        price=price,
        ts=str(fields.get("time", "") or fields.get("t", "")).strip()[:64],
        levels=levels,
        score=_parse_finite_float(fields.get("score")),
        tqi=_parse_finite_float(fields.get("tqi")),
        source="algopro",
    )


def algopro_alert_kind(raw: str) -> Optional[str]:
    """Gövde GERÇEK bir AlgoPro V1.6 alarmı mı? Öyleyse ``kind``, değilse None.

    Köprü (``src/services/follower_forwarder.py``) ve takipçi girişi AYNI
    tanıyıcıyı kullanır: iki yerde iki farklı "AlgoPro mı?" kuralı olması,
    ana botun ilettiği bir gövdenin takipçide 422 alması (ya da tersi)
    demektir. ``?src=`` / ``TV_SOURCE_ALLOWLIST`` bu karara GİRMEZ — karar
    yalnız GÖVDENİN biçimine dayanır.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return _parse_algopro_message(text).kind
    except FollowerParseError:
        return None
    except Exception:  # savunmacı: tanıyıcı ASLA çağıranı düşürmez
        return None


def parse_follower_event(raw: str) -> FollowerEvent:
    """AlgoPro alarm gövdesini çöz. Hata → ``FollowerParseError``."""
    text = str(raw or "").strip()
    if not text:
        raise FollowerParseError("Boş gövde")

    templated = _parse_key_value_template(text)
    if templated is not None:
        return templated
    return _parse_algopro_message(text)
