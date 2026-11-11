"""
Tier 2: Boundary, Corner Cases, and Adversarial E2E Tests.

Tests edge conditions, invalid formats, missing fields, duplicate values,
extreme parameters, and expired/tampered tokens across:
1. User Authentication Boundaries
2. Database Persistence & Scoping Boundaries
3. Angel One Real-Time Boundaries
4. Debate & Trade Guardrail Boundaries
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import jwt
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
    decode_access_token,
    normalize_database_url,
)
from angel_client import AngelClient


# ══════════════════════════════════════════════════════════════════════════════
# 1. User Authentication Boundaries (>=5 test cases)
# ══════════════════════════════════════════════════════════════════════════════

def test_auth_register_duplicate_email(sync_client: TestClient, make_user):
    """Verify attempting to register with an existing email returns HTTP 400 Bad Request."""
    first = make_user(sync_client, email="dup_email@example.com", username="user_first1")

    dup_resp = sync_client.post(
        "/api/auth/register",
        json={
            "email": "dup_email@example.com",
            "username": "user_second2",
            "password": "Password123!",
        },
    )
    assert dup_resp.status_code == 400
    assert "already registered" in dup_resp.text.lower()


def test_auth_register_duplicate_username(sync_client: TestClient, make_user):
    """Verify attempting to register with an existing username returns HTTP 400 Bad Request."""
    first = make_user(sync_client, email="user_one@example.com", username="dup_username")

    dup_resp = sync_client.post(
        "/api/auth/register",
        json={
            "email": "user_two@example.com",
            "username": "dup_username",
            "password": "Password123!",
        },
    )
    assert dup_resp.status_code == 400
    assert "already taken" in dup_resp.text.lower()


def test_auth_register_short_password(sync_client: TestClient):
    """Verify passwords shorter than 6 characters are rejected with HTTP 422."""
    resp = sync_client.post(
        "/api/auth/register",
        json={
            "email": "short_pwd@example.com",
            "username": "short_pwd_user",
            "password": "123",  # Under 6 chars
        },
    )
    assert resp.status_code == 422


def test_auth_register_invalid_email_format(sync_client: TestClient):
    """Verify malformed email addresses are rejected with HTTP 422."""
    for bad_email in ["notanemail", "user@", "@domain.com", "user@domain"]:
        resp = sync_client.post(
            "/api/auth/register",
            json={
                "email": bad_email,
                "username": f"user_{uuid.uuid4().hex[:6]}",
                "password": "ValidPassword123!",
            },
        )
        assert resp.status_code == 422, f"Expected 422 for bad email '{bad_email}', got {resp.status_code}"


def test_auth_login_wrong_password(sync_client: TestClient, make_user):
    """Verify correct username with wrong password returns HTTP 401 Unauthorized."""
    user = make_user(sync_client, password="RealPassword123!")
    username = user["user"]["username"]

    resp = sync_client.post(
        "/api/auth/login",
        json={"username_or_email": username, "password": "WrongPassword999!"},
    )
    assert resp.status_code == 401
    assert "invalid" in resp.text.lower()


def test_auth_login_nonexistent_user(sync_client: TestClient):
    """Verify authentication for non-existent username returns HTTP 401 Unauthorized."""
    resp = sync_client.post(
        "/api/auth/login",
        json={"username_or_email": "ghost_trader_does_not_exist", "password": "AnyPassword123!"},
    )
    assert resp.status_code == 401


def test_auth_expired_jwt_token(sync_client: TestClient):
    """Verify protected endpoints reject expired JWT tokens with HTTP 401 Unauthorized."""
    expired_token = create_access_token(
        data={"sub": "1", "username": "expired_user"},
        expires_delta=timedelta(seconds=-10),  # In the past
    )
    headers = {"Authorization": f"Bearer {expired_token}"}
    resp = sync_client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert "expired" in resp.text.lower() or "credentials" in resp.text.lower()


def test_auth_malformed_jwt_token(sync_client: TestClient):
    """Verify protected endpoints reject garbage/tampered tokens with HTTP 401 Unauthorized."""
    bad_headers = {"Authorization": "Bearer this_is_not_a_valid_jwt_token"}
    resp = sync_client.get("/api/auth/me", headers=bad_headers)
    assert resp.status_code == 401


def test_auth_missing_bearer_header(sync_client: TestClient):
    """Verify strict protected endpoint returns HTTP 401 when Authorization header is omitted."""
    resp = sync_client.get("/api/auth/me")
    assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# 2. Database Persistence & Scoping Boundaries (>=5 test cases)
# ══════════════════════════════════════════════════════════════════════════════

def test_db_url_normalization_edge_cases():
    """Verify normalize_database_url handles edge inputs like whitespaces, query params, etc."""
    # Whitespace trimming
    assert normalize_database_url("   postgres://user:pass@host/db   ") == "postgresql+asyncpg://user:pass@host/db"

    # Already normalized URL remains unchanged
    already = "postgresql+asyncpg://user:pass@host/db"
    assert normalize_database_url(already) == already

    # Complex query params
    complex_url = "postgresql://user:pass@neon.tech/main?sslmode=require&channel_binding=disable"
    norm = normalize_database_url(complex_url)
    assert "ssl=require" in norm
    assert "sslmode=require" not in norm


def test_watchlist_duplicate_item_idempotent(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify adding duplicate strike & contract to watchlist handles idempotently without error."""
    payload = {"symbol": "NIFTY", "strike": 25200, "option_type": "CE", "notes": "Test strike"}

    # First add
    r1 = sync_client.post("/api/user/watchlist", json=payload, headers=auth_headers)
    assert r1.status_code == 200
    first_id = r1.json()["id"]

    # Duplicate add
    r2 = sync_client.post("/api/user/watchlist", json=payload, headers=auth_headers)
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2.get("status") in ("exists", "success")
    assert data2.get("id") == first_id


