"""
Unit and integration tests for advanced institutional metrics:
- CPR (Central Pivot Range: Pivot, BC, TC, width classification)
- Supertrend (10, 3) direction and trailing stop level
- Option Chain Max Pain Strike & Highest Call/Put OI Walls
- Heavyweights Breadth (Reliance, HDFC Bank, ICICI Bank, INFY)
- FII institutional bias & Global sentiment
- Bull, Bear, and Judge agent prompt serialization
"""

import pytest
from httpx import ASGITransport, AsyncClient

from angel_client import get_angel_client
from main import app
from market_data import (
    build_market_snapshot,
    calculate_max_pain_and_walls,
    get_heavyweights_breadth,
    get_macro_sentiment,
)
from schemas import MarketSnapshot
from agents.bull_agent import BullAgent
from agents.bear_agent import BearAgent
from agents.judge_agent import JudgeAgent


def test_calculate_max_pain_and_walls():
    """Verify Max Pain and Call/Put OI Walls calculation."""
    sample_chain = [
        {"strike": 24900, "ce": {"oi": 50000}, "pe": {"oi": 400000}},
        {"strike": 24950, "ce": {"oi": 100000}, "pe": {"oi": 350000}},
        {"strike": 25000, "ce": {"oi": 250000}, "pe": {"oi": 250000}},
        {"strike": 25050, "ce": {"oi": 350000}, "pe": {"oi": 100000}},
        {"strike": 25100, "ce": {"oi": 500000}, "pe": {"oi": 50000}},
    ]

    res = calculate_max_pain_and_walls(sample_chain)
    assert res["max_pain"] is not None
    assert res["call_oi_wall"] == 25100  # Highest Call OI
    assert res["put_oi_wall"] == 24900   # Highest Put OI
    assert 24900 <= res["max_pain"] <= 25100


def test_cpr_and_supertrend_in_angel_indicators():
    """Verify CPR levels and Supertrend are computed in angel_client."""
    angel = get_angel_client()
    now_t = 1700000000
    mock_candles = [
        {"time": now_t + (i * 300), "open": 25000 + i, "high": 25050 + i, "low": 24950 + i, "close": 25020 + i}
        for i in range(30)
    ]

    indicators = angel.calculate_indicators(mock_candles)
    assert "cpr_pivot" in indicators
    assert "cpr_bc" in indicators
    assert "cpr_tc" in indicators
    assert "cpr_width_type" in indicators
    assert indicators["cpr_width_type"] in ("NARROW_TRENDING", "WIDE_RANGEBOUND")
    assert "supertrend_direction" in indicators
    assert indicators["supertrend_direction"] in ("BULLISH", "BEARISH")
    assert indicators["supertrend_value"] > 0


def test_macro_sentiment_classification():
    """Verify institutional macro sentiment derivation."""
    bullish = get_macro_sentiment(spot=25100.0, ema_50=24950.0, vwap=25000.0)
    assert bullish["fii_sentiment"] == "NET_BUYER"
    assert bullish["global_sentiment"] == "POSITIVE"

    bearish = get_macro_sentiment(spot=24800.0, ema_50=24950.0, vwap=24900.0)
    assert bearish["fii_sentiment"] == "NET_SELLER"
    assert bearish["global_sentiment"] == "NEGATIVE"


@pytest.mark.asyncio
async def test_heavyweights_breadth_calculation():
    """Verify heavyweights breadth retrieval."""
    breadth = await get_heavyweights_breadth()
    assert "advance_decline_ratio" in breadth
    assert breadth["advance_decline_ratio"] > 0
    assert breadth["heavyweights_alignment"] in ("BULLISH", "BEARISH", "MIXED")


@pytest.mark.asyncio
async def test_market_snapshot_contains_all_institutional_metrics():
    """Verify build_market_snapshot populates all 13 institutional fields."""
    snapshot = await build_market_snapshot("NIFTY", force_fresh=True)

    assert isinstance(snapshot, MarketSnapshot)
    assert snapshot.max_pain is not None
    assert snapshot.call_oi_wall is not None
    assert snapshot.put_oi_wall is not None
    assert snapshot.cpr_pivot is not None
    assert snapshot.cpr_bc is not None
    assert snapshot.cpr_tc is not None
    assert snapshot.cpr_width_type in ("NARROW_TRENDING", "WIDE_RANGEBOUND")
    assert snapshot.supertrend_direction in ("BULLISH", "BEARISH")
    assert snapshot.supertrend_value is not None
    assert snapshot.advance_decline_ratio is not None
    assert snapshot.heavyweights_alignment in ("BULLISH", "BEARISH", "MIXED")
    assert snapshot.fii_sentiment in ("NET_BUYER", "NET_SELLER", "NEUTRAL")
    assert snapshot.global_sentiment in ("POSITIVE", "NEGATIVE", "NEUTRAL")


@pytest.mark.asyncio
async def test_agent_prompts_serialize_institutional_metrics():
    """Verify Bull, Bear, and Judge agent prompt serialization contains the new institutional metrics."""
    snapshot = await build_market_snapshot("NIFTY", force_fresh=True)

    bull_prompt = BullAgent._build_user_prompt(snapshot)
    assert "CPR Pivot" in bull_prompt
    assert "Supertrend" in bull_prompt
    assert "Max Pain" in bull_prompt
    assert "Put OI Wall" in bull_prompt
    assert "Heavyweights" in bull_prompt

    bear_prompt = BearAgent._build_user_prompt(snapshot)
    assert "CPR Pivot" in bear_prompt
    assert "Supertrend" in bear_prompt
    assert "Call OI Wall" in bear_prompt
    assert "Heavyweights" in bear_prompt

    judge = JudgeAgent()
    bull_out = BullAgent._fallback_output(snapshot)
    bear_out = BearAgent._fallback_output(snapshot)
    judge_prompt = judge._build_user_prompt(snapshot, bull_out, bear_out)
    assert "CPR Pivot" in judge_prompt
    assert "Supertrend" in judge_prompt
    assert "Max Pain" in judge_prompt
    assert "Call OI Wall" in judge_prompt


@pytest.mark.asyncio
async def test_market_overview_endpoint_returns_institutional_metrics():
    """Verify /api/market/overview exposes the institutional fields in snapshots."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/market/overview?symbols=NIFTY")
        assert resp.status_code == 200
        data = resp.json()
        nifty_snap = data["snapshots"].get("NIFTY")
        assert nifty_snap is not None
        assert "max_pain" in nifty_snap
        assert "call_oi_wall" in nifty_snap
        assert "put_oi_wall" in nifty_snap
        assert "cpr_pivot" in nifty_snap
        assert "supertrend_direction" in nifty_snap
        assert "heavyweights_alignment" in nifty_snap
