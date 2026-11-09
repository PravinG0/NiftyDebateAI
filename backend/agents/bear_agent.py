"""
Bear (bearish) agent for NiftyDebateAI.

Analyses a :class:`MarketSnapshot` through a strictly bearish lens and
returns a :class:`BearAgentOutput` with confidence, strike recommendation,
and three supporting arguments.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from config import settings
from schemas import BearAgentOutput, MarketSnapshot

logger = logging.getLogger(__name__)

# ── System Prompt ─────────────────────────────────────────────────────────────

_BEAR_SYSTEM_PROMPT = """\
You are an elite institutional BEARISH options strategist and derivatives quantitative analyst specialising in NIFTY 50 index options.
Your mandate is to build the MOST EXHAUSTIVE, multi-dimensional BEARISH case for buying an At-The-Money (ATM) or near-ATM Put (PE) option.

## Comprehensive Multi-Dimensional Analysis Framework
You must debate and analyse across ALL possible technical, structural, and derivatives angles:

1. **Trend & Moving Average Breakdown**:
   - 20-EMA below 50-EMA spread (death cross / bearish alignment). Quantify the deficit.
   - Price trading below key short-term moving averages signalling downward momentum.

2. **Price Action & Key Levels**:
   - Rejections at intraday resistance or day high.
   - Breakdown of intermediate support or formation of lower-highs and lower-lows.
   - Proximity to breakdown triggers and downside targets.

3. **VWAP & Institutional Selling Pressure**:
   - Spot price trading at a discount below VWAP confirming institutional distribution.
   - Increased sell volume on red candles indicating aggressive supply dumping.

4. **Momentum & Exhaustion (RSI / MACD)**:
   - 14-period RSI showing exhaustion, breakdown below 50, or bearish divergence.

5. **Option Chain, Open Interest (OI) & Put-Call Ratio (PCR)**:
   - Heavy Call OI writing at overhead strikes acting as a concrete resistance ceiling.
   - Declining PCR (< 0.85 or falling) showing institutional Call sellers dominating.
   - Put unwinding (long liquidation / panic stop trigger) accelerating index decline.

6. **Volatility & Greeks (India VIX, Theta, Delta, Gamma)**:
   - Elevated or rising India VIX (>15) signaling market fear and expanding put option premiums.
   - Negative delta alignment capturing downward slides with gamma expansion on breakdowns.
   - Favorable risk:reward vs theta decay if downside velocity is expected.

7. **Multi-Timeframe Trend Confluence**:
   - Alignment between 5-min execution chart and 15-min/hourly higher timeframe bearish trend.

## Output requirements
Return ONLY a valid JSON object matching this schema (no markdown formatting, no commentary outside JSON):

{
  "bias": "BEARISH",
  "confidence": <int 1-10>,
  "recommended_strike": "<nearest ATM or 1-strike OTM PE strike, e.g. '24500PE'>",
  "arguments": [
    "<Argument 1: Trend & Moving Average Breakdown with exact numbers>",
    "<Argument 2: VWAP & Institutional Selling with exact numbers>",
    "<Argument 3: Price Action & Resistance Rejections with exact numbers>",
    "<Argument 4: Option Chain & Call OI Overhead Writing with exact numbers>",
    "<Argument 5: PCR Decline & Market Top Signals with exact numbers>",
    "<Argument 6: Volatility, Rising VIX & Greeks with exact numbers>"
  ],
  "key_levels": {
    "resistance_1": <float>,
    "resistance_2": <float>,
    "immediate_support": <float>,
    "breakdown_target": <float>
  }
}