def test_watchlist_delete_nonexistent_item(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify deleting a non-existent watchlist item returns HTTP 404 Not Found."""
    resp = sync_client.delete("/api/user/watchlist/999999", headers=auth_headers)
    assert resp.status_code == 404


def test_watchlist_delete_other_user_item_forbidden(sync_client: TestClient, make_user):
    """Verify User A cannot delete User B's watchlist item (enforcing tenant isolation)."""
    user_a = make_user(sync_client)
    user_b = make_user(sync_client)
    h_a = {"Authorization": f"Bearer {user_a['access_token']}"}
    h_b = {"Authorization": f"Bearer {user_b['access_token']}"}

    # User B adds to their watchlist
    r_b = sync_client.post(
        "/api/user/watchlist",
        json={"symbol": "BANKNIFTY", "strike": 54000, "option_type": "PE"},
        headers=h_b,
    )
    assert r_b.status_code == 200
    item_id_b = r_b.json()["id"]

    # User A attempts to delete User B's watchlist item
    del_attempt = sync_client.delete(f"/api/user/watchlist/{item_id_b}", headers=h_a)
    assert del_attempt.status_code == 404, "User A should not be able to delete User B's item"


def test_debate_vote_nonexistent_debate(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify voting on a non-existent debate returns HTTP 404 Not Found."""
    resp = sync_client.post(
        "/api/debates/999999/vote",
        json={"vote": "BULL", "confidence": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_debate_vote_invalid_vote_choice(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify voting with an unrecognized choice returns HTTP 400 Bad Request."""
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

    resp = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "INVALID_VOTE_CHOICE", "confidence": 5},
        headers=auth_headers,
    )
    assert resp.status_code in (400, 422)


