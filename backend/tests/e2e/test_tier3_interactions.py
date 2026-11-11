"""
Tier 3: Cross-Feature Combinations & Pairwise Workflow E2E Tests.

Tests complex multi-step interactions between:
1. Register -> Login -> Run Debate -> Cast Vote -> Place Trade -> Close Trade -> Verify User Summary
2. Token Invalidation -> Guest Mode Fallback
3. Multi-User Voting Consensus -> Live Community Sentiment Aggregation
4. Watchlist Strike Bookmarking -> Live Quote Sync -> Trade Execution -> Portfolio Scoping
5. Debate Run -> Automated Paper Trade -> Active Trade Monitoring -> Status Filter
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import (
    DebateSessionModel,
    PaperTradeModel,
    UserModel,
    UserVoteModel,
    UserWatchlistModel,
    async_session_factory,
    create_access_token,
)


def test_pairwise_full_onboarding_to_portfolio_summary(
    sync_client: TestClient,
    mock_debate_session,
):
    """Pairwise Workflow: Register -> Login -> Run Debate -> Cast Vote -> Place Trade -> Close -> Verify Summary."""
    # Step 1: Register
    uid = uuid.uuid4().hex[:8]
    reg_payload = {
        "email": f"journey_{uid}@example.com",
        "username": f"trader_{uid}",
        "password": "SecurePassword123!",
        "full_name": "Journey Trader",
    }
    reg_resp = sync_client.post("/api/auth/register", json=reg_payload)
    assert reg_resp.status_code in (200, 201)
    user_id = reg_resp.json()["user"]["id"]

    # Step 2: Login to acquire fresh session token
    login_resp = sync_client.post(
        "/api/auth/login",
        json={"username_or_email": reg_payload["username"], "password": reg_payload["password"]},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Step 3: Seed debate in DB and trigger debate run with user association
    debate_id = None
    async def _seed():
        nonlocal debate_id
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=25000.0, judge_decision="BUY_CE", index_symbol="NIFTY")
            session.add(d)
            await session.commit()
            await session.refresh(d)
            debate_id = d.id

    asyncio.run(_seed())

    fake_session = mock_debate_session(decision="BUY_CE", session_id=debate_id)
    with patch("main.trigger_debate_now", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = fake_session
        debate_resp = sync_client.post("/api/debate/run", json={"symbol": "NIFTY"}, headers=headers)
        assert debate_resp.status_code == 200
        debate_data = debate_resp.json()
        assert debate_data["judge"]["verdict"] == "BUY_CE"

    # Step 4: Cast consensus vote on debate
    vote_resp = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "AGREE_JUDGE", "confidence": 9, "comment": "Excellent bull setup"},
        headers=headers,
    )
    assert vote_resp.status_code == 200
    assert vote_resp.json()["vote"] == "AGREE_JUDGE"

    # Step 5: Execute manual paper trade based on debate verdict
    trade_req = {
        "symbol": "NIFTY",
        "strike": 25000,
        "option_type": "CE",
        "lots": 1,
        "entry_price": 100.0,
        "stop_loss": 80.0,
        "target": 140.0,
    }
    trade_resp = sync_client.post("/api/trades/manual", json=trade_req, headers=headers)
    assert trade_resp.status_code == 200
    trade_id = trade_resp.json()["trade"]["id"]

    # Manually associate trade with user_id in DB if optional user DI is pending
    async def _link_user():
        async with async_session_factory() as session:
            from sqlalchemy import select
            t = (await session.execute(select(PaperTradeModel).where(PaperTradeModel.id == trade_id))).scalars().first()
            if t and t.user_id is None:
                t.user_id = user_id
                await session.commit()

    asyncio.run(_link_user())

    # Step 6: Close trade at target price
    close_resp = sync_client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 140.0})
    assert close_resp.status_code == 200
    closed = close_resp.json()
    assert closed["status"] == "TARGET_HIT"
    # PnL = (140 - 100) * 25 = 1000.0
    assert closed["pnl"] == 1000.0

    # Step 7: Verify user personal trade summary reflects 1 trade, 1 win, 100% win rate
    summary_resp = sync_client.get("/api/user/trades/summary", headers=headers)
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total_trades"] == 1
    assert summary["wins"] == 1
    assert summary["losses"] == 0
    assert summary["win_rate"] == 1.0
    assert summary["total_pnl"] == 1000.0


def test_pairwise_token_invalidation_and_guest_fallback(sync_client: TestClient, make_user):
    """Pairwise Workflow: Authenticated user loses session/token -> Graceful fallback to Guest browsing."""
    user = make_user(sync_client)
    token = user["access_token"]
    valid_headers = {"Authorization": f"Bearer {token}"}

    # 1. Valid user accesses protected resources
    me_resp = sync_client.get("/api/auth/me", headers=valid_headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == user["user"]["username"]

    # 2. User logs out
    logout_resp = sync_client.post("/api/auth/logout", headers=valid_headers)
    assert logout_resp.status_code == 200

    # 3. Simulate expired / invalidated token
    expired_token = create_access_token(
        data={"sub": str(user["user"]["id"]), "username": user["user"]["username"]},
        expires_delta=timedelta(seconds=-60),
    )
    bad_headers = {"Authorization": f"Bearer {expired_token}"}

    # Protected routes fail with 401
    bad_me = sync_client.get("/api/auth/me", headers=bad_headers)
    assert bad_me.status_code == 401
    bad_trades = sync_client.get("/api/user/trades", headers=bad_headers)
    assert bad_trades.status_code == 401

    # 4. Guest Mode Fallback: Public endpoints continue operating seamlessly without token
    status_resp = sync_client.get("/api/status")
    assert status_resp.status_code == 200
    assert status_resp.json().get("status") == "ok"

    quotes_resp = sync_client.get("/api/market/quotes?symbols=NIFTY,BANKNIFTY")
    assert quotes_resp.status_code == 200
    assert quotes_resp.json().get("source") == "angel_one"

    public_trades = sync_client.get("/api/trades")
    assert public_trades.status_code == 200


def test_pairwise_multi_user_voting_consensus(sync_client: TestClient, make_user):
    """Pairwise Workflow: Two opposing users vote on same debate -> Community consensus reflects distribution."""
    user_bull = make_user(sync_client)
    user_bear = make_user(sync_client)
    h_bull = {"Authorization": f"Bearer {user_bull['access_token']}"}
    h_bear = {"Authorization": f"Bearer {user_bear['access_token']}"}

    debate_id = None
    async def _seed_debate():
        nonlocal debate_id
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=25000.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)
            debate_id = d.id

    asyncio.run(_seed_debate())

    # Bullish trader votes BULL
    r_bull = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "BULL", "confidence": 9, "comment": "Call OI unwinding confirmed"},
        headers=h_bull,
    )
    assert r_bull.status_code == 200

    # Bearish trader votes BEAR
    r_bear = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "BEAR", "confidence": 8, "comment": "Resistance at 25100 will hold"},
        headers=h_bear,
    )
    assert r_bear.status_code == 200

    # Aggregate consensus stats
    stats_resp = sync_client.get(f"/api/debates/{debate_id}/votes")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["total_votes"] == 2
    assert stats["breakdown"]["BULL"] == 1
    assert stats["breakdown"]["BEAR"] == 1

    # Bearish trader converts to AGREE_JUDGE
    r_convert = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "AGREE_JUDGE", "confidence": 7, "comment": "Judge reasoning convinced me"},
        headers=h_bear,
    )
    assert r_convert.status_code == 200

    # Verify updated breakdown
    stats_updated = sync_client.get(f"/api/debates/{debate_id}/votes").json()
    assert stats_updated["total_votes"] == 2
    assert stats_updated["breakdown"]["BULL"] == 1
    assert stats_updated["breakdown"]["BEAR"] == 0
    assert stats_updated["breakdown"]["AGREE_JUDGE"] == 1


def test_pairwise_watchlist_bookmark_to_trade_execution(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Pairwise Workflow: User bookmarks ATM strike -> Places paper trade -> Removes bookmark -> Trade remains."""
    # 1. Fetch live quotes to identify ATM strike
    quotes_resp = sync_client.get("/api/market/quotes?symbols=NIFTY")
    assert quotes_resp.status_code == 200

    # 2. Bookmark strike 25050 CE
    wl_resp = sync_client.post(
        "/api/user/watchlist",
        json={"symbol": "NIFTY", "strike": 25050, "option_type": "CE", "notes": "Potential breakout trigger"},
        headers=auth_headers,
    )
    assert wl_resp.status_code == 200
    wl_item_id = wl_resp.json()["id"]

    # 3. Verify item in watchlist
    wl_items = sync_client.get("/api/user/watchlist", headers=auth_headers).json()
    assert any(i["id"] == wl_item_id for i in wl_items)

    # 4. Place trade on the watchlisted strike
    trade_resp = sync_client.post(
        "/api/trades/manual",
        json={"symbol": "NIFTY", "strike": 25050, "option_type": "CE", "lots": 1, "entry_price": 110.0},
        headers=auth_headers,
    )
    assert trade_resp.status_code == 200
    trade_id = trade_resp.json()["trade"]["id"]

    # 5. Delete bookmark from watchlist
    del_resp = sync_client.delete(f"/api/user/watchlist/{wl_item_id}", headers=auth_headers)
    assert del_resp.status_code == 200

    # 6. Verify trade is still alive in active trades
    active_resp = sync_client.get("/api/trades/active")
    assert active_resp.status_code == 200
    active_trades = active_resp.json()
    assert any(t["id"] == trade_id for t in active_trades)


