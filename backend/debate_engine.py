"""
Debate engine for NiftyDebateAI.

Orchestrates a single debate round:
1. Fetch market snapshot
2. Run Bull agent analysis
3. Run Bear agent analysis
4. Run Judge evaluation
5. Persist to database
6. Optionally create a paper trade
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents import BearAgent, BullAgent, JudgeAgent
from config import (
    BUDGET_LIMIT,
    INDEX_CONFIG,
    BudgetExceededError,
    get_index_config,
    validate_paper_trade_guardrails,
)
from database import DebateSessionModel, PaperTradeModel, async_session_factory
from market_data import (
    build_market_snapshot,
    calculate_atm_strike,
    get_nearest_expiry,
    get_openalgo_client,
)
from openalgo_client import OpenAlgoClient, SafetyError
from schemas import (
    BearAgentOutput,
    BullAgentOutput,
    DebateSession,
    JudgeDecision,
    MarketSnapshot,
    PaperTrade,
)
from time_utils import IST, ist_now

logger = logging.getLogger(__name__)


class DebateEngine:
    """Runs a full bull-vs-bear-vs-judge debate cycle."""

    def __init__(
        self,
        openalgo_client: Optional[OpenAlgoClient] = None,
        bull_agent: Optional[BullAgent] = None,
        bear_agent: Optional[BearAgent] = None,
        judge_agent: Optional[JudgeAgent] = None,
    ) -> None:
        self.bull = bull_agent or BullAgent()
        self.bear = bear_agent or BearAgent()
        self.judge = judge_agent or JudgeAgent()
        self.openalgo = openalgo_client

    async def _get_openalgo_client(self) -> OpenAlgoClient:
        """Return the injected OpenAlgo client or default singleton."""
        if self.openalgo is not None:
            return self.openalgo
        return await get_openalgo_client()

    async def run_debate(
        self,
        symbol: str | MarketSnapshot = "NIFTY",
        snapshot: Optional[MarketSnapshot] = None,
    ) -> DebateSession:
        """Execute one complete debate round and return the result.

        Steps:
            1. Resolve symbol and build a market snapshot (or use provided snapshot).
            2. Run Bull agent analysis.
            3. Run Bear agent analysis sequentially.
            4. Feed both outputs to the Judge evaluation.
            5. Persist the debate session to the database audit trail.
            6. If the judge approves a trade, verify analyzer mode and create a paper trade entry.

        Returns:
            A fully populated :class:`DebateSession`.
        """
        if isinstance(symbol, MarketSnapshot):
            snapshot = symbol
            sym = "BANKNIFTY" if snapshot.spot_price > 35000 else "NIFTY"
        else:
            sym = (symbol or "NIFTY").upper()
            sym = "BANKNIFTY" if "BANK" in sym else "NIFTY"

        logger.info("═══ Starting debate round for %s ═══", sym)

        # 1 ─ Market data ──────────────────────────────────────────────────
        if snapshot is None:
            snapshot = await build_market_snapshot(symbol=sym)
        logger.info("Snapshot ready — symbol=%s spot=%.2f", sym, snapshot.spot_price)

        # 2 ─ Bull & Bear analysis (Sequential) ────────────────────────────
        bull_output: BullAgentOutput = await self.bull.analyze(snapshot)
        logger.info(
            "Bull analysis complete — confidence=%d strike=%s",
            bull_output.confidence,
            bull_output.recommended_strike,
        )

        bear_output: BearAgentOutput = await self.bear.analyze(snapshot)
        logger.info(
            "Bear analysis complete — confidence=%d strike=%s",
            bear_output.confidence,
            bear_output.recommended_strike,
        )

        # 3 ─ Judge evaluation ─────────────────────────────────────────────
        decision: JudgeDecision = await self.judge.evaluate(
            snapshot, bull_output, bear_output,
        )
        logger.info("Judge decision: %s", decision.decision)

        # 4 ─ Persist to DB ────────────────────────────────────────────────
        debate_session = DebateSession(
            timestamp=ist_now(),
            market_snapshot=snapshot,
            bull_output=bull_output,
            bear_output=bear_output,
            judge_decision=decision,
            index_symbol=sym,
        )

        async with async_session_factory() as session:
            db_row = DebateSessionModel(
                timestamp=debate_session.timestamp,
                spot_price=snapshot.spot_price,
                index_symbol=sym,
                market_data_json=snapshot.model_dump_json(),
                bull_reasoning=json.dumps(bull_output.arguments),
                bull_confidence=bull_output.confidence,
                bull_strike=bull_output.recommended_strike,
                bear_reasoning=json.dumps(bear_output.arguments),
                bear_confidence=bear_output.confidence,
                bear_strike=bear_output.recommended_strike,
                judge_decision=decision.decision,
                judge_reasoning=decision.winning_argument_summary,
                judge_full_output=decision.model_dump_json(),
            )
            session.add(db_row)
            await session.commit()
            await session.refresh(db_row)
            debate_session.id = db_row.id
            logger.info("Debate #%d persisted to audit trail.", db_row.id)

            # 5 ─ Create paper trade if approved ───────────────────────────
            if decision.decision != "NO_TRADE":
                try:
                    await self._create_paper_trade(session, db_row.id, decision, snapshot, symbol=sym)
                except BudgetExceededError as exc:
                    logger.warning("Paper trade blocked by budget guardrail: %s", exc)

        logger.info("═══ Debate round complete ═══")
        return debate_session

    # ── Private helpers ───────────────────────────────────────────────────

    async def _create_paper_trade(
        self,
        session: AsyncSession,
        debate_id: int,
        decision: JudgeDecision,
        snapshot: MarketSnapshot,
        symbol: str = "NIFTY",
    ) -> None:
        """Insert a paper trade row based on the judge's decision with 1-lot and budget enforcement."""
        client = await self._get_openalgo_client()

        # Critical Safety Invariant check
        try:
            is_analyzer = await client.check_analyzer_mode()
        except Exception as exc:
            logger.critical(
                "CRITICAL SAFETY VIOLATION: Failed to query OpenAlgo analyzer mode: %s. "
                "Live trading is strictly blocked.",
                exc,
            )
            raise SafetyError(
                "OpenAlgo is not in analyzer mode — live trading is strictly blocked."
            ) from exc

        if is_analyzer is not True:
            logger.critical(
                "CRITICAL SAFETY VIOLATION: OpenAlgo is not in analyzer mode (got %r). "
                "Live trading is strictly blocked.",
                is_analyzer,
            )
            raise SafetyError(
                "OpenAlgo is not in analyzer mode — live trading is strictly blocked."
            )

        sym = (symbol or "NIFTY").upper()
        if "BANK" in sym or snapshot.spot_price > 35000:
            sym = "BANKNIFTY"
        else:
            sym = "NIFTY"

        cfg = INDEX_CONFIG.get(sym, INDEX_CONFIG["NIFTY"])
        strike_step = cfg["strike_step"]
        lot_size = cfg["lot_size"]

        option_type = "CE" if decision.decision == "BUY_CE" else "PE"
        strike = decision.selected_strike or f"{calculate_atm_strike(snapshot.spot_price, step=strike_step)}{option_type}"
        expiry = get_nearest_expiry()

        # Build NSE-style symbol
        strike_num = "".join(c for c in strike if c.isdigit())
        if "-" in str(expiry):
            expiry_fmt = datetime.strptime(str(expiry), "%Y-%m-%d").strftime("%y%m%d")
        else:
            expiry_fmt = str(expiry)
        option_symbol = f"{sym}{expiry_fmt}{strike_num}{option_type}"

        # Try to get a real LTP; fall back to estimate
        entry_price = decision.entry_price_estimate
        if entry_price <= 0:
            entry_price = await client.get_ltp(option_symbol)
        if entry_price <= 0:
            entry_price = 100.0

        # Enforce 1-lot, Rs 5,000 budget, max Rs 700 SL, and max Rs 1,000 Target guardrail
        from config import compute_clamped_sl_target
        clamped_sl, clamped_tgt = compute_clamped_sl_target(sym, entry_price, decision.stop_loss, decision.target)

        guardrail = validate_paper_trade_guardrails(
            sym,
            entry_price=entry_price,
            lots=1,
            stop_loss=clamped_sl,
            target=clamped_tgt,
        )
        quantity = guardrail["quantity"]
        outlay = guardrail["outlay"]

        await client.place_order(
            symbol=option_symbol,
            qty=quantity,
            side="BUY",
            order_type="MARKET",
        )

        trade = PaperTradeModel(
            debate_id=debate_id,
            option_symbol=option_symbol,
            trade_type=decision.decision,
            entry_price=entry_price,
            stop_loss=clamped_sl,
            target=clamped_tgt,
            status="OPEN",
            opened_at=ist_now(),
            quantity=quantity,
            index_symbol=sym,
            trade_source="AI_DEBATE",
        )
        session.add(trade)
        await session.commit()
        logger.info(
            "Paper trade created and committed: %s %s entry=%.2f qty=%d outlay=%.2f SL=%.2f TGT=%.2f (debate_id=%d)",
            decision.decision,
            option_symbol,
            entry_price,
            int(quantity),
            outlay,
            decision.stop_loss,
            decision.target,
            debate_id,
        )


