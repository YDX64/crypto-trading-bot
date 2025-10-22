"""
DeepSeek Reasoner v3.2 kullanarak sinyal analizi yapan modül.
3 farklı perspektiften analiz yapıp konsensüs oluşturur.
DeepSeek Reasoner birincil, Gemini yedek AI olarak kullanılır.
"""

import asyncio
import json
from typing import Tuple
from openai import AsyncOpenAI
import google.generativeai as genai

from src.models.signal import SignalParsed, SignalAnalyzed
from src.core.config import settings
from src.core.logger import app_logger
from src.core.rate_limiter import rate_limiter


class AIAnalyzer:
    """DeepSeek Reasoner powered sinyal analiz motoru"""

    def __init__(self):
        # DeepSeek (Primary) - OpenAI SDK ile uyumlu
        self.deepseek_client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )
        self.deepseek_model = settings.deepseek_model

        # Gemini (Fallback)
        genai.configure(api_key=settings.gemini_api_key)
        self.gemini_model = genai.GenerativeModel(settings.gemini_model)

        self.logger = app_logger

    async def analyze_signal(self, signal: SignalParsed) -> SignalAnalyzed:
        """
        Sinyali 3 farklı AI perspektifinden analiz et ve konsensüs oluştur.
        DeepSeek'in reasoning yeteneğini kullanarak derin analiz yap.

        Args:
            signal: Parse edilmiş sinyal

        Returns:
            AI analizi eklenmiş sinyal
        """
        self.logger.info(f"🤖 DeepSeek AI analizi başlatılıyor: {signal.symbol} {signal.direction}")

        # 3 farklı analizi SIRALIOLARAK çalıştır
        self.logger.debug("Analiz 1/3: Technical Analysis")
        analysis_1 = await self._technical_analysis(signal)
        await asyncio.sleep(1.5)  # Rate limit koruması

        self.logger.debug("Analiz 2/3: Risk Analysis")
        analysis_2 = await self._risk_analysis(signal)
        await asyncio.sleep(1.5)

        self.logger.debug("Analiz 3/3: Sentiment Analysis")
        analysis_3 = await self._sentiment_analysis(signal)

        # Konsensüs oluştur
        bullish_count, bearish_count = self._count_verdicts(
            analysis_1, analysis_2, analysis_3
        )

        verdict = "BULLISH" if bullish_count > bearish_count else "BEARISH"

        # Trend uyumluluğunu kontrol et
        direction = signal.direction.value if signal.direction else ""
        trend_aligned = (
            (verdict == "BULLISH" and direction == "LONG") or
            (verdict == "BEARISH" and direction == "SHORT")
        )

        # Confidence level
        confidence = self._calculate_confidence(bullish_count, bearish_count)

        analyzed = SignalAnalyzed(
            signal=signal,
            ai_verdict=verdict,
            trend_aligned=trend_aligned,
            bullish_votes=bullish_count,
            bearish_votes=bearish_count,
            consensus=f"{bullish_count} BULLISH vs {bearish_count} BEARISH",
            analysis_1=analysis_1[:200],
            analysis_2=analysis_2[:200],
            analysis_3=analysis_3[:200],
            confidence=confidence,
        )

        if trend_aligned:
            self.logger.info(
                f"✅ Trend uyumlu! AI: {verdict}, Sinyal: {direction}, "
                f"Oy: {bullish_count}B-{bearish_count}Be, Güven: {confidence}"
            )
        else:
            self.logger.warning(
                f"⚠️ Trend uyumsuz! AI: {verdict}, Sinyal: {direction}, "
                f"Oy: {bullish_count}B-{bearish_count}Be"
            )

        return analyzed

    async def _call_ai(self, prompt: str, analysis_type: str) -> str:
        """
        AI'ya call yap. Önce DeepSeek Reasoner, hata verirse Gemini kullan.
        DeepSeek'in Chain-of-Thought (CoT) reasoning özelliğini kullanır.
        """
        # Rate limiting
        await rate_limiter.wait_for_openai()

        # 1. DeepSeek Reasoner Dene
        try:
            self.logger.debug(f"{analysis_type}: DeepSeek Reasoner deneniyor...")

            response = await self.deepseek_client.chat.completions.create(
                model=self.deepseek_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert crypto trading analyst with deep knowledge of technical analysis, risk management, and market sentiment. Provide thorough reasoning for your analysis."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=500,  # Reasoning için daha fazla token
                stream=False
            )

            # DeepSeek'in reasoning_content'i varsa logla
            if hasattr(response.choices[0].message, 'reasoning_content'):
                reasoning = response.choices[0].message.reasoning_content
                self.logger.debug(f"{analysis_type} Reasoning: {reasoning[:200]}...")

            result = response.choices[0].message.content
            self.logger.info(f"{analysis_type}: ✅ DeepSeek Reasoner başarılı")
            return result

        except Exception as e:
            self.logger.warning(f"{analysis_type}: ⚠️ DeepSeek hatası: {e}")
            self.logger.info(f"{analysis_type}: 🔄 Gemini'ye geçiliyor...")

            # 2. Gemini Fallback
            try:
                await asyncio.sleep(1)  # Gemini rate limit
                response = await asyncio.to_thread(
                    self.gemini_model.generate_content, prompt
                )

                result = response.text
                self.logger.info(f"{analysis_type}: ✅ Gemini başarılı")
                return result

            except Exception as gemini_error:
                self.logger.error(f"{analysis_type}: ❌ Gemini hatası: {gemini_error}")
                return "VERDICT: BEARISH\nREASONING: Both AI models failed - safety first"

    async def _technical_analysis(self, signal: SignalParsed) -> str:
        """Teknik analiz perspektifi - DeepSeek'in derin analiz yeteneğini kullan"""
        prompt = f"""Analyze this crypto trading signal for {signal.coin}USDT with deep technical analysis reasoning.

Signal Details:
- Direction: {signal.direction}
- Entry Price: {signal.entry}
- Stop Loss: {signal.stoploss}
- Take Profit Targets: {signal.targets}
- Leverage: {signal.leverage}x

Please analyze:
1. Support and resistance levels around the entry point
2. Risk/Reward ratio calculation
3. Momentum indicators and trend strength
4. Volume analysis and market structure
5. Key technical patterns

Based on your deep technical analysis, determine if this trade setup is BULLISH or BEARISH.

Respond in this exact format:
VERDICT: [BULLISH or BEARISH]
REASONING: [Your comprehensive technical analysis in 3-4 sentences]"""

        return await self._call_ai(prompt, "Technical Analysis")

    async def _risk_analysis(self, signal: SignalParsed) -> str:
        """Risk yönetimi perspektifi - DeepSeek'in risk değerlendirmesi"""
        prompt = f"""Evaluate this {signal.coin}USDT {signal.direction} trade from a comprehensive risk management perspective.

Trade Parameters:
- Entry: {signal.entry}
- Stop Loss: {signal.stoploss}
- Targets: {signal.targets}
- Leverage: {signal.leverage}x

Analyze the following risk factors:
1. Position sizing and leverage appropriateness
2. Stop loss placement and invalidation levels
3. Take profit targets realism
4. Market volatility and liquidity conditions
5. Overall risk/reward assessment

Determine if the risk profile makes this trade BULLISH or BEARISH.

Respond in this exact format:
VERDICT: [BULLISH or BEARISH]
REASONING: [Your detailed risk assessment in 3-4 sentences]"""

        return await self._call_ai(prompt, "Risk Analysis")

    async def _sentiment_analysis(self, signal: SignalParsed) -> str:
        """Market sentiment perspektifi - DeepSeek'in piyasa anlayışı"""
        prompt = f"""Provide final validation for {signal.coin}USDT {signal.direction} trade based on market sentiment and broader context.

Consider:
1. Overall crypto market sentiment and Bitcoin correlation
2. News, events, or catalysts affecting {signal.coin}
3. Funding rates and open interest trends
4. Whale activity and smart money flows
5. Social sentiment and fear/greed index

Based on comprehensive market sentiment analysis, is this setup BULLISH or BEARISH?

Respond in this exact format:
VERDICT: [BULLISH or BEARISH]
REASONING: [Your market sentiment analysis in 3-4 sentences]"""

        return await self._call_ai(prompt, "Sentiment Analysis")

    def _count_verdicts(self, *analyses: str) -> Tuple[int, int]:
        """Analizlerdeki BULLISH/BEARISH sayılarını say"""
        bullish_count = sum(1 for analysis in analyses if "VERDICT: BULLISH" in analysis.upper())
        bearish_count = sum(1 for analysis in analyses if "VERDICT: BEARISH" in analysis.upper())

        # Hiçbiri bulunamazsa, metinde BULLISH/BEARISH geçmesine bak
        if bullish_count == 0 and bearish_count == 0:
            bullish_count = sum(1 for analysis in analyses if "BULLISH" in analysis.upper())
            bearish_count = sum(1 for analysis in analyses if "BEARISH" in analysis.upper())

        return bullish_count, bearish_count

    def _calculate_confidence(self, bullish_count: int, bearish_count: int) -> str:
        """Güven seviyesini hesapla"""
        total = bullish_count + bearish_count

        if total == 0:
            return 'Low'

        majority = max(bullish_count, bearish_count)

        if majority == total:  # Oybirliği
            return 'High'
        elif majority >= total * 0.66:  # 2/3 çoğunluk
            return 'Medium'
        else:
            return 'Low'