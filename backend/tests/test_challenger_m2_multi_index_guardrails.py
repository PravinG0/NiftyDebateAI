"""Milestone 2 Challenger Stress Test Suite: Multi-Index Isolation, Strike Steps & Budget Guardrails.

Adversarial Verification Suite for Milestone 2:
1. Multi-Index Isolation & Sizing:
   - run_debate("NIFTY") creates trades with strictly 25 quantity and index_symbol "NIFTY".
   - run_debate("BANKNIFTY") creates trades with strictly 15 quantity and index_symbol "BANKNIFTY".
   - Case-insensitivity ("nifty", "banknifty", "BankNifty").
   - OpenAlgo place_order receives exact lot sizes (25 for Nifty, 15 for Bank Nifty).
2. Strike Steps & Ladder Calculations:
   - Bank Nifty ATM rounding and option chains strictly increment in 100-point steps.
   - Nifty 50 ATM rounding and option chains strictly increment in 50-point steps.
3. Rs 5,000 Capital Budget Guardrail Boundary Stress:
   - NIFTY premium = 200.00 -> outlay = 5,000.00 (Must allow)
   - NIFTY premium = 200.01 -> outlay = 5,000.25 (Must block/reject)
   - BANKNIFTY premium = 333.33 -> outlay = 4,999.95 (Must allow)
   - BANKNIFTY premium = 333.34 -> outlay = 5,000.10 (Must block/reject)
   - Boundary enforcement across validation function, run_debate engine, and manual trade API.
4. Manual Trade API Guardrails:
   - POST /api/trades/manual with lots=2 returns HTTP 400 Bad Request for NIFTY and BANKNIFTY.
   - POST /api/trades/manual with lots=1 and within-budget premium returns HTTP 200 OK.
   - POST /api/trades/manual with lots=1 and over-budget premium returns HTTP 400 Bad Request.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import (
    BUDGET_LIMIT,
    INDEX_CONFIG,
    BudgetExceededError,
    get_index_config,
    validate_paper_trade_guardrails,
)
from database import (
    DebateSessionModel,
    PaperTradeModel,
    async_session_factory,
    create_tables,
    engine,
)
from debate_engine import DebateEngine
from main import app
from market_data import (
    calculate_atm_strike,
    generate_option_chain,
)
from schemas import (
    BearAgentOutput,
    BullAgentOutput,
    JudgeDecision,
)
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


def _make_mock_agents():
    mock_bull = AsyncMock()
    mock_bull.analyze.return_value = BullAgentOutput(
        bias="BULLISH",
        confidence=8,
        recommended_strike="25000CE",
        arguments=[
            "EMA 20 holding above EMA 50",
            "Price above VWAP with rising volume",
            "Put writing support at ATM strikes",
        ],
    )
    mock_bear = AsyncMock()
    mock_bear.analyze.return_value = BearAgentOutput(
        bias="BEARISH",
        confidence=4,
        recommended_strike="24800PE",
        arguments=[
            "Overbought intraday RSI reading",
            "Call resistance near previous high",
            "Higher timeframe consolidation zone",
        ],
    )
    return mock_bull, mock_bear


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: Multi-Index Isolation & Position Sizing
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_debate_nifty_isolation_and_sizing():
    """Empirically verify run_debate('NIFTY') places exactly 25 quantity order and records NIFTY symbol."""
    mock_bull, mock_bear = _make_mock_agents()
    mock_judge = AsyncMock()
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_CE",
        selected_strike="25000CE",
        entry_price_estimate=120.0,
        stop_loss=90.0,
        target=180.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Bullish trend continuation",
        risk_warning="Watch intraday reversals",
    )

    mock_openalgo = AsyncMock()
    mock_openalgo.check_analyzer_mode.return_value = True
    mock_openalgo.place_order.return_value = {"order_id": "ord_nifty_1", "status": "COMPLETE"}

    engine_instance = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    session_res = await engine_instance.run_debate(symbol="NIFTY")
    assert session_res.index_symbol == "NIFTY"

    # Verify OpenAlgo place_order received exact Nifty lot size (25)
    mock_openalgo.place_order.assert_called_once()
    order_kwargs = mock_openalgo.place_order.call_args.kwargs
    assert int(order_kwargs["qty"]) == 25
    assert order_kwargs["symbol"].startswith("NIFTY")

    # Verify database persistence
    async with async_session_factory() as db:
        sess_row = (await db.execute(select(DebateSessionModel).where(DebateSessionModel.id == session_res.id))).scalar_one()
        assert sess_row.index_symbol == "NIFTY"

        trade_row = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == session_res.id))).scalar_one()
        assert int(trade_row.quantity) == 25
        assert trade_row.index_symbol == "NIFTY"
        assert trade_row.trade_type == "BUY_CE"


@pytest.mark.asyncio
async def test_run_debate_banknifty_isolation_and_sizing():
    """Empirically verify run_debate('BANKNIFTY') places exactly 15 quantity order and records BANKNIFTY symbol."""
    mock_bull, mock_bear = _make_mock_agents()
    mock_judge = AsyncMock()
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_PE",
        selected_strike="52000PE",
        entry_price_estimate=180.0,
        stop_loss=140.0,
        target=260.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Banking sector breakdown",
        risk_warning="Watch support at 51800",
    )

    mock_openalgo = AsyncMock()
    mock_openalgo.check_analyzer_mode.return_value = True
    mock_openalgo.place_order.return_value = {"order_id": "ord_bank_1", "status": "COMPLETE"}

    engine_instance = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    session_res = await engine_instance.run_debate(symbol="BANKNIFTY")
    assert session_res.index_symbol == "BANKNIFTY"

    # Verify OpenAlgo place_order received exact Bank Nifty lot size (15)
    mock_openalgo.place_order.assert_called_once()
    order_kwargs = mock_openalgo.place_order.call_args.kwargs
    assert int(order_kwargs["qty"]) == 15
    assert order_kwargs["symbol"].startswith("BANKNIFTY")

    # Verify database persistence
    async with async_session_factory() as db:
        sess_row = (await db.execute(select(DebateSessionModel).where(DebateSessionModel.id == session_res.id))).scalar_one()
        assert sess_row.index_symbol == "BANKNIFTY"

        trade_row = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == session_res.id))).scalar_one()
        assert int(trade_row.quantity) == 15
        assert trade_row.index_symbol == "BANKNIFTY"
        assert trade_row.trade_type == "BUY_PE"


@pytest.mark.asyncio
@pytest.mark.parametrize("sym_input,expected_idx,expected_qty", [
    ("nifty", "NIFTY", 25),
    ("Nifty", "NIFTY", 25),
    ("NIFTY 50", "NIFTY", 25),
    ("banknifty", "BANKNIFTY", 15),
    ("BankNifty", "BANKNIFTY", 15),
    ("BANK NIFTY", "BANKNIFTY", 15),
])
async def test_run_debate_symbol_normalization(sym_input, expected_idx, expected_qty):
    """Verify robust case and whitespace normalization for multi-index selection."""
    mock_bull, mock_bear = _make_mock_agents()
    mock_judge = AsyncMock()
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_CE",
        selected_strike=None,
        entry_price_estimate=50.0,
        stop_loss=30.0,
        target=90.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Summary",
        risk_warning="Warning",
    )

    mock_openalgo = AsyncMock()
    mock_openalgo.check_analyzer_mode.return_value = True
    mock_openalgo.place_order.return_value = {"order_id": "ord_test", "status": "COMPLETE"}

    engine_instance = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    session_res = await engine_instance.run_debate(symbol=sym_input)
    assert session_res.index_symbol == expected_idx

    order_kwargs = mock_openalgo.place_order.call_args.kwargs
    assert int(order_kwargs["qty"]) == expected_qty
    assert order_kwargs["symbol"].startswith(expected_idx)


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: Strike Step Increments (100 for Bank Nifty, 50 for Nifty)
# ══════════════════════════════════════════════════════════════════════════════

def test_banknifty_strike_steps_exact_100():
    """Verify Bank Nifty ATM rounding and strike steps increment by strictly 100 points."""
    step = INDEX_CONFIG["BANKNIFTY"]["strike_step"]
    assert step == 100

    test_spots = [
        (52000.0, 52000),
        (52049.9, 52000),
        (52050.1, 52100),
        (52099.9, 52100),
        (52100.0, 52100),
        (52140.8, 52100),
        (52165.2, 52200),
    ]
    for spot, expected_atm in test_spots:
        calc = calculate_atm_strike(spot, step=step)
        assert calc == expected_atm
        assert calc % 100 == 0

    # Midpoint exact rounding (IEEE 754 round-to-even)
    assert calculate_atm_strike(52050.0, step=step) in (52000, 52100)
    assert calculate_atm_strike(52150.0, step=step) in (52100, 52200)

    chain = generate_option_chain(52140.80, symbol="BANKNIFTY", num_strikes=5)
    assert len(chain) == 11
    strikes = [c["strike"] for c in chain]
    for i in range(len(strikes) - 1):
        assert strikes[i + 1] - strikes[i] == 100
        assert strikes[i] % 100 == 0


def test_nifty_strike_steps_exact_50():
    """Verify Nifty 50 ATM rounding and strike steps increment by strictly 50 points."""
    step = INDEX_CONFIG["NIFTY"]["strike_step"]
    assert step == 50

    test_spots = [
        (24900.0, 24900),
        (24924.9, 24900),
        (24925.1, 24950),
        (24949.9, 24950),
        (24950.0, 24950),
        (24986.4, 25000),
        (24974.9, 24950),
        (24975.1, 25000),
    ]
    for spot, expected_atm in test_spots:
        calc = calculate_atm_strike(spot, step=step)
        assert calc == expected_atm
        assert calc % 50 == 0

    # Midpoint exact rounding (IEEE 754 round-to-even)
    assert calculate_atm_strike(24925.0, step=step) in (24900, 24950)
    assert calculate_atm_strike(24975.0, step=step) in (24950, 25000)

    chain = generate_option_chain(24986.40, symbol="NIFTY", num_strikes=5)
    assert len(chain) == 11
    strikes = [c["strike"] for c in chain]
    for i in range(len(strikes) - 1):
        assert strikes[i + 1] - strikes[i] == 50
        assert strikes[i] % 50 == 0


@pytest.mark.asyncio
async def test_option_chain_endpoint_strike_steps():
    """Verify /api/market/option-chain endpoint outputs 100-step strikes for Bank Nifty and 50-step for Nifty."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Bank Nifty
        res_bank = await client.get("/api/market/option-chain?symbol=BANKNIFTY&strikes=4")
        assert res_bank.status_code == 200
        bank_data = res_bank.json()
        assert bank_data["symbol"] == "BANKNIFTY"
        assert bank_data["lot_size"] == 15
        bank_strikes = [item["strike"] for item in bank_data["chain"]]
        assert len(bank_strikes) == 9
        for i in range(len(bank_strikes) - 1):
            assert bank_strikes[i + 1] - bank_strikes[i] == 100

        # Nifty
        res_nifty = await client.get("/api/market/option-chain?symbol=NIFTY&strikes=4")
        assert res_nifty.status_code == 200
        nifty_data = res_nifty.json()
        assert nifty_data["symbol"] == "NIFTY"
        assert nifty_data["lot_size"] == 25
        nifty_strikes = [item["strike"] for item in nifty_data["chain"]]
        assert len(nifty_strikes) == 9
        for i in range(len(nifty_strikes) - 1):
            assert nifty_strikes[i + 1] - nifty_strikes[i] == 50


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: Rs 5,000 Capital Budget Guardrail Boundary Stress
# ══════════════════════════════════════════════════════════════════════════════

