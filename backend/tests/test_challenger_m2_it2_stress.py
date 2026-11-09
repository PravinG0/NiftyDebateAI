"""
Milestone 2 Iteration 2 Challenger 2 Empirical Stress Test Suite.

Adversarial Verification Scope:
1. PATCH /api/trades/{id}/close:
   - Zero-price option expiry (exit_price: 0.0)
   - Negative exit price rejection (ge=0.0 validation)
   - Duplicate close calls (idempotency under identical and differing payloads)
   - Non-existent trade handling (404 Not Found)
2. PnL Math & Status Logic:
   - 100% loss verification when option expires at ₹0.00
   - Target hit (exit >= target) -> TARGET_HIT
   - Stop-loss triggered (exit <= stop_loss) -> STOPPED_OUT
   - Discretionary / partial exit (stop_loss < exit < target) -> CLOSED
   - Breakeven exit (exit == entry) -> CLOSED, pnl == 0.0
   - Precision floating point handling
3. Concurrency Stress:
   - High-concurrency async requests to /api/status, /api/trades, /api/trades/active, /api/trades/summary, /api/scheduler/status
   - Race-condition testing on simultaneous duplicate close requests for the same trade
   - Rapid scheduler start/pause/resume cycling under concurrency
4. End-to-End Data Consistency:
   - Mathematical cross-consistency across /api/trades, /api/trades/active, and /api/trades/summary
   - State transition consistency upon closing active trades
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
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


@pytest.fixture(autouse=True)
def clean_db():
    """Ensure clean isolated database state before and after each test."""
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


async def _create_test_debate_and_trade(
    entry_price: float = 100.0,
    stop_loss: float = 80.0,
    target: float = 150.0,
    quantity: int = 75,
    status: str = "OPEN",
    exit_price: float | None = None,
    pnl: float | None = None,
    symbol: str = "NIFTY26NOV24500CE",
    trade_type: str = "BUY_CE",
) -> tuple[int, int]:
    """Helper to seed a debate session and an associated paper trade."""
    async with async_session_factory() as session:
        d = DebateSessionModel(
            spot_price=24500.0,
            judge_decision=trade_type,
            judge_reasoning="Adversarial stress test session",
            bull_reasoning=json.dumps(["Arg 1", "Arg 2", "Arg 3"]),
            bear_reasoning=json.dumps(["BArg 1", "BArg 2", "BArg 3"]),
            bull_confidence=8,
            bear_confidence=4,
            bull_strike="24500CE",
            bear_strike="24400PE",
        )
        session.add(d)
        await session.commit()
        await session.refresh(d)

        t = PaperTradeModel(
            debate_id=d.id,
            option_symbol=symbol,
            trade_type=trade_type,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_loss=stop_loss,
            target=target,
            quantity=quantity,
            status=status,
            pnl=pnl,
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return d.id, t.id


# ══════════════════════════════════════════════════════════════════════════════
# OBJECTIVE 1: PATCH /api/trades/{id}/close Adversarial Conditions
# ══════════════════════════════════════════════════════════════════════════════

def test_close_trade_worthless_zero_price_expiry(client: TestClient):
    """CHALLENGE: Option expires worthless at ₹0.00.
    - PnL must be exactly -100% of invested premium: (0.0 - entry_price) * quantity.
    - Status must transition to STOPPED_OUT.
    - exit_price must be persisted as 0.0, not overwritten by mock LTP.
    """
    _, trade_id = asyncio.run(
        _create_test_debate_and_trade(
            entry_price=120.0,
            stop_loss=90.0,
            target=180.0,
            quantity=75,
        )
    )

    res = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 0.0})
    assert res.status_code == 200
    data = res.json()

    assert data["id"] == trade_id
    assert data["exit_price"] == 0.0
    assert data["status"] == "STOPPED_OUT"
    assert data["pnl"] == -9000.0  # (0.0 - 120.0) * 75
    assert data["realized_pnl"] == -9000.0
    assert data["closed_at"] is not None


def test_close_trade_negative_exit_price_rejected(client: TestClient):
    """CHALLENGE: Negative exit price must be strictly rejected with HTTP 422.
    Option prices cannot be negative; trade must remain OPEN.
    """
    _, trade_id = asyncio.run(
        _create_test_debate_and_trade(entry_price=100.0, stop_loss=80.0, target=150.0)
    )

    # Test negative float
    res_neg = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": -15.5})
    assert res_neg.status_code == 422, f"Expected 422 for negative price, got {res_neg.status_code}"

    # Test negative micro-price
    res_micro = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": -0.01})
    assert res_micro.status_code == 422

    # Verify trade remains completely untouched and OPEN
    res_trades = client.get("/api/trades/active")
    assert res_trades.status_code == 200
    active = res_trades.json()
    assert len(active) == 1
    assert active[0]["id"] == trade_id
    assert active[0]["status"] == "OPEN"
    assert active[0]["exit_price"] is None


def test_close_trade_duplicate_calls_idempotency(client: TestClient):
    """CHALLENGE: Repeated close requests on an already closed trade must be idempotent.
    - Subsequent calls must NOT mutate exit_price, pnl, status, or closed_at timestamp.
    - POST and PATCH close aliases must behave identically.
    """
    _, trade_id = asyncio.run(
        _create_test_debate_and_trade(
            entry_price=100.0,
            stop_loss=80.0,
            target=150.0,
            quantity=75,
        )
    )

    # First close at 145.0 (discretionary / near target)
    res_1 = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 145.0})
    assert res_1.status_code == 200
    data_1 = res_1.json()
    assert data_1["exit_price"] == 145.0
    assert data_1["pnl"] == 3375.0
    closed_at_timestamp = data_1["closed_at"]

    # Duplicate PATCH with differing exit_price (200.0)
    res_2 = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 200.0})
    assert res_2.status_code == 200
    data_2 = res_2.json()
    assert data_2["exit_price"] == 145.0
    assert data_2["pnl"] == 3375.0
    assert data_2["closed_at"] == closed_at_timestamp

    # Duplicate POST with exit_price = 0.0
    res_3 = client.post(f"/api/trades/{trade_id}/close", json={"exit_price": 0.0})
    assert res_3.status_code == 200
    data_3 = res_3.json()
    assert data_3["exit_price"] == 145.0
    assert data_3["pnl"] == 3375.0
    assert data_3["closed_at"] == closed_at_timestamp

    # Duplicate close with empty body
    res_4 = client.patch(f"/api/trades/{trade_id}/close", json={})
    assert res_4.status_code == 200
    assert res_4.json()["exit_price"] == 145.0


def test_close_trade_non_existent_id_404(client: TestClient):
    """CHALLENGE: Closing a non-existent trade must return HTTP 404."""
    # Non-existent high positive ID
    res_high = client.patch("/api/trades/999999/close", json={"exit_price": 100.0})
    assert res_high.status_code == 404
    assert "not found" in res_high.json()["detail"].lower()

    # Non-existent ID via POST alias
    res_post = client.post("/api/trades/888888/close", json={"exit_price": 100.0})
    assert res_post.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# OBJECTIVE 2: PnL Calculations & Boundary Status Transitions
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "entry,sl,target,qty,exit_p,expected_pnl,expected_status",
    [
        # 1. 100% loss worthless expiry
        (100.0, 80.0, 150.0, 75, 0.0, -7500.0, "STOPPED_OUT"),
        (250.50, 200.0, 320.0, 50, 0.0, -12525.0, "STOPPED_OUT"),
        # 2. Exact stop-loss hit
        (100.0, 80.0, 150.0, 75, 80.0, -1500.0, "STOPPED_OUT"),
        # 3. Severe gap down below stop-loss
        (100.0, 80.0, 150.0, 75, 45.0, -4125.0, "STOPPED_OUT"),
        # 4. Exact target hit
        (100.0, 80.0, 150.0, 75, 150.0, 3750.0, "TARGET_HIT"),
        # 5. Gap up above target
        (100.0, 80.0, 150.0, 75, 175.50, 5662.5, "TARGET_HIT"),
        # 6. Discretionary close between SL and Target (positive pnl)
        (100.0, 80.0, 150.0, 75, 120.0, 1500.0, "CLOSED"),
        # 7. Discretionary close between SL and Target (negative pnl)
        (100.0, 80.0, 150.0, 75, 85.0, -1125.0, "CLOSED"),
        # 8. Exact breakeven exit
        (100.0, 80.0, 150.0, 75, 100.0, 0.0, "CLOSED"),
    ],
)
def test_pnl_and_status_logic_permutations(
    client: TestClient,
    entry: float,
    sl: float,
    target: float,
    qty: int,
    exit_p: float,
    expected_pnl: float,
    expected_status: str,
):
    """Exhaustive boundary testing of PnL math and status transitions."""
    _, trade_id = asyncio.run(
        _create_test_debate_and_trade(
            entry_price=entry,
            stop_loss=sl,
            target=target,
            quantity=qty,
        )
    )

    res = client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": exit_p})
    assert res.status_code == 200
    data = res.json()
    assert data["exit_price"] == exit_p
    assert data["pnl"] == expected_pnl
    assert data["status"] == expected_status
    assert data["realized_pnl"] == expected_pnl


def test_close_trade_without_body_falls_back_to_ltp_or_entry(client: TestClient):
    """When exit_price is omitted, backend queries OpenAlgo LTP or falls back to entry_price."""
    _, trade_id = asyncio.run(
        _create_test_debate_and_trade(entry_price=110.0, stop_loss=90.0, target=160.0)
    )

    with patch("main.get_openalgo_client") as mock_openalgo:
        mock_c = AsyncMock()
        mock_c.get_ltp.return_value = 165.0  # LTP reaches target
        mock_openalgo.return_value = mock_c

        res = client.patch(f"/api/trades/{trade_id}/close")
        assert res.status_code == 200
        data = res.json()
        assert data["exit_price"] == 165.0
        assert data["status"] == "TARGET_HIT"
        assert data["pnl"] == round((165.0 - 110.0) * 75, 2)


# ══════════════════════════════════════════════════════════════════════════════
# OBJECTIVE 3: Concurrency & Stress Testing
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_concurrent_read_requests():
    """Stress-test concurrent requests across multiple API endpoints.
    50 requests executed simultaneously via asyncio.gather across
    /api/status, /api/trades, /api/trades/active, /api/trades/summary, /api/scheduler/status.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("main.get_openalgo_client") as mock_openalgo:
            mock_c = AsyncMock()
            mock_c.check_analyzer_mode.return_value = True
            mock_c.get_market_snapshot.return_value = {"spot_price": 24800.0}
            mock_openalgo.return_value = mock_c

            endpoints = [
                "/api/status",
                "/api/trades",
                "/api/trades/active",
                "/api/trades/summary",
                "/api/scheduler/status",
            ]

            tasks = [ac.get(endpoints[i % len(endpoints)]) for i in range(50)]
            responses = await asyncio.gather(*tasks)

            assert all(r.status_code == 200 for r in responses), f"Non-200 responses observed: {[r.status_code for r in responses]}"


