"""Pozisyon planı — marj, kaldıraç, miktar (SAF: IO yok).

KULLANICI KARARI (2026-08-23, önceki "işlem başına risk %2" talimatının
YERİNE geçer):

* İşlem başına **MARJ = sermayenin %``FOLLOWER_MARGIN_PCT``'i** (varsayılan
  %10; sermaye = hesabın USDT bakiyesi).
* **Kaldıraç VOLATİLİTEYE GÖRE DİNAMİK**::

      lev = clamp(round(FOLLOWER_SL_ROI_TARGET / sl_pct),
                  FOLLOWER_LEV_MIN, FOLLOWER_LEV_MAX)

  ``sl_pct`` = AlgoPro SL mesafesi / giriş × 100. Böylece stop DAİMA marjın
  ~%``FOLLOWER_SL_ROI_TARGET``'i (varsayılan %30) kadar zarardır: sakin
  sembolde (dar stop) kaldıraç yükselir, oynak sembolde düşer.
* **Zorunlu güvenlik kapıları** (hepsi kaldıracı yalnız DÜŞÜRÜR):
  (a) borsanın sembol/notional bazlı kaldıraç dilimi (`/fapi/v1/leverageBracket`)
      AŞILAMAZ — gerçek değer okunur ve önbelleklenir (``brackets.py``);
  (b) likidasyon koruması: ``lev × sl_pct ≤ FOLLOWER_LEV_LIQ_GUARD_PCT`` (50)
      ve bakım marjı payı: ``1/lev − mmr > FOLLOWER_MMR_SAFETY_MULT × sl_pct/100``;
  (c) nominal = marj × lev, qty = nominal / giriş → borsa filtreleriyle
      (minQty/minNotional/stepSize) doğrulanır (executor'da, IO);
  (d) TP'ler AYNI SL mesafesinin RR katlarıdır → ``TP1 ROI = RR1 × SL ROI``.

Örnekler (tests/test_follower_plan.py bunları BİREBİR doğrular):
    BTC 1m, sl_pct %0.08 → hedef 375 → tavan 100 → SL = marjın %8'i, TP1 %4
    DOGE,   sl_pct %0.30 → hedef 100 →      100 → SL %30, TP1 %15
            sl_pct %0.60 → hedef  50 →       50 → SL %30, TP1 %15
(Gerçek borsa dilimi/mmr bu değerleri AŞAĞI çekebilir — güvenlik kazanır.)
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_DOWN
from typing import Any, List, Optional, Sequence, Tuple

from src.strategies.follower.types import (
    FollowerLevels,
    FollowerPlan,
    FollowerRejected,
    LeverageBracket,
)
from src.strategies.scalper.types import Direction


def _cfg_float(cfg: Any, name: str, default: float) -> float:
    try:
        value = float(getattr(cfg, name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _cfg_int(cfg: Any, name: str, default: int) -> int:
    try:
        return int(getattr(cfg, name, default))
    except (TypeError, ValueError):
        return default


def raw_target_leverage(sl_pct: float, cfg: Any) -> int:
    """``round(SL_ROI_TARGET / sl_pct)`` — KIRPILMAMIŞ oran (telemetri)."""
    if not math.isfinite(sl_pct) or sl_pct <= 0:
        raise FollowerRejected(
            f"Geçersiz stop yüzdesi ({sl_pct}) — kaldıraç çözülemez",
            code="invalid_sl_pct",
        )
    return int(round(_cfg_float(cfg, "follower_sl_roi_target", 30.0) / sl_pct))


def target_leverage(sl_pct: float, cfg: Any) -> int:
    """``clamp(round(SL_ROI_TARGET / sl_pct), LEV_MIN, LEV_MAX)``.

    Borsa dilimi ve likidasyon kapılarından ÖNCEKİ hedef — kullanıcının
    verdiği örnekler bu fonksiyonun çıktısıdır.
    """
    lev_min = max(1, _cfg_int(cfg, "follower_lev_min", 3))
    lev_max = max(lev_min, _cfg_int(cfg, "follower_lev_max", 100))
    raw = raw_target_leverage(sl_pct, cfg)
    return int(min(max(raw, lev_min), lev_max))


def select_bracket(
    brackets: Sequence[LeverageBracket], notional: float
) -> Optional[LeverageBracket]:
    """Notional'a düşen kaldıraç dilimini seç (yoksa None)."""
    if not brackets:
        return None
    ordered = sorted(brackets, key=lambda b: b.notional_floor)
    for bracket in ordered:
        if bracket.notional_floor <= notional < bracket.notional_cap:
            return bracket
    return ordered[-1]


