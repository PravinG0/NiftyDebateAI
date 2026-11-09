"""
Milestone 2 Scheduler Challenger Empirical Stress Test Suite.

Adversarially tests:
1. Challenge 1: Rapid state transitions & APScheduler lifecycle:
   - (start -> pause -> resume -> pause -> start -> stop) cycles
   - Idempotency & exception resistance (SchedulerAlreadyRunningError)
   - Synchronous start_scheduler outside of running loop raises RuntimeError
2. Challenge 2: Concurrent debate triggers & serialization:
   - Concurrent calls to /api/debate/run, trigger_debate_now(), and _debate_job()
   - Verification that _debate_lock enforces strict mutual exclusion (max_concurrent == 1)
   - Verification of transactional database integrity under concurrent load
   - Verification of lock release after exceptions
   - Multi-event-loop lock contention failure mode
3. Challenge 3: Market hours boundary conditions & timezone handling:
   - Boundaries at 09:19, 09:20, 15:15, 15:16 IST on weekdays (Mon-Fri)
   - Weekday vs weekend checks (Saturday, Sunday)
   - UTC-aware timestamp conversion verification (Bug demonstration)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

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
from main import app
import scheduler
from scheduler import (
    _debate_job,
    get_scheduler,
    is_market_open,
    pause_scheduler,
    resume_scheduler,
    scheduler_status,
    start_scheduler,
    stop_scheduler,
    trigger_debate_now,
)
from schemas import (
    BearAgentOutput,
    BullAgentOutput,
    DebateSession,
    JudgeDecision,
    MarketSnapshot,
)

_IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def cleanup_scheduler_and_db():
    stop_scheduler()

    async def _clean():
        await create_tables()
        async with async_session_factory() as session:
            await session.execute(delete(PaperTradeModel))
            await session.execute(delete(DebateSessionModel))
            await session.commit()
        await engine.dispose()

    asyncio.run(_clean())
    yield
    stop_scheduler()
    asyncio.run(_clean())


def make_dummy_session(session_id: int | None = 1) -> DebateSession:
    return DebateSession(
        id=session_id,
        timestamp=datetime.now(timezone.utc),
        market_snapshot=MarketSnapshot(
            spot_price=24520.0,
            ema_20=24480.0,
            ema_50=24450.0,
            vwap=24510.0,
            rsi=55.0,
            volume=100000.0,
            atr=110.0,
            vix=13.0,
            pcr=1.1,
            ce_oi_change=1000.0,
            pe_oi_change=2000.0,
            higher_tf_trend="BULLISH",
        ),
        bull_output=BullAgentOutput(
            bias="BULLISH",
            confidence=8,
            recommended_strike="24555CE",
            arguments=["Bull argument 1", "Bull argument 2", "Bull argument 3"],
        ),
        bear_output=BearAgentOutput(
            bias="BEARISH",
            confidence=4,
            recommended_strike="24500PE",
            arguments=["Bear argument 1", "Bear argument 2", "Bear argument 3"],
        ),
        judge_decision=JudgeDecision(
            decision="NO_TRADE",
            selected_strike=None,
            entry_price_estimate=0.0,
            stop_loss=0.0,
            target=0.0,
            risk_reward_ratio="1:2.0",
            winning_argument_summary="Balanced market conditions; stand aside.",
            risk_warning="None",
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Challenge 1: Rapid State Transitions & Lifecycle Stress
# ══════════════════════════════════════════════════════════════════════════════

def test_challenge1_rapid_state_transitions():
    """Rapid state transitions: start -> pause -> resume -> pause -> start -> stop.
    Must never raise SchedulerAlreadyRunningError or leave the scheduler in an invalid state.
    """
    client = TestClient(app)

    for _ in range(10):
        # 1. Start
        r = client.post("/api/scheduler/start")
        assert r.status_code == 200
        assert r.json()["status"]["running"] is True
        assert r.json()["status"]["state"] == "RUNNING"

        # 2. Pause
        r = client.post("/api/scheduler/pause")
        assert r.status_code == 200
        assert r.json()["status"]["running"] is False
        assert r.json()["status"]["paused"] is True
        assert r.json()["status"]["state"] == "PAUSED"

        # 3. Resume
        r = client.post("/api/scheduler/resume")
        assert r.status_code == 200
        assert r.json()["status"]["running"] is True
        assert r.json()["status"]["state"] == "RUNNING"

        # 4. Pause again
        r = client.post("/api/scheduler/pause")
        assert r.status_code == 200
        assert r.json()["status"]["state"] == "PAUSED"

        # 5. Start while paused (should resume safely without SchedulerAlreadyRunningError)
        r = client.post("/api/scheduler/start")
        assert r.status_code == 200
        assert r.json()["status"]["running"] is True
        assert r.json()["status"]["state"] == "RUNNING"

        # 6. Stop
        r = client.post("/api/scheduler/stop")
        assert r.status_code == 200
        assert r.json()["status"]["running"] is False
        assert r.json()["status"]["state"] == "STOPPED"


@pytest.mark.asyncio
async def test_challenge1_edge_state_transitions_in_loop():
    """Verify idempotency on duplicate and out-of-order transitions inside active loop."""
    stop_scheduler()
    assert scheduler_status()["state"] == "STOPPED"

    # Pause while stopped -> non-destructive warning, remains STOPPED
    pause_scheduler()
    assert scheduler_status()["state"] == "STOPPED"

    # Start -> RUNNING
    start_scheduler()
    assert scheduler_status()["state"] == "RUNNING"

    # Start while RUNNING -> no-op, remains RUNNING
    start_scheduler()
    assert scheduler_status()["state"] == "RUNNING"

    # Resume while RUNNING -> no-op, remains RUNNING
    resume_scheduler()
    assert scheduler_status()["state"] == "RUNNING"

    # Pause -> PAUSED
    pause_scheduler()
    assert scheduler_status()["state"] == "PAUSED"

    # Pause while PAUSED -> no-op, remains PAUSED
    pause_scheduler()
    assert scheduler_status()["state"] == "PAUSED"

    # Stop -> STOPPED
    stop_scheduler()
    assert scheduler_status()["state"] == "STOPPED"

    # Stop while STOPPED -> no-op
    stop_scheduler()
    assert scheduler_status()["state"] == "STOPPED"


def test_challenge1_start_scheduler_outside_event_loop():
    """FINDING: Calling synchronous start_scheduler() when no event loop is running
    raises RuntimeError: no running event loop from APScheduler.
    """
    stop_scheduler()
    with pytest.raises(RuntimeError, match="no running event loop"):
        start_scheduler()


# ══════════════════════════════════════════════════════════════════════════════
# Challenge 2: Concurrent Triggers, Mutual Exclusion, & Database Integrity
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_challenge2_concurrent_debate_triggers():
    """Concurrent calls to /api/debate/run, trigger_debate_now(), and _debate_job().
    _debate_lock MUST serialize all runs such that max_concurrent_runs is strictly 1.
    """
    active_runs = 0
    max_concurrent_runs = 0
    total_completed = 0

    class ConcurrentTestEngine:
        async def run_debate(self):
            nonlocal active_runs, max_concurrent_runs, total_completed
            active_runs += 1
            max_concurrent_runs = max(max_concurrent_runs, active_runs)
            await asyncio.sleep(0.02)
            total_completed += 1

            # Persist to database to verify no SQLite locking issues
            sess = make_dummy_session(None)
            async with async_session_factory() as db:
                row = DebateSessionModel(
                    timestamp=sess.timestamp,
                    spot_price=sess.market_snapshot.spot_price,
                    market_data_json=sess.market_snapshot.model_dump_json(),
                    bull_reasoning=json.dumps(sess.bull_output.arguments),
                    bull_confidence=sess.bull_output.confidence,
                    bull_strike=sess.bull_output.recommended_strike,
                    bear_reasoning=json.dumps(sess.bear_output.arguments),
                    bear_confidence=sess.bear_output.confidence,
                    bear_strike=sess.bear_output.recommended_strike,
                    judge_decision=sess.judge_decision.decision,
                    judge_reasoning=sess.judge_decision.winning_argument_summary,
                    judge_full_output=sess.judge_decision.model_dump_json(),
                )
                db.add(row)
                await db.commit()
                await db.refresh(row)
                sess.id = row.id

            active_runs -= 1
            return sess

    # Clean table before test to ensure exact count assertion
    async with async_session_factory() as db:
        await db.execute(delete(PaperTradeModel))
        await db.execute(delete(DebateSessionModel))
        await db.commit()

    with patch("debate_engine.DebateEngine", ConcurrentTestEngine), \
         patch("scheduler.is_market_open", return_value=True):

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            tasks = []
            # 5 HTTP POST /api/debate/run
            for _ in range(5):
                tasks.append(client.post("/api/debate/run"))
            # 5 trigger_debate_now(bypass_market_hours=True)
            for _ in range(5):
                tasks.append(trigger_debate_now(bypass_market_hours=True))
            # 5 periodic _debate_job() ticks
            for _ in range(5):
                tasks.append(_debate_job())

            results = await asyncio.gather(*tasks, return_exceptions=True)

    # All 15 tasks should finish without unhandled exception
    for r in results:
        if isinstance(r, httpx.Response):
            assert r.status_code == 200

    assert total_completed == 15
    # Critical invariant: lock serialized execution
    assert max_concurrent_runs == 1, f"Lock failed! max_concurrent_runs was {max_concurrent_runs}"

    # Verify database persistence integrity
    async with async_session_factory() as db:
        count = (await db.execute(select(func.count(DebateSessionModel.id)))).scalar()
        assert count == 15


@pytest.mark.asyncio
async def test_challenge2_lock_release_on_exception():
    """Verify that if run_debate raises an exception, _debate_lock is released cleanly."""
    call_count = 0

    class FailingEngine:
        async def run_debate(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Engine explosion")
            return make_dummy_session(2)

    with patch("debate_engine.DebateEngine", FailingEngine):
        # 1. First call fails
        with pytest.raises(ValueError, match="Engine explosion"):
            await trigger_debate_now(bypass_market_hours=True)

        # 2. Subsequent call succeeds immediately (not deadlocked)
        res = await trigger_debate_now(bypass_market_hours=True)
        assert res is not None
        assert res.id == 2


def test_challenge2_cross_loop_lock_contention_failure_mode():
    """Verify that _debate_lock recovers cleanly across event loop lifecycles
    and does NOT crash with 'bound to a different event loop'.
    """
    engine_mock = AsyncMock()
    async def delayed():
        await asyncio.sleep(0.01)
        return "ok"
    engine_mock.run_debate.side_effect = delayed

    async def run_contention():
        await asyncio.gather(
            trigger_debate_now(engine=engine_mock),
            trigger_debate_now(engine=engine_mock),
        )

    # Both runs across separate event loops must now succeed cleanly
    asyncio.run(run_contention())
    asyncio.run(run_contention())


# ══════════════════════════════════════════════════════════════════════════════
# Challenge 3: Market Hours Boundary & Timezone Handling
# ══════════════════════════════════════════════════════════════════════════════

def test_challenge3_market_hours_ist_boundaries():
    """Test standard IST market hours boundaries: Mon-Fri 09:20 - 15:15 IST."""
    # Monday 09:19 IST -> False
    assert not is_market_open(datetime(2026, 11, 9, 9, 19, tzinfo=_IST))
    # Monday 09:20 IST -> True
    assert is_market_open(datetime(2026, 11, 9, 9, 20, tzinfo=_IST))
    # Monday 15:15 IST -> True
    assert is_market_open(datetime(2026, 11, 9, 15, 15, tzinfo=_IST))
    # Monday 15:16 IST -> False
    assert not is_market_open(datetime(2026, 11, 9, 15, 16, tzinfo=_IST))

    # Friday 09:20 IST -> True
    assert is_market_open(datetime(2026, 11, 13, 9, 20, tzinfo=_IST))
    # Friday 15:15 IST -> True
    assert is_market_open(datetime(2026, 11, 13, 15, 15, tzinfo=_IST))

    # Saturday 10:00 IST -> False (weekend)
    assert not is_market_open(datetime(2026, 11, 14, 10, 0, tzinfo=_IST))
    # Sunday 12:00 IST -> False (weekend)
    assert not is_market_open(datetime(2026, 11, 15, 12, 0, tzinfo=_IST))


def test_challenge3_utc_aware_timestamp_bug_demonstration():
    """Verify that is_market_open properly converts UTC timestamps to IST.
    1. At 09:20 IST on Monday (03:50 UTC), market IS OPEN.
    2. At 20:00 IST on Monday (14:30 UTC), market IS CLOSED.
    """
    # 1. Market Open Boundary: Monday 09:20 IST == 03:50 UTC
    dt_ist_open = datetime(2026, 11, 9, 9, 20, tzinfo=_IST)
    dt_utc_open = dt_ist_open.astimezone(timezone.utc)

    assert is_market_open(dt_ist_open) is True
    assert is_market_open(dt_utc_open) is True

    # 2. Market Closed (Night): Monday 20:00 IST == 14:30 UTC
    dt_ist_night = datetime(2026, 11, 9, 20, 0, tzinfo=_IST)
    dt_utc_night = dt_ist_night.astimezone(timezone.utc)

    assert is_market_open(dt_ist_night) is False
    assert is_market_open(dt_utc_night) is False


def test_challenge3_timezone_comprehensive():
    """Comprehensive validation of naive, UTC, and arbitrary timezones in is_market_open()."""
    # 1. Naive datetime: interpreted as local IST
    dt_naive_open = datetime(2026, 11, 9, 10, 30)
    assert is_market_open(dt_naive_open) is True

    dt_naive_closed = datetime(2026, 11, 9, 16, 0)
    assert is_market_open(dt_naive_closed) is False

    # 2. None check_time: returns a bool based on current time
    result = is_market_open(None)
    assert isinstance(result, bool)

    # 3. Market close boundary in UTC: 15:15 IST == 09:45 UTC -> True; 15:16 IST == 09:46 UTC -> False
    dt_utc_close_open = datetime(2026, 11, 9, 9, 45, tzinfo=timezone.utc)
    assert is_market_open(dt_utc_close_open) is True

    dt_utc_close_after = datetime(2026, 11, 9, 9, 46, tzinfo=timezone.utc)
    assert is_market_open(dt_utc_close_after) is False

    # 4. UTC Sunday night / IST Monday early morning (weekday rollover)
    # Sunday 23:55 UTC == Monday 05:25 IST (Weekday is Monday, but time is before 09:20)
    dt_utc_sun_night = datetime(2026, 11, 8, 23, 55, tzinfo=timezone.utc)
    assert is_market_open(dt_utc_sun_night) is False

    # 5. Non-UTC foreign timezone (America/New_York)
    # Monday 09:20 IST == Sunday 22:50 EST (UTC-5)
    from zoneinfo import ZoneInfo
    ny_tz = ZoneInfo("America/New_York")
    dt_ny = datetime(2026, 11, 8, 22, 50, tzinfo=ny_tz)
    assert is_market_open(dt_ny) is True
