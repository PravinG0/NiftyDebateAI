"""Adversarial Empirical Verification Suite for Angel One SmartAPI & Guardrails.

Authored by Challenger 2 (Empirical Verification of Angel One SmartAPI & Guardrails).

Covers 3 core attack vectors:
1. Mathematical Verification of Technical Indicators:
   - Flat prices, monotonic rallies, monotonic crashes, zero volume, single candle, missing ticks, extreme prices.
   - Verification of no division by zero, no NaNs/Infs, mathematical precision against independent oracles.
2. Angel One Resilience & Throttling:
   - Rapid concurrency burst on batch quotes (cache hit verification, zero duplicate HTTP requests, zero 429s).
   - Upstream API failure injection (HTTP 500, 502, 503, 429, timeouts, corrupted/invalid JSON, data=None).
   - Graceful fallback in market snapshot and quote queries.
   - 401 Unauthorized automatic re-authentication.
3. Strict 1-Lot Risk Guardrail Enforcement:
   - Multi-lot orders (lots=2, 5, 50, 100) rejected across guardrail engine and manual trade API.
   - Strict budget limit of Rs 5,000 (boundary testing at 5000.00 vs 5000.25 for Nifty, 4999.95 vs 5000.10 for Bank Nifty).
   - Strict risk limit enforcement (SL > Rs 700 rejected, Target > Rs 1,000 rejected).
   - Directional logic enforcement (SL < entry, Target > entry).
   - Automatic SL/Target clamping verification.
   - Empirical verification of AngelClient.place_order layer boundaries.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from angel_client import AngelClient, get_angel_client
from config import (
    BUDGET_LIMIT,
    INDEX_CONFIG,
    MAX_STOP_LOSS_INR,
    MAX_TARGET_INR,
    BudgetExceededError,
    RiskLimitExceededError,
    compute_clamped_sl_target,
    validate_paper_trade_guardrails,
)
from main import app
from market_data import build_market_snapshot


# ══════════════════════════════════════════════════════════════════════════════
# Vector 1: Mathematical Verification of Technical Indicators
# ══════════════════════════════════════════════════════════════════════════════

class TestIndicatorMathematicalVerification:
    """Stress tests mathematical stability and precision of AngelClient.calculate_indicators."""

    def test_indicators_empty_candles(self):
        """Empty candle list must return zeroed/neutral schema without raising exceptions."""
        res = AngelClient.calculate_indicators([])
        assert res["candle_count"] == 0
        assert res["ema_20"] == 0.0
        assert res["ema_50"] == 0.0
        assert res["rsi"] == 50.0
        assert res["rsi_14"] == 50.0
        assert res["vwap"] == 0.0
        assert res["atr"] == 0.0
        assert res["higher_tf_trend"] == "NEUTRAL"
        assert res["last_close"] == 0.0

    def test_indicators_single_candle(self):
        """Single candle must produce deterministic baselines without division by zero."""
        c = [{"time": 1700000000, "open": 25000.0, "high": 25100.0, "low": 24900.0, "close": 25050.0, "volume": 1200}]
        res = AngelClient.calculate_indicators(c)
        assert res["candle_count"] == 1
        assert res["ema_20"] == 25050.0
        assert res["ema_50"] == 25050.0
        assert res["rsi"] == 50.0
        # VWAP = (H + L + C) / 3 = (25100 + 24900 + 25050) / 3 = 75050 / 3 = 25016.67
        assert res["vwap"] == 25016.67
        # ATR = H - L = 25100 - 24900 = 200.0
        assert res["atr"] == 200.0
        assert res["last_close"] == 25050.0
        assert res["higher_tf_trend"] == "BULLISH"

    def test_indicators_flat_prices_no_division_by_zero(self):
        """Completely flat candle series (zero gains, zero losses, zero spread).
        
        Must NOT divide by zero in RSI, ATR, or VWAP, and must produce finite numbers.
        """
        n = 80
        candles = [
            {"time": 1700000000 + i * 300, "open": 25000.0, "high": 25000.0, "low": 25000.0, "close": 25000.0, "volume": 100}
            for i in range(n)
        ]
        res = AngelClient.calculate_indicators(candles)
        assert res["candle_count"] == n
        assert res["ema_20"] == 25000.0
        assert res["ema_50"] == 25000.0
        assert res["rsi"] == 50.0
        assert res["vwap"] == 25000.0
        assert res["atr"] == 0.0
        assert res["last_close"] == 25000.0
        # Verify strictly finite
        for k, v in res.items():
            if isinstance(v, (int, float)):
                assert math.isfinite(v), f"Value for {k} was not finite: {v}"
                assert not math.isnan(v), f"Value for {k} was NaN: {v}"

    def test_indicators_zero_volume_vwap_fallback(self):
        """Spot indices report volume=0. VWAP must cleanly fall back to mean typical price without ZeroDivisionError."""
        candles = [
            {"time": 1700000000 + i * 300, "open": 25000.0, "high": 25050.0, "low": 24950.0, "close": 25000.0, "volume": 0}
            for i in range(30)
        ]
        res = AngelClient.calculate_indicators(candles)
        assert res["candle_count"] == 30
        assert math.isfinite(res["vwap"])
        assert res["vwap"] == 25000.0

    def test_indicators_missing_or_none_volume(self):
        """Candles with missing volume key or None volume must be coerced safely without TypeError."""
        candles = [
            {"time": 1700000000 + i * 300, "open": 25000.0, "high": 25020.0, "low": 24980.0, "close": 25010.0}
            if i % 2 == 0
            else {"time": 1700000000 + i * 300, "open": 25010.0, "high": 25030.0, "low": 24990.0, "close": 25020.0, "volume": None}
            for i in range(25)
        ]
        res = AngelClient.calculate_indicators(candles)
        assert res["candle_count"] == 25
        assert math.isfinite(res["vwap"])
        assert res["vwap"] > 0

    def test_indicators_string_volume_coercion(self):
        """Candles with string formatted volume (e.g. '1500') must be safely parsed as floats."""
        candles = [
            {"time": 1700000000 + i * 300, "open": 25000.0, "high": 25050.0, "low": 24950.0, "close": 25000.0, "volume": "2500"}
            for i in range(20)
        ]
        res = AngelClient.calculate_indicators(candles)
        assert res["candle_count"] == 20
        assert math.isfinite(res["vwap"])
        assert res["vwap"] == 25000.0

    def test_indicators_monotonic_rally_rsi_capped_at_100(self):
        """Monotonic rally with zero down candles: avg_loss is 0.0.
        
        RSI must cleanly evaluate to 100.0 without ZeroDivisionError or exceeding 100.
        """
        n = 60
        candles = [
            {"time": 1700000000 + i * 300, "open": 24000.0 + i * 15, "high": 24005.0 + i * 15, "low": 23995.0 + i * 15, "close": 24000.0 + (i + 1) * 15, "volume": 1000}
            for i in range(n)
        ]
        res = AngelClient.calculate_indicators(candles)
        assert res["rsi"] == 100.0
        assert res["higher_tf_trend"] == "BULLISH"
        assert res["last_close"] > res["ema_20"] > res["ema_50"]
        assert math.isfinite(res["ema_20"])
        assert math.isfinite(res["ema_50"])

    def test_indicators_monotonic_crash_rsi_floored_at_0(self):
        """Monotonic crash with zero up candles: avg_gain is 0.0.
        
        RSI must cleanly evaluate to 0.0 without going negative.
        """
        n = 60
        candles = [
            {"time": 1700000000 + i * 300, "open": 26000.0 - i * 15, "high": 26005.0 - i * 15, "low": 25990.0 - i * 15, "close": 26000.0 - (i + 1) * 15, "volume": 1000}
            for i in range(n)
        ]
        res = AngelClient.calculate_indicators(candles)
        assert res["rsi"] == 0.0
        assert res["higher_tf_trend"] == "BEARISH"
        assert res["last_close"] < res["ema_20"] < res["ema_50"]

    @pytest.mark.parametrize("count", [2, 14, 15, 19, 20, 49, 50, 150])
    def test_indicators_varying_candle_lengths(self, count):
        """Verify behavior at critical boundaries for indicators (N < 15 for RSI, N < 20 for EMA 20, N < 50 for EMA 50)."""
        candles = [
            {
                "time": 1700000000 + i * 300,
                "open": 25000.0 + math.sin(i / 5.0) * 50,
                "high": 25020.0 + math.sin(i / 5.0) * 50,
                "low": 24980.0 + math.sin(i / 5.0) * 50,
                "close": 25010.0 + math.sin(i / 5.0) * 50,
                "volume": 500,
            }
            for i in range(count)
        ]
        res = AngelClient.calculate_indicators(candles)
        assert res["candle_count"] == count
        assert math.isfinite(res["ema_20"])
        assert math.isfinite(res["ema_50"])
        assert math.isfinite(res["rsi"])
        assert 0.0 <= res["rsi"] <= 100.0
        assert math.isfinite(res["vwap"])
        assert math.isfinite(res["atr"])
        assert res["atr"] >= 0.0

        if count < 15:
            assert res["rsi"] == 50.0  # Default neutral RSI when insufficient periods

    def test_indicators_mathematical_precision_oracle(self):
        """Independent reference calculation oracle to verify exact mathematical formula implementation."""
        # Known deterministic series
        closes = [100.0 + (i % 7) * 3 - (i % 5) * 2 for i in range(60)]
        highs = [c + 2.0 for c in closes]
        lows = [c - 2.0 for c in closes]
        candles = [
            {"time": 1700000000 + i * 300, "open": closes[i], "high": highs[i], "low": lows[i], "close": closes[i], "volume": 100}
            for i in range(60)
        ]
        res = AngelClient.calculate_indicators(candles)

        # 1. Oracle EMA 20
        alpha_20 = 2.0 / 21.0
        ref_ema_20 = sum(closes[:20]) / 20.0
        for p in closes[20:]:
            ref_ema_20 = (p * alpha_20) + (ref_ema_20 * (1.0 - alpha_20))
        assert abs(res["ema_20"] - round(ref_ema_20, 2)) <= 0.02

        # 2. Oracle EMA 50
        alpha_50 = 2.0 / 51.0
        ref_ema_50 = sum(closes[:50]) / 50.0
        for p in closes[50:]:
            ref_ema_50 = (p * alpha_50) + (ref_ema_50 * (1.0 - alpha_50))
        assert abs(res["ema_50"] - round(ref_ema_50, 2)) <= 0.02

        # 3. Oracle RSI 14 (Wilder)
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(0.0, diff))
            losses.append(max(0.0, -diff))
        avg_g = sum(gains[:14]) / 14.0
        avg_l = sum(losses[:14]) / 14.0
        for i in range(14, len(gains)):
            avg_g = (avg_g * 13.0 + gains[i]) / 14.0
            avg_l = (avg_l * 13.0 + losses[i]) / 14.0
        rs = avg_g / avg_l
        ref_rsi = 100.0 - (100.0 / (1.0 + rs))
        assert abs(res["rsi"] - round(ref_rsi, 2)) <= 0.02

    def test_indicators_extreme_scale_and_whipsaw(self):
        """Extreme numbers (e.g. huge index values: 1e7, and violent 10% whipsaw)."""
        base = 10_000_000.0
        candles = [
            {
                "time": 1700000000 + i * 300,
                "open": base + (10000.0 if i % 2 == 0 else -10000.0),
                "high": base + 15000.0,
                "low": base - 15000.0,
                "close": base + (12000.0 if i % 2 == 0 else -12000.0),
                "volume": 50000,
            }
            for i in range(70)
        ]
        res = AngelClient.calculate_indicators(candles)
        assert math.isfinite(res["ema_20"])
        assert math.isfinite(res["ema_50"])
        assert math.isfinite(res["rsi"])
        assert 0.0 <= res["rsi"] <= 100.0
        assert res["atr"] > 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Vector 2: Angel One Resilience, Caching & Upstream Failure Handling
# ══════════════════════════════════════════════════════════════════════════════

class TestAngelOneResilienceAndThrottling:
    """Stress tests high-concurrency quote requests, caching, and upstream HTTP fault injection."""

    @pytest.mark.asyncio
    async def test_batch_quote_caching_burst_concurrency(self):
        """Simulate rapid burst of 50 concurrent calls to get_multi_quotes.
        
        Must hit in-memory cache and dispatch exactly ONE underlying HTTP request,
        preventing rate-limit 429 errors.
        """
        client = AngelClient(api_key="API_KEY", client_code="CODE", pin="1111", totp_secret="SECRET")
        client._jwt_token = "mock_jwt_test"
        client._token_expiry_epoch = time.time() + 3600

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": True,
            "data": {
                "fetched": [
                    {
                        "symbolToken": "99926000",
                        "tradingSymbol": "NIFTY",
                        "exchange": "NSE",
                        "ltp": 25050.25,
                        "open": 25000.0,
                        "high": 25100.0,
                        "low": 24950.0,
                        "close": 24980.0,
                        "netChange": 70.25,
                        "percentChange": 0.28,
                    },
                    {
                        "symbolToken": "99926009",
                        "tradingSymbol": "BANKNIFTY",
                        "exchange": "NSE",
                        "ltp": 52300.50,
                        "open": 52100.0,
                        "high": 52400.0,
                        "low": 52000.0,
                        "close": 52150.0,
                        "netChange": 150.50,
                        "percentChange": 0.29,
                    },
                ]
            },
        }

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
            # Concurrently launch 50 tasks
            tasks = [client.get_multi_quotes(["NIFTY", "BANKNIFTY"], ttl=3.0) for _ in range(50)]
            results = await asyncio.gather(*tasks)

            # All 50 results must be valid
            assert len(results) == 50
            for r in results:
                assert "NIFTY" in r
                assert "BANKNIFTY" in r
                assert r["NIFTY"]["spot_price"] == 25050.25
                assert r["BANKNIFTY"]["spot_price"] == 52300.50

            # Underlying HTTP post should be called exactly once due to caching
            assert mock_http.post.call_count == 1

    @pytest.mark.asyncio
    async def test_batch_quote_cache_ttl_invalidation(self):
        """Verify that cache hits occur within TTL (3s) and new network requests dispatch after TTL expiry."""
        client = AngelClient(api_key="API_KEY", client_code="CODE", pin="1111", totp_secret="SECRET")
        client._jwt_token = "mock_jwt_test"
        client._token_expiry_epoch = time.time() + 3600

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": True,
            "data": {
                "fetched": [
                    {
                        "symbolToken": "99926000",
                        "tradingSymbol": "NIFTY",
                        "exchange": "NSE",
                        "ltp": 25000.0,
                        "open": 25000.0,
                        "high": 25000.0,
                        "low": 25000.0,
                        "close": 25000.0,
                    }
                ]
            },
        }

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
            # Call 1: cold cache -> 1 HTTP request
            res1 = await client.get_multi_quotes(["NIFTY"], ttl=2.0)
            assert mock_http.post.call_count == 1

            # Call 2: within TTL -> 0 new HTTP requests
            res2 = await client.get_multi_quotes(["NIFTY"], ttl=2.0)
            assert mock_http.post.call_count == 1

            # Simulate passage of 2.1 seconds
            cached_time, cached_val = client._quote_cache["NIFTY"]
            client._quote_cache["NIFTY"] = (cached_time - 2.5, cached_val)

            # Call 3: after TTL -> 1 new HTTP request
            res3 = await client.get_multi_quotes(["NIFTY"], ttl=2.0)
            assert mock_http.post.call_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [500, 502, 503, 429])
    async def test_upstream_http_status_errors(self, status_code):
        """Verify upstream server errors (500, 502, 503, 429) do not crash client methods."""
        client = AngelClient(api_key="API_KEY", client_code="CODE", pin="1111", totp_secret="SECRET")
        client._jwt_token = "mock_jwt_test"
        client._token_expiry_epoch = time.time() + 3600

        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.text = f"Upstream error {status_code}"
        mock_resp.json.side_effect = Exception("Not a JSON response")

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
            # get_multi_quotes should return empty dict or fallback, NOT raise
            quotes = await client.get_multi_quotes(["NIFTY"])
            assert isinstance(quotes, dict)

            # get_candles should return empty list, NOT raise
            candles = await client.get_candles("NIFTY")
            assert candles == []

    @pytest.mark.asyncio
    async def test_upstream_corrupted_json_payload(self):
        """Verify upstream returning HTTP 200 with invalid non-JSON body is safely caught."""
        client = AngelClient(api_key="API_KEY", client_code="CODE", pin="1111", totp_secret="SECRET")
        client._jwt_token = "mock_jwt_test"
        client._token_expiry_epoch = time.time() + 3600

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>502 Bad Gateway Cloudflare</body></html>"
        mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
            quotes = await client.get_multi_quotes(["NIFTY"])
            assert isinstance(quotes, dict)
            candles = await client.get_candles("NIFTY")
            assert candles == []

    @pytest.mark.asyncio
    async def test_upstream_smartapi_status_false(self):
        """When SmartAPI returns 200 OK with status: false (e.g. AG8001 invalid session)."""
        client = AngelClient(api_key="API_KEY", client_code="CODE", pin="1111", totp_secret="SECRET")
        client._jwt_token = "mock_jwt_test"
        client._token_expiry_epoch = time.time() + 3600

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": False,
            "message": "Invalid Token",
            "errorcode": "AG8001",
            "data": None,
        }

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)

        with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
            quotes = await client.get_multi_quotes(["NIFTY"])
            assert isinstance(quotes, dict)

    @pytest.mark.asyncio
    async def test_upstream_network_timeout_resilience(self):
        """Verify network timeouts (httpx.ReadTimeout, httpx.ConnectError) are cleanly absorbed."""
        client = AngelClient(api_key="API_KEY", client_code="CODE", pin="1111", totp_secret="SECRET")
        client._jwt_token = "mock_jwt_test"
        client._token_expiry_epoch = time.time() + 3600

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.ReadTimeout("Timed out connecting to Angel One"))

        with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
            quotes = await client.get_multi_quotes(["NIFTY"])
            assert quotes == {}
            candles = await client.get_candles("NIFTY")
            assert candles == []

    @pytest.mark.asyncio
    async def test_automatic_reauth_on_401_unauthorized(self):
        """When SmartAPI returns 401 Unauthorized, client should re-authenticate and retry."""
        client = AngelClient(api_key="API_KEY", client_code="CODE", pin="1111", totp_secret="SECRET")
        client._jwt_token = "stale_jwt_token"
        client._token_expiry_epoch = time.time() + 3600

        resp_401 = MagicMock()
        resp_401.status_code = 401

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {
            "status": True,
            "data": {
                "fetched": [
                    {
                        "symbolToken": "99926000",
                        "tradingSymbol": "NIFTY",
                        "exchange": "NSE",
                        "ltp": 25120.0,
                        "open": 25000.0,
                        "high": 25150.0,
                        "low": 24980.0,
                        "close": 25000.0,
                        "netChange": 120.0,
                        "percentChange": 0.48,
                    }
                ]
            },
        }

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=[resp_401, resp_200])

        with patch.object(client, "get_http_client", AsyncMock(return_value=mock_http)):
            with patch.object(client, "authenticate", AsyncMock(return_value=True)) as mock_auth:
                quotes = await client.get_multi_quotes(["NIFTY"])
                assert mock_auth.called
                assert "NIFTY" in quotes
                assert quotes["NIFTY"]["spot_price"] == 25120.0

    @pytest.mark.asyncio
    async def test_unknown_symbols_handling(self):
        """Querying unregistered or nonsensical symbols must not raise exceptions."""
        client = AngelClient(api_key="API_KEY", client_code="CODE", pin="1111", totp_secret="SECRET")
        quotes = await client.get_multi_quotes(["NON_EXISTENT_INDEX_123", ""])
        assert quotes == {}
        quote_single = await client.get_quote("NON_EXISTENT_INDEX_123")
        assert quote_single is None
        candles = await client.get_candles("NON_EXISTENT_INDEX_123")
        assert candles == []

    @pytest.mark.asyncio
    async def test_build_market_snapshot_resilience_when_angel_fails(self):
        """When Angel One client fails completely, build_market_snapshot must fall back gracefully."""
        with patch("angel_client.get_angel_client") as mock_get_angel:
            mock_angel = AsyncMock()
            # is_configured is a synchronous method on AngelClient
            mock_angel.is_configured = MagicMock(return_value=True)
            mock_angel.get_quote.side_effect = Exception("Downstream SmartAPI failure")
            mock_get_angel.return_value = mock_angel

            snapshot = await build_market_snapshot("NIFTY", force_fresh=True)
            assert snapshot.spot_price > 0
            assert snapshot.ema_20 > 0
            assert math.isfinite(snapshot.rsi)


# ══════════════════════════════════════════════════════════════════════════════
# Vector 3: Strict 1-Lot Risk Guardrail Enforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskGuardrailEnforcement:
    """Stress tests strict 1-lot constraint, Rs 5,000 budget cap, and Rs 700 SL / Rs 1,000 Target limits."""

    # ── Multi-lot enforcement ──────────────────────────────────────────────────

    @pytest.mark.parametrize("lots", [2, 3, 5, 10, 50, 100, 0, -1])
    def test_guardrails_reject_multi_lot_directly(self, lots):
        """validate_paper_trade_guardrails must unconditionally reject lots != 1 with ValueError."""
        with pytest.raises(ValueError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=100.0, lots=lots)
        assert "Strict 1-lot order enforcement violated" in str(exc.value)

    @pytest.mark.parametrize("lots", [2, 4, 10, 50])
    @pytest.mark.asyncio
    async def test_manual_trade_api_rejects_multi_lot_orders(self, lots):
        """POST /api/trades/manual must reject multi-lot attempts with HTTP 400."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/trades/manual",
                json={
                    "symbol": "NIFTY",
                    "strike": 25000,
                    "option_type": "CE",
                    "lots": lots,
                    "entry_price": 50.0,
                },
            )
            assert res.status_code == 400
            assert f"Only 1-lot trading allowed (requested {lots} lots)" in res.json()["detail"]

    # ── Budget limit (Rs 5,000) enforcement ───────────────────────────────────

    def test_nifty_budget_boundary_exact_5000_vs_overrun(self):
        """For Nifty (lot size 25):
        - Max allowable entry premium = 5000 / 25 = 200.00.
        - Outlay at 200.00: 5000.00 -> APPROVED.
        - Outlay at 200.01: 5000.25 -> REJECTED with BudgetExceededError.
        """
        # Exactly Rs 5,000.00
        approved = validate_paper_trade_guardrails("NIFTY", entry_price=200.00, lots=1)
        assert approved["status"] == "APPROVED"
        assert approved["outlay"] == 5000.00

        # Rs 5,000.25 (exceeds budget by 25 paise)
        with pytest.raises(BudgetExceededError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=200.01, lots=1)
        assert "exceeds Rs 5000.00 limit" in str(exc.value)

    def test_banknifty_budget_boundary_exact_vs_overrun(self):
        """For Bank Nifty (lot size 15):
        - Outlay at 333.33: 4999.95 -> APPROVED.
        - Outlay at 333.34: 5000.10 -> REJECTED with BudgetExceededError.
        """
        approved = validate_paper_trade_guardrails("BANKNIFTY", entry_price=333.33, lots=1)
        assert approved["status"] == "APPROVED"
        assert approved["outlay"] == 4999.95

        with pytest.raises(BudgetExceededError) as exc:
            validate_paper_trade_guardrails("BANKNIFTY", entry_price=333.34, lots=1)
        assert "exceeds Rs 5000.00 limit" in str(exc.value)

    @pytest.mark.asyncio
    async def test_manual_trade_api_rejects_budget_overrun(self):
        """POST /api/trades/manual must reject trade where total_amount > Rs 5,000."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Nifty premium 205 * 25 = 5125 > 5000
            res = await client.post(
                "/api/trades/manual",
                json={
                    "symbol": "NIFTY",
                    "strike": 25000,
                    "option_type": "CE",
                    "lots": 1,
                    "entry_price": 205.0,
                },
            )
            assert res.status_code == 400
            assert "exceeds Rs 5,000 budget limit" in res.json()["detail"]

    @pytest.mark.parametrize("invalid_entry", [0.0, -10.0, -0.01])
    def test_guardrails_reject_non_positive_entry_price(self, invalid_entry):
        """Zero and negative entry prices must be rejected with ValueError."""
        with pytest.raises(ValueError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=invalid_entry, lots=1)
        assert "Must be strictly positive" in str(exc.value)

    # ── Stop-Loss and Target Guardrails (Max Rs 700 SL, Max Rs 1,000 Target) ───

    def test_nifty_stop_loss_risk_guardrail(self):
        """For Nifty (25 qty):
        - Max SL risk = Rs 700 -> Max SL distance = 28.00 pts.
        - At entry 100.0:
          - SL = 72.00 (loss = 28.00 * 25 = 700.00) -> APPROVED.
          - SL = 71.80 (loss = 28.20 * 25 = 705.00) -> REJECTED with RiskLimitExceededError.
        """
        approved = validate_paper_trade_guardrails("NIFTY", entry_price=100.0, lots=1, stop_loss=72.00)
        assert approved["status"] == "APPROVED"

        with pytest.raises(RiskLimitExceededError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=100.0, lots=1, stop_loss=71.80)
        assert "Stop loss risk Rs 705.00 exceeds maximum allowed limit of Rs 700.00" in str(exc.value)

    def test_nifty_target_profit_guardrail(self):
        """For Nifty (25 qty):
        - Max Target profit = Rs 1,000 -> Max Target distance = 40.00 pts.
        - At entry 100.0:
          - Target = 140.00 (profit = 40.00 * 25 = 1000.00) -> APPROVED.
          - Target = 140.20 (profit = 40.20 * 25 = 1005.00) -> REJECTED with RiskLimitExceededError.
        """
        approved = validate_paper_trade_guardrails("NIFTY", entry_price=100.0, lots=1, target=140.00)
        assert approved["status"] == "APPROVED"

        with pytest.raises(RiskLimitExceededError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=100.0, lots=1, target=140.20)
        assert "Target profit Rs 1005.00 exceeds maximum allowed limit of Rs 1000.00" in str(exc.value)

    def test_banknifty_stop_loss_and_target_risk_guardrails(self):
        """For Bank Nifty (15 qty):
        - Max SL distance = 46.66 pts (46.66 * 15 = 699.90 <= 700).
        - Max Target distance = 66.66 pts (66.66 * 15 = 999.90 <= 1000).
        - At entry 250.0:
          - SL 203.34 -> APPROVED.
          - SL 200.00 (loss = 50 * 15 = 750 > 700) -> REJECTED.
          - Target 316.66 -> APPROVED.
          - Target 320.00 (profit = 70 * 15 = 1050 > 1000) -> REJECTED.
        """
        approved = validate_paper_trade_guardrails(
            "BANKNIFTY", entry_price=250.0, lots=1, stop_loss=203.34, target=316.66
        )
        assert approved["status"] == "APPROVED"

        with pytest.raises(RiskLimitExceededError):
            validate_paper_trade_guardrails("BANKNIFTY", entry_price=250.0, lots=1, stop_loss=200.00)

        with pytest.raises(RiskLimitExceededError):
            validate_paper_trade_guardrails("BANKNIFTY", entry_price=250.0, lots=1, target=320.00)

    def test_stop_loss_and_target_direction_validation(self):
        """For BUY options: Stop-loss must be strictly BELOW entry price, and Target strictly ABOVE."""
        # SL >= Entry
        with pytest.raises(ValueError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=100.0, stop_loss=100.0)
        assert "must be strictly below entry price" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=100.0, stop_loss=105.0)
        assert "must be strictly below entry price" in str(exc.value)

        # Target <= Entry
        with pytest.raises(ValueError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=100.0, target=100.0)
        assert "must be strictly above entry price" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            validate_paper_trade_guardrails("NIFTY", entry_price=100.0, target=95.0)
        assert "must be strictly above entry price" in str(exc.value)

    def test_compute_clamped_sl_target_auto_clamping(self):
        """compute_clamped_sl_target must auto-clamp unsafe, inverted, or missing SL/Target to safe bounds."""
        entry = 150.0

        # Unsafe wide SL & Target -> clamped to maximum safe INR limits
        sl, tgt = compute_clamped_sl_target("NIFTY", entry_price=entry, stop_loss=50.0, target=300.0)
        assert sl == 150.0 - 28.0  # 122.0
        assert tgt == 150.0 + 40.0  # 190.0

        # None provided -> defaults to max allowable safe levels
        sl_none, tgt_none = compute_clamped_sl_target("NIFTY", entry_price=entry, stop_loss=None, target=None)
        assert sl_none == 122.0
        assert tgt_none == 190.0

        # Inverted SL (SL > entry) -> clamped to safe SL
        sl_inv, _ = compute_clamped_sl_target("NIFTY", entry_price=entry, stop_loss=160.0)
        assert sl_inv == 122.0

        # Inverted Target (Target < entry) -> clamped to safe Target
        _, tgt_inv = compute_clamped_sl_target("NIFTY", entry_price=entry, target=140.0)
        assert tgt_inv == 190.0

    # ── AngelClient.place_order Adapter Boundary Verification ──────────────────

    @pytest.mark.asyncio
    async def test_angel_client_place_order_adapter_validation(self):
        """Empirically test AngelClient.place_order parameter validation:
        - Rejects negative/zero quantity.
        - Rejects invalid transaction types.
        - Documents that AngelClient is a low-level broker transport adapter,
          while business risk guardrails (1-lot, Rs 5,000 budget, SL/Target clamping)
          are enforced at the Application/Domain layer (main.py, debate_engine.py, config.py).
        """
        client = AngelClient()

        # 1. Invalid transaction_type rejected
        with pytest.raises(ValueError) as exc_trans:
            await client.place_order("NIFTY", transaction_type="HOLD", quantity=25, is_paper=True)
        assert "Invalid transaction_type" in str(exc_trans.value)

        # 2. Non-positive quantity rejected
        with pytest.raises(ValueError) as exc_qty:
            await client.place_order("NIFTY", transaction_type="BUY", quantity=0, is_paper=True)
        assert "Invalid quantity" in str(exc_qty.value)

        with pytest.raises(ValueError) as exc_neg:
            await client.place_order("NIFTY", transaction_type="BUY", quantity=-10, is_paper=True)
        assert "Invalid quantity" in str(exc_neg.value)

        # 3. Valid paper order executes and records state
        res = await client.place_order("NIFTY", transaction_type="BUY", quantity=25, price=100.0, is_paper=True)
        assert res["status"] == "success"
        assert res["mode"] == "PAPER"
        assert res["data"]["quantity"] == 25