def _guards_ok(
    *, leverage: int, sl_pct: float, mmr: float, cfg: Any
) -> Tuple[bool, str]:
    """(b) likidasyon + bakım marjı kapıları. Dönüş: (geçti mi, gerekçe)."""
    liq_guard = _cfg_float(cfg, "follower_lev_liq_guard_pct", 50.0)
    if leverage * sl_pct > liq_guard:
        return False, "liq_guard"
    safety_mult = _cfg_float(cfg, "follower_mmr_safety_mult", 2.0)
    if (1.0 / leverage - mmr) <= safety_mult * sl_pct / 100.0:
        return False, "mmr_guard"
    return True, ""


def resolve_leverage(
    *,
    sl_pct: float,
    margin_usdt: float,
    brackets: Sequence[LeverageBracket],
    cfg: Any,
) -> Tuple[int, int, str, float]:
    """Nihai kaldıracı çöz.

    Dönüş: ``(leverage, target, cap_reason, mmr)``. ``cap_reason`` hangi
    kapının bağladığını söyler: ``target`` | ``exchange_bracket`` |
    ``liq_guard`` | ``mmr_guard``.

    Borsa dilimi notional'a bağlıdır, notional da kaldıraca — kaldıraç yalnız
    AŞAĞI gittiği için birkaç turda sabit noktaya yakınsar.
    """
    if not brackets:
        # (a) "gerçek değeri oku" — okunamadıysa 100x'e devam etmek kabul
        # edilemez. Fail-closed: giriş yok (bkz. docs/DECISIONS.md D20).
        raise FollowerRejected(
            "Borsa kaldıraç dilimi (leverageBracket) okunamadı — giriş yapılmadı",
            code="no_bracket",
        )

    lev_min = max(1, _cfg_int(cfg, "follower_lev_min", 3))
    lev_max = max(lev_min, _cfg_int(cfg, "follower_lev_max", 100))
    raw = raw_target_leverage(sl_pct, cfg)
    target = target_leverage(sl_pct, cfg)
    leverage = target
    # Hangi kapının bağladığı deftere yazılır: "lev_max" = oran tavana
    # dayandı (ör. BTC'de round(30/0.08)=375 → 100).
    reason = "lev_max" if raw > lev_max else ("lev_min" if raw < lev_min else "target")

    for _ in range(5):
        bracket = select_bracket(brackets, margin_usdt * leverage)
        assert bracket is not None  # brackets boş değil (yukarıda kontrol)
        capped = min(leverage, max(1, int(bracket.max_leverage)))
        if capped == leverage:
            break
        leverage = capped
        reason = "exchange_bracket"

    bracket = select_bracket(brackets, margin_usdt * leverage)
    mmr = float(bracket.maint_margin_ratio) if bracket else 0.0
    ok, failed = _guards_ok(leverage=leverage, sl_pct=sl_pct, mmr=mmr, cfg=cfg)
    while not ok and leverage > lev_min:
        leverage -= 1
        bracket = select_bracket(brackets, margin_usdt * leverage)
        # Dilim değişebilir: daha düşük notional daha düşük mmr'ye düşer.
        leverage = min(leverage, max(1, int(bracket.max_leverage))) if bracket else leverage
        mmr = float(bracket.maint_margin_ratio) if bracket else 0.0
        reason = failed
        ok, failed = _guards_ok(leverage=leverage, sl_pct=sl_pct, mmr=mmr, cfg=cfg)

    if not ok:
        raise FollowerRejected(
            f"Likidasyon/bakım marjı kapısı geçilemedi (lev={leverage}, "
            f"sl_pct=%{sl_pct:.4f}, mmr={mmr:g}, kapı={failed}) — giriş yapılmadı",
            code=failed or "liq_guard",
        )
    return int(leverage), int(target), reason, float(mmr)


