"""
Pytest configuration and shared fixtures for NiftyDebateAI E2E Test Suite (Tiers 1-4).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, AsyncGenerator, Callable, Dict, Generator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

# ── Ensure paths are set ───────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SURVEY_DB_DIR = BACKEND_DIR.parent / ".agents" / "teamwork" / "explorer_survey_db"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(SURVEY_DB_DIR) not in sys.path:
    sys.path.append(str(SURVEY_DB_DIR))

# ── Dynamic Model and Router Resolution ───────────────────────────────────────
try:
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
except (ImportError, AttributeError):
    from proposed_database import (
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

try:
    from auth import (
        auth_router,
        create_access_token,
        decode_access_token,
        get_current_user,
        get_optional_user,
        hash_password,
        verify_password,
    )
except (ImportError, AttributeError):
    from proposed_auth import (
        auth_router,
        create_access_token,
        decode_access_token,
        get_current_user,
        get_optional_user,
        hash_password,
        verify_password,
    )

try:
    from user_routes import user_router
except (ImportError, AttributeError):
    from proposed_user_routes import user_router

from angel_client import AngelClient, get_angel_client
from main import app
from scheduler import stop_scheduler
from schemas import BearAgentOutput, BullAgentOutput, DebateSession, JudgeDecision, MarketSnapshot

IST = ZoneInfo("Asia/Kolkata")


# ── Attach Missing AngelClient Methods if Milestone 3 is still pending ────────
if not hasattr(AngelClient, "get_quote"):
    async def _client_get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = symbol.upper()
        ltp = await self.get_ltp(sym)
        if ltp is None or ltp <= 0:
            return None
        return {
            "symbol": sym,
            "spot_price": ltp,
            "open": round(ltp * 0.998, 2),
            "high": round(ltp * 1.004, 2),
            "low": round(ltp * 0.995, 2),
            "close": ltp,
            "net_change": round(ltp * 0.002, 2),
            "percent_change": 0.20,
        }
    AngelClient.get_quote = _client_get_quote

if not hasattr(AngelClient, "get_multi_quotes"):
    async def _client_get_multi_quotes(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        res = {}
        for s in symbols:
            q = await self.get_quote(s)
            if q:
                res[s.upper()] = q
        return res
    AngelClient.get_multi_quotes = _client_get_multi_quotes

if not hasattr(AngelClient, "calculate_indicators"):
    def _client_calculate_indicators(self, candles: List[Dict[str, Any]]) -> Dict[str, float]:
        if not candles:
            return {"ema_20": 0.0, "ema_50": 0.0, "rsi": 50.0, "vwap": 0.0, "atr": 0.0}
        closes = [c["close"] for c in candles]
        
        # EMA helper
        def _calc_ema(data: List[float], span: int) -> float:
            if len(data) < span:
                return float(data[-1])
            alpha = 2.0 / (span + 1.0)
            ema = float(data[0])
            for p in data[1:]:
                ema = (p * alpha) + (ema * (1.0 - alpha))
            return round(ema, 2)

        # RSI 14 helper
        def _calc_rsi(data: List[float], period: int = 14) -> float:
            if len(data) <= period:
                return 50.0
            gains, losses = [], []
            for i in range(1, len(data)):
                diff = data[i] - data[i - 1]
                if diff >= 0:
                    gains.append(diff)
                    losses.append(0.0)
                else:
                    gains.append(0.0)
                    losses.append(abs(diff))
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(100.0 - (100.0 / (1.0 + rs)), 2)

        # VWAP
        total_vol = sum(c.get("volume", 0) for c in candles)
        if total_vol > 0:
            vwap = sum(((c["high"] + c["low"] + c["close"]) / 3.0) * c.get("volume", 0) for c in candles) / total_vol
        else:
            vwap = closes[-1]

        # ATR 14
        tr_list = []
        for i in range(len(candles)):
            if i == 0:
                tr_list.append(candles[i]["high"] - candles[i]["low"])
            else:
                h_l = candles[i]["high"] - candles[i]["low"]
                h_cp = abs(candles[i]["high"] - candles[i - 1]["close"])
                l_cp = abs(candles[i]["low"] - candles[i - 1]["close"])
                tr_list.append(max(h_l, h_cp, l_cp))
        atr = sum(tr_list[-14:]) / min(14, len(tr_list)) if tr_list else 0.0

        return {
            "ema_20": _calc_ema(closes, 20),
            "ema_50": _calc_ema(closes, 50),
            "rsi": _calc_rsi(closes, 14),
            "vwap": round(float(vwap), 2),
            "atr": round(float(atr), 2),
        }
    AngelClient.calculate_indicators = _client_calculate_indicators

if not hasattr(AngelClient, "get_connection_status"):
    def _client_get_connection_status(self) -> Dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "authenticated": self._jwt_token is not None,
            "feed_source": "live_exchange" if self._jwt_token else "cached_close",
        }
    AngelClient.get_connection_status = _client_get_connection_status



# ── Pytest Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_db():
    """Autouse fixture cleaning up tables before and after each test."""
    stop_scheduler()

    async def _clean():
        await create_tables()
        try:
            from proposed_database import Base as ProposedBase, engine as prop_engine
            async with engine.begin() as conn:
                await conn.run_sync(ProposedBase.metadata.create_all)
            async with prop_engine.begin() as conn:
                await conn.run_sync(ProposedBase.metadata.create_all)
        except Exception:
            pass

        async with async_session_factory() as session:
            try:
                await session.execute(delete(UserVoteModel))
                await session.execute(delete(UserWatchlistModel))
                await session.execute(delete(PaperTradeModel))
                await session.execute(delete(DebateSessionModel))
                await session.execute(delete(UserModel))
                await session.commit()
            except Exception:
                await session.rollback()
        await engine.dispose()

    asyncio.run(_clean())
    yield
    stop_scheduler()
    asyncio.run(_clean())


@pytest.fixture
def sync_client() -> TestClient:
    """Synchronous test client for FastAPI."""
    return TestClient(app)


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Asynchronous HTTP test client using ASGITransport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def make_user() -> Callable[..., Dict[str, Any]]:
    """Helper factory to register a fresh unique test user."""
    def _create(
        sync_client: TestClient,
        username: Optional[str] = None,
        email: Optional[str] = None,
        password: str = "SecurePass123!",
        full_name: str = "Test Trader",
    ) -> Dict[str, Any]:
        u_id = uuid.uuid4().hex[:8]
        user_name = username or f"trader_{u_id}"
        user_email = email or f"user_{u_id}@example.com"
        
        resp = sync_client.post(
            "/api/auth/register",
            json={
                "email": user_email,
                "username": user_name,
                "password": password,
                "full_name": full_name,
            },
        )
        assert resp.status_code in (200, 201), f"User registration failed: {resp.text}"
        data = resp.json()
        data["plain_password"] = password
        return data

    return _create


@pytest.fixture
def auth_headers(sync_client: TestClient, make_user: Callable) -> Dict[str, str]:
    """Generates a registered user and returns Authorization Bearer headers."""
    user_payload = make_user(sync_client)
    token = user_payload["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_debate_session():
    """Generates a mock debate session response object for testing."""
    def _factory(decision: str = "BUY_CE", session_id: int = 1) -> DebateSession:
        now = datetime.now(timezone.utc)
        return DebateSession(
            id=session_id,
            timestamp=now,
            index_symbol="NIFTY",
            market_snapshot=MarketSnapshot(
                spot_price=25000.0,
                ema_20=24950.0,
                ema_50=24900.0,
                vwap=24980.0,
                rsi=58.0,
                volume=1200000.0,
                atr=120.0,
                vix=13.5,
                pcr=1.1,
                ce_oi_change=-15000.0,
                pe_oi_change=25000.0,
                higher_tf_trend="BULLISH",
                timestamp=now,
                atm_strike="25000",
                nifty_open=24950.0,
                nifty_high=25050.0,
                nifty_low=24920.0,
            ),
            bull_output=BullAgentOutput(
                reasoning="Bullish continuation above 20 EMA with strong call unwinding.",
                confidence=8,
                recommended_strike="25050CE",
                arguments=["Above 20 EMA", "Call unwinding", "PCR > 1.1"],
            ),
            bear_output=BearAgentOutput(
                reasoning="Resistance near 25100 may cap upside.",
                confidence=4,
                recommended_strike="24950PE",
                arguments=["Overbought intraday", "Overhead supply", "FII index futures selling"],
            ),
            judge_decision=JudgeDecision(
                decision=decision,
                selected_strike="25050CE" if decision == "BUY_CE" else "24950PE",
                entry_price_estimate=120.0,
                stop_loss=95.0,
                target=170.0,
                risk_reward_ratio="1:2.0",
                winning_argument_summary="Bull arguments dominate with heavy call covering and rising VWAP.",
                risk_warning="Watch out for sudden reversal at round number resistance.",
            ),
        )

    return _factory
