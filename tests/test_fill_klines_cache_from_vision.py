"""
scripts/fill_klines_cache_from_vision.py testleri.

AĞSIZ birimler: `tests/fixtures/vision/` altındaki GERÇEK arşiv satırlarından
(2026-08-07 BTCUSDT başlıklı / ETHUSDT başlıksız, 6'şar 5m mum + gerçek biçimli
.CHECKSUM) ayrıştırma; sha256 reddi; eksik mumda ret ve DOSYA YAZILMAMASI; önbellek
anahtarının harness fonksiyonlarıyla aynı olduğu (tests/fixtures/klines'teki,
fapi'den üretilmiş gerçek dosya adlarıyla birebir).

Yerel arşiv: `tests/fixtures/klines` içindeki gerçek mumlar, arşivin zip/CSV/CHECKSUM
biçimine geri kodlanıp bir `Transport` taklidiyle (yalnız AĞ katmanı) sunulur; betiğin
geri kalanı (ayrıştırma, doğrulama, harness'in gather_* fonksiyonları, atomik terfi)
gerçek koddur.

AĞ gerektiren kabul testi yalnız VISION_NETWORK_TESTS=1 iken koşar.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fill_klines_cache_from_vision as fv  # noqa: E402

import src.strategies.scalper.backtest as backtest_module  # noqa: E402
from src.strategies.scalper import kline_cache  # noqa: E402
from src.strategies.scalper.types import Candle  # noqa: E402

_TESTS = Path(__file__).resolve().parent
FIXTURE_KLINES = _TESTS / "fixtures" / "klines"
FIXTURE_VISION = _TESTS / "fixtures" / "vision"

_START, _END = "2026-08-07", "2026-08-10"
_END_MS = 1786320000000  # 2026-08-10 00:00 UTC
_GOLDEN_TFS = ("5m", "15m", "15m")  # golden koşu: scalper_tf_regime="15m"
_BASE = "https://data.binance.vision"


# --------------------------------------------------------------------------
# Yardımcılar: fixture mumlarından yerel arşiv
# --------------------------------------------------------------------------

def _fixture_series(symbol: str, interval: str) -> List[Candle]:
    """tests/fixtures/klines'teki (fapi'den üretilmiş) EN UZUN seriyi döner."""
    best: List[Candle] = []
    for p in sorted(FIXTURE_KLINES.glob(f"{symbol}_{interval}_*.json.gz")):
        m = fv._CACHE_NAME_RE.match(p.name)
        c = kline_cache.load(FIXTURE_KLINES, symbol, interval, int(m["start"]), int(m["end"]))
        assert c is not None
        if len(c) > len(best):
            best = c
    assert best
    return best


def _csv_row(c: Candle) -> str:
    return (
        f"{c.open_time},{c.open!r},{c.high!r},{c.low!r},{c.close!r},{c.volume!r},"
        f"{c.close_time},0,0,0,0,0"
    )


def _make_zip(name: str, rows: List[str], header: bool) -> bytes:
    head = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore"] if header else []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name[:-4] + ".csv", "\n".join(head + rows) + "\n")
    return buf.getvalue()


class LocalArchive:
    """data.binance.vision'ın URL düzenini taklit eden `Transport` (yalnız ağ katmanı)."""

    def __init__(self) -> None:
        self.files: Dict[str, bytes] = {}
        self.requests: List[str] = []

    def add_zip(self, kind: str, symbol: str, interval: str, period: str, data: bytes,
                checksum: Optional[str] = None) -> None:
        name = f"{symbol}-{interval}-{period}.zip"
        url = f"{_BASE}/data/futures/um/{kind}/klines/{symbol}/{interval}/{name}"
        self.files[url] = data
        digest = checksum or hashlib.sha256(data).hexdigest()
        self.files[url + ".CHECKSUM"] = f"{digest}  {name}\n".encode()

    def add_daily_from_candles(self, symbol: str, interval: str, candles: List[Candle],
                               header: bool = True, drop: Optional[set] = None) -> None:
        by_day: Dict[str, List[str]] = {}
        for c in candles:
            if drop and c.open_time in drop:
                continue
            day = datetime.fromtimestamp(c.open_time // 1000, tz=timezone.utc).date().isoformat()
            by_day.setdefault(day, []).append(_csv_row(c))
        for day, rows in by_day.items():
            name = f"{symbol}-{interval}-{day}.zip"
            self.add_zip("daily", symbol, interval, day, _make_zip(name, rows, header))

    def __call__(self, url: str) -> Optional[bytes]:
        self.requests.append(url)
        return self.files.get(url)


def _golden_archive(drop_5m: Optional[set] = None) -> LocalArchive:
    arch = LocalArchive()
    for i, sym in enumerate(("BTCUSDT", "ETHUSDT")):
        arch.add_daily_from_candles(sym, "5m", _fixture_series(sym, "5m"),
                                    header=bool(i % 2), drop=drop_5m if sym == "BTCUSDT" else None)
        arch.add_daily_from_candles(sym, "15m", _fixture_series(sym, "15m"), header=bool(i % 2))
    return arch


def _fetcher(arch: LocalArchive, tmp_path: Path) -> fv.VisionFetcher:
    return fv.VisionFetcher(arch, tmp_path / "dl", base_url=_BASE)


def _fill(tmp_path: Path, arch: LocalArchive, **kw):
    cache = tmp_path / "cache"
    args = dict(
        symbols=["BTCUSDT", "ETHUSDT"], start=_START, end=_END,
        cfg=SimpleNamespace(), cache_dir=cache, fetcher=_fetcher(arch, tmp_path),
        timeframes=_GOLDEN_TFS, today=date(2026, 10, 10),
    )
    args.update(kw)
    return asyncio.run(fv.fill_cache(**args)), cache


# --------------------------------------------------------------------------
# Ayrıştırma (gerçek arşiv fixture'ları)
# --------------------------------------------------------------------------

def _fixture_candles(name: str) -> List[Candle]:
    zip_bytes = (FIXTURE_VISION / f"{name}.zip").read_bytes()
    fv.verify_zip_bytes(zip_bytes, (FIXTURE_VISION / f"{name}.zip.CHECKSUM").read_text(), f"{name}.zip")
    return fv.parse_klines_csv(fv.read_csv_from_zip(zip_bytes, f"{name}.zip"), "5m", name)


class TestParse:
    def test_header_and_headerless_real_archive_rows(self):
        btc = _fixture_candles("BTCUSDT-5m-2026-08-07")          # başlıklı
        eth = _fixture_candles("ETHUSDT-5m-2026-08-07")          # başlıksız
        assert len(btc) == len(eth) == 6
        assert btc[0].open_time == 1786060800000 and btc[0].close_time == 1786061099999
        assert (btc[0].open, btc[0].high, btc[0].low, btc[0].close, btc[0].volume) == (
            64299.9, 64332.2, 64279.2, 64327.6, 290.677)
        assert (eth[0].open, eth[0].close, eth[0].volume) == (1903.11, 1903.52, 8517.913)
        assert [type(x) for x in (btc[0].open_time, btc[0].open, btc[0].close_time)] == [int, float, int]

    def test_rows_equal_fapi_derived_fixture_klines_field_by_field(self):
        """Arşiv satırları = fapi'den üretilmiş tests/fixtures/klines mumları (alan alan)."""
        for sym, name in (("BTCUSDT", "BTCUSDT-5m-2026-08-07"), ("ETHUSDT", "ETHUSDT-5m-2026-08-07")):
            ref = {c.open_time: c for c in _fixture_series(sym, "5m")}
            for c in _fixture_candles(name):
                assert ref[c.open_time] == c

    def test_header_optional_gives_same_candles(self):
        rows = [_csv_row(c) for c in _fixture_candles("BTCUSDT-5m-2026-08-07")]
        with_h = fv.parse_klines_csv(
            fv.read_csv_from_zip(_make_zip("X-5m-2026-08-07.zip", rows, True), "X-5m-2026-08-07.zip"), "5m")
        no_h = fv.parse_klines_csv(
            fv.read_csv_from_zip(_make_zip("X-5m-2026-08-07.zip", rows, False), "X-5m-2026-08-07.zip"), "5m")
        assert with_h == no_h and len(with_h) == 6

    @pytest.mark.parametrize("bad, why", [
        ("1786060800000000,1,1,1,1,1,1786061099999999", "mikrosaniye"),
        ("1786060800000,1,1,1,1,1,1786061099998", "close_time"),
        ("1786060800001,1,1,1,1,1,1786061100000", "ızgara"),
        ("abc,1,1,1,1,1,1786061099999", "başlık"),
        ("1786060800000,1,1,1", "sütun"),
    ])
    def test_malformed_rows_rejected(self, bad, why):
        with pytest.raises(fv.VisionError):
            fv.parse_klines_csv(bad + "\n", "5m")

    def test_unordered_or_duplicate_rejected(self):
        a = "1786060800000,1,1,1,1,1,1786061099999"
        b = "1786061100000,1,1,1,1,1,1786061399999"
        with pytest.raises(fv.VisionError):
            fv.parse_klines_csv(f"{b}\n{a}\n", "5m")
        with pytest.raises(fv.VisionError):
            fv.parse_klines_csv(f"{a}\n{a}\n", "5m")

    def test_zip_member_name_must_match(self):
        data = _make_zip("OTHER-5m-2026-08-07.zip", ["1,1,1,1,1,1,1"], False)
        with pytest.raises(fv.VisionError):
            fv.read_csv_from_zip(data, "BTCUSDT-5m-2026-08-07.zip")

    def test_corrupt_zip_rejected(self):
        with pytest.raises(fv.VisionError):
            fv.read_csv_from_zip(b"not a zip", "X-5m-2026-08-07.zip")


# --------------------------------------------------------------------------
# CHECKSUM
# --------------------------------------------------------------------------

class TestChecksum:
    def test_real_fixture_checksum_files_verify(self):
        for name in ("BTCUSDT-5m-2026-08-07", "ETHUSDT-5m-2026-08-07"):
            fv.verify_zip_bytes(
                (FIXTURE_VISION / f"{name}.zip").read_bytes(),
                (FIXTURE_VISION / f"{name}.zip.CHECKSUM").read_text(), f"{name}.zip")

    def test_mismatch_rejected(self):
        name = "BTCUSDT-5m-2026-08-07.zip"
        data = (FIXTURE_VISION / name).read_bytes()
        text = (FIXTURE_VISION / (name + ".CHECKSUM")).read_text()
        with pytest.raises(fv.VisionError, match="UYUŞMUYOR"):
            fv.verify_zip_bytes(data + b"x", text, name)

    def test_checksum_name_and_format_checked(self):
        sha = "a" * 64
        with pytest.raises(fv.VisionError):
            fv.parse_checksum(f"{sha}  other.zip\n", "x.zip")
        with pytest.raises(fv.VisionError):
            fv.parse_checksum("zzz  x.zip\n", "x.zip")
        with pytest.raises(fv.VisionError):
            fv.parse_checksum("\n", "x.zip")
        assert fv.parse_checksum(f"{sha}  x.zip\n", "x.zip") == sha

    def test_tampered_download_never_reaches_download_cache(self, tmp_path):
        arch = LocalArchive()
        data = (FIXTURE_VISION / "BTCUSDT-5m-2026-08-07.zip").read_bytes()
        arch.add_zip("daily", "BTCUSDT", "5m", "2026-08-07", data, checksum="0" * 64)
        f = _fetcher(arch, tmp_path)
        with pytest.raises(fv.VisionError, match="UYUŞMUYOR"):
            f.get_zip("daily", "BTCUSDT", "5m", "2026-08-07")
        assert not list((tmp_path / "dl").rglob("*.zip"))

    def test_missing_checksum_file_rejected(self, tmp_path):
        arch = LocalArchive()
        data = (FIXTURE_VISION / "BTCUSDT-5m-2026-08-07.zip").read_bytes()
        arch.add_zip("daily", "BTCUSDT", "5m", "2026-08-07", data)
        del arch.files[next(u for u in arch.files if u.endswith(".CHECKSUM"))]
        with pytest.raises(fv.VisionError, match="CHECKSUM"):
            _fetcher(arch, tmp_path).get_zip("daily", "BTCUSDT", "5m", "2026-08-07")

    def test_download_cache_reused_and_reverified(self, tmp_path):
        arch = LocalArchive()
        data = (FIXTURE_VISION / "BTCUSDT-5m-2026-08-07.zip").read_bytes()
        arch.add_zip("daily", "BTCUSDT", "5m", "2026-08-07", data)
        f = _fetcher(arch, tmp_path)
        assert f.get_zip("daily", "BTCUSDT", "5m", "2026-08-07") == data
        n = len(arch.requests)
        assert f.get_zip("daily", "BTCUSDT", "5m", "2026-08-07") == data
        assert len(arch.requests) == n and f.from_cache == 1          # tekrar indirmedi
        # önbellekteki zip bozulursa: sha256 tutmaz -> yeniden indirilir
        cached = next((tmp_path / "dl").rglob("*.zip"))
        cached.write_bytes(b"corrupt")
        assert f.get_zip("daily", "BTCUSDT", "5m", "2026-08-07") == data
        assert len(arch.requests) > n


# --------------------------------------------------------------------------
# Doldurma: anahtar paritesi, içerik, eksik veri
# --------------------------------------------------------------------------

class TestFill:
    def test_keys_and_contents_equal_harness_fixture_files(self, tmp_path):
        report, cache = _fill(tmp_path, _golden_archive())
        expected = {p.name for p in FIXTURE_KLINES.glob("*.json.gz")}
        written = {p.name for p in cache.glob("*.json.gz")}
        assert written == expected                       # anahtar = harness'in (fapi'den) ürettiği
        for name in expected:
            m = fv._CACHE_NAME_RE.match(name)
            args = (m["symbol"], m["interval"], int(m["start"]), int(m["end"]))
            assert kline_cache.load(cache, *args) == kline_cache.load(FIXTURE_KLINES, *args)
        assert {f.status for f in report.files} == {"yazıldı"}
        assert sum(f.candles for f in report.files) == 2 * (1014 + 388 + 538)

    def test_no_staging_or_tmp_leftovers(self, tmp_path):
        _, cache = _fill(tmp_path, _golden_archive())
        assert sorted(p.name for p in cache.iterdir() if not p.name.endswith(".json.gz")) == []

    def test_key_uses_harness_functions(self, tmp_path):
        """Dosya adı = kline_cache.window_start_ms(needed) — needed = gün*günlük+ısınma."""
        _, cache = _fill(tmp_path, _golden_archive())
        days = 3
        for interval, warm in (("5m", backtest_module._WARMUP_BY_ROLE[0]),
                               ("15m", backtest_module._WARMUP_BY_ROLE[1]),
                               ("15m", backtest_module._WARMUP_BY_ROLE[2])):
            needed = days * backtest_module._CANDLES_PER_DAY[interval] + warm
            start = kline_cache.window_start_ms(interval, needed, _END_MS)
            assert kline_cache.cache_file(cache, "BTCUSDT", interval, start, _END_MS).exists()

    def test_missing_candle_rejected_and_nothing_written(self, tmp_path):
        series = _fixture_series("BTCUSDT", "5m")
        hole = series[500].open_time
        arch = _golden_archive(drop_5m={hole})
        with pytest.raises(fv.VisionError, match="boşluk|EKSİK"):
            _fill(tmp_path, arch)
        cache = tmp_path / "cache"
        assert list(cache.rglob("*.json.gz")) == []      # ETH dahil HİÇBİR dosya yazılmadı
        assert [p.name for p in cache.iterdir()] == []   # hazırlık dizini de temizlendi

    def test_verify_rejects_hole_even_when_count_matches(self, tmp_path):
        """Mum SAYISI doğru ama İÇİNDE boşluk varsa (eksik mum + yerine tekrar).

        İlk ve son mum KORUNUR (başlangıç/bitiş sınır kontrolleri yakalayamaz);
        500. mum silinip yerine 501. mumun kopyası konur: uzunluk aynı, ilk
        mum start'ta, son mum end-1'de kapanır — reddin TEK nedeni iç boşluktur.
        """
        m = fv._CACHE_NAME_RE.match("BTCUSDT_5m_1786015800000_1786320000000.json.gz")
        args = ("BTCUSDT", "5m", int(m["start"]), int(m["end"]))
        series = _fixture_series("BTCUSDT", "5m")
        holed = series[:500] + [series[501]] + series[501:]
        assert len(holed) == len(series)
        assert holed[0] == series[0] and holed[-1] == series[-1]   # sınırlar sağlam
        assert holed[500].open_time == series[501].open_time        # iç boşluk @500
        kline_cache.save(tmp_path, *args, holed)
        with pytest.raises(fv.VisionError, match=r"boşluk/kayma — 500\. mum") as exc:
            fv.verify_cache_file(kline_cache.cache_file(tmp_path, *args))
        assert str(series[500].open_time) in str(exc.value)         # beklenen @500
        kline_cache.save(tmp_path, *args, series)          # sağlam seri geçer
        assert fv.verify_cache_file(kline_cache.cache_file(tmp_path, *args)).candles == series

    def test_missing_day_in_archive_is_hard_error(self, tmp_path):
        arch = _golden_archive()
        for url in [u for u in arch.files if "BTCUSDT-5m-2026-08-08.zip" in u]:
            del arch.files[url]
        with pytest.raises(fv.ArchiveGapError, match="2026-08-08"):
            _fill(tmp_path, arch)
        assert list((tmp_path / "cache").rglob("*.json.gz")) == []

    def test_insufficient_history_rejected(self, tmp_path):
        """Arşiv pencereden eski veri sunmuyorsa (kısa seri) dosya YAZILMAZ."""
        arch = LocalArchive()
        short = [c for c in _fixture_series("BTCUSDT", "5m") if c.open_time >= 1786060800000]
        arch.add_daily_from_candles("BTCUSDT", "5m", short)
        with pytest.raises(fv.VisionError):
            _fill(tmp_path, arch, symbols=["BTCUSDT"], timeframes=("5m", "5m", "5m"))
        assert list((tmp_path / "cache").rglob("*.json.gz")) == []

    def test_overwrite_policy(self, tmp_path):
        arch = _golden_archive()
        _, cache = _fill(tmp_path, arch)
        # özdeş mevcut dosya -> "değişmedi"
        report, _ = _fill(tmp_path, arch)
        assert {f.status for f in report.files} == {"değişmedi"}
        # farklı içerikli mevcut dosya -> --overwrite olmadan RED, dosyaya dokunulmaz
        target = next(iter(sorted(cache.glob("BTCUSDT_5m_*.json.gz"))))
        m = fv._CACHE_NAME_RE.match(target.name)
        args = (m["symbol"], m["interval"], int(m["start"]), int(m["end"]))
        good = kline_cache.load(cache, *args)
        altered = list(good)
        altered[0] = Candle(**{**good[0].__dict__, "close": good[0].close + 1.0})
        kline_cache.save(cache, *args, altered)
        with pytest.raises(fv.VisionError, match="--overwrite"):
            _fill(tmp_path, arch)
        assert kline_cache.load(cache, *args) == altered
        report, _ = _fill(tmp_path, arch, overwrite=True)
        assert kline_cache.load(cache, *args) == good
        assert "yenilendi" in {f.status for f in report.files}

    # -- terfi ortası OSError: eski sürüm hedefler geçersiz kılınır (--overwrite) --

    @staticmethod
    def _alter(cache: Path, path: Path) -> tuple:
        """Önbellekteki dosyayı ESKİ (farklı içerikli) sürümle değiştirir; (iyi, eski, anahtar) döner."""
        m = fv._CACHE_NAME_RE.match(path.name)
        args = (m["symbol"], m["interval"], int(m["start"]), int(m["end"]))
        good = kline_cache.load(cache, *args)
        old = list(good)
        old[0] = Candle(**{**good[0].__dict__, "close": good[0].close + 1.0})
        kline_cache.save(cache, *args, old)
        return good, old, args

    @staticmethod
    def _fail_nth_promotion(monkeypatch, cache: Path, n: int):
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            if Path(dst).parent == cache:        # yalnız terfi (hazırlık yazımları değil)
                calls["n"] += 1
                if calls["n"] == n:
                    raise OSError(5, "Input/output error")
            return real_replace(src, dst)
        monkeypatch.setattr(fv.os, "replace", flaky)

    def test_overwrite_mid_promotion_error_invalidates_unpromoted_old_files(self, tmp_path, monkeypatch):
        arch = _golden_archive()
        kw = dict(symbols=["BTCUSDT"])
        _, cache = _fill(tmp_path, arch, **kw)
        files = sorted(cache.glob("*.json.gz"))
        assert len(files) == 3
        goods = {}
        for f in files:
            goods[f.name] = self._alter(cache, f)
        self._fail_nth_promotion(monkeypatch, cache, 2)

        with pytest.raises(fv.PromotionError) as exc:
            _fill(tmp_path, arch, overwrite=True, **kw)

        msg = str(exc.value)
        assert "Input/output error" in msg
        # 1. dosya yeni sürüme terfi etti; 2. (hatalı) ve 3. eski sürümleri SİLİNDİ.
        good1, _old1, args1 = goods[files[0].name]
        assert kline_cache.load(cache, *args1) == good1
        assert not files[1].exists() and not files[2].exists()
        assert files[1].name in msg and files[2].name in msg
        assert "geçersiz kılındı" in msg
        assert "SİLİNEMEDİ" not in msg
        assert [p.name for p in cache.iterdir()] == [files[0].name]   # hazırlık dizini de temiz

    def test_overwrite_mid_promotion_error_keeps_unchanged_files(self, tmp_path, monkeypatch):
        """"değişmedi" dosya arşivle aynı içeriktir: silinmez."""
        arch = _golden_archive()
        kw = dict(symbols=["BTCUSDT"])
        _, cache = _fill(tmp_path, arch, **kw)
        files = sorted(cache.glob("*.json.gz"))
        _g, _o, _a = self._alter(cache, files[0])
        _g, _o, _a = self._alter(cache, files[1])      # 3. dosya DEĞİŞMEDİ
        self._fail_nth_promotion(monkeypatch, cache, 2)

        with pytest.raises(fv.PromotionError) as exc:
            _fill(tmp_path, arch, overwrite=True, **kw)

        assert files[2].exists() and files[2].name not in str(exc.value)
        assert not files[1].exists()

    def test_overwrite_mid_promotion_error_reports_failed_deletion(self, tmp_path, monkeypatch):
        arch = _golden_archive()
        kw = dict(symbols=["BTCUSDT"])
        _, cache = _fill(tmp_path, arch, **kw)
        files = sorted(cache.glob("*.json.gz"))
        for f in files:
            self._alter(cache, f)
        self._fail_nth_promotion(monkeypatch, cache, 2)
        real_unlink = Path.unlink

        def selective(self, *a, **k):
            if self.name == files[2].name:
                raise PermissionError(13, "Permission denied")
            return real_unlink(self, *a, **k)
        monkeypatch.setattr(Path, "unlink", selective)

        with pytest.raises(fv.PromotionError) as exc:
            _fill(tmp_path, arch, overwrite=True, **kw)

        msg = str(exc.value)
        assert "SİLİNEMEDİ" in msg and files[2].name in msg and "Permission denied" in msg
        assert not files[1].exists() and files[2].exists()

    def test_no_overwrite_mid_promotion_error_does_not_delete_anything(self, tmp_path, monkeypatch):
        """--overwrite KAPALIYKEN davranış değişmedi: bozuk (okunamayan) mevcut
        dosyalar "yenilendi" planlanır ama terfi hatasında SİLİNMEZ."""
        arch = _golden_archive()
        kw = dict(symbols=["BTCUSDT"])
        _, cache = _fill(tmp_path, arch, **kw)
        files = sorted(cache.glob("*.json.gz"))
        for f in files:
            f.write_bytes(b"not a gzip")                # load() -> None => "yenilendi"
        self._fail_nth_promotion(monkeypatch, cache, 2)

        with pytest.raises(fv.PromotionError) as exc:
            _fill(tmp_path, arch, **kw)                 # overwrite=False

        assert "geçersiz kılındı" not in str(exc.value) and "SİLİNEMEDİ" not in str(exc.value)
        assert all(f.exists() for f in files[1:])
        assert files[0].read_bytes() != b"not a gzip"   # 1. dosya terfi etti
        assert files[1].read_bytes() == b"not a gzip"

    def test_cli_overwrite_mid_promotion_message_and_exit_code(self, tmp_path, capsys, monkeypatch):
        arch = _golden_archive()
        args = ["--symbols", "BTCUSDT", "--start", _START, "--end", _END,
                "--tf-entry", "5m", "--tf-context", "15m", "--tf-regime", "15m", "--no-market-gate",
                "--cache-dir", str(tmp_path / "c"), "--download-cache", str(tmp_path / "d")]
        assert fv.main(args, transport=arch) == 0
        cache = tmp_path / "c"
        files = sorted(cache.glob("*.json.gz"))
        for f in files:
            self._alter(cache, f)
        capsys.readouterr()
        self._fail_nth_promotion(monkeypatch, cache, 2)

        rc = fv.main([*args, "--overwrite"], transport=arch)

        err = capsys.readouterr().err
        assert rc == 2
        assert "geçersiz kılındı" in err and "KISMEN güncellenmiş olabilir" in err
        assert not files[1].exists() and not files[2].exists() and files[0].exists()

    def test_golden_backtest_runs_identically_from_filled_cache(self, tmp_path, monkeypatch):
        """Doldurulan önbellek, fixture'larla AYNI golden sonucu verir (ağsız harness)."""
        import test_golden_backtest as golden

        _fill(tmp_path, _golden_archive())
        trades_ref, missed_ref = asyncio.run(golden._run_golden(monkeypatch))
        monkeypatch.setattr(golden, "_FIXTURE_DIR", tmp_path / "cache")
        trades, missed = asyncio.run(golden._run_golden(monkeypatch))
        assert trades and len(trades) == len(trades_ref) == 2
        assert [(t.symbol, t.entry_time, t.exit_time, round(t.pnl, 8)) for t in trades] == \
               [(t.symbol, t.entry_time, t.exit_time, round(t.pnl, 8)) for t in trades_ref]
        assert missed == missed_ref


# --------------------------------------------------------------------------
# Lider kapısı + istek paritesi (run_backtest'in GERÇEK istek kümesiyle)
# --------------------------------------------------------------------------

class TestRequestParity:
    def _recorded_by_harness(self, monkeypatch, cfg, symbols):
        calls = set()

        class _Recorder:
            def __init__(self, base_url, guard_mode="live"):
                pass

            async def get_klines(self, symbol, interval, limit, end_time=None):
                calls.add((symbol, interval, limit, end_time))
                return []

            async def close(self):
                return None

        monkeypatch.setattr(backtest_module, "KlineFetcher", _Recorder)
        monkeypatch.setattr(backtest_module, "_REQUEST_DELAY_SECONDS", 0.0)
        try:
            asyncio.run(backtest_module.run_backtest(
                days=3, symbols=symbols, strategy_names="C", cfg=cfg,
                start_time_ms=backtest_module._parse_utc_date(_START),
                end_time_ms=_END_MS, cache_dir=None,
            ))
        except Exception:
            pass  # boş seriyle simülasyon düşebilir; istekler zaten kaydedildi
        return calls

    @pytest.mark.parametrize("gate", [False, True])
    def test_series_requested_match_run_backtest(self, tmp_path, monkeypatch, gate):
        cfg = SimpleNamespace(
            scalper_tf_entry="5m", scalper_tf_context="15m", scalper_tf_regime="4h",
            scalper_market_gate=gate, scalper_market_gate_symbol="btcusdt",
            scalper_market_gate_run_days=3,
        )
        recorded = self._recorded_by_harness(monkeypatch, cfg, ["ETHUSDT"])
        assert recorded
        # Bizim orkestrasyon: arşiv yükleme devre dışı (boş sayfa) -> fill_cache aynı
        # gather_* çağrılarını yapar, istek kümesini `source.calls` kaydeder, sonra
        # "EKSİK VERİ" ile durur (boş seri yazılmaz).
        async def _no_load(self, symbol, interval, first_idx, last_idx):
            return None
        monkeypatch.setattr(fv.ArchiveKlineSource, "_ensure", _no_load)
        holder: List[fv.ArchiveKlineSource] = []
        with pytest.raises(fv.VisionError):
            asyncio.run(fv.fill_cache(
                symbols=["ETHUSDT"], start=_START, end=_END, cfg=cfg,
                cache_dir=tmp_path / "cache", fetcher=fv.VisionFetcher(LocalArchive(), tmp_path / "dl"),
                source_out=holder,
            ))
        src = holder[0]
        assert list((tmp_path / "cache").rglob("*.json.gz")) == []
        assert set(src.calls) == recorded
        assert (fv.leader_params(cfg) is not None) == gate
        if gate:
            assert any(c[0] == "BTCUSDT" and c[1] == "1d" for c in recorded)

    def test_leader_params_defaults_mirror_run_backtest(self):
        assert fv.leader_params(SimpleNamespace()) is None
        assert fv.leader_params(SimpleNamespace(scalper_market_gate=True)) == ("BTCUSDT", 3)
        assert fv.leader_params(SimpleNamespace(
            scalper_market_gate=True, scalper_market_gate_symbol=" ethusdt ",
            scalper_market_gate_run_days="x")) == ("ETHUSDT", 3)


# --------------------------------------------------------------------------
# Aylık / günlük zip seçimi
# --------------------------------------------------------------------------

def _synthetic_4h_month(symbol: str, year: int, month: int) -> List[Candle]:
    """Yalnız zip-seçim mantığını sınamak için ızgara-uyumlu sentetik 4h mumlar."""
    import calendar
    from datetime import datetime, timezone
    step = 14_400_000
    first = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1000)
    n = calendar.monthrange(year, month)[1] * 6
    return [Candle(first + i * step, 1.0 + i, 2.0 + i, 0.5, 1.5 + i, 10.0, first + (i + 1) * step - 1)
            for i in range(n)]


