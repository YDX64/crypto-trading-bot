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
# Public market-data (yalnız /fapi/v1/klines) için KABUL EDİLEN host'lar —
# TAM eşleşme (alt-dize DEĞİL). Neden allowlist: bu yol imzasızdır, yani yanlış
# bir host hiçbir kimlik doğrulama hatası üretmez; bot sessizce YABANCI mumlarla
# karar verir ve o mumlardan türeyen chandelier seviyesi gerçek bir stop emrine
# dönüşür. Bir yazım hatası (`fapi.binance.com.evil.tld`) sessizce geçmemeli.
# Yeni bir Binance uç noktası gerekiyorsa bu demete BİLİNÇLİ olarak eklenir.
# ⚠️ `testnet.binance.vision` BİLİNÇLİ olarak YOKTUR (düşmanca inceleme
# bulgusu): orası Binance SPOT testnet'idir ve `/fapi/...` yollarını hiç
# sunmaz. Buraya yazılsaydı bot her kline isteğinde 404 alır, hiçbir sinyal
# üretemez ve operatör "URL kabul edildi" diye çalıştığını sanardı. Allowlist
# yalnız USDⓈ-M Futures uçlarını içerir.
MARKET_DATA_ALLOWED_HOSTS = (
    "fapi.binance.com",
    "fapi1.binance.com",
    "fapi2.binance.com",
    "fapi3.binance.com",
    "fapi4.binance.com",
    "testnet.binancefuture.com",
    "demo-fapi.binance.com",
    "demo.binance.com",
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
    # 2026-08-23 (D17): PUBLIC piyasa verisi (yalnız /fapi/v1/klines) için AYRI
    # host. Boş = bugünkü davranış (binance_base_url ile aynı host).
    # NEDEN: canlı bot TESTNET'teyken RSI/Bollinger/diverjans/rejim/ATR
    # hesapları TESTNET mumlarından üretiliyor; testnet mumları gerçek
    # piyasadan sapar (aynı dakikada mainnet L 77100.0/C 77126.8 vs testnet
    # L 77143.8/C 77182.6; hacim mainnet 84 vs testnet 1494 — uydurma) ve
    # backtest harness'i (backtest.py, mainnet fapi) ile canlı motor AYNI
    # sinyalleri görmez (parite açığı).
    # KAPSAM: yalnız imzasız/public kline çekimi. Emir, bakiye, pozisyon,
    # ticker, exchangeInfo, income — hepsi BINANCE_BASE_URL'de kalır.
    # GÜVENLİK: mainnet'te işlem yapılırken (is_testnet False) bu alan boş ya
    # da mainnet host'u olmalıdır — gerçek parayla işlem açan bir motorun
    # testnet mumlarıyla karar vermesi _validate_binance_environment
    # tarafından reddedilir.
    scalper_market_data_base_url: str = ""
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

    # --- REST ağırlık geri çekilmesi (D22) -----------------------------
    # Binance USDⓈ-M IP ağırlık sınırı 2400/dk'dır ve sayaç IP GENELİNDEDİR
    # (aynı çıkış IP'sindeki başka süreçler de tüketir). `X-MBX-USED-WEIGHT-1M`
    # bu eşiklere ulaştığında KRİTİK OLMAYAN istekler (pano beslemeleri,
    # periyodik hesap özeti, evren taraması, teşhis) dakika penceresi
    # dolana kadar ağa ÇIKMAZ; emir/koruma/kapanış-doğrulama istekleri geçer.
    # 0 = kapalı (eski davranış: yalnız uyarı logu).
    binance_weight_soft_limit: int = 2000
    binance_weight_hard_limit: int = 2300

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
    # 2026-08-23 (D19): olay kanalının dört kaynağı varsayılana EKLENDİ —
    # `luxso_exit` (S&O "Exit Signal"), `luxso_trend` (S&O "Trend Catcher/
    # Tracer Up|Down"), `pac_choch` (PAC "Bullish/Bearish S-CHOCH"),
    # `algopro_tp1` (AlgoPro "🎯 TP1 Hit"). Ekleme SALT GENİŞLETMEDİR:
    # bugüne kadar hiçbir alarm bu değerleri göndermiyordu, dolayısıyla
    # mevcut 49 alarmın davranışı değişmez. ⚠️ Sunucu `.env`'i
    # TV_SOURCE_ALLOWLIST'i AÇIKÇA set ediyorsa bu varsayılan devreye
    # GİRMEZ — o satıra da eklenmeli (docs/RUNBOOK.md "TV olay kanalı").
    tv_source_allowlist: str = (
        "luxosc,luxso,algopro,botv3,tv,luxso_exit,luxso_trend,pac_choch,algopro_tp1"
    )
    # "Olay kaynağı" etiketleri (D19a bulgu A). Bu listedeki bir `src`
    # `kind=entry` ile GİRİŞ OYU VEREMEZ — istek 422 ile reddedilir. Gerekçe:
    # bir çıkış alarmında `kind` belirteci düşerse (yazım hatası, iç içe JSON,
    # unutma) istek sessizce giriş oyuna dönüşür ve LuxAlgo ailesi tek başına
    # 2/2 sağlama kotasını doldurup POZİSYON AÇTIRABİLİR. `kind` yokluğunun
    # varsayılanı "entry" olduğu için tek koruma kaynağın kimliğidir.
    # Yeni bir çıkış/yapı entegrasyonu eklerken adı buraya yazılır (kod
    # değişmez, INTEGRATIONS §1'deki kuralla aynı ruh).
    # ⚠️ Bu değişkeni BOŞ bırakmak korumayı KAPATMAZ: boş/yalnız-boşluk değer
    # kod varsayılanına (`DEFAULT_EVENT_SOURCES`) döner — fail-safe yön. Bir
    # kaynağı korumadan çıkarmak istiyorsan adını listeden SİL, listeyi değil.
    tv_event_sources: str = "luxso_exit,luxso_trend,pac_choch,algopro_tp1"
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
    # --- İşlem adli kaydı (D21, 2026-08-23) — YALNIZ GÖZLEM -------------
    # Hiçbir kapı/boyutlama/çıkış kararı bu ayarları okumaz; kapatılırsa
    # yalnız kayıt tutulmaz, motor davranışı HER İKİ durumda da aynıdır.
    scalper_forensics_enabled: bool = True
    # Kapanıştan sonra "fiyat girişe döndü mü" penceresi (dk; 0 = KAPALI).
    # Post-mortem yalnız bu pencere DOLDUKTAN sonra hesaplanır — look-ahead
    # değildir (bkz. forensics.postmortem_from_candles).
    scalper_forensics_postmortem_min: float = 60.0
    # Etiket eşikleri (bkz. forensics.VerdictThresholds):
    scalper_forensics_counter_drift_pct: float = 1.0   # ters-gün etiketi
    scalper_forensics_run_pct: float = 5.0             # geç-giriş etiketi
    scalper_forensics_stale_signal_sec: float = 30.0   # sinyal→dolum gecikmesi
    scalper_forensics_fee_ratio: float = 0.5           # net/brüt eşiği
    scalper_c_allowed_regimes: str = "UP,DOWN,RANGE"  # deney: "RANGE" ile sınırla
    scalper_d_use_eqhl: bool = True              # D süpürmesi EQH/EQL kümelerine bağlı
    scalper_eqhl_tolerance_pct: float = 0.05     # pivot eşitlik eşiği (%)
    # İşlem başına MARJ tavanı (kasanın %'si). Risk tavanının (%2) ÜSTÜNE
    # ikinci bir sınır: pozisyonun kilitlediği marj bunu aşamaz. Böylece aynı
    # anda birden çok işleme yer kalır ve likidasyon mesafesi hep uzak olur.
    scalper_max_margin_pct: float = 10.0
    # --- TradingView olay kanalı (D19, 2026-08-23) — docs/INTEGRATIONS.md §7 ---
    # `/tv-signal` gövdesinde `kind=exit|choch|trend|tp1` taşıyan istekler
    # sağlamaya (TvConfluence) HİÇ girmez; `src/services/tv_events.py`'ye
    # yapı/çıkış olayı olarak yazılır. Bu üç ayar YALNIZ motorun o olaylara
    # verdiği tepkiyi belirler:
    #   off    = olaylar kaydedilir, motor HİÇ etkilenmez (telemetri yalnız).
    #   shadow = olaylar kaydedilir + "aktif olsaydı ne olurdu" loglanır ve
    #            would_block/would_exit sayaçları artar; emir/stop DEĞİŞMEZ.
    #   active = giriş kapısı + çıkış tetikleyicisi GERÇEKTEN uygulanır.
    # Varsayılan `shadow`: yeni bir sinyal kaynağı canlıya ölçülmeden
    # alınmaz (docs/INTEGRATIONS.md §4 terfi hattı, D14 gölge disiplini).
    scalper_tv_events_mode: str = "shadow"
    # Bir yapı/çıkış olayı bu kadar dakikadan eskiyse yok sayılır. TV
    # alarmları 5 dakikalık grafiklerden gelir; 240 dk ≈ 48 mum.
    # ⚠️ **0 = PENCERE KAPALI** (D19a bulgu G5): "süresiz taze" DEĞİL. Bir
    # sinyal kanalının yanlışlıkla boşaltılan ayarı sonsuz ömürlü bir kapı
    # değil SESSİZ bir kanal üretmelidir.
    scalper_tv_events_max_age_min: float = 240.0
    # `active` modda açık pozisyona ne yapılır:
    #   off   = hiçbir şey (yalnız giriş kapısı çalışır)
    #   be    = stop BE'ye çekilir (mevcut BE mekanizması, yeni emir yolu yok)
    #   close = reduce-only MARKET kapanış (reaper/flatten ile AYNI çağrı),
    #           exit_reason="TV_EVENT"
    scalper_tv_events_exit: str = "be"
    # Pozisyon ZARARDAYKEN çıkış olayı gelirse (D19a bulgu B):
    #   skip  = hiçbir şey (logla + say) — VARSAYILAN
    #   close = reduce-only MARKET kapanış (bilinçli, geri alınamaz)
    # Neden: zararda BE'ye çekmek stopu piyasanın TERS tarafına koymaktır →
    # Binance -2021 → position_manager._emergency_close (ACİL KAPANIŞ). Yani
    # "yalnız stop sıkışır, geri alınabilir" sanılan `be` ayarı fiilen
    # piyasa emriyle kapanışa dönüşürdü. `be` artık zararda ASLA denenmez.
    scalper_tv_events_exit_losing: str = "skip"
    # BE hedefinin piyasadan güvenli uzaklığı (%, tek yönlü pay). Fiyat
    # BE'ye bu paydan yakınsa "kârda" sayılmaz ve stop taşınmaz — tick/spread
    # gürültüsünde -2021'e düşmemek için.
    scalper_tv_events_be_margin_pct: float = 0.05
    # Yapı durumunu KARARA sokan kaynaklar (CSV). S&O trend'i ile PAC
    # CHoCH'u ayrı `src` etiketleriyle tutulur ve ikisi de /scalper/status'ta
    # görünür; hangisinin kapıyı/çıkışı tetikleyeceğini bu liste seçer.
    # ⚠️ **Boş = HİÇBİR KAYNAK karar vermez** (D19a bulgu G5): "tüm kaynaklar"
    # DEĞİL. Kapı kaynakları çelişirse (MIXED) kapı UYGULANMAZ — çelişki
    # "bilinmiyor"dur, "her iki yön de yasak" değil (D19a bulgu F).
    scalper_tv_events_gate_sources: str = "pac_choch,luxso_trend"
    # Olay defterinin restart'a dayanıklı durum dosyası (atomik yazım).
    # Bozuk dosya = boş durum + WARNING (fail-closed DEĞİL — bkz. tv_events.py).
    tv_events_state_path: str = "state/tv_events.json"

    # ------------------------------------------------------------------
    # Çalışma modu (D20) — AlgoPro takipçi halkası
    # ------------------------------------------------------------------
    # "scalper" (varsayılan) = BUGÜNKÜ davranış: scanner + strateji C + TV
    # sağlaması + orchestrator. "follower" = AYRI süreç/halka: scanner ve
    # strateji YOK, giriş yalnız AlgoPro olaylarından (`POST /follower/event`).
    # Aynı kod tabanı, ayrı .env/DB/state/log/port/Binance hesabı.
    bot_mode: str = "scalper"

    # Ana bot (scalper halkası) AlgoPro kaynaklı TV olaylarını bu adrese
    # İLETİR (fire-and-forget). Boş = iletim KAPALI (bugünkü davranış).
    # TV alarm URL'leri DEĞİŞMEZ — tek TV girişi /tv-signal'da kalır.
    follower_forward_url: str = ""
    # Takipçi kanalının secret'ı — TV_WEBHOOK_SECRET ve RISK_EVENT_SECRET'tan
    # AYRI. Boş = `/follower/event` 503 ile kapalı (aynı fail-closed desen).
    follower_forward_secret: str = Field(default="", repr=False)
    # 20 sn: `/follower/event` yanıtı, olay TAMAMEN işlendikten sonra döner ve
    # bir giriş ~10 ardışık Binance çağrısı + SL + 3 TP emri sürer (3-6 sn).
    # 2 sn'lik eski değer her BAŞARILI girişte sahte "iletemedi" uyarısı
    # üretiyordu. Köprü ayrı task olduğu için uzun timeout ana motoru
    # BLOKLAMAZ ve `_post` yeniden deneme YAPMAZ (çift giriş riski yok).
    follower_forward_timeout_seconds: float = 20.0

    # Takipçi evreni ve olay filtreleri.
    follower_symbol_allowlist: str = (
        "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT"
    )
    # TradingView {{interval}} 1 dakikalık grafikte "1" döner; "1m" de kabul.
    follower_timeframe: str = "1"
    follower_max_positions: int = 4
    follower_cooldown_sec: float = 60.0
    # AlgoPro mesajındaki `Score: 8` alanı için alt sınır. 0 = filtre KAPALI
    # (bugünkü karar: "AlgoPro ne diyorsa"). Skoru olmayan mesaj filtreye
    # TAKILMAZ (alan opsiyoneldir), yalnız skor VARSA ve altındaysa reddedilir.
    follower_min_score: float = 0.0
    # Ters AlgoPro sinyalinde mevcut pozisyonu kapatıp yeni yöne gir.
    follower_flip: bool = True
    follower_daily_loss_limit_pct: float = 15.0  # SCALPER_DAILY_LOSS_LIMIT_PCT mantığı

    # --- Boyutlama (KULLANICI KARARI 2026-08-23) ---
    # Marj = bakiyenin %'si; kaldıraç VOLATİLİTEYE göre dinamik:
    #   lev = clamp(round(SL_ROI_TARGET / sl_pct), LEV_MIN, LEV_MAX)
    # yani stop, marjın ~%SL_ROI_TARGET'i olacak şekilde seçilir.
    follower_margin_pct: float = 10.0
    follower_sl_roi_target: float = 30.0
    follower_lev_min: int = 3
    follower_lev_max: int = 100
    # Likidasyon koruması: lev × sl_pct bu değeri aşamaz (stop marjın %'si).
    follower_lev_liq_guard_pct: float = 50.0
    # mmr payı: (1/lev − mmr) > mult × sl_pct/100 değilse kaldıraç düşürülür.
    follower_mmr_safety_mult: float = 2.0
    # Borsa kaldıraç dilimi (/fapi/v1/leverageBracket) önbellek ömrü.
    follower_bracket_cache_ttl_seconds: float = 21600.0
    # ÜCRET EŞİĞİ KAPISI — varsayılan **1.0 = AÇIK** (düşmanca inceleme
    # 2026-08-23: ölçülen AlgoPro seviyeleriyle BTC 1m'de her sonuç negatifti).
    # TP1 ROI'si gidiş-dönüş komisyonun bu KATININ altında kalan işleme HİÇ
    # girilmez (boyut/TP1/stop DEĞİŞMEZ — işlem yalnız hiç açılmaz, kullanıcının
    # "boyutla oynama" yasağıyla uyumlu). Aritmetik kaldıraçtan BAĞIMSIZDIR:
    #   tp1_roi = RR1 × lev × sl_pct ≥ ratio × (lev × 2 × oran × 100)
    #   → sl_pct ≥ ratio × 2 × oran × 100 / RR1  (RR1=0.5, taker %0.05 → %0.20)
    # 0.0 yazarak KAPATILABİLİR (kullanıcı kararı) — bkz. docs/DECISIONS.md D20.
    follower_min_tp1_fee_ratio: float = 1.0

    # --- Seviye motoru ---
    # Öncelik: (a) AlgoPro mesajındaki sl/tp1/tp2/tp3, (b) hesaplanan kural.
    follower_sl_atr_mult: float = 3.0
    follower_atr_len: int = 14
    follower_tp_rr1: float = 0.5
    follower_tp_rr2: float = 1.0
    follower_tp_rr3: float = 1.5
    # Stop mesafesi bandı (fiyat %). Bant dışı = giriş YOK (fail-closed):
    # sl_pct kaldıraç formülünün paydasıdır, sıfıra yaklaşması yasak.
    follower_min_sl_pct: float = 0.02
    follower_max_sl_pct: float = 5.0
    # SİNYAL SAPMA KAPISI: alarm mesajındaki `Price` ile emir anındaki CANLI
    # fiyat arasındaki fark bu yüzdeyi aşarsa GİRİŞ YOK. 0.0 = türetilmiş
    # varsayılan: SL mesafesinin %50'si (`sl_pct × 0.5`). AlgoPro'nun
    # seviyeleri alarm fiyatına göre çizilir; fiyat o mesafenin yarısını
    # geçtiyse tez artık geçerli değildir ve stop "zaten geçilmiş" olabilir.
    follower_max_signal_drift_pct: float = 0.0
    # Olay YAŞI: HTTP'de alındığı andan giriş emrine kadar geçen süre bunu
    # aşarsa giriş YAPILMAZ (global `_entry_lock` kuyruğunda bekleyen bayat
    # sinyal 1 dakikalık grafikte artık bir sinyal değildir).
    follower_max_event_age_sec: float = 20.0
    # Kalibrasyon kancası: her girişte hesaplanan + (varsa) mesaj seviyeleri.
    follower_levels_log_path: str = "state/follower_levels.jsonl"

    # --- Takipçi işletim ---
    # Açık pozisyon izleme (TP/BE/kapanış) turu — scalper'ın safety döngüsüyle
    # aynı mertebe; dolan bir girişin korumasız kalma penceresini kısa tutar.
    follower_safety_interval_seconds: float = 2.0
    # Fail-closed giriş kilidi (UnprotectedPositionError latch'i). Scalper'ın
    # `scalper_entry_halt_path`'inden AYRI dosya; takipçi halkasında bu kilit
    # KAPATILAMAZ (bayrak yoktur — ayrı hesap, ayrı süreç).
    follower_entry_halt_path: str = "state/follower_entry_halt.json"

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

    @property
    def market_data_base_url(self) -> str:
        """Public kline çekiminin GERÇEKTEN kullandığı host (D17).

        Boş `scalper_market_data_base_url` = bugünkü davranış: işlem host'u
        ile aynı. Emir/bakiye/pozisyon yolu bu property'yi ASLA kullanmaz.
        """
        return (self.scalper_market_data_base_url or "").strip() or self.binance_base_url

    @property
    def kline_source(self) -> str:
        """Teşhis etiketi: "trading_host" (tek host) | "separate" (ayrı host)."""
        return (
            "separate"
            if self.market_data_base_url != self.binance_base_url
            else "trading_host"
        )

    @property
    def market_data_is_testnet(self) -> bool:
        """Piyasa verisi bilinen bir testnet host'undan mı geliyor?"""
        url_lower = self.market_data_base_url.lower()
        return any(host in url_lower for host in TESTNET_HOSTS)

    @field_validator("scalper_market_data_base_url")
    @classmethod
    def _validate_market_data_base_url(cls, value: str) -> str:
        """Boş = kapalı. Doluysa biçim startup'ta fail-fast doğrulanır.

        Neden burada: hatalı bir URL yalnız İLK kline çekiminde jenerik bir
        ağ hatası olarak yüzeylenirdi (3 retry sonrası) — operatör bunu
        geçici ağ sorunu sanar ve bot sessizce sinyalsiz kalır.
        """
        value = (value or "").strip()
        if not value:
            return ""
        if not value.startswith("https://"):
            raise ValueError(
                f"SCALPER_MARKET_DATA_BASE_URL 'https://' ile başlamalı: {value!r} "
                "(public veri olsa da düz HTTP kabul edilmez)"
            )
        if value.endswith("/"):
            raise ValueError(
                f"SCALPER_MARKET_DATA_BASE_URL sonunda '/' olmamalı: {value!r} "
                "(endpoint yolu base_url'e doğrudan eklenir: "
                "<base_url>/fapi/v1/klines)"
            )
        host = value[len("https://"):]
        if not host or "/" in host or any(ch.isspace() for ch in value):
            raise ValueError(
                f"SCALPER_MARKET_DATA_BASE_URL yalnız şema+host olmalı (yol/boşluk "
                f"içeremez): {value!r}"
            )
        # TAM host eşleşmesi (alt-dize DEĞİL): imzasız bir yol olduğu için
        # yanlış host sessizce yabancı mumlarla karar verdirir.
        if host.lower() not in MARKET_DATA_ALLOWED_HOSTS:
            raise ValueError(
                f"SCALPER_MARKET_DATA_BASE_URL bilinmeyen host: {host!r}. İzin "
                f"verilenler: {', '.join(MARKET_DATA_ALLOWED_HOSTS)} "
                "(yeni bir Binance uç noktası gerekiyorsa src/core/config.py'deki "
                "MARKET_DATA_ALLOWED_HOSTS demetine bilinçli olarak ekleyin)."
            )
        return value

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

    @field_validator("bot_mode")
    @classmethod
    def _validate_bot_mode(cls, value: str) -> str:
        """Bilinmeyen BOT_MODE sessizce 'scalper' gibi davranmamalı.

        Yazım hatası (`BOT_MODE=folower`) takipçi halkasını sessizce scalper
        olarak başlatır ve İKİ motor aynı hesapta işlem açar — startup'ta
        fail-fast.
        """
        normalized = str(value or "").strip().lower()
        if normalized not in ("scalper", "follower"):
            raise ValueError(
                f"BOT_MODE geçersiz: {value!r} — 'scalper' (varsayılan) veya "
                "'follower' olmalı (bkz. docs/DECISIONS.md D20)."
            )
        return normalized

    @property
    def is_follower_mode(self) -> bool:
        """AlgoPro takipçi halkası mı? (BOT_MODE=follower)"""
        return str(self.bot_mode or "").strip().lower() == "follower"

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
    def _validate_tv_events_settings(self) -> "Settings":
        """TV olay kanalı (D19) ayarlarında yazım hatası startup'ta patlasın.

        Sessiz düşürme (ör. "activee" → shadow) operatörün "kapı açık"
        sandığı ama motorun hiç uygulamadığı bir duruma yol açardı —
        `_validate_fixed_roi_stop_consistency` ile aynı fail-fast disiplini.
        """
        mode = str(self.scalper_tv_events_mode or "").strip().lower()
        if mode not in ("off", "shadow", "active"):
            raise ValueError(
                f"SCALPER_TV_EVENTS_MODE geçersiz: {self.scalper_tv_events_mode!r} — "
                "off | shadow | active olmalı (docs/INTEGRATIONS.md §7)"
            )
        action = str(self.scalper_tv_events_exit or "").strip().lower()
        if action not in ("off", "be", "close"):
            raise ValueError(
                f"SCALPER_TV_EVENTS_EXIT geçersiz: {self.scalper_tv_events_exit!r} — "
                "off | be | close olmalı (docs/INTEGRATIONS.md §7)"
            )
        losing = str(self.scalper_tv_events_exit_losing or "").strip().lower()
        if losing not in ("skip", "close"):
            raise ValueError(
                "SCALPER_TV_EVENTS_EXIT_LOSING geçersiz: "
                f"{self.scalper_tv_events_exit_losing!r} — skip | close olmalı "
                "(docs/INTEGRATIONS.md §7.4)"
            )
        if float(self.scalper_tv_events_max_age_min or 0.0) < 0:
            raise ValueError(
                "SCALPER_TV_EVENTS_MAX_AGE_MIN negatif olamaz "
                f"({self.scalper_tv_events_max_age_min})"
            )
        if float(self.scalper_tv_events_be_margin_pct or 0.0) < 0:
            raise ValueError(
                "SCALPER_TV_EVENTS_BE_MARGIN_PCT negatif olamaz "
                f"({self.scalper_tv_events_be_margin_pct})"
            )
        # 0/boş "KAPALI" demektir (bkz. tv_events.py "SIFIR/BOŞ = KAPALI") ve
        # `off`/`shadow` modlarında bu GEÇERLİ bir yapılandırmadır — teşhis
        # `TvEvents.log_config_health()` WARNING'i ve `/scalper/status` →
        # `tv_events.gate_enabled` / `window_open` alanlarıdır.
        #
        # `active` ise BİLİNÇLİ bir karardır: operatör "kanal artık emir/stop
        # değiştirsin" demiştir. O yüzden `active` iken kanalın HİÇBİR ŞEY
        # yapamaz hale gelmesi kesinlikle yazım hatasıdır → fail-fast.
        # DİKKAT (D19a-2): "kapı kaynağı yok" TEK BAŞINA hata DEĞİLDİR —
        # `gate_sources` YALNIZ yapı olaylarını (choch/trend) süzer;
        # `exit`/`tp1` olayları kaynak ayrımı olmadan uygulanır
        # (`TvEvents.pending_exit` gate_sources'a bakmaz, INTEGRATIONS §7.4).
        # Yani "giriş kapısı yok ama açık çık komutlarına uy" geçerli bir
        # terfi adımıdır. Pencere (`max_age`) ise HER İKİSİNİ birden kapatır.
        gate_sources = {
            s.strip() for s in str(self.scalper_tv_events_gate_sources or "").split(",")
            if s.strip()
        }
        window_open = float(self.scalper_tv_events_max_age_min or 0.0) > 0.0
        can_gate = bool(gate_sources) and window_open
        can_exit = action != "off" and window_open
        if mode == "active" and not (can_gate or can_exit):
            raise ValueError(
                "SCALPER_TV_EVENTS_MODE=active ama kanal HİÇBİR ŞEY yapamaz: "
                f"MAX_AGE_MIN={self.scalper_tv_events_max_age_min} "
                f"(0 = pencere KAPALI), GATE_SOURCES="
                f"{self.scalper_tv_events_gate_sources!r} (boş = hiçbir kaynak "
                f"karar vermez), EXIT={self.scalper_tv_events_exit!r}. "
                "`active` bilinçli bir karardır; sessizce ölü bir kanal "
                "operatörü yanıltır (docs/INTEGRATIONS.md §7.4)"
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
        3) (D17) Mainnet'te işlem yapılırken SCALPER_MARKET_DATA_BASE_URL bir
           TESTNET host'unu gösteremez -> ValueError. Gerçek para sahte
           mumlarla yönetilemez.
        """
        if self.is_follower_mode and not (self.risk_event_secret or "").strip():
            # TESTNET DAHİL zorunludur: takipçi halkasının TEK uzaktan durdurma
            # yolu /risk-event'tir. Telegram YOKTUR, scanner YOKTUR ve köprüyü
            # kapatmak yalnız YENİ sinyali keser — AÇIK pozisyonu kapatmaz.
            # Marj %10 + ≤100x kaldıraçlı bir halkanın "durdurulamaz" olarak
            # başlaması kabul edilemez (mainnet kill-switch kuralıyla aynı ilke,
            # docs/MAINNET_PLAN.md §6).
            raise ValueError(
                "GÜVENLİK HATASI: BOT_MODE=follower için RISK_EVENT_SECRET "
                "ZORUNLUDUR. Takipçi halkasının tek acil durdurma/flatten yolu "
                "POST /risk-event'tir (Telegram yok). .env'e güçlü bir rastgele "
                "değer ekleyin (bkz. docs/RUNBOOK.md 'AlgoPro takipçi halkası' "
                "kurulum adım 2)."
            )

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

            if self.is_follower_mode:
                # docs/MAINNET_PLAN.md §6: takipçi halkası (D20) mainnet'e
                # KENDİ BAŞINA terfi ETMEZ. Ölçülmüş bir kenarı yoktur (kanıt:
                # yok — testnet ölçümü kanıt olacak) ve boyutlaması scalper'ın
                # risk-tabanlı boyutlamasından farklıdır (marj %10 + dinamik
                # kaldıraç ≤100x). Testnet .env'i mainnet'e kopyalanınca bu
                # sessizce gerçek parayla çalışamaz.
                raise ValueError(
                    "GÜVENLİK HATASI: BOT_MODE=follower yalnız TESTNET'te "
                    "çalıştırılabilir. AlgoPro takipçi halkası mainnet'e kendi "
                    "başına terfi etmez (docs/MAINNET_PLAN.md §6, D20) — "
                    "BINANCE_BASE_URL'i testnet'e çevirin."
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

            # D17: "piyasa verisi ayrı host" seçeneği yalnız TESTNET'te işlem
            # yaparken (mainnet verisi + testnet emirleri) anlamlıdır. Mainnet'te
            # işlem yapılırken market-data host'u bir TESTNET host'u olamaz —
            # gerçek parayla açılan bir pozisyonun RSI/BB/rejim kararı sahte
            # (testnet) mumlardan gelemez. Boş (=işlem host'u) veya mainnet
            # host'u kabul edilir.
            if self.market_data_is_testnet:
                raise ValueError(
                    "GÜVENLİK HATASI: SCALPER_MARKET_DATA_BASE_URL bir TESTNET "
                    f"host'unu gösteriyor ({self.market_data_base_url}) ama işlemler "
                    f"MAINNET'te açılıyor ({self.binance_base_url}). Gerçek parayla "
                    "işlem testnet mumlarına dayandırılamaz — ayarı boşaltın "
                    "(=işlem host'u) veya mainnet host'u verin."
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
