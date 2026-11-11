# Project: NiftyDebateAI Autonomous Full-Stack Overhaul

## Architecture
NiftyDebateAI is a full-stack automated and interactive multi-agent trading debate platform for Indian index derivatives (NIFTY 50 and BANK NIFTY).
The system consists of:
1. **Persistent Dual-Dialect Backend**:
   - FastAPI async web service.
   - Dual-dialect SQLAlchemy 2.x async engine:
     - Production: PostgreSQL (Render Managed Postgres, Supabase, Neon) via `asyncpg` with connection pooling (`AsyncAdaptedQueuePool`) and automatic `postgres://` -> `postgresql+asyncpg://` normalization.
     - Local Dev / Test: SQLite via `aiosqlite` with `NullPool` and WAL mode.
   - JWT authentication subsystem (`PyJWT` HS256) with direct bcrypt password hashing and PBKDF2 fallback.
   - Relational models: `UserModel`, `UserVoteModel`, `UserWatchlistModel`, `DebateSessionModel`, `PaperTradeModel`.
   - Dual-tier dependency injection: `get_current_user` (strict) and `get_optional_user` (soft/guest-compatible).
2. **Angel One SmartAPI Real-Time Engine**:
   - Async `httpx`-based native SmartAPI client (`backend/angel_client.py`).
   - Dynamic TOTP login (`loginByPassword`) and automatic session token management.
   - Zero-delay live quotes (`/market/v1/quote/`) and multi-timeframe historical candles (`/historical/v1/getCandleData`).
   - Mathematical technical indicator engine (EMA 20/50, RSI 14, VWAP, ATR) derived directly from live exchange candles.
   - Live endpoints for frontend ticker and market overview, plus paper/live order placement capabilities under strict 1-lot risk guardrails.
3. **Modern Responsive Trading Frontend**:
   - React 18 / Vite 5 / Tailwind CSS / Lightweight-Charts.
   - Obsidian dark trading theme (`#080C14`), glass cards, tabular typography, vibrant semantic accents.
   - Header Navbar with live multi-asset ticker ribbon, view switcher tabs (`Live Arena`, `Option Chain`, `Debate Studio`, `My Dashboard`), and User Profile / Auth state.
   - Authentication UI with `AuthModal` (Sign In / Register / Guest fallback), `AuthContext`, and Axios request interceptor.
   - Interactive Debate Arena with Bull vs Bear conviction meter (sentiment tug-of-war), community voting widget, and debate progression stepper.
   - Upgraded Lightweight-Chart with volume histogram and 20/50 EMA overlays.
   - User Dashboard displaying personal paper trading PnL curve, active positions, win rate, and bookmarked debates.

---

