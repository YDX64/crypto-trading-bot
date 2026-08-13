"""TradingView çoklu-kaynak sağlama (confluence) motoru (2026-08-13).

Kullanıcı vizyonu: "birden çok sinyalin sağlama yapmasıyla daha iyi ve
sorunsuz sinyallere sahip oluruz" — tek göstergenin sözüyle işlem açmak
yerine, FARKLI göstergeler (AlgoPro V1.6, LuxAlgo S&O, ...) aynı yönde oy
verdiğinde işlem açılır.

Kurallar:
- Oy = (sembol, yön, kaynak). Aynı kaynağın tekrar oyu SAYIYI ARTIRMAZ,
  yalnız zaman damgasını tazeler (tek gösterge kendi başına eşiği dolduramaz).
- Pencere: `window_seconds` içinde gelen oylar geçerli; eskiyenler düşer.
- Çelişki: aynı sembole TERS yönde oy gelirse iki taraf da sıfırlanır ve
  yeni oy tek başına kalır — göstergeler anlaşamıyorsa sinyal "temiz" değildir.
- Eşik: pencere içinde FARKLI kaynak sayısı >= required → tetiklenir ve o
  sembol+yönün oyları temizlenir (aynı mumda çifte tetik olmaz).

Eşzamanlılık notu: FastAPI tek event-loop'ta çağırır ve vote() içinde await
yoktur — kilit gerekmez.
"""

from __future__ import annotations

import time
from typing import Dict, List, Tuple

from src.core.logger import app_logger


class TvConfluence:
    def __init__(self, required: int, window_seconds: float):
        self.required = max(1, int(required))
        self.window_seconds = max(1.0, float(window_seconds))
        self.logger = app_logger
        # (SEMBOL, YON) -> {kaynak: son_oy_epoch}
        self._votes: Dict[Tuple[str, str], Dict[str, float]] = {}

    def _prune(self, now: float) -> None:
        for key, sources in list(self._votes.items()):
            for src, ts in list(sources.items()):
                if now - ts > self.window_seconds:
                    sources.pop(src, None)
            if not sources:
                self._votes.pop(key, None)

    def vote(self, symbol: str, direction: str, source: str) -> Dict:
        """Oy kaydet; eşik dolduysa triggered=True döner ve oyları temizler."""
        now = time.time()
        self._prune(now)

        symbol = str(symbol).upper()
        direction = str(direction).upper()
        source = str(source or "tv").lower()
        key = (symbol, direction)
        opposite = (symbol, "SHORT" if direction == "LONG" else "LONG")

        if opposite in self._votes:
            dropped = sorted(self._votes.pop(opposite).keys())
            self.logger.info(
                f"⚖️ {symbol}: ters yön oyu geldi ({direction} ← {source}); "
                f"{opposite[1]} oyları sıfırlandı ({dropped}) — çelişkide sinyal temiz değil"
            )
            # Çelişki anında mevcut yöndeki eski oylar da güvenilmez:
            self._votes.pop(key, None)

        sources = self._votes.setdefault(key, {})
        sources[source] = now
        count = len(sources)
        verdict = {
            "symbol": symbol,
            "direction": direction,
            "votes": count,
            "required": self.required,
            "sources": sorted(sources.keys()),
            "window_seconds": self.window_seconds,
            "triggered": count >= self.required,
        }
        if verdict["triggered"]:
            self._votes.pop(key, None)
            self.logger.info(
                f"✅ Sağlama tamam: {symbol} {direction} — kaynaklar "
                f"{verdict['sources']} ({count}/{self.required})",
                extra={"trade": True},
            )
        else:
            self.logger.info(
                f"🗳️ Sağlama oyu: {symbol} {direction} ← {source} "
                f"({count}/{self.required}, pencere {self.window_seconds:.0f}s)"
            )
        return verdict

    def snapshot(self) -> List[Dict]:
        """Dashboard/teşhis: bekleyen oylar."""
        now = time.time()
        self._prune(now)
        rows = []
        for (symbol, direction), sources in sorted(self._votes.items()):
            rows.append({
                "symbol": symbol,
                "direction": direction,
                "sources": sorted(sources.keys()),
                "oldest_age_seconds": round(now - min(sources.values()), 1),
            })
        return rows
