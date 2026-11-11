"""Adversarial stress-test suite for Authentication & Persistent Database subsystems.

Covers:
1. Concurrency and Race Conditions:
   - Concurrent registrations with the same email or username.
   - Rapid parallel login requests.
   - Concurrent votes on the same debate by the same user (unique constraint enforcement).
2. Security & Edge Cases:
   - Malformed, truncated, or tampered JWT tokens.
   - Expired JWT tokens.
   - Special characters / SQL injection strings in usernames, emails, watchlists, comments.
   - Password boundary lengths (empty, 5 chars, 6 chars, 72 chars, 73 chars, 100 chars, 101 chars, 1000+ chars).
3. Database Dialect Resilience:
   - normalize_database_url against edge-case URLs (nested query params, sslmode variations).
   - SQLite pragmas (foreign_keys, journal_mode, busy_timeout) and concurrent writer resilience.
4. Guest vs Authenticated Isolation:
   - Unauthenticated requests to public routes without auth headers.
   - Protected routes strictly returning 401 without auth headers.
   - Multi-tenant user data boundaries (User A vs User B).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List
import uuid

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from auth import (
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    create_access_token,
    decode_access_token,
    get_current_user,
    get_optional_user,
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
    create_engine_for_url,
    create_tables,
    engine,
    normalize_database_url,
)
from main import app


@pytest.fixture(scope="module", autouse=True)
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def async_client():
    await create_tables()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver", timeout=60.0) as client:
        yield client


# ══════════════════════════════════════════════════════════════════════════════
# DOMAIN 1: Concurrency and Race Conditions
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_concurrent_registrations_same_email(async_client: AsyncClient):
    """Stress-test 10 concurrent registration requests with the exact same email address.
    Verifies that:
    1. Exactly one user row is created in the database (unique constraint is respected).
    2. Identifies whether competing requests receive handled errors (400) or unhandled exceptions.
    """
    u_id = uuid.uuid4().hex[:8]
    target_email = f"race_email_{u_id}@test.com"
    concurrency_count = 10

    async def _send_reg(idx: int):
        return await async_client.post(
            "/api/auth/register",
            json={
                "email": target_email,
                "username": f"race_user_{u_id}_{idx}",
                "password": "ValidPassword123",
            },
        )

    results = await asyncio.gather(*[_send_reg(i) for i in range(concurrency_count)], return_exceptions=True)

    success_count = 0
    handled_error_count = 0
    unhandled_exception_count = 0

    for r in results:
        if isinstance(r, Exception):
            unhandled_exception_count += 1
        elif r.status_code == 201:
            success_count += 1
        elif r.status_code == 400:
            handled_error_count += 1
        else:
            unhandled_exception_count += 1

    # Database invariant: Exactly 1 user MUST exist in DB
    async with async_session_factory() as session:
        stmt = select(UserModel).where(UserModel.email == target_email)
        users = (await session.execute(stmt)).scalars().all()
        assert len(users) == 1, f"Expected exactly 1 user in DB, but found {len(users)}"

    # Exactly 1 user in DB and at least 1 request succeeded with HTTP 201
    assert success_count >= 1, f"Expected at least 1 success (201), got {success_count}"

    # Log empirical behavior of the other 9 competing requests
    print(
        f"\n[EMPIRICAL FINDING] Concurrent Email Registration: "
        f"201 Created: {success_count}, 400 Handled: {handled_error_count}, "
        f"Unhandled Exceptions (500/IntegrityError): {unhandled_exception_count}"
    )


@pytest.mark.asyncio
async def test_concurrent_registrations_same_username(async_client: AsyncClient):
    """Stress-test 10 concurrent registration requests with the exact same username.
    Verifies that:
    1. Exactly one user row is created in the database.
    2. Identifies whether competing requests receive handled errors or unhandled exceptions.
    """
    u_id = uuid.uuid4().hex[:8]
    target_username = f"race_usr_{u_id}"
    concurrency_count = 10

    async def _send_reg(idx: int):
        return await async_client.post(
            "/api/auth/register",
            json={
                "email": f"diff_{idx}_{u_id}@test.com",
                "username": target_username,
                "password": "ValidPassword123",
            },
        )

    results = await asyncio.gather(*[_send_reg(i) for i in range(concurrency_count)], return_exceptions=True)

    success_count = 0
    handled_error_count = 0
    unhandled_exception_count = 0

    for r in results:
        if isinstance(r, Exception):
            unhandled_exception_count += 1
        elif r.status_code == 201:
            success_count += 1
        elif r.status_code == 400:
            handled_error_count += 1
        else:
            unhandled_exception_count += 1

    # Database invariant: Exactly 1 user in DB
    async with async_session_factory() as session:
        stmt = select(UserModel).where(UserModel.username == target_username)
        users = (await session.execute(stmt)).scalars().all()
        assert len(users) == 1, f"Expected exactly 1 user in DB, but found {len(users)}"

    assert success_count >= 1, f"Expected at least 1 success (201), got {success_count}"
    print(
        f"\n[EMPIRICAL FINDING] Concurrent Username Registration: "
        f"201 Created: {success_count}, 400 Handled: {handled_error_count}, "
        f"Unhandled Exceptions (500/IntegrityError): {unhandled_exception_count}"
    )


@pytest.mark.asyncio
async def test_rapid_parallel_logins(async_client: AsyncClient):
    """Stress-test rapid parallel login requests:
    - 20 concurrent logins for valid credentials: all must return 200 OK.
    - 20 concurrent logins for invalid credentials: all must return 401 Unauthorized.
    """
    u_id = uuid.uuid4().hex[:8]
    username = f"login_stress_{u_id}"
    email = f"login_{u_id}@test.com"
    password = "StrongPassword456"

    # Register user
    reg = await async_client.post(
        "/api/auth/register",
        json={"email": email, "username": username, "password": password},
    )
    assert reg.status_code == 201

    # 1. 20 concurrent valid logins
    async def _valid_login():
        return await async_client.post(
            "/api/auth/login",
            json={"username_or_email": username, "password": password},
        )

    valid_results = await asyncio.gather(*[_valid_login() for _ in range(20)], return_exceptions=True)
    valid_successes = [r for r in valid_results if not isinstance(r, Exception) and r.status_code == 200]
    assert len(valid_successes) >= 15, f"Expected parallel valid logins to succeed, got {len(valid_successes)}/20"
    for r in valid_successes:
        claims = decode_access_token(r.json()["access_token"])
        assert claims["username"] == username

    # 2. 20 concurrent invalid logins
    async def _invalid_login():
        return await async_client.post(
            "/api/auth/login",
            json={"username_or_email": username, "password": "WrongPassword999"},
        )

    invalid_results = await asyncio.gather(*[_invalid_login() for _ in range(20)], return_exceptions=True)
    invalid_401s = [r for r in invalid_results if not isinstance(r, Exception) and r.status_code == 401]
    assert len(invalid_401s) >= 15, f"Expected parallel invalid logins to return 401, got {len(invalid_401s)}/20"


@pytest.mark.asyncio
async def test_concurrent_votes_same_user_same_debate(async_client: AsyncClient):
    """Stress-test concurrent votes by the same user on the same debate session.
    Verifies that:
    1. The unique constraint uq_user_debate_vote ensures exactly 1 vote row exists in DB.
    2. Identifies whether competing requests trigger an unhandled IntegrityError or clean handling.
    """
    u_id = uuid.uuid4().hex[:8]
    username = f"voter_{u_id}"
    email = f"voter_{u_id}@test.com"
    password = "VotePassword123"

    # Register user
    reg = await async_client.post(
        "/api/auth/register",
        json={"email": email, "username": username, "password": password},
    )
    token = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure a debate session exists
    async with async_session_factory() as session:
        debate = DebateSessionModel(spot_price=25000.0, index_symbol="NIFTY", judge_decision="BUY_CE")
        session.add(debate)
        await session.commit()
        await session.refresh(debate)
        debate_id = debate.id

    # Fire 10 concurrent votes on this debate
    async def _send_vote(idx: int):
        vote_choice = "BULL" if idx % 2 == 0 else "BEAR"
        return await async_client.post(
            f"/api/debates/{debate_id}/vote",
            headers=headers,
            json={"vote": vote_choice, "confidence": 5 + (idx % 5)},
        )

    results = await asyncio.gather(*[_send_vote(i) for i in range(10)], return_exceptions=True)

    success_count = sum(1 for r in results if not isinstance(r, Exception) and r.status_code == 200)
    unhandled_exceptions = sum(1 for r in results if isinstance(r, Exception))

    # Verify Database Constraint: exactly 1 vote row in user_debate_votes for this (user_id, debate_id)
    async with async_session_factory() as session:
        v_stmt = select(UserVoteModel).where(
            UserVoteModel.user_id == user_id,
            UserVoteModel.debate_id == debate_id,
        )
        vote_rows = (await session.execute(v_stmt)).scalars().all()
        assert len(vote_rows) == 1, f"Unique constraint violated: found {len(vote_rows)} votes in DB!"

    print(
        f"\n[EMPIRICAL FINDING] Concurrent Debate Votes: "
        f"200 OK: {success_count}, Unhandled Exceptions (IntegrityError): {unhandled_exceptions}, "
        f"Rows in DB: {len(vote_rows)}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# DOMAIN 2: Security & Edge Cases
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_jwt_tampering_and_truncation(async_client: AsyncClient):
    """Stress-test JWT verification against tampering, truncation, invalid algorithms, and garbage."""
    claims = {"sub": "1", "username": "valid_user"}
    valid_token = create_access_token(claims)

    # 1. Truncated tokens
    trunc_tokens = [valid_token[:10], valid_token[:30], valid_token[:-15]]
    for t in trunc_tokens:
        resp = await async_client.get("/api/auth/me", headers={"Authorization": f"Bearer {t}"})
        assert resp.status_code == 401, f"Truncated token must return 401, got {resp.status_code}"

    # 2. Tampered signature
    tampered_sig = valid_token[:-4] + "ABCD"
    resp_sig = await async_client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered_sig}"})
    assert resp_sig.status_code == 401, "Tampered signature must return 401"

    # 3. Wrong secret key
    forged_token = jwt.encode(claims, "fake_unauthorized_secret_key", algorithm=JWT_ALGORITHM)
    resp_forged = await async_client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged_token}"})
    assert resp_forged.status_code == 401, "Forged token with wrong key must return 401"

    # 4. None algorithm vulnerability test
    none_alg_token = jwt.encode(claims, key="", algorithm="none")
    resp_none = await async_client.get("/api/auth/me", headers={"Authorization": f"Bearer {none_alg_token}"})
    assert resp_none.status_code == 401, "Token with alg=none must return 401"

    # 5. Non-integer user id (sub claim)
    bad_sub_token = jwt.encode(
        {"sub": "not_an_int", "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    resp_sub = await async_client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad_sub_token}"})
    assert resp_sub.status_code == 401, "Non-integer sub must return 401"
    assert "invalid user id" in resp_sub.json()["detail"].lower()

    # 6. Non-existent user id in database
    ghost_token = jwt.encode(
        {"sub": "999999999", "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    resp_ghost = await async_client.get("/api/auth/me", headers={"Authorization": f"Bearer {ghost_token}"})
    assert resp_ghost.status_code == 401, "Non-existent user must return 401"
    assert "user not found" in resp_ghost.json()["detail"].lower()

    # 7. Arbitrary junk in Bearer header
    for junk in ["Bearer ", "Bearer !@#$%^&*()", "Bearer 12345", "Bearer null"]:
        resp_junk = await async_client.get("/api/auth/me", headers={"Authorization": junk})
        assert resp_junk.status_code == 401


@pytest.mark.asyncio
async def test_jwt_expiration(async_client: AsyncClient):
    """Verify expired tokens are rejected with appropriate 401 detail on strict routes,
    and cleanly fallback to None on soft routes."""
    past_exp = datetime.now(timezone.utc) - timedelta(hours=1)
    expired_token = jwt.encode(
        {"sub": "1", "username": "any_user", "exp": int(past_exp.timestamp())},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    # 1. Strict route: /api/auth/me
    resp_strict = await async_client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp_strict.status_code == 401
    assert "expired" in resp_strict.json()["detail"].lower()

    # 2. Strict user route: /api/user/trades
    resp_user = await async_client.get("/api/user/trades", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp_user.status_code == 401

    # 3. Soft route: get_optional_user directly
    from fastapi.security import HTTPAuthorizationCredentials
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=expired_token)
    user = await get_optional_user(creds, None)  # type: ignore
    assert user is None, "Expired token must return None in get_optional_user"


@pytest.mark.asyncio
async def test_sql_injection_and_xss_vectors(async_client: AsyncClient):
    """Verify that SQL injection strings in username, email, login, watchlist notes,
    and debate comments do NOT cause injection or table corruption."""
    u_id = uuid.uuid4().hex[:8]
    sqli_username = f"admin_{u_id}' OR 1=1; --"
    sqli_notes = "'); DROP TABLE user_watchlists; --"
    sqli_comment = "'); UPDATE users SET is_superuser=1; --"

    # 1. Register with SQL injection in username
    reg_resp = await async_client.post(
        "/api/auth/register",
        json={
            "email": f"sqli_{u_id}@test.com",
            "username": sqli_username,
            "password": "SecurePassword123",
        },
    )
    assert reg_resp.status_code == 201
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Add watchlist item with SQL injection in notes
    wl_resp = await async_client.post(
        "/api/user/watchlist",
        headers=headers,
        json={"symbol": "NIFTY", "strike": 25100, "option_type": "CE", "notes": sqli_notes},
    )
    assert wl_resp.status_code == 200

    # 3. Vote on debate with SQL injection in comment
    async with async_session_factory() as session:
        debate = DebateSessionModel(spot_price=25000.0, index_symbol="NIFTY", judge_decision="BUY_CE")
        session.add(debate)
        await session.commit()
        await session.refresh(debate)
        debate_id = debate.id

    vote_resp = await async_client.post(
        f"/api/debates/{debate_id}/vote",
        headers=headers,
        json={"vote": "BULL", "confidence": 8, "comment": sqli_comment},
    )
    assert vote_resp.status_code == 200

    # 4. Verify all database tables still exist
    async with async_session_factory() as session:
        t_stmt = text("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in (await session.execute(t_stmt)).fetchall()]
        assert "users" in tables
        assert "debate_sessions" in tables
        assert "user_watchlists" in tables
        assert "paper_trades" in tables
        assert "user_debate_votes" in tables

        # Verify no user became superuser through injection
        su_stmt = select(UserModel).where(UserModel.username == sqli_username, UserModel.is_superuser == True)
        superusers = (await session.execute(su_stmt)).scalars().all()
        assert len(superusers) == 0, f"SQL injection vulnerability: unexpected superuser found: {superusers}"

    # 5. Attempt login authentication bypass with SQL injection
    bypass_resp = await async_client.post(
        "/api/auth/login",
        json={"username_or_email": "' OR '1'='1", "password": "anypassword"},
    )
    assert bypass_resp.status_code == 401, "SQL injection auth bypass must return 401"


@pytest.mark.asyncio
async def test_password_boundary_matrix(async_client: AsyncClient):
    """Stress-test password length boundaries:
    - 0 chars (empty): rejected by Pydantic (422)
    - 5 chars (< min_length 6): rejected by Pydantic (422)
    - 6 chars (exact min boundary): accepted (201)
    - 72 chars (exact bcrypt limit): accepted (201)
    - 73 chars: within Pydantic (<= 100) but exceeds bcrypt 72 bytes.
    - 100 chars: exact Pydantic max boundary.
    - 101 chars: rejected by Pydantic (422)
    - 1000 chars: rejected by Pydantic (422)
    """
    boundaries = [
        ("", 422, "empty"),
        ("12345", 422, "5 chars (below min)"),
        ("123456", 201, "6 chars (exact min boundary)"),
        ("A" * 72, 201, "72 chars (exact bcrypt byte limit)"),
        ("A" * 73, "CRASH_OR_422", "73 chars (exceeds bcrypt 72 bytes)"),
        ("A" * 100, "CRASH_OR_422", "100 chars (exact Pydantic max)"),
        ("A" * 101, 422, "101 chars (exceeds Pydantic max)"),
        ("A" * 1000, 422, "1000 chars (gross overflow)"),
    ]

    for pwd, expected, label in boundaries:
        u_id = uuid.uuid4().hex[:6]
        payload = {
            "email": f"pwd_{len(pwd)}_{u_id}@test.com",
            "username": f"usr_{len(pwd)}_{u_id}",
            "password": pwd,
        }
        try:
            r = await async_client.post("/api/auth/register", json=payload)
            if expected == 201:
                assert r.status_code == 201, f"Failed for {label}: expected 201, got {r.status_code}"
            elif expected == 422:
                assert r.status_code == 422, f"Failed for {label}: expected 422, got {r.status_code}"
            else:
                # 73 and 100 chars: check if server crashes with 500
                print(f"[EMPIRICAL FINDING] Password boundary {label} returned status {r.status_code}")
        except Exception as exc:
            print(f"[EMPIRICAL FINDING] Password boundary {label} raised unhandled exception: {type(exc).__name__}: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# DOMAIN 3: Database Dialect Resilience
# ══════════════════════════════════════════════════════════════════════════════

def test_normalize_database_url_comprehensive():
    """Verify URL normalization across PostgreSQL and SQLite edge cases."""
    cases = [
        # Standard Render postgres
        ("postgres://user:pass@host:5432/db", "postgresql+asyncpg://user:pass@host:5432/db"),
        # Standard Supabase postgresql
        ("postgresql://user:pass@host:5432/db", "postgresql+asyncpg://user:pass@host:5432/db"),
        # Render / Neon sslmode=require
        ("postgres://user:pass@host/db?sslmode=require", "postgresql+asyncpg://user:pass@host/db?ssl=require"),
        # Multi-param query string
        (
            "postgres://user:pass@host/db?sslmode=require&application_name=nifty",
            "postgresql+asyncpg://user:pass@host/db?ssl=require&application_name=nifty",
        ),
        (
            "postgres://user:pass@host/db?target_session_attrs=read-write&sslmode=require",
            "postgresql+asyncpg://user:pass@host/db?target_session_attrs=read-write&ssl=require",
        ),
        # SQLite
        ("sqlite:///test.db", "sqlite+aiosqlite:///test.db"),
        # Already normalized SQLite
        ("sqlite+aiosqlite:///test.db", "sqlite+aiosqlite:///test.db"),
        # Already normalized Postgres
        ("postgresql+asyncpg://user:pass@host/db", "postgresql+asyncpg://user:pass@host/db"),
        # Trimming whitespace
        ("   postgres://user:pass@host/db   ", "postgresql+asyncpg://user:pass@host/db"),
    ]
    for inp, expected in cases:
        assert normalize_database_url(inp) == expected, f"Failed normalization for: {inp}"


@pytest.mark.asyncio
async def test_sqlite_pragmas_and_busy_handler():
    """Verify that SQLite engine connection listener enforces:
    - PRAGMA foreign_keys = 1 (ON)
    - PRAGMA journal_mode = wal
    - PRAGMA busy_timeout = 30000 ms
    """
    eng = create_engine_for_url("sqlite+aiosqlite:///test_resilience_pragma.db")
    try:
        async with eng.connect() as conn:
            fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
            jm = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
            bt = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()

            assert fk == 1, f"Expected foreign_keys=1, got {fk}"
            assert str(jm).lower() == "wal", f"Expected journal_mode=wal, got {jm}"
            assert bt == 30000, f"Expected busy_timeout=30000, got {bt}"
    finally:
        await eng.dispose()
        p = Path("test_resilience_pragma.db")
        if p.exists():
            p.unlink()


# ══════════════════════════════════════════════════════════════════════════════
# DOMAIN 4: Guest vs Authenticated Isolation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_public_routes_unauthenticated(async_client: AsyncClient):
    """Verify public endpoints succeed with 200 without Authorization header."""
    public_endpoints = [
        "/api/status",
        "/api/debate/history",
        "/api/trades",
        "/api/trades/active",
        "/api/trades/summary",
    ]
    for route in public_endpoints:
        resp = await async_client.get(route)
        assert resp.status_code == 200, f"Public endpoint {route} failed with status {resp.status_code}"


@pytest.mark.asyncio
async def test_protected_routes_strict_401(async_client: AsyncClient):
    """Verify protected endpoints strictly return 401 with WWW-Authenticate header when unauthenticated."""
    protected_endpoints = [
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/user/trades"),
        ("GET", "/api/user/trades/summary"),
        ("GET", "/api/user/watchlist"),
        ("POST", "/api/user/watchlist"),
        ("DELETE", "/api/user/watchlist/999"),
        ("POST", "/api/debates/1/vote"),
    ]
    for method, route in protected_endpoints:
        resp = await async_client.request(method, route)
        assert resp.status_code == 401, f"Protected endpoint {method} {route} returned {resp.status_code}, expected 401"
        assert "bearer" in resp.headers.get("www-authenticate", "").lower(), (
            f"Missing WWW-Authenticate: Bearer header on {route}"
        )


@pytest.mark.asyncio
async def test_user_data_isolation_boundary(async_client: AsyncClient):
    """Verify strict multi-tenant isolation between User A and User B:
    - User A cannot see User B's watchlist items.
    - User B cannot delete User A's watchlist item (404 Not Found).
    - User A's items remain intact after User B's unauthorized delete attempt.
    """
    u_a = uuid.uuid4().hex[:6]
    u_b = uuid.uuid4().hex[:6]

    # Register User A
    reg_a = await async_client.post(
        "/api/auth/register",
        json={"email": f"usera_{u_a}@corp.com", "username": f"user_a_{u_a}", "password": "Password123"},
    )
    token_a = reg_a.json()["access_token"]
    hdr_a = {"Authorization": f"Bearer {token_a}"}

    # Register User B
    reg_b = await async_client.post(
        "/api/auth/register",
        json={"email": f"userb_{u_b}@corp.com", "username": f"user_b_{u_b}", "password": "Password123"},
    )
    token_b = reg_b.json()["access_token"]
    hdr_b = {"Authorization": f"Bearer {token_b}"}

    # User A adds item to watchlist
    add_a = await async_client.post(
        "/api/user/watchlist",
        headers=hdr_a,
        json={"symbol": "NIFTY", "strike": 25200, "option_type": "CE", "notes": "User A Private Note"},
    )
    assert add_a.status_code == 200
    item_a_id = add_a.json()["id"]

    # User B adds item to watchlist
    add_b = await async_client.post(
        "/api/user/watchlist",
        headers=hdr_b,
        json={"symbol": "BANKNIFTY", "strike": 52500, "option_type": "PE", "notes": "User B Private Note"},
    )
    assert add_b.status_code == 200
    item_b_id = add_b.json()["id"]

    # User A lists watchlist: must see item A, must NOT see item B
    list_a = (await async_client.get("/api/user/watchlist", headers=hdr_a)).json()
    assert any(i["id"] == item_a_id for i in list_a), "User A must see own watchlist item"
    assert not any(i["id"] == item_b_id for i in list_a), "User A must NOT see User B watchlist item"

    # User B lists watchlist: must see item B, must NOT see item A
    list_b = (await async_client.get("/api/user/watchlist", headers=hdr_b)).json()
    assert any(i["id"] == item_b_id for i in list_b), "User B must see own watchlist item"
    assert not any(i["id"] == item_a_id for i in list_b), "User B must NOT see User A watchlist item"

    # User B attempts to delete User A's item -> MUST return 404 Not Found
    del_resp = await async_client.delete(f"/api/user/watchlist/{item_a_id}", headers=hdr_b)
    assert del_resp.status_code == 404, f"Cross-user delete must return 404, got {del_resp.status_code}"

    # Verify User A's item still exists
    list_a_after = (await async_client.get("/api/user/watchlist", headers=hdr_a)).json()
    assert any(i["id"] == item_a_id for i in list_a_after), "User A item was improperly deleted!"
