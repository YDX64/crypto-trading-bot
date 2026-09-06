"""
Scalper modülünün ortak tipleri — TÜM alt modüllerin uyduğu sözleşme.

Bu dosya kasıtlı olarak bağımlılıksızdır (yalnız stdlib): setups/regime saf
fonksiyonlar olarak test edilebilir kalır, IO katmanları (data/executor) bu
tipleri üretir/tüketir.

ROI ↔ fiyat çevrimi (tek gerçek formül, her yerde bu kullanılır):
    fiyat_degisim_yuzdesi = roi_yuzdesi / kaldirac
Örn. 20x'te +%20 ROI = fiyatın lehte %1 hareketi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, List, Optional


class Regime(str, Enum):
    """4h zaman diliminden türetilen piyasa rejimi."""
    UP = "UP"
    DOWN = "DOWN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"  # yetersiz veri — hiçbir strateji işlem açmaz


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class Candle:
    """Tek bir OHLCV mumu. Zamanlar epoch milisaniye."""
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int

    @classmethod
    def from_binance(cls, raw: list) -> "Candle":
        """Binance /fapi/v1/klines satırından (liste formatı) üret."""
        return cls(
            open_time=int(raw[0]),
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
            close_time=int(raw[6]),
        )


@dataclass(frozen=True)
class StrategyContext:
    """Bir sembol için değerlendirme anındaki tüm girdiler.

    setups.py saf kalır: yalnızca bu nesneyi okur, IO yapmaz.
    candles_* listeleri EN ESKİ → EN YENİ sıralıdır ve SON ELEMAN KAPANMIŞ
    en güncel mumdur (oluşmakta olan mum data katmanında atılır — repaint yok).
    """
    symbol: str
    regime: Regime
    candles_4h: List[Candle]      # sabit EMA50/EMA200 rejim bağlamı (>= 200 mum)
    candles_15m: List[Candle]     # yapı teyidi (>= 60 mum)
    candles_5m: List[Candle]      # giriş zamanlaması (>= 120 mum)
    current_price: float
    atr_5m: float                 # ATR(14, 5m) — mutlak fiyat birimi
    leverage: int


@dataclass(frozen=True)
class ScalpSignal:
    """Bir stratejinin ürettiği giriş önerisi.

    stop_price YAPISALDIR (swing ucunun ötesi). Executor mesafe sınırlarını
    [min_stop_pct, max_stop_pct] burada DEĞİL, risk kapısında denetler.
    """
    strategy: str                 # "A" | "B" | "C"
    symbol: str
    direction: Direction
    entry_price: float            # sinyal anındaki fiyat (bilgi amaçlı)
    stop_price: float             # yapısal stop — boyutlama bundan türer
    reason: str                   # insan-okur gerekçe (log + kayıt)
    regime: Regime
    atr_5m: float
    score: float = 0.0            # aynı turda birden çok sinyal olursa seçim
    risk_multiplier: float = 1.0  # C için 0.5 (counter-trend cezası)
    # Coin-bazlı dinamik kaldıraç (2026-08-13): apply_stop_policy volatiliteye
    # göre çözer; None = cfg.scalper_leverage kullanılır. SL her durumda
    # marjın fixed_stop_roi_pct'si olarak kalır — volatil coinde kaldıraç
    # düşer, stop FİYAT mesafesi ATR ile genişler, marj yüzdesi değişmez.
    leverage: Optional[int] = None


@dataclass
class ExitPlan:
    """Executor'ın bir pozisyon için kurduğu çıkış iskeleti."""
    tp1_price: float
    tp1_quantity: float
    tp2_price: float
    tp2_quantity: float
    runner_quantity: float        # sabit TP'siz, chandelier ile yönetilir
    initial_stop: float
    breakeven_price: float        # TP1 dolunca SL buraya çekilir
    chandelier_atr_mult: float
    # Komisyon oranları decimal bacak oranıdır (örn. 0.0005 = %0.05).
    # ``breakeven_price`` giriş + çıkış komisyonunu ve ek buffer'ı cebirsel
    # olarak tam karşılayacak seviyedir; yalnız ``entry*(1+buffer)`` değildir.
    entry_fee_rate: float = 0.0
    exit_fee_rate: float = 0.0
    fee_rate_source: str = "config_conservative"
    breakeven_cost_pct: float = 0.0
    # TP2 gerçekten dolduktan sonra runner stopunun sabit alt/üst sınırı.
    runner_floor_price: float = 0.0
    tp1_algo_id: Optional[str] = None
    tp2_algo_id: Optional[str] = None
    # --- Yalnız AlgoPro takipçi halkası (D20, 3 parça çıkış) doldurur ---
    # Scalper (TP1/TP2 + chandelier runner) bu alanları HİÇ kullanmaz;
    # varsayılanlar bugünkü davranışı birebir korur.
    tp3_price: float = 0.0
    tp3_quantity: float = 0.0
    tp3_algo_id: Optional[str] = None


