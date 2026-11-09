"""Empirical Challenger stress test suite for Milestone 1:
Challenge 1: SQLite Referential Integrity & Foreign Key constraints (orphans, cascades, deletions).
Challenge 2: Audit Trail Resilience under failures (SafetyError, broker crash, LTP error).
Challenge 3: Rapid Sequential Debates (10 iterations loop, monotonically increasing PKs, FK linkage, mixed verdicts).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError

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


# ── Test Helpers ──────────────────────────────────────────────────────────────

def make_snapshot(spot: float = 24500.0) -> MarketSnapshot:
    return MarketSnapshot(
        spot_price=spot,
        ema_20=spot - 20,
        ema_50=spot - 50,
        vwap=spot + 10,
        rsi=55.0,
        volume=1200000.0,
        atr=110.0,
        vix=13.5,
        pcr=1.1,
        ce_oi_change=150000.0,
        pe_oi_change=250000.0,
        higher_tf_trend="BULLISH",
    )


def make_bull(strike: str = "24550CE", confidence: int = 8) -> BullAgentOutput:
    return BullAgentOutput(
        bias="BULLISH",
        confidence=confidence,
        recommended_strike=strike,
        arguments=[
            "Strong support above 20 EMA",
            "Call writing absorption observed at resistance",
            "PCR at 1.15 confirms sustained put accumulation",
        ],
        key_levels={"support": 24450.0, "resistance": 24600.0},
    )


def make_bear(strike: str = "24450PE", confidence: int = 4) -> BearAgentOutput:
    return BearAgentOutput(
        bias="BEARISH",
        confidence=confidence,
        recommended_strike=strike,
        arguments=[
            "VWAP resistance overhead capping momentum",
            "Declining volume on green candles indicates exhaustion",
            "Heavy open interest concentration at immediate call strikes",
        ],
        key_levels={"resistance": 24550.0, "support": 24400.0},
    )


def make_judge(decision: str = "BUY_CE", strike: str = "24550CE") -> JudgeDecision:
    if decision == "BUY_CE":
        return JudgeDecision(
            decision="BUY_CE",
            selected_strike=strike,
            entry_price_estimate=135.0,
            stop_loss=105.0,
            target=195.0,
            risk_reward_ratio="1:2.0",
            winning_argument_summary="Bullish thesis confirmed by volume and PCR.",
            risk_warning="Watch 24600 resistance.",
        )
    elif decision == "BUY_PE":
        return JudgeDecision(
            decision="BUY_PE",
            selected_strike=strike,
            entry_price_estimate=120.0,
            stop_loss=95.0,
            target=170.0,
            risk_reward_ratio="1:2.0",
            winning_argument_summary="Bearish breakdown confirmed.",
            risk_warning="Support at 24400.",
        )
    else:
        return JudgeDecision(
            decision="NO_TRADE",
            selected_strike=None,
            entry_price_estimate=0.0,
            stop_loss=0.0,
            target=0.0,
            risk_reward_ratio="N/A",
            winning_argument_summary="Market in consolidation range.",
            risk_warning="Theta risk.",
        )


@pytest.fixture(autouse=True)
def clean_db():
    """Ensure clean tables before and after each test."""
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


# ══════════════════════════════════════════════════════════════════════════════
# CHALLENGE 1: SQLite Referential Integrity & Foreign Key Constraints
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_challenge_1a_orphan_trade_insertion_orm_fails():
    """Verify ORM rejects insertion of a PaperTradeModel with a non-existent debate_id."""
    async with async_session_factory() as session:
        orphan = PaperTradeModel(
            debate_id=987654,  # Non-existent ID
            option_symbol="NIFTY24DEC24500CE",
            trade_type="BUY_CE",
            entry_price=100.0,
            stop_loss=80.0,
            target=150.0,
            status="OPEN",
            opened_at=datetime.now(timezone.utc),
            quantity=75,
        )
        session.add(orphan)
        with pytest.raises(IntegrityError) as exc_info:
            await session.commit()
        await session.rollback()

    assert "FOREIGN KEY constraint failed" in str(exc_info.value) or "IntegrityError" in str(exc_info.type)


@pytest.mark.asyncio
async def test_challenge_1b_orphan_trade_insertion_raw_sql_fails():
    """Verify raw SQL INSERT of an orphan trade fails under SQLite PRAGMA foreign_keys=ON."""
    async with async_session_factory() as session:
        raw_stmt = insert(PaperTradeModel).values(
            debate_id=888888,
            option_symbol="NIFTY24DEC24500CE",
            trade_type="BUY_CE",
            entry_price=100.0,
            stop_loss=80.0,
            target=150.0,
            status="OPEN",
            opened_at=datetime.now(timezone.utc),
            quantity=75,
        )
        with pytest.raises(IntegrityError):
            await session.execute(raw_stmt)
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_challenge_1c_null_debate_id_rejected():
    """Verify PaperTradeModel rejects NULL debate_id (not-null constraint)."""
    async with async_session_factory() as session:
        invalid_trade = PaperTradeModel(
            debate_id=None,
            option_symbol="NIFTY24DEC24500CE",
            trade_type="BUY_CE",
            entry_price=100.0,
            stop_loss=80.0,
            target=150.0,
            status="OPEN",
            opened_at=datetime.now(timezone.utc),
            quantity=75,
        )
        session.add(invalid_trade)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_challenge_1d_orm_delete_cascade_removes_children():
    """Verify ORM session.delete() on DebateSessionModel cascades and deletes attached PaperTrade."""
    async with async_session_factory() as session:
        # Create parent session
        db_session = DebateSessionModel(
            spot_price=24500.0,
            judge_decision="BUY_CE",
        )
        session.add(db_session)
        await session.commit()
        await session.refresh(db_session)
        parent_id = db_session.id

        # Attach child trade
        trade = PaperTradeModel(
            debate_id=parent_id,
            option_symbol="NIFTY24DEC24550CE",
            trade_type="BUY_CE",
            entry_price=135.0,
            stop_loss=105.0,
            target=195.0,
            status="OPEN",
            opened_at=datetime.now(timezone.utc),
            quantity=75,
        )
        session.add(trade)
        await session.commit()

    # Re-query and delete via ORM
    async with async_session_factory() as session:
        parent_to_delete = await session.get(DebateSessionModel, parent_id)
        assert parent_to_delete is not None
        await session.delete(parent_to_delete)
        await session.commit()

    # Verify both parent and child were removed
    async with async_session_factory() as session:
        remaining_sessions = (await session.execute(select(DebateSessionModel).where(DebateSessionModel.id == parent_id))).scalars().all()
        remaining_trades = (await session.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == parent_id))).scalars().all()
        assert len(remaining_sessions) == 0, "Parent session was not deleted!"
        assert len(remaining_trades) == 0, "Child paper trade was not cascade-deleted by ORM!"


@pytest.mark.asyncio
async def test_challenge_1e_raw_sql_delete_blocked_by_sqlite_fk():
    """Verify raw SQL DELETE of DebateSessionModel with existing child trades is BLOCKED by SQLite FK constraint."""
    async with async_session_factory() as session:
        db_session = DebateSessionModel(
            spot_price=24500.0,
            judge_decision="BUY_CE",
        )
        session.add(db_session)
        await session.commit()
        await session.refresh(db_session)
        parent_id = db_session.id

        trade = PaperTradeModel(
            debate_id=parent_id,
            option_symbol="NIFTY24DEC24550CE",
            trade_type="BUY_CE",
            entry_price=135.0,
            stop_loss=105.0,
            target=195.0,
            status="OPEN",
            opened_at=datetime.now(timezone.utc),
            quantity=75,
        )
        session.add(trade)
        await session.commit()

    # Direct raw SQL delete of parent without deleting child
    async with async_session_factory() as session:
        delete_stmt = delete(DebateSessionModel).where(DebateSessionModel.id == parent_id)
        with pytest.raises(IntegrityError) as exc_info:
            await session.execute(delete_stmt)
            await session.commit()
        await session.rollback()

    assert "FOREIGN KEY constraint failed" in str(exc_info.value) or "IntegrityError" in str(exc_info.type)


# ══════════════════════════════════════════════════════════════════════════════
# CHALLENGE 2: Audit Trail Resilience Under Failures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_challenge_2a_audit_trail_intact_when_safety_blocks():
    """Verify DebateSessionModel is 100% reliably committed when OpenAlgo is NOT in analyzer mode."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull())
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear())
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_judge("BUY_CE"))

    mock_client = MagicMock(spec=OpenAlgoClient)
    mock_client.check_analyzer_mode = AsyncMock(return_value=False)
    mock_client.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_client,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    with pytest.raises(SafetyError):
        await engine.run_debate(snapshot=make_snapshot(24500.0))

    # Verify audit trail in SQLite
    async with async_session_factory() as session:
        sessions = (await session.execute(select(DebateSessionModel))).scalars().all()
        assert len(sessions) == 1, "DebateSessionModel must be committed even if trade is blocked!"
        s = sessions[0]
        assert s.spot_price == 24500.0
        assert s.judge_decision == "BUY_CE"
        assert s.bull_confidence == 8
        assert s.bear_confidence == 4
        assert s.market_data_json is not None
        assert s.judge_full_output is not None

        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0, "No paper trade should exist when safety error is triggered!"