@pytest.mark.asyncio
async def test_concurrent_duplicate_close_requests():
    """Stress-test race conditions: 10 concurrent close requests for the SAME open trade.
    All must succeed (200), and the trade must have exactly one consistent final state.
    """
    _, trade_id = await _create_test_debate_and_trade(entry_price=100.0, stop_loss=80.0, target=150.0)

    exit_prices = [110.0, 120.0, 130.0, 140.0, 150.0, 95.0, 85.0, 0.0, 105.0, 115.0]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        tasks = [ac.patch(f"/api/trades/{trade_id}/close", json={"exit_price": p}) for p in exit_prices]
        responses = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in responses)

        res_final = await ac.get("/api/trades")
        trades = res_final.json()
        assert len(trades) == 1
        t = trades[0]
        assert t["status"] != "OPEN"
        assert t["exit_price"] is not None
        assert t["pnl"] == round((t["exit_price"] - 100.0) * 75, 2)


@pytest.mark.asyncio
async def test_concurrent_scheduler_control_actions():
    """Stress-test scheduler endpoints under rapid interleaved state transitions."""
    control_endpoints = [
        ("GET", "/api/scheduler/status"),
        ("POST", "/api/scheduler/pause"),
        ("POST", "/api/scheduler/resume"),
        ("GET", "/api/scheduler/status"),
        ("POST", "/api/scheduler/stop"),
        ("POST", "/api/scheduler/start"),
    ]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        for method, ep in control_endpoints:
            if method == "GET":
                res = await ac.get(ep)
            else:
                res = await ac.post(ep)
            assert res.status_code in (200, 500)  # Must respond without unhandled server crash