def test_budget_boundary_math_nifty():
    """Empirically test exact borderline option premiums for NIFTY (lot size 25, budget Rs 5,000):
    - premium 200.00 -> outlay = 5,000.00 (Must allow)
    - premium 200.01 -> outlay = 5,000.25 (Must block/reject)
    """
    # 200.00 -> ALLOW
    res_allow = validate_paper_trade_guardrails("NIFTY", entry_price=200.00, lots=1)
    assert res_allow["status"] == "APPROVED"
    assert res_allow["quantity"] == 25
    assert res_allow["outlay"] == 5000.00
    assert res_allow["outlay"] <= BUDGET_LIMIT

    # 200.01 -> BLOCK
    with pytest.raises(BudgetExceededError) as exc_info:
        validate_paper_trade_guardrails("NIFTY", entry_price=200.01, lots=1)
    err_msg = str(exc_info.value)
    assert "Outlay Rs 5000.25 exceeds Rs 5000.00 limit" in err_msg


def test_budget_boundary_math_banknifty():
    """Empirically test exact borderline option premiums for BANKNIFTY (lot size 15, budget Rs 5,000):
    - premium 333.33 -> outlay = 4,999.95 (Must allow)
    - premium 333.34 -> outlay = 5,000.10 (Must block/reject)
    """
    # 333.33 -> ALLOW
    res_allow = validate_paper_trade_guardrails("BANKNIFTY", entry_price=333.33, lots=1)
    assert res_allow["status"] == "APPROVED"
    assert res_allow["quantity"] == 15
    assert res_allow["outlay"] == 4999.95
    assert res_allow["outlay"] <= BUDGET_LIMIT

    # 333.34 -> BLOCK
    with pytest.raises(BudgetExceededError) as exc_info:
        validate_paper_trade_guardrails("BANKNIFTY", entry_price=333.34, lots=1)
    err_msg = str(exc_info.value)
    assert "Outlay Rs 5000.10 exceeds Rs 5000.00 limit" in err_msg


