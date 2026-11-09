"""
Empirical Challenger Test Suite for Milestone 1:
- Mission 1: Concurrency and Cross-Symbol Isolation (NIFTY vs BANKNIFTY interleaving)
- Mission 2: Complete Yahoo Finance Elimination Verification (code scan + DNS failure mock)
- Mission 3: API Endpoint Verification (/api/status, /api/market/candles, /api/market/option-chain)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import socket
import sys
from typing import Any
from unittest.mock import patch

import httpx
from httpx import ASGITransport, AsyncClient
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dhan_client import DhanClient, get_dhan_client
from main import app
from market_data import (
    build_market_snapshot,
    calculate_atm_strike,
    generate_option_chain,
    get_option_chain,
)
from openalgo_client import OpenAlgoClient


# ══════════════════════════════════════════════════════════════════════════════
# Mission 1: Concurrency and Cross-Symbol Isolation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_concurrent_build_market_snapshot_isolation():
    """Execute concurrent requests interleaving NIFTY and BANKNIFTY across build_market_snapshot.
    Verify NIFTY returns spot ~24,500-25,500 and BANKNIFTY returns spot ~51,000-53,000 without cross-contamination.
    """
    symbols = ["NIFTY", "BANKNIFTY"] * 25  # 50 interleaved tasks
    random.shuffle(symbols)

    async def worker(sym: str, idx: int):
        # Introduce tiny random async jitter to provoke race conditions
        await asyncio.sleep(random.uniform(0.001, 0.008))
        snap = await build_market_snapshot(symbol=sym, force_fresh=True)
        return sym, idx, snap

    tasks = [worker(sym, i) for i, sym in enumerate(symbols)]
    results = await asyncio.gather(*tasks)

    for sym, idx, snap in results:
        spot = snap.spot_price
        atm = int(snap.atm_strike)
        if sym == "NIFTY":
            assert 24000.0 <= spot <= 26000.0, f"Task {idx}: NIFTY spot {spot} out of range"
            assert atm % 50 == 0, f"Task {idx}: NIFTY ATM {atm} not multiple of 50"
            assert snap.atr < 250.0, f"Task {idx}: NIFTY ATR {snap.atr} resembles Bank Nifty"
        elif sym == "BANKNIFTY":
            assert 50000.0 <= spot <= 54000.0, f"Task {idx}: BANKNIFTY spot {spot} out of range"
            assert atm % 100 == 0, f"Task {idx}: BANKNIFTY ATM {atm} not multiple of 100"
            assert snap.atr > 250.0, f"Task {idx}: BANKNIFTY ATR {snap.atr} resembles Nifty"


@pytest.mark.asyncio
async def test_concurrent_get_option_chain_isolation():
    """Execute concurrent requests interleaving NIFTY and BANKNIFTY across get_option_chain.
    Verify strikes, lot sizes, step sizes, and symbols are strictly isolated.
    """
    symbols = ["NIFTY", "BANKNIFTY"] * 25  # 50 interleaved tasks
    random.shuffle(symbols)

    async def worker(sym: str, idx: int):
        await asyncio.sleep(random.uniform(0.001, 0.008))
        chain = await get_option_chain(symbol=sym, strikes=7)
        return sym, idx, chain

    tasks = [worker(sym, i) for i, sym in enumerate(symbols)]
    results = await asyncio.gather(*tasks)

    for sym, idx, chain in results:
        assert len(chain) == 15, f"Task {idx}: expected 15 strikes for {sym}, got {len(chain)}"
        strikes = [row["strike"] for row in chain]

        if sym == "NIFTY":
            for i, row in enumerate(chain):
                assert row["lot_size"] == 25, f"Task {idx} row {i}: NIFTY lot_size must be 25, got {row['lot_size']}"
                assert row["strike"] % 50 == 0, f"Task {idx} row {i}: NIFTY strike {row['strike']} not step 50"
                assert row["ce"]["symbol"].startswith("NIFTY"), f"Task {idx} row {i}: CE symbol corrupted"
                assert row["pe"]["symbol"].startswith("NIFTY"), f"Task {idx} row {i}: PE symbol corrupted"
                assert row["ce"]["within_budget"] == (row["ce"]["lot_amount"] <= 5000.0)
                assert row["pe"]["within_budget"] == (row["pe"]["lot_amount"] <= 5000.0)
            # Step difference check
            for a, b in zip(strikes[:-1], strikes[1:]):
                assert b - a == 50, f"Task {idx}: adjacent strikes difference not 50 for NIFTY"

        elif sym == "BANKNIFTY":
            for i, row in enumerate(chain):
                assert row["lot_size"] == 15, f"Task {idx} row {i}: BANKNIFTY lot_size must be 15, got {row['lot_size']}"
                assert row["strike"] % 100 == 0, f"Task {idx} row {i}: BANKNIFTY strike {row['strike']} not step 100"
                assert row["ce"]["symbol"].startswith("BANKNIFTY"), f"Task {idx} row {i}: CE symbol corrupted"
                assert row["pe"]["symbol"].startswith("BANKNIFTY"), f"Task {idx} row {i}: PE symbol corrupted"
                assert row["ce"]["within_budget"] == (row["ce"]["lot_amount"] <= 5000.0)
                assert row["pe"]["within_budget"] == (row["pe"]["lot_amount"] <= 5000.0)
            # Step difference check
            for a, b in zip(strikes[:-1], strikes[1:]):
                assert b - a == 100, f"Task {idx}: adjacent strikes difference not 100 for BANKNIFTY"


@pytest.mark.asyncio
async def test_concurrent_api_market_candles_isolation():
    """Execute concurrent API calls interleaving NIFTY and BANKNIFTY on /api/market/candles.
    Verify clean separation of candle data, symbols, and spot ranges.
    """
    transport = ASGITransport(app=app)
    symbols = ["NIFTY", "BANKNIFTY"] * 25  # 50 tasks
    random.shuffle(symbols)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async def call_candles(sym: str, idx: int):
            await asyncio.sleep(random.uniform(0.001, 0.008))
            res = await client.get(f"/api/market/candles?symbol={sym}&interval=5m&range_period=1d")
            return sym, idx, res

        tasks = [call_candles(sym, i) for i, sym in enumerate(symbols)]
        results = await asyncio.gather(*tasks)

        for sym, idx, res in results:
            assert res.status_code == 200, f"Task {idx} returned {res.status_code}"
            data = res.json()
            assert data["status"] == "success"
            assert data["symbol"] == sym
            candles = data["candles"]
            assert len(candles) > 0, f"Task {idx}: empty candles"

            # Check prices
            last_candle = candles[-1]
            close_price = last_candle["close"]
            if sym == "NIFTY":
                assert 23000.0 <= close_price <= 27000.0, f"Task {idx}: NIFTY candle close {close_price} wrong"
            elif sym == "BANKNIFTY":
                assert 49000.0 <= close_price <= 55000.0, f"Task {idx}: BANKNIFTY candle close {close_price} wrong"

            # Verify ascending timestamps
            timestamps = [c["time"] for c in candles]
            assert timestamps == sorted(timestamps), f"Task {idx}: timestamps not sorted"


@pytest.mark.asyncio
async def test_concurrent_interleaved_multi_endpoint_cross_fire():
    """Stress test mixing snapshot, option chain, candles, and LTP calls simultaneously."""
    transport = ASGITransport(app=app)
    endpoints = ["snapshot", "chain", "candles", "status"] * 15  # 60 operations
    random.shuffle(endpoints)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async def execute_op(op: str, idx: int):
            sym = "NIFTY" if (idx % 2 == 0) else "BANKNIFTY"
            await asyncio.sleep(random.uniform(0.001, 0.005))
            if op == "snapshot":
                snap = await build_market_snapshot(sym)
                return op, sym, snap.spot_price
            elif op == "chain":
                chain = await get_option_chain(sym, strikes=5)
                return op, sym, chain[0]["lot_size"]
            elif op == "candles":
                r = await client.get(f"/api/market/candles?symbol={sym}")
                return op, sym, r.json()["candles"][-1]["close"]
            elif op == "status":
                r = await client.get("/api/status")
                return op, sym, r.json()["dhan_status"]

        tasks = [execute_op(op, i) for i, op in enumerate(endpoints)]
        results = await asyncio.gather(*tasks)

        for op, sym, val in results:
            if op == "snapshot":
                if sym == "NIFTY":
                    assert 24000.0 <= val <= 26000.0
                else:
                    assert 50000.0 <= val <= 54000.0
            elif op == "chain":
                if sym == "NIFTY":
                    assert val == 25
                else:
                    assert val == 15
            elif op == "candles":
                if sym == "NIFTY":
                    assert 23000.0 <= val <= 27000.0
                else:
                    assert 49000.0 <= val <= 55000.0
            elif op == "status":
                assert val in ("LIVE", "FALLBACK")


# ══════════════════════════════════════════════════════════════════════════════
# Mission 2: Complete Yahoo Finance Elimination Verification
# ══════════════════════════════════════════════════════════════════════════════

def test_zero_yahoo_in_backend_source_code_and_comments():
    """Scan all backend production source files for residual Yahoo references."""
    backend_src_files = [
        p for p in BACKEND_DIR.glob("*.py")
        if not p.name.startswith("test_")
    ]
    assert len(backend_src_files) >= 5, f"Expected at least 5 source files in {BACKEND_DIR}"

    forbidden_patterns = [
        re.compile(r"yahoo", re.IGNORECASE),
        re.compile(r"query1\.finance", re.IGNORECASE),
        re.compile(r"%5ENSEI", re.IGNORECASE),
    ]

    violations = []
    for fpath in backend_src_files:
        content = fpath.read_text(encoding="utf-8")
        for line_no, line in enumerate(content.splitlines(), start=1):
            for pat in forbidden_patterns:
                if pat.search(line):
                    violations.append(f"{fpath.name}:{line_no} -> {line.strip()}")

    assert violations == [], f"Found residual Yahoo Finance references in production code:\n" + "\n".join(violations)


def test_zero_yahoo_in_frontend_source_code():
    """Scan frontend source code for any residual Yahoo calls or references."""
    frontend_src = BACKEND_DIR.parent / "frontend" / "src"
    if not frontend_src.exists():
        pytest.skip("frontend/src not found in workspace")

    forbidden = re.compile(r"yahoo", re.IGNORECASE)
    violations = []
    for fpath in frontend_src.rglob("*"):
        if fpath.suffix in (".js", ".jsx", ".ts", ".tsx", ".html", ".css"):
            content = fpath.read_text(encoding="utf-8")
            for line_no, line in enumerate(content.splitlines(), start=1):
                if forbidden.search(line):
                    violations.append(f"{fpath.relative_to(frontend_src)}:{line_no} -> {line.strip()}")

    assert violations == [], f"Found residual Yahoo references in frontend/src:\n" + "\n".join(violations)


@pytest.mark.asyncio
async def test_dns_failure_mock_query1_finance_yahoo_com():
    """Mock network DNS resolution so that any attempt to resolve query1.finance.yahoo.com
    (or any *.yahoo.com domain) raises socket.gaierror.
    Verify that 100% of endpoints and market data functions continue functioning without network errors.
    """
    orig_getaddrinfo = socket.getaddrinfo
    orig_gethostbyname = socket.gethostbyname

    def blocked_getaddrinfo(host, port, *args, **kwargs):
        if "yahoo" in str(host).lower():
            raise socket.gaierror(-2, f"Empirical Challenger blocked DNS resolution for {host}")
        return orig_getaddrinfo(host, port, *args, **kwargs)

    def blocked_gethostbyname(host):
        if "yahoo" in str(host).lower():
            raise socket.gaierror(-2, f"Empirical Challenger blocked DNS resolution for {host}")
        return orig_gethostbyname(host)

    with patch("socket.getaddrinfo", side_effect=blocked_getaddrinfo), \
         patch("socket.gethostbyname", side_effect=blocked_gethostbyname):

        # 1. DhanClient functions
        dhan = get_dhan_client()
        nifty_ltp = await dhan.get_ltp("NIFTY")
        bank_ltp = await dhan.get_ltp("BANKNIFTY")
        assert 24000.0 <= nifty_ltp <= 26000.0
        assert 50000.0 <= bank_ltp <= 54000.0

        nifty_chain = await dhan.get_option_chain("NIFTY", num_strikes=5)
        assert len(nifty_chain) == 11
        bank_chain = await dhan.get_option_chain("BANKNIFTY", num_strikes=5)
        assert len(bank_chain) == 11

        n_candles = await dhan.get_candles("NIFTY", interval="5m")
        assert len(n_candles) > 0
        b_candles = await dhan.get_candles("BANKNIFTY", interval="5m")
        assert len(b_candles) > 0

        # 2. Market data service
        n_snap = await build_market_snapshot("NIFTY", force_fresh=True)
        b_snap = await build_market_snapshot("BANKNIFTY", force_fresh=True)
        assert n_snap.spot_price > 0
        assert b_snap.spot_price > 0

        # 3. OpenAlgo client fallback
        oa = OpenAlgoClient()
        oa_snap = await oa.get_market_snapshot("NIFTY")
        assert oa_snap["spot_price"] > 0

        # 4. HTTP API endpoints via ASGITransport
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res_status = await client.get("/api/status")
            assert res_status.status_code == 200
            st = res_status.json()
            assert st["dhan_status"] in ("LIVE", "FALLBACK")

            res_ncandles = await client.get("/api/market/candles?symbol=NIFTY")
            assert res_ncandles.status_code == 200
            assert len(res_ncandles.json()["candles"]) > 0

            res_bcandles = await client.get("/api/market/candles?symbol=BANKNIFTY")
            assert res_bcandles.status_code == 200
            assert len(res_bcandles.json()["candles"]) > 0

            res_nchain = await client.get("/api/market/option-chain?symbol=NIFTY")
            assert res_nchain.status_code == 200
            assert res_nchain.json()["lot_size"] == 25

            res_bchain = await client.get("/api/market/option-chain?symbol=BANKNIFTY")
            assert res_bchain.status_code == 200
            assert res_bchain.json()["lot_size"] == 15


# ══════════════════════════════════════════════════════════════════════════════
# Mission 3: API Endpoint Verification & Schemas
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_api_status_schema_and_dhan_metadata():
    """Verify /api/status endpoint response schema and presence of dhan_* fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/status")
        assert res.status_code == 200
        payload = res.json()

        # Mandatory Dhan connection monitor fields
        assert "dhan_status" in payload, "Missing dhan_status"
        assert payload["dhan_status"] in ("LIVE", "FALLBACK", "DISCONNECTED"), f"Invalid dhan_status: {payload['dhan_status']}"

        assert "dhan_source" in payload, "Missing dhan_source"
        assert isinstance(payload["dhan_source"], str), f"dhan_source must be str, got {type(payload['dhan_source'])}"

        assert "dhan_tier" in payload, "Missing dhan_tier"
        assert payload["dhan_tier"] in (1, 2, 3), f"dhan_tier must be 1, 2, or 3, got {payload['dhan_tier']}"

        assert "dhan_connected" in payload, "Missing dhan_connected"
        assert isinstance(payload["dhan_connected"], bool)

        assert "dhan_details" in payload, "Missing dhan_details"

        # Multi-index spot prices
        assert "nifty_spot" in payload, "Missing nifty_spot"
        assert 24000.0 <= payload["nifty_spot"] <= 26000.0, f"nifty_spot out of range: {payload['nifty_spot']}"

        assert "banknifty_spot" in payload, "Missing banknifty_spot"
        assert 50000.0 <= payload["banknifty_spot"] <= 54000.0, f"banknifty_spot out of range: {payload['banknifty_spot']}"

        # Position sizing and budget indicators
        assert payload.get("budget_limit") == 5000.0
        assert payload.get("lot_size") == 25
        assert payload.get("selected_index") in ("NIFTY", "BANKNIFTY")


