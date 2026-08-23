"""
Scalper işlem kaydı veri modeli.

position.py'deki PositionModel canlı pozisyon durumunu tutar; ScalpTradeModel
ise KAPANMIŞ (ve açık) scalper işlemlerinin strateji etiketli, ROI/MAE/MFE
dahil geçmiş kaydını tutar — backtest karşılaştırması ve canlı performans
takibi için.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text

from src.core.database import Base


class ScalpTradeModel(Base):
    """Veritabanı scalper işlem kaydı modeli."""
    __tablename__ = "scalp_trades"

    id = Column(Integer, primary_key=True, index=True)

    # Strateji / sembol
    strategy = Column(String, index=True, nullable=False)   # "A" | "B" | "C"
    symbol = Column(String, index=True, nullable=False)
    direction = Column(String, nullable=False)               # "LONG" | "SHORT"

    # Fiyat / miktar
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    leverage = Column(Integer, nullable=False)
    margin_usdt = Column(Float, nullable=False)

    # Sonuç
    realized_pnl = Column(Float, default=0.0)
    roi_pct = Column(Float, default=0.0)
    exit_reason = Column(String, nullable=True)               # "SL"|"TP_LADDER"|"TRAIL"|"MANUAL"|"UNKNOWN"

    # Bağlam
    signal_reason = Column(String, nullable=False)
    mae_pct = Column(Float, default=0.0)                      # en kötü uç (maximum adverse excursion)
    mfe_pct = Column(Float, default=0.0)                      # en iyi uç (maximum favorable excursion)

    # Durum
    status = Column(String, default="OPEN", index=True)       # "OPEN" | "CLOSED"
    opened_at = Column(DateTime, default=datetime.utcnow, index=True)
    closed_at = Column(DateTime, nullable=True)

    # Koruma emirleri (algoId'ler)
    sl_algo_id = Column(String, nullable=True)
    tp1_algo_id = Column(String, nullable=True)
    tp2_algo_id = Column(String, nullable=True)
    # Üçüncü TP yalnız AlgoPro takipçi halkasında (D20, 3 parça çıkış)
    # kullanılır; scalper (TP1/TP2 + runner) bu sütunu NULL bırakır.
    tp3_algo_id = Column(String, nullable=True)

    # Giriş emri kimliği — kapanışta Binance income/userTrades doğrulaması
    # restart sonrası da çalışabilsin diye kalıcıdır.
    entry_order_id = Column(String, nullable=True)

    notes = Column(String, nullable=True)

    # İşlem adli kaydı (D21, gözlemlenebilirlik) — JSON metni:
    #   {"v":1,"entry":{...},"exit":{...},"verdict":[...],"postmortem":{...}}
    # Geriye uyumlu: eski satırlarda NULL kalır ve HİÇBİR karar yolu bu
    # sütunu okumaz (yalnız uçlar/pano/rapor okur). Şema tamamlaması
    # `src/core/database.py::_ensure_schema_migrations` içindedir.
    forensics = Column(Text, nullable=True)