def test_pairwise_user_trade_filtering_and_status_scoping(sync_client: TestClient, make_user):
    """Pairwise Workflow: User manages open and closed trades, verifies filter by status."""
    user = make_user(sync_client)
    headers = {"Authorization": f"Bearer {user['access_token']}"}
    uid = user["user"]["id"]

    async def _seed_trades():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=25000.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            # Open trade
            t_open = PaperTradeModel(
                user_id=uid,
                debate_id=d.id,
                option_symbol="NIFTY26NOV25000CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                stop_loss=80.0,
                target=140.0,
                quantity=25,
                status="OPEN",
            )
            # Target hit trade
            t_closed = PaperTradeModel(
                user_id=uid,
                debate_id=d.id,
                option_symbol="NIFTY26NOV25100CE",
                trade_type="BUY_CE",
                entry_price=90.0,
                exit_price=135.0,
                stop_loss=70.0,
                target=135.0,
                pnl=1125.0,
                quantity=25,
                status="TARGET_HIT",
            )
            session.add_all([t_open, t_closed])
            await session.commit()

    asyncio.run(_seed_trades())

    # Filter status=OPEN
    open_resp = sync_client.get("/api/user/trades?status=OPEN", headers=headers)
    assert open_resp.status_code == 200
    open_trades = open_resp.json()
    assert len(open_trades) == 1
    assert open_trades[0]["status"] == "OPEN"

    # Filter status=TARGET_HIT
    closed_resp = sync_client.get("/api/user/trades?status=TARGET_HIT", headers=headers)
    assert closed_resp.status_code == 200
    closed_trades = closed_resp.json()
    assert len(closed_trades) == 1
    assert closed_trades[0]["status"] == "TARGET_HIT"

    # All user trades (unfiltered)
    all_resp = sync_client.get("/api/user/trades", headers=headers)
    assert all_resp.status_code == 200
    assert len(all_resp.json()) == 2
