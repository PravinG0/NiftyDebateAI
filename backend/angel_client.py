"""
Angel One SmartAPI Client for live market data and chart candles.

Connects to Angel One's SmartAPI endpoints to fetch 0-delay real-time
NSE / BSE historical candles and live LTP quotes with dynamic TOTP authentication.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
import pyotp

from config import settings

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Symbol to Angel One Exchange, Token, and TradingSymbol Mapping ─────────────
ANGEL_SYMBOL_MAP: dict[str, dict[str, str]] = {
    "NIFTY": {
        "exchange": "NSE",
        "symbol_token": "99926000",
        "trading_symbol": "Nifty 50",
    },
    "NIFTY 50": {
        "exchange": "NSE",
        "symbol_token": "99926000",
        "trading_symbol": "Nifty 50",
    },
    "BANKNIFTY": {
        "exchange": "NSE",
        "symbol_token": "99926009",
        "trading_symbol": "Nifty Bank",
    },
    "BANK NIFTY": {
        "exchange": "NSE",
        "symbol_token": "99926009",
        "trading_symbol": "Nifty Bank",
    },
    "FINNIFTY": {
        "exchange": "NSE",
        "symbol_token": "99926037",
        "trading_symbol": "Nifty Fin Service",
    },
    "MIDCPNIFTY": {
        "exchange": "NSE",
        "symbol_token": "99926074",
        "trading_symbol": "NIFTY MID SELECT",
    },
    "SENSEX": {
        "exchange": "BSE",
        "symbol_token": "99919000",
        "trading_symbol": "Sensex",
    },
    "RELIANCE": {
        "exchange": "NSE",
        "symbol_token": "2885",
        "trading_symbol": "RELIANCE-EQ",
    },
    "HDFCBANK": {
        "exchange": "NSE",
        "symbol_token": "1333",
        "trading_symbol": "HDFCBANK-EQ",
    },
    "INFY": {
        "exchange": "NSE",
        "symbol_token": "1594",
        "trading_symbol": "INFY-EQ",
    },
    "TCS": {
        "exchange": "NSE",
        "symbol_token": "11536",
        "trading_symbol": "TCS-EQ",
    },
    "ICICIBANK": {
        "exchange": "NSE",
        "symbol_token": "4963",
        "trading_symbol": "ICICIBANK-EQ",
    },
    "SBIN": {
        "exchange": "NSE",
        "symbol_token": "3045",
        "trading_symbol": "SBIN-EQ",
    },
}

# ── Interval Translation ───────────────────────────────────────────────────────
INTERVAL_MAP: dict[str, str] = {
    "1m": "ONE_MINUTE",
    "3m": "THREE_MINUTE",
    "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "60m": "ONE_HOUR",
    "1d": "ONE_DAY",
    "1D": "ONE_DAY",
}


class AngelClient:
    """Asynchronous client for Angel One SmartAPI."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_code: Optional[str] = None,
        pin: Optional[str] = None,
        totp_secret: Optional[str] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else getattr(settings, "ANGEL_API_KEY", "")
        self.client_code = client_code if client_code is not None else getattr(settings, "ANGEL_CLIENT_CODE", "")
        self.pin = pin if pin is not None else getattr(settings, "ANGEL_PIN", "")
        self.totp_secret = totp_secret if totp_secret is not None else getattr(settings, "ANGEL_TOTP_SECRET", "")

        self._jwt_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._feed_token: Optional[str] = None
        self._token_expiry_epoch: float = 0.0
        self._auth_lock: Optional[asyncio.Lock] = None

        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._candle_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._ltp_cache: dict[str, tuple[float, float]] = {}

    def is_configured(self) -> bool:
        """Check if all necessary Angel One credentials are present."""
        return bool(
            self.api_key
            and self.client_code
            and self.pin
            and self.totp_secret
        )

    async def get_http_client(self) -> httpx.AsyncClient:
        """Return or instantiate an async HTTP client bound to current event loop."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._loop != loop:
            self._loop = loop
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self) -> None:
        """Close underlying HTTP connections."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _base_headers(self) -> dict[str, str]:
        """Generate base SmartAPI headers."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "157.51.15.74",
            "X-MACAddress": "fe80::216e:6507:4b83:3797",
            "X-PrivateKey": self.api_key,
        }

    async def authenticate(self, force_refresh: bool = False) -> bool:
        """Log in via SmartAPI generateSession using dynamic TOTP."""
        if not self.is_configured():
            logger.debug("Angel One SmartAPI is not fully configured.")
            return False

        if self._auth_lock is None:
            self._auth_lock = asyncio.Lock()

        async with self._auth_lock:
            now = time.time()
            if not force_refresh and self._jwt_token and now < self._token_expiry_epoch:
                return True

            try:
                totp_code = pyotp.TOTP(self.totp_secret).now()
                payload = {
                    "clientcode": self.client_code,
                    "password": self.pin,
                    "totp": totp_code,
                }
                client = await self.get_http_client()
                resp = await client.post(
                    "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword",
                    json=payload,
                    headers=self._base_headers(),
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("status") is True:
                    token_data = data.get("data", {})
                    self._jwt_token = token_data.get("jwtToken")
                    self._refresh_token = token_data.get("refreshToken")
                    self._feed_token = token_data.get("feedToken")
                    # Tokens usually valid 24h, expire internally in 12h for freshness
                    self._token_expiry_epoch = now + (12 * 3600)
                    logger.info("Angel One SmartAPI authentication succeeded for %s", self.client_code)
                    return True
                else:
                    logger.warning(
                        "Angel One SmartAPI login rejected: %s (msg: %s)",
                        data.get("errorcode"),
                        data.get("message"),
                    )
                    return False
            except Exception as exc:
                logger.error("Exception authenticating with Angel One SmartAPI: %s", exc)
                return False

    async def get_candles(
        self,
        symbol: str = "NIFTY",
        interval: str = "5m",
        range_period: str = "5d",
    ) -> list[dict[str, Any]]:
        """Fetch historical/intraday candles from Angel One."""
        sym = symbol.upper()
        mapping = ANGEL_SYMBOL_MAP.get(sym)
        if not mapping:
            # Fallback search without spaces or suffixes
            clean_sym = sym.replace(" ", "").replace("-EQ", "")
            mapping = ANGEL_SYMBOL_MAP.get(clean_sym)

        if not mapping:
            logger.debug("No Angel One token mapping found for %s", sym)
            return []

        angel_interval = INTERVAL_MAP.get(interval.lower(), INTERVAL_MAP.get(interval, "FIVE_MINUTE"))

        # Check in-memory TTL cache (5s for intraday, 60s for daily)
        cache_key = f"{mapping['symbol_token']}_{angel_interval}_{range_period.lower()}"
        now = time.time()
        ttl = 60.0 if angel_interval == "ONE_DAY" else 5.0
        if cache_key in self._candle_cache:
            cached_time, cached_candles = self._candle_cache[cache_key]
            if now - cached_time < ttl:
                return [c.copy() for c in cached_candles]

        # Authenticate if needed
        if not await self.authenticate():
            return []

        # Determine date ranges in IST
        now_dt = datetime.now(tz=IST)
        days = 5
        if range_period in ("1d", "1D"):
            days = 1
        elif range_period in ("5d", "5D"):
            days = 5
        elif range_period in ("7d", "7D"):
            days = 7
        elif range_period in ("1mo", "1m"):
            days = 30
        elif range_period in ("3mo", "3m"):
            days = 90
        elif range_period in ("6mo", "1y"):
            days = 180

        # For daily candles, ensure we grab enough historical trading days
        if angel_interval == "ONE_DAY" and days < 30:
            days = 60

        from_date = (now_dt - timedelta(days=days)).strftime("%Y-%m-%d 09:15")
        to_date = now_dt.strftime("%Y-%m-%d 15:30")

        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self._jwt_token}"

        payload = {
            "exchange": mapping["exchange"],
            "symboltoken": mapping["symbol_token"],
            "interval": angel_interval,
            "fromdate": from_date,
            "todate": to_date,
        }

        try:
            client = await self.get_http_client()
            resp = await client.post(
                "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData",
                json=payload,
                headers=headers,
            )

            # If token expired unexpectedly, re-authenticate once
            if resp.status_code in (401, 403):
                logger.info("Token expired, refreshing Angel One session...")
                if await self.authenticate(force_refresh=True):
                    headers["Authorization"] = f"Bearer {self._jwt_token}"
                    resp = await client.post(
                        "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData",
                        json=payload,
                        headers=headers,
                    )

            if resp.status_code == 200:
                data = resp.json()
                raw_candles = data.get("data") or []
                if not raw_candles:
                    return []

                candles: list[dict[str, Any]] = []
                last_time = -1

                for row in raw_candles:
                    # Format: [timestamp_str, open, high, low, close, volume]
                    if len(row) < 5:
                        continue
                    ts_str, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
                    try:
                        # ISO 8601 string: 2026-11-09T15:15:00+05:30
                        dt = datetime.fromisoformat(ts_str)
                        t = int(dt.timestamp())
                        o_f = float(o)
                        h_f = float(h)
                        l_f = float(l)
                        c_f = float(c)
                    except (ValueError, TypeError):
                        continue

                    if not (math.isfinite(o_f) and math.isfinite(h_f) and math.isfinite(l_f) and math.isfinite(c_f)):
                        continue
                    if min(o_f, h_f, l_f, c_f) <= 0:
                        continue

                    # Lightweight-charts strictly ascending timestamp check
                    if t <= last_time:
                        continue

                    candles.append({
                        "time": t,
                        "open": round(o_f, 2),
                        "high": round(max(h_f, o_f, c_f), 2),
                        "low": round(min(l_f, o_f, c_f), 2),
                        "close": round(c_f, 2),
                    })
                    last_time = t

                if candles:
                    self._candle_cache[cache_key] = (now, candles)
                    return [c.copy() for c in candles]

        except Exception as exc:
            logger.warning("Error fetching Angel One candles for %s: %s", sym, exc)

        return []

    async def get_ltp(self, symbol: str = "NIFTY") -> Optional[float]:
        """Fetch real-time Last Traded Price (LTP) from Angel One."""
        sym = symbol.upper()
        mapping = ANGEL_SYMBOL_MAP.get(sym)
        if not mapping:
            clean_sym = sym.replace(" ", "").replace("-EQ", "")
            mapping = ANGEL_SYMBOL_MAP.get(clean_sym)

        if not mapping:
            return None

        # 2-second in-memory cache for high-frequency requests
        now = time.time()
        token = mapping["symbol_token"]
        if token in self._ltp_cache:
            cached_time, cached_ltp = self._ltp_cache[token]
            if now - cached_time < 2.0:
                return cached_ltp

        if not await self.authenticate():
            return None

        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self._jwt_token}"

        payload = {
            "exchange": mapping["exchange"],
            "tradingsymbol": mapping["trading_symbol"],
            "symboltoken": token,
        }

        try:
            client = await self.get_http_client()
            resp = await client.post(
                "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/getLtpData",
                json=payload,
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                ltp = data.get("data", {}).get("ltp")
                if ltp is not None:
                    ltp_val = round(float(ltp), 2)
                    self._ltp_cache[token] = (now, ltp_val)
                    return ltp_val
        except Exception as exc:
            logger.debug("Error fetching Angel One LTP for %s: %s", sym, exc)

        return None


# ── Global Singleton ──────────────────────────────────────────────────────────
_angel_client_instance: Optional[AngelClient] = None


def get_angel_client() -> AngelClient:
    """Return the shared AngelClient singleton."""
    global _angel_client_instance
    if _angel_client_instance is None:
        _angel_client_instance = AngelClient()
    return _angel_client_instance
