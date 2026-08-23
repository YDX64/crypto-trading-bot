"""
Konfigürasyon yönetimi modülü.
Tüm environment variables ve uygulama ayarları burada yönetilir.
"""

import ipaddress
import sys
import warnings
from typing import Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Bilinen Binance testnet host'ları. Sadece "testnet" alt string'ine değil,
# gerçek host isimlerine bakarak kontrol ediyoruz (daha sağlam).
TESTNET_HOSTS = (
    "testnet.binancefuture.com",  # Binance Futures testnet (klasik adres)
    "demo-fapi.binance.com",      # AYNI hesabın yeni REST adresi ("Demo Trading")
    "demo.binance.com",           # Demo Trading web arayüzü ile aynı alan adı
    "testnet.binance.vision",     # Binance Spot testnet
)
# NOT: testnet.binancefuture.com ile demo-fapi.binance.com AYNI hesabı gösterir
# (2026-08-06'da aynı API anahtarıyla doğrulandı: iki adreste de birebir aynı
# bakiye ve pozisyonlar). Binance testnet'i "Demo Trading" olarak yeniden
# markaladı; demo.binance.com bunun web arayüzüdür.


class Settings(BaseSettings):
    """Uygulama ayarları"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # Binance Configuration
    # GÜVENLİK: repr=False — pydantic'in varsayılan repr'i tüm alanları basar ve
    # herhangi bir traceback / log satırı / CI çıktısı anahtarları sızdırır.
    binance_api_key: str = Field(repr=False)
    binance_api_secret: str = Field(repr=False)
    # GÜVENLİK: Varsayılan olarak TESTNET kullanılır. Gerçek parayla (mainnet)
    # işlem yapmak BİLİNÇLİ bir tercih olmalı — bkz. allow_mainnet ve
    # _validate_binance_environment.
    binance_base_url: str = "https://testnet.binancefuture.com"
    # None ise user-data stream REST hostundan türetilir. Demo/testnet
    # geçişlerinde Binance farklı WS hostu yayımlayabildiği için env ile açık
    # override desteklenir (BINANCE_WS_BASE_URL).
    binance_ws_base_url: Optional[str] = None
    # 2026-08-15: Boş değilse Binance REST soketleri bu yerel IP'ye bind edilir
    # (BINANCE_BIND_IP). Neden: sunucudaki NordVPN policy routing'i tüm çıkışı
    # tünele sokuyor; X-MBX-USED-WEIGHT-1M IP bazlı sayıldığından paylaşılan
    # tünel çıkış IP'sinde YABANCI trafik bizim 2400/dk bütçemizi yiyip 418
    # ban'ı yedirtiyordu (bot 10 istek/dk atarken başlık 5864 gösterdi).
    # `from <ana-IP> lookup 100` kuralı sayesinde bind edilen soket tüneli
    # atlar ve temiz, yalnız bize ait weight bütçesinden harcar.
    binance_bind_ip: str = ""
    # Gerçek parayla (mainnet) işlem yapmak için açık onay. Varsayılan False.
    # binance_base_url mainnet'i gösteriyorsa ve bu False ise Settings() hata verir.
    allow_mainnet: bool = False

    # Telegram Configuration
    telegram_bot_token: str = Field(repr=False)
    telegram_chat_id: str
    telegram_vip_channel_id: Optional[str] = None
    
    # OpenAI Configuration
    openai_api_key: str = Field(repr=False)
    openai_model: str = "gpt-4o"
    
    # Gemini (Fallback)
    gemini_api_key: str = Field(repr=False)
    gemini_model: str = "gemini-2.0-flash-exp"

    # DeepSeek Configuration
    deepseek_api_key: str = Field(repr=False)
    deepseek_model: str = "deepseek-reasoner"
    deepseek_base_url: str = "https://api.deepseek.com"
    
    # Trading Parameters
    account_balance: float = 10000.0
    risk_percentage: float = 10.0  # Kasanın %10'u
    first_tp_percentage: float = 25.0  # İlk TP her zaman %25
    trailing_stop_percentage: float = 1.5
    trailing_profit_percentage: float = 0.5
    check_interval_seconds: int = 30
    margin_type: str = "ISOLATED"
    max_leverage: int = 20
    
    # Database
    database_url: str = "sqlite:///./tradingbot.db"
    
    # Redis (Optional)
    redis_url: str = "redis://localhost:6379/0"
    use_redis: bool = False
    
    # Application Settings
    app_env: str = "development"
    log_level: str = "INFO"
    debug: bool = True
    
    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
    # Security
    jwt_secret: str = Field(repr=False)
    api_key: Optional[str] = Field(default=None, repr=False)
    
    # Monitoring
    enable_metrics: bool = True
    metrics_port: int = 9090
    
    # Queue & Rate Limiting
    max_positions: int = 5
    openai_rate_limit_seconds: float = 3.0
    binance_rate_limit_seconds: float = 0.5
    signal_queue_delay_seconds: float = 2.0

    # Waiting Mode Configuration
    waiting_mode_enabled: bool = False
    waiting_mode_max_positions: int = 3
    waiting_mode_max_hours: int = 24
    waiting_mode_check_interval_minutes: int = 5

    # Technical Indicators
    waiting_mode_rsi_period: int = 14
    waiting_mode_rsi_oversold: float = 30.0
    waiting_mode_rsi_overbought: float = 70.0
    waiting_mode_macd_fast: int = 12
    waiting_mode_macd_slow: int = 26
    waiting_mode_macd_signal: int = 9
    waiting_mode_bb_period: int = 20
    waiting_mode_bb_std_dev: float = 2.0

    # Entry Conditions
    waiting_mode_min_conditions: int = 2
    waiting_mode_price_improvement: float = 0.5

    # Scalper
    scalper_enabled: bool = True
    scalper_strategies: str = "A,B,C,D"        # virgülle: hangi varyantlar aktif
    scalper_top_n: int = 12
    scalper_scan_interval_seconds: int = 30
    # Pozisyon/çıkış ve bekleyen maker dolumları, uzun sembol
    # taramasından bağımsız bu sıklıkta izlenir. 2sn, dolan bir
    # girişin korumasız kalma penceresini tarama süresine bağlı olmaktan
    # çıkarır; ortam değişkeni: SCALPER_SAFETY_INTERVAL_SECONDS.
    scalper_safety_interval_seconds: float = 2.0
    # Pozisyon bu saatten yaşlıysa reaper reduce-only MARKET ile kapatır
    # (0 = kapalı). Scalp ufkunu aşan pozisyon slot+sermaye israfıdır.
    scalper_max_hold_hours: float = 0.0
    # Rejime ters C girislerini engelle (LONG yasak DOWN'da, SHORT yasak
    # UP'ta; RANGE/UNKNOWN serbest).
    scalper_regime_filter: bool = True
    # TV dış sinyallerine sembol allowlist'i (virgüllü; boş = tümü serbest).
    # 2026-08-21 kanıtı: LuxAlgo OSC Backtester 8 sembolde tarandı (TV MCP) —
    # BTC/ETH/XRP PF 1.9-2.5, DOGE/ADA/LTC PF<1; canlı defter de aynı yönde
    # (TV kayıpları SOL −30.65 ve DOGE'dan geldi). luxosc sinyali her coinde
    # aynı değil; kanıtı olan sembollere sınırla.
    scalper_tv_symbol_allowlist: str = ""
    # TV dış sinyalleri de rejim kapısına tabi mi? 2026-08-18: True yapıldı —
    # 2 kaynaklı sağlamaya rağmen TV 2 günde −41 USDT (7 işlem, iki gün de
    # negatif); en kötüsü rejime ters 8 saatlik SHORT (SOL #92, −30.65).
    # False = eski davranış (TV muaf).
    scalper_tv_regime_filter: bool = True
    # --- Lider piyasa kapısı ("ters-gün kapısı", D15 / spec §C) -----------
    # Rejim kapısından (yukarıdaki scalper_regime_filter) FARKLI: o, sembolün
    # KENDİ EMA50/200 trendine bakar; bu kapı yalnız LİDER sembole bakar ve
    # kararı tüm evrene uygular. Varsayılan KAPALI — açmadan önce 3 rejim
    # penceresinde backtest şart (CLAUDE.md yasak #1).
    scalper_market_gate: bool = False
    scalper_market_gate_symbol: str = "BTCUSDT"
    # Gün-içi alt-kapısı (%; 0 = kapalı): lider gün açılışının ≥ bu kadar
    # ALTINDAYSA yeni LONG, ≥ bu kadar ÜSTÜNDEYSE yeni SHORT açılmaz.
    # 1.3: İKİ BAĞIMSIZ ölçümün uyuştuğu eşik — E7 (motor-içi harness kapısı,
    # 3 pencere) V1c'yi V1'e (%1.0) üstün buldu; E8 (canlı defter post-hoc)
    # aynı eşiği bağımsız önerdi. Bkz. docs/DECISIONS.md D15 "Varsayılanlar".
    scalper_market_gate_day_pct: float = 1.3
    # Uzama alt-kapısı (%; 0 = kapalı) ve gün sayısı: lider son N tamamlanmış
    # günde ≥ +%Y koştuysa LONG, ≤ −%Y düştüyse SHORT açılmaz.
    # 0 (KAPALI): iki bağımsız ölçüm bu alt-kapıyı ÇÜRÜTTÜ — E7: yalnız ayı
    # penceresinde, TEK lider olayında tetikleniyor, gün-içi kapısının üstüne
    # katkısı yok (V3 ≡ V1); E8: canlı defterde net NEGATİF (−152.7) ve
    # hipotezin İŞARETİ ters. Motor açılışta ayrıca uyarır (>0 bırakılırsa).
    scalper_market_gate_run_pct: float = 0.0
    scalper_market_gate_run_days: int = 3
    # Lider verisi alınamazsa NEGATİF ÖNBELLEK süresi (sn): bu süre dolana
    # kadar yeniden denenmez. Yanlış bir lider sembolü ya da ağ kesintisinde
    # her sinyalin 3 seri × 3 deneme boşa REST isteği açmasını (ve KlineFetcher
    # kilidini saniyelerce tutmasını) engeller. 0 = negatif önbellek kapalı.
    scalper_market_gate_retry_sec: float = 60.0
    scalper_leverage: int = 20
    scalper_risk_percentage: float = 2.0       # işlem başına bakiye riski (C yarısını kullanır)
    scalper_max_positions: int = 3
    scalper_tp1_roi: float = 20.0              # %ROI — 20x'te %1 fiyat
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_roi: float = 50.0
    scalper_tp2_fraction: float = 0.30
    scalper_min_stop_pct: float = 0.15         # fiyat mesafesi sınırları
    scalper_max_stop_pct: float = 3.0
    scalper_breakeven_buffer_pct: float = 0.05
    scalper_chandelier_atr_mult: float = 2.5
    scalper_chandelier_atr_period: int = 14
    # --- Kademeli gevşeyen iz (2026-08-21, kullanıcı kararı: koşucuyu yalnız
    # SL durdurur, %1000'e bile binebilmeli). TEPE ROI şu eşiği geçince
    # chandelier çarpanı bir üst kademeye çıkar ve (high-water mark) bir daha
    # sıkılaşmaz. roi1<=0 = özellik kapalı. Bkz. types.resolve_trail_mult.
    scalper_trail_relax_roi1_pct: float = 0.0
    scalper_trail_relax_mult1: float = 5.0
    scalper_trail_relax_roi2_pct: float = 150.0
    scalper_trail_relax_mult2: float = 7.0
    scalper_daily_loss_limit_pct: float = 15.0 # 0 = kesici kapalı
    scalper_use_equilibrium_filter: bool = True  # LONG yalnız discount, SHORT yalnız premium
    scalper_min_rr: float = 1.2                # beklenen harman TP getirisi / SL riski alt sınırı; 0 = kapalı
    # Scalper — 2. tur deney bayrakları (2026-08-07 karar matrisi için)
    scalper_entry_mode: str = "taker"            # "maker" = limit giriş simülasyonu (backtest)
    scalper_taker_fee_pct: float = 0.05          # nominal % / bacak
    scalper_maker_fee_pct: float = 0.02
    # Borsanın sembol bazlı komisyon sorgusu kullanılamazsa iki bacakta da
    # maker/taker config oranlarının YÜKSEĞİ kullanılır. Bu buffer ek kayma,
    # funding ve minimum net-kâr kilididir; komisyon yerine geçmez.
    scalper_protection_failure_cooldown_minutes: int = 60
    # Yapısal stop (swing/40-mum dibi) girişe ATR×mult'tan yakınsa stop ATR
    # tabanına genişletilir; pozisyon boyutu stop mesafesinden türediği için
    # USD riski değişmez. 0 = kapalı. (2026-08-11 BEAT bulgusu: düşen piyasada
    # yapısal stop girişin kılpayı altında kalıp anında SL yiyor.)
    # DİKKAT — birincil etki min_rr üzerinden gelir: varsayılan TP/RR ayarında
    # min_rr=1.2, stop mesafesini ≤%1.21'e sınırlar. ATR×mult bu sınırı aştığı
    # anda sinyal genişlemek yerine min_rr kapısında REDDEDİLİR — yani aşırı
    # volatil (çöken) coinlerde giriş tamamen kapanır. Bu bilinçli: BEAT tipi
    # düşen bıçaklarda doğru davranış "daha geniş stop" değil "girme".
    # 0.5 seçimi 2026-08-11 matrisinden: 180g/8 majörde maliyeti ≈ 0
    # (C+cd -4180 vs C+cd+0.5 -4204); 1.0'ın ek maliyeti -575. Koruma değeri
    # majör backtestinde görünmez — top-20 evrenindeki BEAT tipi çöp coinler için.
    scalper_stop_atr_floor_mult: float = 0.5
    # SL veya negatif net kapanış sonrası sembol bu süre yeni girişe kapalı.
    # 0 = kapalı. (2026-08-11: BEAT'e 7 dakikada 4 ardışık giriş, toplam -31 USDT.)
    scalper_loss_cooldown_minutes: int = 60
    # Sembol cooldown'larının restart'a dayanıklı state dosyası (atomik yazım).
    scalper_cooldown_state_path: str = "state/scalper_cooldowns.json"
    # --- Stop modu (2026-08-12 kullanıcı isteği: "SL çok hızlı vuruyor, esnet") ---
    # "structural": sinyalin yapısal stopu + ATR tabanı (mevcut davranış).
    # "fixed_roi": stop, marjın scalper_fixed_stop_roi_pct'si kaybedilince vurur
    #   (fiyat mesafesi = pct/kaldıraç; 10x + %50 → %5 fiyat). Boyutlama risk
    #   tabanlı kaldığı için işlem başına USD riski DEĞİŞMEZ — pozisyon küçülür,
    #   işlem nefes alanı kazanır. Bu modda env uyumu ŞART: SCALPER_MIN_RR=0
    #   (RR kapısı bu mesafede her sinyali reddeder) ve SCALPER_MAX_STOP_PCT ≥
    #   pct/kaldıraç.
    scalper_stop_mode: str = "structural"
    scalper_fixed_stop_roi_pct: float = 50.0
    # --- C giriş filtreleri (2026-08-12: para akışı + dönüş bölgesi teyidi) ---
    # flow_confirm: MFI para akışı aşırı uçtan DÖNÜYOR olmalı (LONG: [-2] ≤
    # long_max ve [-1] > [-2]; SHORT aynası) — düşen bıçakta akış hâlâ satış
    # yönündeyken girişi engeller.
    scalper_c_require_flow_confirm: bool = False
    scalper_c_flow_mfi_period: int = 14
    scalper_c_flow_long_max: float = 30.0
    scalper_c_flow_short_min: float = 70.0
    # reversal_zone: fiyat bir order block (dönüş bloğu) veya EQL/EQH destek-
    # direnç kümesine ATR×tolerans içinde olmalı — "boşlukta" dip/tepe avlamayı
    # engeller.
    scalper_c_require_reversal_zone: bool = False
    scalper_c_zone_atr_tolerance: float = 0.75
    # --- C eşikleri (2026-08-12: aktivite ayarı — "durmamalı asla" isteği) ---
    # Varsayılanlar tarihsel davranışla birebir. RSI bandını gevşetmek (30/70)
    # ve/veya diverjans şartını kaldırmak işlem sıklığını artırır; bedeli
    # backtest ile ölçülmeden canlıda gevşetme.
    scalper_c_rsi_long_max: float = 25.0
    scalper_c_rsi_short_min: float = 75.0
    scalper_c_require_divergence: bool = True
    # --- Zaman dilimi profili (2026-08-12: 1m/5m/15m scalping hedefi) ---
    # entry: sinyal mumu; context: üst bağlam (BE/equilibrium); regime: trend.
    # Varsayılan 5m/15m/4h = tarihsel davranış. Deneysel hızlı profil:
    # 1m/5m/15m — canlıya almadan önce backtest ŞART.
    scalper_tf_entry: str = "5m"
    scalper_tf_context: str = "15m"
    scalper_tf_regime: str = "4h"
    # Evren allowlist (CSV): boş = scanner top_n (mevcut davranış). Doluysa
    # tarama YALNIZ bu sembollerle sınırlanır — canlıyı backtest'in kapsadığı
    # "doğru coinlere" sabitlemek için (2026-08-12: BEAT tipi çöp coinler
    # backtest edilemiyor; kanıt yalnız test edilen evren için geçerli).
    scalper_symbol_allowlist: str = ""
    # TradingView webhook köprüsü (2026-08-12): boş = endpoint kapalı.
    # TV header gönderemediği için secret istek GÖVDESİNDE taşınır; nginx
    # tarafında yalnız /tv-signal yolu açılır. Sinyal, scalper'ın KENDİ giriş
    # hattından geçer (stop/TP/BE/cooldown/kapasite aynen uygulanır).
    tv_webhook_secret: str = ""
    # Çoklu-kaynak sağlama (2026-08-13): pencere içinde bu kadar FARKLI
    # gösterge aynı yönde oy vermeden işlem açılmaz. 1 = tek sinyal yeter.
    # Ters yön oyu tüm oyları sıfırlar (çelişkide sinyal temiz değildir).
    tv_confluence_required: int = 1
    tv_confluence_window_seconds: int = 180
    # ?src= allowlist (2026-08-21): TradingView alarm URL'sindeki ?src=...
    # serbest metindir — yazım hatası (ör. "algpro") sessizce hayalet bir
    # kaynak yaratır ve hiçbir zaman sağlamaya (confluence) ulaşmaz çünkü
    # farklı kaynak sayısı asla dolmaz. Bilinmeyen değer REDDEDİLMEZ
    # (erişilebilirlik > katılık) — "tv" jenerik kaynağına eşlenir ve
    # WARNING loglanır ki typo fark edilsin.
    tv_source_allowlist: str = "luxosc,luxso,algopro,botv3,tv"
    # Risk-olayı kanalı (2026-08-21, D10): haber/olay botlarının POST
    # /risk-event ile giriş durdur/devam et/her-şeyi-düzleştir diyebildiği
    # AYRI kanal — TV webhook'undan (yön önerisi) tamamen farklı amaç, o
    # yüzden AYRI secret. Boş = endpoint kapalı (fail-closed, TV webhook ile
    # aynı desen). docs/INTEGRATIONS.md §3.
    risk_event_secret: str = ""
    # Risk-olayı halt'ı scalper_entry_halt_path'ten AYRI bir dosyadır — bkz.
    # engine.py _risk_event_halt_snapshot yorumu: SCALPER_ENTRY_HALT_ENABLED
    # yalnız koruma-hatası otomatik latch'ini gater; risk-olayı halt'ı bu
    # bayraktan bağımsız HER ZAMAN uygulanır.
    risk_event_halt_path: str = "state/risk_event_halt.json"
    # --- Coin-bazlı dinamik kaldıraç (2026-08-13, kullanıcı isteği) ---
    # fixed_roi stop modunda kaldıraç volatiliteye göre coin başına çözülür:
    # hedef stop FİYAT mesafesi = ATR × dyn_lev_stop_atr_mult olacak şekilde
    # leverage = fixed_stop_roi_pct / (mult × ATR%). Böylece SL yine marjın
    # %fixed_stop_roi_pct'si olur AMA mesafe coin'in gerçek oynaklığını izler:
    # sakin coin (BTC) yüksek kaldıraç, vahşi coin düşük kaldıraç alır.
    scalper_dynamic_leverage: bool = False
    scalper_dyn_lev_stop_atr_mult: float = 3.0
    scalper_dyn_lev_min: int = 3
    scalper_dyn_lev_max: int = 20
    # TESTNET sermaye disiplini: 0 gerçek available balance'ı kullanır.
    # Pozitif değer, bu taban + başlangıç trade id'sinden sonraki yalnız
    # doğrulanmış net PnL (ve muhafazakâr negatif fallback) ile compounding
    # yapar; hiçbir zaman borsadaki available balance'ı aşmaz.
    scalper_virtual_capital_usdt: float = 0.0
    scalper_virtual_capital_start_trade_id: int = 0
    scalper_maker_fill_timeout_candles: int = 3  # limit bu kadar mumda dolmazsa sinyal iptal
    # Maker emir niyeti POST'tan önce bu atomik journal'a yazılır. Relative
    # path process working directory'sine göredir. Test fake cfg'lerinde alan
    # yoksa persistence bilinçli olarak kapalıdır.
    scalper_pending_journal_path: str = "state/scalper_pending_entries.json"
    # Güvenli koruma/recovery kanıtlanamazsa yeni giriş latch'i restart ile
    # temizlenmemeli. Bu atomik durum dosyası yalnız doğrulanmış manuel
    # müdahaleyle kaldırılır.
    scalper_entry_halt_path: str = "state/scalper_entry_halt.json"
    # Fail-closed giriş latch'i. LIVE'da daima True kalmalı; testnet'te hızlı
    # test turları için False yapılabilir (UnprotectedPositionError yine acil
    # kapatma + CRITICAL log üretir, yalnız yeni girişler durdurulmaz).
    # Mainnet'te False, _validate_binance_environment tarafından startup'ta
    # ValueError ile reddedilir — testnet .env'i canlıya kopyalanınca güvenlik
    # kilidi sessizce devre dışı kalamaz.
    scalper_entry_halt_enabled: bool = True
    # Gölge modu (docs/MAINNET_PLAN.md §3, D14): True iken engine sinyalleri
    # BUGÜNKÜ GİBİ tüm kapılardan (rejim/kapasite/cooldown/confluence) geçirir
    # ve executor risk boyutlamasını hesaplar, ama emri BORSAYA GÖNDERMEZ —
    # scalp_trades'e status="SHADOW" olarak kaydeder. Yeni parametreleri
    # mainnet'te riske girmeden 3 gün gözlemlemek için. Testnet'te varsayılan
    # kapalı (mevcut davranış aynen sürer); mainnet'te açık DEĞİLSE
    # _validate_binance_environment risk/webhook secret'larını ve allowlist'i
    # zorunlu kılar (aşağıda).
    scalper_shadow_mode: bool = False
    # Gölge tekilleştirme penceresi (dakika, D14 review, bulgu A/B): occupancy
    # bırakmayan gölge dalı düzeltilmezse aynı sembol her tarama turunda
    # yeniden SHADOW satırı yazar (2-5x şişme) VE kapasite kapısı hiç
    # devreye girmez. Bu pencere içinde aynı sembol yeniden kaydedilmez;
    # ayrıca ScalpExecutor.shadow_active_count() bu pencereyi "açık" gibi
    # sayıp SCALPER_MAX_POSITIONS'a karşı ölçer. ≤0/tanımsız ise
    # scalper_loss_cooldown_minutes'e (o da yoksa 60 dk) düşer — canlıda aynı
    # sembolün fiilen ne kadar süre meşgul sayılacağıyla aynı mertebede.
    scalper_shadow_dedup_minutes: float = 0.0
    # --- Piyasa yapısı (BOS/CHoCH) kapısı — E9/D18 adayı, VARSAYILAN KAPALI ---
    # Sorun (kullanıcı, 2026-08-23): rejim kapısı (D5) 15m EMA50/200 ile dönüşleri
    # saatler geç görüyor; dönüş günlerinde düşen-bıçak LONG / rahatlama-rallisi
    # SHORT kayıpları buradan geliyor. Yapı kapısı aynı soruyu ortalama yerine
    # swing kırılımıyla sorar (src/strategies/scalper/structure.py).
    # KAPALIYKEN hiçbir kod yolu davranış değiştirmez (byte-for-byte aynı
    # backtest — tests/test_golden_backtest.py).
    scalper_structure_gate: bool = False
    # Hangi seri? Rol adı ("entry"/"context"/"regime") ya da doğrudan zaman
    # dilimi ("5m") — canlı env'de context=5m, regime=15m. YENİ REST çağrısı
    # yok: bu seriler her tarama turunda zaten çekiliyor.
    scalper_structure_tf: str = "context"
    # Fraktal pivot uzunluğu (her iki taraf). Büyük = daha az/daha güvenilir
    # swing ama daha geç onay (pivot ancak sağındaki N mum kapanınca onaylanır).
    scalper_structure_pivot: int = 5
    # Kırılım kapanışla mı onaylansın (True, fitil-avı gürültüsünü eler) yoksa
    # fitil yeter mi (False)?
    scalper_structure_use_close: bool = True
    # Kapı açıkken yapıya TERS girişleri engelle (BEAR yapıda LONG yok).
    scalper_structure_block_counter: bool = True
    # Açık pozisyonun TERSİNE CHoCH gelince ne yapılsın:
    # "off" (varsayılan, hiçbir şey) | "be" (stopu break-even'a çek) |
    # "close" (reduce-only MARKET ile kapat).
    scalper_structure_exit: str = "off"
    scalper_c_allowed_regimes: str = "UP,DOWN,RANGE"  # deney: "RANGE" ile sınırla
    scalper_d_use_eqhl: bool = True              # D süpürmesi EQH/EQL kümelerine bağlı
    scalper_eqhl_tolerance_pct: float = 0.05     # pivot eşitlik eşiği (%)
    # İşlem başına MARJ tavanı (kasanın %'si). Risk tavanının (%2) ÜSTÜNE
    # ikinci bir sınır: pozisyonun kilitlediği marj bunu aşamaz. Böylece aynı
    # anda birden çok işleme yer kalır ve likidasyon mesafesi hep uzak olur.
    scalper_max_margin_pct: float = 10.0

    @property
    def is_production(self) -> bool:
        """Production ortamında mı?"""
        return self.app_env.lower() == "production"

    @property
    def is_testnet(self) -> bool:
        """
        Testnet kullanılıyor mu?
        Sadece "testnet" alt string'ine değil, bilinen testnet host'larına
        (TESTNET_HOSTS) bakarak kontrol eder. Böylece "testnet" geçen ama
        gerçek bir testnet host'u olmayan URL'ler yanlışlıkla testnet
        sayılmaz.
        """
        base_url_lower = self.binance_base_url.lower()
        return any(host in base_url_lower for host in TESTNET_HOSTS)

    @field_validator("binance_bind_ip")
    @classmethod
    def _validate_bind_ip(cls, value: str) -> str:
        """Geçersiz IP startup'ta patlasın: httpx.AsyncHTTPTransport hatalı
        local_address'i hatasız kabul eder ve sorun ancak İLK istekte jenerik
        ConnectError olarak (3 retry ardından) yüzeylenir — operatör bunu
        geçici ağ sorunu sanır."""
        value = (value or "").strip()
        if value:
            try:
                ipaddress.ip_address(value)
            except ValueError as e:
                raise ValueError(
                    f"BINANCE_BIND_IP geçersiz IP adresi: {value!r}"
                ) from e
        return value

    @model_validator(mode="after")
    def _validate_structure_gate(self) -> "Settings":
        """Yapı kapısı/çıkışı AÇIKKEN ayarların anlamlı olduğunu startup'ta
        doğrula. Kapalıyken (varsayılan) hiçbir kontrol yapılmaz — kapalı bir
        özelliğin ayarı botu başlatmamazlık etmemeli.

        Sessiz yanlış davranış riski: SCALPER_STRUCTURE_TF çözülemezse yapı
        yanlış seriden okunur ya da hiç okunmaz; SCALPER_STRUCTURE_EXIT'te
        yazım hatası çıkışı sessizce kapatır.
        """
        from src.strategies.scalper import structure as _structure  # döngüsel import yok (structure saf)

        exit_raw = str(self.scalper_structure_exit or "off").strip().lower()
        if exit_raw not in ("off", "be", "close"):
            raise ValueError(
                f"SCALPER_STRUCTURE_EXIT geçersiz: {self.scalper_structure_exit!r} "
                f"(geçerli: off | be | close)"
            )
        if not (self.scalper_structure_gate or exit_raw != "off"):
            return self
        if int(self.scalper_structure_pivot or 0) < 1:
            raise ValueError(
                f"SCALPER_STRUCTURE_PIVOT >= 1 olmalı (verilen: {self.scalper_structure_pivot})"
            )
        _structure.resolve_structure_role(self)  # çözülemezse ValueError
        return self

    @model_validator(mode="after")
    def _validate_fixed_roi_stop_consistency(self) -> "Settings":
        """fixed_roi stop modu üç ayrı ayara sessizce bağımlı — tutarsız
        kombinasyon botu 'healthy görünüp hiç işlem açmaz' duruma sokar
        (2026-08-12 inceleme bulgusu). Startup'ta fail-fast:

        - mesafe = fixed_stop_roi_pct / kaldıraç, [min_stop_pct, max_stop_pct]
          bandında olmalı (executor risk kapısı her sinyali reddetmesin);
        - min_rr > 0 ise beklenen TP harman ROI'siyle R:R bu mesafede
          min_rr'ı geçebilmeli (yoksa RR kapısı her sinyali reddeder).
        """
        if str(self.scalper_stop_mode).lower() != "fixed_roi":
            return self
        leverage = float(self.scalper_leverage or 0)
        roi_pct = float(self.scalper_fixed_stop_roi_pct or 0)
        if leverage <= 0 or roi_pct <= 0:
            return self
        distance_pct = roi_pct / leverage
        if distance_pct > self.scalper_max_stop_pct:
            raise ValueError(
                f"SCALPER_STOP_MODE=fixed_roi tutarsız: stop mesafesi "
                f"%{distance_pct:.2f} (={roi_pct}/{leverage:g}) > "
                f"SCALPER_MAX_STOP_PCT=%{self.scalper_max_stop_pct} — risk kapısı "
                f"HER sinyali reddeder. MAX_STOP_PCT'yi yükseltin veya "
                f"FIXED_STOP_ROI_PCT'yi düşürün."
            )
        if distance_pct < self.scalper_min_stop_pct:
            raise ValueError(
                f"SCALPER_STOP_MODE=fixed_roi tutarsız: stop mesafesi "
                f"%{distance_pct:.2f} < SCALPER_MIN_STOP_PCT=%{self.scalper_min_stop_pct}"
                f" — risk kapısı HER sinyali reddeder."
            )
        if self.scalper_min_rr > 0:
            runner_fraction = max(
                0.0, 1.0 - self.scalper_tp1_fraction - self.scalper_tp2_fraction
            )
            expected_roi = (
                self.scalper_tp1_roi * self.scalper_tp1_fraction
                + self.scalper_tp2_roi * self.scalper_tp2_fraction
                + self.scalper_tp1_roi * runner_fraction
            )
            rr = expected_roi / roi_pct if roi_pct > 0 else 0.0
            if rr < self.scalper_min_rr:
                raise ValueError(
                    f"SCALPER_STOP_MODE=fixed_roi tutarsız: beklenen R:R "
                    f"{rr:.2f} < SCALPER_MIN_RR={self.scalper_min_rr} — RR kapısı "
                    f"HER sinyali reddeder. Bu modda SCALPER_MIN_RR=0 önerilir."
                )
        return self

    @model_validator(mode="after")
    def _validate_binance_environment(self) -> "Settings":
        """
        GÜVENLİK KONTROLÜ: Gerçek parayla (mainnet) yanlışlıkla işlem
        açılmasını engeller.

        1) binance_base_url mainnet'i (bilinen bir testnet host'u DEĞİLSE)
           gösteriyorsa VE allow_mainnet False ise -> ValueError fırlatılır.
           Bot yanlışlıkla veya .env bozulduğunda sessizce gerçek parayla
           işlem açamaz.
        2) app_env "production" DEĞİLKEN binance_base_url mainnet'i
           gösteriyorsa (allow_mainnet=True ile bilinçli olarak izin
           verilmiş olsa bile) yüksek sesle uyarı basılır — geliştirme/test
           ortamında gerçek parayla işlem açma riskine dikkat çekmek için.
        """
        if not self.is_testnet:
            # binance_base_url bilinen bir testnet host'u değil -> mainnet
            # veya bilinmeyen/özel bir host. Güvenlik açısından mainnet
            # gibi ele alınır.
            if not self.allow_mainnet:
                raise ValueError(
                    "GÜVENLİK HATASI: BINANCE_BASE_URL gerçek parayla işlem yapılan "
                    f"MAINNET'i gösteriyor ({self.binance_base_url}) ama bu bilinçli "
                    "olarak onaylanmadı. Gerçek parayla işlem için ALLOW_MAINNET=true "
                    "ayarlayın. Testnet kullanmak istiyorsanız BINANCE_BASE_URL="
                    "https://testnet.binancefuture.com olarak ayarlayın."
                )

            if not self.scalper_entry_halt_enabled:
                # Testnet .env'inden kalan SCALPER_ENTRY_HALT_ENABLED=false,
                # canlıda fail-closed giriş kilidini (UnprotectedPositionError
                # latch'i + kalıcı halt dosyası) sessizce devre dışı bırakır.
                raise ValueError(
                    "GÜVENLİK HATASI: SCALPER_ENTRY_HALT_ENABLED=false yalnız "
                    "testnet'te kullanılabilir. Mainnet'te fail-closed giriş "
                    "kilidi devre dışı bırakılamaz — ayarı .env'den kaldırın "
                    "veya true yapın."
                )

            if not self.scalper_shadow_mode:
                # Gölge modu (D14) mainnet'e AÇIK olarak girmenin tek istisnası:
                # emir gönderilmediği için kill-switch/webhook/allowlist'in
                # HENÜZ kurulu olmaması riske girmez. Gölge KAPALIYSA (gerçek
                # emir gönderilecek demektir) bu üç koruma ZORUNLUDUR —
                # docs/MAINNET_PLAN.md §5.3.
                # .strip() ile: bu üç değeri okuyan HER tüketici (main.py:705
                # tv_webhook_secret, main.py:793 risk_event_secret, engine.py
                # ~1085 allowlist) zaten .strip() uygular; pydantic bunu
                # otomatik yapmaz. Bare truthiness kontrolü yalnız boşluk
                # içeren tırnaklı bir değeri ("   ") GEÇERLİ sayıp korumaları
                # sessizce devre dışı bırakırdı (D14 review, bulgu C).
                missing_mainnet_protections = []
                if not (self.risk_event_secret or "").strip():
                    missing_mainnet_protections.append("RISK_EVENT_SECRET")
                if not (self.tv_webhook_secret or "").strip():
                    missing_mainnet_protections.append("TV_WEBHOOK_SECRET")
                # engine.py'nin allowlist parse'ıyla BİREBİR aynı süzgeç:
                # yalnız virgül/boşluktan ibaret bir değer ("," veya "  ")
                # boş evrene düşer, bu yüzden mainnet korumasında da boş
                # sayılmalı.
                if not [
                    s
                    for s in str(self.scalper_symbol_allowlist or "").split(",")
                    if s.strip()
                ]:
                    missing_mainnet_protections.append("SCALPER_SYMBOL_ALLOWLIST")
                if missing_mainnet_protections:
                    raise ValueError(
                        "GÜVENLİK HATASI: Mainnet'te (SCALPER_SHADOW_MODE=false iken) şu "
                        f"ayarlar boş bırakılamaz: {', '.join(missing_mainnet_protections)}. "
                        "Kill-switch (RISK_EVENT_SECRET), TV webhook doğrulaması "
                        "(TV_WEBHOOK_SECRET) ve sembol allowlist'i (SCALPER_SYMBOL_ALLOWLIST) "
                        "mainnet'te ZORUNLUDUR — doldurun ya da önce SCALPER_SHADOW_MODE=true "
                        "ile 3 gün gölge modda çalıştırın (docs/MAINNET_PLAN.md §5.3)."
                    )

            if not self.is_production:
                warning_message = (
                    "\n"
                    + "!" * 70 + "\n"
                    "UYARI: GERÇEK PARAYLA (MAINNET) İŞLEM YAPILANDIRMASI "
                    "PRODUCTION OLMAYAN BİR ORTAMDA AKTİF!\n"
                    f"  APP_ENV        = '{self.app_env}' (production değil)\n"
                    f"  BINANCE_BASE_URL = '{self.binance_base_url}' (mainnet)\n"
                    f"  ALLOW_MAINNET  = True\n"
                    "Bu, geliştirme/test sırasında yanlışlıkla gerçek parayla işlem "
                    "açma riski taşır. Emin değilseniz BINANCE_BASE_URL'i testnet'e "
                    "çevirin veya APP_ENV=production yapın.\n"
                    + "!" * 70
                )
                warnings.warn(warning_message, UserWarning, stacklevel=2)
                # loguru henüz yapılandırılmamış olabilir (circular import
                # riski nedeniyle burada app_logger kullanılmıyor); bu yüzden
                # ayrıca stderr'e de basılıyor ki mutlaka görülsün.
                print(warning_message, file=sys.stderr)

        return self


# Global settings instance
settings = Settings()
