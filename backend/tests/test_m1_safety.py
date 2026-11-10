"""Unit and integration tests for Milestone 1:
1. Sequential execution order (Bull -> Bear -> Judge).
2. Critical Safety Invariant enforcement (Analyzer mode check & blocking).
3. Audit trail persistence and foreign key linkage (DebateSession -> PaperTrade).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, select

# Ensure backend directory is in sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import (
    DebateSessionModel,
    PaperTradeModel,
    async_session_factory,
    create_tables,
    engine,
)
from debate_engine import DebateEngine
from openalgo_client import OpenAlgoClient, SafetyError
from schemas import (
    BearAgentOutput,
    BullAgentOutput,
    JudgeDecision,
    MarketSnapshot,
)


# ── Test Helpers & Fixtures ───────────────────────────────────────────────────

def make_market_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        spot_price=24500.0,
        ema_20=24480.0,
        ema_50=24450.0,
        vwap=24510.0,
        rsi=55.0,
        volume=1200000.0,
        atr=110.0,
        vix=13.5,
        pcr=1.1,
        ce_oi_change=150000.0,
        pe_oi_change=250000.0,
        higher_tf_trend="BULLISH",
    )


def make_bull_output() -> BullAgentOutput:
    return BullAgentOutput(
        bias="BULLISH",
        confidence=8,
        recommended_strike="24550CE",
        arguments=[
            "Spot is trading cleanly above both 20 EMA and 50 EMA on 5m chart.",
            "PCR at 1.10 indicates dominant put writing and firm base.",
            "Rising volume on bullish candles confirms momentum expansion.",
        ],
        key_levels={"support": 24450.0, "resistance": 24600.0},
    )


def make_bear_output() -> BearAgentOutput:
    return BearAgentOutput(
        bias="BEARISH",
        confidence=4,
        recommended_strike="24450PE",
        arguments=[
            "Spot is hovering near intraday VWAP resistance line.",
            "RSI at 55 shows mild momentum deceleration in current leg.",
            "Call OI buildup at 24600 strike may cap near-term upside.",
        ],
        key_levels={"resistance": 24550.0, "support": 24400.0},
    )


def make_judge_decision(decision: str = "BUY_CE") -> JudgeDecision:
    if decision == "BUY_CE":
        return JudgeDecision(
            decision="BUY_CE",
            selected_strike="24550CE",
            entry_price_estimate=135.0,
            stop_loss=105.0,
            target=195.0,
            risk_reward_ratio="1:2.0",
            winning_argument_summary="Bullish thesis stronger with EMA alignment and supportive PCR.",
            risk_warning="Watch for sudden rejection at 24600 resistance.",
        )
    elif decision == "BUY_PE":
        return JudgeDecision(
            decision="BUY_PE",
            selected_strike="24450PE",
            entry_price_estimate=120.0,
            stop_loss=95.0,
            target=170.0,
            risk_reward_ratio="1:2.0",
            winning_argument_summary="Bearish breakdown thesis valid on sustained selling pressure.",
            risk_warning="Support at 24400 may induce short-covering bounce.",
        )
    else:
        return JudgeDecision(
            decision="NO_TRADE",
            selected_strike=None,
            entry_price_estimate=0.0,
            stop_loss=0.0,
            target=0.0,
            risk_reward_ratio="N/A",
            winning_argument_summary="Choppy market conditions; neither side has conclusive edge.",
            risk_warning="High chop risk with theta erosion.",
        )


@pytest.fixture(autouse=True)
def db_clean():
    """Ensure tables exist and clear test data before and after each test."""
    async def _clean():
        await create_tables()
        async with async_session_factory() as session:
            await session.execute(delete(PaperTradeModel))
            await session.execute(delete(DebateSessionModel))
            await session.commit()
        await engine.dispose()

    asyncio.run(_clean())
    yield
    asyncio.run(_clean())


# ── Task 3.a: Sequential Execution Order Tests ────────────────────────────────

@pytest.mark.asyncio
async def test_sequential_execution_order():
    """Verify that Bull, Bear, and Judge agents are executed strictly sequentially."""
    execution_timeline: list[str] = []

    mock_bull = MagicMock()
    mock_bear = MagicMock()
    mock_judge = MagicMock()

    bull_output = make_bull_output()
    bear_output = make_bear_output()
    judge_decision = make_judge_decision("NO_TRADE")

    async def bull_analyze(snap):
        execution_timeline.append("BULL_START")
        await asyncio.sleep(0.01)
        execution_timeline.append("BULL_END")
        return bull_output

    async def bear_analyze(snap):
        execution_timeline.append("BEAR_START")
        await asyncio.sleep(0.01)
        execution_timeline.append("BEAR_END")
        return bear_output

    async def judge_evaluate(snap, bull, bear):
        execution_timeline.append("JUDGE_START")
        await asyncio.sleep(0.01)
        execution_timeline.append("JUDGE_END")
        return judge_decision

    mock_bull.analyze = AsyncMock(side_effect=bull_analyze)
    mock_bear.analyze = AsyncMock(side_effect=bear_analyze)
    mock_judge.evaluate = AsyncMock(side_effect=judge_evaluate)

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_market_snapshot()
    result = await engine.run_debate(snapshot=snapshot)

    # Assert strict sequential ordering: Bull finishes before Bear starts, Bear finishes before Judge starts
    expected_order = [
        "BULL_START",
        "BULL_END",
        "BEAR_START",
        "BEAR_END",
        "JUDGE_START",
        "JUDGE_END",
    ]
    assert execution_timeline == expected_order, (
        f"Agents were not executed in strict sequential order. Got: {execution_timeline}"
    )

    # Verify Judge received the exact outputs from Bull and Bear
    mock_judge.evaluate.assert_called_once_with(snapshot, bull_output, bear_output)
    assert result.judge_decision.decision == "NO_TRADE"
    assert result.id is not None


# ── Task 3.b: Critical Safety Invariant Failure Tests ─────────────────────────

@pytest.mark.asyncio
async def test_safety_invariant_blocks_trade_when_analyzer_mode_false():
    """Verify SafetyError is raised, place_order is NOT called, and no PaperTrade is persisted

    when check_analyzer_mode returns False.
    """
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull_output())

    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear_output())

    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_judge_decision("BUY_CE"))

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    # OpenAlgo is in live trading mode or analyzer_mode is false!
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=False)
    mock_openalgo.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_market_snapshot()

    # Safety invariant must raise SafetyError
    with pytest.raises(SafetyError) as exc_info:
        await engine.run_debate(snapshot=snapshot)

    assert "OpenAlgo is not in analyzer mode" in str(exc_info.value)

    # Invariant: place_order MUST NOT be called
    mock_openalgo.place_order.assert_not_called()

    # Invariant: Database audit trail MUST have persisted DebateSessionModel
    async with async_session_factory() as session:
        sessions_res = await session.execute(select(DebateSessionModel))
        saved_sessions = sessions_res.scalars().all()
        assert len(saved_sessions) == 1
        assert saved_sessions[0].judge_decision == "BUY_CE"
        assert saved_sessions[0].spot_price == 24500.0

        # Invariant: Database MUST NOT have created any PaperTradeModel
        trades_res = await session.execute(select(PaperTradeModel))
        saved_trades = trades_res.scalars().all()
        assert len(saved_trades) == 0, "Security failure: Paper trade row was persisted despite failed safety check!"


@pytest.mark.asyncio
async def test_safety_invariant_blocks_trade_when_analyzer_mode_none():
    """Verify SafetyError is raised when check_analyzer_mode returns None."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull_output())
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear_output())
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_judge_decision("BUY_PE"))

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=None)
    mock_openalgo.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_market_snapshot()

    with pytest.raises(SafetyError):
        await engine.run_debate(snapshot=snapshot)

    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0


