#!/usr/bin/env python3
"""
data.binance.vision (resmi arşiv) -> backtest kline önbelleği doldurucu.

NEDEN: Bulut oturumundan `fapi.binance.com` HTTP 451 (bölge engeli) verir; harness
(`python -m src.strategies.scalper.backtest`) kline indiremez. Resmi arşiv
(`data.binance.vision`) erişilebilir ve fapi ile BİREBİR aynı mumları taşır
(BTCUSDT 5m: tests/fixtures/klines ile 1014/1014 mum alan alan aynı; 15m 384/384).
Bu betik arşivden indirip `src/strategies/scalper/kline_cache.py` biçiminde
önbelleği yazar; sonra harness AĞSIZ koşar (cache-miss'te data.py 451'i
`MarketDataHostError` ile yükseltir — sessiz sapma yok).

NASIL ÇALIŞIR (parite için KASITLI tasarım):
  * Hangi (sembol, zaman dilimi, mum sayısı, pencere) serisinin hangi dosyaya
    yazılacağı ELLE hesaplanmaz. Betik harness'in KENDİ fonksiyonlarını çağırır:
    `backtest.gather_symbol_data` / `backtest.gather_leader_series` (mum sayısı =
    gün*günlük-mum + ısınma, anahtar = `kline_cache.window_start_ms`, dosya adı =
    `kline_cache.cache_file`, yazım = `kline_cache.save`). Yalnız "ağ" katmanı
    (`FetchFn`) arşivden beslenir: harness'in sayfalama + `close_time <= end` süzgeci
    + boyut denetimi olduğu gibi çalışır.
  * Zaman dilimleri `backtest.resolve_timeframes(settings)` ile, lider piyasa kapısı
    (D15) `settings.scalper_market_gate*` ile belirlenir -> betik, backtest ile AYNI
    ortam değişkenleriyle (SCALPER_*) çalıştırılmalıdır (aksi halde farklı seri
    yazılır ve backtest cache-miss alır).
  * Önce GEÇİCİ bir hazırlık dizinine yazılır; HER dosya katı denetimden geçer
    (mum sayısı == anahtardan türeyen `needed`, her mum tam ızgarada, boşluk YOK,
    son mum `end-1`'de kapanır). Tek bir sorun -> hata ile durulur, hedef önbelleğe
    HİÇBİR ŞEY yazılmaz. Hepsi geçerse dosyalar tek tek `os.replace` (atomik) ile
    taşınır.
  * Her zip için `.CHECKSUM` (sha256) doğrulanır; uyuşmazlık = hata. İndirilen zip'ler
    `--download-cache` altında tutulur (tekrar indirilmez; her kullanımda sha256
    yeniden doğrulanır).
  * Aylık zip yalnız TAM ve BİTMİŞ aylar için denenir (404 -> günlük zip'lere düşer).
    Günlük zip yoksa (arşiv gecikmesi ~1-2 gün, ya da sembol o tarihte yok) durulur.
  * CSV başlık satırı olabilir de olmayabilir de; sütunlar `Candle.from_binance`
    (fapi dizisi -> Candle) ile çevrilir — harness'in canlı çekimiyle aynı dönüşüm.

Örnek (backtest ile AYNI SCALPER_* ortamıyla):
  .venv/bin/python scripts/fill_klines_cache_from_vision.py \
      --symbols BTCUSDT,ETHUSDT --start 2026-08-07 --end 2026-08-10
  .venv/bin/python -m src.strategies.scalper.backtest --strategies C \
      --symbols BTCUSDT,ETHUSDT --start 2026-08-07 --end 2026-08-10

Çıkış kodu: 0 başarı; 2 hata (veri/arşiv/doğrulama/argüman).
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import gzip
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.config import settings  # noqa: E402
from src.strategies.scalper import backtest, kline_cache  # noqa: E402
from src.strategies.scalper.setups import get_enabled  # noqa: E402
from src.strategies.scalper.types import Candle  # noqa: E402

DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT"
DEFAULT_BASE_URL = "https://data.binance.vision"
DEFAULT_CACHE_DIR = "data/klines_cache"            # backtest.py --cache-dir varsayılanı
DEFAULT_DOWNLOAD_CACHE = "data/vision_cache"
_DAY_MS = 86_400_000
_MAX_CSV_BYTES = 128 * 1024 * 1024                 # zip-bomb koruması (aylık 1m ~ 6 MB)
_CACHE_NAME_RE = re.compile(
    r"^(?P<symbol>[A-Z0-9]+)_(?P<interval>[0-9a-z]+)_(?P<start>-?\d+)_(?P<end>\d+)\.json\.gz$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class VisionError(RuntimeError):
    """Arşiv/ağ/doğrulama hatası — çağıran durur, önbelleğe bir şey yazılmaz."""


class ArchiveGapError(VisionError):
    """İstenen gün/ay arşivde yok (404) — pencere arşiv kapsamının dışında."""


# --------------------------------------------------------------------------
# Taşıma katmanı (ağ) — sıralı, zaman aşımı + yeniden deneme
# --------------------------------------------------------------------------

# Taşıma sözleşmesi: url -> bayt; 404 -> None; başka hata -> VisionError.
Transport = Callable[[str], Optional[bytes]]


class HttpTransport:
    """urllib ile sıralı indirme; 404 -> None; ağ/5xx hatasında üstel geri çekilmeyle
    yeniden dener. Ortam proxy'sini (HTTPS_PROXY) ve CA paketini (SSL_CERT_FILE)
    urllib varsayılanları olarak kullanır."""

    def __init__(self, timeout: float = 30.0, retries: int = 4, backoff: float = 1.0) -> None:
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff = backoff
        self.requests = 0
        self.bytes = 0

    def __call__(self, url: str) -> Optional[bytes]:
        last: Optional[BaseException] = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "tradingbot-vision-fill/1"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    data = resp.read()
                self.requests += 1
                self.bytes += len(data)
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    self.requests += 1
                    return None
                last = exc
                if exc.code < 500 and exc.code != 429:
                    break  # kalıcı 4xx: tekrar denemenin anlamı yok
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last = exc
            if attempt < self.retries - 1:
                time.sleep(self.backoff * (2 ** attempt))
        raise VisionError(f"İndirilemedi: {url} ({last!r})")


# --------------------------------------------------------------------------
# Zip / CSV / CHECKSUM ayrıştırma (saf, ağsız)
# --------------------------------------------------------------------------

def parse_checksum(text: str, expected_name: str) -> str:
    """`<sha256>  <dosya adı>` satırından sha256'yı döner; ad uyuşmalıdır."""
    for line in text.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        digest = parts[0].lower()
        if not _SHA256_RE.match(digest):
            raise VisionError(f"CHECKSUM biçimi geçersiz: {line!r}")
        if len(parts) >= 2:
            name = parts[1].lstrip("*")
            if os.path.basename(name) != expected_name:
                raise VisionError(
                    f"CHECKSUM dosya adı uyuşmuyor: {name!r} != {expected_name!r}"
                )
        return digest
    raise VisionError(f"CHECKSUM dosyası boş: {expected_name}")