class TestMonthlySelection:
    def _run(self, arch, tmp_path, today):
        src = fv.ArchiveKlineSource(fv.VisionFetcher(arch, tmp_path / "dl", base_url=_BASE), today=today)
        # Temmuz'un TAMAMI: end = 2026-08-01 00:00, 186 adet 4h mum
        out = asyncio.run(src("BTCUSDT", "4h", 186, 1785542400000))
        return src, out

    def test_full_finished_month_uses_monthly_zip(self, tmp_path):
        arch = LocalArchive()
        july = _synthetic_4h_month("BTCUSDT", 2026, 7)
        name = "BTCUSDT-4h-2026-07.zip"
        arch.add_zip("monthly", "BTCUSDT", "4h", "2026-07",
                     _make_zip(name, [_csv_row(c) for c in july], True))
        _, out = self._run(arch, tmp_path, date(2026, 10, 10))
        assert out == july
        assert [u for u in arch.requests if "/daily/" in u] == []
        assert any("/monthly/" in u and u.endswith("BTCUSDT-4h-2026-07.zip") for u in arch.requests)

    def test_monthly_404_falls_back_to_daily(self, tmp_path):
        arch = LocalArchive()
        july = _synthetic_4h_month("BTCUSDT", 2026, 7)
        arch.add_daily_from_candles("BTCUSDT", "4h", july)
        _, out = self._run(arch, tmp_path, date(2026, 10, 10))
        assert out == july
        assert len([u for u in arch.requests if "/daily/" in u and not u.endswith("CHECKSUM")]) == 31

    def test_current_month_never_uses_monthly(self, tmp_path):
        arch = LocalArchive()
        july = _synthetic_4h_month("BTCUSDT", 2026, 7)
        arch.add_daily_from_candles("BTCUSDT", "4h", july)
        _, out = self._run(arch, tmp_path, date(2026, 7, 31))   # "bugün" temmuz içinde
        assert out == july
        assert [u for u in arch.requests if "/monthly/" in u] == []

    def test_candle_outside_its_period_rejected(self, tmp_path):
        arch = LocalArchive()
        july = _synthetic_4h_month("BTCUSDT", 2026, 7)
        arch.add_zip("monthly", "BTCUSDT", "4h", "2026-07",
                     _make_zip("BTCUSDT-4h-2026-07.zip", [_csv_row(c) for c in july[1:]]
                               + [_csv_row(_synthetic_4h_month("BTCUSDT", 2026, 8)[0])], False))
        with pytest.raises(fv.VisionError, match="dışında"):
            self._run(arch, tmp_path, date(2026, 10, 10))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