@pytest.mark.asyncio
async def test_run_debate_budget_guardrail_boundary_enforcement():
    """Verify DebateEngine.run_debate creates trades at 200.00 (Nifty) and 333.33 (Bank Nifty),
    and strictly blocks trades at 200.01 (Nifty) and 333.34 (Bank Nifty)."""
    mock_bull, mock_bear = _make_mock_agents()
    mock_openalgo = AsyncMock()
    mock_openalgo.check_analyzer_mode.return_value = True
    mock_openalgo.place_order.return_value = {"order_id": "ord_ok", "status": "COMPLETE"}

    mock_judge = AsyncMock()
    engine_instance = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    # 1. NIFTY 200.00 -> Trade Created
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_CE",
        selected_strike="25000CE",
        entry_price_estimate=200.00,
        stop_loss=170.0,
        target=260.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Summary",
        risk_warning="Warning",
    )
    sess_nifty_ok = await engine_instance.run_debate(symbol="NIFTY")
    async with async_session_factory() as db:
        t = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == sess_nifty_ok.id))).scalars().first()
        assert t is not None
        assert t.entry_price == 200.00
        assert int(t.quantity) == 25

    # 2. NIFTY 200.01 -> Trade Blocked (no trade row)
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_CE",
        selected_strike="25000CE",
        entry_price_estimate=200.01,
        stop_loss=170.0,
        target=260.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Summary",
        risk_warning="Warning",
    )
    sess_nifty_blocked = await engine_instance.run_debate(symbol="NIFTY")
    async with async_session_factory() as db:
        t = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == sess_nifty_blocked.id))).scalars().first()
        assert t is None

    # 3. BANKNIFTY 333.33 -> Trade Created
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_PE",
        selected_strike="52000PE",
        entry_price_estimate=333.33,
        stop_loss=300.0,
        target=400.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Summary",
        risk_warning="Warning",
    )
    sess_bank_ok = await engine_instance.run_debate(symbol="BANKNIFTY")
    async with async_session_factory() as db:
        t = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == sess_bank_ok.id))).scalars().first()
        assert t is not None
        assert t.entry_price == 333.33
        assert int(t.quantity) == 15

    # 4. BANKNIFTY 333.34 -> Trade Blocked (no trade row)
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_PE",
        selected_strike="52000PE",
        entry_price_estimate=333.34,
        stop_loss=300.0,
        target=400.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Summary",
        risk_warning="Warning",
    )
    sess_bank_blocked = await engine_instance.run_debate(symbol="BANKNIFTY")
    async with async_session_factory() as db:
        t = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == sess_bank_blocked.id))).scalars().first()
        assert t is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: Manual Trade API Guardrails (/api/trades/manual)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_manual_trade_api_lots_2_rejected_with_400():
    """Verify POST /api/trades/manual with lots=2 returns HTTP 400 Bad Request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # NIFTY lots=2
        res_nifty = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": 2,
            "entry_price": 50.0,
        })
        assert res_nifty.status_code == 400
        assert "Only 1-lot trading allowed" in res_nifty.json()["detail"]

        # BANKNIFTY lots=2
        res_bank = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY",
            "strike": 52000,
            "option_type": "PE",
            "lots": 2,
            "entry_price": 50.0,
        })
        assert res_bank.status_code == 400
        assert "Only 1-lot trading allowed" in res_bank.json()["detail"]


@pytest.mark.asyncio
async def test_manual_trade_api_budget_boundary_stress():
    """Verify POST /api/trades/manual enforces the exact Rs 5,000 budget boundary:
    - NIFTY 200.00 -> 200 OK
    - NIFTY 200.01 -> 400 Bad Request
    - BANKNIFTY 333.33 -> 200 OK
    - BANKNIFTY 333.34 -> 400 Bad Request
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. NIFTY 200.00 -> 200 OK
        r1 = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": 1,
            "entry_price": 200.00,
        })
        assert r1.status_code == 200
        assert r1.json()["success"] is True
        assert r1.json()["trade"]["total_amount"] == 5000.00
        assert r1.json()["trade"]["quantity"] == 25

        # 2. NIFTY 200.01 -> 400 Bad Request
        r2 = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": 1,
            "entry_price": 200.01,
        })
        assert r2.status_code == 400
        assert "exceeds Rs 5,000 budget limit" in r2.json()["detail"]

        # 3. BANKNIFTY 333.33 -> 200 OK
        r3 = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY",
            "strike": 52000,
            "option_type": "PE",
            "lots": 1,
            "entry_price": 333.33,
        })
        assert r3.status_code == 200
        assert r3.json()["success"] is True
        assert r3.json()["trade"]["total_amount"] == 4999.95
        assert r3.json()["trade"]["quantity"] == 15

        # 4. BANKNIFTY 333.34 -> 400 Bad Request
        r4 = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY",
            "strike": 52000,
            "option_type": "PE",
            "lots": 1,
            "entry_price": 333.34,
        })
        assert r4.status_code == 400
        assert "exceeds Rs 5,000 budget limit" in r4.json()["detail"]


