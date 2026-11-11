"""
Angel One SmartAPI Client for live market data and chart candles.

Connects to Angel One's SmartAPI endpoints to fetch 0-delay real-time
NSE / BSE historical candles and live LTP quotes with dynamic TOTP authentication.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
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
    "^NSEI": {
        "exchange": "NSE",
        "symbol_token": "99926000",
        "trading_symbol": "Nifty 50",
    },
    "%5ENSEI": {
        "exchange": "NSE",
        "symbol_token": "99926000",
        "trading_symbol": "Nifty 50",
    },
    "^NSEBANK": {
        "exchange": "NSE",
        "symbol_token": "99926009",
        "trading_symbol": "Nifty Bank",
    },
    "%5ENSEBANK": {
        "exchange": "NSE",
        "symbol_token": "99926009",
        "trading_symbol": "Nifty Bank",
    },
    "RELIANCE.NS": {
        "exchange": "NSE",
        "symbol_token": "2885",
        "trading_symbol": "RELIANCE-EQ",
    },
    "HDFCBANK.NS": {
        "exchange": "NSE",
        "symbol_token": "1333",
        "trading_symbol": "HDFCBANK-EQ",
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

    _shared_tokens: dict[str, tuple[str, str, str, float]] = {}

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
        self._last_auth_attempt: float = 0.0
        self._auth_cooldown: float = 5.0

        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._candle_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._ltp_cache: dict[str, tuple[float, float]] = {}
        self._quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._paper_orders: list[dict[str, Any]] = []
        self._paper_positions: dict[str, dict[str, Any]] = {}
        self._candle_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def resolve_symbol(cls, symbol: str) -> Optional[dict[str, str]]:
        """Resolve symbol into Angel One exchange, token, and trading_symbol."""
        if not symbol:
            return None
        sym = symbol.strip().upper()
        if sym in ANGEL_SYMBOL_MAP:
            return ANGEL_SYMBOL_MAP[sym]

        clean = sym.replace(" ", "").replace("-EQ", "").replace("^", "")
        if clean in ANGEL_SYMBOL_MAP:
            return ANGEL_SYMBOL_MAP[clean]
        if f"{clean}-EQ" in ANGEL_SYMBOL_MAP:
            return ANGEL_SYMBOL_MAP[f"{clean}-EQ"]

        for k, v in ANGEL_SYMBOL_MAP.items():
            if v.get("symbol_token") == symbol or v.get("trading_symbol", "").upper() == sym:
                return v
        return None

    def is_configured(self) -> bool:
        """Check if all necessary Angel One credentials are present."""
        return bool(
            self.api_key
            and self.client_code
            and self.pin
            and self.totp_secret
        )

    def _ensure_loop(self) -> None:
        """Ensure HTTP client and concurrency locks match the current running event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop != loop:
            self._loop = loop
            self._auth_lock = asyncio.Lock()
            self._candle_locks.clear()
            if self._client is not None and not self._client.is_closed:
                self._client = None

    async def get_http_client(self) -> httpx.AsyncClient:
        """Return or instantiate an async HTTP client bound to current event loop."""
        self._ensure_loop()
        if self._client is None or self._client.is_closed:
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

        self._ensure_loop()
        bound_loop = getattr(self._auth_lock, "_loop", None)
        if self._auth_lock is None or (bound_loop is not None and bound_loop is not self._loop):
            self._auth_lock = asyncio.Lock()

        async with self._auth_lock:
            now = time.time()
            if not force_refresh and self._jwt_token and now < self._token_expiry_epoch:
                return True

            if not force_refresh and self.client_code in AngelClient._shared_tokens:
                s_jwt, s_ref, s_feed, s_exp = AngelClient._shared_tokens[self.client_code]
                if now < s_exp:
                    self._jwt_token = s_jwt
                    self._refresh_token = s_ref
                    self._feed_token = s_feed
                    self._token_expiry_epoch = s_exp
                    return True

            # Cooldown guard: prevent repeated auth hammering under rate limiting
            if now - self._last_auth_attempt < self._auth_cooldown:
                logger.warning(
                    "Angel One authentication cooldown active (skipping login retry within %.1fs)",
                    self._auth_cooldown,
                )
                return bool(self._jwt_token and now < self._token_expiry_epoch)

            self._last_auth_attempt = now

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
                if resp.status_code != 200:
                    logger.warning("Angel One login HTTP %s: %s", resp.status_code, resp.text[:200] if resp.text else "(empty body)")
                    return False

                try:
                    data = resp.json()
                except Exception as json_err:
                    logger.warning("Failed to decode JSON from Angel One login: %s", json_err)
                    return False

                if data.get("status") is True:
                    token_data = data.get("data", {})
                    self._jwt_token = token_data.get("jwtToken")
                    self._refresh_token = token_data.get("refreshToken")
                    self._feed_token = token_data.get("feedToken")
                    # Tokens usually valid 24h, expire internally in 12h for freshness
                    self._token_expiry_epoch = now + (12 * 3600)
                    AngelClient._shared_tokens[self.client_code] = (
                        self._jwt_token,
                        self._refresh_token,
                        self._feed_token,
                        self._token_expiry_epoch,
                    )
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

    def _generate_fallback_candles(
        self,
        symbol: str,
        interval: str = "5m",
        count: int = 60,
    ) -> list[dict[str, Any]]:
        """Generate realistic synthetic candles adhering to lightweight-charts specs if Angel One is rate-limited or offline."""
        sym = symbol.upper()
        if "BANK" in sym:
            base_price = 52140.0
        elif "RELIANCE" in sym:
            base_price = 2850.0
        elif "HDFC" in sym:
            base_price = 1680.0
        elif "INFY" in sym:
            base_price = 1820.0
        elif "ICICI" in sym:
            base_price = 1260.0
        elif "TCS" in sym:
            base_price = 4120.0
        else:
            base_price = 24980.0

        step_seconds = 300
        if "1m" in interval:
            step_seconds = 60
        elif "15m" in interval:
            step_seconds = 900
        elif "1h" in interval or "60m" in interval:
            step_seconds = 3600
        elif "1d" in interval.lower():
            step_seconds = 86400

        now_sec = int(time.time())
        end_time = (now_sec // step_seconds) * step_seconds
        start_time = end_time - (count * step_seconds)

        candles: list[dict[str, Any]] = []
        cur_price = base_price
        for i in range(count):
            t = start_time + (i * step_seconds)
            drift = math.sin(i * 0.25) * (base_price * 0.001)
            o = round(cur_price, 2)
            c = round(cur_price + drift + ((i % 3 - 1) * (base_price * 0.0005)), 2)
            h = round(max(o, c) + (base_price * 0.0008), 2)
            l = round(min(o, c) - (base_price * 0.0008), 2)
            candles.append({
                "time": t,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            })
            cur_price = c

        return candles

    async def get_candles(
        self,
        symbol: str = "NIFTY",
        interval: str = "5m",
        range_period: str = "5d",
    ) -> list[dict[str, Any]]:
        """Fetch historical/intraday candles from Angel One."""
        if not symbol or not str(symbol).strip():
            sym = "NIFTY"
        else:
            sym = str(symbol).strip().upper()

        mapping = ANGEL_SYMBOL_MAP.get(sym)
        if not mapping:
            clean_sym = sym.replace(" ", "").replace("-EQ", "").replace("^", "").replace("%5E", "")
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

        # Concurrency lock per cache_key to coalesce concurrent burst requests
        self._ensure_loop()
        existing_lock = self._candle_locks.get(cache_key)
        bound_loop = getattr(existing_lock, "_loop", None)
        if existing_lock is None or (bound_loop is not None and bound_loop is not self._loop):
            self._candle_locks[cache_key] = asyncio.Lock()

        async with self._candle_locks[cache_key]:
            # Double check cache inside lock
            now = time.time()
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

                # If token expired unexpectedly (HTTP 401, or 403 not caused by rate limiting), re-authenticate once
                is_rate_limited = (
                    resp.status_code == 429
                    or (resp.status_code == 403 and "exceeding access rate" in resp.text.lower())
                )
                if is_rate_limited:
                    logger.warning(
                        "Angel One rate limit encountered on getCandleData (HTTP %s): %s",
                        resp.status_code,
                        resp.text[:120],
                    )
                    if cache_key in self._candle_cache:
                        return [c.copy() for c in self._candle_cache[cache_key][1]]
                    return []
                elif resp.status_code in (401, 403):
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

    async def get_multi_quotes(
        self,
        symbols: list[str],
        ttl: float = 3.0,
    ) -> dict[str, dict[str, Any]]:
        """Fetch quotes for multiple symbols using SmartAPI /market/v1/quote/ with TTL caching."""
        if not symbols:
            return {}

        now = time.time()
        norm_symbols = [s.strip().upper() for s in symbols if s.strip()]
        result: dict[str, dict[str, Any]] = {}
        missing_symbols: list[str] = []

        # 1. Check in-memory quote cache
        for sym in norm_symbols:
            if sym in self._quote_cache:
                cache_time, cached_quote = self._quote_cache[sym]
                if now - cache_time < ttl:
                    result[sym] = cached_quote.copy()
                    continue
            missing_symbols.append(sym)

        if not missing_symbols:
            return result

        # 2. Group tokens by exchange
        exchange_tokens: dict[str, list[str]] = {}
        token_to_syms: dict[tuple[str, str], list[str]] = {}

        for sym in missing_symbols:
            mapping = self.resolve_symbol(sym)
            if not mapping:
                continue
            exch = mapping["exchange"]
            tok = mapping["symbol_token"]
            exchange_tokens.setdefault(exch, [])
            if tok not in exchange_tokens[exch]:
                exchange_tokens[exch].append(tok)
            token_to_syms.setdefault((exch, tok), []).append(sym)

        # 3. Call SmartAPI batch quote endpoint if configured & tokens exist
        if exchange_tokens and await self.authenticate():
            headers = self._base_headers()
            headers["Authorization"] = f"Bearer {self._jwt_token}"
            payload = {
                "mode": "FULL",
                "exchangeTokens": exchange_tokens,
            }

            try:
                client = await self.get_http_client()
                resp = await client.post(
                    "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/",
                    json=payload,
                    headers=headers,
                )

                is_rate_limited = (
                    resp.status_code == 429
                    or (resp.status_code == 403 and "exceeding access rate" in resp.text.lower())
                )
                if is_rate_limited:
                    logger.warning(
                        "Angel One rate limit encountered on get_multi_quotes (HTTP %s): %s",
                        resp.status_code,
                        resp.text[:120],
                    )
                elif resp.status_code in (401, 403):
                    logger.info("Token expired or unauthorized, refreshing Angel One session...")
                    if await self.authenticate(force_refresh=True):
                        headers["Authorization"] = f"Bearer {self._jwt_token}"
                        resp = await client.post(
                            "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/",
                            json=payload,
                            headers=headers,
                        )

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") is True:
                        fetched = data.get("data", {}).get("fetched") or []
                        for item in fetched:
                            tok = str(item.get("symbolToken", ""))
                            exch = str(item.get("exchange", "NSE"))
                            ltp_val = float(item.get("ltp") or 0.0)
                            open_val = float(item.get("open") or ltp_val)
                            high_val = float(item.get("high") or ltp_val)
                            low_val = float(item.get("low") or ltp_val)
                            close_val = float(item.get("close") or ltp_val)
                            net_chg = float(item.get("netChange") or round(ltp_val - close_val, 2))
                            pct_chg = float(
                                item.get("percentChange")
                                or (round((net_chg / close_val) * 100, 2) if close_val > 0 else 0.0)
                            )

                            matched_syms = token_to_syms.get((exch, tok)) or [item.get("tradingSymbol", "")]
                            for ms in matched_syms:
                                if not ms:
                                    continue
                                q = {
                                    "symbol": ms,
                                    "trading_symbol": item.get("tradingSymbol", ms),
                                    "symbol_token": tok,
                                    "exchange": exch,
                                    "spot_price": round(ltp_val, 2),
                                    "ltp": round(ltp_val, 2),
                                    "open": round(open_val, 2),
                                    "high": round(high_val, 2),
                                    "low": round(low_val, 2),
                                    "close": round(close_val, 2),
                                    "net_change": round(net_chg, 2),
                                    "percent_change": round(pct_chg, 2),
                                    "timestamp": datetime.now(tz=IST).isoformat(),
                                }
                                self._quote_cache[ms] = (now, q)
                                self._ltp_cache[tok] = (now, round(ltp_val, 2))
                                result[ms] = q.copy()
            except Exception as exc:
                logger.warning("Error fetching Angel One batch quotes: %s", exc)

        # 4. Fallback for any symbols still missing: attempt get_ltp
        for sym in missing_symbols:
            if sym not in result:
                ltp = await self.get_ltp(sym)
                if ltp is not None and ltp > 0:
                    fb_quote = {
                        "symbol": sym,
                        "trading_symbol": sym,
                        "symbol_token": "",
                        "exchange": "NSE",
                        "spot_price": round(ltp, 2),
                        "ltp": round(ltp, 2),
                        "open": round(ltp, 2),
                        "high": round(ltp, 2),
                        "low": round(ltp, 2),
                        "close": round(ltp, 2),
                        "net_change": 0.0,
                        "percent_change": 0.0,
                        "timestamp": datetime.now(tz=IST).isoformat(),
                    }
                    self._quote_cache[sym] = (now, fb_quote)
                    result[sym] = fb_quote.copy()

        return result

    async def get_quote(self, symbol: str = "NIFTY") -> Optional[dict[str, Any]]:
        """Fetch full quote for a symbol with {spot_price, open, high, low, close, net_change, percent_change}."""
        sym = symbol.strip().upper()
        quotes = await self.get_multi_quotes([sym])
        if sym in quotes:
            return quotes[sym].copy()
        clean = sym.replace(" ", "")
        if clean in quotes:
            return quotes[clean].copy()
        return None

    @staticmethod
    def calculate_indicators(candles: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate mathematical EMA 20, EMA 50, RSI 14, VWAP, ATR directly from candles."""
        if not candles:
            return {
                "ema_20": 0.0,
                "ema_50": 0.0,
                "rsi": 50.0,
                "rsi_14": 50.0,
                "vwap": 0.0,
                "atr": 0.0,
                "atr_14": 0.0,
                "higher_tf_trend": "NEUTRAL",
                "last_close": 0.0,
                "candle_count": 0,
                "cpr_pivot": 0.0,
                "cpr_bc": 0.0,
                "cpr_tc": 0.0,
                "cpr_width": 0.0,
                "cpr_width_type": "NARROW_TRENDING",
                "supertrend_direction": "NEUTRAL",
                "supertrend_value": 0.0,
            }

        n = len(candles)
        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        volumes = [float(c.get("volume", 0) or 0) for c in candles]
        last_close = round(closes[-1], 2)

        if n == 1:
            c = candles[0]
            hl = round(float(c["high"]) - float(c["low"]), 2)
            vwap_val = round((float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0, 2)
            return {
                "ema_20": last_close,
                "ema_50": last_close,
                "rsi": 50.0,
                "rsi_14": 50.0,
                "vwap": vwap_val,
                "atr": hl,
                "atr_14": hl,
                "higher_tf_trend": "BULLISH",
                "last_close": last_close,
                "candle_count": 1,
                "cpr_pivot": last_close,
                "cpr_bc": last_close,
                "cpr_tc": last_close,
                "cpr_width": 0.0,
                "cpr_width_type": "NARROW_TRENDING",
                "supertrend_direction": "BULLISH",
                "supertrend_value": last_close,
            }

        def _calc_ema(span: int) -> float:
            if n < span:
                return sum(closes) / n
            alpha = 2.0 / (span + 1.0)
            ema = sum(closes[:span]) / span
            for price in closes[span:]:
                ema = (price * alpha) + (ema * (1.0 - alpha))
            return ema

        ema_20 = round(_calc_ema(20), 2)
        ema_50 = round(_calc_ema(50), 2)

        # RSI 14 with Wilder's smoothing
        if n < 15:
            rsi = 50.0
        else:
            gains = []
            losses = []
            for i in range(1, n):
                diff = closes[i] - closes[i - 1]
                gains.append(max(0.0, diff))
                losses.append(max(0.0, -diff))
            avg_gain = sum(gains[:14]) / 14.0
            avg_loss = sum(losses[:14]) / 14.0
            for i in range(14, len(gains)):
                avg_gain = (avg_gain * 13.0 + gains[i]) / 14.0
                avg_loss = (avg_loss * 13.0 + losses[i]) / 14.0
            if avg_loss == 0.0:
                rsi = 100.0 if avg_gain > 0.0 else 50.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi = round(max(0.0, min(100.0, rsi)), 2)

        # VWAP: volume-weighted typical price (or mean typical price if volume is zero)
        tot_vol = sum(volumes)
        typ_prices = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
        if tot_vol > 0:
            vwap = round(sum(tp * v for tp, v in zip(typ_prices, volumes)) / tot_vol, 2)
        else:
            vwap = round(sum(typ_prices) / n, 2)

        # ATR 14 with Wilder's smoothing
        tr_list = [highs[0] - lows[0]]
        for i in range(1, n):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_list.append(tr)

        if n <= 14:
            atr = sum(tr_list) / n
        else:
            atr = sum(tr_list[:14]) / 14.0
            for i in range(14, n):
                atr = (atr * 13.0 + tr_list[i]) / 14.0
        atr = round(atr, 2)

        trend = "BULLISH" if last_close >= ema_20 else "BEARISH"

        # CPR (Central Pivot Range) calculated from session bounds
        recent_window = min(n, 75)
        day_high = max(highs[-recent_window:])
        day_low = min(lows[-recent_window:])
        pivot = round((day_high + day_low + last_close) / 3.0, 2)
        bc = round((day_high + day_low) / 2.0, 2)
        tc = round((pivot - bc) + pivot, 2)
        cpr_width = round(abs(tc - bc), 2)
        width_pct = (cpr_width / last_close) * 100.0 if last_close > 0 else 0.0
        cpr_width_type = "NARROW_TRENDING" if width_pct < 0.35 else "WIDE_RANGEBOUND"

        # Supertrend (period 10, multiplier 3.0)
        st_period = min(10, n)
        if st_period > 1 and len(tr_list) >= st_period:
            st_atr = sum(tr_list[-st_period:]) / float(st_period)
        else:
            st_atr = atr
        hl2 = (highs[-1] + lows[-1]) / 2.0
        st_upper = hl2 + (3.0 * st_atr)
        st_lower = hl2 - (3.0 * st_atr)
        st_dir = "BULLISH" if last_close >= st_lower else "BEARISH"
        st_val = round(st_lower if st_dir == "BULLISH" else st_upper, 2)

        return {
            "ema_20": ema_20,
            "ema_50": ema_50,
            "rsi": rsi,
            "rsi_14": rsi,
            "vwap": vwap,
            "atr": atr,
            "atr_14": atr,
            "higher_tf_trend": trend,
            "last_close": last_close,
            "candle_count": n,
            "cpr_pivot": pivot,
            "cpr_bc": min(bc, tc),
            "cpr_tc": max(bc, tc),
            "cpr_width": cpr_width,
            "cpr_width_type": cpr_width_type,
            "supertrend_direction": st_dir,
            "supertrend_value": st_val,
        }

    def get_connection_status_sync(self) -> dict[str, Any]:
        """Return synchronous connection telemetry dictionary."""
        now = time.time()
        configured = self.is_configured()
        authenticated = bool(self._jwt_token and now < self._token_expiry_epoch)
        status = "CONNECTED" if authenticated else ("CONFIGURED" if configured else "DISCONNECTED")
        feed_source = "live_exchange" if authenticated else ("smartapi_rest" if configured else "unavailable")
        expires_in = max(0.0, self._token_expiry_epoch - now) if authenticated else 0.0
        expiry_iso = (
            datetime.fromtimestamp(self._token_expiry_epoch, tz=IST).isoformat()
            if authenticated
            else None
        )
        masked_code = (
            f"{self.client_code[:2]}****{self.client_code[-2:]}"
            if len(self.client_code) > 4
            else self.client_code
        )

        return {
            "status": status,
            "angel_status": status,
            "configured": configured,
            "angel_configured": configured,
            "authenticated": authenticated,
            "angel_connected": authenticated or configured,
            "client_code": masked_code,
            "token_expiry": expiry_iso,
            "token_expiry_epoch": self._token_expiry_epoch,
            "token_expiry_seconds": round(expires_in, 1),
            "feed_source": feed_source,
            "angel_feed_source": feed_source,
            "mode": "PAPER_ANALYZER" if getattr(settings, "FORCE_ANALYZER_MODE", True) else "LIVE",
            "rate_limit_healthy": True,
            "cached_tokens": list(self._ltp_cache.keys()),
            "cached_quotes": list(self._quote_cache.keys()),
            "cached_quotes_count": len(self._quote_cache),
            "last_heartbeat": datetime.now(tz=IST).isoformat(),
        }

    async def get_connection_status(self) -> dict[str, Any]:
        """Return connection telemetry, attempting authentication once if configured."""
        if self.is_configured() and (not self._jwt_token or time.time() >= self._token_expiry_epoch):
            try:
                await self.authenticate()
            except Exception as exc:
                logger.debug("Automatic authentication during get_connection_status failed: %s", exc)
        return self.get_connection_status_sync()

    async def place_order(
        self,
        symbol: str,
        transaction_type: str = "BUY",
        quantity: int = 1,
        order_type: str = "MARKET",
        price: float = 0.0,
        product_type: str = "INTRADAY",
        exchange: Optional[str] = None,
        symbol_token: Optional[str] = None,
        is_paper: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Place an order in paper mode or via real Angel One SmartAPI."""
        sym_up = symbol.strip().upper()
        trans_up = transaction_type.strip().upper()
        order_type_up = order_type.strip().upper()
        prod_up = product_type.strip().upper()

        if trans_up not in ("BUY", "SELL"):
            raise ValueError(f"Invalid transaction_type: {transaction_type}. Must be 'BUY' or 'SELL'.")
        if quantity <= 0:
            raise ValueError(f"Invalid quantity: {quantity}. Must be positive.")

        # Determine execution mode: paper vs live
        force_paper = getattr(settings, "FORCE_ANALYZER_MODE", True)
        live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
        if is_paper is None:
            is_paper = force_paper or (not live_enabled)

        mapping = self.resolve_symbol(sym_up)
        token = symbol_token or (mapping.get("symbol_token") if mapping else "99926000")
        exch = exchange or (mapping.get("exchange") if mapping else "NSE")
        trading_symbol = mapping.get("trading_symbol") if mapping else sym_up

        if is_paper:
            # Determine paper fill price
            if price > 0 and order_type_up == "LIMIT":
                fill_price = round(float(price), 2)
            else:
                quote = await self.get_quote(sym_up)
                fill_price = round(float(quote["spot_price"]), 2) if quote else (round(float(price), 2) if price > 0 else 100.0)

            order_id = f"PAPER_{int(time.time() * 1000)}_{len(self._paper_orders) + 1}"
            order_record = {
                "order_id": order_id,
                "orderid": order_id,
                "symbol": sym_up,
                "trading_symbol": trading_symbol,
                "symbol_token": token,
                "transaction_type": trans_up,
                "quantity": quantity,
                "price": fill_price,
                "average_price": fill_price,
                "order_type": order_type_up,
                "product_type": prod_up,
                "exchange": exch,
                "order_status": "COMPLETE",
                "status": "COMPLETE",
                "placed_at": datetime.now(tz=IST).isoformat(),
                "is_paper": True,
                "mode": "PAPER",
            }
            self._paper_orders.append(order_record)

            # Update paper positions
            pos_key = f"{sym_up}_{prod_up}"
            existing_pos = self._paper_positions.get(pos_key)
            if not existing_pos:
                net_qty = quantity if trans_up == "BUY" else -quantity
                self._paper_positions[pos_key] = {
                    "symbol": sym_up,
                    "trading_symbol": trading_symbol,
                    "symbol_token": token,
                    "exchange": exch,
                    "product_type": prod_up,
                    "net_quantity": net_qty,
                    "quantity": net_qty,
                    "buy_quantity": quantity if trans_up == "BUY" else 0,
                    "sell_quantity": quantity if trans_up == "SELL" else 0,
                    "average_price": fill_price,
                    "ltp": fill_price,
                    "unrealized_pnl": 0.0,
                    "pnl": 0.0,
                }
            else:
                curr_qty = existing_pos["net_quantity"]
                if trans_up == "BUY":
                    new_qty = curr_qty + quantity
                    if new_qty > 0 and curr_qty >= 0:
                        avg_p = ((curr_qty * existing_pos["average_price"]) + (quantity * fill_price)) / new_qty
                    else:
                        avg_p = existing_pos["average_price"] if new_qty != 0 else fill_price
                    existing_pos["buy_quantity"] += quantity
                else:
                    new_qty = curr_qty - quantity
                    avg_p = existing_pos["average_price"]
                    existing_pos["sell_quantity"] += quantity

                existing_pos["net_quantity"] = new_qty
                existing_pos["quantity"] = new_qty
                existing_pos["average_price"] = round(avg_p, 2)
                existing_pos["ltp"] = fill_price

            logger.info("Paper order placed: %s %d %s @ %.2f (order_id=%s)", trans_up, quantity, sym_up, fill_price, order_id)
            return {
                "status": "success",
                "order_id": order_id,
                "orderid": order_id,
                "mode": "PAPER",
                "data": order_record,
                "message": f"Paper order executed: {trans_up} {quantity} {sym_up} @ Rs {fill_price:.2f}",
            }

        # Live execution via SmartAPI
        if not await self.authenticate():
            return {
                "status": "error",
                "message": "Authentication failed; cannot place live order.",
            }

        payload = {
            "variety": "NORMAL",
            "tradingsymbol": trading_symbol,
            "symboltoken": token,
            "transactiontype": trans_up,
            "exchange": exch,
            "ordertype": order_type_up,
            "producttype": prod_up,
            "duration": "DAY",
            "price": str(price) if price > 0 else "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(quantity),
        }

        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self._jwt_token}"

        try:
            client = await self.get_http_client()
            resp = await client.post(
                "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/placeOrder",
                json=payload,
                headers=headers,
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("status") is True:
                order_id = data.get("data", {}).get("orderid", "")
                return {
                    "status": "success",
                    "order_id": order_id,
                    "orderid": order_id,
                    "mode": "LIVE",
                    "data": data.get("data"),
                    "message": data.get("message", "Live order placed successfully"),
                }
            else:
                return {
                    "status": "error",
                    "mode": "LIVE",
                    "message": data.get("message", "Order rejected by SmartAPI"),
                    "errorcode": data.get("errorcode"),
                }
        except Exception as exc:
            logger.error("Exception during live order placement: %s", exc)
            return {"status": "error", "mode": "LIVE", "message": str(exc)}

    async def get_order_book(self, paper_only: bool = False) -> list[dict[str, Any]]:
        """Retrieve order book (paper orders and/or SmartAPI live orders)."""
        orders = [o.copy() for o in self._paper_orders]
        if paper_only or not self._jwt_token:
            return orders

        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self._jwt_token}"
        try:
            client = await self.get_http_client()
            resp = await client.get(
                "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/getOrderBook",
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") is True:
                    live_orders = data.get("data") or []
                    return list(live_orders) + orders
        except Exception as exc:
            logger.debug("Failed to fetch live order book: %s", exc)
        return orders

    async def get_positions(self, paper_only: bool = False) -> list[dict[str, Any]]:
        """Retrieve open positions with live P&L updates."""
        positions = []
        for pos in self._paper_positions.values():
            p = pos.copy()
            cached_ltp = self._ltp_cache.get(p["symbol_token"], (0, p["average_price"]))[1]
            p["ltp"] = cached_ltp
            net_qty = p["net_quantity"]
            if net_qty > 0:
                pnl = round((cached_ltp - p["average_price"]) * net_qty, 2)
            elif net_qty < 0:
                pnl = round((p["average_price"] - cached_ltp) * abs(net_qty), 2)
            else:
                pnl = 0.0
            p["unrealized_pnl"] = pnl
            p["pnl"] = pnl
            positions.append(p)

        if paper_only or not self._jwt_token:
            return positions

        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self._jwt_token}"
        try:
            client = await self.get_http_client()
            resp = await client.get(
                "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/getPosition",
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") is True:
                    live_pos = data.get("data") or []
                    return list(live_pos) + positions
        except Exception as exc:
            logger.debug("Failed to fetch live positions: %s", exc)
        return positions

    async def cancel_order(self, order_id: str, is_paper: Optional[bool] = None) -> dict[str, Any]:
        """Cancel an order by ID (paper or SmartAPI)."""
        if is_paper or order_id.startswith("PAPER_"):
            for order in self._paper_orders:
                if order.get("order_id") == order_id or order.get("orderid") == order_id:
                    order["status"] = "CANCELLED"
                    order["order_status"] = "CANCELLED"
                    return {"status": "success", "order_id": order_id, "message": "Paper order cancelled"}
            return {"status": "error", "message": f"Order {order_id} not found in paper book"}

        if not await self.authenticate():
            return {"status": "error", "message": "Authentication required to cancel live order"}

        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self._jwt_token}"
        payload = {"variety": "NORMAL", "orderid": order_id}
        try:
            client = await self.get_http_client()
            resp = await client.post(
                "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/cancelOrder",
                json=payload,
                headers=headers,
            )
            return resp.json()
        except Exception as exc:
            return {"status": "error", "message": str(exc)}



# ── Global Singleton ──────────────────────────────────────────────────────────
_angel_client_instance: Optional[AngelClient] = None


def get_angel_client() -> AngelClient:
    """Return the shared AngelClient singleton."""
    global _angel_client_instance
    if _angel_client_instance is None:
        _angel_client_instance = AngelClient()
    return _angel_client_instance
