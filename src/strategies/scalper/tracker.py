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
        # Her başarılı record_close'ta artar. Engine'in günlük income
        # önbelleği bunu karşılaştırarak kapanış sonrası TTL beklemeden
        # taze okur (kill switch tepkisi kapanışa kilitli, TTL'e değil).
        self.close_seq: int = 0

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
        entry_order_id: Optional[str] = None,
        tp3_algo_id: Optional[str] = None,
    ) -> int:
        """Yeni scalp işlemini OPEN durumunda kaydet; satır id'sini döner.

        ``tp3_algo_id`` yalnız AlgoPro takipçi halkası (D20, 3 parça çıkış)
        tarafından verilir; scalper çağrılarında None kalır — davranış aynı.
        """
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
                tp3_algo_id=tp3_algo_id,
                entry_order_id=entry_order_id or None,
            )
            session.add(trade)
            await session.commit()
            self.logger.info(
                f"📝 Scalp işlem kaydı açıldı: #{trade.id} {signal.strategy}/{signal.symbol} "
                f"{signal.direction.value} @ {entry_price}"
            )
            return trade.id

    async def record_shadow(
        self,
        signal: ScalpSignal,
        entry_price: float,
        quantity: float,
        leverage: int,
        margin_usdt: float,
    ) -> int:
        """Gölge modunda emir GÖNDERİLMEDEN sinyali SHADOW olarak kaydet.

        entry_price sinyal fiyatıdır (gerçek dolum yok, borsaya hiç emir
        gitmedi). status="SHADOW" — stats()/open_trades() yalnız
        "CLOSED"/"OPEN" sorguladığı için bu satırlar istatistiklerden,
        kapasite sayımından ve restart kurtarmasından KENDİLİĞİNDEN dışlanır
        (bkz. docs/MAINNET_PLAN.md §3, D14). Cooldown/loss-cooldown burada
        HİÇ tetiklenmez — hiçbir risk alınmadı.
        """
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
                status="SHADOW",
                opened_at=datetime.utcnow(),
                notes="shadow_mode",
            )
            session.add(trade)
            await session.commit()
            self.logger.info(
                f"👻 GÖLGE: {signal.symbol} {signal.direction.value} @{entry_price} "
                f"({signal.reason})"
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
        pnl_source: Optional[str] = None,
        notes: Optional[str] = None,
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
            trade.notes = self._merge_close_notes(
                existing=trade.notes,
                pnl_source=pnl_source,
                notes=notes,
            )
            trade.status = "CLOSED"
            trade.closed_at = datetime.utcnow()
            await session.commit()
            self.close_seq += 1

            self.logger.info(
                f"📝 Scalp işlem kaydı kapandı: #{trade_id} PNL={realized_pnl:.2f} "
                f"ROI={roi_pct:.2f}% neden={exit_reason}",
                extra={"trade": True},
            )

    async def record_failed_execution(
        self,
        *,
        signal: ScalpSignal,
        entry_price: float,
        exit_price: float,
        quantity: float,
        leverage: int,
        realized_pnl: float,
        pnl_source: str,
        entry_order_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """İlk SL kurulamayınca acil kapatılan fill'i doğrudan CLOSED yaz.

        Eski akış ``record_open`` aşamasına gelmeden ``None`` döndüğü için bu
        gerçek giriş/çıkış tamamen kayboluyordu. Tek transaction, dashboard'un
        kısa süreli hayalet OPEN kayıt görmesini de engeller.
        """
        now = datetime.utcnow()
        margin = (quantity * entry_price / leverage) if leverage else quantity * entry_price
        roi_pct = (realized_pnl / margin * 100.0) if margin > 0 else 0.0
        order_note = f"entry_order_id={entry_order_id}" if entry_order_id else None
        merged_notes = self._merge_close_notes(
            existing=None,
            pnl_source=pnl_source,
            notes=";".join(
                part for part in ("initial_sl_failed_emergency_close", order_note, notes) if part
            ),
        )

        async with AsyncSessionLocal() as session:
            trade = ScalpTradeModel(
                strategy=signal.strategy,
                symbol=signal.symbol,
                direction=signal.direction.value,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                leverage=leverage,
                margin_usdt=margin,
                realized_pnl=realized_pnl,
                roi_pct=roi_pct,
                exit_reason="PROTECTION_FAILED_FLAT",
                signal_reason=signal.reason,
                mae_pct=0.0,
                mfe_pct=0.0,
                status="CLOSED",
                opened_at=now,
                closed_at=now,
                notes=merged_notes,
            )
            session.add(trade)
            await session.commit()
            self.logger.warning(
                f"🧾 Başarısız koruma işlemi kaydedildi: #{trade.id} "
                f"{signal.symbol} PNL={realized_pnl:.8f} kaynak={pnl_source}",
                extra={"trade": True},
            )
            return int(trade.id)

    @staticmethod
    def _merge_close_notes(
        existing: Optional[str],
        pnl_source: Optional[str],
        notes: Optional[str],
    ) -> Optional[str]:
        """Kapanış meta verisini mevcut ``notes`` sütununa idempotent ekle.

        Yeni bir şema alanı açmadan kaynağı makinece okunabilir
        ``pnl_source=<değer>`` etiketiyle saklarız. Eski kayıtlarda bu etiket
        bulunmadığı için istatistik katmanı onları açıkça ``legacy`` sayar.
        """
        parts = [part.strip() for part in (existing or "").split(";") if part.strip()]
        if pnl_source:
            parts = [part for part in parts if not part.startswith("pnl_source=")]
            parts.append(f"pnl_source={pnl_source}")
        if notes and notes not in parts:
            parts.append(notes)
        return ";".join(parts) or None

    @staticmethod
    def _pnl_source(notes: Optional[str]) -> str:
        for part in (notes or "").split(";"):
            if part.startswith("pnl_source="):
                value = part.split("=", 1)[1]
                if value in ("binance_income_net", "binance_account_trades_net"):
                    return "verified"
                # "binance_trades_close_net": kapanış bacağı borsa satırlarıyla
                # kanıtlı ama giriş komisyonu tahmini + funding hariç — pozitif
                # sonucu sermayeye eklemek için yeterince kesin değil; kayıp
                # zaten fallback yolundan dahil olur (kayıp saklanmaz kuralı).
                if value in ("estimated_gross", "binance_trades_close_net"):
                    return "fallback"
        return "legacy"

    @staticmethod
    def _pnl_basis(verified: int, fallback: int, legacy: int) -> str:
        populated = sum(count > 0 for count in (verified, fallback, legacy))
        if populated > 1:
            return "mixed"
        if verified:
            return "binance_income_net"
        if fallback:
            return "estimated_gross"
        return "legacy_unknown"

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

    async def compounding_snapshot(self, start_trade_id: int = 0) -> Dict[str, Any]:
        """Sanal sermayeye eklenebilecek muhafazakâr gerçekleşmiş PnL özeti.

        - Binance tarafından doğrulanmış net PnL: pozitif/negatif dahil.
        - ``estimated_gross``: yalnız negatifse dahil (kayıp saklanmaz).
        - Pozitif fallback ve tüm legacy satırlar: sermayeyi şişiremez.
        """
        start_id = max(0, int(start_trade_id or 0))
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ScalpTradeModel).where(
                    ScalpTradeModel.status == "CLOSED",
                    ScalpTradeModel.id >= start_id,
                )
            )
            rows = list(result.scalars().all())

        eligible_pnl = 0.0
        verified_count = 0
        negative_fallback_count = 0
        excluded_positive_fallback = 0
        excluded_legacy = 0
        for row in rows:
            pnl = float(row.realized_pnl or 0.0)
            source = self._pnl_source(row.notes)
            if source == "verified":
                eligible_pnl += pnl
                verified_count += 1
            elif source == "fallback" and pnl < 0:
                eligible_pnl += pnl
                negative_fallback_count += 1
            elif source == "fallback":
                excluded_positive_fallback += 1
            else:
                excluded_legacy += 1

        return {
            "start_trade_id": start_id,
            "eligible_realized_pnl": eligible_pnl,
            "verified_count": verified_count,
            "negative_fallback_count": negative_fallback_count,
            "excluded_positive_fallback": excluded_positive_fallback,
            "excluded_legacy": excluded_legacy,
        }

    async def eligible_compounding_pnl(self, start_trade_id: int = 0) -> float:
        """``compounding_snapshot`` için geriye uyumlu sayısal sarmalayıcı."""
        snapshot = await self.compounding_snapshot(start_trade_id)
        return float(snapshot["eligible_realized_pnl"])

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

            source_counts = {"verified": 0, "fallback": 0, "legacy": 0}
            for row in rows:
                source_counts[self._pnl_source(row.notes)] += 1

            out[strategy] = {
                "trades": n,
                "wins": len(wins),
                "winrate": (len(wins) / n * 100.0) if n else 0.0,
                "total_pnl": total_pnl,
                "avg_roi": avg_roi,
                "profit_factor": profit_factor,
                "verified_trades": source_counts["verified"],
                "fallback_trades": source_counts["fallback"],
                "legacy_trades": source_counts["legacy"],
                "pnl_basis": self._pnl_basis(
                    source_counts["verified"],
                    source_counts["fallback"],
                    source_counts["legacy"],
                ),
            }
        return out

    async def open_trades(self) -> List[ScalpTradeModel]:
        """DB'de status=OPEN olan tüm scalp işlemleri (restart kurtarma için)."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ScalpTradeModel).where(ScalpTradeModel.status == "OPEN")
            )
            return list(result.scalars().all())
