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


@pytest.mark.asyncio
async def test_angel_client_get_quote_live():
    """Verify get_quote returns full OHLC quote without discarding fields."""
    client = get_angel_client()
    if not client.is_configured():
        pytest.skip("Angel One credentials not configured")

    quote = await client.get_quote("NIFTY")
    assert quote is not None, "Failed to fetch NIFTY quote"
    for field in ["spot_price", "open", "high", "low", "close", "net_change", "percent_change"]:
        assert field in quote, f"Missing required field: {field}"
        assert quote[field] is not None
    assert quote["spot_price"] > 0
    assert quote["high"] >= quote["low"]


@pytest.mark.asyncio
async def test_angel_client_get_multi_quotes_batch():
    """Verify get_multi_quotes fetches batch quotes for multiple symbols with caching."""
    client = get_angel_client()
    if not client.is_configured():
        pytest.skip("Angel One credentials not configured")

    quotes = await client.get_multi_quotes(["NIFTY", "BANKNIFTY"])
    assert isinstance(quotes, dict)
    assert "NIFTY" in quotes
    assert "BANKNIFTY" in quotes
    assert quotes["NIFTY"]["spot_price"] > 0
    assert quotes["BANKNIFTY"]["spot_price"] > 0

    # Test cache hit within TTL
    quotes_cached = await client.get_multi_quotes(["NIFTY", "BANKNIFTY"], ttl=5.0)
    assert quotes_cached["NIFTY"]["spot_price"] == quotes["NIFTY"]["spot_price"]


@pytest.mark.asyncio
async def test_angel_client_calculate_indicators_on_real_candles():
    """Verify indicator calculation returns valid EMA, RSI, VWAP, ATR on real candles."""
    client = get_angel_client()
    if not client.is_configured():
        pytest.skip("Angel One credentials not configured")

    candles = await client.get_candles("NIFTY", "5m", "5d")
    assert len(candles) >= 20, "Insufficient candles for indicators test"

    indicators = client.calculate_indicators(candles)
    for ind in ["ema_20", "ema_50", "rsi", "vwap", "atr", "higher_tf_trend"]:
        assert ind in indicators, f"Missing indicator: {ind}"
    assert indicators["ema_20"] > 0
    assert indicators["ema_50"] > 0
    assert 0.0 <= indicators["rsi"] <= 100.0
    assert indicators["atr"] > 0
    assert indicators["higher_tf_trend"] in ("BULLISH", "BEARISH", "NEUTRAL")


@pytest.mark.asyncio
async def test_angel_client_connection_status_telemetry():
    """Verify get_connection_status returns complete telemetry."""
    client = get_angel_client()
    status = await client.get_connection_status()
    assert isinstance(status, dict)
    assert "configured" in status
    assert "authenticated" in status
    assert "feed_source" in status
    assert "status" in status
    assert "angel_connected" in status


@pytest.mark.asyncio
async def test_angel_client_order_execution_lifecycle():
    """Verify paper order placement, order book, and positions tracking."""
    client = get_angel_client()
    order_result = await client.place_order(
        symbol="NIFTY",
        transaction_type="BUY",
        quantity=1,
        order_type="MARKET",
        is_paper=True,
    )
    assert order_result["status"] == "success"
    assert order_result["mode"] == "PAPER"
    order_id = order_result["order_id"]
    assert order_id.startswith("PAPER_")

    book = await client.get_order_book(paper_only=True)
    assert any(o.get("order_id") == order_id for o in book)

    positions = await client.get_positions(paper_only=True)
    assert len(positions) > 0
    pos = next((p for p in positions if p["symbol"] == "NIFTY"), None)
    assert pos is not None
    assert pos["net_quantity"] >= 1

