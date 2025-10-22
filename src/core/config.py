"""
Konfigürasyon yönetimi modülü.
Tüm environment variables ve uygulama ayarları burada yönetilir.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Uygulama ayarları"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )
    
    # Binance Configuration
    binance_api_key: str
    binance_api_secret: str
    binance_base_url: str = "https://fapi.binance.com"
    
    # Telegram Configuration
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_vip_channel_id: Optional[str] = None
    
    # OpenAI Configuration
    openai_api_key: str
    openai_model: str = "gpt-4o"
    
    # Gemini (Fallback)
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash-exp"

    # DeepSeek Configuration
    deepseek_api_key: str
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
    jwt_secret: str
    api_key: Optional[str] = None
    
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
    
    @property
    def is_production(self) -> bool:
        """Production ortamında mı?"""
        return self.app_env.lower() == "production"
    
    @property
    def is_testnet(self) -> bool:
        """Testnet kullanılıyor mu?"""
        return "testnet" in self.binance_base_url.lower()


# Global settings instance
settings = Settings()

