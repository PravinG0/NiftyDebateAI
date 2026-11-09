"""Adversarial Empirical Verification Suite by Challenger 1.

Stress-tests:
1. Multi-Index Dynamic Routing & Lot Sizes (NIFTY 25 / step 50 vs BANKNIFTY 15 / step 100).
2. Spot quote retrieval & option chain ATM increments.
3. run_debate setting PaperTradeModel.quantity == 25 for NIFTY and 15 for BANKNIFTY.
4. Verification that PaperTradeModel.quantity is genuine int (and != 75 when 25).
5. Strict Guardrail Boundaries:
   - Exact Rs 5,000.00 outlay: MUST be approved.
   - Exactly 1 tick over (Rs 5,000.25 / Rs 5,000.10): MUST be rejected.
   - Multi-lot orders (lots in [2, 3, 5, 10, 50]): MUST be rejected with HTTP 400 across engine and API.
   - Out-of-range lots (lots > 50, lots < 1): rejected with HTTP 422 by Pydantic schema.
   - Zero and negative entry prices: rejected.
6. Option Chain within_budget boolean precision at 5000.00 vs 5000.25.
7. SQLite schema inspection: column `quantity` type is INTEGER and post-commit default is 25.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text

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
from dhan_client import get_dhan_client
from main import app
from market_data import (
    build_market_snapshot,
    calculate_atm_strike,
    generate_option_chain,
    get_option_chain,
)
from schemas import (
    BearAgentOutput,
    BullAgentOutput,
    JudgeDecision,
    MarketSnapshot,
)
from scheduler import stop_scheduler


@pytest.fixture(autouse=True)
def clean_db():
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
    bull = AsyncMock()
    bull.analyze.return_value = BullAgentOutput(
        bias="BULLISH",
        confidence=8,
        recommended_strike="25000CE",
        arguments=[
            "Strong EMA crossover on 15m timeframe",
            "High volume breakout above VWAP level",
            "Put writing support accumulating at ATM strikes",
        ],
    )
    bear = AsyncMock()
    bear.analyze.return_value = BearAgentOutput(
        bias="BEARISH",
        confidence=3,
        recommended_strike="24800PE",
        arguments=[
            "Overbought RSI divergence on 5m timeframe",
            "Call writing resistance near overhead swing high",
            "Decelerating upside volume on momentum bars",
        ],
    )
    return bull, bear


# ══════════════════════════════════════════════════════════════════════════════
# 1. Multi-Index Dynamic Routing & Lot Sizes
# ══════════════════════════════════════════════════════════════════════════════

def test_index_config_invariants():
    """Verify INDEX_CONFIG specifies exact parameters for NIFTY and BANKNIFTY."""
    nifty_cfg = get_index_config("NIFTY")
    assert nifty_cfg["lot_size"] == 25
    assert nifty_cfg["strike_step"] == 50
    assert nifty_cfg["scrip_id"] == 13

    bank_cfg = get_index_config("BANKNIFTY")
    assert bank_cfg["lot_size"] == 15
    assert bank_cfg["strike_step"] == 100
    assert bank_cfg["scrip_id"] == 25


@pytest.mark.asyncio
async def test_spot_quote_retrieval_and_snapshot():
    """Verify spot quote retrieval and snapshot building for both indices."""
    dhan = get_dhan_client()

    # NIFTY spot
    nifty_ltp = await dhan.get_ltp("NIFTY")
    assert isinstance(nifty_ltp, float)
    assert nifty_ltp > 15000.0

    nifty_snap = await build_market_snapshot("NIFTY", force_fresh=True)
    assert nifty_snap.spot_price == nifty_ltp
    assert int(nifty_snap.atm_strike) % 50 == 0

    # BANKNIFTY spot
    bank_ltp = await dhan.get_ltp("BANKNIFTY")
    assert isinstance(bank_ltp, float)
    assert bank_ltp > 30000.0

    bank_snap = await build_market_snapshot("BANKNIFTY", force_fresh=True)
    assert bank_snap.spot_price == bank_ltp
    assert int(bank_snap.atm_strike) % 100 == 0


@pytest.mark.asyncio
async def test_option_chain_atm_increments():
    """Verify option chain ATM strikes increment in 50s for Nifty and 100s for Bank Nifty."""
    # NIFTY option chain
    nifty_chain = await get_option_chain("NIFTY", strikes=5)
    assert len(nifty_chain) == 11
    n_strikes = [row["strike"] for row in nifty_chain]
    for i in range(len(n_strikes) - 1):
        diff = n_strikes[i + 1] - n_strikes[i]
        assert diff == 50, f"Expected NIFTY strike step 50, got {diff} between {n_strikes[i]} and {n_strikes[i+1]}"
        assert n_strikes[i] % 50 == 0
    assert nifty_chain[0]["lot_size"] == 25

    # BANKNIFTY option chain
    bank_chain = await get_option_chain("BANKNIFTY", strikes=5)
    assert len(bank_chain) == 11
    b_strikes = [row["strike"] for row in bank_chain]
    for i in range(len(b_strikes) - 1):
        diff = b_strikes[i + 1] - b_strikes[i]
        assert diff == 100, f"Expected BANKNIFTY strike step 100, got {diff} between {b_strikes[i]} and {b_strikes[i+1]}"
        assert b_strikes[i] % 100 == 0
    assert bank_chain[0]["lot_size"] == 15


@pytest.mark.asyncio
async def test_run_debate_exact_quantities_and_native_integer_type():
    """Empirically verify run_debate sets PaperTradeModel.quantity == 25 for NIFTY,
    quantity == 15 for BANKNIFTY, and quantity is a real native Python int."""
    mock_bull, mock_bear = _make_mock_agents()
    mock_judge = AsyncMock()
    mock_openalgo = AsyncMock()
    mock_openalgo.check_analyzer_mode.return_value = True
    mock_openalgo.place_order.return_value = {"order_id": "test_ord", "status": "COMPLETE"}

    engine_instance = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    # 1. NIFTY
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_CE",
        selected_strike="25000CE",
        entry_price_estimate=100.0,
        stop_loss=70.0,
        target=160.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Nifty Bull Win",
        risk_warning="None",
    )
    sess_nifty = await engine_instance.run_debate(symbol="NIFTY")
    assert sess_nifty.index_symbol == "NIFTY"
    assert mock_openalgo.place_order.call_args.kwargs["qty"] == 25

    async with async_session_factory() as db:
        trade_nifty = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == sess_nifty.id))).scalar_one()
        assert trade_nifty.quantity == 25
        # Verify genuine int behavior:
        assert type(trade_nifty.quantity) is int
        assert trade_nifty.quantity != 75, "CRITICAL: Flexible quantity facade detected! 25 must NOT equal 75!"
        assert trade_nifty.quantity == 25
        assert trade_nifty.index_symbol == "NIFTY"

    mock_openalgo.place_order.reset_mock()

    # 2. BANKNIFTY
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_PE",
        selected_strike="52000PE",
        entry_price_estimate=200.0,
        stop_loss=160.0,
        target=280.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="BankNifty Bear Win",
        risk_warning="None",
    )
    sess_bank = await engine_instance.run_debate(symbol="BANKNIFTY")
    assert sess_bank.index_symbol == "BANKNIFTY"
    assert mock_openalgo.place_order.call_args.kwargs["qty"] == 15

    async with async_session_factory() as db:
        trade_bank = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == sess_bank.id))).scalar_one()
        assert trade_bank.quantity == 15
        assert type(trade_bank.quantity) is int
        assert trade_bank.quantity != 25
        assert trade_bank.quantity != 75
        assert trade_bank.quantity == 15
        assert trade_bank.index_symbol == "BANKNIFTY"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Strict Guardrail Boundaries (Rs 5,000.00 vs Rs 5,000.25)
# ══════════════════════════════════════════════════════════════════════════════

def test_guardrail_boundary_exact_5000_vs_5000_25_nifty():
    """Verify NIFTY (lot size 25):
    - entry_price 200.00: outlay = 5,000.00 -> MUST be approved.
    - entry_price 200.01: outlay = 5,000.25 (1 tick over) -> MUST be rejected.
    """
    # Exact 5000.00
    res = validate_paper_trade_guardrails("NIFTY", entry_price=200.00, lots=1)
    assert res["status"] == "APPROVED"
    assert res["outlay"] == 5000.00
    assert res["quantity"] == 25

    # 1 tick over: 5000.25
    with pytest.raises(BudgetExceededError) as exc_info:
        validate_paper_trade_guardrails("NIFTY", entry_price=200.01, lots=1)
    assert "Outlay Rs 5000.25 exceeds Rs 5000.00 limit" in str(exc_info.value)


def test_guardrail_boundary_banknifty():
    """Verify BANKNIFTY (lot size 15):
    - entry_price 333.33: outlay = 4,999.95 -> MUST be approved.
    - entry_price 333.34: outlay = 5,000.10 (1 tick over) -> MUST be rejected.
    """
    # 4999.95 <= 5000.00
    res = validate_paper_trade_guardrails("BANKNIFTY", entry_price=333.33, lots=1)
    assert res["status"] == "APPROVED"
    assert res["outlay"] == 4999.95
    assert res["quantity"] == 15

    # 1 tick over: 5000.10
    with pytest.raises(BudgetExceededError) as exc_info:
        validate_paper_trade_guardrails("BANKNIFTY", entry_price=333.34, lots=1)
    assert "Outlay Rs 5000.10 exceeds Rs 5000.00 limit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_debate_engine_boundary_enforcement():
    """Verify DebateEngine creates paper trade at 200.00 for NIFTY,
    and blocks trade (creates NO paper trade) at 200.01."""
    mock_bull, mock_bear = _make_mock_agents()
    mock_judge = AsyncMock()
    mock_openalgo = AsyncMock()
    mock_openalgo.check_analyzer_mode.return_value = True
    mock_openalgo.place_order.return_value = {"order_id": "test_ord", "status": "COMPLETE"}

    engine_instance = DebateEngine(
        openalgo_client=mock_openalgo,
        bull_agent=mock_bull,
        bear_agent=mock_bear,
        judge_agent=mock_judge,
    )

    # 1. 200.00 -> APPROVED & CREATED
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_CE",
        selected_strike="25000CE",
        entry_price_estimate=200.00,
        stop_loss=170.0,
        target=260.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Boundary Win",
        risk_warning="None",
    )
    s1 = await engine_instance.run_debate(symbol="NIFTY")
    async with async_session_factory() as db:
        t1 = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == s1.id))).scalars().first()
        assert t1 is not None
        assert t1.entry_price == 200.00
        assert t1.quantity == 25

    # 2. 200.01 -> REJECTED & NO TRADE CREATED
    mock_judge.evaluate.return_value = JudgeDecision(
        decision="BUY_CE",
        selected_strike="25000CE",
        entry_price_estimate=200.01,
        stop_loss=170.0,
        target=260.0,
        risk_reward_ratio="1:2.0",
        winning_argument_summary="Boundary Exceeded",
        risk_warning="None",
    )
    s2 = await engine_instance.run_debate(symbol="NIFTY")
    async with async_session_factory() as db:
        t2 = (await db.execute(select(PaperTradeModel).where(PaperTradeModel.debate_id == s2.id))).scalars().first()
        assert t2 is None, "CRITICAL: Overbudget trade must NOT be created!"


@pytest.mark.asyncio
async def test_manual_trade_boundary_enforcement():
    """Verify POST /api/trades/manual:
    - NIFTY 200.00 -> 200 OK (outlay 5000.00)
    - NIFTY 200.01 -> 400 Bad Request (outlay 5000.25)
    - BANKNIFTY 333.33 -> 200 OK (outlay 4999.95)
    - BANKNIFTY 333.34 -> 400 Bad Request (outlay 5000.10)
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. NIFTY 200.00
        r_n_ok = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": 1,
            "entry_price": 200.00,
        })
        assert r_n_ok.status_code == 200
        data_n = r_n_ok.json()
        assert data_n["trade"]["total_amount"] == 5000.00
        assert data_n["trade"]["quantity"] == 25

        # 2. NIFTY 200.01
        r_n_fail = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": 1,
            "entry_price": 200.01,
        })
        assert r_n_fail.status_code == 400
        assert "exceeds Rs 5,000 budget limit" in r_n_fail.json()["detail"]

        # 3. BANKNIFTY 333.33
        r_b_ok = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY",
            "strike": 52000,
            "option_type": "PE",
            "lots": 1,
            "entry_price": 333.33,
        })
        assert r_b_ok.status_code == 200
        data_b = r_b_ok.json()
        assert data_b["trade"]["total_amount"] == 4999.95
        assert data_b["trade"]["quantity"] == 15

        # 4. BANKNIFTY 333.34
        r_b_fail = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY",
            "strike": 52000,
            "option_type": "PE",
            "lots": 1,
            "entry_price": 333.34,
        })
        assert r_b_fail.status_code == 400
        assert "exceeds Rs 5,000 budget limit" in r_b_fail.json()["detail"]


