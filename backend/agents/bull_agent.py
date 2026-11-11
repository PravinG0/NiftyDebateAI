"""
Bull (bullish) agent for NiftyDebateAI.

Analyses a :class:`MarketSnapshot` through a strictly bullish lens and
returns a :class:`BullAgentOutput` with confidence, strike recommendation,
and three supporting arguments.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from config import settings
from schemas import BullAgentOutput, MarketSnapshot

logger = logging.getLogger(__name__)

# ── System Prompt ─────────────────────────────────────────────────────────────

_BULL_SYSTEM_PROMPT = """\
You are an elite institutional BULLISH options strategist and derivatives quantitative analyst specialising in NIFTY 50 index options.
Your mandate is to build the MOST EXHAUSTIVE, multi-dimensional BULLISH case for buying an At-The-Money (ATM) or near-ATM Call (CE) option.

## Comprehensive Multi-Dimensional Analysis Framework
You must debate and analyse across ALL possible technical, structural, and derivatives angles:

1. **Trend & Moving Average Alignment**:
   - 20-EMA vs 50-EMA spread and slope. Quantify the gap.
   - Price position relative to key moving averages confirming bullish structure.

2. **Price Action & Key Levels**:
   - Defending intraday support / day low.
   - Structure of higher-highs and higher-lows.
   - Proximity to immediate resistance and breakout velocity targets.

3. **VWAP & Institutional Order Flow**:
   - Spot price premium over VWAP confirming institutional buyer absorption.
   - Volume expansion on upward candles indicating aggressive market orders.

4. **Momentum & Oscillators (RSI / MACD)**:
   - 14-period RSI trajectory, absence of negative divergence, room before extreme overbought levels.

5. **Option Chain, Open Interest (OI) & Put-Call Ratio (PCR)**:
   - Put OI additions at support strikes (Put writers providing cushion/floor).
   - PCR (> 1.0 or rising) showing aggressive Put selling by smart money.
   - Call short-covering / Call OI unwinding potential accelerating gamma.

6. **Volatility & Greeks (India VIX, Theta, Delta, Gamma)**:
   - India VIX level (<16) providing cheap entry premiums and asymmetric upside.
   - High positive delta on ATM CE capturing sharp index moves.
   - Favorable risk:reward against intraday theta decay.

7. **Central Pivot Range (CPR) & Supertrend Strategy**:
   - CPR Pivot, BC, TC levels: Spot holding above CPR Top Central (TC) confirms institutional bullish accumulation.
   - CPR Width: "NARROW_TRENDING" signals imminent high-probability trending expansion day; "WIDE_RANGEBOUND" signals support bounce.
   - Supertrend (10, 3): Confluence when Supertrend is BULLISH and providing a tight trailing stop floor.

8. **Institutional OI Walls, Max Pain & Market Breadth**:
   - Put OI Wall strike acts as a rock-solid institutional support floor.
   - Max Pain strike pull: Upward gravity if spot is below or near Max Pain.
   - Heavyweights & Breadth: Positive Advance/Decline ratio and Reliance/HDFC Bank alignment confirming market-wide participation.

9. **Multi-Timeframe Trend Confluence**:
   - Alignment between 5-min execution chart and 15-min/hourly higher timeframe trend.

## Output requirements
Return ONLY a valid JSON object matching this schema (no markdown formatting, no commentary outside JSON):

{
  "bias": "BULLISH",
  "confidence": <int 1-10>,
  "recommended_strike": "<nearest ATM or 1-strike OTM CE strike, e.g. '24500CE'>",
  "arguments": [
    "<Argument 1: Trend & Moving Averages with exact numbers>",
    "<Argument 2: VWAP & Institutional Order Flow with exact numbers>",
    "<Argument 3: Price Action & Support Defense with exact numbers>",
    "<Argument 4: Option Chain & Put OI Writing with exact numbers>",
    "<Argument 5: PCR & Market Sentiment with exact numbers>",
    "<Argument 6: Volatility, VIX & Greeks with exact numbers>"
  ],
  "key_levels": {
    "support_1": <float>,
    "support_2": <float>,
    "immediate_resistance": <float>,
    "breakout_target": <float>
  }
}