def verify_zip_bytes(data: bytes, checksum_text: str, zip_name: str) -> None:
    """Zip baytlarının sha256'sı CHECKSUM ile uyuşmalı; değilse VisionError."""
    expected = parse_checksum(checksum_text, zip_name)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise VisionError(
            f"CHECKSUM UYUŞMUYOR {zip_name}: beklenen {expected}, hesaplanan {actual}"
        )


def read_csv_from_zip(data: bytes, zip_name: str) -> str:
    """Zip'in TEK csv üyesini bellekte okur (diske AÇMAZ). Ad beklenenle eşleşmeli."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
            if len(members) != 1:
                raise VisionError(f"{zip_name}: zip'te tek üye beklenirdi, {len(members)} var")
            info = members[0]
            expected_csv = zip_name[:-4] + ".csv"
            if os.path.basename(info.filename) != expected_csv:
                raise VisionError(
                    f"{zip_name}: üye adı beklenmiyor: {info.filename!r} != {expected_csv!r}"
                )
            if info.file_size > _MAX_CSV_BYTES:
                raise VisionError(f"{zip_name}: csv çok büyük ({info.file_size} bayt)")
            raw = zf.read(info)
    except zipfile.BadZipFile as exc:
        raise VisionError(f"{zip_name}: bozuk zip ({exc})") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VisionError(f"{zip_name}: csv UTF-8 değil") from exc


def parse_klines_csv(text: str, interval: str, source: str = "csv") -> List[Candle]:
    """Arşiv CSV'sini Candle listesine çevirir (başlıklı/başlıksız).

    Sütunlar fapi `/fapi/v1/klines` dizisiyle aynı sırada
    (open_time, open, high, low, close, volume, close_time, ...). Dönüşüm
    `Candle.from_binance` ile yapılır (harness'in canlı yoluyla aynı). Sıkı:
    open_time milisaniye ve ızgarada, close_time = open_time + dilim - 1, artan sıra.
    """
    step = kline_cache._INTERVAL_MS.get(interval)
    if step is None:
        raise VisionError(f"Desteklenmeyen zaman dilimi: {interval!r}")
    candles: List[Candle] = []
    first_row = True
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        cells = [c.strip() for c in line.split(",")]
        if first_row:
            first_row = False
            if not re.fullmatch(r"-?\d+", cells[0]):
                if cells[0].lower() != "open_time":
                    raise VisionError(f"{source}:{lineno}: tanınmayan başlık satırı {line[:60]!r}")
                continue
        if len(cells) < 7:
            raise VisionError(f"{source}:{lineno}: en az 7 sütun beklenirdi, {len(cells)} var")
        try:
            candle = Candle.from_binance(cells[:7])
        except (ValueError, TypeError) as exc:
            raise VisionError(f"{source}:{lineno}: satır çevrilemedi ({exc})") from exc
        if candle.open_time >= 10 ** 14:
            raise VisionError(
                f"{source}:{lineno}: open_time={candle.open_time} mikrosaniye görünüyor "
                "(milisaniye beklenir) — tahmin yürütülmez"
            )
        if candle.open_time % step != 0:
            raise VisionError(f"{source}:{lineno}: open_time {candle.open_time} {interval} ızgarasında değil")
        if candle.close_time != candle.open_time + step - 1:
            raise VisionError(
                f"{source}:{lineno}: close_time {candle.close_time} != open_time+{step}-1"
            )
        if candles and candle.open_time <= candles[-1].open_time:
            raise VisionError(f"{source}:{lineno}: mumlar artan sırada değil / tekrar var")
        candles.append(candle)
    if not candles:
        raise VisionError(f"{source}: csv'de mum yok")
    return candles


# --------------------------------------------------------------------------
# Doğrulamalı indirme (+ indirme önbelleği)
# --------------------------------------------------------------------------

def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_bytes(data)
    tmp.replace(path)


class VisionFetcher:
    """Arşivden doğrulanmış zip getirir. Sıra: indirme önbelleği (sha256 yeniden
    doğrulanır) -> ağ. 404 -> None."""

    def __init__(
        self, transport: Transport, download_cache: Path,
        base_url: str = DEFAULT_BASE_URL,
        log: Callable[[str], None] = lambda _m: None,
    ) -> None:
        self.transport = transport
        self.download_cache = Path(download_cache)
        self.base_url = base_url.rstrip("/")
        self.log = log
        self.downloaded = 0
        self.from_cache = 0
        self.missing = 0

    def url_for(self, kind: str, symbol: str, interval: str, period: str) -> str:
        name = f"{symbol}-{interval}-{period}.zip"
        return f"{self.base_url}/data/futures/um/{kind}/klines/{symbol}/{interval}/{name}"

    def get_zip(self, kind: str, symbol: str, interval: str, period: str) -> Optional[bytes]:
        """`kind` = 'daily' | 'monthly'; `period` = 'YYYY-MM-DD' | 'YYYY-MM'."""
        name = f"{symbol}-{interval}-{period}.zip"
        base = self.download_cache / kind / symbol / interval
        zip_path = base / name
        sum_path = base / (name + ".CHECKSUM")

        if zip_path.exists() and sum_path.exists():
            data = zip_path.read_bytes()
            try:
                verify_zip_bytes(data, sum_path.read_text(encoding="utf-8"), name)
                self.from_cache += 1
                return data
            except VisionError:
                self.log(f"  ! indirme önbelleği bozuk, yeniden indirilecek: {name}")
                zip_path.unlink(missing_ok=True)
                sum_path.unlink(missing_ok=True)

        url = self.url_for(kind, symbol, interval, period)
        data = self.transport(url)
        if data is None:
            self.missing += 1
            return None
        sum_bytes = self.transport(url + ".CHECKSUM")
        if sum_bytes is None:
            raise VisionError(f"CHECKSUM dosyası yok (404): {url}.CHECKSUM — doğrulanamayan veri kullanılmaz")
        sum_text = sum_bytes.decode("utf-8", errors="strict")
        verify_zip_bytes(data, sum_text, name)
        _atomic_write_bytes(zip_path, data)
        _atomic_write_bytes(sum_path, sum_bytes)
        self.downloaded += 1
        self.log(f"  + indirildi {name} ({len(data)} bayt)")
        return data


# --------------------------------------------------------------------------
# Arşiv-destekli FetchFn
# --------------------------------------------------------------------------

def _day_index(ms: int) -> int:
    return ms // _DAY_MS


def _date_of_index(idx: int) -> date:
    return date(1970, 1, 1) + timedelta(days=idx)


def _month_bounds(year: int, month: int) -> Tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _ms_of_date(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class ArchiveKlineSource:
    """`backtest.FetchFn` uyumlu: (symbol, interval, limit, end_time) -> Candle listesi.

    Binance `endTime` davranışının harness'e görünen kısmını taklit eder: `end_time`
    dahil olmak üzere KAPANMIŞ (close_time <= end_time) en yeni `limit` mum, eski->yeni.
    (Harness zaten `close_time <= cursor_end` süzgecini uygular; kapanmamış sınır
    mumunu arşivden hiç istememek eşdeğerdir ve pencere sonu arşiv sınırındayken
    gereksiz 404 üretmez.) Gerekli günler/aylar tembel indirilir ve bellekte tutulur;
    eksik gün = ArchiveGapError (sessiz boşluk YOK).
    """

    def __init__(
        self, fetcher: VisionFetcher, today: Optional[date] = None,
        use_monthly: bool = True,
    ) -> None:
        self.fetcher = fetcher
        self.today = today or datetime.now(timezone.utc).date()
        self.use_monthly = use_monthly
        self._store: Dict[Tuple[str, str], Dict[int, Candle]] = {}
        self._loaded_days: Dict[Tuple[str, str], set] = {}
        self.calls: List[Tuple[str, str, int, Optional[int]]] = []

    async def __call__(
        self, symbol: str, interval: str, limit: int, end_time: Optional[int] = None,
    ) -> List[Candle]:
        if end_time is None:
            raise VisionError("ArchiveKlineSource end_time olmadan çağrılamaz (canlı veri yok)")
        step = kline_cache._INTERVAL_MS.get(interval)
        if step is None:
            raise VisionError(f"Desteklenmeyen zaman dilimi: {interval!r}")
        self.calls.append((symbol, interval, limit, end_time))
        # end_time'a kadar KAPANAN son mumun açılışı = ızgara sınırı - step.
        boundary = ((end_time + 1) // step) * step
        last_open = boundary - step
        first_open = boundary - limit * step
        await self._ensure(symbol, interval, _day_index(first_open), _day_index(last_open))
        store = self._store.get((symbol, interval), {})
        eligible = sorted(
            (c for c in store.values() if c.close_time <= end_time),
            key=lambda c: c.open_time,
        )
        return eligible[-limit:]

    # -- yükleme ---------------------------------------------------------

    async def _ensure(self, symbol: str, interval: str, first_idx: int, last_idx: int) -> None:
        key = (symbol, interval)
        loaded = self._loaded_days.setdefault(key, set())
        need = [i for i in range(first_idx, last_idx + 1) if i not in loaded]
        if not need:
            return
        by_month: Dict[Tuple[int, int], List[int]] = {}
        for i in need:
            d = _date_of_index(i)
            by_month.setdefault((d.year, d.month), []).append(i)
        for (year, month), idxs in sorted(by_month.items()):
            m_first, m_last = _month_bounds(year, month)
            month_idxs = set(range(_day_index(_ms_of_date(m_first)), _day_index(_ms_of_date(m_last)) + 1))
            month_done = False
            if (
                self.use_monthly
                and month_idxs.issubset(set(idxs))
                and m_last < self.today.replace(day=1)
            ):
                month_done = self._load_monthly(symbol, interval, year, month)
            if month_done:
                loaded.update(month_idxs)
                continue
            for i in idxs:
                self._load_daily(symbol, interval, _date_of_index(i))
                loaded.add(i)

    def _merge(self, symbol: str, interval: str, candles: List[Candle], source: str) -> None:
        store = self._store.setdefault((symbol, interval), {})
        for c in candles:
            prev = store.get(c.open_time)
            if prev is not None and prev != c:
                raise VisionError(f"{source}: open_time={c.open_time} için çelişen mum")
            store[c.open_time] = c

    def _load_daily(self, symbol: str, interval: str, day: date) -> None:
        period = day.isoformat()
        name = f"{symbol}-{interval}-{period}.zip"
        data = self.fetcher.get_zip("daily", symbol, interval, period)
        if data is None:
            raise ArchiveGapError(
                f"{name} arşivde YOK (404). Arşiv gecikmesi ~1-2 gün olabilir ya da sembol "
                f"{period} tarihinde işlem görmüyordu; pencereyi daraltın."
            )
        candles = parse_klines_csv(read_csv_from_zip(data, name), interval, name)
        lo, hi = _ms_of_date(day), _ms_of_date(day) + _DAY_MS
        if any(not (lo <= c.open_time < hi) for c in candles):
            raise VisionError(f"{name}: {period} gününün dışında mum var")
        self._merge(symbol, interval, candles, name)

    def _load_monthly(self, symbol: str, interval: str, year: int, month: int) -> bool:
        period = f"{year:04d}-{month:02d}"
        name = f"{symbol}-{interval}-{period}.zip"
        data = self.fetcher.get_zip("monthly", symbol, interval, period)
        if data is None:
            return False  # günlük zip'lere düş
        candles = parse_klines_csv(read_csv_from_zip(data, name), interval, name)
        m_first, m_last = _month_bounds(year, month)
        lo, hi = _ms_of_date(m_first), _ms_of_date(m_last) + _DAY_MS
        if any(not (lo <= c.open_time < hi) for c in candles):
            raise VisionError(f"{name}: {period} ayının dışında mum var")
        self._merge(symbol, interval, candles, name)
        return True


# --------------------------------------------------------------------------
# Önbellek dosyası doğrulama + atomik terfi
# --------------------------------------------------------------------------

@dataclass
class StagedFile:
    path: Path
    symbol: str
    interval: str
    start_ms: int
    end_ms: int
    candles: List[Candle]


def verify_cache_file(path: Path) -> StagedFile:
    """Harness'in YAZDIĞI dosyayı harness'in OKUYUŞUYLA katı doğrular.

    Beklenen mum sayısı (`needed`) dosya adındaki anahtardan türer
    (start = end - needed*step). Kontroller: okunabilirlik, len == needed,
    open_time[i] == start + i*step (boşluk/tekrar YOK), close_time = open+step-1,
    son mum end-1'de kapanır.
    """
    m = _CACHE_NAME_RE.match(path.name)
    if not m:
        raise VisionError(f"Önbellek dosya adı tanınmıyor: {path.name}")
    symbol, interval = m["symbol"], m["interval"]
    start_ms, end_ms = int(m["start"]), int(m["end"])
    step = kline_cache._INTERVAL_MS.get(interval)
    if step is None:
        raise VisionError(f"{path.name}: desteklenmeyen zaman dilimi {interval!r}")
    span = end_ms - start_ms
    if span <= 0 or span % step != 0:
        raise VisionError(f"{path.name}: anahtar aralığı {step} ms'nin katı değil")
    needed = span // step
    candles = kline_cache.load(path.parent, symbol, interval, start_ms, end_ms)
    if candles is None:
        raise VisionError(f"{path.name}: yazılan dosya okunamadı")
    if len(candles) != needed:
        raise VisionError(
            f"{path.name}: EKSİK VERİ — {len(candles)} mum var, {needed} gerekiyor "
            "(arşivde kapsam yok ya da boşluk); önbelleğe YAZILMADI"
        )
    for i, c in enumerate(candles):
        expected_open = start_ms + i * step
        if c.open_time != expected_open:
            raise VisionError(
                f"{path.name}: boşluk/kayma — {i}. mum open_time={c.open_time}, "
                f"beklenen {expected_open} ({_utc(expected_open)}); önbelleğe YAZILMADI"
            )
        if c.close_time != c.open_time + step - 1:
            raise VisionError(f"{path.name}: {i}. mumun close_time değeri tutarsız")
    if candles[-1].close_time != end_ms - 1:
        raise VisionError(f"{path.name}: son mum end-1'de kapanmıyor")
    return StagedFile(path, symbol, interval, start_ms, end_ms, candles)


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------
# Orkestrasyon
# --------------------------------------------------------------------------

def leader_params(cfg: Any, force: bool = False) -> Optional[Tuple[str, int]]:
    """Lider piyasa kapısı AÇIKSA (lider sembol, run_days); kapalıysa None.
    `force=True` kapı bayrağını yok sayıp sembol/run_days'i cfg'den türetir.

    `backtest.run_backtest`'in aynı bloğunun birebir aynası (aynı varsayılanlar);
    tests/test_fill_klines_cache_from_vision.py bunu run_backtest'in gerçek istek
    kümesiyle karşılaştırır.
    """
    if not force and not bool(getattr(cfg, "scalper_market_gate", False)):
        return None
    leader_symbol = str(
        getattr(cfg, "scalper_market_gate_symbol", "") or "BTCUSDT"
    ).strip().upper() or "BTCUSDT"
    try:
        run_days = int(getattr(cfg, "scalper_market_gate_run_days", 3) or 3)
    except (TypeError, ValueError):
        run_days = 3
    return leader_symbol, run_days


@dataclass
class FileReport:
    path: Path
    symbol: str
    interval: str
    candles: int
    first_open_ms: int
    last_close_ms: int
    status: str  # "yazıldı" | "değişmedi" | "yenilendi"


@dataclass
class FillReport:
    files: List[FileReport] = field(default_factory=list)
    downloaded: int = 0
    from_cache: int = 0


async def fill_cache(
    *,
    symbols: Sequence[str],
    start: str,
    end: str,
    cfg: Any,
    cache_dir: Path,
    fetcher: VisionFetcher,
    timeframes: Optional[Tuple[str, str, str]] = None,
    market_gate: Optional[bool] = None,
    overwrite: bool = False,
    today: Optional[date] = None,
    use_monthly: bool = True,
    log: Callable[[str], None] = lambda _m: None,
    source_out: Optional[List[ArchiveKlineSource]] = None,
) -> FillReport:
    """Pencere için harness'in okuyacağı TÜM önbellek dosyalarını doldurur.

    Hata -> VisionError; hedef `cache_dir`e hiçbir dosya yazılmaz.
    """
    effective_days, start_ms, end_ms = backtest.resolve_backtest_window(0, start, end)
    if start_ms is None or end_ms is None:  # resolve_backtest_window start/end verildiğinde dolu döner
        raise VisionError("--start ve --end zorunlu")
    tfs = timeframes or backtest.resolve_timeframes(cfg)
    leader = leader_params(cfg, force=bool(market_gate)) if market_gate is not False else None

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    source = ArchiveKlineSource(fetcher, today=today, use_monthly=use_monthly)
    if source_out is not None:
        source_out.append(source)
    staging = Path(tempfile.mkdtemp(prefix=".vision-staging-", dir=cache_dir))
    report = FillReport()
    try:
        if leader is not None:
            leader_symbol, run_days = leader
            log(f"Lider piyasa kapısı AÇIK: {leader_symbol} (run_days={run_days})")
            await backtest.gather_leader_series(
                source, leader_symbol, effective_days, end_ms, tfs[0], run_days,
                cache_dir=staging, refresh=False,
            )
        for symbol in symbols:
            log(f"{symbol}: {tfs} serileri hazırlanıyor ...")
            await backtest.gather_symbol_data(
                source, symbol, effective_days, end_time=end_ms, timeframes=tfs,
                cache_dir=staging, refresh=False,
            )
        staged_paths = sorted(p for p in staging.iterdir() if p.name.endswith(".json.gz"))
        if not staged_paths:
            raise VisionError("Hiç seri üretilmedi")
        staged = [verify_cache_file(p) for p in staged_paths]  # TÜMÜ geçmeden hiçbiri taşınmaz

        # Mevcut dosya çakışması (üzerine yazma politikası) — taşımadan ÖNCE karar ver.
        plan: List[Tuple[StagedFile, Path, str]] = []
        for sf in staged:
            final = kline_cache.cache_file(cache_dir, sf.symbol, sf.interval, sf.start_ms, sf.end_ms)
            status = "yazıldı"
            if final.exists():
                existing = kline_cache.load(cache_dir, sf.symbol, sf.interval, sf.start_ms, sf.end_ms)
                if existing is None:
                    status = "yenilendi"
                elif existing == sf.candles:
                    status = "değişmedi"
                elif not overwrite:
                    raise VisionError(
                        f"{final.name}: mevcut önbellek arşiv verisinden FARKLI; "
                        "üzerine yazmak için --overwrite verin"
                    )
                else:
                    status = "yenilendi"
            plan.append((sf, final, status))

        for sf, final, status in plan:
            if status != "değişmedi":
                os.replace(sf.path, final)
            report.files.append(FileReport(
                path=final, symbol=sf.symbol, interval=sf.interval, candles=len(sf.candles),
                first_open_ms=sf.candles[0].open_time, last_close_ms=sf.candles[-1].close_time,
                status=status,
            ))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    report.downloaded = fetcher.downloaded
    report.from_cache = fetcher.from_cache
    return report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "data.binance.vision arşivinden backtest kline önbelleğini doldurur "
            "(fapi'ye erişilemeyen ortamlar için). backtest ile AYNI SCALPER_* "
            "ortamıyla çalıştırın."
        )
    )
    p.add_argument("--symbols", default=DEFAULT_SYMBOLS, help="Virgülle ayrılmış semboller (varsayılan: CLAUDE.md'deki 8)")
    p.add_argument("--start", required=True, help="Pencere başı UTC YYYY-MM-DD (backtest --start ile aynı anlam)")
    p.add_argument("--end", required=True, help="Pencere sonu UTC YYYY-MM-DD, DAHİL DEĞİL — [start, end)")
    p.add_argument("--strategies", default="C", help="Strateji adları (varsayılan C). Veri ihtiyacı stratejiden değil cfg zaman dilimlerinden gelir; yalnız doğrulanır")
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help=f"Hedef önbellek (varsayılan {DEFAULT_CACHE_DIR}, backtest ile aynı)")
    p.add_argument("--download-cache", default=DEFAULT_DOWNLOAD_CACHE, help=f"İndirilen zip'lerin önbelleği (varsayılan {DEFAULT_DOWNLOAD_CACHE})")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--tf-entry", default=None, help="Giriş TF'yi geçersiz kıl (varsayılan: cfg scalper_tf_entry)")
    p.add_argument("--tf-context", default=None, help="Bağlam TF'yi geçersiz kıl (varsayılan: cfg scalper_tf_context)")
    p.add_argument("--tf-regime", default=None, help="Rejim TF'yi geçersiz kıl (varsayılan: cfg scalper_tf_regime)")
    p.add_argument("--market-gate", dest="market_gate", action="store_true", default=None, help="Lider kapısı serilerini de yaz (varsayılan: cfg scalper_market_gate)")
    p.add_argument("--no-market-gate", dest="market_gate", action="store_false", help="Lider kapısı serilerini yazma")
    p.add_argument("--overwrite", action="store_true", help="Mevcut ve FARKLI önbellek dosyasının üzerine yaz")
    p.add_argument("--no-monthly", action="store_true", help="Aylık zip kullanma (yalnız günlük)")
    p.add_argument("--timeout", type=float, default=30.0, help="İstek zaman aşımı sn (varsayılan 30)")
    p.add_argument("--retries", type=int, default=4, help="Ağ hatasında deneme sayısı (varsayılan 4)")
    return p


def main(argv: Optional[Sequence[str]] = None, transport: Optional[Transport] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        log("HATA: sembol listesi boş")
        return 2
    if not get_enabled(args.strategies):
        log(f"HATA: geçerli strateji yok: {args.strategies!r}")
        return 2

    base_tfs = backtest.resolve_timeframes(settings)
    tfs = (
        args.tf_entry or base_tfs[0],
        args.tf_context or base_tfs[1],
        args.tf_regime or base_tfs[2],
    )
    gate = args.market_gate if args.market_gate is not None else bool(
        getattr(settings, "scalper_market_gate", False)
    )
    log(
        f"Pencere [{args.start}, {args.end}) UTC | semboller={symbols} | TF(entry,ctx,regime)={tfs} "
        f"| lider kapısı={'AÇIK' if gate else 'kapalı'} | strateji={args.strategies}"
    )
    log("NOT: harness günlüğündeki \"Binance'ten çekiliyor\" satırları burada ARŞİV kaynağını ifade eder.")

    http = transport or HttpTransport(timeout=args.timeout, retries=args.retries)
    fetcher = VisionFetcher(http, Path(args.download_cache), base_url=args.base_url, log=log)

    cfg_for_leader: Any = settings
    try:
        report = asyncio.run(fill_cache(
            symbols=symbols, start=args.start, end=args.end, cfg=cfg_for_leader,
            cache_dir=Path(args.cache_dir), fetcher=fetcher, timeframes=tfs,
            market_gate=gate, overwrite=args.overwrite, use_monthly=not args.no_monthly,
            log=log,
        ))
    except (VisionError, ValueError) as exc:
        log(f"HATA: {exc}")
        log("Önbelleğe HİÇBİR dosya yazılmadı.")
        return 2

    for f in report.files:
        print(
            f"{f.status:9s} {f.path}  {f.symbol} {f.interval}  {f.candles} mum  "
            f"{_utc(f.first_open_ms)} -> {_utc(f.last_close_ms + 1)} UTC"
        )
    written = sum(1 for f in report.files if f.status != "değişmedi")
    print(
        f"ÖZET: {len(report.files)} dosya ({written} yazıldı/yenilendi, "
        f"{len(report.files) - written} değişmedi), {sum(f.candles for f in report.files)} mum | "
        f"zip: {report.downloaded} indirildi, {report.from_cache} indirme önbelleğinden"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
