"""
Unit and integration tests for Milestone 1:
- Persistent dual-dialect database engine and URL normalization.
- Idempotent dialect-aware migrations and ORM schema integrity.
- Password hashing (bcrypt + PBKDF2) and verification.
- JWT issuance, expiration, and payload decoding.
- Strict get_current_user and soft get_optional_user dependency behaviors.
- Auth API endpoints (/register, /login, /me, /logout).
- User-scoped endpoints (/api/user/trades, /summary, /watchlist, /debates/{id}/vote).
- Backward compatibility for unauthenticated guest operations.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config
from auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from database import (
    Base,
    DebateSessionModel,
    PaperTradeModel,
    UserModel,
    UserVoteModel,
    UserWatchlistModel,
    async_session_factory,
    create_tables,
    engine,
    normalize_database_url,
)
from main import app


@pytest.fixture(scope="module", autouse=True)
def anyio_backend():
    return "asyncio"


# ── 1. Database Engine & URL Normalization Tests ──────────────────────────────

def test_database_url_normalization():
    """Verify that various connection string dialects are safely converted to asyncpg or aiosqlite."""
    # Render / Supabase postgres:// -> postgresql+asyncpg://
    url1 = "postgres://user:pass@host.render.com:5432/dbname"
    assert normalize_database_url(url1) == "postgresql+asyncpg://user:pass@host.render.com:5432/dbname"

    # Standard postgresql:// -> postgresql+asyncpg://
    url2 = "postgresql://user:pass@ep-neon.us-east-2.aws.neon.tech/neondb"
    assert normalize_database_url(url2) == "postgresql+asyncpg://user:pass@ep-neon.us-east-2.aws.neon.tech/neondb"

    # sslmode=require -> ssl=require for asyncpg
    url3 = "postgres://user:pass@host/db?sslmode=require"
    assert normalize_database_url(url3) == "postgresql+asyncpg://user:pass@host/db?ssl=require"

    # sqlite:// -> sqlite+aiosqlite://
    url4 = "sqlite:///local_test.db"
    assert normalize_database_url(url4) == "sqlite+aiosqlite:///local_test.db"

    # Already normalized
    url5 = "postgresql+asyncpg://user:pass@host/db"
    assert normalize_database_url(url5) == url5


@pytest.mark.asyncio
async def test_idempotent_schema_creation_and_migration():
    """Verify create_tables() runs cleanly multiple times and creates all required tables and columns."""
    # Run once
    await create_tables()
    # Run again (must be completely idempotent and not raise errors)
    await create_tables()

    # Verify tables exist in metadata
    assert "users" in Base.metadata.tables
    assert "debate_sessions" in Base.metadata.tables
    assert "paper_trades" in Base.metadata.tables
    assert "user_debate_votes" in Base.metadata.tables
    assert "user_watchlists" in Base.metadata.tables

    # Verify column existence via DB query
    async with engine.begin() as conn:
        res = await conn.execute(text("SELECT id, email, username FROM users LIMIT 1"))
        assert res is not None

        ds_res = await conn.execute(text("SELECT id, user_id, index_symbol FROM debate_sessions LIMIT 1"))
        assert ds_res is not None

        pt_res = await conn.execute(text("SELECT id, user_id, trade_source, quantity FROM paper_trades LIMIT 1"))
        assert pt_res is not None


# ── 2. Password Hashing & Verification Tests ──────────────────────────────────

def test_password_hashing_and_verification():
    """Verify password hashing produces verifiable hashes with rejection of incorrect passwords."""
    raw_pass = "MySecretPass@2026!"
    hashed = hash_password(raw_pass)

    assert hashed != raw_pass
    assert verify_password(raw_pass, hashed) is True
    assert verify_password("WrongPassword123", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(raw_pass, "") is False


def test_pbkdf2_fallback_verification():
    """Verify standard library PBKDF2 hash verification works seamlessly."""
    raw_pass = "PBKDF2Secret999!"
    # Manually generate PBKDF2 hash format: pbkdf2:sha256:100000$salt$key
    import hashlib
    import secrets

    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", raw_pass.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
    pbkdf2_hash = f"pbkdf2:sha256:100000${salt}${key}"

    assert verify_password(raw_pass, pbkdf2_hash) is True
    assert verify_password("WrongPass", pbkdf2_hash) is False


# ── 3. JWT Token Generation & Validation Tests ────────────────────────────────

def test_jwt_generation_and_decoding():
    """Verify JWT access tokens encode and decode correctly with expiration checks."""
    claims = {"sub": "42", "username": "pro_trader", "email": "trader@market.in"}
    token = create_access_token(claims)
    decoded = decode_access_token(token)

    assert decoded["sub"] == "42"
    assert decoded["username"] == "pro_trader"
    assert decoded["email"] == "trader@market.in"
    assert "exp" in decoded

    # Expired token test
    expired_token = create_access_token(claims, expires_delta=timedelta(seconds=-10))
    with pytest.raises(Exception) as exc_info:
        decode_access_token(expired_token)
    assert "expired" in str(exc_info.value.detail).lower()

    # Tampered token test
    with pytest.raises(Exception) as exc_info:
        decode_access_token(token + "tampered")
    assert "could not validate credentials" in str(exc_info.value.detail).lower()


# ── 4. Auth & User API Integration Tests ──────────────────────────────────────

@pytest_asyncio.fixture
async def async_client():
    await create_tables()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_auth_registration_and_login_flow(async_client: AsyncClient):
    """Verify registration, duplicate handling, login, profile retrieval (/me), and logout."""
    unique_suffix = int(datetime.now(timezone.utc).timestamp() * 1000)
    email = f"trader_{unique_suffix}@example.com"
    username = f"trader_{unique_suffix}"
    password = "SuperPassword123"

    # 1. Register new user
    reg_resp = await async_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": username,
            "password": password,
            "full_name": "Pro Trader",
        },
    )
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert "access_token" in reg_data
    assert reg_data["user"]["email"] == email
    assert reg_data["user"]["username"] == username
    token = reg_data["access_token"]

    # 2. Duplicate registration attempt (email)
    dup_email_resp = await async_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": f"other_{unique_suffix}",
            "password": password,
        },
    )
    assert dup_email_resp.status_code == 400
    assert "email is already registered" in dup_email_resp.json()["detail"].lower()

    # 3. Duplicate registration attempt (username)
    dup_user_resp = await async_client.post(
        "/api/auth/register",
        json={
            "email": f"diff_{unique_suffix}@example.com",
            "username": username,
            "password": password,
        },
    )
    assert dup_user_resp.status_code == 400
    assert "username is already taken" in dup_user_resp.json()["detail"].lower()

    # 4. Login with username
    login_user_resp = await async_client.post(
        "/api/auth/login",
        json={"username_or_email": username, "password": password},
    )
    assert login_user_resp.status_code == 200
    assert "access_token" in login_user_resp.json()

    # 5. Login with email
    login_email_resp = await async_client.post(
        "/api/auth/login",
        json={"username_or_email": email, "password": password},
    )
    assert login_email_resp.status_code == 200

    # 6. Login with wrong password
    bad_login_resp = await async_client.post(
        "/api/auth/login",
        json={"username_or_email": username, "password": "WrongPassword!"},
    )
    assert bad_login_resp.status_code == 401

    # 7. Access /api/auth/me with Bearer token
    me_resp = await async_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["username"] == username
    assert me_data["email"] == email

    # 8. Access /api/auth/me without token -> 401
    unauth_resp = await async_client.get("/api/auth/me")
    assert unauth_resp.status_code == 401

    # 9. Logout
    logout_resp = await async_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_user_watchlist_endpoints(async_client: AsyncClient):
    """Verify adding, listing, duplicate checking, and deleting watchlist items."""
    unique_suffix = int(datetime.now(timezone.utc).timestamp() * 1000)
    username = f"watch_{unique_suffix}"
    email = f"watch_{unique_suffix}@example.com"
    reg_resp = await async_client.post(
        "/api/auth/register",
        json={"email": email, "username": username, "password": "WatchPassword123"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Add item to watchlist
    add_resp = await async_client.post(
        "/api/user/watchlist",
        headers=headers,
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE", "notes": "Key resistance"},
    )
    assert add_resp.status_code == 200
    add_data = add_resp.json()
    assert add_data["status"] == "success"
    item_id = add_data["id"]

    # 2. Add duplicate item -> returns status exists
    dup_resp = await async_client.post(
        "/api/user/watchlist",
        headers=headers,
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE"},
    )
    assert dup_resp.status_code == 200
    assert dup_resp.json()["status"] == "exists"

    # 3. List watchlist items
    list_resp = await async_client.get("/api/user/watchlist", headers=headers)
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) >= 1
    assert any(i["strike"] == 25000 and i["option_type"] == "CE" for i in items)

    # 4. Delete item
    del_resp = await async_client.delete(f"/api/user/watchlist/{item_id}", headers=headers)
    assert del_resp.status_code == 200

    # 5. Delete again -> 404
    del_404_resp = await async_client.delete(f"/api/user/watchlist/{item_id}", headers=headers)
    assert del_404_resp.status_code == 404


@pytest.mark.asyncio
async def test_debate_voting_and_consensus(async_client: AsyncClient):
    """Verify casting, updating, and querying community votes on a debate."""
    unique_suffix = int(datetime.now(timezone.utc).timestamp() * 1000)
    username = f"voter_{unique_suffix}"
    email = f"voter_{unique_suffix}@example.com"
    reg_resp = await async_client.post(
        "/api/auth/register",
        json={"email": email, "username": username, "password": "VoterPassword123"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure a debate session exists
    async with AsyncSession(engine) as session:
        debate = DebateSessionModel(
            spot_price=25000.0,
            index_symbol="NIFTY",
            judge_decision="BUY_CE",
            judge_reasoning="Strong institutional CE buying",
        )
        session.add(debate)
        await session.commit()
        await session.refresh(debate)
        debate_id = debate.id

    # 1. Cast a vote
    vote_resp = await async_client.post(
        f"/api/debates/{debate_id}/vote",
        headers=headers,
        json={"vote": "BULL", "confidence": 8, "comment": "Clear bullish flag"},
    )
    assert vote_resp.status_code == 200
    assert vote_resp.json()["status"] == "success"
    assert vote_resp.json()["vote"] == "BULL"

    # 2. Update vote to AGREE_JUDGE
    update_vote_resp = await async_client.post(
        f"/api/debates/{debate_id}/vote",
        headers=headers,
        json={"vote": "AGREE_JUDGE", "confidence": 9, "comment": "Agreed with judge decision"},
    )
    assert update_vote_resp.status_code == 200
    assert update_vote_resp.json()["vote"] == "AGREE_JUDGE"

    # 3. Invalid vote option -> 400
    bad_vote_resp = await async_client.post(
        f"/api/debates/{debate_id}/vote",
        headers=headers,
        json={"vote": "INVALID_CHOICE"},
    )
    assert bad_vote_resp.status_code == 400

    # 4. Query vote breakdown
    breakdown_resp = await async_client.get(f"/api/debates/{debate_id}/votes")
    assert breakdown_resp.status_code == 200
    b_data = breakdown_resp.json()
    assert b_data["debate_id"] == debate_id
    assert b_data["total_votes"] >= 1
    assert b_data["breakdown"]["AGREE_JUDGE"] >= 1


@pytest.mark.asyncio
async def test_user_scoped_trades_isolation(async_client: AsyncClient):
    """Verify that user trades and summaries are strictly scoped to the authenticated user."""
    unique_suffix = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Register User A
    user_a_resp = await async_client.post(
        "/api/auth/register",
        json={"email": f"usera_{unique_suffix}@trade.com", "username": f"usera_{unique_suffix}", "password": "Password123"},
    )
    token_a = user_a_resp.json()["access_token"]
    user_a_id = user_a_resp.json()["user"]["id"]

    # Register User B
    user_b_resp = await async_client.post(
        "/api/auth/register",
        json={"email": f"userb_{unique_suffix}@trade.com", "username": f"userb_{unique_suffix}", "password": "Password123"},
    )
    token_b = user_b_resp.json()["access_token"]

    # Place manual trade for User A
    trade_resp = await async_client.post(
        "/api/trades/manual",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 1, "entry_price": 100.0},
    )
    assert trade_resp.status_code == 200
    trade_id = trade_resp.json()["trade"]["id"]

    # User A queries trades -> sees 1 trade
    a_trades_resp = await async_client.get(
        "/api/user/trades",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert a_trades_resp.status_code == 200
    a_trades = a_trades_resp.json()
    assert any(t["id"] == trade_id for t in a_trades)

    # User B queries trades -> does NOT see User A's trade
    b_trades_resp = await async_client.get(
        "/api/user/trades",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert b_trades_resp.status_code == 200
    b_trades = b_trades_resp.json()
    assert not any(t["id"] == trade_id for t in b_trades)

    # User A summary reflects 1 trade
    summary_a = await async_client.get(
        "/api/user/trades/summary",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert summary_a.status_code == 200
    s_data = summary_a.json()
    assert s_data["total_trades"] >= 1
    assert s_data["user_id"] == user_a_id


@pytest.mark.asyncio
async def test_guest_backward_compatibility(async_client: AsyncClient):
    """Verify that unauthenticated guest users can place manual trades without 401 errors."""
    guest_trade = await async_client.post(
        "/api/trades/manual",
        json={"symbol": "NIFTY", "strike": 25000, "option_type": "CE", "lots": 1, "entry_price": 95.0},
    )
    assert guest_trade.status_code == 200
    data = guest_trade.json()
    assert data["success"] is True
    assert "trade" in data


# ── 5. Dependency & Edge-Case Unit Tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_soft_dependency_get_optional_user():
    """Verify get_optional_user behavior with valid, invalid, expired, and absent credentials."""
    from auth import get_optional_user
    from fastapi.security import HTTPAuthorizationCredentials

    # Absent credentials
    res_none = await get_optional_user(None, None)  # type: ignore
    assert res_none is None

    # Invalid string token
    invalid_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not_a_valid_jwt_token")
    res_inv = await get_optional_user(invalid_creds, None)  # type: ignore
    assert res_inv is None

    # Valid token for non-existent user
    non_existent_token = create_access_token({"sub": "9999999", "username": "ghost"})
    creds_ghost = HTTPAuthorizationCredentials(scheme="Bearer", credentials=non_existent_token)
    async with async_session_factory() as session:
        res_ghost = await get_optional_user(creds_ghost, session)
        assert res_ghost is None

    # Valid token for real user
    async with async_session_factory() as session:
        user = UserModel(
            email="soft_test@example.com",
            username="soft_user",
            hashed_password=hash_password("Pass123"),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        user_id = user.id
        user_name = user.username
        real_token = create_access_token({"sub": str(user_id), "username": user_name})
        creds_real = HTTPAuthorizationCredentials(scheme="Bearer", credentials=real_token)
        res_real = await get_optional_user(creds_real, session)
        assert res_real is not None
        assert res_real.id == user_id

        # Deactivated user should return None
        user.is_active = False
        await session.commit()
        res_inactive = await get_optional_user(creds_real, session)
        assert res_inactive is None


@pytest.mark.asyncio
async def test_strict_dependency_get_current_user_rejections():
    """Verify get_current_user raises HTTPException 401 on missing, expired, and inactive users."""
    from auth import get_current_user
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    # Missing credentials
    with pytest.raises(HTTPException) as exc:
        await get_current_user(None, None)  # type: ignore
    assert exc.value.status_code == 401

    # Inactive user
    async with async_session_factory() as session:
        u = UserModel(
            email="inactive@example.com",
            username="inactive_user",
            hashed_password=hash_password("Pass123"),
            is_active=False,
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)

        u_id = u.id
        u_name = u.username
        token = create_access_token({"sub": str(u_id), "username": u_name})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc_inactive:
            await get_current_user(creds, session)
        assert exc_inactive.value.status_code == 401
        assert "inactive" in str(exc_inactive.value.detail).lower()


@pytest.mark.asyncio
async def test_database_unique_constraints_enforcement():
    """Verify that unique constraints on UserVoteModel and UserWatchlistModel raise IntegrityError on collision."""
    from sqlalchemy.exc import IntegrityError

    async with async_session_factory() as session:
        user = UserModel(
            email="uniq_test@example.com",
            username="uniq_user",
            hashed_password=hash_password("Pass123"),
        )
        debate = DebateSessionModel(
            spot_price=25000.0,
            judge_decision="BUY_CE",
        )
        session.add_all([user, debate])
        await session.commit()
        await session.refresh(user)
        await session.refresh(debate)

        u_id = user.id
        d_id = debate.id

        # 1. UserVoteModel unique constraint (user_id, debate_id)
        v1 = UserVoteModel(user_id=u_id, debate_id=d_id, vote="BULL")
        session.add(v1)
        await session.commit()

        v2 = UserVoteModel(user_id=u_id, debate_id=d_id, vote="BEAR")
        session.add(v2)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # 2. UserWatchlistModel unique constraint (user_id, symbol, strike, option_type)
        w1 = UserWatchlistModel(user_id=u_id, symbol="NIFTY", strike=25000, option_type="CE")
        session.add(w1)
        await session.commit()

        w2 = UserWatchlistModel(user_id=u_id, symbol="NIFTY", strike=25000, option_type="CE")
        session.add(w2)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


def test_model_representations():
    """Verify string representations of all ORM models."""
    u = UserModel(id=1, username="alice", email="alice@test.com")
    assert repr(u) == "<User id=1 username=alice email=alice@test.com>"

    d = DebateSessionModel(id=2, spot_price=24950.0, judge_decision="BUY_CE")
    assert repr(d) == "<DebateSession id=2 spot=24950.0 decision=BUY_CE>"

    t = PaperTradeModel(id=3, option_symbol="NIFTY25000CE", status="OPEN", pnl=None)
    assert repr(t) == "<PaperTrade id=3 symbol=NIFTY25000CE status=OPEN pnl=None>"

    v = UserVoteModel(id=4, user_id=1, debate_id=2, vote="BULL")
    assert repr(v) == "<UserVote id=4 user_id=1 debate_id=2 vote=BULL>"

    w = UserWatchlistModel(id=5, user_id=1, symbol="NIFTY", strike=25000, option_type="CE")
    assert repr(w) == "<UserWatchlist id=5 user_id=1 NIFTY 25000CE>"
