"""
Telegram sinyal mesajlarını parse eden modül.
VIP kanallardan gelen sinyalleri analiz eder.
"""

import re
from typing import Optional
from src.models.signal import SignalParsed, SignalDirection
from src.core.logger import app_logger


class TelegramSignalParser:
    """Telegram sinyal parser"""
    
    def __init__(self):
        self.logger = app_logger
    
    def is_profit_message(self, message: str) -> bool:
        """
        Mesajın profit bildirimi olup olmadığını kontrol et.
        Profit mesajları işlenmez, direkt skip edilir.
        """
        profit_keywords = [
            'PROFIT', 'TARGET HIT', 'TP HIT', 'TAKE PROFIT',
            'CLOSED', 'COMPLETED', 'BREAKEVEN', 'BREAK EVEN',
            'BREAK-EVEN', 'TRAILING', 'STOPPED OUT'
        ]
        msg_upper = message.upper()
        return any(keyword in msg_upper for keyword in profit_keywords)
    
    def parse(self, message: str) -> SignalParsed:
        """
        Telegram mesajını parse et.
        
        Format:
        BTC/USDT LONG
        MARGIN: 10X
        ENTRY: <45000-46000>
        TARGETS:
        1. [47000]
        2. [48000]
        3. [49000]
        STOPLOSS: [44000]
        """
        signal = SignalParsed(raw_message=message)
        
        try:
            # Emoji ve URL'leri temizle
            clean_message = message
            clean_message = re.sub(r'[🟢🔴⚡️📊💰🎯❌️]', '', clean_message)  # Emojileri kaldır
            clean_message = re.sub(r'\(https?://[^\)]+\)', '', clean_message)  # URL'leri kaldır
            clean_message = re.sub(r'\(buy\)|\(sell\)', '', clean_message, flags=re.IGNORECASE)  # (buy)/(sell) kaldır
            clean_message = clean_message.upper().strip()
            
            # 1. Coin ve Direction (Daha esnek pattern)
            coin_match = re.search(
                r'([A-Z0-9]+)/USDT.*?(LONG|SHORT)',
                clean_message
            )
            if coin_match:
                signal.coin = coin_match.group(1)
                signal.direction = SignalDirection(coin_match.group(2))
                signal.symbol = f"{signal.coin}USDT"
                self.logger.debug(f"Coin: {signal.coin}, Direction: {signal.direction}")
            
            # 2. Margin Type ve Leverage (Yeni format)
            margin_match = re.search(
                r'MARGIN:\s*(CROSS|ISOLATED),?\s*(\d+)X',
                clean_message
            )
            if margin_match:
                signal.margin_type = margin_match.group(1)
                signal.leverage = int(margin_match.group(2))
                self.logger.debug(
                    f"Margin: {signal.margin_type}, Leverage: {signal.leverage}X"
                )
            else:
                # Eski format fallback (sadece leverage)
                leverage_match = re.search(
                    r'MARGIN:.*?(\d+)X',
                    clean_message
                )
                if leverage_match:
                    signal.leverage = int(leverage_match.group(1))
                    self.logger.debug(f"Leverage: {signal.leverage}X")
            
            # 3. Entry Range
            entry_match = re.search(
                r'ENTRY:.*?<([0-9.]+)-([0-9.]+)>',
                clean_message
            )
            if entry_match:
                signal.entry_min = float(entry_match.group(1))
                signal.entry_max = float(entry_match.group(2))
                signal.entry = (signal.entry_min + signal.entry_max) / 2
                self.logger.debug(
                    f"Entry: {signal.entry} (Range: {signal.entry_min}-{signal.entry_max})"
                )
            
            # 4. Targets
            target_matches = re.findall(
                r'\d+\.\s*\[([0-9.]+)\]',
                clean_message
            )
            if target_matches:
                signal.targets = [float(t) for t in target_matches]
                self.logger.debug(f"Targets: {signal.targets}")
            
            # 5. Stop Loss
            sl_match = re.search(
                r'STOPLOSS:.*?\[([0-9.]+)\]',
                clean_message
            )
            if sl_match:
                signal.stoploss = float(sl_match.group(1))
                self.logger.debug(f"Stop Loss: {signal.stoploss}")
            
            # Validasyon
            if self._validate_signal(signal):
                signal.parsed = True
                self.logger.info(
                    f"✅ Sinyal başarıyla parse edildi: {signal.symbol} {signal.direction}"
                )
            else:
                signal.error = "Eksik veya geçersiz alanlar"
                self.logger.warning(f"⚠️ Sinyal parse edildi ama validasyon hatası: {signal.error}")
        
        except Exception as e:
            signal.error = str(e)
            self.logger.error(f"❌ Sinyal parse hatası: {e}")
        
        return signal
    
    def _validate_signal(self, signal: SignalParsed) -> bool:
        """Sinyalin geçerli olup olmadığını kontrol et"""
        required_fields = [
            signal.coin,
            signal.direction,
            signal.leverage,
            signal.entry,
            signal.stoploss,
        ]
        
        if not all(required_fields):
            self.logger.warning("Gerekli alanlar eksik")
            return False
        
        if not signal.targets or len(signal.targets) == 0:
            self.logger.warning("Target bulunamadı")
            return False
        
        # Stop loss kontrolü
        if signal.direction == SignalDirection.LONG:
            if signal.stoploss >= signal.entry:
                self.logger.warning(
                    f"LONG için SL ({signal.stoploss}) entry'den ({signal.entry}) küçük olmalı"
                )
                return False
            
            if any(t <= signal.entry for t in signal.targets):
                self.logger.warning("LONG için tüm target'lar entry'den büyük olmalı")
                return False
        
        elif signal.direction == SignalDirection.SHORT:
            if signal.stoploss <= signal.entry:
                self.logger.warning(
                    f"SHORT için SL ({signal.stoploss}) entry'den ({signal.entry}) büyük olmalı"
                )
                return False
            
            if any(t >= signal.entry for t in signal.targets):
                self.logger.warning("SHORT için tüm target'lar entry'den küçük olmalı")
                return False
        
        # Leverage kontrolü
        if signal.leverage <= 0 or signal.leverage > 125:
            self.logger.warning(f"Geçersiz leverage: {signal.leverage}")
            return False
        
        return True
    
    def extract_coin_from_message(self, message: str) -> Optional[str]:
        """Mesajdan sadece coin ismini çıkar (hızlı kontrol için)"""
        match = re.search(r'([A-Z0-9]+)/USDT', message.upper())
        return match.group(1) if match else None

