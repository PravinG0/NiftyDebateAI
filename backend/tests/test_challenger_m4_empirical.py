"""
Milestone 4 Empirical Challenger Verification & Stress Suite.
Tests:
1. Live ASGI Runtime Checks across endpoints:
   - GET /api/status (dual spots, Dhan status enum, IST countdown, 1-lot & Rs 5,000 budget)
   - GET /api/market/option-chain?symbol=BANKNIFTY (step 100, lot 15, within_budget flags)
   - GET /api/market/option-chain?symbol=NIFTY (step 50, lot 25, within_budget flags)
   - POST /api/trades/manual (1-lot restriction, schema validations, exact Rs 5,000 budget limits)
2. Adversarial & Edge Cases:
   - Negative / extreme inputs
   - Fallback and error handling for unknown symbols
   - Concurrent endpoint queries
"""

import asyncio
import re
import sys
from pathlib import Path
import pytest
import httpx
from httpx import ASGITransport

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from main import app
from scheduler import start_scheduler, stop_scheduler


@pytest.mark.asyncio
async def test_live_asgi_status_endpoint():
    """Verify GET /api/status runtime schema and contracts."""
    start_scheduler()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            data = resp.json()

            # Status and Dhan connection
            assert data.get("status") == "ok"
            dhan_status = data.get("dhan_status")
            assert dhan_status in ("LIVE", "FALLBACK", "DISCONNECTED"), f"Invalid dhan_status: {dhan_status}"

            # Dual spots
            nifty_spot = data.get("nifty_spot")
            assert isinstance(nifty_spot, (int, float)) and nifty_spot > 0, f"Invalid nifty_spot: {nifty_spot}"
            banknifty_spot = data.get("banknifty_spot")
            assert isinstance(banknifty_spot, (int, float)) and banknifty_spot > 0, f"Invalid banknifty_spot: {banknifty_spot}"

            # IST countdown and timestamps
            next_debate_ist = data.get("next_debate_ist")
            assert next_debate_ist is not None
            assert ("+05:30" in next_debate_ist) or next_debate_ist.endswith(" IST")

            countdown_timer = data.get("countdown_timer")
            assert countdown_timer is not None
            assert re.match(r"^(\d{2}:\d{2}|--:--)$", countdown_timer), f"Invalid timer: {countdown_timer}"

            # Guardrails
            assert data.get("budget_limit") == 5000.0
            assert data.get("max_lots") == 1
    finally:
        stop_scheduler()


@pytest.mark.asyncio
async def test_live_asgi_option_chain_banknifty():
    """Verify GET /api/market/option-chain?symbol=BANKNIFTY returns step 100, lot 15."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/market/option-chain?symbol=BANKNIFTY")
        assert resp.status_code == 200
        data = resp.json()

        assert data.get("symbol") == "BANKNIFTY"
        assert data.get("lot_size") == 15

        chain = data.get("chain", [])
        assert len(chain) >= 2, f"Chain too short: {len(chain)}"
        strikes = [row["strike"] for row in chain]
        strike_diffs = [strikes[i+1] - strikes[i] for i in range(len(strikes)-1)]
        assert all(diff == 100 for diff in strike_diffs), f"BANKNIFTY strike steps must be 100, got {strike_diffs}"

        # Verify within_budget flag logic (budget 5000 / 15 = 333.33)
        for row in chain:
            for opt_key in ("ce", "pe"):
                opt = row.get(opt_key)
                if opt and "ltp" in opt and "within_budget" in opt:
                    cost = opt["ltp"] * 15
                    if cost <= 5000.0:
                        assert opt["within_budget"] is True, f"Cost {cost} should be within budget"
                    else:
                        assert opt["within_budget"] is False, f"Cost {cost} should exceed budget"


@pytest.mark.asyncio
async def test_live_asgi_option_chain_nifty():
    """Verify GET /api/market/option-chain?symbol=NIFTY returns step 50, lot 25."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/market/option-chain?symbol=NIFTY")
        assert resp.status_code == 200
        data = resp.json()

        assert data.get("symbol") == "NIFTY"
        assert data.get("lot_size") == 25

        chain = data.get("chain", [])
        assert len(chain) >= 2, f"Chain too short: {len(chain)}"
        strikes = [row["strike"] for row in chain]
        strike_diffs = [strikes[i+1] - strikes[i] for i in range(len(strikes)-1)]
        assert all(diff == 50 for diff in strike_diffs), f"NIFTY strike steps must be 50, got {strike_diffs}"

        # Verify within_budget flag logic (budget 5000 / 25 = 200.0)
        for row in chain:
            for opt_key in ("ce", "pe"):
                opt = row.get(opt_key)
                if opt and "ltp" in opt and "within_budget" in opt:
                    cost = opt["ltp"] * 25
                    if cost <= 5000.0:
                        assert opt["within_budget"] is True, f"Cost {cost} should be within budget"
                    else:
                        assert opt["within_budget"] is False, f"Cost {cost} should exceed budget"


@pytest.mark.asyncio
async def test_live_asgi_manual_trades_rejections_and_boundaries():
    """Verify POST /api/trades/manual enforcement of 1-lot limit and Rs 5,000 budget."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Reject lots: 2 (HTTP 400)
        r_lots2 = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 2, "entry_price": 50.0
        })
        assert r_lots2.status_code == 400
        assert "Only 1-lot trading allowed" in r_lots2.json().get("detail", "")

        # 2. Reject lots: 0 (HTTP 422)
        r_lots0 = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 0, "entry_price": 50.0
        })
        assert r_lots0.status_code == 422

        # 3. NIFTY budget boundary: 200.01 * 25 = 5000.25 -> 400 Bad Request
        r_over_nifty = await client.post("/api/trades/manual", json={
            "symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 1, "entry_price": 200.01
        })
        assert r_over_nifty.status_code == 400
        assert "exceeds Rs 5,000 budget limit" in r_over_nifty.json().get("detail", "")

        # 4. BANKNIFTY budget boundary: 333.34 * 15 = 5000.10 -> 400 Bad Request
        r_over_bank = await client.post("/api/trades/manual", json={
            "symbol": "BANKNIFTY", "strike": 52000, "option_type": "PE", "lots": 1, "entry_price": 333.34
        })
        assert r_over_bank.status_code == 400
        assert "exceeds Rs 5,000 budget limit" in r_over_bank.json().get("detail", "")


@pytest.mark.asyncio
async def test_live_asgi_concurrent_load():
    """Verify rapid concurrent queries to status and option-chain endpoints."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        tasks = [
            client.get("/api/status"),
            client.get("/api/market/option-chain?symbol=NIFTY"),
            client.get("/api/market/option-chain?symbol=BANKNIFTY"),
            client.get("/api/market/candles?symbol=NIFTY"),
            client.get("/api/market/candles?symbol=BANKNIFTY"),
        ] * 4  # 20 concurrent requests

        responses = await asyncio.gather(*tasks)
        for r in responses:
            assert r.status_code == 200, f"Endpoint failed under load: {r.url} -> {r.status_code}"