@pytest.mark.asyncio
async def test_safety_invariant_blocks_trade_when_check_analyzer_mode_raises():
    """Verify SafetyError is raised when check_analyzer_mode throws a connection error."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull_output())
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear_output())
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_judge_decision("BUY_CE"))

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(side_effect=RuntimeError("Connection refused by OpenAlgo"))
    mock_openalgo.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_market_snapshot()

    with pytest.raises(SafetyError) as exc_info:
        await engine.run_debate(snapshot=snapshot)

    assert "OpenAlgo is not in analyzer mode" in str(exc_info.value)
    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0


# ── Task 3.c: Critical Safety Invariant Success Tests ─────────────────────────

@pytest.mark.asyncio
async def test_safety_invariant_allows_order_and_persists_trade_on_analyzer_true():
    """Verify order is placed with side='BUY' and both DebateSessionModel and

    PaperTradeModel are persisted with matching debate_id when analyzer_mode is True.
    """
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull_output())

    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear_output())

    judge_decision = make_judge_decision("BUY_CE")
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=judge_decision)

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)
    mock_openalgo.place_order = AsyncMock(return_value={"order_id": "SIM-9999", "status": "COMPLETE"})
    mock_openalgo.get_ltp = AsyncMock(return_value=135.0)

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_market_snapshot()
    result = await engine.run_debate(snapshot=snapshot)

    # 1. Verify place_order was invoked with side="BUY" and order_type="MARKET"
    mock_openalgo.place_order.assert_called_once()
    _, call_kwargs = mock_openalgo.place_order.call_args
    assert call_kwargs["side"] == "BUY"
    assert call_kwargs["order_type"] == "MARKET"
    assert call_kwargs["qty"] == 25
    assert "24550CE" in call_kwargs["symbol"]

    # 2. Verify database records and foreign key linkage
    async with async_session_factory() as session:
        sessions_res = await session.execute(select(DebateSessionModel))
        saved_sessions = sessions_res.scalars().all()
        assert len(saved_sessions) == 1
        db_session = saved_sessions[0]
        assert db_session.id == result.id
        assert db_session.judge_decision == "BUY_CE"

        trades_res = await session.execute(select(PaperTradeModel))
        saved_trades = trades_res.scalars().all()
        assert len(saved_trades) == 1
        db_trade = saved_trades[0]

        # Audit trail linkage check
        assert db_trade.debate_id == db_session.id
        assert db_trade.trade_type == "BUY_CE"
        assert db_trade.entry_price == 135.0
        assert db_trade.stop_loss in (105.0, 107.0)
        assert db_trade.target in (175.0, 195.0)
        assert db_trade.quantity == 25
        assert db_trade.status == "OPEN"
        assert "24550CE" in db_trade.option_symbol


@pytest.mark.asyncio
async def test_safety_invariant_success_for_put_trade():
    """Verify BUY_PE also executes place_order with side='BUY' and persists correctly."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull_output())

    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear_output())

    judge_decision = make_judge_decision("BUY_PE")
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=judge_decision)

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)
    mock_openalgo.place_order = AsyncMock(return_value={"order_id": "SIM-8888", "status": "COMPLETE"})
    mock_openalgo.get_ltp = AsyncMock(return_value=120.0)

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_market_snapshot()
    result = await engine.run_debate(snapshot=snapshot)

    mock_openalgo.place_order.assert_called_once()
    _, call_kwargs = mock_openalgo.place_order.call_args
    assert call_kwargs["side"] == "BUY"
    assert "24450PE" in call_kwargs["symbol"]

    async with async_session_factory() as session:
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 1
        assert trades[0].trade_type == "BUY_PE"
        assert trades[0].debate_id == result.id


