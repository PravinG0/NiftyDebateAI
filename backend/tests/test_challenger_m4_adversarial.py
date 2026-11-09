"""
Challenger 2 Adversarial Stress Test Suite: Milestone 4 Gate (Tier 5 Adversarial Coverage Hardening).

Conducts empirical white-box testing of edge cases across the integrated codebase:
1. Malformed symbols (lowercase, mixed case, trailing whitespace, invalid indices like 'FINNIFTY').
2. Strike rounding and step boundaries across edge spot prices.
3. Exact Rs 5,000.00 outlay vs Rs 5,000.25 outlay.
4. Offline fallback transition: corrupt market_cache.json and simulated network errors.
5. Midnight IST transitions and weekend market-hours checks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from config import (
    BUDGET_LIMIT,
    INDEX_CONFIG,
    BudgetExceededError,
    validate_paper_trade_guardrails,
)
from dhan_client import (
    BlackScholesEngine,
    DhanClient,
    DiskCacheManager,
    MarketSimulator,
)
from market_data import (
    calculate_atm_strike,
    generate_option_chain,
    parse_option_symbol,
)
from time_utils import (
    IST,
    calculate_countdown_seconds,
    format_countdown_timer,
    format_ist_display,
    is_market_open_ist,
    is_scheduler_window_ist,
    to_ist,
)

# ══════════════════════════════════════════════════════════════════════════════
# 1. Malformed Symbols & Normalization Probes
# ══════════════════════════════════════════════════════════════════════════════

def test_guardrails_symbol_case_insensitivity():
    """Verify validate_paper_trade_guardrails correctly accepts lowercase and mixed case."""
    res_lower = validate_paper_trade_guardrails("nifty", 100.0, 1)
    assert res_lower["symbol"] == "NIFTY"
    assert res_lower["status"] == "APPROVED"

    res_mixed = validate_paper_trade_guardrails("BankNifty", 200.0, 1)
    assert res_mixed["symbol"] == "BANKNIFTY"
    assert res_mixed["status"] == "APPROVED"


def test_guardrails_invalid_index_rejected():
    """Verify validate_paper_trade_guardrails explicitly raises ValueError for unsupported indices."""
    with pytest.raises(ValueError, match="Invalid index symbol: FINNIFTY"):
        validate_paper_trade_guardrails("FINNIFTY", 100.0, 1)

    with pytest.raises(ValueError, match="Invalid index symbol: SENSEX"):
        validate_paper_trade_guardrails("SENSEX", 100.0, 1)


def test_market_simulator_finnifty_unhandled_keyerror_vulnerability():
    """Empirically prove that MarketSimulator.generate_snapshot_dict raises KeyError on unhandled index."""
    sim = MarketSimulator()
    with pytest.raises(KeyError) as exc_info:
        sim.generate_snapshot_dict("FINNIFTY")
    assert "FINNIFTY" in str(exc_info.value)


def test_market_simulator_whitespace_keyerror_vulnerability():
    """Empirically prove that MarketSimulator.generate_snapshot_dict raises KeyError on whitespace symbols."""
    sim = MarketSimulator()
    with pytest.raises(KeyError) as exc_info:
        sim.generate_snapshot_dict("  NIFTY  ")
    assert "  NIFTY  " in str(exc_info.value)


def test_parse_option_symbol_malformed_inputs():
    """Verify parse_option_symbol extracts strike and type or gracefully falls back on malformed inputs."""
    assert parse_option_symbol("NIFTY26111224500CE") == (24500, "CE")
    assert parse_option_symbol("BANKNIFTY26111252100PE") == (52100, "PE")
    assert parse_option_symbol("24800 PE") == (24800, "PE")
    assert parse_option_symbol("52000CE") == (52000, "CE")
    # Empty or digitless string falls back safely
    assert parse_option_symbol("") == (24500, "CE")
    assert parse_option_symbol("INVALID") == (24500, "CE")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Strike Rounding & Step Boundaries Across Spot Prices
# ══════════════════════════════════════════════════════════════════════════════

def test_strike_rounding_boundaries_nifty():
    """Verify ATM strike calculations for NIFTY (step=50) across boundary spot prices."""
    assert calculate_atm_strike(24999.99, step=50) == 25000
    assert calculate_atm_strike(25000.00, step=50) == 25000
    assert calculate_atm_strike(25024.99, step=50) == 25000
    # Halfway boundary rounding (bankers rounding to even: 25025.0 / 50 = 500.5 -> 500)
    assert calculate_atm_strike(25025.00, step=50) == 25000
    assert calculate_atm_strike(25025.01, step=50) == 25050


def test_strike_rounding_boundaries_banknifty():
    """Verify ATM strike calculations for BANKNIFTY (step=100) across boundary spot prices."""
    assert calculate_atm_strike(52049.99, step=100) == 52000
    assert calculate_atm_strike(52050.00, step=100) == 52000
    assert calculate_atm_strike(52050.01, step=100) == 52100
    assert calculate_atm_strike(52099.99, step=100) == 52100
    assert calculate_atm_strike(52100.00, step=100) == 52100


def test_option_chain_ladder_increments():
    """Verify generated option chain ladder strictly preserves strike steps."""
    nifty_chain = generate_option_chain(25010.5, lot_size=25, num_strikes=5, step=50, symbol="NIFTY")
    nifty_strikes = [c["strike"] for c in nifty_chain]
    assert len(nifty_strikes) == 11
    assert all(nifty_strikes[i+1] - nifty_strikes[i] == 50 for i in range(len(nifty_strikes)-1))

    bank_chain = generate_option_chain(52140.8, lot_size=15, num_strikes=5, step=100, symbol="BANKNIFTY")
    bank_strikes = [c["strike"] for c in bank_chain]
    assert len(bank_strikes) == 11
    assert all(bank_strikes[i+1] - bank_strikes[i] == 100 for i in range(len(bank_strikes)-1))


# ══════════════════════════════════════════════════════════════════════════════
# 3. Exact Rs 5,000.00 Outlay vs Rs 5,000.25 Outlay
# ══════════════════════════════════════════════════════════════════════════════

def test_budget_exact_boundary_nifty():
    """Verify Nifty Rs 5,000.00 outlay is allowed and Rs 5,000.25 is strictly rejected."""
    # Premium = 200.00 * 25 = Rs 5,000.00 -> exactly at boundary
    res = validate_paper_trade_guardrails("NIFTY", 200.00, lots=1)
    assert res["status"] == "APPROVED"
    assert res["outlay"] == 5000.00

    # Premium = 200.01 * 25 = Rs 5,000.25 -> 25 paise over budget
    with pytest.raises(BudgetExceededError) as exc_info:
        validate_paper_trade_guardrails("NIFTY", 200.01, lots=1)
    assert "Outlay Rs 5000.25 exceeds Rs 5000.00 limit" in str(exc_info.value)


def test_budget_exact_boundary_banknifty():
    """Verify Bank Nifty Rs 4,999.95 outlay is allowed and Rs 5,000.10 is strictly rejected."""
    # Premium = 333.33 * 15 = Rs 4,999.95 -> within budget
    res = validate_paper_trade_guardrails("BANKNIFTY", 333.33, lots=1)
    assert res["status"] == "APPROVED"
    assert res["outlay"] == 4999.95

    # Premium = 333.34 * 15 = Rs 5,000.10 -> 10 paise over budget
    with pytest.raises(BudgetExceededError) as exc_info:
        validate_paper_trade_guardrails("BANKNIFTY", 333.34, lots=1)
    assert "Outlay Rs 5000.10 exceeds Rs 5000.00 limit" in str(exc_info.value)


def test_budget_strict_single_lot_enforcement():
    """Verify lots != 1 is rejected regardless of outlay."""
    with pytest.raises(ValueError, match="Strict 1-lot order enforcement violated"):
        validate_paper_trade_guardrails("NIFTY", 10.0, lots=2)

    with pytest.raises(ValueError, match="Strict 1-lot order enforcement violated"):
        validate_paper_trade_guardrails("BANKNIFTY", 10.0, lots=0)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Offline Fallback Transition & Resilient Tier 3 Brownian Motion
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_corrupt_disk_cache_recovery():
    """Verify corrupted market_cache.json is caught and reseeded with default seeds."""
    with tempfile.TemporaryDirectory() as tmpdir:
        corrupt_path = Path(tmpdir) / "corrupt_cache.json"
        corrupt_path.write_text("{{INVALID JSON CORRUPTED DATA:::", encoding="utf-8")

        mgr = DiskCacheManager(cache_file=corrupt_path)
        snap = mgr.get_snapshot("NIFTY")
        assert snap is not None
        assert snap["spot_price"] == 24986.40

        # DhanClient using corrupt cache file recovers smoothly
        client = DhanClient(cache_file=corrupt_path)
        ltp = await client.get_ltp("NIFTY")
        assert 24500.0 < ltp < 25500.0
        status = await client.get_connection_status()
        assert status["dhan_status"] == "FALLBACK"
        await client.aclose()


@pytest.mark.asyncio
async def test_simulated_network_errors_trigger_tier3_fallback():
    """Verify simulated network connection errors and timeouts cleanly fall back to Tier 3 Brownian feed."""
    client = DhanClient()
    mock_http = AsyncMock()
    client._http = mock_http

    with patch.object(DhanClient, "is_market_open", return_value=True):
        # 1. ConnectError
        mock_http.post.side_effect = httpx.ConnectError("Network unreachable")
        spot_nifty = await client.get_ltp("NIFTY")
        assert isinstance(spot_nifty, float)
        assert spot_nifty > 0

        # 2. ReadTimeout
        mock_http.post.side_effect = httpx.ReadTimeout("Socket timeout")
        spot_bank = await client.get_ltp("BANKNIFTY")
        assert isinstance(spot_bank, float)
        assert spot_bank > 0

        # 3. HTTP 500 Internal Error from Dhan
        mock_resp_500 = httpx.Response(500, text="Internal Server Error", request=httpx.Request("POST", "http://test"))
        mock_http.post.side_effect = None
        mock_http.post.return_value = mock_resp_500
        chain = await client.get_option_chain("NIFTY")
        assert len(chain) == 15
        assert all("ce" in item and "pe" in item for item in chain)

    await client.aclose()


# ══════════════════════════════════════════════════════════════════════════════
# 5. Midnight IST Transitions & Weekend Market-Hours Checks
# ══════════════════════════════════════════════════════════════════════════════

def test_midnight_ist_transitions():
    """Verify timestamp and countdown behavior across 23:59:59 -> 00:00:00 -> 00:00:01 IST."""
    t_235959 = datetime(2026, 11, 11, 23, 59, 59, tzinfo=IST)
    t_000000 = datetime(2026, 11, 12, 0, 0, 0, tzinfo=IST)
    t_000001 = datetime(2026, 11, 12, 0, 0, 1, tzinfo=IST)

    cd_235959 = calculate_countdown_seconds(t_235959, 5)
    assert cd_235959 == 1
    assert format_countdown_timer(cd_235959) == "00:01"

    cd_000000 = calculate_countdown_seconds(t_000000, 5)
    assert cd_000000 == 300
    assert format_countdown_timer(cd_000000) == "05:00"

    cd_000001 = calculate_countdown_seconds(t_000001, 5)
    assert cd_000001 == 299
    assert format_countdown_timer(cd_000001) == "04:59"

    assert is_market_open_ist(t_235959) is False
    assert is_market_open_ist(t_000000) is False
    assert is_scheduler_window_ist(t_235959) is False
    assert is_scheduler_window_ist(t_000000) is False


def test_weekend_market_hours_rejection():
    """Verify Saturday and Sunday are strictly closed regardless of time."""
    sat_noon = datetime(2026, 11, 14, 12, 0, 0, tzinfo=IST)
    sun_noon = datetime(2026, 11, 15, 12, 0, 0, tzinfo=IST)

    assert sat_noon.weekday() == 5
    assert sun_noon.weekday() == 6

    assert is_market_open_ist(sat_noon) is False
    assert is_market_open_ist(sun_noon) is False
    assert is_scheduler_window_ist(sat_noon) is False
    assert is_scheduler_window_ist(sun_noon) is False


def test_weekday_market_hours_exact_boundaries():
    """Verify standard weekday market open window (09:15:00 to 15:30:00 IST)."""
    # Monday Nov 9, 2026
    pre_open = datetime(2026, 11, 9, 9, 14, 59, tzinfo=IST)
    open_tick = datetime(2026, 11, 9, 9, 15, 0, tzinfo=IST)
    close_tick = datetime(2026, 11, 9, 15, 30, 0, tzinfo=IST)
    post_close = datetime(2026, 11, 9, 15, 30, 1, tzinfo=IST)

    assert is_market_open_ist(pre_open) is False
    assert is_market_open_ist(open_tick) is True
    assert is_market_open_ist(close_tick) is True
    assert is_market_open_ist(post_close) is False