# ══════════════════════════════════════════════════════════════════════════════
# 3. Multi-Lot Enforcement (lots >= 2) across Engine & API
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("lots", [2, 3, 4, 5, 10, 50])
def test_validate_paper_trade_guardrails_multi_lot_rejected(lots):
    """Verify validate_paper_trade_guardrails unconditionally rejects lots >= 2."""
    with pytest.raises(ValueError) as exc:
        validate_paper_trade_guardrails("NIFTY", entry_price=10.0, lots=lots)
    assert "Strict 1-lot order enforcement violated" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        validate_paper_trade_guardrails("BANKNIFTY", entry_price=10.0, lots=lots)
    assert "Strict 1-lot order enforcement violated" in str(exc.value)


@pytest.mark.parametrize("invalid_lots", [0, -1, -5])
def test_validate_paper_trade_guardrails_invalid_lots_rejected(invalid_lots):
    """Verify non-positive lots are rejected."""
    with pytest.raises(ValueError) as exc:
        validate_paper_trade_guardrails("NIFTY", entry_price=10.0, lots=invalid_lots)
    assert "Strict 1-lot order enforcement violated" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("lots", [2, 3, 5, 10, 50])
async def test_manual_trade_api_multi_lot_rejected_with_400(lots):
    """Verify POST /api/trades/manual strictly rejects multi-lot orders with HTTP 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # With explicit NIFTY
        res_n = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": lots,
            "entry_price": 10.0,
        })
        assert res_n.status_code == 400
        assert f"Only 1-lot trading allowed (requested {lots} lots)" in res_n.json()["detail"]

        # With explicit BANKNIFTY
        res_b = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY",
            "strike": 52000,
            "option_type": "PE",
            "lots": lots,
            "entry_price": 10.0,
        })
        assert res_b.status_code == 400
        assert f"Only 1-lot trading allowed (requested {lots} lots)" in res_b.json()["detail"]

        # Without symbol (defaults to NIFTY)
        res_def = await client.post("/api/trades/manual", json={
            "strike": 25000,
            "option_type": "CE",
            "lots": lots,
            "entry_price": 10.0,
        })
        assert res_def.status_code == 400
        assert f"Only 1-lot trading allowed (requested {lots} lots)" in res_def.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("out_of_range_lots", [0, -1, -10, 51, 100])
async def test_manual_trade_api_out_of_range_lots_rejected_with_422(out_of_range_lots):
    """Verify out-of-range lots (< 1 or > 50) are rejected by Pydantic schema with HTTP 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 25000,
            "option_type": "CE",
            "lots": out_of_range_lots,
            "entry_price": 50.0,
        })
        assert res.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# 4. Option Chain within_budget Boolean Precision