def test_debate_vote_update_existing_vote(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify a user voting twice updates their vote rather than failing unique constraint."""
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

    # Vote 1: BULL
    r1 = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "BULL", "confidence": 7},
        headers=auth_headers,
    )
    assert r1.status_code == 200
    assert r1.json()["vote"] == "BULL"

    # Vote 2: Change to BEAR
    r2 = sync_client.post(
        f"/api/debates/{debate_id}/vote",
        json={"vote": "BEAR", "confidence": 9},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["vote"] == "BEAR"

    # Verify breakdown still counts only 1 vote
    stats = sync_client.get(f"/api/debates/{debate_id}/votes").json()
    assert stats["total_votes"] == 1
    assert stats["breakdown"]["BEAR"] == 1
    assert stats["breakdown"]["BULL"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# 3. Angel One Real-Time Boundaries (>=5 test cases)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_angel_quote_unknown_symbol():
    """Verify querying an unmapped/unknown symbol returns None without unhandled exception."""
    client = AngelClient("KEY", "CODE", "1234", "SECRET")
    with patch.object(client, "get_ltp", new_callable=AsyncMock) as mock_ltp:
        mock_ltp.return_value = None
        quote = await client.get_quote("UNMAPPED_SYMBOL_XYZ")
        assert quote is None


def test_angel_indicators_empty_candles():
    """Verify calculate_indicators on an empty list returns default zeroed indicators."""
    client = AngelClient("KEY", "CODE", "1234", "SECRET")
    ind = client.calculate_indicators([])
    assert ind["ema_20"] == 0.0
    assert ind["ema_50"] == 0.0
    assert ind["rsi"] == 50.0
    assert ind["vwap"] == 0.0
    assert ind["atr"] == 0.0


def test_angel_indicators_insufficient_candles():
    """Verify calculate_indicators on fewer candles than period (e.g. 5 candles) handles safely."""
    client = AngelClient("KEY", "CODE", "1234", "SECRET")
    small_candles = [
        {"time": 1700000000 + i * 300, "open": 25000.0, "high": 25020.0, "low": 24980.0, "close": 25010.0, "volume": 100}
        for i in range(5)
    ]
    ind = client.calculate_indicators(small_candles)
    assert ind["ema_20"] > 0
    assert 0 <= ind["rsi"] <= 100
    assert ind["vwap"] > 0


@pytest.mark.asyncio
async def test_angel_auth_invalid_credentials():
    """Verify authenticate returns False cleanly when bad credentials are used."""
    client = AngelClient(
        api_key="INVALID_KEY",
        client_code="INVALID_USER",
        pin="0000",
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    with patch.object(client, "get_http_client", new_callable=AsyncMock) as mock_get_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"status": False, "message": "Invalid Client Code or PIN"}
        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_http

        ok = await client.authenticate()
        assert ok is False


@pytest.mark.asyncio
async def test_angel_multi_quotes_empty_list():
    """Verify get_multi_quotes with empty list returns empty dict immediately."""
    client = AngelClient("KEY", "CODE", "1234", "SECRET")
    quotes = await client.get_multi_quotes([])
    assert quotes == {}


# ══════════════════════════════════════════════════════════════════════════════
# 4. Debate & Trade Guardrail Boundaries (>=5 test cases)
# ══════════════════════════════════════════════════════════════════════════════

def test_manual_trade_extreme_stop_loss_rejected(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify stop loss exceeding maximum risk limit is rejected with HTTP 400."""
    # NIFTY maximum allowable SL loss is Rs 700 (28 pts for 25 lot)
    req = {
        "symbol": "NIFTY",
        "strike": 25000,
        "option_type": "CE",
        "lots": 1,
        "entry_price": 200.0,
        "stop_loss": 50.0,  # 150 pts loss = 150 * 25 = 3750 > 700 max
        "target": 230.0,
    }
    resp = sync_client.post("/api/trades/manual", json=req, headers=auth_headers)
    # The application either clamps SL safely or rejects with 400
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        data = resp.json().get("trade", resp.json())
        # Clamped safely within Rs 700 limit
        assert (200.0 - data["stop_loss"]) * 25 <= 700.10


def test_manual_trade_extreme_target_rejected(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify profit target exceeding maximum target limit is rejected or clamped."""
    req = {
        "symbol": "NIFTY",
        "strike": 25000,
        "option_type": "CE",
        "lots": 1,
        "entry_price": 100.0,
        "stop_loss": 85.0,
        "target": 300.0,  # 200 pts profit = 200 * 25 = 5000 > 1000 max
    }
    resp = sync_client.post("/api/trades/manual", json=req, headers=auth_headers)
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        data = resp.json().get("trade", resp.json())
        # Clamped safely within Rs 1000 limit
        assert (data["target"] - 100.0) * 25 <= 1000.10


def test_manual_trade_lots_exceeding_1_rejected(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify attempting manual trade with more than 1 lot is rejected with HTTP 400."""
    req = {
        "symbol": "NIFTY",
        "strike": 25000,
        "option_type": "CE",
        "lots": 2,  # 2 lots > 1-lot limit
        "entry_price": 50.0,
    }
    resp = sync_client.post("/api/trades/manual", json=req, headers=auth_headers)
    assert resp.status_code == 400
    assert "1-lot" in resp.text.lower() or "only 1 lot" in resp.text.lower()


def test_manual_trade_outlay_exceeding_budget_rejected(sync_client: TestClient, auth_headers: Dict[str, str]):
    """Verify trade outlay exceeding Rs 5,000 budget is rejected with HTTP 400."""
    req = {
        "symbol": "NIFTY",
        "strike": 25000,
        "option_type": "CE",
        "lots": 1,
        "entry_price": 250.0,  # 250 * 25 = 6250 > 5000 budget
    }
    resp = sync_client.post("/api/trades/manual", json=req, headers=auth_headers)
    assert resp.status_code == 400
    assert "budget" in resp.text.lower() or "exceeds" in resp.text.lower()


def test_guardrails_validation_boundary_rejections():
    """Verify validate_paper_trade_guardrails raises appropriate errors for invalid inputs."""
    from config import validate_paper_trade_guardrails, BudgetExceededError

    # 1. Invalid index symbol
    with pytest.raises(ValueError, match="Invalid index symbol"):
        validate_paper_trade_guardrails(symbol="CRUDEOIL", entry_price=50.0)

    # 2. Non-positive entry price
    with pytest.raises(ValueError, match="Invalid option premium"):
        validate_paper_trade_guardrails(symbol="NIFTY", entry_price=-10.0)

    # 3. Stop loss above entry price
    with pytest.raises(ValueError, match="strictly below"):
        validate_paper_trade_guardrails(symbol="NIFTY", entry_price=100.0, stop_loss=110.0)

    # 4. Target below entry price
    with pytest.raises(ValueError, match="strictly above"):
        validate_paper_trade_guardrails(symbol="NIFTY", entry_price=100.0, target=90.0)


def test_trade_close_nonexistent_trade_id(sync_client: TestClient):
    """Verify closing a non-existent trade returns HTTP 404 Not Found."""
    resp = sync_client.patch("/api/trades/999999/close", json={"exit_price": 150.0})
    assert resp.status_code == 404


def test_debate_get_nonexistent_id(sync_client: TestClient):
    """Verify GET /api/debate/{id} for non-existent debate returns HTTP 404 Not Found."""
    resp = sync_client.get("/api/debate/999999")
    assert resp.status_code == 404
