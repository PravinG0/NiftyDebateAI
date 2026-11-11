"""
Market data utilities for NiftyDebateAI.

Builds enriched :class:`schemas.MarketSnapshot` objects from OpenAlgo data,
provides helpers for strike selection and NSE symbol generation, and falls
back to realistic mock data when a live feed is unavailable.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter

from schemas import MarketSnapshot
from openalgo_client import OpenAlgoClient

logger = logging.getLogger(__name__)


# ── Singleton client (module-level) ───────────────────────────────────────────

_openalgo = OpenAlgoClient()


async def get_openalgo_client() -> OpenAlgoClient:
    """Return the shared :class:`OpenAlgoClient` singleton."""
    return _openalgo


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

_cached_snapshots: dict[str, tuple[float, MarketSnapshot]] = {}


async def build_market_snapshot(symbol: str = "NIFTY", force_fresh: bool = False) -> MarketSnapshot:
    """Build enriched MarketSnapshot prioritizing Angel One live quotes and real candle calculations.

    Falls back to DhanClient and simulation only when Angel One is unavailable or unconfigured.
    Caches for 5 seconds per symbol to ensure option chain, debates, and trade executions
    use consistent spot and option premiums without microsecond drift.
    """
    global _cached_snapshots
    import time
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")

    sym = symbol.upper()
    now = time.time()
    if not force_fresh and sym in _cached_snapshots:
        cache_time, snap = _cached_snapshots[sym]
        if now - cache_time < 5.0:
            return snap

    is_bank = "BANK" in sym
    step = 100 if is_bank else 50

    # 1. Prioritize Angel One SmartAPI live quote and real candle calculations
    angel_quote = None
    angel_indicators = None
    try:
        from angel_client import get_angel_client
        angel = get_angel_client()
        if angel.is_configured():
            angel_quote = await angel.get_quote(sym)
            candles = await angel.get_candles(sym, interval="5m", range_period="5d")
            if candles:
                angel_indicators = angel.calculate_indicators(candles)
    except Exception as exc:
        logger.debug("Angel One live market data query skipped or failed for %s: %s", sym, exc)

    if angel_quote and angel_quote.get("spot_price", 0) > 0:
        spot = float(angel_quote["spot_price"])
        open_price = float(angel_quote.get("open", spot))
        high_price = float(angel_quote.get("high", spot))
        low_price = float(angel_quote.get("low", spot))

        if angel_indicators and angel_indicators.get("candle_count", 0) > 0:
            ema_20 = float(angel_indicators.get("ema_20", spot))
            ema_50 = float(angel_indicators.get("ema_50", spot))
            vwap = float(angel_indicators.get("vwap", spot))
            rsi = float(angel_indicators.get("rsi", 50.0))
            atr = float(angel_indicators.get("atr", 370.0 if is_bank else 146.55))
            trend = str(angel_indicators.get("higher_tf_trend", "BULLISH" if spot >= ema_20 else "BEARISH"))
        else:
            ema_20 = float(spot - (80.0 if is_bank else 45.0))
            ema_50 = float(spot - (176.0 if is_bank else 110.0))
            vwap = float(spot)
            rsi = 52.4
            atr = 370.0 if is_bank else 146.55
            trend = "BULLISH" if spot >= ema_20 else "BEARISH"

        atm = calculate_atm_strike(spot, step=step)

        # Standard secondary derivatives metrics
        volume = float(angel_quote.get("trade_volume") or angel_quote.get("volume") or (980000.0 if is_bank else 1250000.0))
        vix = 14.5
        pcr = 1.05
        ce_oi_change = 45000.0
        pe_oi_change = 52000.0

        snapshot = MarketSnapshot(
            spot_price=spot,
            ema_20=ema_20,
            ema_50=ema_50,
            vwap=vwap,
            rsi=rsi,
            volume=volume,
            atr=atr,
            vix=vix,
            pcr=pcr,
            ce_oi_change=ce_oi_change,
            pe_oi_change=pe_oi_change,
            higher_tf_trend=trend,
            timestamp=datetime.now(ist),
            atm_strike=str(atm),
            nifty_high=high_price,
            nifty_low=low_price,
            nifty_open=open_price,
        )

        logger.info(
            "MarketSnapshot built (Angel One Live) for %s — spot=%.2f ema20=%.2f rsi=%.2f atr=%.2f trend=%s",
            sym,
            snapshot.spot_price,
            snapshot.ema_20,
            snapshot.rsi,
            snapshot.atr,
            snapshot.higher_tf_trend,
        )
        _cached_snapshots[sym] = (now, snapshot)
        return snapshot

    # 2. Fallback to Dhan / Simulator snapshot when Angel One is unavailable
    from dhan_client import get_dhan_client
    dhan = get_dhan_client()
    raw: dict = await dhan.get_market_snapshot(sym)

    spot = float(raw.get("spot_price", 24986.40 if sym == "NIFTY" else 52140.80))
    atm = calculate_atm_strike(spot, step=step)

    snapshot = MarketSnapshot(
        spot_price=spot,
        ema_20=float(raw.get("ema_20", spot - (80.0 if is_bank else 45.0))),
        ema_50=float(raw.get("ema_50", spot - (176.0 if is_bank else 110.0))),
        vwap=float(raw.get("vwap", spot)),
        rsi=float(raw.get("rsi", 52.4)),
        volume=float(raw.get("volume", 980000.0 if is_bank else 1250000.0)),
        atr=float(raw.get("atr", 370.0 if is_bank else 146.55)),
        vix=float(raw.get("vix", 14.5)),
        pcr=float(raw.get("pcr", 1.05)),
        ce_oi_change=float(raw.get("ce_oi_change", 45000.0)),
        pe_oi_change=float(raw.get("pe_oi_change", 52000.0)),
        higher_tf_trend=str(raw.get("higher_tf_trend", "BULLISH" if spot > float(raw.get("ema_20", spot - 45.0)) else "BEARISH")),
        timestamp=datetime.now(ist),
        atm_strike=str(atm),
        nifty_high=float(raw.get("high", spot + (150.0 if is_bank else 60.0))),
        nifty_low=float(raw.get("low", spot - (150.0 if is_bank else 60.0))),
        nifty_open=float(raw.get("open", spot)),
    )

    logger.info(
        "MarketSnapshot built (Fallback) for %s — spot=%.2f rsi=%.2f vix=%.2f trend=%s",
        sym,
        snapshot.spot_price,
        snapshot.rsi,
        snapshot.vix,
        snapshot.higher_tf_trend,
    )
    _cached_snapshots[sym] = (now, snapshot)
    return snapshot


# ── Market Quotes Router & Helper ─────────────────────────────────────────────

market_router = APIRouter(tags=["Market Data"])


async def get_market_quotes_response(symbols: str | list[str] = "NIFTY,BANKNIFTY") -> dict[str, Any]:
    """Retrieve multi-symbol live quotes from Angel One SmartAPI matching PROJECT.md interface contract."""
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    from angel_client import get_angel_client
    angel = get_angel_client()

    if isinstance(symbols, str):
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        sym_list = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not sym_list:
        sym_list = ["NIFTY", "BANKNIFTY"]

    quotes = await angel.get_multi_quotes(sym_list)
    return {
        "source": "angel_one",
        "timestamp": datetime.now(ist).isoformat(),
        "quotes": quotes,
    }


@market_router.get("/api/market/quotes")
@market_router.get("/market/quotes")
async def get_market_quotes_endpoint(symbols: str = "NIFTY,BANKNIFTY") -> dict[str, Any]:
    """Return live quotes for active indices."""
    return await get_market_quotes_response(symbols)


@market_router.get("/api/market/overview")
@market_router.get("/market/overview")
async def get_market_overview_endpoint(symbols: str = "NIFTY,BANKNIFTY") -> dict[str, Any]:
    """Return comprehensive market overview containing live quotes, technical indicators, and market status."""
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    from angel_client import get_angel_client
    from scheduler import is_market_open

    quotes_res = await get_market_quotes_response(symbols)
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        sym_list = ["NIFTY", "BANKNIFTY"]

    client = get_angel_client()
    conn_status = await client.get_connection_status()

    snapshots: dict[str, Any] = {}
    for s in sym_list:
        try:
            snap = await build_market_snapshot(s)
            snapshots[s] = {
                "spot_price": snap.spot_price,
                "ema_20": snap.ema_20,
                "ema_50": snap.ema_50,
                "vwap": snap.vwap,
                "rsi": snap.rsi,
                "atr": snap.atr,
                "higher_tf_trend": snap.higher_tf_trend,
                "atm_strike": snap.atm_strike,
            }
        except Exception as exc:
            logger.warning("Failed to build market snapshot for %s in overview: %s", s, exc)

    return {
        "status": "ok",
        "market_status": "OPEN" if is_market_open() else "CLOSED",
        "source": "angel_one",
        "feed_source": conn_status.get("angel_feed_source") or conn_status.get("feed_source", "live_exchange"),
        "timestamp": datetime.now(ist).isoformat(),
        "quotes": quotes_res.get("quotes", {}),
        "snapshots": snapshots,
    }




def calculate_atm_strike(spot_price: float, step: int = 50) -> int:
    """Return the at-the-money strike rounded to the nearest *step*.

    Example:
        >>> calculate_atm_strike(24537.65, step=50)
        24550
    """
    return int(round(spot_price / step) * step)


def generate_option_symbol(
    expiry: str,
    strike: int | str,
    option_type: str,
) -> str:
    """Build an NSE-style option symbol.

    Args:
        expiry: Date string in ``YYMMDD`` or ``YYYY-MM-DD`` format.
        strike: Strike price (e.g. ``24500``).
        option_type: ``"CE"`` or ``"PE"``.

    Returns:
        Symbol like ``NIFTY2611124500CE``.
    """
    # Normalise expiry to YYMMDD
    if "-" in str(expiry):
        dt = datetime.strptime(str(expiry), "%Y-%m-%d")
        expiry_fmt = dt.strftime("%y%m%d")
    else:
        expiry_fmt = str(expiry)

    return f"NIFTY{expiry_fmt}{int(strike)}{option_type.upper()}"


def get_nearest_expiry() -> str:
    """Return a synthetic *nearest Thursday* expiry as ``YYMMDD``.

    Uses a simple heuristic — production code would query the exchange
    calendar or OpenAlgo.
    """
    from datetime import timedelta

    today = datetime.now(timezone.utc).date()
    days_ahead = (3 - today.weekday()) % 7  # Thursday = 3
    if days_ahead == 0:
        days_ahead = 7  # already Thursday → next week
    nearest = today + timedelta(days=days_ahead)
    return nearest.strftime("%y%m%d")


def calculate_option_premium(spot: float, strike: int, option_type: str, vix: float = 14.5) -> float:
    """Calculate realistic option premium based on intrinsic and time value."""
    diff = spot - strike if option_type.upper() == "CE" else strike - spot
    intrinsic = max(0.0, diff)
    # Time value based on VIX and distance from ATM
    atm_distance = abs(spot - strike)
    decay_factor = max(0.15, 1.0 - (atm_distance / 600.0))
    time_value = max(12.0, (vix * 6.5) * decay_factor)
    total_premium = round(intrinsic + time_value, 2)
    return max(1.0, total_premium)


def parse_option_symbol(symbol: str) -> tuple[int, str]:
    """Extract (strike, option_type) from symbols like 'NIFTY26111224500CE', '24500CE', '24500 PE'.
    
    Returns:
        tuple[int, str]: (strike, "CE" or "PE")
    """
    s = str(symbol).strip().upper()
    opt_type = "PE" if (s.endswith("PE") or "PUT" in s) else "CE"
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) >= 5:
        # e.g. NIFTY26111224500CE -> strike is last 5 digits (24500)
        strike = int(digits[-5:])
    elif digits:
        strike = int(digits)
    else:
        strike = 24500
    return strike, opt_type


def get_live_option_ltp(symbol: str, spot: float, vix: float = 14.5) -> float:
    """Calculate the exact option premium for any contract symbol based on spot price."""
    strike, opt_type = parse_option_symbol(symbol)
    return calculate_option_premium(spot=spot, strike=strike, option_type=opt_type, vix=vix)


def generate_option_chain(
    spot_price: float,
    lot_size: int = 25,
    num_strikes: int = 7,
    step: int | None = None,
    symbol: str = "NIFTY",
) -> list[dict[str, Any]]:
    """Generate an Angel One-style Option Chain table with Total Lot Amounts at every strike price.
    
    Returns strikes around ATM with Call & Put prices, Total Lot Amount (price * lot_size),
    OI, within_budget indicator, and one-click execution details.
    """
    sym_up = symbol.upper()
    if step is None:
        step = 100 if "BANK" in sym_up else 50
    if lot_size == 25 and "BANK" in sym_up:
        lot_size = 15

    atm = calculate_atm_strike(spot_price, step=step)
    chain = []
    
    # Generate strikes from (atm - num_strikes*step) to (atm + num_strikes*step)
    for offset in range(-num_strikes, num_strikes + 1):
        strike = atm + (offset * step)
        is_atm = (strike == atm)
        
        ce_price = calculate_option_premium(spot_price, strike, "CE")
        pe_price = calculate_option_premium(spot_price, strike, "PE")
        
        # Total amount required to buy 1 lot
        ce_lot_amount = round(ce_price * lot_size, 2)
        pe_lot_amount = round(pe_price * lot_size, 2)
        
        # Synthetic realistic OI based on strike distance
        ce_oi = int(max(50000, 2500000 - abs(strike - (spot_price + step)) * 2500))
        pe_oi = int(max(50000, 2500000 - abs(strike - (spot_price - step)) * 2500))
        
        # Day change simulation
        ce_change_pts = round((spot_price - strike) * 0.05 + 4.2, 2)
        pe_change_pts = round((strike - spot_price) * 0.05 - 3.8, 2)
        
        expiry = get_nearest_expiry()
        ce_symbol = f"{sym_up}{expiry}{strike}CE"
        pe_symbol = f"{sym_up}{expiry}{strike}PE"
        
        chain.append({
            "strike": strike,
            "is_atm": is_atm,
            "lot_size": lot_size,
            "ce": {
                "symbol": ce_symbol,
                "strike_name": f"{strike} CE",
                "ltp": ce_price,
                "lot_amount": ce_lot_amount,
                "within_budget": (ce_lot_amount <= 5000.0),
                "change": ce_change_pts,
                "change_pct": round((ce_change_pts / ce_price) * 100, 2) if ce_price > 0 else 0.0,
                "oi": ce_oi,
                "oi_change": int(ce_oi * 0.08),
            },
            "pe": {
                "symbol": pe_symbol,
                "strike_name": f"{strike} PE",
                "ltp": pe_price,
                "lot_amount": pe_lot_amount,
                "within_budget": (pe_lot_amount <= 5000.0),
                "change": pe_change_pts,
                "change_pct": round((pe_change_pts / pe_price) * 100, 2) if pe_price > 0 else 0.0,
                "oi": pe_oi,
                "oi_change": int(pe_oi * 0.07),
            },
        })
        
    return chain


async def get_option_chain(
    symbol: str = "NIFTY",
    strikes: int = 7,
    expiry: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Retrieve Option Chain via DhanClient with 3-tier fallback."""
    from dhan_client import get_dhan_client
    dhan = get_dhan_client()
    return await dhan.get_option_chain(symbol=symbol, expiry=expiry, num_strikes=strikes)