@pytest.mark.asyncio
async def test_api_market_candles_schema_and_integrity():
    """Verify /api/market/candles endpoint for NIFTY and BANKNIFTY."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for sym, exp_low, exp_high in [("NIFTY", 23000.0, 27000.0), ("BANKNIFTY", 49000.0, 55000.0)]:
            res = await client.get(f"/api/market/candles?symbol={sym}&interval=5m&range_period=5d")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert data["symbol"] == sym
            candles = data["candles"]
            assert len(candles) >= 50, f"Expected >=50 candles, got {len(candles)}"

            for i, c in enumerate(candles):
                assert "time" in c and isinstance(c["time"], int)
                assert "open" in c and c["open"] > 0
                assert "high" in c and c["high"] >= max(c["open"], c["close"])
                assert "low" in c and c["low"] <= min(c["open"], c["close"])
                assert "close" in c and exp_low <= c["close"] <= exp_high

            # Monotonic ascending time
            times = [c["time"] for c in candles]
            assert times == sorted(times), f"Candle times not sorted ascending for {sym}"


@pytest.mark.asyncio
async def test_api_market_option_chain_schema_and_budget_guardrails():
    """Verify /api/market/option-chain endpoint and within_budget calculation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # NIFTY test
        res_n = await client.get("/api/market/option-chain?symbol=NIFTY&strikes=7")
        assert res_n.status_code == 200
        n_data = res_n.json()
        assert n_data["status"] == "success"
        assert n_data["symbol"] == "NIFTY"
        assert n_data["lot_size"] == 25
        assert len(n_data["chain"]) == 15

        for row in n_data["chain"]:
            strike = row["strike"]
            assert strike % 50 == 0
            ce = row["ce"]
            pe = row["pe"]
            # Validate within_budget math
            expected_ce_budget = (ce["lot_amount"] <= 5000.0)
            expected_pe_budget = (pe["lot_amount"] <= 5000.0)
            assert ce["within_budget"] == expected_ce_budget, f"NIFTY CE budget mismatch at strike {strike}"
            assert pe["within_budget"] == expected_pe_budget, f"NIFTY PE budget mismatch at strike {strike}"

        # BANKNIFTY test
        res_b = await client.get("/api/market/option-chain?symbol=BANKNIFTY&strikes=7")
        assert res_b.status_code == 200
        b_data = res_b.json()
        assert b_data["status"] == "success"
        assert b_data["symbol"] == "BANKNIFTY"
        assert b_data["lot_size"] == 15
        assert len(b_data["chain"]) == 15

        for row in b_data["chain"]:
            strike = row["strike"]
            assert strike % 100 == 0
            ce = row["ce"]
            pe = row["pe"]
            expected_ce_budget = (ce["lot_amount"] <= 5000.0)
            expected_pe_budget = (pe["lot_amount"] <= 5000.0)
            assert ce["within_budget"] == expected_ce_budget, f"BANKNIFTY CE budget mismatch at strike {strike}"
            assert pe["within_budget"] == expected_pe_budget, f"BANKNIFTY PE budget mismatch at strike {strike}"


