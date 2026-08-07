"""
ScalpTracker — scalper işlemlerinin veritabanı kaydı ve istatistik özetleri.

executor.py ve exits.py'den bağımsız olarak kendi AsyncSessionLocal
oturumunu açar/kapatır; böylece çağıran taraflara ekstra bir session
yaşam döngüsü sorumluluğu bindirmez.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from src.core.database import AsyncSessionLocal
from src.core.logger import app_logger
from src.models.scalp_trade import ScalpTradeModel
from src.strategies.scalper.types import ScalpSignal


class ScalpTracker:
    """scalp_trades tablosuna yazan ve strateji bazlı istatistik üreten katman."""

    def __init__(self):
        self.logger = app_logger

    async def record_open(
        self,
        signal: ScalpSignal,
        entry_price: float,
        quantity: float,
        leverage: int,
        margin_usdt: float,
        sl_algo_id: Optional[str],
        tp1_algo_id: Optional[str],
        tp2_algo_id: Optional[str],
    ) -> int:
        """Yeni scalp işlemini OPEN durumunda kaydet; satır id'sini döner."""
        async with AsyncSessionLocal() as session:
            trade = ScalpTradeModel(
                strategy=signal.strategy,
                symbol=signal.symbol,
                direction=signal.direction.value,
                entry_price=entry_price,
                quantity=quantity,
                leverage=leverage,
                margin_usdt=margin_usdt,
                signal_reason=signal.reason,
                status="OPEN",
                opened_at=datetime.utcnow(),
                sl_algo_id=sl_algo_id,
                tp1_algo_id=tp1_algo_id,
                tp2_algo_id=tp2_algo_id,
            )
            session.add(trade)
            await session.commit()
            self.logger.info(
                f"📝 Scalp işlem kaydı açıldı: #{trade.id} {signal.strategy}/{signal.symbol} "
                f"{signal.direction.value} @ {entry_price}"
            )
            return trade.id

    async def record_close(
        self,
        trade_id: int,
        exit_price: float,
        realized_pnl: float,
        exit_reason: str,
        mae_pct: float = 0.0,
        mfe_pct: float = 0.0,
    ) -> None:
        """İşlemi kapat: exit fiyatı, gerçekleşen PNL, ROI% ve MAE/MFE yaz.

        roi_pct = realized_pnl / margin_usdt * 100 (marja göre — kaldıraçlı
        gerçek getiri budur, nominal değere göre değil).
        """
        async with AsyncSessionLocal() as session:
            trade = await session.get(ScalpTradeModel, trade_id)
            if trade is None:
                self.logger.error(f"❌ Scalp işlem kaydı bulunamadı: #{trade_id}, kapanış yazılamadı")
                return

            margin = trade.margin_usdt or 0.0
            roi_pct = (realized_pnl / margin * 100.0) if margin > 0 else 0.0

            trade.exit_price = exit_price
            trade.realized_pnl = realized_pnl
            trade.roi_pct = roi_pct
            trade.exit_reason = exit_reason
            trade.mae_pct = mae_pct
            trade.mfe_pct = mfe_pct
            trade.status = "CLOSED"
            trade.closed_at = datetime.utcnow()
            await session.commit()

            self.logger.info(
                f"📝 Scalp işlem kaydı kapandı: #{trade_id} PNL={realized_pnl:.2f} "
                f"ROI={roi_pct:.2f}% neden={exit_reason}",
                extra={"trade": True},
            )

    async def today_realized_pnl(self) -> float:
        """Bugün (UTC gün başından itibaren) kapanan scalp işlemlerinin toplam PNL'i."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(ScalpTradeModel.realized_pnl), 0.0)).where(
                    ScalpTradeModel.status == "CLOSED",
                    ScalpTradeModel.closed_at >= today_start,
                )
            )
            return float(result.scalar() or 0.0)

    async def stats(self) -> Dict[str, Dict[str, Any]]:
        """Strateji bazında kapanmış işlem istatistikleri.

        Döner: {"A": {"trades", "wins", "winrate", "total_pnl", "avg_roi",
                       "profit_factor"}, ...}
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ScalpTradeModel).where(ScalpTradeModel.status == "CLOSED")
            )
            trades = list(result.scalars().all())

        by_strategy: Dict[str, List[ScalpTradeModel]] = {}
        for t in trades:
            by_strategy.setdefault(t.strategy, []).append(t)

        out: Dict[str, Dict[str, Any]] = {}
        for strategy, rows in by_strategy.items():
            n = len(rows)
            wins = [r for r in rows if (r.realized_pnl or 0.0) > 0]
            losses = [r for r in rows if (r.realized_pnl or 0.0) < 0]
            total_pnl = sum(r.realized_pnl or 0.0 for r in rows)
            avg_roi = (sum(r.roi_pct or 0.0 for r in rows) / n) if n else 0.0
            gross_profit = sum(r.realized_pnl or 0.0 for r in wins)
            gross_loss = abs(sum(r.realized_pnl or 0.0 for r in losses))
            if gross_loss > 0:
                profit_factor = gross_profit / gross_loss
            else:
                profit_factor = float("inf") if gross_profit > 0 else 0.0

            out[strategy] = {
                "trades": n,
                "wins": len(wins),
                "winrate": (len(wins) / n * 100.0) if n else 0.0,
                "total_pnl": total_pnl,
                "avg_roi": avg_roi,
                "profit_factor": profit_factor,
            }
        return out

    async def open_trades(self) -> List[ScalpTradeModel]:
        """DB'de status=OPEN olan tüm scalp işlemleri (restart kurtarma için)."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ScalpTradeModel).where(ScalpTradeModel.status == "OPEN")
            )
            return list(result.scalars().all())
