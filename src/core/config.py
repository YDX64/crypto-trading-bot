"""
Konfigürasyon yönetimi modülü.
Tüm environment variables ve uygulama ayarları burada yönetilir.
"""

import sys
import warnings
from typing import Optional
from pydantic import Field, model_validator
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
    scalper_daily_loss_limit_pct: float = 15.0 # 0 = kesici kapalı
    scalper_use_equilibrium_filter: bool = True  # LONG yalnız discount, SHORT yalnız premium
    scalper_min_rr: float = 1.2                # beklenen harman TP getirisi / SL riski alt sınırı; 0 = kapalı
    # Scalper — 2. tur deney bayrakları (2026-08-07 karar matrisi için)
    scalper_entry_mode: str = "taker"            # "maker" = limit giriş simülasyonu (backtest)
    scalper_taker_fee_pct: float = 0.05          # nominal % / bacak
    scalper_maker_fee_pct: float = 0.02
    scalper_maker_fill_timeout_candles: int = 3  # limit bu kadar mumda dolmazsa sinyal iptal
    scalper_c_allowed_regimes: str = "UP,DOWN,RANGE"  # deney: "RANGE" ile sınırla
    scalper_d_use_eqhl: bool = True              # D süpürmesi EQH/EQL kümelerine bağlı
    scalper_eqhl_tolerance_pct: float = 0.05     # pivot eşitlik eşiği (%)

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