def roundtrip_fee_roi_pct(leverage: int, cfg: Any) -> float:
    """Gidiş-dönüş komisyonun MARJA oranı (%), config oranlarıyla (IO yok).

    Takipçide iki bacak da taker'dır (giriş MARKET, çıkış MARKET/koşullu emir),
    bu yüzden muhafazakâr oran ``max(taker, maker)``tır — scalper ile aynı ilke.
    ``ROI = lev × 2 × oran × 100``: 100x'te %0.05 taker → marjın %10'u.

    NEDEN ÖNEMLİ: ``sl_roi_pct = lev × sl_pct`` ve ``tp1_roi = RR1 × sl_roi``.
    Kaldıraç LEV_MAX'e KIRPILDIĞINDA (raw hedef > 100, yani sl_pct < ~%0.30)
    ``tp1_roi`` bu eşiğin ALTINA düşer: BTC örneğinde (sl_pct %0.08) TP1 = marjın
    %4'ü, komisyon %10'u → üç TP de dolsa işlem NET NEGATİFTİR. Bu bir
    boyutlama sorunu değil, ölçülebilir bir gerçektir; bkz. docs/DECISIONS.md
    D20 "ücret eşiği" ve varsayılan KAPALI ``FOLLOWER_MIN_TP1_FEE_RATIO``.
    """
    rate = max(
        _cfg_float(cfg, "scalper_taker_fee_pct", 0.05),
        _cfg_float(cfg, "scalper_maker_fee_pct", 0.02),
    ) / 100.0
    if rate <= 0:
        return 0.0
    return float(leverage) * 2.0 * rate * 100.0


def split_three_quantities(total: float, step: float) -> Tuple[float, float, float]:
    """Miktarı 1/3'er üç parçaya böl; YUVARLAMA ARTIĞI SON PARÇAYA gider.

    İlk iki parça stepSize'a AŞAĞI yuvarlanır, üçüncü parça kalandır — böylece
    üç parçanın toplamı canlı miktarı ASLA aşmaz (reduce-only -2022 riski yok)
    ve hiçbir miktar "buharlaşmaz".
    """
    if total <= 0:
        return (0.0, 0.0, 0.0)
    if step and step > 0:
        step_dec = Decimal(str(step))
        third = (Decimal(str(total)) / Decimal(3) / step_dec).to_integral_value(
            rounding=ROUND_DOWN
        ) * step_dec
        part = float(third)
    else:
        part = total / 3.0
    if part <= 0:
        return (0.0, 0.0, float(total))
    remainder = float(Decimal(str(total)) - Decimal(str(part)) * 2)
    if remainder <= 0:
        return (0.0, 0.0, float(total))
    return (part, part, remainder)


def build_plan(
    *,
    symbol: str,
    direction: Direction,
    levels: FollowerLevels,
    equity_usdt: float,
    brackets: Sequence[LeverageBracket],
    cfg: Any,
    step_size: float = 0.0,
) -> FollowerPlan:
    """Tam pozisyon planını kur (miktar HAM — borsa yuvarlaması executor'da)."""
    if not math.isfinite(equity_usdt) or equity_usdt <= 0:
        raise FollowerRejected(
            f"Sermaye bilinmiyor veya sıfır ({equity_usdt}) — giriş yapılmadı",
            code="no_equity",
        )

    margin_pct = _cfg_float(cfg, "follower_margin_pct", 10.0)
    if margin_pct <= 0:
        raise FollowerRejected(
            "FOLLOWER_MARGIN_PCT <= 0 — giriş yapılmadı", code="margin_pct"
        )
    margin_usdt = equity_usdt * margin_pct / 100.0

    leverage, target, cap_reason, mmr = resolve_leverage(
        sl_pct=levels.sl_pct,
        margin_usdt=margin_usdt,
        brackets=brackets,
        cfg=cfg,
    )

    notional = margin_usdt * leverage
    quantity = notional / levels.entry
    if quantity <= 0:
        raise FollowerRejected(
            "Hesaplanan miktar sıfır — giriş yapılmadı", code="zero_qty"
        )

    sl_roi_pct = leverage * levels.sl_pct
    rr = (
        _cfg_float(cfg, "follower_tp_rr1", 0.5),
        _cfg_float(cfg, "follower_tp_rr2", 1.0),
        _cfg_float(cfg, "follower_tp_rr3", 1.5),
    )
    tp_roi = (rr[0] * sl_roi_pct, rr[1] * sl_roi_pct, rr[2] * sl_roi_pct)

    fee_roi = roundtrip_fee_roi_pct(leverage, cfg)
    # VARSAYILAN KAPALI (0.0): kullanıcı kararı (2026-08-23) "boyut/TP1/stop
    # ile kayıp küçültme YASAK — çözüm sinyal kalitesi". Bu kapı boyut
    # DEĞİŞTİRMEZ, yalnız komisyonu ödeyemeyeceği ölçülmüş bir işleme HİÇ
    # girmemeyi sağlar. Açmak ayrı bir kullanıcı kararıdır.
    min_ratio = _cfg_float(cfg, "follower_min_tp1_fee_ratio", 0.0)
    if min_ratio > 0 and fee_roi > 0 and tp_roi[0] < min_ratio * fee_roi:
        raise FollowerRejected(
            f"TP1 ROI (%{tp_roi[0]:.2f}) gidiş-dönüş komisyonun "
            f"({min_ratio:g}×%{fee_roi:.2f}) altında — giriş yapılmadı",
            code="fee_threshold",
        )

    return FollowerPlan(
        symbol=symbol,
        direction=direction,
        levels=levels,
        leverage=leverage,
        leverage_target=target,
        leverage_cap_reason=cap_reason,
        sl_pct=levels.sl_pct,
        sl_roi_pct=sl_roi_pct,
        tp_roi_pct=tp_roi,
        margin_usdt=margin_usdt,
        notional_usdt=notional,
        quantity=quantity,
        tp_quantities=split_three_quantities(quantity, step_size),
        equity_usdt=float(equity_usdt),
        maint_margin_ratio=mmr,
        roundtrip_fee_roi_pct=fee_roi,
    )


