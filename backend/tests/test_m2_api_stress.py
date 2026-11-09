"""
Milestone 2 API & Schema Empirical Stress Test Suite.

Adversarially challenges:
1. Endpoint schema verification: /api/status, /api/debate/latest, /api/debate/history,
   /api/trades, /api/trades/active, /api/trades/summary, /api/trades/{id}/close.
2. Frontend contract compatibility: sandbox_mode mirroring analyzer_mode,
   bare array format for /api/trades, computed fields on all schemas.
3. Trade closing & Realized PnL calculations:
   - PnL math on win/loss/breakeven
   - TARGET_HIT / STOPPED_OUT / CLOSED status transitions
   - Idempotency on repeated close requests
   - Zero-price expiry edge case (body.exit_price == 0.0)
   - Concurrency & input validation
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
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
from main import app
from scheduler import stop_scheduler
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


@pytest.fixture(autouse=True)
def clean_database():
    """Ensure clean database state before and after each test."""
    stop_scheduler()

    async def _reset():
        await create_tables()
        async with async_session_factory() as session:
            await session.execute(delete(PaperTradeModel))
            await session.execute(delete(DebateSessionModel))
            await session.commit()
        await engine.dispose()

    asyncio.run(_reset())
    yield
    stop_scheduler()
    asyncio.run(_reset())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════════════════
# CHALLENGE 1: Endpoint Schema Verification
# ══════════════════════════════════════════════════════════════════════════════

def test_challenge1_status_schema_and_types(client: TestClient):
    """Stress-test /api/status fields and strict typing."""
    with patch("main.get_openalgo_client") as mock_openalgo:
        mock_c = AsyncMock()
        mock_c.check_analyzer_mode.return_value = True
        mock_c.get_market_snapshot.return_value = {"spot_price": 24750.25}
        mock_openalgo.return_value = mock_c

        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()

        # Strict type and field presence checks
        assert isinstance(data["status"], str)
        assert data["status"] == "ok"
        assert isinstance(data["openalgo_connected"], bool) and data["openalgo_connected"] is True
        assert isinstance(data["analyzer_mode"], bool) and data["analyzer_mode"] is True
        assert isinstance(data["sandbox_mode"], bool) and data["sandbox_mode"] is True
        assert isinstance(data["spot_price"], float) and data["spot_price"] == 24750.25
        assert isinstance(data["nifty_spot"], float) and data["nifty_spot"] == 24750.25
        assert isinstance(data["scheduler_running"], bool)
        assert isinstance(data["database_ok"], bool) and data["database_ok"] is True
        assert isinstance(data["market_open"], bool)
        assert isinstance(data["message"], str)


def test_challenge1_status_openalgo_exception_handling(client: TestClient):
    """When OpenAlgo is down or throws, /api/status must remain 200 with connected=False and sandbox=False."""
    with patch("main.get_openalgo_client") as mock_openalgo:
        mock_c = AsyncMock()
        mock_c.check_analyzer_mode.side_effect = ConnectionRefusedError("OpenAlgo offline")
        mock_c.get_market_snapshot.side_effect = ConnectionRefusedError("OpenAlgo offline")
        mock_openalgo.return_value = mock_c

        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()

        assert data["openalgo_connected"] is False
        assert data["analyzer_mode"] is False
        assert data["sandbox_mode"] is False
        assert data["database_ok"] is True
        assert data["spot_price"] == 24500.0  # Safe fallback spot price


def test_challenge1_debate_latest_empty_and_populated(client: TestClient):
    """Verify /api/debate/latest behavior when empty (404) vs populated (200)."""
    # 1. Empty DB -> 404
    res_empty = client.get("/api/debate/latest")
    assert res_empty.status_code == 404
    assert "No debate sessions found" in res_empty.json()["detail"]

    # 2. Populate DB
    async def _populate():
        async with async_session_factory() as session:
            d = DebateSessionModel(
                spot_price=24800.0,
                judge_decision="BUY_CE",
                judge_reasoning="Strong institutional CE buying",
                bull_reasoning=json.dumps(["Arg1", "Arg2", "Arg3"]),
                bear_reasoning=json.dumps(["BArg1", "BArg2", "BArg3"]),
                bull_confidence=9,
                bear_confidence=3,
                bull_strike="24800CE",
                bear_strike="24700PE",
                judge_full_output=json.dumps({
                    "decision": "BUY_CE",
                    "selected_strike": "24800CE",
                    "entry_price_estimate": 140.0,
                    "stop_loss": 110.0,
                    "target": 200.0,
                    "risk_reward_ratio": "1:2.0",
                    "winning_argument_summary": "Strong institutional CE buying",
                    "risk_warning": "Watch for sudden volatility",
                }),
            )
            session.add(d)
            await session.commit()
            await session.refresh(d)

            t = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV24800CE",
                trade_type="BUY_CE",
                entry_price=140.0,
                stop_loss=110.0,
                target=200.0,
                status="OPEN",
            )
            session.add(t)
            await session.commit()

    asyncio.run(_populate())

    res_pop = client.get("/api/debate/latest")
    assert res_pop.status_code == 200
    data = res_pop.json()
    assert data["decision"] == "BUY_CE"
    assert data["spot_price"] == 24800.0
    assert data["bull"]["confidence"] == 9
    assert data["bear"]["confidence"] == 3
    assert data["judge"]["entry_price"] == 140.0
    assert data["trade"]["symbol"] == "NIFTY26NOV24800CE"


def test_challenge1_debate_history_pagination_and_validation(client: TestClient):
    """Stress-test /api/debate/history pagination bounds and parameter validation."""
    async def _seed(count: int = 15):
        async with async_session_factory() as session:
            for i in range(count):
                d = DebateSessionModel(
                    spot_price=24500.0 + i,
                    judge_decision="BUY_CE" if i % 2 == 0 else "NO_TRADE",
                    judge_reasoning=f"Debate reasoning {i}",
                )
                session.add(d)
            await session.commit()

    asyncio.run(_seed(15))

    # Page 1 (limit 10, offset 0)
    res = client.get("/api/debate/history?limit=10&offset=0")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 15
    assert len(data["debates"]) == 10

    # Page 2 (limit 10, offset 10)
    res2 = client.get("/api/debate/history?limit=10&offset=10")
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2["debates"]) == 5

    # Out of range offset
    res3 = client.get("/api/debate/history?limit=10&offset=100")
    assert res3.status_code == 200
    assert len(res3.json()["debates"]) == 0

    # Validation: limit > 100 should fail with 422
    assert client.get("/api/debate/history?limit=101").status_code == 422
    # Validation: limit < 1 should fail with 422
    assert client.get("/api/debate/history?limit=0").status_code == 422
    # Validation: offset < 0 should fail with 422
    assert client.get("/api/debate/history?offset=-1").status_code == 422


def test_challenge1_trades_active_filtering(client: TestClient):
    """Verify /api/trades/active strictly isolates OPEN trades."""
    async def _seed_trades():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=24500.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            t_open = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY_OPEN_CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                stop_loss=80.0,
                target=150.0,
                status="OPEN",
            )
            t_closed = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY_CLOSED_CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                exit_price=130.0,
                stop_loss=80.0,
                target=150.0,
                pnl=2250.0,
                status="CLOSED",
            )
            t_stopped = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY_STOPPED_PE",
                trade_type="BUY_PE",
                entry_price=100.0,
                exit_price=75.0,
                stop_loss=80.0,
                target=150.0,
                pnl=-1875.0,
                status="STOPPED_OUT",
            )
            session.add_all([t_open, t_closed, t_stopped])
            await session.commit()

    asyncio.run(_seed_trades())

    res = client.get("/api/trades/active")
    assert res.status_code == 200
    trades = res.json()
    assert isinstance(trades, list)
    assert len(trades) == 1
    assert trades[0]["option_symbol"] == "NIFTY_OPEN_CE"
    assert trades[0]["status"] == "OPEN"


def test_challenge1_trades_summary_empty_and_populated(client: TestClient):
    """Test /api/trades/summary with 0 trades vs populated trades."""
    # 1. Zero trades
    res_empty = client.get("/api/trades/summary")
    assert res_empty.status_code == 200
    s_empty = res_empty.json()
    assert s_empty["total_trades"] == 0
    assert s_empty["open_trades"] == 0
    assert s_empty["closed_trades"] == 0
    assert s_empty["total_pnl"] == 0.0
    assert s_empty["win_rate"] == 0.0

    # 2. Populated trades with 2 wins, 1 loss, 1 open
    async def _seed_data():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=24500.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            t1 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="WIN1",
                trade_type="BUY_CE",
                entry_price=100.0,
                exit_price=140.0,
                stop_loss=80.0,
                target=150.0,
                pnl=3000.0,
                status="CLOSED",
            )
            t2 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="WIN2",
                trade_type="BUY_CE",
                entry_price=100.0,
                exit_price=120.0,
                stop_loss=80.0,
                target=150.0,
                pnl=1500.0,
                status="TARGET_HIT",
            )
            t3 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="LOSS1",
                trade_type="BUY_PE",
                entry_price=100.0,
                exit_price=80.0,
                stop_loss=80.0,
                target=150.0,
                pnl=-1500.0,
                status="STOPPED_OUT",
            )
            t4 = PaperTradeModel(
                debate_id=d.id,
                option_symbol="OPEN1",
                trade_type="BUY_CE",
                entry_price=100.0,
                stop_loss=80.0,
                target=150.0,
                status="OPEN",
            )
            session.add_all([t1, t2, t3, t4])
            await session.commit()

    asyncio.run(_seed_data())

    res_pop = client.get("/api/trades/summary")
    assert res_pop.status_code == 200
    s = res_pop.json()
    assert s["total_trades"] == 4
    assert s["open_trades"] == 1
    assert s["closed_trades"] == 3
    assert s["total_pnl"] == 3000.0
    assert s["win_count"] == 2
    assert s["loss_count"] == 1
    assert s["win_rate"] == round(2 / 3, 4)
    assert s["best_trade"] == 3000.0
    assert s["worst_trade"] == -1500.0


# ══════════════════════════════════════════════════════════════════════════════
# CHALLENGE 2: Frontend Contract Compatibility
# ══════════════════════════════════════════════════════════════════════════════

def test_challenge2_sandbox_mode_strictly_mirrors_analyzer_mode(client: TestClient):
    """CRITICAL INVARIANT: Verify sandbox_mode NEVER defaults to True when analyzer_mode is False.
    This prevents false-green UI indicators on live brokerage credentials.
    """
    # Test True -> True
    with patch("main.get_openalgo_client") as mock_openalgo:
        mock_c = AsyncMock()
        mock_c.check_analyzer_mode.return_value = True
        mock_openalgo.return_value = mock_c

        res_true = client.get("/api/status")
        assert res_true.json()["analyzer_mode"] is True
        assert res_true.json()["sandbox_mode"] is True

    # Test False -> False
    with patch("main.get_openalgo_client") as mock_openalgo:
        mock_c = AsyncMock()
        mock_c.check_analyzer_mode.return_value = False
        mock_openalgo.return_value = mock_c

        res_false = client.get("/api/status")
        assert res_false.json()["analyzer_mode"] is False
        assert res_false.json()["sandbox_mode"] is False


def test_challenge2_trades_bare_array_contract(client: TestClient):
    """CRITICAL INVARIANT: GET /api/trades MUST return a bare array by default.
    If it returned an Object, React's (trades || []).slice(...) throws TypeError.
    """
    res = client.get("/api/trades")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list), f"Expected bare list, got {type(data)}"

    # Envelope parameter check
    res_env = client.get("/api/trades?envelope=true")
    assert res_env.status_code == 200
    assert isinstance(res_env.json(), dict)
    assert "trades" in res_env.json()


def test_challenge2_computed_fields_contract():
    """Verify computed fields required by React components exist on Pydantic schemas."""
    # 1. StatusResponse
    status_resp = StatusResponse(analyzer_mode=False, spot_price=24600.0)
    dump_status = status_resp.model_dump()
    assert "sandbox_mode" in dump_status
    assert dump_status["sandbox_mode"] is False
    assert "nifty_spot" in dump_status
    assert dump_status["nifty_spot"] == 24600.0

    # 2. BullAgentOutput & BearAgentOutput
    bull = BullAgentOutput(
        bias="BULLISH",
        confidence=8,
        recommended_strike="24600CE",
        arguments=["Arg1", "Arg2", "Arg3"],
    )
    dump_bull = bull.model_dump()
    assert dump_bull["strike"] == "24600CE"
    assert dump_bull["confidence_score"] == 8
    assert dump_bull["key_arguments"] == ["Arg1", "Arg2", "Arg3"]

    # 3. JudgeDecision
    judge = JudgeDecision(
        decision="BUY_CE",
        selected_strike="24600CE",
        entry_price_estimate=125.0,
        stop_loss=100.0,
        target=175.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Clean breakout",
        risk_warning="Volatility risk",
    )
    dump_judge = judge.model_dump()
    assert dump_judge["verdict"] == "BUY_CE"
    assert dump_judge["strike"] == "24600CE"
    assert dump_judge["entry_price"] == 125.0
    assert dump_judge["entry"] == 125.0
    assert dump_judge["sl"] == 100.0
    assert dump_judge["target_price"] == 175.0
    assert dump_judge["risk_reward"] == "2.0"
    assert dump_judge["reasoning"] == "Clean breakout"
    assert dump_judge["winning_argument"] == "Clean breakout"
    assert dump_judge["summary"] == "Clean breakout"

    # 4. PaperTrade
    trade = PaperTrade(
        debate_id=1,
        option_symbol="NIFTY26NOV24600CE",
        trade_type="BUY_CE",
        entry_price=125.0,
        stop_loss=100.0,
        target=175.0,
        status="OPEN",
        pnl=0.0,
    )
    dump_trade = trade.model_dump()
    assert dump_trade["symbol"] == "NIFTY26NOV24600CE"
    assert dump_trade["trading_symbol"] == "NIFTY26NOV24600CE"
    assert "created_at" in dump_trade
    assert "entry_time" in dump_trade
    assert dump_trade["entry"] == 125.0
    assert dump_trade["sl"] == 100.0
    assert dump_trade["target_price"] == 175.0


# ══════════════════════════════════════════════════════════════════════════════
# CHALLENGE 3: Trade Closing and Realized P&L Calculations
# ══════════════════════════════════════════════════════════════════════════════

def test_challenge3_pnl_math_and_status_transitions(client: TestClient):
    """Stress-test trade closing calculations:
    - CE profit: entry 100, exit 150, lot 75 -> pnl = +3750.0 (TARGET_HIT)
    - CE loss: entry 100, exit 70, lot 75 -> pnl = -2250.0 (STOPPED_OUT)
    - Mid exit: entry 100, exit 115, lot 75 -> pnl = +1125.0 (CLOSED)
    """
    trade_id = None

    async def _setup():
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

    # 1. Target Hit
    asyncio.run(_setup())
    res1 = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 145.0})
    assert res1.status_code == 200
    t1 = res1.json()
    assert t1["status"] == "TARGET_HIT"
    assert t1["exit_price"] == 145.0
    assert t1["pnl"] == 3375.0  # (145 - 100) * 75
    assert t1["realized_pnl"] == 3375.0
    assert t1["unrealized_pnl"] is None

    # 2. Stopped Out
    asyncio.run(_setup())
    res2 = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 75.0})
    assert res2.status_code == 200
    t2 = res2.json()
    assert t2["status"] == "STOPPED_OUT"
    assert t2["exit_price"] == 75.0
    assert t2["pnl"] == -1875.0  # (75 - 100) * 75
    assert t2["realized_pnl"] == -1875.0

    # 3. Discretionary close between SL and Target
    asyncio.run(_setup())
    res3 = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 110.0})
    assert res3.status_code == 200
    t3 = res3.json()
    assert t3["status"] == "CLOSED"
    assert t3["exit_price"] == 110.0
    assert t3["pnl"] == 750.0  # (110 - 100) * 75


def test_challenge3_close_trade_idempotency(client: TestClient):
    """Stress-test idempotency: Closing an already-closed trade must NOT recompute P&L or alter state."""
    trade_id = None

    async def _setup():
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

    asyncio.run(_setup())

    # Initial close at 130.0
    res_first = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 130.0})
    assert res_first.status_code == 200
    first_data = res_first.json()
    assert first_data["exit_price"] == 130.0
    assert first_data["pnl"] == 2250.0
    first_closed_at = first_data["closed_at"]

    # Duplicate close with different price (must be ignored)
    res_second = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 200.0})
    assert res_second.status_code == 200
    second_data = res_second.json()
    assert second_data["exit_price"] == 130.0
    assert second_data["pnl"] == 2250.0
    assert second_data["closed_at"] == first_closed_at

    # POST alias close should also be idempotent
    res_third = client.post(f"/api/trades/{trade_id}/close", json={"exit_price": 50.0})
    assert res_third.status_code == 200
    third_data = res_third.json()
    assert third_data["exit_price"] == 130.0
    assert third_data["pnl"] == 2250.0


def test_challenge3_close_trade_zero_exit_price(client: TestClient):
    """EDGE CASE: What happens when an option expires worthless with exit_price=0.0?
    In main.py:576:
    exit_price = body.exit_price if body and body.exit_price and body.exit_price > 0 else None
    If body.exit_price == 0.0, `body.exit_price > 0` is False!
    This edge-case stress test reveals whether zero-price expiration is handled properly.
    """
    trade_id = None

    async def _setup():
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

    asyncio.run(_setup())

    # When exit_price is 0.0 (option expired worthless)
    res = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 0.0})
    assert res.status_code == 200
    data = res.json()
    assert data["exit_price"] == 0.0
    assert data["status"] == "STOPPED_OUT"
    assert data["pnl"] == -7500.0  # (0.0 - 100.0) * 75
    assert data["realized_pnl"] == -7500.0