class StrategyProtocol:
    """Her strateji bu arayüzü uygular (yapısal protokol; ABC şart değil).

    evaluate() SAF olmalı: IO yok, saat yok, rastgelelik yok.
    Sinyal yoksa None döner.
    """

    name: str = "?"

    def evaluate(self, ctx: StrategyContext) -> Optional[ScalpSignal]:
        raise NotImplementedError


def roi_to_price_delta_pct(roi_pct: float, leverage: int) -> float:
    """ROI yüzdesini fiyat değişim yüzdesine çevir. 20x'te %20 ROI → %1."""
    if leverage <= 0:
        raise ValueError(f"Geçersiz kaldıraç: {leverage}")
    return roi_pct / leverage


def price_at_roi(entry: float, roi_pct: float, leverage: int,
                 direction: Direction) -> float:
    """Girişten itibaren hedef ROI'ye denk gelen fiyat."""
    delta = entry * roi_to_price_delta_pct(roi_pct, leverage) / 100.0
    return entry + delta if direction == Direction.LONG else entry - delta


# D30 — bayat-kâr kapanışı etiketi. Burada (bağımlılıksız modülde) durur ki
# canlı defter (`exits.py`) ve backtest harness'i (`backtest.py`) AYNI dizeyi
# kullansın; harness `exits`i import etmez (ağ/istemci bağımlılığı taşımaz).
EXIT_REASON_STALE_TP = "STALE_TP"


def position_roi_pct(
    entry: float, price: float, leverage: int, direction: Direction
) -> float:
    """Açık pozisyonun `price`taki kaldıraçlı ROI'si (%, marj üzerinden).

    `exits._update_mae_mfe` ile AYNI tanım (fiyat farkı % × kaldıraç; SHORT'ta
    işaret ters). Komisyon DÜŞÜLMEZ — eşikler bunu bilerek konur (bkz.
    `scalper_stale_tp_min_roi_pct`). Geçersiz giriş fiyatında 0.0 döner.
    """
    if entry <= 0:
        return 0.0
    lev = leverage if leverage and leverage > 0 else 1
    delta_pct = (price - entry) / entry * 100.0
    if direction == Direction.SHORT:
        delta_pct = -delta_pct
    return delta_pct * lev


def stale_tp_should_close(cfg: Any, *, age_ms: float, roi_pct: float) -> bool:
    """D30 — bayat-kâr kapanışı kararı (SAF; canlı motor ve harness ortak).

    True ⇔ özellik açık (`scalper_stale_tp_hours` > 0) VE pozisyon yaşı bu
    saati doldurmuş VE anlık ROI ≥ `scalper_stale_tp_min_roi_pct`.
    Çağıran taraf "TP1 görülmemiş" (trailing_active False) ön koşulunu kendi
    uygular — reaper ile aynı muafiyet: BE korumalı koşucuya dokunulmaz.
    """
    hours = float(getattr(cfg, "scalper_stale_tp_hours", 0.0) or 0.0)
    if hours <= 0.0:
        return False
    if age_ms < hours * 3_600_000.0:
        return False
    min_roi = float(getattr(cfg, "scalper_stale_tp_min_roi_pct", 0.0) or 0.0)
    return roi_pct >= min_roi


def resolve_trail_mult(cfg: Any, peak_roi_pct: float) -> float:
    """Kademeli gevşeyen iz: TEPE ROI (high-water mark) büyüdükçe chandelier
    çarpanı büyür — küçük kârda sıkı koru, dev trendde geniş bırak.

    Anlık ROI değil tepe ROI kullanılır: geri çekilme sırasında iz yeniden
    sıkılaşsaydı koşucu tam korumak istediğimiz anda ölürdü; kademe tek
    yönlüdür. scalper_trail_relax_roi1_pct<=0 özelliği kapatır (temel çarpan).
    Canlı (exits._update_trailing) ve backtest (_update_trailing) İKİSİ DE
    bunu çağırır — parite bozulmamalı.
    """
    base = float(getattr(cfg, "scalper_chandelier_atr_mult", 2.5) or 2.5)
    roi1 = float(getattr(cfg, "scalper_trail_relax_roi1_pct", 0.0) or 0.0)
    if roi1 <= 0:
        return base
    mult1 = float(getattr(cfg, "scalper_trail_relax_mult1", base) or base)
    roi2 = float(getattr(cfg, "scalper_trail_relax_roi2_pct", 0.0) or 0.0)
    mult2 = float(getattr(cfg, "scalper_trail_relax_mult2", mult1) or mult1)
    if roi2 > roi1 and peak_roi_pct >= roi2:
        return mult2
    if peak_roi_pct >= roi1:
        return mult1
    return base


