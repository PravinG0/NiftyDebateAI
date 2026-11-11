"""
Comprehensive test suite for Angel One SmartAPI Real-Time Enhancements (Milestone 3).

Verifies:
1. Authentication & configuration detection
2. Single quote (get_quote) with full OHLC
3. Batch multi-quotes (get_multi_quotes) with TTL caching
4. Indicator calculation mathematical accuracy (EMA 20, EMA 50, RSI 14, VWAP, ATR)
5. Connection status telemetry
6. Order execution lifecycle (paper orders, order book, positions, live API dispatch)
7. Integration with market_data.build_market_snapshot and market quotes endpoint
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import Response

from angel_client import AngelClient, get_angel_client
from market_data import build_market_snapshot, get_market_quotes_response


# ══════════════════════════════════════════════════════════════════════════════
# 1. Configuration & Authentication Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_angel_client_config_validation():
    """Verify is_configured reflects presence of all four required credentials."""
    client_full = AngelClient(
        api_key="API123", client_code="C123", pin="1234", totp_secret="JBSWY3DPEHPK3PXP"
    )
    assert client_full.is_configured() is True

    # Missing each credential one by one
    assert AngelClient(api_key="", client_code="C", pin="P", totp_secret="T").is_configured() is False
    assert AngelClient(api_key="A", client_code="", pin="P", totp_secret="T").is_configured() is False
    assert AngelClient(api_key="A", client_code="C", pin="", totp_secret="T").is_configured() is False
    assert AngelClient(api_key="A", client_code="C", pin="P", totp_secret="").is_configured() is False


@pytest.mark.asyncio
async def test_angel_client_mock_authenticate_success():
    """Verify authenticate stores tokens and computes expiration."""
    client = AngelClient(
        api_key="FAKE_KEY", client_code="FAKE_CODE", pin="1234", totp_secret="JBSWY3DPEHPK3PXP"
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": True,
        "message": "SUCCESS",
        "errorcode": "",
        "data": {
            "jwtToken": "mock_jwt_token_xyz",
            "refreshToken": "mock_refresh_token_xyz",
            "feedToken": "mock_feed_token_xyz",
        },
    }

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
        ok = await client.authenticate(force_refresh=True)
        assert ok is True
        assert client._jwt_token == "mock_jwt_token_xyz"
        assert client._refresh_token == "mock_refresh_token_xyz"
        assert client._token_expiry_epoch > time.time()


@pytest.mark.asyncio
async def test_angel_client_mock_authenticate_failure():
    """Verify authenticate returns False and handles error response cleanly."""
    client = AngelClient(
        api_key="FAKE_KEY", client_code="FAKE_CODE", pin="1234", totp_secret="JBSWY3DPEHPK3PXP"
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": False,
        "message": "Invalid TOTP or credentials",
        "errorcode": "AB1001",
    }

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
        ok = await client.authenticate(force_refresh=True)
        assert ok is False
        assert client._jwt_token is None


# ══════════════════════════════════════════════════════════════════════════════
# 2. Quote and Batch Quotes Tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_quote_schema_and_values():
    """Verify single quote returns spot_price, open, high, low, close, net_change, percent_change."""
    client = AngelClient()
    client._jwt_token = "mock_token"
    client._token_expiry_epoch = time.time() + 3600

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": True,
        "data": {
            "fetched": [
                {
                    "exchange": "NSE",
                    "tradingSymbol": "Nifty 50",
                    "symbolToken": "99926000",
                    "ltp": 25050.25,
                    "open": 25000.00,
                    "high": 25100.50,
                    "low": 24950.00,
                    "close": 24980.00,
                    "netChange": 70.25,
                    "percentChange": 0.28,
                }
            ]
        },
    }

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
        with patch.object(client, "authenticate", AsyncMock(return_value=True)):
            quote = await client.get_quote("NIFTY")
            assert quote is not None
            assert quote["symbol"] == "NIFTY"
            assert quote["spot_price"] == 25050.25
            assert quote["ltp"] == 25050.25
            assert quote["open"] == 25000.00
            assert quote["high"] == 25100.50
            assert quote["low"] == 24950.00
            assert quote["close"] == 24980.00
            assert quote["net_change"] == 70.25
            assert quote["percent_change"] == 0.28


@pytest.mark.asyncio
async def test_get_multi_quotes_batch_and_caching():
    """Verify batch quotes query multiple symbols and cache hits within TTL."""
    client = AngelClient()
    client._jwt_token = "mock_token"
    client._token_expiry_epoch = time.time() + 3600

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": True,
        "data": {
            "fetched": [
                {
                    "exchange": "NSE",
                    "tradingSymbol": "Nifty 50",
                    "symbolToken": "99926000",
                    "ltp": 25000.0,
                    "open": 24950.0,
                    "high": 25050.0,
                    "low": 24900.0,
                    "close": 24950.0,
                    "netChange": 50.0,
                    "percentChange": 0.20,
                },
                {
                    "exchange": "NSE",
                    "tradingSymbol": "Nifty Bank",
                    "symbolToken": "99926009",
                    "ltp": 59500.0,
                    "open": 59000.0,
                    "high": 59700.0,
                    "low": 58900.0,
                    "close": 59000.0,
                    "netChange": 500.0,
                    "percentChange": 0.85,
                },
            ]
        },
    }

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
        with patch.object(client, "authenticate", AsyncMock(return_value=True)):
            quotes1 = await client.get_multi_quotes(["NIFTY", "BANKNIFTY"], ttl=3.0)
            assert len(quotes1) == 2
            assert quotes1["NIFTY"]["spot_price"] == 25000.0
            assert quotes1["BANKNIFTY"]["spot_price"] == 59500.0
            assert mock_http.post.call_count == 1

            # Second call within TTL must return cached without invoking HTTP POST
            quotes2 = await client.get_multi_quotes(["NIFTY", "BANKNIFTY"], ttl=3.0)
            assert len(quotes2) == 2
            assert mock_http.post.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. Mathematical Accuracy of Indicator Calculations
# ══════════════════════════════════════════════════════════════════════════════

def test_calculate_indicators_empty_and_single_candle():
    """Verify indicators handle boundary inputs: empty list and single candle."""
    empty_res = AngelClient.calculate_indicators([])
    assert empty_res["ema_20"] == 0.0
    assert empty_res["ema_50"] == 0.0
    assert empty_res["rsi"] == 50.0
    assert empty_res["vwap"] == 0.0
    assert empty_res["atr"] == 0.0
    assert empty_res["candle_count"] == 0

    single = [{"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 500}]
    single_res = AngelClient.calculate_indicators(single)
    assert single_res["last_close"] == 105.0
    assert single_res["ema_20"] == 105.0
    assert single_res["ema_50"] == 105.0
    assert single_res["rsi"] == 50.0
    assert single_res["atr"] == 20.0
    assert single_res["vwap"] == round((110.0 + 90.0 + 105.0) / 3.0, 2)


def test_calculate_indicators_ema_mathematical_precision():
    """Verify EMA matches standard formula: alpha = 2 / (N + 1), initialized with SMA."""
    # Create 25 candles with sequential closes 1..25
    candles = [
        {"open": float(i), "high": float(i + 1), "low": float(i - 1), "close": float(i), "volume": 100}
        for i in range(1, 26)
    ]
    ind = AngelClient.calculate_indicators(candles)

    # Hand calculation for EMA 20:
    # First 20 closes are 1..20. SMA = sum(1..20) / 20 = 210 / 20 = 10.5
    # alpha = 2 / 21
    ema = 10.5
    alpha = 2.0 / 21.0
    for p in range(21, 26):
        ema = (float(p) * alpha) + (ema * (1.0 - alpha))
    expected_ema_20 = round(ema, 2)

    assert ind["ema_20"] == expected_ema_20


def test_calculate_indicators_rsi_extremes():
    """Verify RSI handles strictly increasing, strictly decreasing, and flat series."""
    # 20 strictly increasing closes: every delta is positive -> RSI must be 100
    up_candles = [
        {"open": float(i), "high": float(i + 1), "low": float(i), "close": float(i + 1), "volume": 100}
        for i in range(1, 21)
    ]
    up_ind = AngelClient.calculate_indicators(up_candles)
    assert up_ind["rsi"] == 100.0

    # 20 strictly decreasing closes: every delta is negative -> RSI must be 0
    down_candles = [
        {"open": float(50 - i), "high": float(50 - i), "low": float(50 - i - 1), "close": float(50 - i - 1), "volume": 100}
        for i in range(1, 21)
    ]
    down_ind = AngelClient.calculate_indicators(down_candles)
    assert down_ind["rsi"] == 0.0

    # 20 flat closes: all deltas 0 -> RSI must be 50.0, ATR must be 0
    flat_candles = [
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100}
        for _ in range(20)
    ]
    flat_ind = AngelClient.calculate_indicators(flat_candles)
    assert flat_ind["rsi"] == 50.0
    assert flat_ind["atr"] == 0.0


def test_calculate_indicators_vwap_and_atr_precision():
    """Verify VWAP weighting and ATR Wilder smoothing."""
    candles = [
        {"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "volume": 1000},
        {"open": 100.0, "high": 110.0, "low": 98.0, "close": 108.0, "volume": 2000},
        {"open": 108.0, "high": 112.0, "low": 104.0, "close": 106.0, "volume": 3000},
    ]
    # Candle 0: typical = (105+95+100)/3 = 100.0. vol=1000. prod = 100,000
    # Candle 1: typical = (110+98+108)/3 = 105.333. vol=2000. prod = 210,666.67
    # Candle 2: typical = (112+104+106)/3 = 107.333. vol=3000. prod = 322,000.0
    # total vol = 6000. sum prod = 632,666.67. vwap = 632666.67 / 6000 = 105.44
    ind = AngelClient.calculate_indicators(candles)
    assert ind["vwap"] == pytest.approx(105.44, abs=0.05)

    # TR0 = 105 - 95 = 10.0
    # TR1 = max(110-98, |110-100|, |98-100|) = max(12, 10, 2) = 12.0
    # TR2 = max(112-104, |112-108|, |104-108|) = max(8, 4, 4) = 8.0
    # average TR for 3 candles = (10 + 12 + 8) / 3 = 10.0
    assert ind["atr"] == 10.0


# ══════════════════════════════════════════════════════════════════════════════
# 4. Connection Status Telemetry
# ══════════════════════════════════════════════════════════════════════════════

def test_connection_status_sync_and_async():
    """Verify connection status reports authentication, feed source, and expiry."""
    client = AngelClient(
        api_key="API_KEY", client_code="USER1234", pin="1234", totp_secret="SECRET"
    )
    # When not logged in
    st_unauth = client.get_connection_status_sync()
    assert st_unauth["configured"] is True
    assert st_unauth["authenticated"] is False
    assert st_unauth["status"] == "CONFIGURED"

    # When authenticated
    client._jwt_token = "valid_jwt"
    client._token_expiry_epoch = time.time() + 7200
    st_auth = client.get_connection_status_sync()
    assert st_auth["authenticated"] is True
    assert st_auth["status"] == "CONNECTED"
    assert st_auth["feed_source"] == "live_exchange"
    assert st_auth["token_expiry_seconds"] > 7000


# ══════════════════════════════════════════════════════════════════════════════
# 5. Order Execution Lifecycle
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_order_execution_paper_flow():
    """Verify paper order execution, order book tracking, position aggregation, and cancellation."""
    client = AngelClient()
    # Place BUY order
    res_buy = await client.place_order(
        symbol="NIFTY",
        transaction_type="BUY",
        quantity=25,
        price=150.0,
        order_type="LIMIT",
        is_paper=True,
    )
    assert res_buy["status"] == "success"
    assert res_buy["mode"] == "PAPER"
    order_id = res_buy["order_id"]

    # Verify order book
    book = await client.get_order_book(paper_only=True)
    assert any(o["order_id"] == order_id for o in book)

    # Verify position
    positions = await client.get_positions(paper_only=True)
    nifty_pos = next((p for p in positions if p["symbol"] == "NIFTY"), None)
    assert nifty_pos is not None
    assert nifty_pos["net_quantity"] == 25
    assert nifty_pos["average_price"] == 150.0

    # Place SELL order
    res_sell = await client.place_order(
        symbol="NIFTY",
        transaction_type="SELL",
        quantity=25,
        price=160.0,
        order_type="LIMIT",
        is_paper=True,
    )
    assert res_sell["status"] == "success"

    # Position should now be flat (net_quantity == 0)
    positions_after = await client.get_positions(paper_only=True)
    nifty_pos_after = next((p for p in positions_after if p["symbol"] == "NIFTY"), None)
    assert nifty_pos_after["net_quantity"] == 0

    # Cancel a test order
    dummy_order = await client.place_order(
        symbol="BANKNIFTY", transaction_type="BUY", quantity=15, price=300.0, is_paper=True
    )
    cancel_res = await client.cancel_order(dummy_order["order_id"], is_paper=True)
    assert cancel_res["status"] == "success"


@pytest.mark.asyncio
async def test_order_execution_live_api_dispatch_mock():
    """Verify live order placement dispatches expected JSON payload and Bearer header to SmartAPI."""
    client = AngelClient(
        api_key="TEST_API_KEY", client_code="USER123", pin="1234", totp_secret="SECRET"
    )
    client._jwt_token = "mock_jwt_token_456"
    client._token_expiry_epoch = time.time() + 3600

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": True,
        "message": "Order Placed",
        "data": {"orderid": "SMARTAPI_ORDER_999"},
    }

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
        res = await client.place_order(
            symbol="NIFTY",
            transaction_type="BUY",
            quantity=25,
            order_type="MARKET",
            is_paper=False,
        )
        assert res["status"] == "success"
        assert res["order_id"] == "SMARTAPI_ORDER_999"

        # Check call arguments to SmartAPI
        call_args = mock_http.post.call_args
        assert call_args is not None
        payload = call_args.kwargs["json"]
        headers = call_args.kwargs["headers"]

        assert payload["variety"] == "NORMAL"
        assert payload["tradingsymbol"] == "Nifty 50"
        assert payload["symboltoken"] == "99926000"
        assert payload["transactiontype"] == "BUY"
        assert payload["ordertype"] == "MARKET"
        assert payload["quantity"] == "25"
        assert headers["Authorization"] == "Bearer mock_jwt_token_456"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Market Data Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_build_market_snapshot_uses_angel_client_first():
    """Verify build_market_snapshot queries AngelClient and populates real fields."""
    mock_quote = {
        "symbol": "NIFTY",
        "spot_price": 25100.0,
        "open": 25050.0,
        "high": 25150.0,
        "low": 25020.0,
        "close": 25080.0,
        "net_change": 20.0,
        "percent_change": 0.08,
    }
    mock_candles = [
        {"open": 25000.0, "high": 25100.0, "low": 24950.0, "close": 25080.0, "volume": 1000}
        for _ in range(30)
    ]

    with patch("angel_client.get_angel_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.get_quote = AsyncMock(return_value=mock_quote)
        mock_client.get_candles = AsyncMock(return_value=mock_candles)
        mock_client.calculate_indicators = MagicMock(return_value={
            "ema_20": 25075.5,
            "ema_50": 25050.2,
            "rsi": 62.5,
            "vwap": 25060.0,
            "atr": 45.0,
            "higher_tf_trend": "BULLISH",
            "candle_count": 30,
        })
        mock_get_client.return_value = mock_client

        snapshot = await build_market_snapshot("NIFTY", force_fresh=True)
        assert snapshot.spot_price == 25100.0
        assert snapshot.nifty_open == 25050.0
        assert snapshot.nifty_high == 25150.0
        assert snapshot.nifty_low == 25020.0
        assert snapshot.ema_20 == 25075.5
        assert snapshot.ema_50 == 25050.2
        assert snapshot.rsi == 62.5
        assert snapshot.vwap == 25060.0
        assert snapshot.atr == 45.0
        assert snapshot.higher_tf_trend == "BULLISH"


@pytest.mark.asyncio
async def test_build_market_snapshot_graceful_fallback():
    """Verify build_market_snapshot falls back cleanly when AngelClient is unconfigured."""
    with patch("angel_client.get_angel_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.is_configured.return_value = False
        mock_get_client.return_value = mock_client

        snapshot = await build_market_snapshot("NIFTY", force_fresh=True)
        assert snapshot.spot_price > 0
        assert snapshot.ema_20 > 0
        assert snapshot.rsi > 0


@pytest.mark.asyncio
async def test_get_market_quotes_response_contract():
    """Verify get_market_quotes_response adheres to PROJECT.md interface contract."""
    res = await get_market_quotes_response("NIFTY,BANKNIFTY")
    assert isinstance(res, dict)
    assert res["source"] == "angel_one"
    assert "timestamp" in res
    assert "quotes" in res
    assert "NIFTY" in res["quotes"]
    assert "BANKNIFTY" in res["quotes"]
    assert res["quotes"]["NIFTY"]["spot_price"] > 0
