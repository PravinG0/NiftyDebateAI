"""
OpenAlgo REST client for NiftyDebateAI.

Provides helpers to verify analyzer (sandbox) mode, fetch market data,
place paper-trade orders, and retrieve option chain / LTP data.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


# ── Custom Exception ──────────────────────────────────────────────────────────

class SafetyError(Exception):
    """Raised when a trade is attempted outside analyzer / sandbox mode."""
    pass


# ── Client ────────────────────────────────────────────────────────────────────

class OpenAlgoClient:
    """Async HTTP client wrapping the OpenAlgo REST API."""

    def __init__(self) -> None:
        self.base_url: str = settings.OPENALGO_BASE_URL.rstrip("/")
        self.api_key: str = settings.OPENALGO_API_KEY
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        self._client: httpx.AsyncClient | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (or lazily create) the shared httpx async client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                timeout=httpx.Timeout(15.0),
            )
        return self._client

    async def close(self) -> None:
        """Gracefully close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Analyzer / Sandbox Mode Check ─────────────────────────────────────

    def _parse_bool_mode(self, raw: Any) -> bool:
        if isinstance(raw, str):
            return raw.strip().lower() in ("true", "1", "yes")
        return raw is True

    async def check_analyzer_mode(self) -> bool:
        """Verify that OpenAlgo is running in *analyzer* (sandbox) mode.

        Resolution order:
        1. Call ``GET /api/v1/analyzer/status`` on OpenAlgo.
        2. Fall back to ``GET /api/v1/config`` and inspect the payload.
        3. Fall back to the ``FORCE_ANALYZER_MODE`` env-var override.
        4. Default to **False** (safe — blocks trading) with a warning.
        """
        client = await self._get_client()

        # Attempt 1 – dedicated analyzer endpoint
        try:
            resp = await client.get("/api/v1/analyzer/status")
            if resp.status_code == 200:
                data = resp.json()
                return self._parse_bool_mode(data.get("analyzer_mode", False))
        except (httpx.HTTPError, Exception) as exc:
            logger.debug("analyzer/status probe failed: %s", exc)

        # Attempt 2 – config endpoint
        try:
            resp = await client.get("/api/v1/config")
            if resp.status_code == 200:
                data = resp.json()
                return self._parse_bool_mode(data.get("analyzer_mode", False))
        except (httpx.HTTPError, Exception) as exc:
            logger.debug("config probe failed: %s", exc)

        # Attempt 3 – environment variable override
        force = settings.FORCE_ANALYZER_MODE
        if force:
            logger.warning(
                "Could not verify analyzer mode via OpenAlgo API. "
                "FORCE_ANALYZER_MODE is set — treating as sandbox."
            )
            return True

        # Attempt 4 – safe default
        logger.warning(
            "Cannot confirm analyzer mode. Defaulting to SAFE (trading blocked)."
        )
        return False

    # ── Market Data ───────────────────────────────────────────────────────

    async def get_market_snapshot(self, symbol: str = "NIFTY") -> dict[str, Any]:
        """Fetch live market data from OpenAlgo.

        Falls back to a realistic mock payload when the real endpoint is
        unreachable so the system can be demoed without a live connection.
        """
        client = await self._get_client()

        try:
            resp = await client.get(
                "/api/v1/market/snapshot",
                params={"symbol": symbol},
            )
            if resp.status_code == 200:
                return resp.json()
        except (httpx.HTTPError, Exception) as exc:
            logger.warning("market/snapshot request failed: %s", exc)

        # ── Mock fallback ─────────────────────────────────────────────────
        logger.info("Returning mock market snapshot for demo purposes.")
        return self._mock_market_snapshot(symbol)

    async def get_option_chain(self, symbol: str = "NIFTY") -> dict[str, Any]:
        """Retrieve option-chain data from OpenAlgo."""
        client = await self._get_client()

        try:
            resp = await client.get(
                "/api/v1/market/optionchain",
                params={"symbol": symbol},
            )
            if resp.status_code == 200:
                return resp.json()
        except (httpx.HTTPError, Exception) as exc:
            logger.warning("option chain request failed: %s", exc)

        logger.info("Returning mock option chain for demo purposes.")
        return {"symbol": symbol, "expiry": "2026-11-12", "chain": []}

    async def get_ltp(self, symbol: str) -> float:
        """Get the last traded price for *symbol*."""
        client = await self._get_client()

        try:
            resp = await client.get(
                "/api/v1/market/ltp",
                params={"symbol": symbol},
            )
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("ltp", 0.0))
        except (httpx.HTTPError, Exception) as exc:
            logger.warning("LTP request failed for %s: %s", symbol, exc)

        # Fallback – return consistent real-time option premium matching Option Chain
        try:
            sym_clean = symbol.upper()
            if sym_clean in ("NIFTY", "BANKNIFTY"):
                from dhan_client import get_dhan_client
                return get_dhan_client().get_ltp_sync(sym_clean)
            from market_data import calculate_option_premium, parse_option_symbol
            mock = self._mock_market_snapshot("BANKNIFTY" if "BANK" in sym_clean else "NIFTY")
            spot = float(mock.get("spot_price", 24986.40))
            vix = float(mock.get("vix", 14.5))
            strike, opt_type = parse_option_symbol(symbol)
            return calculate_option_premium(spot=spot, strike=strike, option_type=opt_type, vix=vix)
        except Exception:
            return 120.0

    # ── Order Placement ───────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = "MARKET",
    ) -> dict[str, Any]:
        """Place a paper trade via OpenAlgo.

        Raises :class:`SafetyError` if the system is **not** in analyzer mode.
        """
        is_sandbox = await self.check_analyzer_mode()
        if is_sandbox is not True:
            raise SafetyError(
                "Trading is blocked — OpenAlgo is not in analyzer (sandbox) mode."
            )

        client = await self._get_client()

        payload = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "product": "MIS",
        }

        try:
            resp = await client.post("/api/v1/orders/place", json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Order placement HTTP error: %s", exc)
            raise
        except httpx.HTTPError as exc:
            logger.error("Order placement failed: %s", exc)
            # In demo / fallback mode return a synthetic confirmation
            return {
                "order_id": "DEMO-0001",
                "status": "COMPLETE",
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "message": "Demo order — OpenAlgo unreachable",
            }

    # ── Private Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _mock_market_snapshot(symbol: str = "NIFTY") -> dict[str, Any]:
        """Generate a realistic market snapshot for NIFTY or BANKNIFTY using DhanClient."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ist = ZoneInfo("Asia/Kolkata")
        is_bank = "BANK" in symbol.upper()
        default_spot = 52140.80 if is_bank else 24986.40

        try:
            from dhan_client import get_dhan_client
            dhan = get_dhan_client()
            quote = dhan.get_cached_quote(symbol)
            spot = float(quote.get("spot_price", default_spot))
            high = float(quote.get("high", spot + (150.0 if is_bank else 60.0)))
            low = float(quote.get("low", spot - (150.0 if is_bank else 60.0)))
            open_price = float(quote.get("open", spot))
        except Exception as exc:
            logger.debug("Dhan client quote fetch failed, using fallback: %s", exc)
            spot = default_spot
            high = default_spot + (150.0 if is_bank else 60.0)
            low = default_spot - (150.0 if is_bank else 60.0)
            open_price = default_spot

        ema_offset = 80.0 if is_bank else 45.0
        ema_20 = round(spot - ema_offset, 2)
        ema_50 = round(spot - (ema_offset * 2.2), 2)
        vwap = round((high + low + spot) / 3, 2)
        rsi = 52.4
        volume = 980000.0 if is_bank else 1250000.0
        atr = round(max(high - low, 350.0 if is_bank else 120.0), 2)
        vix = 14.5
        pcr = 1.05
        ce_oi = 45000.0
        pe_oi = 52000.0
        trend = "BULLISH" if spot > ema_20 else "BEARISH"

        return {
            "symbol": symbol.upper(),
            "spot_price": spot,
            "open": open_price,
            "high": high,
            "low": low,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "vwap": vwap,
            "rsi": rsi,
            "volume": volume,
            "atr": atr,
            "vix": vix,
            "pcr": pcr,
            "ce_oi_change": ce_oi,
            "pe_oi_change": pe_oi,
            "higher_tf_trend": trend,
            "timestamp": datetime.now(ist).isoformat(),
        }
