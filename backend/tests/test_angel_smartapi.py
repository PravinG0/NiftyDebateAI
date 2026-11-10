"""
Tests for Angel One SmartAPI live chart candles and authentication.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from angel_client import AngelClient, get_angel_client
from main import app


def test_angel_client_configuration():
    """Verify AngelClient properly detects configured credentials."""
    client = AngelClient(
        api_key="TEST_KEY",
        client_code="TEST_USER",
        pin="1234",
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    assert client.is_configured() is True

    unconfigured = AngelClient(api_key="", client_code="", pin="", totp_secret="")
    assert unconfigured.is_configured() is False


@pytest.mark.asyncio
async def test_angel_client_live_connection():
    """Verify live AngelClient can authenticate and retrieve real candles if configured."""
    client = get_angel_client()
    if not client.is_configured():
        pytest.skip("Angel One credentials not configured in environment")

    # 1. Test authentication
    auth_ok = await client.authenticate()
    assert auth_ok is True, "Angel One SmartAPI authentication failed"

    # 2. Test candle fetching for NIFTY
    candles = await client.get_candles(symbol="NIFTY", interval="15m", range_period="5d")
    assert isinstance(candles, list)
    assert len(candles) > 0, "No candles returned from Angel One for NIFTY"

    for c in candles:
        assert "time" in c and isinstance(c["time"], int)
        assert "open" in c and c["open"] > 0
        assert "high" in c and c["high"] >= max(c["open"], c["close"])
        assert "low" in c and c["low"] <= min(c["open"], c["close"])
        assert "close" in c and c["close"] > 0

    # Strictly ascending timestamps
    times = [c["time"] for c in candles]
    assert times == sorted(times)
    assert len(times) == len(set(times))


@pytest.mark.asyncio
async def test_api_market_candles_returns_angelone_source():
    """Verify /api/market/candles endpoint uses Angel One as primary source."""
    client = get_angel_client()
    if not client.is_configured():
        pytest.skip("Angel One credentials not configured")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        resp = await http_client.get("/api/market/candles?symbol=NIFTY&interval=5m&range_period=5d")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "success"
        assert data.get("symbol") == "NIFTY"
        assert data.get("source") == "angelone"
        assert len(data.get("candles", [])) > 0
