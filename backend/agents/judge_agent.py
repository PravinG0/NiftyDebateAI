"""
Judge (Chief Risk Officer) agent for NiftyDebateAI.

Evaluates bull and bear arguments against raw market data, defaults to
NO_TRADE unless one side presents overwhelming confluence, and outputs
a :class:`JudgeDecision` with strike, SL, target, and risk-reward.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from config import settings
from schemas import BearAgentOutput, BullAgentOutput, JudgeDecision, MarketSnapshot

logger = logging.getLogger(__name__)

# ── System Prompt ─────────────────────────────────────────────────────────────

_JUDGE_SYSTEM_PROMPT = """\
You are the CHIEF RISK OFFICER and impartial JUDGE for a NIFTY options trading desk.
You are presented with:
  1. Raw market data (the ground truth).
  2. A BULLISH analyst's argument and confidence score.
  3. A BEARISH analyst's argument and confidence score.

## Your mandate

- Your DEFAULT decision is **NO_TRADE**. Capital preservation is paramount.
- Only approve a trade (BUY_CE or BUY_PE) when one side demonstrates
  **OVERWHELMING confluence**: confidence >= 8 AND multi-timeframe alignment
  AND the raw data supports their claims.

## Evaluation checklist

1. **Cross-verify** each argument against the raw data — reject any claim that
   misrepresents the numbers.
2. **VIX / IV risk**: VIX > 20 means elevated premiums; buying options is risky
   due to potential IV crush, even if direction is correct.
3. **Confidence threshold**: Only consider a side if confidence >= 8.
4. **Multi-timeframe alignment**: The higher_tf_trend must agree with the
   proposed direction.
5. **Risk management** (ATR-based):
   - Stop-Loss = Entry - 1.2 × ATR_equivalent (for option premium proxy)
   - Target    = Entry + 2.0 × ATR_equivalent
   - ATR_equivalent for option premium ≈ ATR × 0.5 (delta approximation for ATM)
   - Compute risk:reward ratio = (target - entry) / (entry - stop_loss).
     Format as "1:X.X".

## Output requirements

Return ONLY a JSON object matching this exact schema (no markdown, no explanation):

{
  "decision": "BUY_CE" | "BUY_PE" | "NO_TRADE",
  "selected_strike": "<strike string or null for NO_TRADE>",
  "entry_price_estimate": <float — estimated option premium>,
  "stop_loss": <float>,
  "target": <float>,
  "risk_reward_ratio": "<string e.g. '1:2.0'>",
  "winning_argument_summary": "<one-paragraph summary of why this side won, or why no trade>",
  "risk_warning": "<key risk to watch if trade is taken, or reason for passing>"
}

Rules:
- If decision is NO_TRADE, set selected_strike to null, entry_price_estimate/stop_loss/target
  to 0, risk_reward_ratio to "N/A".
- winning_argument_summary and risk_warning must always be non-empty.
"""


class JudgeAgent:
    """Impartial judge that evaluates bull vs bear and decides on a trade."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._client = genai.Client(api_key=self._api_key)

    async def evaluate(
        self,
        snapshot: MarketSnapshot,
        bull_output: BullAgentOutput,
        bear_output: BearAgentOutput,
    ) -> JudgeDecision:
        """Run the judge evaluation and return a structured decision.

        Args:
            snapshot: Raw market data.
            bull_output: Bullish agent's analysis.
            bear_output: Bearish agent's analysis.

        Returns:
            Validated :class:`JudgeDecision`.
        """
        user_prompt = self._build_user_prompt(snapshot, bull_output, bear_output)

        models_to_try = [settings.GEMINI_MODEL, "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        last_exc = None
        for model_name in models_to_try:
            try:
                response = self._client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=_JUDGE_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )

                raw_text = response.text.strip()
                logger.debug("JudgeAgent raw response: %s", raw_text)

                data: dict[str, Any] = json.loads(raw_text)
                decision = JudgeDecision(**data)
                logger.info(
                    "JudgeAgent → decision=%s strike=%s RR=%s (model=%s)",
                    decision.decision,
                    decision.selected_strike,
                    decision.risk_reward_ratio,
                    model_name,
                )
                return decision

            except Exception as exc:
                last_exc = exc
                logger.warning("JudgeAgent attempt with %s failed: %s", model_name, exc)
                continue

        logger.error("All JudgeAgent models failed, last error: %s", last_exc)
        return self._fallback_decision()

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _build_user_prompt(
        snapshot: MarketSnapshot,
        bull: BullAgentOutput,
        bear: BearAgentOutput,
    ) -> str:
        """Build the combined prompt for the judge, including live option chain pricing."""
        from market_data import calculate_option_premium, parse_option_symbol

        bull_stk, bull_typ = parse_option_symbol(bull.recommended_strike or "24500CE")
        bull_ltp = calculate_option_premium(snapshot.spot_price, bull_stk, bull_typ, snapshot.vix)

        bear_stk, bear_typ = parse_option_symbol(bear.recommended_strike or "24500PE")
        bear_ltp = calculate_option_premium(snapshot.spot_price, bear_stk, bear_typ, snapshot.vix)

        return (
            "## RAW MARKET DATA\n"
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
            f"HTF Trend  : {snapshot.higher_tf_trend}\n\n"
            "## LIVE OPTION CHAIN PRICES (EXACT CONTRACT PREMIUMS)\n"
            f"Bull Contract ({bull.recommended_strike}): Live LTP = ₹{bull_ltp:.2f} (1 Lot = ₹{bull_ltp*25:.2f})\n"
            f"Bear Contract ({bear.recommended_strike}): Live LTP = ₹{bear_ltp:.2f} (1 Lot = ₹{bear_ltp*25:.2f})\n\n"
            "## BULLISH ANALYST\n"
            f"Confidence : {bull.confidence}/10\n"
            f"Strike     : {bull.recommended_strike} (LTP: ₹{bull_ltp:.2f})\n"
            f"Arguments  :\n"
            + "\n".join(f"  {i+1}. {a}" for i, a in enumerate(bull.arguments))
            + f"\nKey Levels : {json.dumps(bull.key_levels)}\n\n"
            "## BEARISH ANALYST\n"
            f"Confidence : {bear.confidence}/10\n"
            f"Strike     : {bear.recommended_strike} (LTP: ₹{bear_ltp:.2f})\n"
            f"Arguments  :\n"
            + "\n".join(f"  {i+1}. {a}" for i, a in enumerate(bear.arguments))
            + f"\nKey Levels : {json.dumps(bear.key_levels)}\n\n"
            "Evaluate both sides against the raw data and render your decision. "
            "If approving a trade, set entry_price_estimate to the EXACT Live Option Chain LTP provided above."
        )

    @staticmethod
    def _fallback_decision() -> JudgeDecision:
        """Conservative NO_TRADE fallback when Gemini call fails."""
        return JudgeDecision(
            decision="NO_TRADE",
            selected_strike=None,
            entry_price_estimate=0.0,
            stop_loss=0.0,
            target=0.0,
            risk_reward_ratio="N/A",
            winning_argument_summary=(
                "Judge evaluation could not be completed due to an error. "
                "Defaulting to NO_TRADE to preserve capital."
            ),
            risk_warning="System error — manual review recommended.",
        )
