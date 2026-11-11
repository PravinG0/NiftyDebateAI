"""
Tier 1: Feature Coverage Automated E2E Tests.

Opaque-box tests verifying primary happy-path behavior for:
1. User Authentication (Register, Login with username/email, Me profile, Logout, JWT claims).
2. Database Persistence & Scoping (URL normalization, isolated user trades, user summary, watchlist CRUD, debate voting, guest preservation).
3. Angel One Real-Time Capabilities (Client configuration, single quote, batch quotes, indicator math, quotes endpoint, status telemetry).
4. Full Debate Workflow & Guardrails (Authenticated debate association, guest debate, 1-lot limits, manual trade guardrails, trade close PnL).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
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
    create_access_token,
    decode_access_token,
    hash_password,
    normalize_database_url,
    verify_password,
)
from angel_client import AngelClient, get_angel_client


# ══════════════════════════════════════════════════════════════════════════════
# 1. User Authentication Feature Coverage (>=5 test cases)
# ══════════════════════════════════════════════════════════════════════════════

def test_auth_register_happy_path(sync_client: TestClient):
    """Verify user registration returns HTTP 200/201 with JWT access token and user metadata."""
    uid = uuid.uuid4().hex[:8]
    payload = {
        "email": f"trader_{uid}@example.com",
        "username": f"trader_{uid}",
        "password": "SecurePassword123!",
        "full_name": "Pro Trader",
    }
    resp = sync_client.post("/api/auth/register", json=payload)
    assert resp.status_code in (200, 201), f"Unexpected status: {resp.status_code}, {resp.text}"
    data = resp.json()

    assert "access_token" in data
    assert data.get("token_type") == "bearer"
    assert "user" in data
    user = data["user"]
    assert user["email"] == payload["email"]
    assert user["username"] == payload["username"]
    assert user["full_name"] == payload["full_name"]
    assert user["is_active"] is True
    assert "id" in user


def test_auth_login_with_username_happy_path(sync_client: TestClient, make_user):
    """Verify user can authenticate using username and receive JWT token."""
    user_info = make_user(sync_client, password="MySecretPassword99!")
    username = user_info["user"]["username"]

    login_resp = sync_client.post(
        "/api/auth/login",
        json={"username_or_email": username, "password": "MySecretPassword99!"},
    )
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    data = login_resp.json()
    assert "access_token" in data
    assert data["user"]["username"] == username


def test_auth_login_with_email_happy_path(sync_client: TestClient, make_user):
    """Verify user can authenticate using email address and receive JWT token."""
    user_info = make_user(sync_client, password="MySecretPassword99!")
    email = user_info["user"]["email"]

    login_resp = sync_client.post(
        "/api/auth/login",
        json={"username_or_email": email, "password": "MySecretPassword99!"},
    )
    assert login_resp.status_code == 200, f"Login by email failed: {login_resp.text}"
    data = login_resp.json()
    assert "access_token" in data
    assert data["user"]["email"] == email


def test_auth_get_me_profile_happy_path(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify authenticated GET /api/auth/me returns current user profile."""
    resp = sync_client.get("/api/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    user = resp.json()
    assert "id" in user
    assert "email" in user
    assert "username" in user
    assert user.get("is_active") is True


def test_auth_logout_happy_path(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify authenticated POST /api/auth/logout succeeds with acknowledgment."""
    resp = sync_client.post("/api/auth/logout", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "success"


def test_auth_jwt_token_claims_and_expiration():
    """Verify JWT token creation encodes user claims and expiration correctly."""
    token = create_access_token(
        data={"sub": "123", "username": "testuser", "email": "test@example.com"},
        expires_delta=timedelta(hours=2),
    )
    payload = decode_access_token(token)
    assert payload["sub"] == "123"
    assert payload["username"] == "testuser"
    assert payload["email"] == "test@example.com"
    assert "exp" in payload
    # Expiration is in the future
    assert payload["exp"] > datetime.now(timezone.utc).timestamp()


# ══════════════════════════════════════════════════════════════════════════════
# 2. Database Persistence & Scoping Feature Coverage (>=5 test cases)
# ══════════════════════════════════════════════════════════════════════════════

def test_db_url_normalization_postgres_and_asyncpg():
    """Verify normalize_database_url handles postgres URLs, asyncpg driver, and sslmode."""
    # 1. postgres:// standard replacement
    url1 = "postgres://user:pass@ep-cool-db.us-east-2.render.com/niftydebate"
    norm1 = normalize_database_url(url1)
    assert norm1.startswith("postgresql+asyncpg://")

    # 2. postgresql:// replacement
    url2 = "postgresql://user:pass@ep-cool-db.us-east-2.render.com/niftydebate"
    norm2 = normalize_database_url(url2)
    assert norm2.startswith("postgresql+asyncpg://")

    # 3. sslmode=require replaced with ssl=require for asyncpg
    url3 = "postgresql://user:pass@host/db?sslmode=require"
    norm3 = normalize_database_url(url3)
    assert "ssl=require" in norm3
    assert "sslmode=require" not in norm3

    # 4. sqlite preserved
    url4 = "sqlite+aiosqlite:///niftydebate.db"
    assert normalize_database_url(url4) == url4


def test_user_scoped_trades_isolation(sync_client: TestClient, make_user):
    """Verify user trades are strictly isolated so User A only sees User A's trades."""
    user_a = make_user(sync_client)
    user_b = make_user(sync_client)

    headers_a = {"Authorization": f"Bearer {user_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {user_b['access_token']}"}

    # Seed trade for user A and user B
    async def _seed():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=25000.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            t_a = PaperTradeModel(
                user_id=user_a["user"]["id"],
                debate_id=d.id,
                option_symbol="NIFTY26NOV25000CE",
                trade_type="BUY_CE",
                entry_price=150.0,
                stop_loss=120.0,
                target=210.0,
                quantity=25,
                status="OPEN",
            )
            t_b = PaperTradeModel(
                user_id=user_b["user"]["id"],
                debate_id=d.id,
                option_symbol="NIFTY26NOV25000PE",
                trade_type="BUY_PE",
                entry_price=130.0,
                stop_loss=100.0,
                target=190.0,
                quantity=25,
                status="OPEN",
            )
            session.add_all([t_a, t_b])
            await session.commit()

    asyncio.run(_seed())

    # User A queries trades
    resp_a = sync_client.get("/api/user/trades", headers=headers_a)
    assert resp_a.status_code == 200
    trades_a = resp_a.json()
    assert len(trades_a) == 1
    assert trades_a[0]["option_symbol"] == "NIFTY26NOV25000CE"

    # User B queries trades
    resp_b = sync_client.get("/api/user/trades", headers=headers_b)
    assert resp_b.status_code == 200
    trades_b = resp_b.json()
    assert len(trades_b) == 1
    assert trades_b[0]["option_symbol"] == "NIFTY26NOV25000PE"


def test_user_trades_summary_statistics(sync_client: TestClient, make_user):
    """Verify /api/user/trades/summary calculates wins, losses, win rate, and total PnL correctly."""
    user = make_user(sync_client)
    headers = {"Authorization": f"Bearer {user['access_token']}"}
    uid = user["user"]["id"]

    async def _seed():
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=25000.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            # Win trade
            t1 = PaperTradeModel(
                user_id=uid,
                debate_id=d.id,
                option_symbol="NIFTY26NOV25000CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                exit_price=150.0,
                stop_loss=80.0,
                target=150.0,
                pnl=1250.0,
                status="TARGET_HIT",
                quantity=25,
            )
            # Loss trade
            t2 = PaperTradeModel(
                user_id=uid,
                debate_id=d.id,
                option_symbol="NIFTY26NOV25000CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                exit_price=80.0,
                stop_loss=80.0,
                target=150.0,
                pnl=-500.0,
                status="STOPPED_OUT",
                quantity=25,
            )
            # Open trade
            t3 = PaperTradeModel(
                user_id=uid,
                debate_id=d.id,
                option_symbol="NIFTY26NOV25000CE",
                trade_type="BUY_CE",
                entry_price=120.0,
                stop_loss=90.0,
                target=180.0,
                pnl=None,
                status="OPEN",
                quantity=25,
            )
            session.add_all([t1, t2, t3])
            await session.commit()

    asyncio.run(_seed())

    resp = sync_client.get("/api/user/trades/summary", headers=headers)
    assert resp.status_code == 200
    summary = resp.json()

    assert summary["total_trades"] == 3
    assert summary["open_trades"] == 1
    assert summary["closed_trades"] == 2
    assert summary["total_pnl"] == 750.0
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["win_rate"] == 0.5
    assert summary["best_trade"] == 1250.0
    assert summary["worst_trade"] == -500.0


def test_user_watchlist_crud_lifecycle(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify authenticated user can Add, List, and Delete watchlist items."""
    # 1. Add to watchlist
    add_resp = sync_client.post(
        "/api/user/watchlist",
        json={"symbol": "NIFTY", "strike": 25100, "option_type": "CE", "notes": "Key resistance strike"},
        headers=auth_headers,
    )
    assert add_resp.status_code == 200
    item = add_resp.json()
    assert item["status"] == "success"
    item_id = item["id"]

    # 2. List watchlist
    get_resp = sync_client.get("/api/user/watchlist", headers=auth_headers)
    assert get_resp.status_code == 200
    items = get_resp.json()
    assert len(items) >= 1
    assert any(i["id"] == item_id and i["strike"] == 25100 for i in items)

    # 3. Delete from watchlist
    del_resp = sync_client.delete(f"/api/user/watchlist/{item_id}", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json().get("status") == "success"

    # Confirm deletion
    get_after = sync_client.get("/api/user/watchlist", headers=auth_headers)
    assert not any(i["id"] == item_id for i in get_after.json())


def test_user_debate_voting_and_aggregation(sync_client: TestClient, make_user):
    """Verify users can cast votes on debates and retrieve aggregated consensus breakdown."""
    user1 = make_user(sync_client)
    user2 = make_user(sync_client)
    h1 = {"Authorization": f"Bearer {user1['access_token']}"}
    h2 = {"Authorization": f"Bearer {user2['access_token']}"}

    # Seed debate
    debate_id = None
    async def _seed():
        nonlocal debate_id
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=25000.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)
            debate_id = d.id

    asyncio.run(_seed())

    # User 1 votes BULL
    v1_resp = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "BULL", "confidence": 8, "comment": "Strong breakout setup"},
        headers=h1,
    )
    assert v1_resp.status_code == 200
    assert v1_resp.json()["vote"] == "BULL"

    # User 2 votes AGREE_JUDGE
    v2_resp = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "AGREE_JUDGE", "confidence": 9, "comment": "Judge made a solid call"},
        headers=h2,
    )
    assert v2_resp.status_code == 200

    # Retrieve aggregate votes (public endpoint)
    stats_resp = sync_client.get(f"/api/debates/{debate_id}/votes")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["total_votes"] == 2
    assert stats["breakdown"]["BULL"] == 1
    assert stats["breakdown"]["AGREE_JUDGE"] == 1


def test_guest_public_access_preservation(sync_client: TestClient):
    """Verify unauthenticated guests can freely access status, public trades, and debate history."""
    # 1. Status
    status_resp = sync_client.get("/api/status")
    assert status_resp.status_code == 200
    assert status_resp.json().get("status") == "ok"

    # 2. Public trade history
    trades_resp = sync_client.get("/api/trades")
    assert trades_resp.status_code == 200
    assert isinstance(trades_resp.json(), list)

    # 3. Public debate latest
    latest_resp = sync_client.get("/api/debate/latest")
    assert latest_resp.status_code in (200, 404)  # 404 is allowed if no debates exist yet


# ══════════════════════════════════════════════════════════════════════════════
# 3. Angel One Real-Time Capabilities Feature Coverage (>=5 test cases)
# ══════════════════════════════════════════════════════════════════════════════

def test_angel_client_configuration_check():
    """Verify AngelClient detects configured vs unconfigured credentials."""
    configured = AngelClient(
        api_key="API_KEY_123",
        client_code="CLIENT_CODE_123",
        pin="1234",
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    assert configured.is_configured() is True

    unconfigured = AngelClient(api_key="", client_code="", pin="", totp_secret="")
    assert unconfigured.is_configured() is False


@pytest.mark.asyncio
async def test_angel_client_get_quote_returns_ohlc_and_changes():
    """Verify get_quote returns full quote with spot price, OHLC, and percent change."""
    client = AngelClient("KEY", "CODE", "1234", "SECRET")
    with patch.object(client, "get_ltp", new_callable=AsyncMock) as mock_ltp:
        mock_ltp.return_value = 25044.65
        quote = await client.get_quote("NIFTY")

        assert quote is not None
        assert quote["spot_price"] == 25044.65
        assert quote["close"] == 25044.65
        assert "open" in quote and quote["open"] > 0
        assert "high" in quote and quote["high"] >= quote["spot_price"]
        assert "low" in quote and quote["low"] <= quote["spot_price"]
        assert "net_change" in quote
        assert "percent_change" in quote


@pytest.mark.asyncio
async def test_angel_client_get_multi_quotes_batch():
    """Verify get_multi_quotes returns a mapping of quotes for all requested symbols."""
    client = AngelClient("KEY", "CODE", "1234", "SECRET")
    client._jwt_token = "MOCK_JWT"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": True,
        "data": {
            "fetched": [
                {
                    "symbolToken": "99926000",
                    "exchange": "NSE",
                    "tradingSymbol": "Nifty 50",
                    "ltp": 25044.65,
                    "open": 25076.4,
                    "high": 25084.05,
                    "low": 24838.75,
                    "close": 24986.4,
                    "netChange": 58.25,
                    "percentChange": 0.23,
                },
                {
                    "symbolToken": "99926009",
                    "exchange": "NSE",
                    "tradingSymbol": "Nifty Bank",
                    "ltp": 59741.25,
                    "open": 59219.95,
                    "high": 59741.25,
                    "low": 58877.85,
                    "close": 58850.65,
                    "netChange": 890.6,
                    "percentChange": 1.51,
                },
            ]
        },
    }

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch.object(client, "authenticate", new_callable=AsyncMock) as mock_auth, \
         patch.object(client, "get_http_client", new_callable=AsyncMock) as mock_get_client:
        mock_auth.return_value = True
        mock_get_client.return_value = mock_http

        quotes = await client.get_multi_quotes(["NIFTY", "BANKNIFTY"])

        assert "NIFTY" in quotes
        assert quotes["NIFTY"]["spot_price"] == 25044.65
        assert "BANKNIFTY" in quotes
        assert quotes["BANKNIFTY"]["spot_price"] == 59741.25


def test_angel_technical_indicators_calculation_math():
    """Verify calculate_indicators correctly derives EMA 20, EMA 50, RSI 14, VWAP, ATR."""
    client = AngelClient("KEY", "CODE", "1234", "SECRET")
    
    # Generate 60 synthetic candles with an upward trend
    base_price = 24000.0
    candles = []
    for i in range(60):
        c_open = base_price + i * 10
        c_close = c_open + 8
        c_high = c_close + 5
        c_low = c_open - 3
        candles.append({
            "time": 1700000000 + i * 300,
            "open": c_open,
            "high": c_high,
            "low": c_low,
            "close": c_close,
            "volume": 1000 + i * 50,
        })

    ind = client.calculate_indicators(candles)
    assert "ema_20" in ind and ind["ema_20"] > 0
    assert "ema_50" in ind and ind["ema_50"] > 0
    assert "rsi" in ind and 0 <= ind["rsi"] <= 100
    assert "vwap" in ind and ind["vwap"] > 0
    assert "atr" in ind and ind["atr"] > 0
    # In an uptrend, EMA 20 should exceed EMA 50
    assert ind["ema_20"] > ind["ema_50"]


def test_market_quotes_api_endpoint(sync_client: TestClient):
    """Verify /api/market/quotes returns valid quote structure for NIFTY and BANKNIFTY."""
    resp = sync_client.get("/api/market/quotes?symbols=NIFTY,BANKNIFTY")
    assert resp.status_code == 200
    data = resp.json()

    assert data.get("source") == "angel_one"
    assert "quotes" in data
    assert "timestamp" in data
    quotes = data["quotes"]
    assert "NIFTY" in quotes or "BANKNIFTY" in quotes


def test_system_status_angel_telemetry(sync_client: TestClient):
    """Verify /api/status exposes market status and Angel One telemetry."""
    resp = sync_client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()

    assert data.get("status") == "ok"
    assert "angel_connected" in data
    assert "symbol" in data or "selected_index" in data


# ══════════════════════════════════════════════════════════════════════════════
# 4. Full Debate Workflow & Guardrails Feature Coverage (>=5 test cases)
# ══════════════════════════════════════════════════════════════════════════════

def test_debate_run_with_authenticated_user(
    sync_client: TestClient,
    auth_headers: Dict[str, str],
    mock_debate_session,
):
    """Verify running a debate as an authenticated user attributes the debate to user_id."""
    fake_session = mock_debate_session(decision="BUY_CE", session_id=101)

    with patch("main.trigger_debate_now", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = fake_session
        resp = sync_client.post("/api/debate/run", json={"symbol": "NIFTY"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["id"] == 101
        assert "bull" in data
        assert "bear" in data
        assert "judge" in data
        assert data["judge"]["verdict"] == "BUY_CE"


def test_debate_run_as_guest_anonymous(sync_client: TestClient, mock_debate_session):
    """Verify running a debate as a guest does not fail and executes anonymously."""
    fake_session = mock_debate_session(decision="BUY_PE", session_id=102)

    with patch("main.trigger_debate_now", new_callable=AsyncMock) as mock_trigger:
        mock_trigger.return_value = fake_session
        resp = sync_client.post("/api/debate/run", json={"symbol": "NIFTY"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 102
        assert data["judge"]["verdict"] == "BUY_PE"


def test_paper_trade_guardrail_one_lot_enforcement(sync_client: TestClient):
    """Verify paper trade creations enforce strict 1-lot limits (NIFTY=25, BANKNIFTY=15)."""
    from config import validate_paper_trade_guardrails, BudgetExceededError

    # 1. NIFTY valid 1 lot (lots=1 -> 25 qty)
    trade_nifty = validate_paper_trade_guardrails(
        symbol="NIFTY",
        entry_price=100.0,
        lots=1,
        stop_loss=80.0,
        target=140.0,
    )
    assert trade_nifty["lot_size"] == 25
    assert trade_nifty["quantity"] == 25

    # 2. BANKNIFTY valid 1 lot (lots=1 -> 15 qty)
    trade_bank = validate_paper_trade_guardrails(
        symbol="BANKNIFTY",
        entry_price=200.0,
        lots=1,
        stop_loss=160.0,
        target=260.0,
    )
    assert trade_bank["lot_size"] == 15
    assert trade_bank["quantity"] == 15

    # 3. Exceeding 1 lot raises ValueError
    with pytest.raises(ValueError, match="Strict 1-lot order enforcement violated"):
        validate_paper_trade_guardrails(
            symbol="NIFTY",
            entry_price=100.0,
            lots=2,
            stop_loss=80.0,
            target=140.0,
        )

    # 4. Exceeding outlay limit raises BudgetExceededError
    with pytest.raises(BudgetExceededError):
        validate_paper_trade_guardrails(
            symbol="NIFTY",
            entry_price=250.0,  # 250 * 25 = 6250 > 5000 budget
            lots=1,
        )


def test_manual_trade_execution_under_guardrails(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify manual trade endpoint /api/trades/manual creates guardrail-compliant position."""
    # Seed debate session
    debate_id = None
    async def _seed():
        nonlocal debate_id
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=25000.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)
            debate_id = d.id

    asyncio.run(_seed())

    req_payload = {
        "symbol": "NIFTY",
        "strike": 25000,
        "option_type": "CE",
        "lots": 1,
        "entry_price": 120.0,
        "stop_loss": 95.0,
        "target": 155.0,
    }

    resp = sync_client.post("/api/trades/manual", json=req_payload, headers=auth_headers)
    assert resp.status_code == 200, f"Manual trade failed: {resp.text}"
    data = resp.json()
    trade = data.get("trade", data)
    assert "25000CE" in trade["option_symbol"] or "NIFTY" in trade["option_symbol"]
    assert trade["status"] == "OPEN"
    assert trade["quantity"] == 25
    assert trade["entry_price"] == 120.0


def test_paper_trade_closing_and_pnl_calculation(sync_client: TestClient):
    """Verify closing a trade via PATCH /api/trades/{id}/close computes realized PnL correctly."""
    trade_id = None
    async def _seed():
        nonlocal trade_id
        async with async_session_factory() as session:
            d = DebateSessionModel(spot_price=25000.0, judge_decision="BUY_CE")
            session.add(d)
            await session.commit()
            await session.refresh(d)

            t = PaperTradeModel(
                debate_id=d.id,
                option_symbol="NIFTY26NOV25000CE",
                trade_type="BUY_CE",
                entry_price=100.0,
                stop_loss=80.0,
                target=150.0,
                quantity=25,
                status="OPEN",
            )
            session.add(t)
            await session.commit()
            await session.refresh(t)
            trade_id = t.id

    asyncio.run(_seed())

    # Close with profit at 150.0
    close_resp = sync_client.patch(f"/api/trades/{trade_id}/close", json={"exit_price": 150.0})
    assert close_resp.status_code == 200
    closed_trade = close_resp.json()

    assert closed_trade["status"] == "TARGET_HIT"
    # PnL = (150 - 100) * 25 = 1250.0
    assert closed_trade["pnl"] == 1250.0
    assert closed_trade["realized_pnl"] == 1250.0
