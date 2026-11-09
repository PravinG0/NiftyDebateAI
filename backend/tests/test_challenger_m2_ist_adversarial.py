"""Adversarial Empirical Stress Test Suite for Milestone 2 Iteration 2 Gate (Challenger 2).

Mission:
1. IST Timing Verification:
   - to_ist(), to_ist_iso(), format_ist_display()
   - All timestamps returned by /api/status, /api/debate/latest, /api/debate/history, /api/trades contain +05:30.
   - APScheduler runs on 5-minute intervals during 09:20–15:15 IST and is paused/inactive outside those hours.
   - Countdown timer computation produces valid mm:ss string down to 00:00.
2. Concurrency & Model Stability:
   - Concurrent DB operations on DebateSessionModel and PaperTradeModel do not crash or corrupt database state.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import delete, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import event

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import settings
from database import (
    Base,
    DebateSessionModel,
    PaperTradeModel,
    async_session_factory,
    create_tables,
    engine,
)
from main import app
import scheduler
from scheduler import (
    get_scheduler,
    is_market_open,
    pause_scheduler,
    resume_scheduler,
    scheduler_status,
    start_scheduler,
    stop_scheduler,
    trigger_debate_now,
)
from time_utils import (
    IST,
    IST_OFFSET,
    calculate_countdown_seconds,
    format_countdown_timer,
    format_ist_display,
    is_market_open_ist,
    is_scheduler_window_ist,
    ist_now,
    to_ist,
    to_ist_iso,
)


@pytest.fixture(autouse=True)
def clean_scheduler():
    stop_scheduler()
    yield
    stop_scheduler()


# ══════════════════════════════════════════════════════════════════════════════
# 1. IST TIMING FUNCTIONS EMPIRICAL VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestISTTimingFunctionsAdversarial:
    """Stress test to_ist, to_ist_iso, and format_ist_display across diverse inputs."""

    def test_to_ist_none(self):
        """to_ist(None) must return current time in Asia/Kolkata."""
        now = to_ist(None)
        assert isinstance(now, datetime)
        assert now.tzinfo is not None
        assert str(now.tzinfo) == "Asia/Kolkata" or now.utcoffset() == timedelta(hours=5, minutes=30)

    def test_to_ist_naive_datetime(self):
        """Naive datetime should have IST timezone attached without hour shifting."""
        dt = datetime(2026, 11, 9, 14, 30, 0)
        res = to_ist(dt)
        assert res.tzinfo is not None
        assert res.hour == 14
        assert res.minute == 30
        assert res.utcoffset() == timedelta(hours=5, minutes=30)

    def test_to_ist_utc_datetime(self):
        """UTC aware datetime should be converted to IST (+5:30 offset)."""
        dt_utc = datetime(2026, 11, 9, 9, 0, 0, tzinfo=timezone.utc)
        res = to_ist(dt_utc)
        assert res.hour == 14
        assert res.minute == 30
        assert res.utcoffset() == timedelta(hours=5, minutes=30)

    def test_to_ist_other_timezones(self):
        """Timezones with negative and positive offsets must convert accurately to IST."""
        # US Eastern (UTC-5) 04:00 == UTC 09:00 == IST 14:30
        us_est = timezone(timedelta(hours=-5))
        dt_est = datetime(2026, 11, 9, 4, 0, 0, tzinfo=us_est)
        res_est = to_ist(dt_est)
        assert res_est.hour == 14
        assert res_est.minute == 30
        assert res_est.utcoffset() == timedelta(hours=5, minutes=30)

        # Tokyo (UTC+9) 18:00 == UTC 09:00 == IST 14:30
        tokyo_tz = timezone(timedelta(hours=9))
        dt_tokyo = datetime(2026, 11, 9, 18, 0, 0, tzinfo=tokyo_tz)
        res_tokyo = to_ist(dt_tokyo)
        assert res_tokyo.hour == 14
        assert res_tokyo.minute == 30
        assert res_tokyo.utcoffset() == timedelta(hours=5, minutes=30)

    def test_to_ist_iso_strings(self):
        """ISO strings with and without Z/offset must parse and normalize to IST."""
        # UTC string with Z
        res1 = to_ist("2026-11-09T03:50:00Z")
        assert res1.hour == 9
        assert res1.minute == 20
        assert res1.utcoffset() == timedelta(hours=5, minutes=30)

        # Naive ISO string
        res2 = to_ist("2026-11-09T09:20:00")
        assert res2.hour == 9
        assert res2.minute == 20
        assert res2.utcoffset() == timedelta(hours=5, minutes=30)

        # Display string with ' IST' suffix
        res3 = to_ist("2026-11-09 15:15:00 IST")
        assert res3.hour == 15
        assert res3.minute == 15
        assert res3.utcoffset() == timedelta(hours=5, minutes=30)

    def test_to_ist_iso_and_display_formatting(self):
        """Verify to_ist_iso() format contains +05:30 and format_ist_display() contains ' IST'."""
        dt = datetime(2026, 11, 9, 10, 15, 30, tzinfo=IST)
        iso_str = to_ist_iso(dt)
        assert iso_str == "2026-11-09T10:15:30+05:30"
        assert "+05:30" in iso_str

        display_str = format_ist_display(dt)
        assert display_str == "2026-11-09 10:15:30 IST"
        assert display_str.endswith(" IST")


# ══════════════════════════════════════════════════════════════════════════════
# 2. ENDPOINT TIMESTAMPS VERIFICATION (+05:30)
# ══════════════════════════════════════════════════════════════════════════════

class TestEndpointTimestampsAdversarial:
    """Empirically test that /api/status, /api/debate/*, /api/trades/* timestamps are +05:30."""

    @pytest.mark.asyncio
    async def test_api_status_timestamps(self):
        """Verify GET /api/status next_debate_ist and countdown."""
        start_scheduler()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/status")
                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "ok"

                # Countdown
                assert "countdown_seconds" in data
                assert isinstance(data["countdown_seconds"], int)
                assert data["countdown_seconds"] >= 0
                assert "countdown_timer" in data
                assert re.match(r"^\d{2}:\d{2}$", data["countdown_timer"])

                # If scheduler is running and has next_debate_ist
                if data.get("next_debate_ist"):
                    val = data["next_debate_ist"]
                    assert ("+05:30" in val) or val.endswith(" IST"), f"Invalid next_debate_ist: {val}"
        finally:
            stop_scheduler()

    @pytest.mark.asyncio
    async def test_api_debate_latest_and_history_timestamps(self):
        """Verify /api/debate/latest and /api/debate/history contain +05:30 and ' IST'."""
        await create_tables()

        async with async_session_factory() as session:
            d = DebateSessionModel(
                spot_price=24950.0,
                index_symbol="NIFTY",
                judge_decision="BUY_CE",
                judge_reasoning="Challenger 2 IST test",
                timestamp=datetime(2026, 11, 9, 11, 30, 0, tzinfo=IST),
            )
            session.add(d)
            await session.commit()
            await session.refresh(d)
            debate_id = d.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. /api/debate/latest
            res_lat = await client.get("/api/debate/latest")
            assert res_lat.status_code == 200
            latest = res_lat.json()
            assert "+05:30" in latest["timestamp"]
            assert "+05:30" in latest["created_at"]
            assert latest["timestamp_ist"].endswith(" IST")
            assert "+05:30" in latest["debate"]["timestamp"]
            assert latest["debate"]["timestamp_ist"].endswith(" IST")

            # 2. /api/debate/history
            res_hist = await client.get("/api/debate/history")
            assert res_hist.status_code == 200
            hist = res_hist.json()
            assert len(hist["debates"]) > 0
            d_item = next(x for x in hist["debates"] if x["id"] == debate_id)
            assert "+05:30" in d_item["timestamp"]
            assert "+05:30" in d_item["created_at"]
            assert d_item["timestamp_ist"].endswith(" IST")

    @pytest.mark.asyncio
    async def test_api_trades_timestamps(self):
        """Verify /api/trades, /api/trades/active, and trade close return +05:30."""
        await create_tables()

        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=24950.0, index_symbol="NIFTY", judge_decision="BUY_CE")
            session.add(d)
            await session.flush()

            t = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV25000CE",
                index_symbol="NIFTY",
                trade_type="BUY_CE",
                entry_price=100.0,
                stop_loss=80.0,
                target=140.0,
                status="OPEN",
                quantity=25,
                opened_at=datetime(2026, 11, 9, 10, 0, 0, tzinfo=IST),
            )
            session.add(t)
            await session.commit()
            await session.refresh(t)
            trade_id = t.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # /api/trades
            res_all = await client.get("/api/trades")
            assert res_all.status_code == 200
            trades = res_all.json()
            tr = next(x for x in trades if x["id"] == trade_id)
            assert "+05:30" in tr["opened_at"]
            assert "+05:30" in tr["created_at"]
            assert "+05:30" in tr["timestamp"]
            assert tr["opened_at_display"].endswith(" IST")

            # /api/trades/active
            res_act = await client.get("/api/trades/active")
            assert res_act.status_code == 200
            active = res_act.json()
            tr_act = next(x for x in active if x["id"] == trade_id)
            assert "+05:30" in tr_act["opened_at"]
            assert tr_act["opened_at_display"].endswith(" IST")

            # /api/trades/{id}/close
            res_close = await client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 120.0})
            assert res_close.status_code == 200
            closed = res_close.json()
            assert closed["status"] == "CLOSED"
            assert "+05:30" in closed["opened_at"]
            assert closed["closed_at"] is not None
            assert "+05:30" in closed["closed_at"]
            assert closed["closed_at_display"].endswith(" IST")


# ══════════════════════════════════════════════════════════════════════════════
# 3. SCHEDULER MARKET HOURS & INTERVAL VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestSchedulerMarketHoursAdversarial:
    """Stress-test 5-minute interval during 09:20–15:15 IST and inactive outside."""

    def test_scheduler_market_hours_configuration(self):
        """Verify config values for market open, close, and interval."""
        assert settings.MARKET_OPEN == "09:20"
        assert settings.MARKET_CLOSE == "15:15"
        assert settings.DEBATE_INTERVAL_MINUTES == 5

    def test_scheduler_market_window_exact_boundaries(self):
        """Check minute-by-minute boundary conditions for 09:20–15:15 IST."""
        # Monday (weekday)
        # 09:19 IST -> False
        assert is_market_open(datetime(2026, 11, 9, 9, 19, 0, tzinfo=IST)) is False
        assert is_scheduler_window_ist(datetime(2026, 11, 9, 9, 19, 59, tzinfo=IST)) is False

        # 09:20 IST -> True
        assert is_market_open(datetime(2026, 11, 9, 9, 20, 0, tzinfo=IST)) is True
        assert is_scheduler_window_ist(datetime(2026, 11, 9, 9, 20, 0, tzinfo=IST)) is True

        # 12:00 IST -> True
        assert is_market_open(datetime(2026, 11, 9, 12, 0, 0, tzinfo=IST)) is True

        # 15:15 IST -> True
        assert is_market_open(datetime(2026, 11, 9, 15, 15, 0, tzinfo=IST)) is True
        assert is_scheduler_window_ist(datetime(2026, 11, 9, 15, 15, 0, tzinfo=IST)) is True

        # 15:16 IST -> False
        assert is_market_open(datetime(2026, 11, 9, 15, 16, 0, tzinfo=IST)) is False
        assert is_scheduler_window_ist(datetime(2026, 11, 9, 15, 16, 0, tzinfo=IST)) is False

        # Saturday & Sunday -> False
        assert is_market_open(datetime(2026, 11, 14, 11, 0, 0, tzinfo=IST)) is False
        assert is_market_open(datetime(2026, 11, 15, 11, 0, 0, tzinfo=IST)) is False

    @pytest.mark.asyncio
    async def test_scheduler_trigger_skips_outside_hours(self):
        """trigger_debate_now(bypass_market_hours=False) returns None when market closed."""
        with patch("scheduler.is_market_open", return_value=False):
            res = await trigger_debate_now(bypass_market_hours=False)
            assert res is None

    @pytest.mark.asyncio
    async def test_scheduler_job_trigger_properties(self):
        """Verify scheduler job uses 5-minute interval in IST timezone."""
        sched = get_scheduler()
        start_scheduler()
        try:
            job = sched.get_job("debate_job")
            assert job is not None
            # Trigger check
            trigger = job.trigger
            assert str(trigger.timezone) == "Asia/Kolkata" or trigger.timezone.utcoffset(datetime.now()) == timedelta(hours=5, minutes=30)
            # Check 5 minutes interval
            assert trigger.interval == timedelta(minutes=5)
        finally:
            stop_scheduler()

    @pytest.mark.asyncio
    async def test_scheduler_lifecycle_transitions(self):
        """Test start -> pause -> resume -> stop lifecycle transitions."""
        start_scheduler()
        st1 = scheduler_status()
        assert st1["running"] is True
        assert st1["paused"] is False
        assert st1["state"] == "RUNNING"

        pause_scheduler()
        st2 = scheduler_status()
        assert st2["paused"] is True
        assert st2["state"] == "PAUSED"

        resume_scheduler()
        st3 = scheduler_status()
        assert st3["running"] is True
        assert st3["paused"] is False
        assert st3["state"] == "RUNNING"

        stop_scheduler()
        st4 = scheduler_status()
        assert st4["running"] is False
        assert st4["state"] == "STOPPED"


# ══════════════════════════════════════════════════════════════════════════════
# 4. COUNTDOWN TIMER ADVERSARIAL VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestCountdownTimerAdversarial:
    """Empirically test countdown calculations down to 00:00 and clamp safety."""

    def test_countdown_seconds_calculation(self):
        """calculate_countdown_seconds on 5-minute intervals."""
        # 10:00:00 -> 300 seconds
        assert calculate_countdown_seconds(datetime(2026, 11, 9, 10, 0, 0, tzinfo=IST)) == 300

        # 10:04:59 -> 1 second
        assert calculate_countdown_seconds(datetime(2026, 11, 9, 10, 4, 59, tzinfo=IST)) == 1

        # 10:05:00 -> 300 seconds
        assert calculate_countdown_seconds(datetime(2026, 11, 9, 10, 5, 0, tzinfo=IST)) == 300

        # 10:02:30 -> (5*60) - 150 = 150 seconds
        assert calculate_countdown_seconds(datetime(2026, 11, 9, 10, 2, 30, tzinfo=IST)) == 150

    def test_countdown_timer_formatting_down_to_zero(self):
        """format_countdown_timer produces mm:ss down to 00:00 and clamps negative numbers."""
        assert format_countdown_timer(300) == "05:00"
        assert format_countdown_timer(299) == "04:59"
        assert format_countdown_timer(60) == "01:00"
        assert format_countdown_timer(59) == "00:59"
        assert format_countdown_timer(1) == "00:01"
        assert format_countdown_timer(0) == "00:00"
        assert format_countdown_timer(-1) == "00:00"
        assert format_countdown_timer(-100) == "00:00"


# ══════════════════════════════════════════════════════════════════════════════
# 5. CONCURRENCY & MODEL STABILITY ADVERSARIAL STRESS TEST
# ══════════════════════════════════════════════════════════════════════════════

class TestConcurrencyAndModelStabilityAdversarial:
    """Stress test high concurrency DB operations on DebateSessionModel & PaperTradeModel."""

    @pytest.mark.asyncio
    async def test_high_concurrency_inserts_and_queries(self, tmp_path):
        """Stress test 80 concurrent async coroutines performing inserts, queries, updates."""
        stress_db = tmp_path / "adv_stress.db"
        stress_engine = create_async_engine(
            f"sqlite+aiosqlite:///{stress_db}",
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(stress_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        stress_factory = async_sessionmaker(stress_engine, class_=AsyncSession, expire_on_commit=False)

        async with stress_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def insert_task(i: int):
            sym = "BANKNIFTY" if i % 2 == 0 else "NIFTY"
            lot = 15 if sym == "BANKNIFTY" else 25
            async with stress_factory() as session:
                sess = DebateSessionModel(
                    spot_price=52000.0 if sym == "BANKNIFTY" else 25000.0,
                    index_symbol=sym,
                    judge_decision="BUY_CE" if i % 3 == 0 else "BUY_PE",
                    judge_reasoning=f"Adversarial worker {i}",
                )
                session.add(sess)
                await session.flush()

                trade = PaperTradeModel(
                    debate_id=sess.id,
                    option_symbol=f"{sym}_OPT_{i}",
                    index_symbol=sym,
                    trade_type=sess.judge_decision,
                    entry_price=100.0 + (i % 20),
                    stop_loss=80.0,
                    target=140.0,
                    status="OPEN",
                    quantity=lot,
                )
                session.add(trade)
                await session.commit()
                return sess.id, trade.id

        tasks = [insert_task(i) for i in range(80)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check zero exceptions
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 0, f"Concurrency errors observed: {errors}"

        # Verify DB integrity
        async with stress_factory() as session:
            num_sess = (await session.execute(select(func.count(DebateSessionModel.id)))).scalar()
            num_trades = (await session.execute(select(func.count(PaperTradeModel.id)))).scalar()
            assert num_sess == 80
            assert num_trades == 80

            # Verify model defaults and types
            sample_trade = (await session.execute(select(PaperTradeModel).limit(1))).scalar_one()
            assert isinstance(sample_trade.quantity, int)
            assert type(sample_trade.quantity) is int  # Genuine int, not subclass
            assert sample_trade.trade_source == "AI_DEBATE"

        await stress_engine.dispose()

    @pytest.mark.asyncio
    async def test_foreign_key_cascade_deletion(self, tmp_path):
        """Verify cascade deletion: deleting DebateSessionModel deletes associated PaperTradeModel."""
        stress_db = tmp_path / "cascade_test.db"
        stress_engine = create_async_engine(
            f"sqlite+aiosqlite:///{stress_db}",
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(stress_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        stress_factory = async_sessionmaker(stress_engine, class_=AsyncSession, expire_on_commit=False)

        async with stress_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with stress_factory() as session:
            sess = DebateSessionModel(spot_price=25000.0, index_symbol="NIFTY", judge_decision="BUY_CE")
            session.add(sess)
            await session.flush()

            trade = PaperTradeModel(
                debate_id=sess.id,
                option_symbol="NIFTY26NOV25000CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                stop_loss=80.0,
                target=140.0,
                quantity=25,
            )
            session.add(trade)
            await session.commit()
            sess_id = sess.id
            trade_id = trade.id

        # Delete session
        async with stress_factory() as session:
            s_to_del = (await session.execute(select(DebateSessionModel).where(DebateSessionModel.id == sess_id))).scalar_one()
            await session.delete(s_to_del)
            await session.commit()

        # Verify trade was cascade deleted
        async with stress_factory() as session:
            remaining_trades = (await session.execute(select(PaperTradeModel).where(PaperTradeModel.id == trade_id))).scalars().all()
            assert len(remaining_trades) == 0

        await stress_engine.dispose()