@pytest.mark.asyncio
async def test_manual_trade_api_invalid_lots_schema():
    """Verify non-positive lots are rejected by schema or endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # lots = 0 -> 422 Unprocessable Entity
        r_zero = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": 0,
        })
        assert r_zero.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: Cross-Index Active Trades & Status Validation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_active_trades_multi_index_valuation():
    """Verify GET /api/trades/active correctly prices NIFTY and BANKNIFTY trades with their respective spots."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a NIFTY trade
        r_nifty = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": 1,
            "entry_price": 100.0,
        })
        assert r_nifty.status_code == 200

        # Create a BANKNIFTY trade
        r_bank = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY",
            "strike": 52000,
            "option_type": "PE",
            "lots": 1,
            "entry_price": 150.0,
        })
        assert r_bank.status_code == 200

        # Fetch active trades
        r_active = await client.get("/api/trades/active")
        assert r_active.status_code == 200
        trades = r_active.json()
        assert len(trades) >= 2

        nifty_trades = [t for t in trades if t["index_symbol"] == "NIFTY"]
        bank_trades = [t for t in trades if t["index_symbol"] == "BANKNIFTY"]

        assert len(nifty_trades) >= 1
        assert len(bank_trades) >= 1

        assert nifty_trades[0]["quantity"] == 25
        assert nifty_trades[0]["lot_size"] == 25
        assert nifty_trades[0]["lots"] == 1

        assert bank_trades[0]["quantity"] == 15
        assert bank_trades[0]["lot_size"] == 15
        assert bank_trades[0]["lots"] == 1


@pytest.mark.asyncio
async def test_status_endpoint_multi_index_metadata():
    """Verify /api/status returns dual index spots, budget limit, and default index configuration."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/status")
        assert res.status_code == 200
        data = res.json()

        assert "nifty_spot" in data
        assert "banknifty_spot" in data
        assert data["budget_limit"] == 5000.0
        assert data["max_lots"] == 1
        assert data["selected_index"] in ("NIFTY", "BANKNIFTY")
        assert data["nifty_spot"] > 20000.0
        assert data["banknifty_spot"] > 40000.0