Rules:
- Provide comprehensive arguments across ALL dimensions (at least 4 to 6+ in-depth arguments).
- Always cite the exact numbers from the market snapshot.
- Confidence must be an integer between 1 and 10 reflecting multi-indicator confluence.
- recommended_strike must be a valid strike string like "24500CE".
"""


class BullAgent:
    """Generates a bullish analysis of the current NIFTY market state."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._client = genai.Client(api_key=self._api_key)

    async def analyze(self, snapshot: MarketSnapshot) -> BullAgentOutput:
        """Run the bull analysis against Gemini and return structured output.

        Args:
            snapshot: Current market data.

        Returns:
            Validated :class:`BullAgentOutput`.
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
                        system_instruction=_BULL_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )

                raw_text = response.text.strip()
                logger.debug("BullAgent raw response: %s", raw_text)

                data: dict[str, Any] = json.loads(raw_text)
                output = BullAgentOutput(**data)
                logger.info(
                    "BullAgent → confidence=%d strike=%s (model=%s)",
                    output.confidence,
                    output.recommended_strike,
                    model_name,
                )
                return output

            except Exception as exc:
                last_exc = exc
                logger.warning("BullAgent attempt with %s failed: %s", model_name, exc)
                continue

        logger.error("All BullAgent models failed, last error: %s", last_exc)
        return self._fallback_output(snapshot)

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _build_user_prompt(snapshot: MarketSnapshot) -> str:
        """Serialise the snapshot into a textual prompt."""
        return (
            "Analyse the following NIFTY market data from a BULLISH perspective.\n\n"
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
            f"CPR Pivot  : {snapshot.cpr_pivot} (BC: {snapshot.cpr_bc}, TC: {snapshot.cpr_tc}, Regime: {snapshot.cpr_width_type})\n"
            f"Supertrend : {snapshot.supertrend_direction} (Level: {snapshot.supertrend_value})\n"
            f"Max Pain   : {snapshot.max_pain}\n"
            f"Put OI Wall: {snapshot.put_oi_wall} (Institutional Support)\n"
            f"Call OI Wall: {snapshot.call_oi_wall} (Institutional Resistance)\n"
            f"Breadth    : A/D Ratio {snapshot.advance_decline_ratio}, Heavyweights: {snapshot.heavyweights_alignment}\n"
            f"Institutions: FII {snapshot.fii_sentiment}, Global {snapshot.global_sentiment}\n"
            f"Timestamp  : {snapshot.timestamp.isoformat()}\n"
        )

    @staticmethod
    def _fallback_output(snapshot: MarketSnapshot) -> BullAgentOutput:
        """Return a safe low-confidence fallback when Gemini call fails."""
        from market_data import calculate_atm_strike

        atm = calculate_atm_strike(snapshot.spot_price)
        return BullAgentOutput(
            bias="BULLISH",
            confidence=3,
            recommended_strike=f"{atm}CE",
            arguments=[
                f"Trend Alignment: Spot ({snapshot.spot_price}) holding above EMA-20 ({snapshot.ema_20}) and EMA-50 ({snapshot.ema_50}), reflecting positive short-term momentum.",
                f"VWAP Dynamics: Price at {snapshot.spot_price} vs VWAP {snapshot.vwap} indicates institutional buyer support holding near average price.",
                f"RSI Momentum: 14-period RSI at {snapshot.rsi} indicates healthy room for upside expansion before reaching overbought threshold.",
                f"Option Chain Support: Net Put OI change of {snapshot.pe_oi_change:+,.0f} reflects active Put writing creating a safety floor at ATM strikes.",
                f"PCR Context: Put-Call Ratio at {snapshot.pcr:.2f} confirms bullish derivative positioning and Put-heavy open interest.",
                f"Volatility & Greeks: India VIX at {snapshot.vix:.1f} keeps option premiums affordable, offering high asymmetric delta capture against intraday theta.",
            ],
            key_levels={
                "support_1": round(snapshot.ema_20, 2),
                "support_2": round(snapshot.ema_50, 2),
                "immediate_resistance": round(snapshot.spot_price + snapshot.atr, 2),
                "breakout_target": round(snapshot.spot_price + 2 * snapshot.atr, 2),
            },
        )
