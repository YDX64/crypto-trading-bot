"""AlgoPro takipçi halkası (D20) — `BOT_MODE=follower`.

Bu paket, scalper motorundan (strateji C + TV sağlaması) TAMAMEN AYRI bir
çalışma modudur: scanner yoktur, strateji yoktur, gösterge tabanlı sinyal
yoktur. Giriş ve çıkış YALNIZ AlgoPro V1.6 alarmlarından gelir
(`POST /follower/event`).

Katmanlar (saf → IO):
  * ``types``    — ortak veri sözleşmesi (bağımlılıksız)
  * ``parser``   — AlgoPro alarm gövdesi → ``FollowerEvent`` (saf)
  * ``levels``   — SL/TP seviyeleri: mesaj > ATR kuralı (saf)
  * ``plan``     — marj/kaldıraç/miktar planı (saf)
  * ``brackets`` — /fapi/v1/leverageBracket önbelleği (IO)
  * ``executor`` — korumalı açılış (MARKET → SL → 3× TP) (IO)
  * ``exits``    — TP1→BE, çıkış/ters sinyal, kapanış defteri (IO)
  * ``engine``   — orkestrasyon, kapılar, kill switch, risk-olayı halt'ı (IO)

Scalper paketine (``src/strategies/scalper``) YAZILMAZ; yalnız kanıtlanmış
yollar (PositionManager, ImprovedBinanceClient, ScalpTracker, ExitManager'ın
kapanış defteri) yeniden KULLANILIR.
"""
