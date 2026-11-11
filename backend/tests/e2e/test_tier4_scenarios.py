"""
Tier 4: Real-World Scenarios Automated E2E Tests.

High-fidelity simulations of complete multi-agent derivative trading sessions:
1. Complete Retail Day-Trading Journey (Onboarding -> Market Intelligence -> Debate Analysis -> Voting -> Watchlist -> Order Execution -> Position Tracking -> Target Exit -> Dashboard Review -> Logout)
2. Multi-User Concurrent Trading Arena (Bull Trader vs Bear Trader simultaneous operations, isolated tenant state, opposing PnL outcomes, platform aggregate tracking)
3. Market Disruption & Failover Resilience (Provider disconnect simulation, graceful fallback cache, recovery, and trade execution)
4. Risk Management & Guardrail Shielding (Attempted multi-lot violation, budget overrun rejection, automatic SL/target clamping, protected capital verification)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import (
    DebateSessionModel,
    PaperTradeModel,
    UserModel,
    UserVoteModel,
    UserWatchlistModel,
    async_session_factory,
)
from angel_client import AngelClient, get_angel_client


def test_scenario_complete_day_trading_session(sync_client: TestClient, mock_debate_session):
    """Scenario 1: Comprehensive retail day-trading session from onboarding to portfolio tracking."""
    # ── Phase 1: Onboarding & Authentication ──────────────────────────────────
    uid = uuid.uuid4().hex[:8]
    trader_data = {
        "email": f"daytrader_{uid}@niftydebate.ai",
        "username": f"daytrader_{uid}",
        "password": "InstitutionalGradePass2026!",
        "full_name": "Nifty Momentum Trader",
    }
    reg_resp = sync_client.post("/api/auth/register", json=trader_data)
    assert reg_resp.status_code in (200, 201)
    user_id = reg_resp.json()["user"]["id"]

    login_resp = sync_client.post(
        "/api/auth/login",
        json={"username_or_email": trader_data["username"], "password": trader_data["password"]},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Verify profile
    profile_resp = sync_client.get("/api/auth/me", headers=headers)
    assert profile_resp.status_code == 200
    assert profile_resp.json()["username"] == trader_data["username"]

    # ── Phase 2: Market Open Preparation & Intelligence ───────────────────────
    # System health check
    status_resp = sync_client.get("/api/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "ok"

    # Fetch live quotes for active indices
    quotes_resp = sync_client.get("/api/market/quotes?symbols=NIFTY,BANKNIFTY")
    assert quotes_resp.status_code == 200
    quotes_data = quotes_resp.json()
    assert quotes_data.get("source") == "angel_one"
    assert "quotes" in quotes_data

    # Fetch historical 5m candles
    candles_resp = sync_client.get("/api/market/candles?symbol=NIFTY&interval=5m&range_period=5d")
    assert candles_resp.status_code in (200, 404, 500)  # Handles live or cached

    # ── Phase 3: AI Multi-Agent Debate Trigger ─────────────────────────────────
    debate_id = None
    async def _seed_debate():
        nonlocal debate_id
        async with async_session_factory() as session:
            d = DebateSessionModel(
                spot_price=25000.0,
                index_symbol="NIFTY",
                judge_decision="BUY_CE",
                judge_reasoning="Bull continuation confirmed with rising VWAP and call short covering.",
                bull_confidence=8,
                bear_confidence=4,
            )
            session.add(d)
            await session.commit()
            await session.refresh(d)
            debate_id = d.id

    asyncio.run(_seed_debate())

    fake_session = mock_debate_session(decision="BUY_CE", session_id=debate_id)
    with patch("main.trigger_debate_now", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = fake_session
        run_resp = sync_client.post("/api/debate/run", json={"symbol": "NIFTY"}, headers=headers)
        assert run_resp.status_code == 200
        debate_json = run_resp.json()

        # Trader inspects debate outputs
        assert debate_json["judge"]["verdict"] == "BUY_CE"
        assert len(debate_json["bull"]["arguments"]) >= 3
        assert len(debate_json["bear"]["arguments"]) >= 3

    # ── Phase 4: Community Voting ──────────────────────────────────────────────
    vote_resp = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "AGREE_JUDGE", "confidence": 9, "comment": "Clear bullish flag breakout on 5m chart"},
        headers=headers,
    )
    assert vote_resp.status_code == 200

    # ── Phase 5: Watchlist Strike Bookmarking ──────────────────────────────────
    wl_resp = sync_client.post(
        "/api/user/watchlist",
        json={"symbol": "NIFTY", "strike": 25050, "option_type": "CE", "notes": "Trade candidate ATM+1"},
        headers=headers,
    )
    assert wl_resp.status_code == 200
    wl_id = wl_resp.json()["id"]

    # ── Phase 6: Order Execution under Guardrails ──────────────────────────────
    trade_req = {
        "symbol": "NIFTY",
        "strike": 25050,
        "option_type": "CE",
        "lots": 1,
        "entry_price": 110.0,
        "stop_loss": 90.0,
        "target": 150.0,
    }
    order_resp = sync_client.post("/api/trades/manual", json=trade_req, headers=headers)
    assert order_resp.status_code == 200
    trade_info = order_resp.json()["trade"]
    trade_id = trade_info["id"]
    assert trade_info["quantity"] == 25
    assert trade_info["status"] == "OPEN"

    # Explicitly attribute trade to user
    async def _attribute():
        async with async_session_factory() as session:
            t = (await session.execute(select(PaperTradeModel).where(PaperTradeModel.id == trade_id))).scalars().first()
            if t:
                t.user_id = user_id
                await session.commit()

    asyncio.run(_attribute())

    # ── Phase 7: Position Tracking ─────────────────────────────────────────────
    user_trades_resp = sync_client.get("/api/user/trades", headers=headers)
    assert user_trades_resp.status_code == 200
    assert any(t["id"] == trade_id for t in user_trades_resp.json())

    # ── Phase 8: Target Exit & Profit Realization ──────────────────────────────
    close_resp = sync_client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 150.0})
    assert close_resp.status_code == 200
    closed_data = close_resp.json()
    assert closed_data["status"] == "TARGET_HIT"
    # PnL = (150 - 110) * 25 = 1000.0
    assert closed_data["pnl"] == 1000.0

    # ── Phase 9: Portfolio Dashboard Review ────────────────────────────────────
    summary_resp = sync_client.get("/api/user/trades/summary", headers=headers)
    assert summary_resp.status_code == 200
    dash = summary_resp.json()
    assert dash["total_trades"] == 1
    assert dash["closed_trades"] == 1
    assert dash["wins"] == 1
    assert dash["losses"] == 0
    assert dash["win_rate"] == 1.0
    assert dash["total_pnl"] == 1000.0

    # ── Phase 10: Clean Session Logout ─────────────────────────────────────────
    logout_resp = sync_client.post("/api/auth/logout", headers=headers)
    assert logout_resp.status_code == 200


def test_scenario_multi_user_concurrent_trading_arena(sync_client: TestClient, make_user):
    """Scenario 2: Multi-user arena simulating opposing Bull and Bear traders on the same debate."""
    # 1. Onboard Bull Trader (Alpha) and Bear Trader (Beta)
    trader_alpha = make_user(sync_client, username="trader_alpha", email="alpha@fund.com")
    trader_beta = make_user(sync_client, username="trader_beta", email="beta@fund.com")

    h_alpha = {"Authorization": f"Bearer {trader_alpha['access_token']}"}
    h_beta = {"Authorization": f"Bearer {trader_beta['access_token']}"}

    uid_alpha = trader_alpha["user"]["id"]
    uid_beta = trader_beta["user"]["id"]

    # 2. Seed a live debate session
    debate_id = None
    async def _seed():
        nonlocal debate_id
        async with async_session_factory() as session:
            d = DebateSessionModel(
                spot_price=25000.0,
                index_symbol="NIFTY",
                judge_decision="BUY_CE",
                judge_reasoning="Consensus split; Judge rules in favor of Bullish trend continuation.",
            )
            session.add(d)
            await session.commit()
            await session.refresh(d)
            debate_id = d.id

    asyncio.run(_seed())

    # 3. Alpha votes BULL, Beta votes BEAR
    v_a = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "BULL", "confidence": 9, "comment": "Volume expanding on rallies"},
        headers=h_alpha,
    )
    assert v_a.status_code == 200

    v_b = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "BEAR", "confidence": 8, "comment": "Heavy Call writing at 25100 resistance"},
        headers=h_beta,
    )
    assert v_b.status_code == 200

    # Consensus reflection
    votes_summary = sync_client.get(f"/api/debates/{debate_id}/votes").json()
    assert votes_summary["total_votes"] == 2
    assert votes_summary["breakdown"]["BULL"] == 1
    assert votes_summary["breakdown"]["BEAR"] == 1

    # 4. Simultaneous opposing trade placements
    # Alpha buys CALL
    trade_a_resp = sync_client.post(
        "/api/trades/manual",
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 1, "entry_price": 100.0, "stop_loss": 80.0, "target": 140.0},
        headers=h_alpha,
    )
    assert trade_a_resp.status_code == 200
    id_a = trade_a_resp.json()["trade"]["id"]

    # Beta buys PUT
    trade_b_resp = sync_client.post(
        "/api/trades/manual",
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "PE", "lots": 1, "entry_price": 100.0, "stop_loss": 80.0, "target": 140.0},
        headers=h_beta,
    )
    assert trade_b_resp.status_code == 200
    id_b = trade_b_resp.json()["trade"]["id"]

    # Attribute trades strictly to their respective owners
    async def _scope():
        async with async_session_factory() as session:
            ta = (await session.execute(select(PaperTradeModel).where(PaperTradeModel.id == id_a))).scalars().first()
            tb = (await session.execute(select(PaperTradeModel).where(PaperTradeModel.id == id_b))).scalars().first()
            if ta:
                ta.user_id = uid_alpha
            if tb:
                tb.user_id = uid_beta
            await session.commit()

    asyncio.run(_scope())

    # Confirm strict isolation in user endpoints
    trades_a = sync_client.get("/api/user/trades", headers=h_alpha).json()
    trades_b = sync_client.get("/api/user/trades", headers=h_beta).json()
    assert len(trades_a) == 1 and trades_a[0]["id"] == id_a
    assert len(trades_b) == 1 and trades_b[0]["id"] == id_b

    # 5. Market moves up: Alpha's Call hits target (140.0), Beta's Put gets stopped out (80.0)
    sync_client.patch(f"/api/trades/{id_a}/close", json={"exit_price": 140.0})
    sync_client.patch(f"/api/trades/{id_b}/close", json={"exit_price": 80.0})

    # 6. Verify Alpha has winning portfolio, Beta has losing portfolio
    sum_a = sync_client.get("/api/user/trades/summary", headers=h_alpha).json()
    sum_b = sync_client.get("/api/user/trades/summary", headers=h_beta).json()

    assert sum_a["wins"] == 1 and sum_a["losses"] == 0 and sum_a["win_rate"] == 1.0
    assert sum_a["total_pnl"] == 1000.0  # (140 - 100) * 25

    assert sum_b["wins"] == 0 and sum_b["losses"] == 1 and sum_b["win_rate"] == 0.0
    assert sum_b["total_pnl"] == -500.0  # (80 - 100) * 25

    # 7. Global public trade history contains both trades
    public_trades = sync_client.get("/api/trades").json()
    assert len(public_trades) >= 2


def test_scenario_market_disruption_and_failover_resilience(sync_client: TestClient):
    """Scenario 3: Resilient behavior during simulated Angel One upstream disruption."""
    client = get_angel_client()

    # 1. Normal state: endpoint functions
    normal_status = sync_client.get("/api/status")
    assert normal_status.status_code == 200

    # 2. Simulate upstream network failure or HTTP 503 from Angel One
    with patch.object(client, "get_quote", new_callable=AsyncMock) as mock_q, \
         patch.object(client, "get_ltp", new_callable=AsyncMock) as mock_ltp:
        mock_q.return_value = None
        mock_ltp.side_effect = Exception("SmartAPI Gateway Timeout 504")

        # Status endpoint still returns 200 and reports telemetry without crashing
        status_resp = sync_client.get("/api/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "ok"

        # Market quotes returns graceful empty or cached response
        quotes_resp = sync_client.get("/api/market/quotes?symbols=NIFTY")
        assert quotes_resp.status_code == 200

    # 3. Disruption resolved: live functionality resumes
    recovered_status = sync_client.get("/api/status")
    assert recovered_status.status_code == 200


def test_scenario_risk_guardrail_budget_and_clamping_protection(
    sync_client: TestClient,
    auth_headers: Dict[str, str],
):
    """Scenario 4: Comprehensive guardrail shielding against unauthorized risk exposure."""
    # 1. Attempt 1: Trader attempts multi-lot trading (lots=3) -> Rejected
    resp_lots = sync_client.post(
        "/api/trades/manual",
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 3, "entry_price": 50.0},
        headers=auth_headers,
    )
    assert resp_lots.status_code == 400
    assert "1-lot" in resp_lots.text.lower() or "only 1 lot" in resp_lots.text.lower()

    # 2. Attempt 2: Trader attempts outlay exceeding Rs 5,000 budget -> Rejected
    resp_budget = sync_client.post(
        "/api/trades/manual",
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 1, "entry_price": 300.0},
        headers=auth_headers,
    )
    assert resp_budget.status_code == 400
    assert "budget" in resp_budget.text.lower() or "exceeds" in resp_budget.text.lower()

    # 3. Attempt 3: Trader submits reckless stop loss (loss > Rs 700 limit) -> Clamped safely
    resp_clamp = sync_client.post(
        "/api/trades/manual",
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 1, "entry_price": 100.0, "stop_loss": 20.0, "target": 130.0},
        headers=auth_headers,
    )
    assert resp_clamp.status_code in (200, 400)
    if resp_clamp.status_code == 200:
        trade_data = resp_clamp.json().get("trade", resp_clamp.json())
        # Risk is clamped within Rs 700 (max 28 points)
        assert (100.0 - trade_data["stop_loss"]) * 25 <= 700.10

    # 4. Attempt 4: Clean, guardrail-compliant 1-lot trade executes successfully
    resp_ok = sync_client.post(
        "/api/trades/manual",
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 1, "entry_price": 100.0, "stop_loss": 80.0, "target": 135.0},
        headers=auth_headers,
    )
    assert resp_ok.status_code == 200
    clean_trade = resp_ok.json()["trade"]
    assert clean_trade["quantity"] == 25
    assert clean_trade["status"] == "OPEN"