class TestCli:
    def test_cli_end_to_end_with_local_transport(self, tmp_path, capsys):
        arch = _golden_archive()
        rc = fv.main([
            "--symbols", "BTCUSDT,ETHUSDT", "--start", _START, "--end", _END,
            "--tf-entry", "5m", "--tf-context", "15m", "--tf-regime", "15m", "--no-market-gate",
            "--cache-dir", str(tmp_path / "c"), "--download-cache", str(tmp_path / "d"),
        ], transport=arch)
        out = capsys.readouterr().out
        assert rc == 0
        assert "ÖZET: 6 dosya" in out and "3880 mum" in out
        assert len(list((tmp_path / "c").glob("*.json.gz"))) == 6

    def test_cli_error_exit_code_and_no_files(self, tmp_path, capsys):
        rc = fv.main([
            "--symbols", "BTCUSDT", "--start", _START, "--end", _END, "--no-market-gate",
            "--cache-dir", str(tmp_path / "c"), "--download-cache", str(tmp_path / "d"),
        ], transport=LocalArchive())            # arşiv boş: her şey 404
        err = capsys.readouterr().err
        assert rc == 2 and "HİÇBİR dosya yazılmadı" in err
        assert list((tmp_path / "c").rglob("*.json.gz")) == []

    def _cli_args(self, tmp_path):
        return [
            "--symbols", "BTCUSDT,ETHUSDT", "--start", _START, "--end", _END,
            "--tf-entry", "5m", "--tf-context", "15m", "--tf-regime", "15m", "--no-market-gate",
            "--cache-dir", str(tmp_path / "c"), "--download-cache", str(tmp_path / "d"),
        ]

    def test_cli_oserror_before_promotion_exits_2_nothing_written(self, tmp_path, capsys, monkeypatch):
        """Terfi ÖNCESİ G/Ç hatası (hazırlık dizini/disk): traceback değil, çıkış 2
        ve 'HİÇBİR dosya yazılmadı' (doğru mesaj)."""
        def boom(*_a, **_k):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(fv.tempfile, "mkdtemp", boom)
        rc = fv.main(self._cli_args(tmp_path), transport=_golden_archive())
        err = capsys.readouterr().err
        assert rc == 2
        assert "No space left" in err and "HİÇBİR dosya yazılmadı" in err
        assert "KISMEN" not in err
        assert list((tmp_path / "c").rglob("*.json.gz")) == []

    def test_cli_oserror_during_promotion_exits_2_with_partial_warning(self, tmp_path, capsys, monkeypatch):
        """Terfi SIRASINDA G/Ç hatası: 'HİÇBİR dosya yazılmadı' YALAN olurdu —
        ilk dosya taşınmış olabilir; mesaj kısmi güncellemeyi söylemeli."""
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            # Yalnız TERFİ (hedef önbellek dizinine taşıma) sayılır; hazırlık
            # dizinindeki kline_cache.save atomik yazımları geçer.
            if Path(dst).parent == tmp_path / "c":
                calls["n"] += 1
                if calls["n"] == 3:
                    raise OSError(5, "Input/output error")
            return real_replace(src, dst)
        monkeypatch.setattr(fv.os, "replace", flaky)
        rc = fv.main(self._cli_args(tmp_path), transport=_golden_archive())
        err = capsys.readouterr().err
        assert rc == 2
        assert "KISMEN güncellenmiş olabilir" in err
        assert "tek başına doğrulanmıştır" in err
        assert "HİÇBİR dosya yazılmadı" not in err
        # Gerçekten kısmi: 2 dosya terfi etti, her biri tam ve doğrulanabilir.
        written = sorted((tmp_path / "c").glob("*.json.gz"))
        assert len(written) == 2
        for f in written:
            fv.verify_cache_file(f)

    def test_cli_rejects_bad_window_and_strategy(self, tmp_path, capsys):
        base = ["--cache-dir", str(tmp_path / "c"), "--download-cache", str(tmp_path / "d")]
        assert fv.main(["--start", "2026-08-10", "--end", "2026-08-07", *base], transport=LocalArchive()) == 2
        assert fv.main(["--start", _START, "--end", _END, "--strategies", "ZZZ", *base],
                       transport=LocalArchive()) == 2


