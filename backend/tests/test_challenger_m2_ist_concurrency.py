"""
Milestone 2 Challenger 2 Empirical Stress Test Suite:
1. IST Timing & Boundary Conditions:
   - Pre-market (09:14:59), market open (09:15:00), market close (15:30:00), post-market (15:30:01), weekend.
   - APScheduler window (09:20:00 - 15:15:00 IST) boundary conditions.
   - Countdown calculations: negative delta, 0 delta, 1s, 300s, formatted output ('mm:ss').
   - ISO strings verification: ensure all emitted datetime strings contain '+05:30'.
2. Database Concurrency & Column Migrations:
   - High-concurrency async inserts of DebateSessionModel and PaperTradeModel with index_symbol.
   - SQLite timezone preservation through ISTDateTime (naive, UTC-aware, IST-aware, ISO strings).
   - Column migrations idempotency and defaults.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, desc, func, select, text

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
    get_scheduler,
    is_market_open,
    pause_scheduler,
    resume_scheduler,
    scheduler_status,
    start_scheduler,
    stop_scheduler,
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
def cleanup_scheduler_and_state():
    """Ensure clean scheduler state before and after each test."""
    stop_scheduler()
    yield
    stop_scheduler()


# ══════════════════════════════════════════════════════════════════════════════
# 1. IST TIMING & BOUNDARY CONDITIONS
# ══════════════════════════════════════════════════════════════════════════════

class TestISTTimingAndBoundaries:
    """Stress-test IST market hours, scheduler windows, and weekend boundaries."""

    def test_market_open_ist_boundaries(self):
        """Test pre-market, open (09:15), close (15:30), post-market, and weekend."""
        # 2026-11-09 is Monday (weekday)
        # Pre-market: 09:14:59 IST
        pre_market = datetime(2026, 11, 9, 9, 14, 59, tzinfo=IST)
        assert is_market_open_ist(pre_market) is False

        # Market open exact: 09:15:00 IST
        market_open = datetime(2026, 11, 9, 9, 15, 0, tzinfo=IST)
        assert is_market_open_ist(market_open) is True

        # Mid-day trading: 12:30:00 IST
        mid_day = datetime(2026, 11, 9, 12, 30, 0, tzinfo=IST)
        assert is_market_open_ist(mid_day) is True

        # Market close exact: 15:30:00 IST
        market_close = datetime(2026, 11, 9, 15, 30, 0, tzinfo=IST)
        assert is_market_open_ist(market_close) is True

        # Post-market: 15:30:01 IST
        post_market = datetime(2026, 11, 9, 15, 30, 1, tzinfo=IST)
        assert is_market_open_ist(post_market) is False

        # Evening: 20:00:00 IST
        evening = datetime(2026, 11, 9, 20, 0, 0, tzinfo=IST)
        assert is_market_open_ist(evening) is False

    def test_market_open_weekend_rejection(self):
        """Verify Saturday and Sunday timestamps are strictly closed."""
        # 2026-11-14 is Saturday
        sat_open_time = datetime(2026, 11, 14, 10, 30, 0, tzinfo=IST)
        assert is_market_open_ist(sat_open_time) is False

        # 2026-11-15 is Sunday
        sun_open_time = datetime(2026, 11, 15, 11, 0, 0, tzinfo=IST)
        assert is_market_open_ist(sun_open_time) is False

    def test_market_open_utc_timezone_conversion(self):
        """Verify UTC timestamps are converted to IST before checking market hours."""
        # 09:15:00 IST corresponds to 03:45:00 UTC
        utc_pre_open = datetime(2026, 11, 9, 3, 44, 59, tzinfo=timezone.utc)
        assert is_market_open_ist(utc_pre_open) is False

        utc_open = datetime(2026, 11, 9, 3, 45, 0, tzinfo=timezone.utc)
        assert is_market_open_ist(utc_open) is True

        # 15:30:00 IST corresponds to 10:00:00 UTC
        utc_close = datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc)
        assert is_market_open_ist(utc_close) is True

        utc_post_close = datetime(2026, 11, 9, 10, 0, 1, tzinfo=timezone.utc)
        assert is_market_open_ist(utc_post_close) is False

    def test_scheduler_window_ist_boundaries(self):
        """Verify APScheduler window (09:20 - 15:15 IST) boundary conditions."""
        # 09:19:59 IST -> Outside window
        t_0919 = datetime(2026, 11, 9, 9, 19, 59, tzinfo=IST)
        assert is_scheduler_window_ist(t_0919) is False
        assert scheduler.is_market_open(t_0919) is False

        # 09:20:00 IST -> Exactly window start
        t_0920 = datetime(2026, 11, 9, 9, 20, 0, tzinfo=IST)
        assert is_scheduler_window_ist(t_0920) is True
        assert scheduler.is_market_open(t_0920) is True

        # 12:00:00 IST -> Within window
        t_1200 = datetime(2026, 11, 9, 12, 0, 0, tzinfo=IST)
        assert is_scheduler_window_ist(t_1200) is True
        assert scheduler.is_market_open(t_1200) is True

        # 15:15:00 IST -> Exactly window end
        t_1515 = datetime(2026, 11, 9, 15, 15, 0, tzinfo=IST)
        assert is_scheduler_window_ist(t_1515) is True
        assert scheduler.is_market_open(t_1515) is True

        # 15:15:59 IST -> Same minute boundary
        t_1515_59 = datetime(2026, 11, 9, 15, 15, 59, tzinfo=IST)
        assert is_scheduler_window_ist(t_1515_59) is True

        # 15:16:00 IST -> Past window
        t_1516 = datetime(2026, 11, 9, 15, 16, 0, tzinfo=IST)
        assert is_scheduler_window_ist(t_1516) is False
        assert scheduler.is_market_open(t_1516) is False

        # Weekend test for scheduler window
        t_sat_1000 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=IST)
        assert is_scheduler_window_ist(t_sat_1000) is False
        assert scheduler.is_market_open(t_sat_1000) is False

    def test_scheduler_utc_input_conversion(self):
        """Verify scheduler market check correctly converts UTC input."""
        # 09:20 IST == 03:50 UTC
        utc_0350 = datetime(2026, 11, 9, 3, 50, 0, tzinfo=timezone.utc)
        assert scheduler.is_market_open(utc_0350) is True

        utc_0349 = datetime(2026, 11, 9, 3, 49, 0, tzinfo=timezone.utc)
        assert scheduler.is_market_open(utc_0349) is False

        # 15:15 IST == 09:45 UTC
        utc_0945 = datetime(2026, 11, 9, 9, 45, 0, tzinfo=timezone.utc)
        assert scheduler.is_market_open(utc_0945) is True

        utc_0946 = datetime(2026, 11, 9, 9, 46, 0, tzinfo=timezone.utc)
        assert scheduler.is_market_open(utc_0946) is False


# ══════════════════════════════════════════════════════════════════════════════
# 2. COUNTDOWN CALCULATION ACROSS TIME DELTAS
# ══════════════════════════════════════════════════════════════════════════════

class TestCountdownCalculations:
    """Stress-test countdown seconds and mm:ss formatting across edge cases."""

    def test_format_countdown_timer_deltas(self):
        """Verify format_countdown_timer across negative delta, 0 delta, 1s, 300s."""
        # Negative deltas should clamp cleanly to 00:00 without crashing or producing negative strings
        assert format_countdown_timer(-1) == "00:00"
        assert format_countdown_timer(-300) == "00:00"
        assert format_countdown_timer(-9999) == "00:00"

        # 0 delta
        assert format_countdown_timer(0) == "00:00"

        # 1 second
        assert format_countdown_timer(1) == "00:01"

        # Under 60 seconds
        assert format_countdown_timer(5) == "00:05"
        assert format_countdown_timer(42) == "00:42"
        assert format_countdown_timer(59) == "00:59"

        # 60 seconds
        assert format_countdown_timer(60) == "01:00"

        # Multiple minutes
        assert format_countdown_timer(125) == "02:05"

        # 300 seconds (5 minutes interval)
        assert format_countdown_timer(300) == "05:00"

        # Over 5 minutes (e.g. 10 minutes)
        assert format_countdown_timer(600) == "10:00"

    def test_calculate_countdown_seconds_interval_boundaries(self):
        """Verify calculate_countdown_seconds for various clock points."""
        # At exact 5-min mark: 10:00:00 -> exactly 300s remaining until 10:05:00
        t_1000 = datetime(2026, 11, 9, 10, 0, 0, tzinfo=IST)
        assert calculate_countdown_seconds(t_1000, interval_minutes=5) == 300

        # 1 second before mark: 10:04:59 -> 1s remaining
        t_100459 = datetime(2026, 11, 9, 10, 4, 59, tzinfo=IST)
        assert calculate_countdown_seconds(t_100459, interval_minutes=5) == 1
        assert format_countdown_timer(calculate_countdown_seconds(t_100459)) == "00:01"

        # Mid-interval: 10:02:15 -> (5*60) - (2*60 + 15) = 300 - 135 = 165s
        t_100215 = datetime(2026, 11, 9, 10, 2, 15, tzinfo=IST)
        assert calculate_countdown_seconds(t_100215, interval_minutes=5) == 165
        assert format_countdown_timer(165) == "02:45"

        # 1 second past mark: 10:05:01 -> (5*60) - 1 = 299s
        t_100501 = datetime(2026, 11, 9, 10, 5, 1, tzinfo=IST)
        assert calculate_countdown_seconds(t_100501, interval_minutes=5) == 299
        assert format_countdown_timer(299) == "04:59"

    def test_custom_intervals(self):
        """Verify custom interval minutes in countdown calculation."""
        # 1-minute interval: at 10:00:45 -> 15s remaining
        t = datetime(2026, 11, 9, 10, 0, 45, tzinfo=IST)
        assert calculate_countdown_seconds(t, interval_minutes=1) == 15
        assert format_countdown_timer(15) == "00:15"


# ══════════════════════════════════════════════════════════════════════════════
# 3. VERIFY ISO STRINGS EMITTED BY ALL ENDPOINTS CONTAIN +05:30
# ══════════════════════════════════════════════════════════════════════════════

class TestEndpointISOStringsContainOffset:
    """Empirically test that ISO strings emitted by REST endpoints contain '+05:30'."""

    @pytest.mark.asyncio
    async def test_scheduler_status_iso_string_contains_offset(self):
        """GET /api/scheduler/status emits next_run with +05:30 when scheduler is running."""
        start_scheduler()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                res = await client.get("/api/scheduler/status")
                assert res.status_code == 200
                data = res.json()
                if data.get("next_run"):
                    assert "+05:30" in data["next_run"], f"next_run {data['next_run']} missing +05:30"
                if data.get("next_run_time"):
                    assert "+05:30" in data["next_run_time"], f"next_run_time {data['next_run_time']} missing +05:30"
        finally:
            stop_scheduler()

    @pytest.mark.asyncio
    async def test_trades_endpoints_iso_strings_contain_offset(self):
        """Verify /api/trades, /api/trades/active, and /api/trades/{id}/close emit +05:30."""
        await create_tables()

        # Seed an active trade
        async with async_session_factory() as session:
            sess = DebateSessionModel(
                spot_price=24900.0,
                index_symbol="NIFTY",
                judge_decision="BUY_CE",
                judge_reasoning="Adversarial ISO test session",
            )
            session.add(sess)
            await session.commit()
            await session.refresh(sess)

            trade = PaperTradeModel(
                debate_id=sess.id,
                option_symbol="NIFTY26NOV25000CE",
                index_symbol="NIFTY",
                trade_type="BUY_CE",
                entry_price=120.0,
                stop_loss=90.0,
                target=170.0,
                status="OPEN",
                quantity=25,
            )
            session.add(trade)
            await session.commit()
            await session.refresh(trade)
            trade_id = trade.id

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 1. Test /api/trades
            res_all = await client.get("/api/trades")
            assert res_all.status_code == 200
            trades = res_all.json()
            assert len(trades) > 0
            t_obj = next(t for t in trades if t["id"] == trade_id)
            assert "+05:30" in t_obj["opened_at"], f"opened_at {t_obj['opened_at']} lacks +05:30"
            assert "+05:30" in t_obj["created_at"], f"created_at {t_obj['created_at']} lacks +05:30"
            assert "+05:30" in t_obj["timestamp"], f"timestamp {t_obj['timestamp']} lacks +05:30"

            # 2. Test /api/trades/active
            res_active = await client.get("/api/trades/active")
            assert res_active.status_code == 200
            active_trades = res_active.json()
            t_act = next(t for t in active_trades if t["id"] == trade_id)
            assert "+05:30" in t_act["opened_at"], f"active opened_at {t_act['opened_at']} lacks +05:30"

            # 3. Test /api/trades/{id}/close
            res_close = await client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 140.0})
            assert res_close.status_code == 200
            closed_trade = res_close.json()
            assert closed_trade["status"] == "CLOSED"
            assert "+05:30" in closed_trade["opened_at"], f"closed opened_at lacks +05:30"
            assert closed_trade["closed_at"] is not None
            assert "+05:30" in closed_trade["closed_at"], f"closed_at {closed_trade['closed_at']} lacks +05:30"

    @pytest.mark.asyncio
    async def test_manual_trade_endpoint_iso_strings(self):
        """Verify POST /api/trades/manual emits trade with +05:30 timestamp."""
        await create_tables()

        payload = {
            "symbol": "BANKNIFTY",
            "strike": 52100,
            "option_type": "CE",
            "lots": 1,
            "entry_price": 200.0,
            "stop_loss": 160.0,
            "target": 260.0,
        }

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/trades/manual", json=payload)
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            trade = data["trade"]
            assert trade["index_symbol"] == "BANKNIFTY"
            assert trade["lot_size"] == 15
            assert "+05:30" in trade["opened_at"], f"manual trade opened_at lacks +05:30"
            assert "+05:30" in trade["created_at"], f"manual trade created_at lacks +05:30"

    @pytest.mark.asyncio
    async def test_debate_endpoints_iso_strings(self):
        """Verify debate history and latest endpoints emit +05:30 timestamps."""
        await create_tables()

        async with async_session_factory() as session:
            sess = DebateSessionModel(
                spot_price=52200.0,
                index_symbol="BANKNIFTY",
                judge_decision="BUY_PE",
                judge_reasoning="Empirical debate check",
            )
            session.add(sess)
            await session.commit()
            await session.refresh(sess)
            sess_id = sess.id

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Latest
            res_latest = await client.get("/api/debate/latest")
            assert res_latest.status_code == 200
            latest = res_latest.json()
            assert "+05:30" in latest["timestamp"], f"latest debate timestamp lacks +05:30"
            assert "+05:30" in latest["created_at"], f"latest debate created_at lacks +05:30"

            # History
            res_hist = await client.get("/api/debate/history")
            assert res_hist.status_code == 200
            hist = res_hist.json()
            debates = hist.get("debates", [])
            assert len(debates) > 0
            for d in debates:
                assert "+05:30" in d["timestamp"], f"history debate {d['id']} timestamp lacks +05:30"
                assert "+05:30" in d["created_at"], f"history debate {d['id']} created_at lacks +05:30"

            # Single ID
            res_single = await client.get(f"/api/debate/{sess_id}")
            assert res_single.status_code == 200
            single = res_single.json()
            assert "+05:30" in single["timestamp"], f"single debate timestamp lacks +05:30"


# ══════════════════════════════════════════════════════════════════════════════
# 4. DATABASE CONCURRENCY & COLUMN MIGRATIONS
# ══════════════════════════════════════════════════════════════════════════════

class TestDatabaseConcurrencyAndMigrations:
    """Stress-test high concurrency inserts of DebateSessionModel & PaperTradeModel."""

    @pytest.mark.asyncio
    async def test_concurrent_session_and_trade_inserts(self, tmp_path):
        """Stress test: 50 concurrent tasks inserting sessions & trades with mixed index_symbols."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool
        from sqlalchemy import event
        from database import Base

        db_path = tmp_path / "concurrency_stress.db"
        test_engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(test_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        test_session_factory = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def worker_insert(task_id: int):
            sym = "BANKNIFTY" if task_id % 2 == 0 else "NIFTY"
            spot = 52000.0 + task_id if sym == "BANKNIFTY" else 24900.0 + task_id
            lot_size = 15 if sym == "BANKNIFTY" else 25

            async with test_session_factory() as session:
                sess = DebateSessionModel(
                    spot_price=spot,
                    index_symbol=sym,
                    judge_decision="BUY_CE" if task_id % 3 == 0 else "BUY_PE",
                    judge_reasoning=f"Stress worker {task_id} on {sym}",
                )
                session.add(sess)
                await session.flush()

                trade = PaperTradeModel(
                    debate_id=sess.id,
                    option_symbol=f"{sym}OPT_{task_id}",
                    index_symbol=sym,
                    trade_type=sess.judge_decision,
                    entry_price=100.0 + (task_id % 50),
                    stop_loss=80.0,
                    target=140.0,
                    status="OPEN",
                    quantity=lot_size,
                )
                session.add(trade)
                await session.commit()
                return sess.id, trade.id, sym

        # Fire 50 concurrent inserts
        tasks = [worker_insert(i) for i in range(50)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Ensure NO task threw an exception (e.g. database locked)
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0, f"Concurrent inserts failed with exceptions: {exceptions}"

        # Verify all 50 sessions and trades exist in the database
        async with test_session_factory() as session:
            sess_count = (await session.execute(select(func.count(DebateSessionModel.id)))).scalar()
            trade_count = (await session.execute(select(func.count(PaperTradeModel.id)))).scalar()
            assert sess_count == 50
            assert trade_count == 50

            # Verify index_symbol distribution
            nifty_sess = (await session.execute(
                select(func.count(DebateSessionModel.id)).where(DebateSessionModel.index_symbol == "NIFTY")
            )).scalar()
            bank_sess = (await session.execute(
                select(func.count(DebateSessionModel.id)).where(DebateSessionModel.index_symbol == "BANKNIFTY")
            )).scalar()
            assert nifty_sess == 25
            assert bank_sess == 25

        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_ist_datetime_preservation(self):
        """Verify that SQLite correctly preserves IST timezone offsets through ISTDateTime."""
        await create_tables()

        # Test varying datetime representations
        dt_naive = datetime(2026, 11, 9, 14, 30, 0)
        dt_ist = datetime(2026, 11, 9, 14, 30, 0, tzinfo=IST)
        dt_utc = datetime(2026, 11, 9, 9, 0, 0, tzinfo=timezone.utc)  # 09:00 UTC == 14:30 IST

        async with async_session_factory() as session:
            s_naive = DebateSessionModel(
                timestamp=dt_naive,
                spot_price=24900.0,
                index_symbol="NIFTY",
                judge_decision="NO_TRADE",
            )
            s_ist = DebateSessionModel(
                timestamp=dt_ist,
                spot_price=24900.0,
                index_symbol="NIFTY",
                judge_decision="NO_TRADE",
            )
            s_utc = DebateSessionModel(
                timestamp=dt_utc,
                spot_price=24900.0,
                index_symbol="NIFTY",
                judge_decision="NO_TRADE",
            )
            session.add_all([s_naive, s_ist, s_utc])
            await session.commit()
            naive_id, ist_id, utc_id = s_naive.id, s_ist.id, s_utc.id

        # Query back and verify timezone attributes
        async with async_session_factory() as session:
            q_naive = (await session.execute(select(DebateSessionModel).where(DebateSessionModel.id == naive_id))).scalar_one()
            q_ist = (await session.execute(select(DebateSessionModel).where(DebateSessionModel.id == ist_id))).scalar_one()
            q_utc = (await session.execute(select(DebateSessionModel).where(DebateSessionModel.id == utc_id))).scalar_one()

            # 1. Ensure tzinfo is not None
            assert q_naive.timestamp.tzinfo is not None
            assert q_ist.timestamp.tzinfo is not None
            assert q_utc.timestamp.tzinfo is not None

            # 2. Ensure timezone offset is exactly +05:30
            assert q_naive.timestamp.utcoffset() == timedelta(hours=5, minutes=30)
            assert q_ist.timestamp.utcoffset() == timedelta(hours=5, minutes=30)
            assert q_utc.timestamp.utcoffset() == timedelta(hours=5, minutes=30)

            # 3. Ensure UTC 09:00 was correctly converted to IST 14:30
            assert q_utc.timestamp.hour == 14
            assert q_utc.timestamp.minute == 30

            # 4. Ensure ISO representation contains +05:30
            assert "+05:30" in q_naive.timestamp.isoformat()
            assert "+05:30" in q_ist.timestamp.isoformat()
            assert "+05:30" in q_utc.timestamp.isoformat()

    @pytest.mark.asyncio
    async def test_column_migrations_idempotency(self):
        """Verify that create_tables() migration SQL statements are completely idempotent."""
        # Calling create_tables multiple times in succession should never fail
        await create_tables()
        await create_tables()
        await create_tables()

        # Verify index_symbol column works as expected
        async with async_session_factory() as session:
            sess = DebateSessionModel(
                spot_price=25000.0,
                judge_decision="NO_TRADE",
            )
            session.add(sess)
            await session.commit()
            await session.refresh(sess)

            # Check default server / python value
            assert sess.index_symbol == "NIFTY"

    @pytest.mark.asyncio
    async def test_mixed_read_write_update_concurrency(self, tmp_path):
        """Stress test: 100 mixed concurrent operations (inserts, queries, updates) under WAL mode."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool
        from sqlalchemy import event
        from database import Base

        db_path = tmp_path / "mixed_concurrency.db"
        test_engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(test_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        test_session_factory = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed initial session and trade for update workers
        async with test_session_factory() as session:
            seed_sess = DebateSessionModel(spot_price=25000.0, index_symbol="NIFTY", judge_decision="BUY_CE")
            session.add(seed_sess)
            await session.flush()
            seed_trade = PaperTradeModel(
                debate_id=seed_sess.id,
                option_symbol="NIFTY26NOV25000CE",
                index_symbol="NIFTY",
                trade_type="BUY_CE",
                entry_price=100.0,
                stop_loss=80.0,
                target=140.0,
                status="OPEN",
                quantity=25,
            )
            session.add(seed_trade)
            await session.commit()
            seed_trade_id = seed_trade.id

        async def insert_worker(i: int):
            sym = "BANKNIFTY" if i % 2 == 0 else "NIFTY"
            lot = 15 if sym == "BANKNIFTY" else 25
            async with test_session_factory() as session:
                sess = DebateSessionModel(
                    spot_price=52000.0 if sym == "BANKNIFTY" else 25000.0,
                    index_symbol=sym,
                    judge_decision="BUY_CE",
                )
                session.add(sess)
                await session.flush()
                tr = PaperTradeModel(
                    debate_id=sess.id,
                    option_symbol=f"{sym}_OPT_{i}",
                    index_symbol=sym,
                    trade_type="BUY_CE",
                    entry_price=150.0,
                    stop_loss=120.0,
                    target=200.0,
                    status="OPEN",
                    quantity=lot,
                )
                session.add(tr)
                await session.commit()
                return "insert", tr.id

        async def query_worker(i: int):
            sym = "BANKNIFTY" if i % 2 == 0 else "NIFTY"
            async with test_session_factory() as session:
                stmt = select(PaperTradeModel).where(PaperTradeModel.index_symbol == sym)
                rows = (await session.execute(stmt)).scalars().all()
                return "query", len(rows)

        async def update_worker(i: int):
            async with test_session_factory() as session:
                stmt = select(PaperTradeModel).where(PaperTradeModel.id == seed_trade_id)
                tr = (await session.execute(stmt)).scalars().first()
                if tr:
                    tr.exit_price = 120.0 + (i % 10)
                    tr.pnl = (tr.exit_price - tr.entry_price) * tr.quantity
                    await session.commit()
                return "update", seed_trade_id

        # 40 inserts + 30 queries + 30 updates = 100 concurrent tasks
        tasks = (
            [insert_worker(i) for i in range(40)]
            + [query_worker(i) for i in range(30)]
            + [update_worker(i) for i in range(30)]
        )
        import random
        random.shuffle(tasks)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0, f"Mixed concurrency had exceptions: {exceptions}"

        # Verify final state
        async with test_session_factory() as session:
            final_sess = (await session.execute(select(func.count(DebateSessionModel.id)))).scalar()
            final_trades = (await session.execute(select(func.count(PaperTradeModel.id)))).scalar()
            assert final_sess == 41  # 1 seed + 40 inserted
            assert final_trades == 41

        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_ist_datetime_range_queries(self, tmp_path):
        """Verify SQLite range filtering works accurately across IST and UTC queries."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool
        from database import Base

        db_path = tmp_path / "range_test.db"
        test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed datetimes: 09:00, 11:00, 14:00, 16:00 IST
        times = [
            datetime(2026, 11, 9, 9, 0, 0, tzinfo=IST),
            datetime(2026, 11, 9, 11, 0, 0, tzinfo=IST),
            datetime(2026, 11, 9, 14, 0, 0, tzinfo=IST),
            datetime(2026, 11, 9, 16, 0, 0, tzinfo=IST),
        ]

        async with test_session_factory() as session:
            for t in times:
                session.add(DebateSessionModel(timestamp=t, spot_price=25000.0, index_symbol="NIFTY", judge_decision="NO_TRADE"))
            await session.commit()

        # Query between 10:00 IST and 15:00 IST (should return 11:00 and 14:00 -> exactly 2)
        async with test_session_factory() as session:
            q_start = datetime(2026, 11, 9, 10, 0, 0, tzinfo=IST)
            q_end = datetime(2026, 11, 9, 15, 0, 0, tzinfo=IST)
            stmt = select(DebateSessionModel).where(
                DebateSessionModel.timestamp >= q_start,
                DebateSessionModel.timestamp <= q_end,
            )
            rows = (await session.execute(stmt)).scalars().all()
            assert len(rows) == 2
            hours = [r.timestamp.hour for r in rows]
            assert 11 in hours
            assert 14 in hours

        await test_engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# 5. API STATUS ENVELOPE & COUNTDOWN TICKER VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusCountdownAndMultiIndex:
    """Empirically test GET /api/status countdown ticker, dual spots, and lot size."""

    @pytest.mark.asyncio
    async def test_status_endpoint_countdown_and_nifty(self):
        """Verify GET /api/status returns NIFTY metadata and formatted countdown."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/status?symbol=NIFTY")
            assert res.status_code == 200
            data = res.json()

            # R5 Verification
            assert data["status"] == "ok"
            assert data["selected_index"] == "NIFTY"
            assert data["lot_size"] == 25
            assert data["budget_limit"] == 5000.0
            assert data["max_lots"] == 1
            assert "nifty_spot" in data
            assert "banknifty_spot" in data
            assert data["nifty_spot"] > 0
            assert data["banknifty_spot"] > 0

            # Countdown verification
            assert "countdown_seconds" in data
            assert "countdown_timer" in data
            assert isinstance(data["countdown_seconds"], int)
            assert data["countdown_seconds"] >= 0
            # Formatted as mm:ss (e.g. '04:12')
            import re
            assert re.match(r"^\d{2}:\d{2}$", data["countdown_timer"]) is not None

    @pytest.mark.asyncio
    async def test_status_endpoint_banknifty(self):
        """Verify GET /api/status?symbol=BANKNIFTY returns lot size 15."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/status?symbol=BANKNIFTY")
            assert res.status_code == 200
            data = res.json()

            assert data["selected_index"] == "BANKNIFTY"
            assert data["lot_size"] == 15
            assert data["budget_limit"] == 5000.0
            assert data["max_lots"] == 1