def fill_anchored_stop_price(
    signal_entry: float,
    signal_stop: float,
    fill_price: float,
    direction: Direction,
    max_distance_pct: float,
) -> float:
    """Preserve signal stop distance at the actual fill, capped like live.

    Shared by execution and the historical harness. Sizing still uses the
    original signal distance; reanchoring happens only after the fill.
    """
    if signal_entry <= 0 or signal_stop <= 0 or fill_price <= 0:
        return signal_stop
    drift = fill_price - signal_entry
    if drift == 0.0:
        return signal_stop
    adjusted = signal_stop + drift
    if adjusted <= 0:
        return signal_stop
    if direction == Direction.LONG and adjusted >= fill_price:
        return signal_stop
    if direction == Direction.SHORT and adjusted <= fill_price:
        return signal_stop
    if max_distance_pct > 0:
        distance_pct = abs(fill_price - adjusted) / fill_price * 100.0
        if distance_pct > max_distance_pct:
            return (
                fill_price * (1.0 - max_distance_pct / 100.0)
                if direction == Direction.LONG
                else fill_price * (1.0 + max_distance_pct / 100.0)
            )
    return adjusted


def fee_aware_breakeven_price(
    entry: float,
    direction: Direction,
    entry_fee_rate: float,
    exit_fee_rate: float,
    buffer_pct: float,
) -> float:
    """Giriş/çıkış maliyetlerini karşılayan cebirsel tam stop seviyesini döndür.

    Oranlar bacak nominali üzerinden hesaplanır. ``buffer_pct`` yüzde fiyat
    birimindedir ve kayma/funding/net-kâr kilidi için giriş nominaline eklenen
    muhafazakâr maliyettir.

    LONG için net sıfır denklemi::

        X - E - E*r_entry - X*r_exit - E*buffer = 0
        X = E*(1 + r_entry + buffer) / (1 - r_exit)

    SHORT için::

        E - X - E*r_entry - X*r_exit - E*buffer = 0
        X = E*(1 - r_entry - buffer) / (1 + r_exit)

    ``Decimal(str(...))`` kullanımı binary-float ara yuvarlamasını formülün
    içine taşımadan, sonucu borsa fiyat yuvarlamasına bırakır.
    """
    try:
        e = Decimal(str(entry))
        entry_rate = Decimal(str(entry_fee_rate))
        exit_rate = Decimal(str(exit_fee_rate))
        buffer_rate = Decimal(str(buffer_pct)) / Decimal("100")
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Geçersiz break-even girdisi: {exc}") from exc

    if not e.is_finite() or e <= 0:
        raise ValueError(f"Geçersiz giriş fiyatı: {entry}")
    for label, rate in (("entry_fee_rate", entry_rate), ("exit_fee_rate", exit_rate)):
        if not rate.is_finite() or rate < 0 or rate >= 1:
            raise ValueError(f"Geçersiz {label}: {rate}")
    if not buffer_rate.is_finite() or buffer_rate < 0 or buffer_rate >= 1:
        raise ValueError(f"Geçersiz buffer_pct: {buffer_pct}")

    if direction == Direction.LONG:
        result = e * (Decimal("1") + entry_rate + buffer_rate) / (
            Decimal("1") - exit_rate
        )
    elif direction == Direction.SHORT:
        numerator = Decimal("1") - entry_rate - buffer_rate
        if numerator <= 0:
            raise ValueError("SHORT break-even maliyeti giriş fiyatını aşıyor")
        result = e * numerator / (Decimal("1") + exit_rate)
    else:
        raise ValueError(f"Geçersiz yön: {direction}")

    return float(result)


#: Gömülü AlgoPro takipçisinin defter etiketi (`scalp_trades.strategy`, D20b).
#: Scalper ve takipçi AYNI tabloyu paylaşır; bu sabit iki defteri ayırmanın
#: TEK gerçek kaynağıdır (`follower/executor.FOLLOWER_STRATEGY` buna eşittir).
FOLLOWER_LEDGER_STRATEGY = "AP"
