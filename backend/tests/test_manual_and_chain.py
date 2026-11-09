"""Tests for Manual Paper Trading, Option Chain, and Angel One lot sizing."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from database import DebateSessionModel, PaperTradeModel, async_session_factory
from main import app
from market_data import calculate_option_premium, generate_option_chain


def test_calculate_option_premium():
    ce = calculate_option_premium(spot=24500.0, strike=24500, option_type="CE")
    pe = calculate_option_premium(spot=24500.0, strike=24500, option_type="PE")
    assert ce > 0
    assert pe > 0
    # ITM call should be worth more than OTM call
    itm_ce = calculate_option_premium(spot=24500.0, strike=24000, option_type="CE")
    otm_ce = calculate_option_premium(spot=24500.0, strike=25000, option_type="CE")
    assert itm_ce > otm_ce


def test_generate_option_chain():
    chain = generate_option_chain(spot_price=24500.0, lot_size=25, num_strikes=5)
    assert len(chain) == 11
    atm_items = [c for c in chain if c["is_atm"]]
    assert len(atm_items) == 1
    assert atm_items[0]["strike"] == 24500
    assert atm_items[0]["ce"]["lot_amount"] == round(atm_items[0]["ce"]["ltp"] * 25, 2)
    assert atm_items[0]["pe"]["lot_amount"] == round(atm_items[0]["pe"]["ltp"] * 25, 2)


@pytest.mark.asyncio
async def test_api_option_chain():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/market/option-chain?symbol=NIFTY&strikes=3")
        assert res.status_code == 200
        data = res.json()
        assert data["symbol"] == "NIFTY"
        assert data["lot_size"] == 25
        assert len(data["chain"]) == 7


@pytest.mark.asyncio
async def test_manual_paper_trade_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "strike": 24600,
            "option_type": "PE",
            "lots": 1,
            "entry_price": 140.0,
            "stop_loss": 105.0,
            "target": 190.0,
        }
        res = await client.post("/api/trades/manual", json=payload)
        assert res.status_code == 200
        trade = res.json()["trade"]
        assert trade["trade_source"] == "MANUAL"
        assert trade["trade_type"] == "BUY_PE"
        assert trade["quantity"] == 25
        assert trade["lots"] == 1
        assert trade["total_amount"] == 3500.0
        trade_id = trade["id"]

        # Verify active trades list includes it
        active_res = await client.get("/api/trades/active")
        assert active_res.status_code == 200
        active_trades = active_res.json()
        matching = [t for t in active_trades if t["id"] == trade_id]
        assert len(matching) == 1
        assert matching[0]["trade_source"] == "MANUAL"

        # Close trade
        close_res = await client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 180.0})
        assert close_res.status_code == 200
        closed_trade = close_res.json()
        assert closed_trade["status"] == "CLOSED"
        assert closed_trade["pnl"] == round((180.0 - 140.0) * 25, 2)


@pytest.mark.asyncio
async def test_manual_paper_trade_guardrails_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Multi-lot rejection (lots = 2) without explicit symbol
        res_lots = await client.post("/api/trades/manual", json={
            "strike": 24600,
            "option_type": "PE",
            "lots": 2,
            "entry_price": 100.0,
        })
        assert res_lots.status_code == 400
        assert "Only 1-lot trading allowed" in res_lots.json()["detail"]

        # Multi-lot rejection (lots = 3) with explicit symbol
        res_lots_sym = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY",
            "strike": 24600,
            "option_type": "PE",
            "lots": 3,
            "entry_price": 100.0,
        })
        assert res_lots_sym.status_code == 400
        assert "Only 1-lot trading allowed" in res_lots_sym.json()["detail"]

        # Budget exceeded rejection (entry_price * 25 > 5000) without explicit symbol
        res_budget = await client.post("/api/trades/manual", json={
            "strike": 24600,
            "option_type": "PE",
            "lots": 1,
            "entry_price": 250.0,  # 250 * 25 = 6250 > 5000
        })
        assert res_budget.status_code == 400
        assert "exceeds Rs 5,000 budget limit" in res_budget.json()["detail"]

        # Budget exceeded rejection with explicit symbol
        res_budget_sym = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY",
            "strike": 52000,
            "option_type": "CE",
            "lots": 1,
            "entry_price": 400.0,  # 400 * 15 = 6000 > 5000
        })
        assert res_budget_sym.status_code == 400
        assert "exceeds Rs 5,000 budget limit" in res_budget_sym.json()["detail"]

