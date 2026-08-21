"""
Kline (mum) önbelleği — backtest.py'nin geçmiş veri çekimini diskte
gzip'lenmiş JSON olarak saklar; aynı (sembol, aralık, pencere) için
sonraki koşular Binance'e gitmeden buradan okur. Amaç: backtest'i AĞSIZ
ve deterministik hâle getirmek (bkz. tests/test_golden_backtest.py).

Anahtar/dosya adı: {symbol}_{interval}_{start_ms}_{end_ms}.json.gz
`start_ms`, backtest.gather_symbol_data'nın o an isteyeceği TOPLAM mum
sayısından (`needed` = gün×gün-içi-mum-sayısı + warm-up) ve `interval`
süresinden türetilir (bkz. window_start_ms). `needed` değişirse (örn.
warm-up sabitleri ileride büyürse) dosya adı da değişir — bu yüzden
ESKİ/yanlış boyutlu bir önbellek asla sessizce yanlış eşleşmez; en kötü
ihtimalle bir cache-miss olur ve taze veri çekilir.

Dosya biçimi: gzip JSON dizisi, her eleman Candle alanlarının sözlüğü
(open_time/open/high/low/close/volume/close_time) — küçük, commit'lenebilir,
`zcat dosya.json.gz | python3 -m json.tool` ile insan tarafından okunabilir.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Dict, List, Optional

from src.strategies.scalper.types import Candle

# Aralık -> milisaniye süre. YALNIZ önbellek dosya adı için "start"
# türetmede kullanılır; gerçek çekim backtest.fetch_paginated'ın
# total_needed/end_time sözleşmesiyle yapılır — bu modül o mantığı
# DEĞİŞTİRMEZ, yalnız aynı pencereyi adlandırmak için türetir.
_INTERVAL_MS: Dict[str, int] = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
}


def window_start_ms(interval: str, needed: int, end_ms: int) -> int:
    """`needed` mumluk pencerenin (interval'e göre) türetilmiş başlangıcı.

    Yalnız önbellek anahtarı/dosya adı için kullanılır — gerçek sayfalama
    fetch_paginated'da (total_needed, end_time) ile yapılır, burada tekrar
    üretilmez. Bilinmeyen `interval` için ValueError (sessiz yanlış anahtar
    YOK).
    """
    step = _INTERVAL_MS.get(interval)
    if step is None:
        raise ValueError(f"Desteklenmeyen zaman dilimi: {interval!r}")
    return end_ms - needed * step


def cache_file(cache_dir: Path, symbol: str, interval: str, start_ms: int, end_ms: int) -> Path:
    return Path(cache_dir) / f"{symbol}_{interval}_{start_ms}_{end_ms}.json.gz"


def load(
    cache_dir: Path, symbol: str, interval: str, start_ms: int, end_ms: int,
) -> Optional[List[Candle]]:
    """Önbellekte varsa mum listesini (eski→yeni) döner; yoksa None.

    Bozuk/okunamaz bir dosya da None sayılır (çağıran taraf bunu cache-miss
    gibi işleyip taze çeker) — asla istisna yükseltip backtest'i durdurmaz.
    """
    path = cache_file(cache_dir, symbol, interval, start_ms, end_ms)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            raw = json.load(f)
        return [Candle(**row) for row in raw]
    except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError, TypeError, KeyError):
        return None


def save(
    cache_dir: Path, symbol: str, interval: str, start_ms: int, end_ms: int,
    candles: List[Candle],
) -> None:
    """Mum listesini gzip JSON olarak yazar.

    Atomik: önce `.tmp` dosyasına yazılır, sonra hedef ada taşınır — yarım
    yazılmış bir dosya asla `load()`'a görünmez (ör. süreç ortasında
    kesilirse eski dosya bozulmadan kalır).
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_file(cache_dir, symbol, interval, start_ms, end_ms)
    tmp_path = path.with_name(path.name + ".tmp")
    rows = [
        {
            "open_time": c.open_time, "open": c.open, "high": c.high,
            "low": c.low, "close": c.close, "volume": c.volume,
            "close_time": c.close_time,
        }
        for c in candles
    ]
    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        json.dump(rows, f, separators=(",", ":"))
    tmp_path.replace(path)