## Feature Inventory
Every feature identified during the Survey phase mapped to its execution milestone:

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Persistent Database Engine | Dual-dialect async SQLAlchemy engine supporting PostgreSQL (asyncpg, pooling, URL normalization) with SQLite fallback | M1 | survey_db |
| 2 | Relational Models & Migrations | User, UserVote, UserWatchlist models; nullable user_id on debate_sessions & paper_trades; idempotent migration | M1 | survey_db |
| 3 | Password Hashing Subsystem | Direct bcrypt password hashing with PBKDF2-HMAC-SHA256 fallback | M1 | survey_db |
| 4 | JWT Authentication Subsystem | Token generation, verification, PyJWT HS256, 7-day expiry, config secret key | M1 | survey_db |
| 5 | Auth Dependency Injection | Strict `get_current_user` and soft `get_optional_user` dependencies preserving guest access | M1 | survey_db |
| 6 | Auth API Endpoints | `/api/auth/register`, `/api/auth/login`, `/api/auth/me`, `/api/auth/logout` | M1 | survey_db |
| 7 | User-Scoped Data Endpoints | `/api/user/trades`, `/api/user/trades/summary`, `/api/user/watchlist` (GET/POST/DELETE), `/api/debates/{id}/vote` | M1 | survey_db |
| 8 | Angel One Real-Time Quotes | Full quote (`get_quote`) and batch multi-quotes (`get_multi_quotes`) returning LTP, OHLC, net change, % change | M3 | survey_angel |
| 9 | Real-Time Technical Indicators | Calculate real EMA 20, EMA 50, RSI 14, VWAP, ATR directly from SmartAPI historical candles | M3 | survey_angel |
| 10 | Live Market Endpoints | `/api/market/quotes`, `/api/market/overview`, and updated `/api/status` with Angel One telemetry | M3 | survey_angel |
| 11 | Debate Engine Live Feed Integration | Feed real Angel One live quotes and calculated indicators into Bull and Bear prompts | M3 | survey_angel |
| 12 | Order Execution Wrapper | Paper trade execution against Angel One live prices and live order placement capability in `angel_client.py` | M3 | survey_angel |
| 13 | Design System & Dark Theme | Obsidian theme (`#080C14`), glass cards, tabular numbers, semantic bull/bear accents, animations | M2 | survey_ui |
| 14 | Responsive Navbar & Ticker Ribbon | Header with live Nifty/Bank Nifty quote ribbon, view switcher tabs, market status pill, Angel One status badge | M2 | survey_ui |
| 15 | Frontend Auth UI & Context | `AuthModal` (Sign In / Register), `AuthContext`, JWT in localStorage, Axios bearer interceptor, User profile dropdown | M2 | survey_ui |
| 16 | Interactive Debate Arena | Conviction meter (Bull vs Bear tug-of-war), community voting widget, debate progression stepper | M2 | survey_ui |
| 17 | Enhanced TradingView Chart | Lightweight-Charts with volume histogram pane, 20/50 EMA overlays, 1m/5m/15m/1D timeframe presets | M2 | survey_ui |
| 18 | User Portfolio Dashboard | Personal trading performance stats (PnL, win rate), active positions, saved debates, voting history | M2 | survey_ui |
| 19 | Opaque-Box E2E Test Suite | Comprehensive 4-tier test suite verifying Auth, DB persistence, Angel One quotes, and UI contracts | E2E | survey_all |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | User Auth & Persistent Database | Persistent PostgreSQL + SQLite engine, ORM models, bcrypt/PBKDF2, JWT auth, auth endpoints, user-scoped endpoints | none | PLANNED |
| M3 | Angel One Live Market Capabilities | Batch quotes, live candles, real EMA/RSI/VWAP indicators, market overview API, debate feed integration | none | PLANNED |
| M2 | Modern Responsive UI Overhaul | Modern dark trading aesthetic, Navbar, Ticker ribbon, AuthModal, AuthContext, interactive Debate Arena, charts, User Dashboard | M1, M3 | PLANNED |
| M4 | Final Integration & E2E Test Pass | Pass 100% of Tiers 1-4 E2E tests, followed by Tier 5 adversarial coverage hardening and forensic audit | M1, M2, M3, E2E | PLANNED |

*Note: Milestone numbers correspond to functional tracks. M1 and M3 can execute concurrently; M2 connects with M1 and M3 interfaces; M4 validates full convergence.*

---

## Interface Contracts

### 1. Auth & User Subsystem ↔ Frontend
- `POST /api/auth/register`
  - Request: `{"email": "user@example.com", "username": "trader1", "password": "securepassword", "full_name": "Optional Name"}`
  - Response (201): `{"access_token": "<jwt>", "token_type": "bearer", "user": {"id": 1, "email": "...", "username": "...", "full_name": "..."}}`
- `POST /api/auth/login`
  - Request: `{"username": "trader1", "password": "securepassword"}` (or standard OAuth2 form)
  - Response (200): `{"access_token": "<jwt>", "token_type": "bearer", "user": {"id": 1, ...}}`
- `GET /api/auth/me`
  - Header: `Authorization: Bearer <jwt>`
  - Response (200): `{"id": 1, "email": "...", "username": "...", "full_name": "...", "is_active": true}`
- `GET /api/user/trades` & `GET /api/user/trades/summary`
  - Header: `Authorization: Bearer <jwt>`
  - Response (200): User-scoped trade list and PnL metrics.