@pytest.mark.asyncio
async def test_no_trade_decision_persists_session_without_order():
    """Verify NO_TRADE persists debate session without calling OpenAlgo place_order."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull_output())

    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear_output())

    judge_decision = make_judge_decision("NO_TRADE")
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=judge_decision)

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)
    mock_openalgo.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_market_snapshot()
    result = await engine.run_debate(snapshot=snapshot)

    mock_openalgo.check_analyzer_mode.assert_not_called()
    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        sessions = (await session.execute(select(DebateSessionModel))).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].judge_decision == "NO_TRADE"

        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0


@pytest.mark.asyncio
async def test_direct_create_paper_trade_safety_block():
    """Directly test _create_paper_trade raises SafetyError when client is not in analyzer mode."""
    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=False)
    mock_openalgo.place_order = AsyncMock()

    engine = DebateEngine(openalgo_client=mock_openalgo)
    snapshot = make_market_snapshot()
    decision = make_judge_decision("BUY_CE")

    # Insert a dummy debate session row
    async with async_session_factory() as session:
        db_row = DebateSessionModel(
            spot_price=snapshot.spot_price,
            judge_decision="BUY_CE",
        )
        session.add(db_row)
        await session.commit()
        await session.refresh(db_row)

        with pytest.raises(SafetyError) as exc_info:
            await engine._create_paper_trade(session, db_row.id, decision, snapshot)

        assert "OpenAlgo is not in analyzer mode" in str(exc_info.value)
        mock_openalgo.place_order.assert_not_called()

        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0


@pytest.mark.asyncio
async def test_entry_price_estimate_fallback_to_ltp():
    """Verify that if entry_price_estimate is 0, client.get_ltp is queried."""
    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)
    mock_openalgo.place_order = AsyncMock(return_value={"order_id": "ORD-1", "status": "COMPLETE"})
    mock_openalgo.get_ltp = AsyncMock(return_value=175.50)

    engine = DebateEngine(openalgo_client=mock_openalgo)
    snapshot = make_market_snapshot()
    decision = make_judge_decision("BUY_CE")
    decision.entry_price_estimate = 0.0  # Force fallback to LTP

    async with async_session_factory() as session:
        db_row = DebateSessionModel(
            spot_price=snapshot.spot_price,
            judge_decision="BUY_CE",
        )
        session.add(db_row)
        await session.commit()
        await session.refresh(db_row)

        await engine._create_paper_trade(session, db_row.id, decision, snapshot)

        mock_openalgo.get_ltp.assert_called_once()
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 1
        assert trades[0].entry_price == 175.50


@pytest.mark.asyncio
async def test_openalgo_client_di_and_default_fallback():
    """Verify DebateEngine client resolution handles DI and default fallback."""
    # When openalgo_client is passed:
    custom_client = MagicMock(spec=OpenAlgoClient)
    engine_custom = DebateEngine(openalgo_client=custom_client)
    assert await engine_custom._get_openalgo_client() is custom_client

    # When openalgo_client is None:
    engine_default = DebateEngine()
    resolved_default = await engine_default._get_openalgo_client()
    assert isinstance(resolved_default, OpenAlgoClient)


@pytest.mark.asyncio
async def test_sqlite_foreign_key_referential_integrity():
    """Verify that SQLite rejects orphan paper_trades with invalid debate_id."""
    from datetime import datetime, timezone
    from sqlalchemy.exc import IntegrityError

    async with async_session_factory() as session:
        orphan_trade = PaperTradeModel(
            debate_id=999999,  # Non-existent debate session
            option_symbol="NIFTY24DEC24500CE",
            trade_type="BUY_CE",
            entry_price=100.0,
            stop_loss=80.0,
            target=150.0,
            opened_at=datetime.now(timezone.utc),
            quantity=75,
        )
        session.add(orphan_trade)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


