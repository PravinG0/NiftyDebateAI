"""
Tests for Strict ₹700 Max Stop-Loss & ₹1,000 Max Target Guardrails.
Enforces:
- Max Loss in Rupees: <= ₹700
- Max Target Profit in Rupees: <= ₹1,000
- For Nifty (lot 25): Max SL distance = 28.00 pts, Max Target distance = 40.00 pts
- For Bank Nifty (lot 15): Max SL distance = 46.67 pts, Max Target distance = 66.67 pts
"""

import pytest
from config import (
    MAX_STOP_LOSS_INR,
    MAX_TARGET_INR,
    compute_clamped_sl_target,
    validate_paper_trade_guardrails,
    RiskLimitExceededError,
)


def test_nifty_sl_target_clamp_and_validation():
    entry = 120.0
    # For Nifty (qty 25): 700 / 25 = 28.0 pts max SL distance. 1000 / 25 = 40.0 pts max Target distance.
    sl, tgt = compute_clamped_sl_target("NIFTY", entry_price=entry)
    assert sl == 120.0 - 28.0  # 92.0
    assert tgt == 120.0 + 40.0  # 160.0

    # Risk in INR = (120 - 92) * 25 = 700.00
    assert (entry - sl) * 25 <= MAX_STOP_LOSS_INR
    # Profit in INR = (160 - 120) * 25 = 1000.00
    assert (tgt - entry) * 25 <= MAX_TARGET_INR

    # Validation succeeds
    res = validate_paper_trade_guardrails("NIFTY", entry_price=entry, lots=1, stop_loss=sl, target=tgt)
    assert res["status"] == "APPROVED"


def test_banknifty_sl_target_clamp_and_validation():
    entry = 250.0
    # For Bank Nifty (qty 15): 700 / 15 = 46.67 pts max SL distance. 1000 / 15 = 66.67 pts max Target distance.
    sl, tgt = compute_clamped_sl_target("BANKNIFTY", entry_price=entry)
    # Bank Nifty max SL distance: floor(700/15*100)/100 = 46.66 pts -> sl = 203.34
    assert sl == 203.34
    assert tgt == round(250.0 + 66.66, 2)

    # Risk in INR <= 700
    assert round((entry - sl) * 15, 2) <= 700.05
    # Profit in INR <= 1000
    assert round((tgt - entry) * 15, 2) <= 1000.05

    res = validate_paper_trade_guardrails("BANKNIFTY", entry_price=entry, lots=1, stop_loss=sl, target=tgt)
    assert res["status"] == "APPROVED"


def test_nifty_sl_exceeding_700_rejected():
    entry = 120.0
    # Attempt SL that risks 35 pts (35 * 25 = 875 > 700)
    unsafe_sl = entry - 35.0  # 85.0
    with pytest.raises(RiskLimitExceededError) as exc:
        validate_paper_trade_guardrails("NIFTY", entry_price=entry, lots=1, stop_loss=unsafe_sl, target=150.0)
    assert "Stop loss risk Rs 875.00 exceeds maximum allowed limit of Rs 700.00" in str(exc.value)


def test_nifty_target_exceeding_1000_rejected():
    entry = 120.0
    # Attempt target that profits 50 pts (50 * 25 = 1250 > 1000)
    unsafe_tgt = entry + 50.0  # 170.0
    with pytest.raises(RiskLimitExceededError) as exc:
        validate_paper_trade_guardrails("NIFTY", entry_price=entry, lots=1, stop_loss=100.0, target=unsafe_tgt)
    assert "Target profit Rs 1250.00 exceeds maximum allowed limit of Rs 1000.00" in str(exc.value)
