"""
Swing/Trend Scalper — kendi sinyalini üreten çok-stratejili scalping motoru.

Modüller:
    types       — ortak sözleşme (Candle, ScalpSignal, StrategyContext, ...)
    indicators  — saf teknik indikatörler (EMA, ATR, swing, diverjans, ...)
    data        — kline çekme + önbellek (public endpoint)
    regime      — 4h rejim tespiti (UP/DOWN/RANGE)
    setups      — A/B/C strateji varyantları (saf, test edilebilir)
    scanner     — hacim bazlı sembol evreni
    executor    — güvenli pozisyon açma (koruma garantili)
    exits       — TP merdiveni + break-even + chandelier trailing
    tracker     — strateji etiketli işlem kaydı ve istatistik
    engine      — ana tarama/karar döngüsü
    backtest    — tarihsel simülasyon ve karşılaştırma raporu

Tasarım: docs/superpowers/specs/2026-08-07-scalper-design.md
"""