# ══════════════════════════════════════════════════════════════════════════════

def test_option_chain_within_budget_flag_precision():
    """Verify within_budget is True at <= 5000.0 and False at > 5000.0."""
    chain = generate_option_chain(25000.0, lot_size=25, num_strikes=5, symbol="NIFTY")
    for row in chain:
        ce = row["ce"]
        pe = row["pe"]
        assert ce["within_budget"] == (ce["lot_amount"] <= 5000.0)
        assert pe["within_budget"] == (pe["lot_amount"] <= 5000.0)

    # Edge cases:
    # 200.00 * 25 = 5000.00 -> True
    assert (200.00 * 25 <= 5000.0) is True
    # 200.01 * 25 = 5000.25 -> False
    assert (200.01 * 25 <= 5000.0) is False


# ══════════════════════════════════════════════════════════════════════════════
# 5. SQLite Schema & Database Integrity
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sqlite_column_schema_and_types():
    """Verify SQLite table PRAGMA shows quantity column is INTEGER and post-commit default is 25."""
    async with engine.connect() as conn:
        res = await conn.execute(text("PRAGMA table_info(paper_trades)"))
        cols = {row[1]: {"type": row[2], "notnull": row[3]} for row in res.fetchall()}
        assert "quantity" in cols
        assert cols["quantity"]["type"].upper() == "INTEGER"
        assert cols["quantity"]["notnull"] == 1

    # Verify post-commit default in database
    async with async_session_factory() as db:
        sess = DebateSessionModel(spot_price=25000.0)
        db.add(sess)
        await db.commit()
        await db.refresh(sess)

        trade = PaperTradeModel(
            debate_id=sess.id,
            option_symbol="NIFTY24000CE",
            trade_type="BUY_CE",
            entry_price=100.0,
            stop_loss=80.0,
            target=140.0,
        )
        db.add(trade)
        await db.commit()
        await db.refresh(trade)
        assert trade.quantity == 25
        assert type(trade.quantity) is int
        assert (trade.quantity == 75) is False