def with_exchange_quantity(
    plan: FollowerPlan, quantity: float, step_size: float
) -> FollowerPlan:
    """Borsa yuvarlaması sonrası miktarı ve 3 parçayı yeniden kur."""
    parts: Tuple[float, float, float] = split_three_quantities(quantity, step_size)
    notional = float(quantity) * plan.levels.entry
    return FollowerPlan(
        symbol=plan.symbol,
        direction=plan.direction,
        levels=plan.levels,
        leverage=plan.leverage,
        leverage_target=plan.leverage_target,
        leverage_cap_reason=plan.leverage_cap_reason,
        sl_pct=plan.sl_pct,
        sl_roi_pct=plan.sl_roi_pct,
        tp_roi_pct=plan.tp_roi_pct,
        # GERÇEK marj: `quantize_quantity` miktarı AŞAĞI yuvarlar, bu yüzden
        # planlanan marj daima fiilî marjdan büyüktür. Defter ve
        # /follower/status gerçeği göstermeli (`notional = margin × lev`
        # özdeşliği korunur), aksi halde sonraki PF/risk analizi bozulur.
        margin_usdt=notional / max(1, plan.leverage),
        notional_usdt=notional,
        quantity=float(quantity),
        tp_quantities=parts,
        equity_usdt=plan.equity_usdt,
        maint_margin_ratio=plan.maint_margin_ratio,
        roundtrip_fee_roi_pct=plan.roundtrip_fee_roi_pct,
    )


def parse_brackets(payload: Any) -> List[LeverageBracket]:
    """`/fapi/v1/leverageBracket` yanıtını ``LeverageBracket`` listesine çevir.

    Binance sembol sorgusunda liste ([{symbol, brackets:[...]}]) döner; bazı
    testnet sürümleri tek nesne döndürebilir — ikisi de kabul edilir.
    Ayrıştırılamayan satır SESSİZCE atlanmaz, listeye hiç girmez; sonuç boşsa
    çağıran fail-closed davranır (bkz. ``resolve_leverage``).
    """
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []

    out: List[LeverageBracket] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        rows = entry.get("brackets")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                max_lev = int(row["initialLeverage"])
                mmr = float(row["maintMarginRatio"])
                floor_value = float(row.get("notionalFloor", 0) or 0)
                cap_raw = row.get("notionalCap")
                cap = float(cap_raw) if cap_raw is not None else float("inf")
            except (KeyError, TypeError, ValueError):
                continue
            if max_lev <= 0 or not math.isfinite(mmr) or mmr < 0:
                continue
            out.append(
                LeverageBracket(
                    max_leverage=max_lev,
                    maint_margin_ratio=mmr,
                    notional_floor=floor_value,
                    notional_cap=cap if math.isfinite(cap) else float("inf"),
                )
            )
    return out
