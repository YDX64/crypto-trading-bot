"""
ScalpTracker — scalper işlemlerinin veritabanı kaydı ve istatistik özetleri.

executor.py ve exits.py'den bağımsız olarak kendi AsyncSessionLocal
oturumunu açar/kapatır; böylece çağıran taraflara ekstra bir session
yaşam döngüsü sorumluluğu bindirmez.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select

from src.core.database import AsyncSessionLocal
from src.core.logger import app_logger
from src.models.scalp_trade import ScalpTradeModel
from src.strategies.scalper.forensics import FORENSICS_VERSION
from src.strategies.scalper.types import ScalpSignal


class ScalpTracker:
    """scalp_trades tablosuna yazan ve strateji bazlı istatistik üreten katman."""

    def __init__(self):
        self.logger = app_logger
        # Her başarılı record_close'ta artar. Engine'in günlük income
        # önbelleği bunu karşılaştırarak kapanış sonrası TTL beklemeden
        # taze okur (kill switch tepkisi kapanışa kilitli, TTL'e değil).
        self.close_seq: int = 0
        # Adli kayıt (D21) serileştirme hatası bir kez uyarılır; işlem akışı
        # ASLA etkilenmez (gözlem, güvenlik kilidi değildir).
        self._forensics_error_logged: bool = False

    # ------------------------------------------------------------------
    # İşlem adli kaydı (D21) — JSON sütunu yardımcıları
    # ------------------------------------------------------------------

    @staticmethod
    def parse_forensics(raw: Optional[str]) -> Optional[Dict[str, Any]]:
        """`scalp_trades.forensics` metnini sözlüğe çevir; bozuksa None."""
        if not raw:
            return None
        try:
            document = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def _dump_forensics(self, document: Optional[Dict[str, Any]]) -> Optional[str]:
        """Belgeyi JSON metnine çevir. Hata çağıranı ASLA etkilemez."""
        if not document:
            return None
        payload = dict(document)
        payload.setdefault("v", FORENSICS_VERSION)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception as e:  # pragma: no cover - savunma
            if not self._forensics_error_logged:
                self._forensics_error_logged = True
                self.logger.warning(
                    f"⚠️ Adli kayıt JSON'a çevrilemedi ({e}) — bu uyarı bir kez "
                    f"loglanır, işlem akışı ETKİLENMEZ"
                )
            return None

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
        forensics: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Yeni scalp işlemini OPEN durumunda kaydet; satır id'sini döner.

        ``tp3_algo_id`` yalnız AlgoPro takipçi halkası (D20, 3 parça çıkış)
        tarafından verilir; scalper çağrılarında None kalır — davranış aynı.

        ``forensics`` (D21) adli kaydın GİRİŞ belgesidir
        (``{"entry": {...}, "verdict": [...]}``). None ise sütun NULL kalır ve
        davranış birebir eskisidir.
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
                forensics=self._dump_forensics(forensics),
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
        forensics_exit: Optional[Dict[str, Any]] = None,
        verdict: Optional[List[str]] = None,
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
            # D21: adli kaydın ÇIKIŞ bölümü. Giriş belgesi (varsa) korunur,
            # yalnız `exit`/`verdict` eklenir. Serileştirme hatası kapanış
            # kaydını ENGELLEMEZ (sütun eski hâlinde kalır).
            if forensics_exit is not None or verdict is not None:
                document = self.parse_forensics(trade.forensics) or {}
                if forensics_exit is not None:
                    document["exit"] = forensics_exit
                if verdict is not None:
                    # BİRLEŞİM, üzerine yazma DEĞİL: giriş etiketleri giriş
                    # ANINDA tam veriyle hesaplandı ve GİRİŞ hakkında bir
                    # olgudur. Restart sonrası kurtarılan bir pozisyonun
                    # bellekteki giriş belgesi YOKTUR (`exits.recover`), o
                    # yüzden kapanışta yalnız çıkış etiketleri türetilebilir —
                    # üzerine yazmak, restart'ı bir veri kaybına çevirirdi.
                    merged_verdict = list(document.get("verdict") or [])
                    for tag in verdict:
                        if tag not in merged_verdict:
                            merged_verdict.append(tag)
                    document["verdict"] = merged_verdict
                merged = self._dump_forensics(document)
                if merged is not None:
                    trade.forensics = merged
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

    async def compounding_snapshot(
        self,
        start_trade_id: int = 0,
        *,
        strategies: Optional[Sequence[str]] = None,
        exclude_strategies: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Sanal sermayeye eklenebilecek muhafazakâr gerçekleşmiş PnL özeti.

        - Binance tarafından doğrulanmış net PnL: pozitif/negatif dahil.
        - ``estimated_gross``: yalnız negatifse dahil (kayıp saklanmaz).
        - Pozitif fallback ve tüm legacy satırlar: sermayeyi şişiremez.

        ``strategies`` / ``exclude_strategies`` (D20b): gömülü takipçi
        (``strategy="AP"``) scalper ile AYNI DB'yi paylaşır. İki defter
        birbirinin sanal sermayesini KİRLETMEMELİDİR: scalper AP'yi dışlar,
        takipçi YALNIZ AP'yi sayar. İkisi de None (varsayılan) = eski
        davranış birebir.
        """
        start_id = max(0, int(start_trade_id or 0))
        wanted = {str(s).strip().upper() for s in (strategies or ()) if str(s).strip()}
        unwanted = {
            str(s).strip().upper()
            for s in (exclude_strategies or ())
            if str(s).strip()
        }
        async with AsyncSessionLocal() as session:
            stmt = select(ScalpTradeModel).where(
                ScalpTradeModel.status == "CLOSED",
                ScalpTradeModel.id >= start_id,
            )
            if wanted:
                stmt = stmt.where(ScalpTradeModel.strategy.in_(sorted(wanted)))
            if unwanted:
                stmt = stmt.where(
                    ScalpTradeModel.strategy.notin_(sorted(unwanted))
                )
            result = await session.execute(stmt)
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

    async def eligible_compounding_pnl(
        self,
        start_trade_id: int = 0,
        *,
        strategies: Optional[Sequence[str]] = None,
        exclude_strategies: Optional[Sequence[str]] = None,
    ) -> float:
        """``compounding_snapshot`` için geriye uyumlu sayısal sarmalayıcı."""
        snapshot = await self.compounding_snapshot(
            start_trade_id,
            strategies=strategies,
            exclude_strategies=exclude_strategies,
        )
        return float(snapshot["eligible_realized_pnl"])

    async def realized_pnl_since(
        self,
        since: datetime,
        *,
        strategies: Optional[Sequence[str]] = None,
        exclude_strategies: Optional[Sequence[str]] = None,
    ) -> float:
        """``since``den (naive UTC) sonra KAPANAN işlemlerin net PnL toplamı.

        D20b: gömülü modda iki motor AYNI hesabı paylaşır ve
        `/fapi/v1/income` iki defteri BİRLİKTE raporlar. Her motorun günlük
        kesicisi bu yüzden KENDİ DEFTERİNDEN beslenir; ``realized_pnl``
        komisyon düşülmüş nettir (`_CloseLedger.net_pnl_estimate`).
        """
        wanted = {str(s).strip().upper() for s in (strategies or ()) if str(s).strip()}
        unwanted = {
            str(s).strip().upper()
            for s in (exclude_strategies or ())
            if str(s).strip()
        }
        async with AsyncSessionLocal() as session:
            stmt = select(
                func.coalesce(func.sum(ScalpTradeModel.realized_pnl), 0.0)
            ).where(
                ScalpTradeModel.status == "CLOSED",
                ScalpTradeModel.closed_at >= since,
            )
            if wanted:
                stmt = stmt.where(ScalpTradeModel.strategy.in_(sorted(wanted)))
            if unwanted:
                stmt = stmt.where(ScalpTradeModel.strategy.notin_(sorted(unwanted)))
            result = await session.execute(stmt)
            return float(result.scalar() or 0.0)

    async def strategy_realized_pnl_since(
        self, strategy: str, since: datetime
    ) -> float:
        """``realized_pnl_since`` için tek-strateji sarmalayıcı (geri uyum)."""
        wanted = str(strategy or "").strip().upper()
        if not wanted:
            return 0.0
        return await self.realized_pnl_since(since, strategies=(wanted,))

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

    async def open_trades(
        self,
        *,
        strategies: Optional[Sequence[str]] = None,
        exclude_strategies: Optional[Sequence[str]] = None,
    ) -> List[ScalpTradeModel]:
        """DB'de status=OPEN olan scalp işlemleri (restart kurtarma için).

        ``strategies`` / ``exclude_strategies`` (D20b düşmanca inceleme, KRİTİK
        bulgu): gömülü modda scalper ve takipçi AYNI `scalp_trades` tablosunu
        paylaşır. Filtre YOKKEN her iki motorun `recover()`'ı DİĞERİNİN açık
        satırını kendi pozisyonu sanıp izlemeye alıyordu → aynı net pozisyonun
        İKİ yöneticisi (iki stop taşıma, iki kapanış defteri, AlgoPro
        pozisyonuna scalper'ın chandelier/reaper kuralları). İkisi de None
        (varsayılan) = eski davranış birebir.
        """
        wanted = {str(s).strip().upper() for s in (strategies or ()) if str(s).strip()}
        unwanted = {
            str(s).strip().upper()
            for s in (exclude_strategies or ())
            if str(s).strip()
        }
        async with AsyncSessionLocal() as session:
            stmt = select(ScalpTradeModel).where(ScalpTradeModel.status == "OPEN")
            if wanted:
                stmt = stmt.where(ScalpTradeModel.strategy.in_(sorted(wanted)))
            if unwanted:
                stmt = stmt.where(ScalpTradeModel.strategy.notin_(sorted(unwanted)))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Adli kayıt okuma/güncelleme (D21) — yalnız GÖZLEM yolları
    # ------------------------------------------------------------------

    @staticmethod
    def forensics_row(trade: ScalpTradeModel) -> Dict[str, Any]:
        """Bir DB satırını uçların/panonun beklediği adli kayıt biçimine çevir."""
        document = ScalpTracker.parse_forensics(trade.forensics) or {}
        return {
            "id": trade.id,
            "strategy": trade.strategy,
            "symbol": trade.symbol,
            "direction": trade.direction,
            "status": trade.status,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "realized_pnl": trade.realized_pnl,
            "roi_pct": trade.roi_pct,
            "exit_reason": trade.exit_reason,
            "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
            "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
            "signal_reason": trade.signal_reason,
            "pnl_source": (
                None if trade.status == "SHADOW"
                else ScalpTracker._pnl_source(trade.notes)
            ),
            "has_forensics": bool(document),
            "verdict": list(document.get("verdict") or []),
            "entry": document.get("entry"),
            "exit": document.get("exit"),
            "postmortem": document.get("postmortem"),
            # D23: AI karar katmanının (gölge) bu işlem için ürettiği kayıt.
            # `attach_ai` yazar; yoksa None (katman kapalı ya da karar
            # yetişmedi) — "AI baktı, izin verdi" ile karıştırılmamalı.
            "ai": document.get("ai"),
        }

    async def attach_ai(
        self, trade_id: int, record: Optional[Dict[str, Any]]
    ) -> bool:
        """D23: AI kararını işlemin adli belgesine `document['ai']` olarak ekle.

        MIGRATION YOK: mevcut `scalp_trades.forensics` JSON sütununa yazılır.
        `record_close` bu belgeyi OKUYUP yalnız `exit`/`verdict` anahtarlarını
        eklediği için `ai` bloğu kapanışta KENDİLİĞİNDEN korunur (tracker.py
        "BİRLEŞİM, üzerine yazma DEĞİL" kuralı).

        Yalnız GÖZLEM: hata çağıranı ASLA etkilemez (arka plan görevinden
        çağrılır) ve işlem satırı bulunamazsa sessizce False döner.
        """
        if not record:
            return False
        try:
            async with AsyncSessionLocal() as session:
                trade = await session.get(ScalpTradeModel, int(trade_id))
                if trade is None:
                    return False
                document = self.parse_forensics(trade.forensics) or {}
                document["ai"] = dict(record)
                merged = self._dump_forensics(document)
                if merged is None:
                    return False
                trade.forensics = merged
                await session.commit()
            return True
        except Exception as e:
            # D21 disiplini: adli kayıt bir gözlem katmanıdır; DB hatası
            # işlem akışını ETKİLEMEZ ve bir kez uyarılır.
            if not self._forensics_error_logged:
                self._forensics_error_logged = True
                self.logger.warning(
                    f"⚠️ AI kararı adli belgeye yazılamadı (#{trade_id}: {e}) — "
                    f"bu uyarı bir kez loglanır, işlem akışı ETKİLENMEZ "
                    f"(kayıt logs/trades.jsonl'de durur)"
                )
            return False

    async def forensics_for(self, trade_id: int) -> Optional[Dict[str, Any]]:
        """Tek bir işlemin adli kaydı (yoksa None)."""
        async with AsyncSessionLocal() as session:
            trade = await session.get(ScalpTradeModel, int(trade_id))
            if trade is None:
                return None
            return self.forensics_row(trade)

    async def recent_forensics(self, limit: int = 50) -> List[Dict[str, Any]]:
        """En yeni kapanmış işlemlerin adli kaydı (en yeni önce)."""
        capped = max(1, min(int(limit or 50), 500))
        order_col = func.coalesce(ScalpTradeModel.closed_at, ScalpTradeModel.opened_at)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ScalpTradeModel)
                .where(ScalpTradeModel.status == "CLOSED")
                .order_by(order_col.desc())
                .limit(capped)
            )
            return [self.forensics_row(row) for row in result.scalars().all()]

    async def forensics_summary(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        *,
        strategies: Optional[Sequence[str]] = None,
        exclude_strategies: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Etiket × sonuç tablosu — "neler etkiliyor" sorusunun cevabı.

        D20b: gömülü takipçi AYNI tabloya yazar ve onun etiketleri (kapılar
        `off`, gösterge yok) scalper'ın etiket istatistiğini kirletir. Uç
        nokta AP'yi VARSAYILAN olarak dışlar; `?strategy=AP` ile takipçinin
        kendi tablosu ayrıca çekilebilir.
        """
        from src.strategies.scalper.forensics import (
            expectation_from_entry,
            summarize,
        )

        filters = [ScalpTradeModel.status == "CLOSED"]
        wanted = {str(x).strip().upper() for x in (strategies or ()) if str(x).strip()}
        unwanted = {
            str(x).strip().upper()
            for x in (exclude_strategies or ())
            if str(x).strip()
        }
        if wanted:
            filters.append(ScalpTradeModel.strategy.in_(sorted(wanted)))
        if unwanted:
            filters.append(ScalpTradeModel.strategy.notin_(sorted(unwanted)))
        if since is not None:
            filters.append(ScalpTradeModel.closed_at >= since)
        if until is not None:
            filters.append(ScalpTradeModel.closed_at <= until)
        # YALNIZ iki sütun: bu uç pano tarafından düzenli yoklanır ve tam
        # satırları (uzun `forensics` JSON'ları dahil) ORM nesnesine
        # çevirmek gereksiz iştir (bkz. "dashboard polling açlığı" dersi).
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(
                    ScalpTradeModel.realized_pnl, ScalpTradeModel.forensics
                ).where(*filters)
            )
            trades = list(result.all())

        rows = []
        tagged = 0
        for realized_pnl, raw_forensics in trades:
            document = self.parse_forensics(raw_forensics) or {}
            if document:
                tagged += 1
            exit_block = document.get("exit")
            rows.append({
                "tags": document.get("verdict") or [],
                "pnl": float(realized_pnl or 0.0),
                # D24/madde 6: "ne BEKLEDİK" bloğu. Doldurulmadıysa None =
                # ÖLÇÜLMEDİ (beklenti kurulmamıştı DEĞİL).
                "expectation": expectation_from_entry(document.get("entry")),
                # D27/A1: çıkış nedeni × sonuç kırılımı için. Adli kaydı
                # OLMAYAN satırlarda `None` → `_bilinmiyor_` kovası
                # ("ölçülmedi", "nedensiz kapandı" DEĞİL).
                "exit_reason": (
                    exit_block.get("reason")
                    if isinstance(exit_block, dict) else None
                ),
            })
        summary = summarize(rows)
        summary["since"] = since.isoformat() if since else None
        summary["until"] = until.isoformat() if until else None
        # Kapsama: adli kaydı OLMAYAN işlemler etiketsiz görünür ama bu bir
        # "temiz işlem" değil "ölçülmemiş işlem"tir — ayrımı görünür kıl.
        summary["with_forensics"] = tagged
        summary["without_forensics"] = len(trades) - tagged
        return summary

    async def postmortem_candidates(
        self,
        *,
        now: datetime,
        min_age_minutes: float,
        max_age_hours: float = 12.0,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Post-mortem penceresi DOLMUŞ ama henüz doldurulmamış kapanışlar.

        Pencere ALT sınırı: kapanıştan en az `min_age_minutes` geçmiş olmalı
        (aksi hâlde "60 dakikada döndü mü" sorusu henüz yanıtlanamaz).
        ÜST sınır: eski kayıtları sonsuza dek yeniden denemeyelim.

        **LIMIT, "ölçülmüş" filtresinden SONRA uygulanır** (D21-R3, bulgu 5):
        `postmortem` alanı JSON metninin İÇİNDEDİR, SQL onu göremez. SQL
        tarafında `limit(20)` uygulanırsa ve en yeni 20 kapanışın hepsi zaten
        ölçülmüşse fonksiyon BOŞ döner — arkadaki ölçülmemiş satırlar hiç
        görülmez ve sonsuza dek ölçülmez. Bu yüzden pencere `scan_limit`
        kadar taranır, ölçülmemişler süzülür ve `limit` en sonda uygulanır.
        `scan_limit` taramayı yine de sınırlar (12 saatlik pencerede
        binlerce satır olamaz ama savunma ucuzdur).
        """
        if min_age_minutes <= 0:
            return []
        newest = now - timedelta(minutes=min_age_minutes)
        oldest = now - timedelta(hours=max(1.0, float(max_age_hours)))
        wanted = max(1, int(limit))
        scan_limit = max(200, wanted * 10)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(
                    ScalpTradeModel.id,
                    ScalpTradeModel.symbol,
                    ScalpTradeModel.closed_at,
                    ScalpTradeModel.forensics,
                )
                .where(
                    ScalpTradeModel.status == "CLOSED",
                    ScalpTradeModel.forensics.isnot(None),
                    ScalpTradeModel.closed_at <= newest,
                    ScalpTradeModel.closed_at >= oldest,
                )
                .order_by(ScalpTradeModel.closed_at.desc())
                .limit(scan_limit)
            )
            trades = list(result.all())

        out: List[Dict[str, Any]] = []
        for trade_id, symbol, closed_at, raw_forensics in trades:
            document = self.parse_forensics(raw_forensics)
            if not document or document.get("postmortem") is not None:
                continue
            out.append({
                "id": int(trade_id),
                "symbol": symbol,
                "closed_at": closed_at,
                "entry": document.get("entry") or {},
                "exit": document.get("exit") or {},
            })
            if len(out) >= wanted:
                break
        return out

    async def record_postmortem(
        self, trade_id: int, postmortem: Dict[str, Any]
    ) -> bool:
        """Kapanıştan SONRA ölçülen alanı yaz; etiketlerini `verdict`e ekle.

        Look-ahead değildir: `postmortem` yalnız kapanış zamanından SONRAKİ
        mumlardan türetilir ve AYRI bir alanda saklanır (bkz.
        `forensics.postmortem_from_candles`).
        """
        async with AsyncSessionLocal() as session:
            trade = await session.get(ScalpTradeModel, int(trade_id))
            if trade is None:
                return False
            document = self.parse_forensics(trade.forensics) or {}
            document["postmortem"] = postmortem
            verdict = list(document.get("verdict") or [])
            for tag in postmortem.get("tags") or []:
                if tag not in verdict:
                    verdict.append(tag)
            document["verdict"] = verdict
            merged = self._dump_forensics(document)
            if merged is None:
                return False
            trade.forensics = merged
            await session.commit()
            return True