# --------------------------------------------------------------------------
# HttpTransport yeniden deneme sınıflaması
# --------------------------------------------------------------------------

class TestHttpRetryClassification:
    @staticmethod
    def _transport(monkeypatch, codes):
        import urllib.error

        seq = list(codes)
        calls = {"n": 0}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"ok"

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            code = seq.pop(0)
            if code == 200:
                return _Resp()
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)

        monkeypatch.setattr(fv.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(fv.time, "sleep", lambda _s: None)
        return fv.HttpTransport(timeout=1.0, retries=4, backoff=0.0), calls

    @pytest.mark.parametrize("code", [408, 429, 500, 503])
    def test_transient_codes_are_retried(self, monkeypatch, code):
        t, calls = self._transport(monkeypatch, [code, 200])
        assert t("https://x/y.zip") == b"ok"
        assert calls["n"] == 2

    @pytest.mark.parametrize("code", [400, 403, 410])
    def test_permanent_4xx_not_retried(self, monkeypatch, code):
        t, calls = self._transport(monkeypatch, [code, 200])
        with pytest.raises(fv.VisionError):
            t("https://x/y.zip")
        assert calls["n"] == 1

    def test_404_is_none_not_retried(self, monkeypatch):
        t, calls = self._transport(monkeypatch, [404, 200])
        assert t("https://x/y.zip") is None
        assert calls["n"] == 1


# --------------------------------------------------------------------------
# AĞ gerektiren kabul testi — yalnız VISION_NETWORK_TESTS=1
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("VISION_NETWORK_TESTS") != "1",
                    reason="ağ gerektirir: VISION_NETWORK_TESTS=1 ile koşturun")
