"""Karşı-olgu defteri — DURUM katmanı (D27/B). YALNIZ GÖZLEM.

Sorun (2026-08-24 kök-neden analizi, öneri 4): raporun bütün "filtre şu kadar
kazandırır/kaybettirir" rakamları **ÜST SINIR TAHMİNİ** olarak kalıyor. Bir
girişi engellediğinizde onun yerine — kapasite ve kayıp-cooldown serbest
kaldığı için — BAŞKA bir işlem açılır ve bu, kapalı işlem defterinden
çıkarılamaz. Yani "TV sağlaması 150+ sinyali reddetti, iyi mi yaptı?"
sorusunun bugün SAYISAL cevabı yok (kapının seçicilik gücü ölçüldü:
LONG p=0.894, SHORT p=0.368 — ölçülebilir SIFIR).

Bu modül o cevabı üretir: reddedilen her niyet için "girilseydi, MEVCUT
TP/SL kurallarıyla ne olurdu" H saat sonra ölçülür ve kalıcı olarak
`logs/trades.jsonl`'e (`event="counterfactual"`) yazılır.

Sözleşme (D21/D24 ile aynı disiplin)
------------------------------------
* **Motor davranışı DEĞİŞMEZ.** Buradan hiçbir kapı, boyutlama ya da çıkış
  kararı beslenmez. Tüm giriş noktaları istisna yutar.
* **YENİ REST AĞIRLIĞI SIFIR.** Çözüm, tarama turunun ZATEN çektiği
  mumlarla yapılır (`engine._evaluate_symbol` içindeki `ctx`). Mum yoksa
  hesap ERTELENİR (`pending` kalır) — ekstra bir kline isteği ASLA
  yapılmaz.
* **Look-ahead YOK.** Simülasyon yalnız niyet anından SONRA açılmış mumları
  görür (`counterfactual.window`), aynı mumda hem stop hem TP1 vurursa
  STOP kazanır (karamsar taraf). Ayrıntı: `counterfactual.simulate`.
* **Sınırlı hafıza.** Bekleyen kayıt sayısı `max_pending` ile, satır hacmi
  `dedup_sec` ile sınırlıdır; JSONL'in kendi günlük rotasyonu + 30 gün
  saklaması zaten vardır (`forensics_log`).

Dürüstlük notu (bekleyenler restart'ta KAYBOLUR)
------------------------------------------------
`_pending` **süreç-içidir**: restart'ta çözülmemiş niyetlerin ÖLÇÜMÜ kalıcı
olarak KAYBOLUR. D27 kaydı bir dönem "restart ölçümü geciktirir, kaydı yok
etmez" diyordu; **bu yanlıştı** (düşmanca inceleme, Y3): `event="intent"`
satırlarını yeniden simüle eden HİÇBİR kod yolu yoktur (`grep read_events`
→ yalnız `"counterfactual"`). Kalıcı olan yalnız şudur:

* niyetin KENDİSİ (`event="intent"`, fiyat/stop/TP1/kaldıraç alanlarıyla) —
  yani "böyle bir niyet vardı ve reddedildi" bilgisi kaybolmaz;
* ZATEN ÇÖZÜLMÜŞ satırlar (`event="counterfactual"`).

Kaybolan, o niyetin "girilseydi ne olurdu" ÖLÇÜMÜdür. 8 saatlik ufuk +
her deploy'da restart demek, bekleyen ölçümlerin çözülmeden düşmesi demektir;
rapor bunu `counters.pending` ve `registered` farkından değil, ancak
`logs/trades.jsonl`'deki `intent`/`counterfactual` satır sayılarını
kıyaslayarak görebilir. Rehidrasyon (intent satırlarından yeniden kurma)
BİLİNÇLİ olarak yapılMAdı: `configure()` motor `__init__`'inde senkron
çağrılır ve orada 30 günlük JSONL okumak başlangıcı bloklar.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from src.strategies.scalper import counterfactual as cf
from src.strategies.scalper.types import Direction, price_at_roi

#: Uçta/panoda gösterilecek son çözülmüş satır sayısı (süreç-içi halka).
RECENT_MAX = 200

#: Varsayılan ufuklar (saat). `.env` ile değiştirilir.
DEFAULT_HORIZONS: Tuple[float, ...] = (1.0, 4.0, 8.0)

#: Bekleyen kayıt tavanı. Dolarsa YENİ kayıt düşürülür (eskisi korunur) —
#: `forensics_log` kuyruğuyla AYNI ilke: bir teşhis kaydı için sınırsız RAM
#: tutmak, kaydın kendisinden pahalıdır.
DEFAULT_MAX_PENDING = 500

#: Aynı (sembol, yön, gerekçe) üçlüsü bu saniye içinde tekrar reddedilirse
#: YENİ kayıt AÇILMAZ, mevcut kaydın `dup_count`u artar. Tarama turu birkaç
#: saniyede bir döner: dedup olmadan tek bir kalıcı ret, günde binlerce
#: özdeş satır üretirdi (ölçüldü: 08-21'de 215 ters-yön sinyali tek günde
#: kapıda öldü). Ağırlık kaybolmaz — `dup_count` raporda `collapsed` olarak
#: görünür.
DEFAULT_DEDUP_SEC = 300.0

#: Bir bekleyen kayıt bu yaştan sonra ÇÖZÜLEMEDİYSE düşürülür (sembol tarama
#: evreninden çıkmış olabilir). En büyük ufkun çok üstünde tutulur.
DEFAULT_MAX_AGE_H = 48.0

#: Yaş süpürmesinin (`sweep_expired`) EN SIK koşacağı aralık (sn). Süpürme
#: O(bekleyen) saf bellek işidir (tavan `max_pending`), ama her tarama
#: turunda her sembol için koşmasının anlamı yok.
SWEEP_MIN_INTERVAL_SEC = 60.0

#: Planı olmayan bir niyet için referans girişin niyet anından AZAMİ gecikmesi
#: (sn). Giriş dilimi 5m, bağlam dilimi 15m'dir; 900 sn ikisini de bir mum +
#: pay ile karşılar. Bundan uzak bir "ilk mum" referans giriş sayılamaz.
PLAN_REF_MAX_LAG_SEC = 900.0

# Motor her turda yalnız son 150 giriş mumu getirir. 1m profilinde bu 2,5
# saattir; D27'nin 8 saatlik ufku tek bir turdaki pencereye sığmaz. Aynı
# mumları süreç içinde biriktirmek YENİ REST çağrısı açmadan tam pencereyi
# korur. Bir saatlik pay; niyetin yarım muma denk gelmesini, tarama jitter'ını
# ve en uzun mumun kapanışını kapsar.
CANDLE_HISTORY_PADDING_SEC = 3600.0


# --------------------------------------------------------------------------
# Modül durumu
# --------------------------------------------------------------------------

_lock = threading.RLock()

_enabled: bool = False
_horizons: Tuple[float, ...] = DEFAULT_HORIZONS
_max_pending: int = DEFAULT_MAX_PENDING
_dedup_sec: float = DEFAULT_DEDUP_SEC
_max_age_h: float = DEFAULT_MAX_AGE_H
#: Planı OLMAYAN niyetler (ör. TV sağlaması `/tv-signal`'da reddeder; orada
#: bir `ScalpSignal` ve dolayısıyla stop/TP1 YOKTUR) için yedek ROI politikası.
_tp1_roi_pct: float = 0.0
_stop_roi_pct: float = 0.0
_policy_leverage: int = 0

#: sembol → bekleyen kayıtlar (kayıt sırasına göre).
_pending: Dict[str, List[Dict[str, Any]]] = {}
_pending_count: int = 0
#: sembol → open_epoch → motorun zaten çektiği mum. Yalnız o sembolde
#: bekleyen karşı-olgu varken tutulur; son kayıt çözülünce hemen bırakılır.
_candle_history: Dict[str, Dict[float, Any]] = {}
#: Son GLOBAL yaş süpürmesinin anı (çağıranın saatiyle, saniye).
_last_sweep_epoch: float = 0.0

_registered: int = 0
_dedup_hits: int = 0
_dropped_full: int = 0
_expired: int = 0
_resolved: int = 0
_measured: int = 0
_logged: int = 0
_log_dropped: int = 0

_recent: Deque[Dict[str, Any]] = deque(maxlen=RECENT_MAX)


def configure(
    *,
    enabled: bool,
    horizons_h: Sequence[float] = DEFAULT_HORIZONS,
    max_pending: int = DEFAULT_MAX_PENDING,
    dedup_sec: float = DEFAULT_DEDUP_SEC,
    max_age_h: float = DEFAULT_MAX_AGE_H,
    tp1_roi_pct: float = 0.0,
    stop_roi_pct: float = 0.0,
    policy_leverage: int = 0,
) -> None:
    """Defteri kur. Motor başlarken BİR KEZ çağrılır; testler yeniden çağırır.

    `tp1_roi_pct`/`stop_roi_pct`/`policy_leverage`: planı OLMAYAN niyetler
    için yedek ROI politikası (bkz. `_fill_plan`). Sıfır bırakılırsa plansız
    niyetler ölçülemez ve `no_data` olarak raporlanır.
    """
    global _enabled, _horizons, _max_pending, _dedup_sec, _max_age_h
    global _tp1_roi_pct, _stop_roi_pct, _policy_leverage
    with _lock:
        try:
            _tp1_roi_pct = max(0.0, float(tp1_roi_pct or 0.0))
        except (TypeError, ValueError):
            _tp1_roi_pct = 0.0
        try:
            _stop_roi_pct = max(0.0, float(stop_roi_pct or 0.0))
        except (TypeError, ValueError):
            _stop_roi_pct = 0.0
        try:
            _policy_leverage = max(0, int(policy_leverage or 0))
        except (TypeError, ValueError):
            _policy_leverage = 0
        _enabled = bool(enabled)
        parsed = tuple(cf._horizons(horizons_h))
        _horizons = parsed or DEFAULT_HORIZONS
        try:
            _max_pending = max(0, int(max_pending))
        except (TypeError, ValueError):
            _max_pending = DEFAULT_MAX_PENDING
        try:
            _dedup_sec = max(0.0, float(dedup_sec))
        except (TypeError, ValueError):
            _dedup_sec = DEFAULT_DEDUP_SEC
        try:
            _max_age_h = max(0.0, float(max_age_h))
        except (TypeError, ValueError):
            _max_age_h = DEFAULT_MAX_AGE_H


def enabled() -> bool:
    with _lock:
        return _enabled


def horizons() -> Tuple[float, ...]:
    with _lock:
        return tuple(_horizons)


def parse_horizons(raw: Any) -> Tuple[float, ...]:
    """"1,4,8" ya da [1,4,8] → (1.0, 4.0, 8.0). Bozuk girdi varsayılana düşer."""
    if raw is None:
        return DEFAULT_HORIZONS
    if isinstance(raw, str):
        parts: List[Any] = [p for p in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        parts = [raw]
    parsed = tuple(cf._horizons(parts))
    return parsed or DEFAULT_HORIZONS


# --------------------------------------------------------------------------
# Kayıt
# --------------------------------------------------------------------------

def register(
    *,
    at: str,
    at_epoch: float,
    symbol: Any,
    direction: Any,
    reason: Any,
    price: Optional[float] = None,
    stop_price: Optional[float] = None,
    tp1_price: Optional[float] = None,
    leverage: Optional[int] = None,
    strategy: Any = None,
    source: Any = None,
    plan_source: Optional[str] = None,
    intent_id: Any = None,
) -> Optional[Dict[str, Any]]:
    """Reddedilen bir niyeti karşı-olgu kuyruğuna al.

    Döner: kuyruğa giren kayıt; dedup/kapasite nedeniyle girmediyse `None`.
    ASLA istisna yükseltmez.
    """
    global _pending_count, _registered, _dedup_hits, _dropped_full
    try:
        with _lock:
            if not _enabled:
                return None
            row = cf.build_pending(
                at=at,
                at_epoch=at_epoch,
                symbol=symbol,
                direction=direction,
                reason=reason,
                price=price,
                stop_price=stop_price,
                tp1_price=tp1_price,
                leverage=leverage,
                strategy=strategy,
                source=source,
                horizons_h=_horizons,
                intent_id=intent_id,
                extra={"plan_source": plan_source} if plan_source else None,
            )
            key = row.get("symbol")
            if not key or not row.get("direction") or row.get("at_epoch") is None:
                return None

            # D27 incelemesi (D2): `setdefault` KAPASİTE KONTROLÜNDEN SONRA
            # çalışır. Aksi hâlde defter doluyken her yeni sembol için kalıcı
            # bir BOŞ liste kalırdı (sınırsız anahtar birikimi).
            bucket = _pending.get(key) or []
            # Dedup: aynı (sembol, yön, gerekçe) penceresi içinde YENİ kayıt
            # açma; ağırlığı `dup_count`ta biriktir.
            if _dedup_sec > 0:
                for existing in reversed(bucket):
                    if (
                        existing.get("direction") == row.get("direction")
                        and existing.get("reason") == row.get("reason")
                        and abs(
                            float(row["at_epoch"])
                            - float(existing.get("at_epoch") or 0.0)
                        )
                        <= _dedup_sec
                    ):
                        existing["dup_count"] = int(
                            existing.get("dup_count", 1) or 1
                        ) + 1
                        _dedup_hits += 1
                        return None

            if _max_pending and _pending_count >= _max_pending:
                # Tavan dolduysa ÖNCE yaşı geçmişleri süpür: aksi hâlde
                # tarama evreninden çıkmış bir avuç sembol defteri kalıcı
                # olarak kilitler (D27 incelemesi Y2).
                _sweep_expired_locked(row["at_epoch"], force=True)
            if _max_pending and _pending_count >= _max_pending:
                _dropped_full += 1
                return None

            # Yaş süpürmesi kovayı değiştirmiş/silmiş olabilir. Eski listeyi
            # yeniden yazmak, sona ermiş kayıtları diriltir ve pending
            # sayacını gerçek kova boyutundan koparır.
            bucket = _pending.get(key) or []
            bucket.append(row)
            _pending[key] = bucket
            _pending_count += 1
            _registered += 1
            return row
    except Exception:  # pragma: no cover - teşhis kaydı akışı ASLA kesmez
        return None


# --------------------------------------------------------------------------
# Çözüm
# --------------------------------------------------------------------------

def _fill_plan(row: Dict[str, Any], candles: Sequence[Any]) -> None:
    """Planı OLMAYAN bir niyete referans giriş/stop/TP1 tak — YERİNDE.

    NEDEN GEREKLİ: en kritik kapı olan TV sağlaması `/tv-signal` isteğinde
    reddeder; orada bir `ScalpSignal` YOKTUR, dolayısıyla stop/TP1 de yoktur.
    Plansız bırakmak, raporun cevaplaması gereken asıl soruyu ("150+ sinyal
    gerçekten kötü müydü?") ölçülemez kılardı.

    **Look-ahead YOK:** referans giriş, niyet anından SONRA açılan İLK mumun
    `open` fiyatıdır — karar anında zaten önünüzde olan fiyat. (Niyet anını
    İÇEREN yarım mumun kapanışını kullanmak, karar anında bilinmeyen bir
    fiyatı kullanmak olurdu.)

    **Dürüst sınır:** stop/TP1 ROI POLİTİKASINDAN türer
    (`SCALPER_FIXED_STOP_ROI_PCT` / `SCALPER_TP1_ROI` / kaldıraç).
    `SCALPER_STOP_MODE=structural` iken canlı stop YAPISAL (swing + ATR
    tabanı) olurdu; bu yaklaşıklık `extra.plan_source="roi_policy"` ile
    işaretlenir ve rapor onu AYRI okuyabilir.
    """
    at_epoch = row.get("at_epoch")
    if at_epoch is None:
        return
    lev = row.get("leverage") or _policy_leverage
    if not lev or lev <= 0 or _tp1_roi_pct <= 0 or _stop_roi_pct <= 0:
        return
    direction = Direction.LONG if row.get("direction") == "LONG" else Direction.SHORT

    first_open: Optional[float] = None
    first_epoch: Optional[float] = None
    for candle in candles or []:
        opened = cf._open_epoch(candle)
        if opened is None or opened < float(at_epoch):
            continue
        if first_epoch is None or opened < first_epoch:
            first_epoch = opened
            first_open = cf._f(getattr(candle, "open", None))
    if not first_open or first_open <= 0:
        return
    # GECİKME KELEPÇESİ: mum penceresi ~12.5 saatliktir. Kayıt bundan
    # ESKİYSE (ör. sembol bir süre tarama evreninden çıkmış), bulunan "ilk
    # mum" niyet anından saatlerce sonra olabilir ve onun açılışını referans
    # giriş saymak UYDURMA olur. Böyle bir kayıt plansız kalır ve
    # `measured=False` ile kapanır — "ölçemedik" demek, yanlış ölçmekten
    # iyidir.
    if first_epoch is None or (first_epoch - float(at_epoch)) > PLAN_REF_MAX_LAG_SEC:
        return

    row["price"] = first_open
    row["leverage"] = int(lev)
    row["tp1_price"] = price_at_roi(first_open, _tp1_roi_pct, int(lev), direction)
    row["stop_price"] = price_at_roi(
        first_open, -abs(_stop_roi_pct), int(lev), direction
    )
    extra = row.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        row["extra"] = extra
    extra["plan_source"] = "roi_policy"
    extra["plan_ref_epoch"] = first_epoch


def _sweep_expired_locked(now_epoch: Any, *, force: bool = False) -> int:
    """Yaş sınırını aşan TÜM bekleyenleri düşür — SEMBOLDEN BAĞIMSIZ.

    NEDEN GEREKLİ (D27 incelemesi Y2 / inceleme-2 bulgu 7). Yaş kapısı bir
    zamanlar YALNIZ `resolve_symbol` içindeydi, yani **yalnız o an taranan
    sembolün** kovasına uygulanıyordu. Tarama evreninden çıkan bir sembolün
    satırları hiç iterate edilmiyor, dolayısıyla ASLA sona ermiyordu; probe:
    100 saatlik 5 kayıt, `max_age_h=48` → `expired: 0`, `pending: 5` kalıcı.
    `max_pending=500` dolduğunda defter TÜM semboller için sessizce ölçmeyi
    bırakıyordu — yani "hacim koruması" ölçümü öldürerek çalışıyordu.
    (`scalper_top_n=12` rotasyonu ve top-12'de olmak zorunda olmayan
    `/tv-signal` sembolleri bu senaryoyu sıradan kılar.)

    Kilit ÇAĞIRAN tarafından tutulur. Döner: düşürülen satır sayısı.
    """
    global _pending_count, _expired, _last_sweep_epoch
    try:
        now = float(now_epoch)
    except (TypeError, ValueError):
        return 0
    if not _max_age_h:
        return 0
    if not force and (now - _last_sweep_epoch) < SWEEP_MIN_INTERVAL_SEC:
        return 0
    _last_sweep_epoch = now

    cutoff = now - _max_age_h * cf.SECONDS_PER_HOUR
    dropped = 0
    for key in list(_pending.keys()):
        bucket = _pending.get(key) or []
        keep = []
        for row in bucket:
            try:
                at_epoch = float(row.get("at_epoch") or 0.0)
            except (TypeError, ValueError):
                at_epoch = 0.0
            if at_epoch < cutoff:
                dropped += 1
                continue
            keep.append(row)
        if keep:
            _pending[key] = keep
        else:
            _pending.pop(key, None)
            _candle_history.pop(key, None)
    if dropped:
        _expired += dropped
        _pending_count -= dropped
    return dropped


def sweep_expired(now_epoch: float, *, force: bool = True) -> int:
    """`_sweep_expired_locked`in dışarıya açık hâli (teşhis/test/operasyon)."""
    try:
        with _lock:
            if not _enabled:
                return 0
            return _sweep_expired_locked(now_epoch, force=force)
    except Exception:  # pragma: no cover - teşhis kaydı akışı ASLA kesmez
        return 0


def resolve_symbol(
    symbol: Any,
    candles: Sequence[Any],
    now_epoch: float,
) -> List[Dict[str, Any]]:
    """Bir sembolün olgunlaşmış bekleyenlerini ZATEN ÇEKİLMİŞ mumlarla çöz.

    `engine._evaluate_symbol` her tarama turunda, `ctx` kurulduktan HEMEN
    SONRA çağırır: mumlar zaten oradadır, **yeni REST çağrısı YOKTUR**.

    Olgunlaşmamış kayıt kuyrukta KALIR. Olgunlaşmış ama mum penceresi boş
    (ya da ilk TP/stop'a kadar kesintisiz olmayan / OPEN için son ufka
    ulaşmayan) kayıt `measured=False` ile çözülür —
    "ölçemedik" demek, uydurmaktan iyidir.

    Yaş kapısı bu çağrıda AMA sembolden BAĞIMSIZ koşar
    (`_sweep_expired_locked`, en sık `SWEEP_MIN_INTERVAL_SEC`'te bir): tarama
    evreninden çıkmış sembollerin kayıtları da böylece sona erer.
    """
    global _pending_count, _resolved, _measured, _logged, _log_dropped
    out: List[Dict[str, Any]] = []
    try:
        with _lock:
            if not _enabled:
                return out
            key = cf._symbol(symbol)
            normalized_key = key or ""
            bucket = _pending.get(normalized_key)
            if not bucket:
                _candle_history.pop(normalized_key, None)
                # Kova boş olsa bile GLOBAL süpürme koşar: bu çağrının asıl
                # ikinci işi, tarama evreninden ÇIKMIŞ sembollerin
                # kayıtlarını sona erdirmektir (Y2).
                _sweep_expired_locked(now_epoch)
                return out

            # D28: tek taramadaki 150 mumu değil, bu bekleyen kayıt yaşarken
            # motorun ZATEN çekmiş olduğu örtüşen pencerelerin birleşimini
            # kullan. Bu özellikle 1m × 150 (=2,5h) ile 8h ufuk arasındaki
            # sessiz ölçüm açığını kapatır; hiçbir ek Binance isteği yoktur.
            history = _candle_history.setdefault(normalized_key, {})
            for candle in candles or []:
                opened = cf._open_epoch(candle)
                if opened is not None:
                    history[opened] = candle
            max_horizon_sec = max(_horizons or DEFAULT_HORIZONS) * cf.SECONDS_PER_HOUR
            cutoff = float(now_epoch) - max_horizon_sec - CANDLE_HISTORY_PADDING_SEC
            # Çözüm taraması gecikmiş olabilir (sembol top-N dışında kaldı,
            # market-data geçici kesildi). Bekleyen EN ESKİ niyetin başlangıç
            # mumlarını sırf `now` ilerledi diye çözümden hemen önce silme.
            intent_epochs = [
                float(row["at_epoch"])
                for row in bucket
                if row.get("at_epoch") is not None
            ]
            if intent_epochs:
                cutoff = min(cutoff, min(intent_epochs) - CANDLE_HISTORY_PADDING_SEC)
            for opened in list(history):
                if opened < cutoff:
                    history.pop(opened, None)
            combined_candles = [history[opened] for opened in sorted(history)]

            keep: List[Dict[str, Any]] = []
            islenen = 0
            try:
                for row in bucket:
                    islenen += 1
                    # SATIR BAŞINA `try`: tek bir bozuk kayıt, AYNI sembolün
                    # diğer kayıtlarını kuyrukta öksüz bırakmamalı ve
                    # sayaçları yarım güncellenmiş hâlde terk etmemeli.
                    # Bozuk satır kuyrukta KALIR ve yaş sınırında düşer.
                    try:
                        if row.get("price") is None:
                            # Planı olmayan niyet (ör. TV sağlaması):
                            # referans girişi niyet anından SONRAKİ ilk
                            # mumdan tak. Look-ahead YOK.
                            _fill_plan(row, combined_candles)
                        resolved = cf.resolve(
                            pending=row,
                            candles=combined_candles,
                            now_epoch=now_epoch,
                        )
                    except Exception:  # pragma: no cover - bozuk satır savunması
                        keep.append(row)
                        continue
                    if resolved is None:
                        keep.append(row)
                        continue
                    # SIRA ÖNEMLİ: sayaç, satırı kuyruktan düşürmeye KARAR
                    # verildiği anda azalır; aşağıdaki adımlardan biri
                    # patlarsa `finally` kovayı bu karara uygun yazar.
                    _pending_count -= 1
                    _resolved += 1
                    if resolved.get("measured"):
                        _measured += 1
                    _recent.append(resolved)
                    out.append(resolved)
            finally:
                # D27 incelemesi (D3): geri-yazım `finally`dedir. Aksi hâlde
                # döngüden kaçan bir istisna, ÇÖZÜLMÜŞ satırları kovada
                # bırakırdı (bir sonraki turda yeniden çözülüp yeniden
                # loglanırlardı) ve `_pending_count` bucket ile tutarsız
                # kalırdı — o sayaç `register` kapasite kapısını besliyor.
                kalan = keep + list(bucket[islenen:])
                if kalan:
                    _pending[key] = kalan
                else:
                    _pending.pop(key, None)
                    _candle_history.pop(normalized_key, None)
            # GLOBAL yaş süpürmesi ÇÖZÜMDEN SONRA koşar: bu turda taranan
            # sembolün kayıtları önce ÖLÇÜLME şansını alır (yaşı geçmiş ama
            # mumları elde olan bir satırı sessizce düşürmek, ölçebilecekken
            # ölçmemek olurdu); geri kalan semboller burada sona erer.
            _sweep_expired_locked(now_epoch)
    except Exception:  # pragma: no cover - teşhis kaydı akışı ASLA kesmez
        return out

    # JSONL yazımı kilidin DIŞINDA: `append_soon` O(1)'dir ama modül kilidini
    # başka bir modülün kuyruğu için tutmanın gereği yok.
    for resolved in out:
        written = False
        try:
            from src.strategies.scalper import forensics_log

            written = bool(forensics_log.append_soon("counterfactual", resolved))
        except Exception:
            written = False
        with _lock:
            if written:
                _logged += 1
            else:
                _log_dropped += 1
    return out


# --------------------------------------------------------------------------
# Telemetri
# --------------------------------------------------------------------------

def counters_snapshot() -> Dict[str, Any]:
    """O(1) özet — `/api/status` ve `/scalper/status` buradan okur.

    DÜRÜSTLÜK: **süreç-içidir**, restart'ta sıfırlanır. Kalıcı tarihçe
    `logs/trades.jsonl` (`event="counterfactual"`); pencereli tablo
    `/scalper/counterfactual`.
    """
    with _lock:
        return {
            "enabled": _enabled,
            "window": "process_start",
            "horizons_h": list(_horizons),
            "dedup_sec": _dedup_sec,
            "max_pending": _max_pending,
            "pending": _pending_count,
            "registered": _registered,
            "dedup_hits": _dedup_hits,
            "dropped_full": _dropped_full,
            "expired": _expired,
            "resolved": _resolved,
            "measured": _measured,
            "logged": _logged,
            "log_dropped": _log_dropped,
            "candle_buffer_symbols": len(_candle_history),
            "candle_buffer_bars": sum(len(rows) for rows in _candle_history.values()),
        }


def recent(limit: int = 25) -> List[Dict[str, Any]]:
    """Süreç-içi son çözülmüş satırlar (en yeni önce)."""
    with _lock:
        rows = list(_recent)
    rows.reverse()
    try:
        n = max(0, int(limit))
    except (TypeError, ValueError):
        n = 25
    return rows[:n]


def summary(rows: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Ret gerekçesi × karşı-olgu sonucu tablosu.

    `rows` verilmezse SÜREÇ-İÇİ halka kullanılır (en fazla `RECENT_MAX`
    satır) — pencereli/kalıcı tablo için çağıran `forensics_log.read_events`
    ile satırları geçirir.
    """
    if rows is None:
        with _lock:
            rows = list(_recent)
    return cf.summarize(rows)


def pending_for(symbol: Any) -> List[Dict[str, Any]]:
    """YALNIZ testler/teşhis: bir sembolün bekleyen kayıtlarının kopyası."""
    with _lock:
        return [dict(row) for row in _pending.get(cf._symbol(symbol) or "", [])]


def bucket_keys() -> List[str]:
    """YALNIZ testler/teşhis: bekleyen kovası olan semboller (sıralı).

    Boş kova KALMAMALIDIR (D27 incelemesi D2): kapasite reddi ya da tam
    süpürme sonrası anahtar da silinir.
    """
    with _lock:
        return sorted(_pending.keys())


def reset() -> None:
    """YALNIZ testler için: durumu ve sayaçları sıfırla."""
    global _pending_count, _registered, _dedup_hits, _dropped_full
    global _expired, _resolved, _measured, _logged, _log_dropped
    global _last_sweep_epoch
    with _lock:
        _last_sweep_epoch = 0.0
        _pending.clear()
        _candle_history.clear()
        _recent.clear()
        _pending_count = 0
        _registered = 0
        _dedup_hits = 0
        _dropped_full = 0
        _expired = 0
        _resolved = 0
        _measured = 0
        _logged = 0
        _log_dropped = 0
