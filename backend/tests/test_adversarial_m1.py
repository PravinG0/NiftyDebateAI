"""Adversarial stress-test suite for Milestone 1.

Challenges tested:
1. Truthiness and return variations of check_analyzer_mode():
   - False, None, "false", 0, "", "False", and random exceptions.
   - Verifies whether _create_paper_trade or run_debate leaks an order or trade row.
2. Sequential execution under latency differences:
   - Bull agent latency = 0.10s, Bear agent latency = 0.01s.
   - Verifies Bear NEVER starts before Bull completes, no concurrency exists,
     and overall elapsed time reflects sequential execution.
3. Agent exceptions:
   - Bull agent raises an exception.
   - Bear agent raises an exception.
   - Judge agent raises an exception.
   - Verifies database state and order leakage under failures.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, select

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


def make_snapshot() -> MarketSnapshot:
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


def make_bull() -> BullAgentOutput:
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


def make_bear() -> BearAgentOutput:
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


def make_buy_ce_decision() -> JudgeDecision:
    return JudgeDecision(
        decision="BUY_CE",
        selected_strike="24550CE",
        entry_price_estimate=135.0,
        stop_loss=105.0,
        target=195.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Bullish trend verified.",
        risk_warning="Watch 24600.",
    )


@pytest.fixture(autouse=True)
def db_cleanup():
    """Ensure clean tables before and after each adversarial test."""
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


# ═════════════════════════════════════════════════════════════════════════════
# CHALLENGE 1: Truthiness & Return Variations of check_analyzer_mode()
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "return_val",
    [
        False,
        None,
        0,
        "",
        0.0,
        [],
        {},
    ],
)
async def test_challenge_1_analyzer_mode_falsy_values_blocked(return_val: Any):
    """Verify standard Python falsy values raise SafetyError and block orders/trades."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull())
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear())
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_buy_ce_decision())

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=return_val)
    mock_openalgo.place_order = AsyncMock(return_value={"order_id": "LEAKED", "status": "COMPLETE"})
    mock_openalgo.get_ltp = AsyncMock(return_value=135.0)

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_snapshot()

    with pytest.raises(SafetyError) as exc_info:
        await engine.run_debate(snapshot=snapshot)

    assert "OpenAlgo is not in analyzer mode" in str(exc_info.value)
    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0, f"LEAK DETECTED: Trade persisted when check_analyzer_mode returned {return_val!r}!"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "return_val",
    [
        "false",  # Adversarial string: "false" is truthy in Python!
        "False",  # Adversarial string: "False"
        "0",      # Adversarial string: "0"
        1,        # Integer 1 is not boolean True
    ],
)
async def test_challenge_1_analyzer_mode_truthiness_string_leak_vulnerability(return_val: Any):
    """Stress-test truthiness variations for check_analyzer_mode().

    CRITICAL SAFETY REQUIREMENT (PROJECT.md contract: place_order called only if check_analyzer_mode() is True):
    If check_analyzer_mode() returns a string like "false", "False", or "0", or int 1,
    the system MUST reject it and raise SafetyError. It MUST NOT interpret truthy strings as approval.
    Failure of this test proves an order was placed or trade row persisted in live mode!
    """
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull())
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear())
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_buy_ce_decision())

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=return_val)
    mock_openalgo.place_order = AsyncMock(return_value={"order_id": "LEAKED", "status": "COMPLETE"})
    mock_openalgo.get_ltp = AsyncMock(return_value=135.0)

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_snapshot()

    with pytest.raises(SafetyError) as exc_info:
        await engine.run_debate(snapshot=snapshot)

    assert "OpenAlgo is not in analyzer mode" in str(exc_info.value)
    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0, f"LEAK DETECTED: Trade persisted when check_analyzer_mode returned {return_val!r}!"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception_cls",
    [
        RuntimeError("Network socket dropped"),
        ConnectionRefusedError("OpenAlgo port 5000 unreachable"),
        TimeoutError("Analyzer check timed out after 10s"),
        ValueError("Malformed JSON response from analyzer endpoint"),
        KeyError("analyzer_mode key missing"),
    ],
)
async def test_challenge_1_analyzer_mode_exceptions(exception_cls: Exception):
    """Verify that any exception raised during check_analyzer_mode blocks orders and trades."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull())
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear())
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_buy_ce_decision())

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(side_effect=exception_cls)
    mock_openalgo.place_order = AsyncMock()
    mock_openalgo.get_ltp = AsyncMock(return_value=135.0)

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    snapshot = make_snapshot()

    with pytest.raises(SafetyError) as exc_info:
        await engine.run_debate(snapshot=snapshot)

    assert "OpenAlgo is not in analyzer mode" in str(exc_info.value)
    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0, f"LEAK DETECTED: Trade persisted on exception {exception_cls!r}"


# ═════════════════════════════════════════════════════════════════════════════
# CHALLENGE 2: Sequential Execution Under Latency Differences
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_challenge_2_sequential_execution_under_latency_skew():
    """Prove Bear NEVER finishes before Bull starts, and NEVER runs concurrently.

    Bull latency: 0.10s (slow)
    Bear latency: 0.01s (fast)
    """
    bull_is_running = False
    bear_is_running = False
    concurrency_detected = False

    timeline: list[tuple[str, float]] = []

    async def slow_bull_analyze(snap):
        nonlocal bull_is_running, concurrency_detected
        bull_is_running = True
        timeline.append(("BULL_START", time.monotonic()))
        await asyncio.sleep(0.10)
        if bear_is_running:
            concurrency_detected = True
        timeline.append(("BULL_END", time.monotonic()))
        bull_is_running = False
        return make_bull()

    async def fast_bear_analyze(snap):
        nonlocal bear_is_running, concurrency_detected
        if bull_is_running:
            concurrency_detected = True
        bear_is_running = True
        timeline.append(("BEAR_START", time.monotonic()))
        await asyncio.sleep(0.01)
        timeline.append(("BEAR_END", time.monotonic()))
        bear_is_running = False
        return make_bear()

    async def judge_analyze(snap, b_out, br_out):
        timeline.append(("JUDGE_START", time.monotonic()))
        await asyncio.sleep(0.005)
        timeline.append(("JUDGE_END", time.monotonic()))
        return make_buy_ce_decision()

    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(side_effect=slow_bull_analyze)
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(side_effect=fast_bear_analyze)
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(side_effect=judge_analyze)

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)
    mock_openalgo.place_order = AsyncMock(return_value={"order_id": "ORD-1", "status": "COMPLETE"})
    mock_openalgo.get_ltp = AsyncMock(return_value=135.0)

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    start_time = time.monotonic()
    await engine.run_debate(snapshot=make_snapshot())
    total_elapsed = time.monotonic() - start_time

    # 1. Concurrency check
    assert not concurrency_detected, "Concurrency detected between Bull and Bear agents!"

    # 2. Strict sequential order in timeline
    event_names = [ev[0] for ev in timeline]
    expected_order = [
        "BULL_START",
        "BULL_END",
        "BEAR_START",
        "BEAR_END",
        "JUDGE_START",
        "JUDGE_END",
    ]
    assert event_names == expected_order, f"Execution order violated! Got: {event_names}"

    # 3. Timing checks
    time_map = {ev[0]: ev[1] for ev in timeline}
    assert time_map["BEAR_START"] >= time_map["BULL_END"], (
        f"Bear started at {time_map['BEAR_START']} before Bull ended at {time_map['BULL_END']}"
    )
    assert time_map["JUDGE_START"] >= time_map["BEAR_END"], (
        f"Judge started at {time_map['JUDGE_START']} before Bear ended at {time_map['BEAR_END']}"
    )

    # 4. Total duration must be >= (0.10 + 0.01 + 0.005) = 0.115s
    assert total_elapsed >= 0.110, f"Execution was suspiciously fast ({total_elapsed:.4f}s), indicating parallelism!"


# ═════════════════════════════════════════════════════════════════════════════
# CHALLENGE 3: Agent Exceptions
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_challenge_3_bull_agent_exception():
    """Verify that when Bull agent raises an exception:
    - Exception propagates
    - Bear and Judge are NEVER executed
    - OpenAlgo place_order is NEVER executed
    - No DebateSession or PaperTrade rows are persisted
    """
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(side_effect=RuntimeError("Bull Agent LLM Failure"))

    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear())

    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_buy_ce_decision())

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)
    mock_openalgo.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await engine.run_debate(snapshot=make_snapshot())

    assert "Bull Agent LLM Failure" in str(exc_info.value)
    mock_bear.analyze.assert_not_called()
    mock_judge.evaluate.assert_not_called()
    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        sessions = (await session.execute(select(DebateSessionModel))).scalars().all()
        assert len(sessions) == 0, "DebateSession was persisted despite Bull failure!"
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0, "PaperTrade was persisted despite Bull failure!"


@pytest.mark.asyncio
async def test_challenge_3_bear_agent_exception():
    """Verify that when Bear agent raises an exception:
    - Exception propagates
    - Bull completed, but Judge is NEVER executed
    - OpenAlgo place_order is NEVER executed
    - No DebateSession or PaperTrade rows are persisted
    """
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull())

    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(side_effect=ValueError("Bear Agent Invalid Schema"))

    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_buy_ce_decision())

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)
    mock_openalgo.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    with pytest.raises(ValueError) as exc_info:
        await engine.run_debate(snapshot=make_snapshot())

    assert "Bear Agent Invalid Schema" in str(exc_info.value)
    mock_bull.analyze.assert_called_once()
    mock_judge.evaluate.assert_not_called()
    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        sessions = (await session.execute(select(DebateSessionModel))).scalars().all()
        assert len(sessions) == 0, "DebateSession was persisted despite Bear failure!"
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0, "PaperTrade was persisted despite Bear failure!"


@pytest.mark.asyncio
async def test_challenge_3_judge_agent_exception():
    """Verify that when Judge agent raises an exception:
    - Exception propagates
    - Bull and Bear completed, but OpenAlgo place_order is NEVER executed
    - No DebateSession or PaperTrade rows are persisted
    """
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull())

    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear())

    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(side_effect=RuntimeError("Judge Agent Quorum Error"))

    mock_openalgo = MagicMock(spec=OpenAlgoClient)
    mock_openalgo.check_analyzer_mode = AsyncMock(return_value=True)
    mock_openalgo.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await engine.run_debate(snapshot=make_snapshot())

    assert "Judge Agent Quorum Error" in str(exc_info.value)
    mock_bull.analyze.assert_called_once()
    mock_bear.analyze.assert_called_once()
    mock_openalgo.place_order.assert_not_called()

    async with async_session_factory() as session:
        sessions = (await session.execute(select(DebateSessionModel))).scalars().all()
        assert len(sessions) == 0, "DebateSession was persisted despite Judge failure!"
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0, "PaperTrade was persisted despite Judge failure!"