@pytest.mark.asyncio
async def test_api_market_option_chain_boundary_validations():
    """Test boundary conditions for option-chain endpoint parameters."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Minimum strikes: 3 -> 2*3 + 1 = 7 strikes
        res_min = await client.get("/api/market/option-chain?symbol=NIFTY&strikes=3")
        assert res_min.status_code == 200
        assert len(res_min.json()["chain"]) == 7

        # Maximum strikes: 15 -> 2*15 + 1 = 31 strikes
        res_max = await client.get("/api/market/option-chain?symbol=NIFTY&strikes=15")
        assert res_max.status_code == 200
        assert len(res_max.json()["chain"]) == 31

        # Violate ge=3: strikes=2 -> 422
        res_invalid_low = await client.get("/api/market/option-chain?symbol=NIFTY&strikes=2")
        assert res_invalid_low.status_code == 422

        # Violate le=15: strikes=16 -> 422
        res_invalid_high = await client.get("/api/market/option-chain?symbol=NIFTY&strikes=16")
        assert res_invalid_high.status_code == 422

        # Case insensitive symbol
        res_lower = await client.get("/api/market/option-chain?symbol=banknifty&strikes=5")
        assert res_lower.status_code == 200
        assert res_lower.json()["lot_size"] == 15


# ══════════════════════════════════════════════════════════════════════════════
# Mission 4: Deep Adversarial Stress & Failure Mode Mining
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_disk_cache_corruption_and_concurrency_recovery(tmp_path):
    """Test disk cache recovery under concurrent reads when market_cache.json is corrupted."""
    corrupt_cache = tmp_path / "market_cache.json"
    corrupt_cache.write_text("CORRUPTED_JSON_DATA_NON_PARSEABLE", encoding="utf-8")

    dhan = DhanClient(cache_file=corrupt_cache)

    async def fetch(sym: str):
        await asyncio.sleep(random.uniform(0.001, 0.005))
        return await dhan.get_ltp(sym)

    symbols = ["NIFTY", "BANKNIFTY"] * 10
    tasks = [fetch(s) for s in symbols]
    spots = await asyncio.gather(*tasks)

    for sym, spot in zip(symbols, spots):
        if sym == "NIFTY":
            assert 24000.0 <= spot <= 26000.0
        else:
            assert 50000.0 <= spot <= 54000.0

    # Verify disk cache self-healed into valid JSON
    assert corrupt_cache.exists()
    recovered_data = json.loads(corrupt_cache.read_text(encoding="utf-8"))
    assert "indices" in recovered_data
    assert "NIFTY" in recovered_data["indices"]
    assert "BANKNIFTY" in recovered_data["indices"]


@pytest.mark.asyncio
async def test_high_stress_100_concurrent_requests():
    """Execute 100 concurrent asynchronous requests rapidly alternating NIFTY and BANKNIFTY."""
    symbols = ["NIFTY", "BANKNIFTY"] * 50
    random.shuffle(symbols)

    async def stress_worker(sym: str, i: int):
        snap = await build_market_snapshot(symbol=sym)
        chain = await get_option_chain(symbol=sym, strikes=5)
        return sym, snap.spot_price, chain[0]["lot_size"], [r["strike"] for r in chain]

    tasks = [stress_worker(s, i) for i, s in enumerate(symbols)]
    results = await asyncio.gather(*tasks)

    for sym, spot, lot, strikes in results:
        if sym == "NIFTY":
            assert 24000.0 <= spot <= 26000.0
            assert lot == 25
            for a, b in zip(strikes[:-1], strikes[1:]):
                assert b - a == 50
        elif sym == "BANKNIFTY":
            assert 50000.0 <= spot <= 54000.0
            assert lot == 15
            for a, b in zip(strikes[:-1], strikes[1:]):
                assert b - a == 100


def test_black_scholes_engine_greeks_invariants():
    """Verify analytical Black-Scholes Greeks invariants across ITM, ATM, and OTM strikes."""
    from dhan_client import BlackScholesEngine

    spot = 25000.0
    strikes = [24000.0, 24500.0, 25000.0, 25500.0, 26000.0]
    t_years = 3.0 / 365.0  # 3 days to expiry

    for strike in strikes:
        ce = BlackScholesEngine.calculate_greeks(spot, strike, t_years, iv=0.15, option_type="CE")
        pe = BlackScholesEngine.calculate_greeks(spot, strike, t_years, iv=0.15, option_type="PE")

        # Call Delta in [0, 1]
        assert 0.0 <= ce["delta"] <= 1.0, f"CE delta out of bounds: {ce['delta']}"
        # Put Delta in [-1, 0]
        assert -1.0 <= pe["delta"] <= 0.0, f"PE delta out of bounds: {pe['delta']}"
        # Put-Call Delta parity: Delta_CE - Delta_PE ~= 1.0
        assert abs((ce["delta"] - pe["delta"]) - 1.0) < 0.05, "Put-Call Delta parity violated"
        # Gamma > 0
        assert ce["gamma"] > 0, f"CE gamma must be positive, got {ce['gamma']}"
        assert pe["gamma"] > 0, f"PE gamma must be positive, got {pe['gamma']}"
        # Vega > 0
        assert ce["vega"] > 0, f"CE vega must be positive, got {ce['vega']}"
        assert pe["vega"] > 0, f"PE vega must be positive, got {pe['vega']}"
        # Theta: CE theta is strictly negative across all strikes
        assert ce["theta"] < 0, f"CE theta must be negative, got {ce['theta']}"
        # PE theta is negative for ATM and OTM strikes (strike <= spot)
        if strike <= spot:
            assert pe["theta"] < 0, f"ATM/OTM PE theta must be negative, got {pe['theta']}"
        else:
            # Deep ITM European put options have positive theta due to strike discounting (+r*K*e^-rT*N(-d2))
            assert pe["theta"] >= -5.0, f"ITM PE theta unexpected: {pe['theta']}"
        # Prices strictly positive
        assert ce["price"] >= 0.5
        assert pe["price"] >= 0.5


@pytest.mark.asyncio
async def test_dhan_connection_status_under_unsubscribed_token():
    """Verify status diagnostics when Dhan returns HTTP 401 code 806 (Data APIs not Subscribed)."""
    from dhan_client import DhanDataAPIUnsubscribedError
    from unittest.mock import MagicMock

    dhan = DhanClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"errorCode": "806", "errorMessage": "Data APIs not Subscribed"}
    mock_resp.text = json.dumps({"errorCode": "806", "errorMessage": "Data APIs not Subscribed"})

    with pytest.raises(DhanDataAPIUnsubscribedError):
        dhan._parse_and_raise_error(mock_resp, "/v2/marketfeed/quote")

    status = await dhan.get_connection_status()
    assert status["status"] == "FALLBACK"
    assert status["dhan_status"] == "FALLBACK"
    assert status["dhan_connected"] is False
    assert status["data_api_subscribed"] is False
    assert "806" in status["details"]