@pytest.mark.asyncio
async def test_challenge_2b_audit_trail_intact_when_place_order_crashes():
    """Verify DebateSessionModel remains committed if OpenAlgo place_order throws an unhandled exception."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull())
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear())
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=make_judge("BUY_CE"))

    mock_client = MagicMock(spec=OpenAlgoClient)
    mock_client.check_analyzer_mode = AsyncMock(return_value=True)
    # Simulate network failure or OpenAlgo crash during order placement
    mock_client.place_order = AsyncMock(side_effect=ConnectionResetError("Socket abruptly closed by OpenAlgo server"))
    mock_client.get_ltp = AsyncMock(return_value=135.0)

    engine = DebateEngine(
        openalgo_client=mock_client,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    with pytest.raises(ConnectionResetError) as exc_info:
        await engine.run_debate(snapshot=make_snapshot(24500.0))

    assert "Socket abruptly closed" in str(exc_info.value)

    # Verify DebateSessionModel was committed and intact before place_order crashed
    async with async_session_factory() as session:
        sessions = (await session.execute(select(DebateSessionModel))).scalars().all()
        assert len(sessions) == 1, "DebateSessionModel must be reliably saved before order execution!"
        assert sessions[0].judge_decision == "BUY_CE"
        assert sessions[0].spot_price == 24500.0

        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0, "No partial or ghost trade record should be persisted!"


@pytest.mark.asyncio
async def test_challenge_2c_audit_trail_intact_when_get_ltp_crashes():
    """Verify DebateSessionModel is committed even if get_ltp fails."""
    mock_bull = MagicMock()
    mock_bull.analyze = AsyncMock(return_value=make_bull())
    mock_bear = MagicMock()
    mock_bear.analyze = AsyncMock(return_value=make_bear())

    judge_dec = make_judge("BUY_PE")
    judge_dec.entry_price_estimate = 0.0  # Force get_ltp query
    mock_judge = MagicMock()
    mock_judge.evaluate = AsyncMock(return_value=judge_dec)

    mock_client = MagicMock(spec=OpenAlgoClient)
    mock_client.check_analyzer_mode = AsyncMock(return_value=True)
    mock_client.get_ltp = AsyncMock(side_effect=TimeoutError("LTP request timed out"))
    mock_client.place_order = AsyncMock()

    engine = DebateEngine(
        openalgo_client=mock_client,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    with pytest.raises(TimeoutError):
        await engine.run_debate(snapshot=make_snapshot(24500.0))

    mock_client.place_order.assert_not_called()

    async with async_session_factory() as session:
        sessions = (await session.execute(select(DebateSessionModel))).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].judge_decision == "BUY_PE"

        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(trades) == 0


# ══════════════════════════════════════════════════════════════════════════════
# CHALLENGE 3: Rapid Sequential Debate Executions & Concurrency Stress
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_challenge_3a_rapid_sequential_10_debates_loop():
    """Execute 10 debate sessions in a rapid loop, verifying primary keys, debate_id linkages, and audit trail."""
    mock_bull = MagicMock()
    mock_bear = MagicMock()
    mock_judge = MagicMock()
    mock_client = MagicMock(spec=OpenAlgoClient)
    mock_client.check_analyzer_mode = AsyncMock(return_value=True)
    mock_client.place_order = AsyncMock(return_value={"status": "COMPLETE"})
    mock_client.get_ltp = AsyncMock(return_value=150.0)

    engine = DebateEngine(
        openalgo_client=mock_client,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    returned_sessions = []
    num_runs = 10

    for i in range(num_runs):
        spot = 24500.0 + (i * 25.0)
        snapshot = make_snapshot(spot)
        bull_out = make_bull(f"{int(spot + 50)}CE", confidence=7 + (i % 3))
        bear_out = make_bear(f"{int(spot - 50)}PE", confidence=5)
        decision_str = "BUY_CE" if i % 2 == 0 else "BUY_PE"
        chosen_strike = bull_out.recommended_strike if decision_str == "BUY_CE" else bear_out.recommended_strike
        judge_out = make_judge(decision_str, strike=chosen_strike)

        mock_bull.analyze = AsyncMock(return_value=bull_out)
        mock_bear.analyze = AsyncMock(return_value=bear_out)
        mock_judge.evaluate = AsyncMock(return_value=judge_out)

        session_result = await engine.run_debate(snapshot=snapshot)
        assert session_result.id is not None
        returned_sessions.append(session_result)

    # Verify 10 returned sessions have strictly monotonically increasing IDs
    session_ids = [s.id for s in returned_sessions]
    assert len(session_ids) == num_runs
    assert session_ids == sorted(session_ids), f"Session IDs not monotonically sorted: {session_ids}"
    assert len(set(session_ids)) == num_runs, "Duplicate session IDs detected!"

    # Verify in SQLite database
    async with async_session_factory() as db_session:
        db_rows = (await db_session.execute(select(DebateSessionModel).order_by(DebateSessionModel.id))).scalars().all()
        assert len(db_rows) == num_runs, f"Expected {num_runs} debate sessions in DB, found {len(db_rows)}"

        db_trades = (await db_session.execute(select(PaperTradeModel).order_by(PaperTradeModel.id))).scalars().all()
        assert len(db_trades) == num_runs, f"Expected {num_runs} paper trades in DB, found {len(db_trades)}"

        for idx, (s_row, t_row) in enumerate(zip(db_rows, db_trades)):
            expected_spot = 24500.0 + (idx * 25.0)
            assert s_row.id == session_ids[idx]
            assert s_row.spot_price == expected_spot
            # Verify foreign key linkage
            assert t_row.debate_id == s_row.id, (
                f"Mismatch: trade {t_row.id} has debate_id {t_row.debate_id}, expected session {s_row.id}"
            )
            expected_type = "BUY_CE" if idx % 2 == 0 else "BUY_PE"
            assert t_row.trade_type == expected_type
            assert s_row.judge_decision == expected_type


@pytest.mark.asyncio
async def test_challenge_3b_rapid_mixed_verdicts_and_failures_loop():
    """Execute 10 debate sessions with alternating outcomes: BUY_CE, NO_TRADE, BUY_PE, and SafetyError.

    Verify database state remains fully consistent despite interleaved failures.
    """
    mock_bull = MagicMock()
    mock_bear = MagicMock()
    mock_judge = MagicMock()
    mock_client = MagicMock(spec=OpenAlgoClient)

    engine = DebateEngine(
        openalgo_client=mock_client,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    outcomes = [
        ("BUY_CE", True),    # Trade created
        ("NO_TRADE", True),  # No trade created
        ("BUY_PE", False),   # SafetyError raised, session saved, no trade
        ("BUY_PE", True),    # Trade created
        ("NO_TRADE", True),  # No trade created
        ("BUY_CE", False),   # SafetyError raised, session saved, no trade
        ("BUY_CE", True),    # Trade created
        ("BUY_PE", True),    # Trade created
        ("NO_TRADE", True),  # No trade created
        ("BUY_CE", True),    # Trade created
    ]

    expected_trade_count = 0
    expected_session_count = len(outcomes)

    for i, (decision, analyzer_mode) in enumerate(outcomes):
        spot = 24500.0 + i * 10
        snapshot = make_snapshot(spot)
        mock_bull.analyze = AsyncMock(return_value=make_bull())
        mock_bear.analyze = AsyncMock(return_value=make_bear())
        mock_judge.evaluate = AsyncMock(return_value=make_judge(decision))
        mock_client.check_analyzer_mode = AsyncMock(return_value=analyzer_mode)
        mock_client.place_order = AsyncMock(return_value={"status": "OK"})
        mock_client.get_ltp = AsyncMock(return_value=125.0)

        if decision != "NO_TRADE" and not analyzer_mode:
            with pytest.raises(SafetyError):
                await engine.run_debate(snapshot=snapshot)
        else:
            await engine.run_debate(snapshot=snapshot)
            if decision != "NO_TRADE":
                expected_trade_count += 1

    # Check database counts and FK linkages
    async with async_session_factory() as db_session:
        sessions = (await db_session.execute(select(DebateSessionModel).order_by(DebateSessionModel.id))).scalars().all()
        assert len(sessions) == expected_session_count, (
            f"Expected {expected_session_count} sessions, got {len(sessions)}"
        )

        trades = (await db_session.execute(select(PaperTradeModel).order_by(PaperTradeModel.id))).scalars().all()
        assert len(trades) == expected_trade_count, (
            f"Expected {expected_trade_count} trades, got {len(trades)}"
        )

        # Check every trade has a valid foreign key to an existing session
        session_id_set = {s.id for s in sessions}
        for trade in trades:
            assert trade.debate_id in session_id_set, f"Trade {trade.id} has invalid debate_id {trade.debate_id}"


@pytest.mark.asyncio
async def test_challenge_3c_concurrent_debate_executions():
    """Stress test SQLite connection pool under concurrent debate executions."""
    mock_bull = MagicMock()
    mock_bear = MagicMock()
    mock_judge = MagicMock()
    mock_client = MagicMock(spec=OpenAlgoClient)
    mock_client.check_analyzer_mode = AsyncMock(return_value=True)
    mock_client.place_order = AsyncMock(return_value={"status": "OK"})
    mock_client.get_ltp = AsyncMock(return_value=110.0)

    engine = DebateEngine(
        openalgo_client=mock_client,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    async def single_debate(idx: int):
        spot = 24000.0 + idx * 50
        snap = make_snapshot(spot)
        return await engine.run_debate(snapshot=snap)

    mock_bull.analyze = AsyncMock(return_value=make_bull())
    mock_bear.analyze = AsyncMock(return_value=make_bear())
    mock_judge.evaluate = AsyncMock(return_value=make_judge("BUY_CE"))

    # Run 5 concurrent debates
    results = await asyncio.gather(*(single_debate(i) for i in range(5)))
    assert len(results) == 5

    async with async_session_factory() as session:
        sessions = (await session.execute(select(DebateSessionModel))).scalars().all()
        trades = (await session.execute(select(PaperTradeModel))).scalars().all()
        assert len(sessions) == 5
        assert len(trades) == 5

        # Verify all foreign keys match
        session_ids = {s.id for s in sessions}
        for t in trades:
            assert t.debate_id in session_ids