- `POST /api/debates/{id}/vote`
  - Header: `Authorization: Bearer <jwt>`
  - Request: `{"vote": "BULL" | "BEAR" | "AGREE_JUDGE" | "DISAGREE_JUDGE", "confidence": 1..10, "comment": "..."}`
  - Response (200): `{"id": 1, "debate_id": id, "vote": "BULL", "created_at": "..."}`

### 2. Angel One SmartAPI ↔ Frontend & Debate Engine
- `GET /api/market/quotes?symbols=NIFTY,BANKNIFTY`
  - Response (200):
    ```json
    {
      "source": "angel_one",
      "timestamp": "2026-11-10T20:00:00+05:30",
      "quotes": {
        "NIFTY": {"ltp": 25044.65, "open": 25076.4, "high": 25084.05, "low": 24838.75, "close": 24986.4, "net_change": 58.25, "percent_change": 0.23},
        "BANKNIFTY": {"ltp": 59741.25, "open": 59219.95, "high": 59741.25, "low": 58877.85, "close": 58850.65, "net_change": 890.6, "percent_change": 1.51}
      }
    }
    ```
- `GET /api/market/candles?symbol=NIFTY&interval=5m&range=5d`
  - Response (200):
    ```json
    {
      "symbol": "NIFTY",
      "interval": "5m",
      "candles": [{"time": 1794218100, "open": 25044.65, "high": 25044.65, "low": 25044.65, "close": 25044.65, "volume": 0}]
    }
    ```
- `GET /api/status`
  - Response (200):
    ```json
    {
      "status": "ok",
      "market_status": "CLOSED" | "OPEN",
      "angel_connected": true,
      "angel_feed_source": "live_exchange" | "cached_close",
      "active_index": "NIFTY"
    }
    ```

---

## Code Layout
```
backend/
├── config.py                     # App settings (DATABASE_URL, JWT settings, Angel credentials, Guardrails)
├── database.py                   # Async engine (PostgreSQL + SQLite), connection pool, ORM models, migrations
├── auth.py                       # Passlib/bcrypt + PBKDF2 hashing, JWT token manager, auth dependencies, router
├── user_routes.py                # User-scoped trades, summaries, watchlists, debate voting router
├── angel_client.py               # Async Angel One SmartAPI client (auth, TOTP, quotes, candles, orders)
├── market_data.py                # Market snapshot builder using live Angel One candles & real indicator math
├── debate_engine.py              # Bull, Bear, and Judge agents consuming live snapshot
├── main.py                       # FastAPI application mounting auth, user, market, and debate routes
└── tests/                        # Backend unit & integration test suite

frontend/
├── src/
│   ├── api.js                    # Axios client with JWT interceptor & API methods
│   ├── main.jsx                  # Entrypoint wrapped in AuthProvider
│   ├── App.jsx                   # Modern responsive layout with Navbar & view switching
│   ├── index.css                 # Obsidian dark theme, glass styles, tabular typography, animations
│   ├── context/
│   │   └── AuthContext.jsx       # Auth provider & state (token, user, login, logout, guest mode)
│   ├── components/
│   │   ├── Navbar.jsx            # Topbar with Logo, Ticker ribbon, View tabs, User profile dropdown
│   │   ├── MarketTicker.jsx      # Live multi-asset quote ticker (Nifty, Bank Nifty, Angel status)
│   │   ├── AuthModal.jsx         # Sign In / Register dialog with guest fallback
│   │   ├── ControlBar.jsx        # Streamlined trading actions (index switcher, debate trigger, scheduler)
│   │   ├── TradingViewChart.jsx  # Lightweight-Chart with Volume & 20/50 EMA overlays
│   │   ├── DebateArena.jsx       # Bull vs Bear cards, SentimentMeter, DebateVoting, DebateStepper
│   │   ├── OptionChainBoard.jsx  # Strike ladder with live quotes and 1-click trade action
│   │   ├── UserDashboard.jsx     # Personal paper portfolio, PnL metrics, saved debates
│   │   ├── ManualTradeModal.jsx  # Guardrail-enforced order ticket
│   │   └── TradeHistory.jsx      # Global & paper trade history
```
