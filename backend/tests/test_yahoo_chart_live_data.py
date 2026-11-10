"""
Automated tests for Live Yahoo Finance Market Data in Charts.

Fulfills Acceptance Criteria:
1. Automated test confirms live market data is successfully retrieved from Yahoo Finance for configured symbols (NIFTY, BANKNIFTY, RELIANCE, HDFCBANK).
2. Automated test confirms chart data endpoints (/api/market/candles and /market/candles) format and deliver live Yahoo data without errors.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import patch

import httpx
from httpx import ASGITransport, AsyncClient
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dhan_client import get_dhan_client, map_symbol_to_yahoo_ticker
from main import app

CONFIGURED_SYMBOLS = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK"]
CONFIGURED_TIMEFRAMES = [
    ("5m", "5d"),
    ("15m", "5d"),
    ("1d", "1mo"),
]


class TestYahooFinanceDataRetrieval:
    """Acceptance Criterion 1: Live Market Data Retrieval from Yahoo Finance."""

    def test_symbol_to_yahoo_ticker_mapping(self):
        """Verify all configured symbols map to valid Yahoo Finance tickers."""
        expected_mappings = {
            "NIFTY": "%5ENSEI",
            "^NSEI": "%5ENSEI",
            "NIFTY 50": "%5ENSEI",
            "BANKNIFTY": "%5ENSEBANK",
            "^NSEBANK": "%5ENSEBANK",
            "BANK NIFTY": "%5ENSEBANK",
            "RELIANCE": "RELIANCE.NS",
            "RELIANCE.NS": "RELIANCE.NS",
            "HDFCBANK": "HDFCBANK.NS",
            "HDFCBANK.NS": "HDFCBANK.NS",
            "INDIA_VIX": "%5EINDIAVIX",
        }
        for sym, expected in expected_mappings.items():
            assert map_symbol_to_yahoo_ticker(sym) == expected, f"Failed mapping for {sym}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", CONFIGURED_SYMBOLS)
    async def test_live_yahoo_candles_retrieval(self, symbol: str):
        """Verify live OHLC candles are fetched from Yahoo Finance for configured symbols."""
        dhan = get_dhan_client()
        candles = await dhan.fetch_real_exchange_candles(symbol, interval="15m", range_period="5d")

        assert isinstance(candles, list), f"Expected list for {symbol}"
        assert len(candles) > 0, f"No live candles retrieved from Yahoo for {symbol}"

        last_time = -1
        for i, c in enumerate(candles):
            assert "time" in c and isinstance(c["time"], int), f"Candle {i} missing integer time: {c}"
            assert c["time"] > last_time, f"Candle {i} time {c['time']} not strictly greater than {last_time}"
            last_time = c["time"]

            o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
            assert all(isinstance(v, (int, float)) and v > 0 for v in (o, h, l, cl)), f"Invalid OHLC: {c}"
            assert h >= max(o, cl), f"High {h} must be >= max(open={o}, close={cl})"
            assert l <= min(o, cl), f"Low {l} must be <= min(open={o}, close={cl})"

        # Asset-specific price verification to ensure distinct live quotes
        latest_close = candles[-1]["close"]
        if symbol == "NIFTY":
            assert 18000.0 <= latest_close <= 35000.0, f"NIFTY price {latest_close} out of expected range"
        elif symbol == "BANKNIFTY":
            assert 40000.0 <= latest_close <= 75000.0, f"BANKNIFTY price {latest_close} out of expected range"
        elif symbol == "RELIANCE":
            assert 800.0 <= latest_close <= 4000.0, f"RELIANCE price {latest_close} must reflect equity, not index"
        elif symbol == "HDFCBANK":
            assert 400.0 <= latest_close <= 3000.0, f"HDFCBANK price {latest_close} must reflect equity, not index"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", CONFIGURED_SYMBOLS)
    async def test_live_yahoo_real_exchange_feed(self, symbol: str):
        """Verify real-time quote feed retrieves live market prices from Yahoo Finance."""
        dhan = get_dhan_client()
        feed = await dhan.fetch_real_exchange_feed(symbol)

        assert isinstance(feed, dict)
        assert "spot_price" in feed and feed["spot_price"] > 0
        assert "high" in feed and feed["high"] >= feed["low"]
        assert "low" in feed and feed["low"] > 0
        assert "previous_close" in feed and feed["previous_close"] > 0


class TestChartDataEndpointAndFormatting:
    """Acceptance Criterion 2: Chart Data Endpoint Formats & Delivers Live Data."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", CONFIGURED_SYMBOLS + ["^NSEI", "^NSEBANK"])
    @pytest.mark.parametrize("interval,range_period", CONFIGURED_TIMEFRAMES)
    async def test_chart_endpoint_delivers_live_candles(self, symbol: str, interval: str, range_period: str):
        """Verify /api/market/candles formats and delivers valid data across symbols and timeframes."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/market/candles?symbol={symbol}&interval={interval}&range_period={range_period}"
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

            data = resp.json()
            assert data.get("status") == "success"
            assert data.get("symbol") == symbol
            candles = data.get("candles", [])
            assert len(candles) > 0, f"Candles empty for {symbol} ({interval}, {range_period})"

            # Validate lightweight-charts formatting constraints
            prev_time = -1
            for idx, candle in enumerate(candles):
                t = candle.get("time")
                assert isinstance(t, int), f"Candle {idx} time not int: {t}"
                assert t > prev_time, f"Candle {idx} timestamp not strictly ascending: {t} <= {prev_time}"
                prev_time = t

                o = candle.get("open")
                h = candle.get("high")
                l = candle.get("low")
                c = candle.get("close")
                assert all(v is not None and not isinstance(v, str) and v > 0 for v in (o, h, l, c)), f"Invalid candle: {candle}"
                assert h >= max(o, c), f"High {h} < max({o}, {c})"
                assert l <= min(o, c), f"Low {l} > min({o}, {c})"

    @pytest.mark.asyncio
    async def test_chart_endpoint_without_api_prefix(self):
        """Verify /market/candles route also works without /api prefix."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/market/candles?symbol=NIFTY&interval=5m&range_period=5d")
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("status") == "success"
            assert len(data.get("candles", [])) > 0

    @pytest.mark.asyncio
    async def test_chart_resilience_fallback_on_network_error(self):
        """Verify chart endpoint gracefully falls back when Yahoo Finance network call fails."""
        dhan = get_dhan_client()

        with patch.object(dhan, "fetch_real_exchange_candles", return_value=[]):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/market/candles?symbol=NIFTY&interval=5m&range_period=5d")
                assert resp.status_code == 200
                data = resp.json()
                assert data.get("status") == "success"
                assert data.get("source") == "simulator"
                candles = data.get("candles", [])
                assert len(candles) > 0, "Fallback generator should supply candles when Yahoo feed is empty"
                times = [c["time"] for c in candles]
                assert times == sorted(times)
                assert len(times) == len(set(times)), "Duplicate timestamps in fallback candles"

    @pytest.mark.asyncio
    async def test_candle_caching_and_rate_limit_mitigation(self):
        """Verify repeated calls within TTL return cached data without repeated outbound calls."""
        dhan = get_dhan_client()
        # Prime cache
        c1 = await dhan.fetch_real_exchange_candles("RELIANCE", interval="15m", range_period="5d")
        assert len(c1) > 0

        # Spy or patch httpx client to ensure no network calls on subsequent requests
        with patch("httpx.AsyncClient.get", side_effect=RuntimeError("Should hit TTL cache")):
            c2 = await dhan.fetch_real_exchange_candles("RELIANCE", interval="15m", range_period="5d")
            assert len(c2) == len(c1)
            assert c2[-1]["close"] == c1[-1]["close"]

    @pytest.mark.asyncio
    async def test_normalized_banknifty_alias_fallback_prices(self):
        """Verify ^NSEBANK and %5ENSEBANK fallback candles anchor around BANKNIFTY (~59,741) not NIFTY."""
        dhan = get_dhan_client()
        with patch.object(dhan, "fetch_real_exchange_candles", return_value=[]):
            c_bank = await dhan.get_candles("^NSEBANK", interval="5m", range_period="5d")
            assert len(c_bank) > 0
            # BANKNIFTY is ~59k, NIFTY is ~25k
            assert c_bank[-1]["close"] > 45000.0, f"Expected BANKNIFTY fallback spot >45000, got {c_bank[-1]['close']}"

    @pytest.mark.asyncio
    async def test_rapid_concurrent_interleaved_candle_queries(self):
        """Stress test rapid concurrent requests for distinct symbols, ensuring no cross-talk."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            symbols = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK"] * 5
            tasks = [client.get(f"/api/market/candles?symbol={s}&interval=5m&range_period=5d") for s in symbols]
            responses = await asyncio.gather(*tasks)

            for sym, resp in zip(symbols, responses):
                assert resp.status_code == 200
                data = resp.json()
                assert data["symbol"] == sym
                candles = data["candles"]
                assert len(candles) > 0
                last_close = candles[-1]["close"]
                if sym == "NIFTY":
                    assert 20000.0 <= last_close <= 30000.0
                elif sym == "BANKNIFTY":
                    assert 45000.0 <= last_close <= 70000.0
                elif sym == "RELIANCE":
                    assert 900.0 <= last_close <= 2500.0
                elif sym == "HDFCBANK":
                    assert 500.0 <= last_close <= 1500.0

    @pytest.mark.asyncio
    async def test_out_of_order_and_duplicate_timestamp_handling(self):
        """Verify scrambled or duplicate Yahoo timestamps are correctly sorted and deduplicated."""
        dhan = get_dhan_client()
        mock_yahoo_data = {
            "chart": {
                "result": [
                    {
                        "meta": {"regularMarketPrice": 25000.0},
                        "timestamp": [1700000300, 1700000100, 1700000200, 1700000200, 1700000400],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [25030.0, 25010.0, 25020.0, 25020.0, 25040.0],
                                    "high": [25035.0, 25015.0, 25025.0, 25025.0, 25045.0],
                                    "low": [25025.0, 25005.0, 25015.0, 25015.0, 25035.0],
                                    "close": [25032.0, 25012.0, 25022.0, 25022.0, 25042.0],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        mock_resp = httpx.Response(200, json=mock_yahoo_data, request=httpx.Request("GET", "http://test"))
        client = await dhan.get_yahoo_client()
        with patch.object(client, "get", return_value=mock_resp):
            candles = await dhan.fetch_real_exchange_candles("MOCK_SYM", interval="5m", range_period="1d")
            assert len(candles) == 4, f"Expected 4 deduplicated bars, got {len(candles)}"
            times = [c["time"] for c in candles]
            assert times == [1700000100, 1700000200, 1700000300, 1700000400]
            assert times == sorted(times) and len(times) == len(set(times))

    @pytest.mark.asyncio
    async def test_cached_candles_immutability(self):
        """Verify mutating returned candles does not pollute the in-memory cache."""
        dhan = get_dhan_client()
        c1 = await dhan.fetch_real_exchange_candles("NIFTY", interval="15m", range_period="5d")
        original_close = c1[0]["close"]

        # Mutate returned list and dict
        c1[0]["close"] = -999999.0
        c1.pop()

        # Fetch again from cache
        c2 = await dhan.fetch_real_exchange_candles("NIFTY", interval="15m", range_period="5d")
        assert c2[0]["close"] == original_close, "In-memory cache was corrupted by downstream mutation"
        assert len(c2) == len(c1) + 1, "In-memory cache length was affected by pop()"

    @pytest.mark.asyncio
    async def test_yahoo_http_client_reuse(self):
        """Verify DhanClient reuses the persistent _yahoo_http client across calls."""
        dhan = get_dhan_client()
        client1 = await dhan.get_yahoo_client()
        client2 = await dhan.get_yahoo_client()
        assert client1 is client2
        assert not client1.is_closed

    @pytest.mark.asyncio
    async def test_real_exchange_feed_open_price_parsing(self):
        """Verify fetch_real_exchange_feed correctly extracts the opening price."""
        dhan = get_dhan_client()
        feed = await dhan.fetch_real_exchange_feed("NIFTY")
        assert feed["open"] > 0
        assert feed["high"] >= feed["low"]

    @pytest.mark.asyncio
    async def test_chart_endpoint_empty_and_falsy_symbol_handling(self):
        """Verify /api/market/candles handles empty or missing symbol query parameter gracefully."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res_empty = await client.get("/api/market/candles?symbol=")
            assert res_empty.status_code == 200
            assert len(res_empty.json()["candles"]) > 0

            res_none = await client.get("/api/market/candles")
            assert res_none.status_code == 200
            assert len(res_none.json()["candles"]) > 0