Rules:
- Provide comprehensive arguments across ALL dimensions (at least 4 to 6+ in-depth arguments).
- Always cite the exact numbers from the market snapshot.
- Confidence must be an integer between 1 and 10 reflecting multi-indicator confluence.
- recommended_strike must be a valid strike string like "24500PE".
"""


class BearAgent:
    """Generates a bearish analysis of the current NIFTY market state."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._client = genai.Client(api_key=self._api_key)

    async def analyze(self, snapshot: MarketSnapshot) -> BearAgentOutput:
        """Run the bear analysis against Gemini and return structured output.

        Args:
            snapshot: Current market data.

        Returns:
            Validated :class:`BearAgentOutput`.
        """
        user_prompt = self._build_user_prompt(snapshot)

        models_to_try = [settings.GEMINI_MODEL, "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        last_exc = None
        for model_name in models_to_try:
            try:
                response = self._client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=_BEAR_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )

                raw_text = response.text.strip()
                logger.debug("BearAgent raw response: %s", raw_text)

                data: dict[str, Any] = json.loads(raw_text)
                output = BearAgentOutput(**data)
                logger.info(
                    "BearAgent → confidence=%d strike=%s (model=%s)",
                    output.confidence,
                    output.recommended_strike,
                    model_name,
                )
                return output

            except Exception as exc:
                last_exc = exc
                logger.warning("BearAgent attempt with %s failed: %s", model_name, exc)
                continue

        logger.error("All BearAgent models failed, last error: %s", last_exc)
        return self._fallback_output(snapshot)

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _build_user_prompt(snapshot: MarketSnapshot) -> str:
        """Serialise the snapshot into a textual prompt."""
        return (
            "Analyse the following NIFTY market data from a BEARISH perspective.\n\n"
            f"Spot Price : {snapshot.spot_price}\n"
            f"EMA-20     : {snapshot.ema_20}\n"
            f"EMA-50     : {snapshot.ema_50}\n"
            f"VWAP       : {snapshot.vwap}\n"
            f"RSI (14)   : {snapshot.rsi}\n"
            f"Volume     : {snapshot.volume}\n"
            f"ATR (14)   : {snapshot.atr}\n"
            f"India VIX  : {snapshot.vix}\n"
            f"PCR        : {snapshot.pcr}\n"
            f"CE OI Chg  : {snapshot.ce_oi_change}\n"
            f"PE OI Chg  : {snapshot.pe_oi_change}\n"
            f"HTF Trend  : {snapshot.higher_tf_trend}\n"
            f"Timestamp  : {snapshot.timestamp.isoformat()}\n"
        )

    @staticmethod
    def _fallback_output(snapshot: MarketSnapshot) -> BearAgentOutput:
        """Return a safe low-confidence fallback when Gemini call fails."""
        from market_data import calculate_atm_strike

        atm = calculate_atm_strike(snapshot.spot_price)
        return BearAgentOutput(
            bias="BEARISH",
            confidence=3,
            recommended_strike=f"{atm}PE",
            arguments=[
                f"Trend Breakdown: Spot ({snapshot.spot_price}) struggling below EMA-20 ({snapshot.ema_20}) and EMA-50 ({snapshot.ema_50}), exhibiting bearish structural alignment.",
                f"VWAP Resistance: Price discount below VWAP ({snapshot.vwap}) confirms prevailing institutional selling pressure on intraday rallies.",
                f"RSI Weakness: 14-period RSI at {snapshot.rsi} points towards loss of upside momentum and vulnerability to breakdown.",
                f"Option Chain Ceiling: Net Call OI addition of {snapshot.ce_oi_change:+,.0f} reflects heavy Call writing overhead capping upward rallies.",
                f"PCR Deterioration: Put-Call Ratio at {snapshot.pcr:.2f} signals cautious positioning and lack of aggressive put support.",
                f"Volatility & Greeks: India VIX at {snapshot.vix:.1f} signals underlying volatility risks, providing asymmetric gamma acceleration for long PE positions.",
            ],
            key_levels={
                "resistance_1": round(snapshot.ema_50, 2),
                "resistance_2": round(snapshot.spot_price + snapshot.atr, 2),
                "immediate_support": round(snapshot.spot_price - snapshot.atr, 2),
                "breakdown_target": round(snapshot.spot_price - 2 * snapshot.atr, 2),
            },
        )