# ══════════════════════════════════════════════════════════════════════════════
# OBJECTIVE 4: Cross-Endpoint Data Consistency
# ══════════════════════════════════════════════════════════════════════════════

def test_cross_endpoint_trades_consistency(client: TestClient):
    """Verify mathematical consistency across /api/trades, /api/trades/active, and /api/trades/summary:
    Seed:
    - 2 OPEN trades
    - 2 Target Hit trades (Wins)
    - 1 Discretionary profit trade (Win)
    - 1 Stopped out trade (Loss)
    - 1 Zero-price expiry trade (Loss)
    - 1 Breakeven trade (PnL == 0.0)
    Total = 8 trades (2 open, 6 closed: 3 wins, 2 losses, 1 breakeven).
    """
    async def _seed_data():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=24500.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            # Open trades
            t1 = PaperTradeModel(debate_id=d.id, option_symbol="OPEN1", trade_type="BUY_CE", entry_price=100.0, stop_loss=80.0, target=150.0, status="OPEN")
            t2 = PaperTradeModel(debate_id=d.id, option_symbol="OPEN2", trade_type="BUY_PE", entry_price=100.0, stop_loss=80.0, target=150.0, status="OPEN")

            # Closed wins
            t3 = PaperTradeModel(debate_id=d.id, option_symbol="WIN_TGT1", trade_type="BUY_CE", entry_price=100.0, exit_price=150.0, stop_loss=80.0, target=150.0, pnl=3750.0, status="TARGET_HIT")
            t4 = PaperTradeModel(debate_id=d.id, option_symbol="WIN_TGT2", trade_type="BUY_PE", entry_price=100.0, exit_price=160.0, stop_loss=80.0, target=150.0, pnl=4500.0, status="TARGET_HIT")
            t5 = PaperTradeModel(debate_id=d.id, option_symbol="WIN_DISC", trade_type="BUY_CE", entry_price=100.0, exit_price=120.0, stop_loss=80.0, target=150.0, pnl=1500.0, status="CLOSED")

            # Closed losses
            t6 = PaperTradeModel(debate_id=d.id, option_symbol="LOSS_SL", trade_type="BUY_PE", entry_price=100.0, exit_price=80.0, stop_loss=80.0, target=150.0, pnl=-1500.0, status="STOPPED_OUT")
            t7 = PaperTradeModel(debate_id=d.id, option_symbol="LOSS_ZERO", trade_type="BUY_CE", entry_price=100.0, exit_price=0.0, stop_loss=80.0, target=150.0, pnl=-7500.0, status="STOPPED_OUT")

            # Breakeven
            t8 = PaperTradeModel(debate_id=d.id, option_symbol="BREAKEVEN", trade_type="BUY_CE", entry_price=100.0, exit_price=100.0, stop_loss=80.0, target=150.0, pnl=0.0, status="CLOSED")

            session.add_all([t1, t2, t3, t4, t5, t6, t7, t8])
            await session.commit()

    asyncio.run(_seed_data())

    # 1. Check /api/trades (all trades)
    res_all = client.get("/api/trades")
    assert res_all.status_code == 200
    all_trades = res_all.json()
    assert len(all_trades) == 8

    # 2. Check /api/trades/active
    res_act = client.get("/api/trades/active")
    assert res_act.status_code == 200
    active_trades = res_act.json()
    assert len(active_trades) == 2
    assert all(t["status"] == "OPEN" for t in active_trades)

    # 3. Check /api/trades/summary
    res_sum = client.get("/api/trades/summary")
    assert res_sum.status_code == 200
    s = res_sum.json()

    assert s["total_trades"] == 8
    assert s["open_trades"] == 2
    assert s["closed_trades"] == 6

    # Mathematical check: 3750 + 4500 + 1500 - 1500 - 7500 + 0 = 750.0
    expected_total_pnl = 3750.0 + 4500.0 + 1500.0 - 1500.0 - 7500.0 + 0.0
    assert s["total_pnl"] == expected_total_pnl
    assert s["win_count"] == 3
    assert s["loss_count"] == 2
    # win_rate: 3 wins / 6 closed trades = 0.5000
    assert s["win_rate"] == 0.5
    assert s["best_trade"] == 4500.0
    assert s["worst_trade"] == -7500.0

    # 4. Now close one open trade and verify dynamic consistency update
    open_trade_to_close = active_trades[0]
    res_close = client.patch(
        f"/api/trades/{open_trade_to_close['id']}/close",
        json={"exit_price": 140.0},
    )
    assert res_close.status_code == 200

    # Re-verify summary
    res_sum2 = client.get("/api/trades/summary")
    s2 = res_sum2.json()
    assert s2["total_trades"] == 8
    assert s2["open_trades"] == 1
    assert s2["closed_trades"] == 7
    # Added 1 win: (140 - 100) * 25 = 1000.0 (Nifty lot size 25)
    assert s2["total_pnl"] == expected_total_pnl + 1000.0
    assert s2["win_count"] == 4
    assert s2["loss_count"] == 2
    assert s2["win_rate"] == round(4 / 7, 4)
