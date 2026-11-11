# NiftyDebateAI Test Infrastructure (`TEST_INFRA.md`)

## 1. Overview & Architecture

The NiftyDebateAI E2E test infrastructure provides an opaque-box, requirement-driven automated verification framework for all core application subsystems:
1. **User Authentication & Identity Subsystem** (Registration, login, JWT token management, profile retrieval, logout).
2. **Database Persistence & Tenant Scoping** (User-scoped trades, user-scoped watchlist, user debate votes, persistent database URI normalization, guest access preservation).
3. **Angel One SmartAPI Real-Time Capabilities** (Live LTP/quotes, batch quotes, historical candles, real indicator calculations, connection status).
4. **Full Debate Workflow & Risk Guardrails** (Debate execution with optional user association, paper trade execution under 1-lot limits, PnL tracking).

Testing is structured in **four distinct tiers** of escalating complexity:
- **Tier 1 (Feature Coverage)**: Happy-path verification ensuring every feature in `PROJECT.md` satisfies its primary interface contract ($\ge 5$ test cases per feature, total $\ge 20$).
- **Tier 2 (Boundary & Corner Cases)**: Stress and negative verification testing edge conditions, invalid formats, missing fields, duplicate values, extreme parameters, and expired/tampered tokens ($\ge 5$ test cases per feature, total $\ge 20$).
- **Tier 3 (Cross-Feature Combinations)**: Multi-step pairwise interactions across modules (Register $\to$ Login $\to$ Run Debate $\to$ Cast Vote $\to$ User Trade Summary; Logout $\to$ Guest Mode Fallback; Token Invalidation).
- **Tier 4 (Real-World Scenarios)**: High-fidelity end-to-end user journeys simulating complete trader lifecycle, multi-user concurrent sessions, and market disruption recovery.

---

## 2. Directory Layout

All E2E test files reside in `backend/tests/e2e/`:

```
backend/tests/e2e/
├── conftest.py                   # Shared pytest fixtures, db isolation, client injection, mock factories
├── test_tier1_features.py        # Tier 1: Core feature coverage (Auth, Scoping, Quotes, Debate)
├── test_tier2_boundaries.py      # Tier 2: Boundary, corner cases, and negative testing
├── test_tier3_interactions.py    # Tier 3: Cross-feature pairwise workflows
└── test_tier4_scenarios.py       # Tier 4: Real-world end-to-end multi-agent trading sessions
```

---

## 3. Test Runner & Execution Commands

### Prerequisites
- Python 3.12+
- Dependencies installed: `fastapi`, `httpx`, `pytest`, `pytest-asyncio`, `sqlalchemy`, `aiosqlite`, `pyjwt`, `pydantic`.

### Running All E2E Tests
To execute the complete 4-tier E2E test suite:
```bash
pytest backend/tests/e2e/ -v
```

### Running Specific Tiers
```bash
# Tier 1 only (Feature Coverage)
pytest backend/tests/e2e/test_tier1_features.py -v

# Tier 2 only (Boundary & Corner Cases)
pytest backend/tests/e2e/test_tier2_boundaries.py -v

# Tier 3 only (Cross-Feature Combinations)
pytest backend/tests/e2e/test_tier3_interactions.py -v

# Tier 4 only (Real-World Scenarios)
pytest backend/tests/e2e/test_tier4_scenarios.py -v
```

### Output Filtering & Reporting
```bash
# Short summary with duration
pytest backend/tests/e2e/ -v --durations=10

# Run specific test function
pytest backend/tests/e2e/test_tier1_features.py -k "test_auth_register_success" -v
```

---

## 4. Fixture Architecture (`conftest.py`)

The test harness provides modular, self-contained fixtures ensuring tests do not produce cross-test contamination:

1. **`clean_db`**:
   - Autouse fixture that ensures database schema initialization via `create_tables()`.
   - Cleans up `users`, `user_debate_votes`, `user_watchlists`, `paper_trades`, and `debate_sessions` before and after each test case.
   - Preserves database isolation regardless of whether running SQLite or PostgreSQL.

