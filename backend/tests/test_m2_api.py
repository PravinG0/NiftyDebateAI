"""
Milestone 2 Automated Test Suite:
- FastAPI Backend REST Endpoints (/api/status, /api/debate/*, /api/trades/*, /api/scheduler/*)
- Market Hours Scheduler Controls (start, pause, resume, stop, market hours gating, bypass)
- Frontend Schema Aliasing & Compatibility (sandbox_mode, bull, bear, judge, strike, reasoning, symbol, etc.)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
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
from main import app
from openalgo_client import SafetyError
from scheduler import (
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
    PaperTrade,
    SchedulerStatusResponse,
    StatusResponse,
    TradeSummaryResponse,
)

_IST = ZoneInfo("Asia/Kolkata")


# ── Test Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_db_and_scheduler():
    """Ensure database tables exist, clear test data, and reset scheduler."""
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


@pytest.fixture
def test_client() -> TestClient:
    """Synchronous test client for FastAPI."""
    return TestClient(app)


def make_mock_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        spot_price=24520.50,
        ema_20=24480.0,
        ema_50=24450.0,
        vwap=24510.0,
        rsi=56.0,
        volume=1250000.0,
        atr=115.0,
        vix=13.2,
        pcr=1.15,
        ce_oi_change=-12000.0,
        pe_oi_change=18000.0,
        higher_tf_trend="BULLISH",
    )


def make_mock_debate_session(
    decision: str = "BUY_CE",
    session_id: int = 101,
) -> DebateSession:
    snapshot = make_mock_snapshot()
    bull = BullAgentOutput(
        bias="BULLISH",
        confidence=8,
        recommended_strike="24550CE",
        arguments=[
            "Spot broke above intraday resistance cleanly.",
            "PCR at 1.15 indicates robust put writing support.",
            "Volume confirmation on breakout candles.",
        ],
    )
    bear = BearAgentOutput(
        bias="BEARISH",
        confidence=4,
        recommended_strike="24450PE",
        arguments=[
            "Spot near daily pivot upper band.",
            "RSI entering overbought territory on lower timeframes.",
            "Call open interest still noticeable at 24600.",
        ],
    )
    judge = JudgeDecision(
        decision=decision,  # type: ignore[arg-type]
        selected_strike="24550CE" if decision == "BUY_CE" else ("24450PE" if decision == "BUY_PE" else None),
        entry_price_estimate=125.0 if decision != "NO_TRADE" else 0.0,
        stop_loss=100.0 if decision != "NO_TRADE" else 0.0,
        target=175.0 if decision != "NO_TRADE" else 0.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Bull thesis backed by firm PCR and volume expansion.",
        risk_warning="Watch 24600 resistance closely.",
    )
    return DebateSession(
        id=session_id,
        timestamp=datetime.now(timezone.utc),
        market_snapshot=snapshot,
        bull_output=bull,
        bear_output=bear,
        judge_decision=judge,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. System Status Endpoint Tests (/api/status)
# ══════════════════════════════════════════════════════════════════════════════

def test_status_endpoint_returns_ok_and_sandbox(test_client: TestClient):
    """Verify /api/status returns HTTP 200 with status='ok', analyzer_mode, and sandbox_mode."""
    with patch("main.get_openalgo_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.check_analyzer_mode.return_value = True
        mock_client.get_market_snapshot.return_value = {"spot_price": 24550.0}
        mock_get_client.return_value = mock_client

        res = test_client.get("/api/status")
        assert res.status_code == 200
        data = res.json()

        assert data["status"] == "ok"
        assert data["openalgo_connected"] is True
        assert data["analyzer_mode"] is True
        # Critical Safety Aliasing check: sandbox_mode MUST be True when analyzer_mode is True
        assert data["sandbox_mode"] is True
        assert data["spot_price"] == 24550.0
        assert data["nifty_spot"] == 24550.0
        assert data["database_ok"] is True
        assert "scheduler_running" in data
        assert "market_open" in data


def test_status_endpoint_live_mode_prevents_false_green(test_client: TestClient):
    """Verify that when OpenAlgo is NOT in analyzer mode, sandbox_mode is strictly False."""
    with patch("main.get_openalgo_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.check_analyzer_mode.return_value = False
        mock_client.get_market_snapshot.return_value = {"spot_price": 24500.0}
        mock_get_client.return_value = mock_client

        res = test_client.get("/api/status")
        assert res.status_code == 200
        data = res.json()

        assert data["analyzer_mode"] is False
        assert data["sandbox_mode"] is False  # Must not fall back to True!


# ══════════════════════════════════════════════════════════════════════════════
# 2. AI Debate Endpoints (/api/debate/*)
# ══════════════════════════════════════════════════════════════════════════════

def test_debate_run_endpoint_success(test_client: TestClient):
    """Test POST /api/debate/run returns full debate session with frontend aliases."""
    mock_session = make_mock_debate_session("BUY_CE", session_id=42)

    with patch("main.trigger_debate_now", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = mock_session

        res = test_client.post("/api/debate/run")
        assert res.status_code == 200
        data = res.json()

        # Top-level frontend contract checks (DebateArena.jsx)
        assert data["id"] == 42
        assert "bull" in data
        assert "bear" in data
        assert "judge" in data
        assert data["spot_price"] == 24520.50
        assert data["nifty_spot"] == 24520.50

        # Bull agent card checks (BullCard.jsx)
        bull = data["bull"]
        assert bull["confidence"] == 8
        assert bull["confidence_score"] == 8
        assert bull["strike"] == "24550CE"
        assert bull["recommended_strike"] == "24550CE"
        assert len(bull["arguments"]) == 3

        # Bear agent card checks (BearCard.jsx)
        bear = data["bear"]
        assert bear["confidence"] == 4
        assert bear["strike"] == "24450PE"
        assert len(bear["arguments"]) == 3

        # Judge verdict card checks (JudgeCard.jsx)
        judge = data["judge"]
        assert judge["verdict"] == "BUY_CE"
        assert judge["strike"] == "24550CE"
        assert judge["entry_price"] == 125.0
        assert judge["stop_loss"] == 100.0
        assert judge["target"] == 175.0
        assert judge["risk_reward"] == "2.0"  # Stripped of "1:" prefix
        assert "firm PCR" in judge["reasoning"]

        # Nested debate envelope
        assert "debate" in data
        assert data["debate"]["id"] == 42
        assert data["debate"]["bull"]["strike"] == "24550CE"


def test_debate_run_endpoint_safety_error(test_client: TestClient):
    """Verify POST /api/debate/run returns HTTP 400 when SafetyError is raised."""
    with patch("main.trigger_debate_now", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.side_effect = SafetyError("OpenAlgo is not in analyzer mode")

        res = test_client.post("/api/debate/run")
        assert res.status_code == 400
        assert "Safety Error" in res.json()["detail"]


def test_debate_latest_404_when_empty(test_client: TestClient):
    """Verify GET /api/debate/latest returns HTTP 404 when no debates exist."""
    res = test_client.get("/api/debate/latest")
    assert res.status_code == 404
    assert "No debate sessions found" in res.json()["detail"]


def test_debate_latest_success_after_db_insert(test_client: TestClient):
    """Verify GET /api/debate/latest returns the most recent debate session from DB."""
    async def _insert_debates():
        async with async_session_factory() as session:
            d1 = DebateSessionModel(
                spot_price=24400.0,
                judge_decision="BUY_PE",
                judge_reasoning="Bearish breakdown",
                bull_reasoning=json.dumps(["b1", "b2", "b3"]),
                bear_reasoning=json.dumps(["br1", "br2", "br3"]),
            )
            d2 = DebateSessionModel(
                spot_price=24550.0,
                judge_decision="BUY_CE",
                judge_reasoning="Bullish breakout",
                bull_reasoning=json.dumps(["b4", "b5", "b6"]),
                bear_reasoning=json.dumps(["br4", "br5", "br6"]),
                bull_strike="24550CE",
                bear_strike="24450PE",
            )
            session.add_all([d1, d2])
            await session.commit()
            await session.refresh(d2)

            trade = PaperTradeModel(
                debate_id=d2.id,
                option_symbol="NIFTY26NOV24550CE",
                trade_type="BUY_CE",
                entry_price=130.0,
                stop_loss=105.0,
                target=180.0,
                status="OPEN",
            )
            session.add(trade)
            await session.commit()

    asyncio.run(_insert_debates())

    res = test_client.get("/api/debate/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "BUY_CE"
    assert data["spot_price"] == 24550.0
    assert data["nifty_spot"] == 24550.0
    assert data["bull"]["strike"] == "24550CE"
    assert data["trade"] is not None
    assert data["trade"]["symbol"] == "NIFTY26NOV24550CE"


def test_debate_history_paginated(test_client: TestClient):
    """Verify GET /api/debate/history supports limit and offset pagination."""
    async def _insert_batch():
        async with async_session_factory() as session:
            for i in range(5):
                d = DebateSessionModel(
                    spot_price=24500.0 + i * 10,
                    judge_decision="BUY_CE" if i % 2 == 0 else "NO_TRADE",
                    judge_reasoning=f"Reason {i}",
                )
                session.add(d)
            await session.commit()

    asyncio.run(_insert_batch())

    res = test_client.get("/api/debate/history?limit=3&offset=0")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 5
    assert data["limit"] == 3
    assert data["offset"] == 0
    assert len(data["debates"]) == 3

    # Next page
    res_page2 = test_client.get("/api/debate/history?limit=3&offset=3")
    assert res_page2.status_code == 200
    data2 = res_page2.json()
    assert len(data2["debates"]) == 2


def test_debate_get_by_id(test_client: TestClient):
    """Verify GET /api/debate/{id} returns specific debate or 404."""
    debate_id = None

    async def _insert_one():
        nonlocal debate_id
        async with async_session_factory() as session:
            d = DebateSessionModel(
                spot_price=24600.0,
                judge_decision="BUY_CE",
                judge_reasoning="Target test debate",
            )
            session.add(d)
            await session.commit()
            await session.refresh(d)
            debate_id = d.id

    asyncio.run(_insert_one())

    res = test_client.get(f"/api/debate/{debate_id}")
    assert res.status_code == 200
    assert res.json()["id"] == debate_id
    assert res.json()["spot_price"] == 24600.0

    # Non-existent
    res_404 = test_client.get("/api/debate/999999")
    assert res_404.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 3. Paper Trade Endpoints (/api/trades/*)
# ══════════════════════════════════════════════════════════════════════════════

def test_trades_endpoint_returns_bare_array(test_client: TestClient):
    """CRITICAL: GET /api/trades MUST return a JSON list directly so frontend
    (trades || []).slice(...) in TradeHistory.jsx does not throw TypeError.
    """
    async def _seed_trade():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=24500.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            t = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV24500CE",
                trade_type="BUY_CE",
                entry_price=120.0,
                stop_loss=100.0,
                target=160.0,
                status="OPEN",
            )
            session.add(t)
            await session.commit()

    asyncio.run(_seed_trade())

    res = test_client.get("/api/trades")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 1

    trade = data[0]
    # TradeHistory.jsx property accesses
    assert trade["symbol"] == "NIFTY26NOV24500CE"
    assert trade["trading_symbol"] == "NIFTY26NOV24500CE"
    assert trade["option_symbol"] == "NIFTY26NOV24500CE"
    assert trade["entry_price"] == 120.0
    assert trade["entry"] == 120.0
    assert trade["status"] == "OPEN"
    assert "created_at" in trade
    assert "timestamp" in trade


def test_trades_envelope_parameter(test_client: TestClient):
    """Verify GET /api/trades?envelope=true returns wrapped dictionary."""
    res = test_client.get("/api/trades?envelope=true")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)
    assert data["success"] is True
    assert "trades" in data
    assert "count" in data


def test_active_trades_endpoint(test_client: TestClient):
    """Verify GET /api/trades/active filters out CLOSED trades."""
    async def _seed():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=24500.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            t1 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV24500CE",
                trade_type="BUY_CE",
                entry_price=120.0,
                stop_loss=100.0,
                target=160.0,
                status="OPEN",
            )
            t2 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV24400PE",
                trade_type="BUY_PE",
                entry_price=90.0,
                exit_price=110.0,
                stop_loss=75.0,
                target=125.0,
                pnl=1500.0,
                status="CLOSED",
            )
            session.add_all([t1, t2])
            await session.commit()

    asyncio.run(_seed())

    res = test_client.get("/api/trades/active")
    assert res.status_code == 200
    active = res.json()
    assert isinstance(active, list)
    assert len(active) == 1
    assert active[0]["option_symbol"] == "NIFTY26NOV24500CE"
    assert active[0]["status"] == "OPEN"


def test_trades_summary_statistics(test_client: TestClient):
    """Verify GET /api/trades/summary calculates win rate, total P&L, wins, and losses."""
    async def _seed():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=24500.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            # Win (+1500)
            t1 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV24500CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                exit_price=120.0,
                stop_loss=80.0,
                target=140.0,
                pnl=1500.0,
                status="CLOSED",
            )
            # Loss (-600)
            t2 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV24400PE",
                trade_type="BUY_PE",
                entry_price=100.0,
                exit_price=92.0,
                stop_loss=80.0,
                target=140.0,
                pnl=-600.0,
                status="CLOSED",
            )
            # Open
            t3 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV24550CE",
                trade_type="BUY_CE",
                entry_price=110.0,
                stop_loss=90.0,
                target=150.0,
                status="OPEN",
            )
            session.add_all([t1, t2, t3])
            await session.commit()

    asyncio.run(_seed())

    res = test_client.get("/api/trades/summary")
    assert res.status_code == 200
    summary = res.json()

    assert summary["total_trades"] == 3
    assert summary["open_trades"] == 1
    assert summary["closed_trades"] == 2
    assert summary["total_pnl"] == 900.0
    assert summary["net_pnl"] == 900.0  # Frontend alias
    assert summary["win_count"] == 1
    assert summary["wins"] == 1  # Frontend alias
    assert summary["loss_count"] == 1
    assert summary["losses"] == 1  # Frontend alias
    assert summary["win_rate"] == 0.5
    assert summary["best_trade"] == 1500.0
    assert summary["worst_trade"] == -600.0


def test_close_trade_outcomes(test_client: TestClient):
    """Test closing a trade: TARGET_HIT, STOPPED_OUT, and manual exit price."""
    trade_id = None

    async def _setup_trade():
        nonlocal trade_id
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=24500.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            t = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV24500CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                stop_loss=80.0,
                target=140.0,
                quantity=75,
                status="OPEN",
            )
            session.add(t)
            await session.commit()
            await session.refresh(t)
            trade_id = t.id

    # 1. Target hit exit
    asyncio.run(_setup_trade())
    res_tgt = test_client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 145.0})
    assert res_tgt.status_code == 200
    t_tgt = res_tgt.json()
    assert t_tgt["status"] == "TARGET_HIT"
    assert t_tgt["pnl"] == (145.0 - 100.0) * 75
    assert t_tgt["realized_pnl"] == (145.0 - 100.0) * 75

    # 2. Stop loss hit exit
    asyncio.run(_setup_trade())
    res_sl = test_client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 75.0})
    assert res_sl.status_code == 200
    t_sl = res_sl.json()
    assert t_sl["status"] == "STOPPED_OUT"
    assert t_sl["pnl"] == (75.0 - 100.0) * 75

    # 3. Nonexistent trade
    res_404 = test_client.patch("/api/trades/999999/close", json={"exit_price": 120.0})
    assert res_404.status_code == 404

    # 4. Zero exit price (worthless option expiry)
    asyncio.run(_setup_trade())
    res_zero = test_client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 0.0})
    assert res_zero.status_code == 200
    t_zero = res_zero.json()
    assert t_zero["exit_price"] == 0.0
    assert t_zero["status"] == "STOPPED_OUT"
    assert t_zero["pnl"] == -7500.0  # (0.0 - 100.0) * 75
    assert t_zero["realized_pnl"] == -7500.0
    assert t_zero["unrealized_pnl"] is None

    # Duplicate close with different price (idempotency check)
    res_zero_again = test_client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 50.0})
    assert res_zero_again.status_code == 200
    assert res_zero_again.json()["exit_price"] == 0.0
    assert res_zero_again.json()["pnl"] == -7500.0


# ══════════════════════════════════════════════════════════════════════════════
# 4. Scheduler Endpoints (/api/scheduler/*)
# ══════════════════════════════════════════════════════════════════════════════

def test_scheduler_endpoints_lifecycle(test_client: TestClient):
    """Test start, pause, resume, and stop controls via REST API."""
    # 1. Status initially STOPPED
    res = test_client.get("/api/scheduler/status")
    assert res.status_code == 200
    assert res.json()["running"] is False
    assert res.json()["state"] == "STOPPED"

    # 2. Start
    res_start = test_client.post("/api/scheduler/start")
    assert res_start.status_code == 200
    assert res_start.json()["success"] is True
    assert res_start.json()["status"]["running"] is True
    assert res_start.json()["status"]["state"] == "RUNNING"

    # 3. Pause
    res_pause = test_client.post("/api/scheduler/pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["success"] is True
    assert res_pause.json()["status"]["running"] is False
    assert res_pause.json()["status"]["paused"] is True
    assert res_pause.json()["status"]["state"] == "PAUSED"

    # 4. Resume
    res_resume = test_client.post("/api/scheduler/resume")
    assert res_resume.status_code == 200
    assert res_resume.json()["success"] is True
    assert res_resume.json()["status"]["running"] is True
    assert res_resume.json()["status"]["paused"] is False
    assert res_resume.json()["status"]["state"] == "RUNNING"

    # 5. Stop
    res_stop = test_client.post("/api/scheduler/stop")
    assert res_stop.status_code == 200
    assert res_stop.json()["success"] is True
    assert res_stop.json()["status"]["running"] is False
    assert res_stop.json()["status"]["state"] == "STOPPED"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Scheduler Python API Unit Tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_scheduler_pause_resume_non_destructive():
    """Verify APScheduler pause and resume preserve jobs in memory without re-registration."""
    stop_scheduler()
    assert scheduler_status()["state"] == "STOPPED"

    start_scheduler()
    stat_running = scheduler_status()
    assert stat_running["running"] is True
    assert stat_running["paused"] is False
    assert stat_running["state"] == "RUNNING"
    assert stat_running["next_run"] is not None

    # Pause
    pause_scheduler()
    stat_paused = scheduler_status()
    assert stat_paused["running"] is False
    assert stat_paused["is_running"] is False
    assert stat_paused["paused"] is True
    assert stat_paused["is_paused"] is True
    assert stat_paused["state"] == "PAUSED"

    # Calling start_scheduler while paused safely resumes instead of throwing SchedulerAlreadyRunningError
    start_scheduler()
    stat_restarted = scheduler_status()
    assert stat_restarted["running"] is True
    assert stat_restarted["paused"] is False
    assert stat_restarted["state"] == "RUNNING"

    # Stop
    stop_scheduler()
    stat_stopped = scheduler_status()
    assert stat_stopped["state"] == "STOPPED"
    assert stat_stopped["running"] is False


def test_is_market_open_boundary_conditions():
    """Verify market hours logic (09:20 - 15:15 IST, Mon-Fri)."""
    # Monday 09:19 IST -> False
    assert not is_market_open(datetime(2026, 11, 9, 9, 19, tzinfo=_IST))
    # Monday 09:20 IST -> True (market open boundary)
    assert is_market_open(datetime(2026, 11, 9, 9, 20, tzinfo=_IST))
    # Monday 12:00 IST -> True (midday trading)
    assert is_market_open(datetime(2026, 11, 9, 12, 0, tzinfo=_IST))
    # Monday 15:15 IST -> True (market close boundary)
    assert is_market_open(datetime(2026, 11, 9, 15, 15, tzinfo=_IST))
    # Monday 15:16 IST -> False (after close)
    assert not is_market_open(datetime(2026, 11, 9, 15, 16, tzinfo=_IST))
    # Sunday 12:00 IST -> False (weekend)
    assert not is_market_open(datetime(2026, 11, 8, 12, 0, tzinfo=_IST))
    # Saturday 10:00 IST -> False (weekend)
    assert not is_market_open(datetime(2026, 11, 7, 10, 0, tzinfo=_IST))


@pytest.mark.asyncio
async def test_trigger_debate_now_bypass_market_hours():
    """Verify trigger_debate_now skips when market closed unless bypass_market_hours=True."""
    mock_engine = MagicMock()
    mock_engine.run_debate = AsyncMock(return_value=make_mock_debate_session())

    # When market is closed (Sunday)
    with patch("scheduler.is_market_open", return_value=False):
        # 1. Scheduled run (bypass_market_hours=False) skips
        res_skipped = await trigger_debate_now(bypass_market_hours=False, engine=mock_engine)
        assert res_skipped is None
        assert mock_engine.run_debate.call_count == 0

        # 2. Manual on-demand run (bypass_market_hours=True) proceeds
        res_forced = await trigger_debate_now(bypass_market_hours=True, engine=mock_engine)
        assert res_forced is not None
        assert mock_engine.run_debate.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 6. Frontend Schema Compatibility & Aliasing Unit Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_status_response_schema_computed_fields():
    """Verify StatusResponse computed fields provide sandbox_mode and nifty_spot."""
    s = StatusResponse(
        status="ok",
        analyzer_mode=True,
        spot_price=24580.0,
        scheduler_running=True,
    )
    d = s.model_dump()
    assert d["analyzer_mode"] is True
    assert d["sandbox_mode"] is True
    assert d["spot_price"] == 24580.0
    assert d["nifty_spot"] == 24580.0


def test_judge_decision_schema_computed_fields():
    """Verify JudgeDecision computed fields supply strike, entry_price, and reasoning."""
    j = JudgeDecision(
        decision="BUY_CE",
        selected_strike="24600CE",
        entry_price_estimate=145.5,
        stop_loss=115.0,
        target=205.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Robust momentum with positive PCR divergence.",
        risk_warning="Watch 24650 resistance level.",
    )
    d = j.model_dump()

    assert d["verdict"] == "BUY_CE"
    assert d["strike"] == "24600CE"
    assert d["recommended_strike"] == "24600CE"
    assert d["entry_price"] == 145.5
    assert d["entry"] == 145.5
    assert d["sl"] == 115.0
    assert d["target_price"] == 205.0
    assert d["risk_reward"] == "2.0"  # Stripped of '1:' prefix
    assert d["reasoning"] == "Robust momentum with positive PCR divergence."
    assert d["winning_argument"] == "Robust momentum with positive PCR divergence."
    assert d["summary"] == "Robust momentum with positive PCR divergence."


def test_debate_session_schema_computed_fields():
    """Verify DebateSession computed fields supply bull, bear, judge, and verdict."""
    session = make_mock_debate_session("BUY_CE", session_id=12)
    d = session.model_dump()

    assert d["bull"]["strike"] == "24550CE"
    assert d["bull"]["confidence_score"] == 8
    assert d["bear"]["strike"] == "24450PE"
    assert d["judge"]["strike"] == "24550CE"
    assert d["verdict"] == "BUY_CE"
    assert d["spot_price"] == 24520.50
    assert d["nifty_spot"] == 24520.50
    assert "created_at" in d


def test_paper_trade_schema_computed_fields():
    """Verify PaperTrade computed fields supply symbol, trading_symbol, created_at, entry, and sl."""
    t = PaperTrade(
        id=7,
        debate_id=12,
        option_symbol="NIFTY26NOV24550CE",
        trade_type="BUY_CE",
        entry_price=135.0,
        stop_loss=110.0,
        target=185.0,
        status="OPEN",
    )
    d = t.model_dump()

    assert d["symbol"] == "NIFTY26NOV24550CE"
    assert d["trading_symbol"] == "NIFTY26NOV24550CE"
    assert d["entry"] == 135.0
    assert d["sl"] == 110.0
    assert d["target_price"] == 185.0
    assert d["status"] == "OPEN"
    assert "created_at" in d
    assert "timestamp" in d
    assert "entry_time" in d


def test_trade_summary_schema_computed_fields():
    """Verify TradeSummaryResponse computed fields supply wins, losses, and net_pnl."""
    s = TradeSummaryResponse(
        total_trades=10,
        open_trades=2,
        closed_trades=8,
        total_pnl=3400.0,
        win_count=6,
        loss_count=2,
        win_rate=0.75,
    )
    d = s.model_dump()

    assert d["wins"] == 6
    assert d["losses"] == 2
    assert d["net_pnl"] == 3400.0
    assert d["win_rate"] == 0.75