def test_network_acceptance_matches_fapi_fixtures(tmp_path):
    """GERÇEK data.binance.vision indirmesi: 2026-08-07→08-10 BTC+ETH 5m+15m için
    üretilen önbellek, fapi'den üretilmiş tests/fixtures/klines ile alan alan eşit."""
    fetcher = fv.VisionFetcher(
        fv.HttpTransport(timeout=30.0, retries=4), tmp_path / "dl", base_url=fv.DEFAULT_BASE_URL)
    cache = tmp_path / "cache"
    report = asyncio.run(fv.fill_cache(
        symbols=["BTCUSDT", "ETHUSDT"], start=_START, end=_END, cfg=SimpleNamespace(),
        cache_dir=cache, fetcher=fetcher, timeframes=_GOLDEN_TFS,
    ))
    fixture_names = {p.name for p in FIXTURE_KLINES.glob("*.json.gz")}
    assert {p.name for p in cache.glob("*.json.gz")} == fixture_names
    total = 0
    for name in sorted(fixture_names):
        m = fv._CACHE_NAME_RE.match(name)
        args = (m["symbol"], m["interval"], int(m["start"]), int(m["end"]))
        mine, ref = kline_cache.load(cache, *args), kline_cache.load(FIXTURE_KLINES, *args)
        assert mine is not None and ref is not None and len(mine) == len(ref)
        for a, b in zip(mine, ref):
            assert a == b, f"{name}: {a} != {b}"
        total += len(mine)
    assert total == sum(f.candles for f in report.files) == 2 * (1014 + 388 + 538)