2. **`e2e_client`**:
   - Provides an asynchronous `httpx.AsyncClient` bound to the FastAPI application via `ASGITransport(app=app)`.
   - Enables native async endpoint dispatching matching production ASGI performance.

3. **`sync_client`**:
   - Provides a synchronous `fastapi.testclient.TestClient(app)` for rapid synchronous HTTP verification.

4. **`registered_user` & `auth_headers`**:
   - Pre-seeds a unique registered test user and generates a signed JWT bearer token.
   - Returns `{ "Authorization": "Bearer <token>" }` ready for injection into protected endpoints.

5. **`mock_angel_client`**:
   - Provides controlled mock responses for Angel One SmartAPI quotes, candles, and order books.
   - Shields test runs from external network outages, rate limits, or off-market hours while preserving schema fidelity.

---

## 5. Interface Contracts & Expected Behaviors

### 1. Authentication (`/api/auth`)
| Endpoint | Method | Input | Success Status | Key Response Fields |
|---|---|---|---|---|
| `/register` | POST | `{email, username, password, full_name}` | 200 / 201 | `access_token`, `token_type`, `user: {id, email, username}` |
| `/login` | POST | `{username_or_email, password}` | 200 | `access_token`, `token_type`, `user` |
| `/me` | GET | Header: `Bearer <token>` | 200 | `{id, email, username, is_active, created_at}` |
| `/logout` | POST | Header: `Bearer <token>` | 200 | `status: "success"` |

### 2. User-Scoped Data (`/api/user`)
| Endpoint | Method | Auth Required | Purpose |
|---|---|---|---|
| `/api/user/trades` | GET | Strict | Lists trades belonging strictly to authenticated user |
| `/api/user/trades/summary` | GET | Strict | Computes user-specific PnL, win rate, best/worst trade |
| `/api/user/watchlist` | GET/POST | Strict | Manages personal strike/contract watchlist |
| `/api/user/watchlist/{id}` | DELETE | Strict | Removes item owned by user (404 if owned by other) |
| `/api/debates/{id}/vote` | POST | Strict | Casts or updates community consensus vote |
| `/api/debates/{id}/votes` | GET | Public | Returns aggregated vote breakdown for debate |

### 3. Angel One Market Data (`/api/market`, `AngelClient`)
| Endpoint / Method | Purpose | Key Attributes |
|---|---|---|
| `AngelClient.get_quote(symbol)` | Live quote | `spot_price, open, high, low, close, net_change, percent_change` |
| `AngelClient.get_multi_quotes(symbols)` | Batch multi-symbol quote | Keyed dictionary of live quotes |
| `AngelClient.calculate_indicators(candles)` | Derived indicators | `ema_20, ema_50, rsi_14, vwap, atr` |
| `/api/market/quotes` | Frontend ticker quote API | `{source: "angel_one", timestamp, quotes: {...}}` |
| `/api/status` | System telemetry | `angel_connected, angel_feed_source, market_status` |

### 4. Debate & Paper Trading (`/api/debate`, `/api/trades`)
| Endpoint | Method | Auth Mode | Behavior |
|---|---|---|---|
| `/api/debate/run` | POST | Soft (Optional) | Links debate & trade to `user_id` if authenticated; anonymous if guest |
| `/api/trades/manual` | POST | Soft (Optional) | Creates guardrail-compliant 1-lot trade linked to user if logged in |
| `/api/trades/{id}/close` | PATCH/POST | Public / Scoped | Closes active position, calculates realized PnL, updates status |

---

## 6. Progressive Testability & Test Isolation Guarantees

- **Progressive Testability**: Tests dynamically test endpoints available on the FastAPI application, seamlessly accommodating both current development milestones and final converged deployment.
- **Hermetic Isolation**: All test cases generate distinct emails and usernames (`uuid4()`), operate in transactional or cleaned tables, and explicitly tear down created sessions.
- **Adversarial Integrity**: Tests assert actual mathematical constraints (1-lot boundaries, strike ranges, risk-reward ratios, JWT signatures) rather than hardcoded dummy values.
