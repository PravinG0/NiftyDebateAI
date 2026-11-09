"""
Main FastAPI application entrypoint for NiftyDebateAI.

Exposes REST APIs for:
- System and OpenAlgo status (/api/status)
- AI debate orchestration (/api/debate/*)
- Paper trading lifecycle & history (/api/trades/*)
- Market hours scheduler controls (/api/scheduler/*)
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    BUDGET_LIMIT,
    INDEX_CONFIG,
    BudgetExceededError,
    get_index_config,
    settings,
    validate_paper_trade_guardrails,
)
from database import (
    DebateSessionModel,
    PaperTradeModel,
    async_session_factory,
    create_tables,
    engine,
    get_session,
)
from debate_engine import DebateEngine
from market_data import build_market_snapshot, get_openalgo_client
from openalgo_client import OpenAlgoClient, SafetyError
from scheduler import (
    get_scheduler,
    is_market_open,
    pause_scheduler,
    resume_scheduler,
    scheduler_status,
    start_scheduler,
    stop_scheduler,
    trigger_debate_now,
)
from schemas import ManualTradeRequest, StatusResponse
from time_utils import (
    IST,
    calculate_countdown_seconds,
    format_countdown_timer,
    format_ist_display,
    ist_now,
    to_ist_iso,
)


class DebateRunRequest(BaseModel):
    symbol: Optional[str] = Field(default="NIFTY", description="Index symbol: NIFTY or BANKNIFTY")

logger = logging.getLogger("niftydebate.api")


# ── Lifespan Context Manager ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle management."""
    logger.info("Initializing NiftyDebateAI FastAPI service...")

    # 1. Initialize SQLite tables
    try:
        await create_tables()
        logger.info("Database tables initialized successfully.")
    except Exception as exc:
        logger.error("Failed to initialize database tables: %s", exc, exc_info=True)

    # 2. Warm up OpenAlgo client
    try:
        client = await get_openalgo_client()
        is_analyzer = await client.check_analyzer_mode()
        logger.info("OpenAlgo connection verified (analyzer_mode=%s)", is_analyzer)
    except Exception as exc:
        logger.warning("Initial OpenAlgo connection check failed: %s", exc)

    # 3. Initialize scheduler
    try:
        start_scheduler()
        logger.info("Market hours scheduler started.")
    except Exception as exc:
        logger.warning("Could not auto-start scheduler on boot: %s", exc)

    yield

    # Shutdown sequence
    logger.info("Shutting down NiftyDebateAI FastAPI service...")
    try:
        stop_scheduler()
    except Exception as exc:
        logger.error("Error stopping scheduler: %s", exc)

    try:
        client = await get_openalgo_client()
        await client.close()
    except Exception as exc:
        logger.error("Error closing OpenAlgo client: %s", exc)

    try:
        await engine.dispose()
    except Exception as exc:
        logger.error("Error disposing database engine: %s", exc)
    logger.info("NiftyDebateAI shutdown complete.")


app = FastAPI(
    title="NiftyDebateAI Backend",
    description="3-Agent Adversarial Debate & Automated Options Paper Trading System",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS Middleware ───────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Helper Schemas ──────────────────────────────────────────────────

class CloseTradeRequest(BaseModel):
    exit_price: Optional[float] = Field(
        None,
        ge=0.0,
        description="Optional manual exit price override (must be >= 0.0)",
    )


# ── Response Formatting Helpers ───────────────────────────────────────────────

def format_debate_dict(
    session_id: int,
    ts: datetime,
    spot_price: float,
    market_data: dict,
    bull_args: list[str],
    bull_confidence: int,
    bull_strike: str,
    bear_args: list[str],
    bear_confidence: int,
    bear_strike: str,
    judge_dec: str,
    judge_reasoning: str,
    judge_full: dict | None,
    trade_info: dict | None = None,
    index_symbol: str = "NIFTY",
) -> dict[str, Any]:
    judge_full = judge_full or {}
    entry_price = float(judge_full.get("entry_price_estimate", judge_full.get("entry_price", 0.0)) or 0.0)
    sl = float(judge_full.get("stop_loss", 0.0) or 0.0)
    target = float(judge_full.get("target", 0.0) or 0.0)
    raw_rr = str(judge_full.get("risk_reward_ratio", judge_full.get("risk_reward", "1:2.0")))
    rr_stripped = raw_rr[2:] if raw_rr.startswith("1:") else raw_rr
    risk_warning = str(judge_full.get("risk_warning", ""))
    strike = str(judge_full.get("selected_strike") or (bull_strike if judge_dec == "BUY_CE" else bear_strike) or "")

    bull_dict = {
        "bias": "BULLISH",
        "confidence": bull_confidence or 7,
        "confidence_score": bull_confidence or 7,
        "recommended_strike": bull_strike or "",
        "strike": bull_strike or "",
        "arguments": bull_args or [],
        "key_arguments": bull_args or [],
        "reasons": bull_args or [],
        "key_levels": {},
    }

    bear_dict = {
        "bias": "BEARISH",
        "confidence": bear_confidence or 7,
        "confidence_score": bear_confidence or 7,
        "recommended_strike": bear_strike or "",
        "strike": bear_strike or "",
        "arguments": bear_args or [],
        "key_arguments": bear_args or [],
        "reasons": bear_args or [],
        "key_levels": {},
    }

    judge_dict = {
        "verdict": judge_dec or "NO_TRADE",
        "decision": judge_dec or "NO_TRADE",
        "action": judge_dec or "NO_TRADE",
        "strike": strike,
        "recommended_strike": strike,
        "selected_strike": strike,
        "entry_price": entry_price,
        "entry": entry_price,
        "entry_price_estimate": entry_price,
        "stop_loss": sl,
        "sl": sl,
        "target": target,
        "target_price": target,
        "risk_reward": rr_stripped,
        "risk_reward_ratio": raw_rr,
        "reasoning": judge_reasoning or "",
        "winning_argument": judge_reasoning or "",
        "winning_argument_summary": judge_reasoning or "",
        "summary": judge_reasoning or "",
        "rationale": judge_reasoning or "",
        "risk_warning": risk_warning,
        "risk_note": risk_warning,
        "warning": risk_warning,
    }

    ts_iso = to_ist_iso(ts)
    ts_display = format_ist_display(ts)

    res: dict[str, Any] = {
        "id": session_id,
        "timestamp": ts_iso,
        "created_at": ts_iso,
        "timestamp_ist": ts_display,
        "spot_price": spot_price,
        "nifty_spot": spot_price,
        "index_symbol": index_symbol,
        "symbol": index_symbol,
        "decision": judge_dec or "NO_TRADE",
        "verdict": judge_dec or "NO_TRADE",
        "summary": judge_reasoning or "",
        "bull": bull_dict,
        "bull_output": bull_dict,
        "bull_analysis": bull_dict,
        "bear": bear_dict,
        "bear_output": bear_dict,
        "bear_analysis": bear_dict,
        "judge": judge_dict,
        "judge_decision": judge_dict,
        "judge_verdict": judge_dict,
        "market_snapshot": market_data or {},
        "trade": trade_info,
        "trade_created": trade_info is not None,
        "success": True,
    }
    # Nested debate key for envelope consumers
    res["debate"] = {
        "id": session_id,
        "timestamp": ts_iso,
        "created_at": ts_iso,
        "timestamp_ist": ts_display,
        "spot_price": spot_price,
        "nifty_spot": spot_price,
        "index_symbol": index_symbol,
        "symbol": index_symbol,
        "bull": bull_dict,
        "bull_output": bull_dict,
        "bear": bear_dict,
        "bear_output": bear_dict,
        "judge": judge_dict,
        "judge_decision": judge_dict,
        "decision": judge_dec or "NO_TRADE",
        "trade": trade_info,
    }
    return res


def format_db_debate_session(row: DebateSessionModel, trade_info: dict | None = None) -> dict[str, Any]:
    market_data = {}
    if row.market_data_json:
        try:
            market_data = json.loads(row.market_data_json)
        except Exception:
            pass

    bull_args = []
    if row.bull_reasoning:
        try:
            bull_args = json.loads(row.bull_reasoning) if row.bull_reasoning.startswith("[") else [row.bull_reasoning]
        except Exception:
            bull_args = [row.bull_reasoning]

    bear_args = []
    if row.bear_reasoning:
        try:
            bear_args = json.loads(row.bear_reasoning) if row.bear_reasoning.startswith("[") else [row.bear_reasoning]
        except Exception:
            bear_args = [row.bear_reasoning]

    judge_full = {}
    if row.judge_full_output:
        try:
            judge_full = json.loads(row.judge_full_output)
        except Exception:
            pass

    return format_debate_dict(
        session_id=row.id,
        ts=row.timestamp,
        spot_price=row.spot_price,
        market_data=market_data,
        bull_args=bull_args,
        bull_confidence=row.bull_confidence or 7,
        bull_strike=row.bull_strike or "",
        bear_args=bear_args,
        bear_confidence=row.bear_confidence or 7,
        bear_strike=row.bear_strike or "",
        judge_dec=row.judge_decision or "NO_TRADE",
        judge_reasoning=row.judge_reasoning or "",
        judge_full=judge_full,
        trade_info=trade_info,
        index_symbol=getattr(row, "index_symbol", "NIFTY") or "NIFTY",
    )


def format_trade_dict(trade: PaperTradeModel) -> dict[str, Any]:
    opened_iso = to_ist_iso(trade.opened_at) if isinstance(trade.opened_at, datetime) else str(trade.opened_at)
    closed_iso = to_ist_iso(trade.closed_at) if isinstance(trade.closed_at, datetime) else (str(trade.closed_at) if trade.closed_at else None)
    opened_display = format_ist_display(trade.opened_at) if isinstance(trade.opened_at, datetime) else None
    closed_display = format_ist_display(trade.closed_at) if isinstance(trade.closed_at, datetime) else None

    idx_sym = getattr(trade, "index_symbol", None) or ("BANKNIFTY" if "BANK" in (trade.option_symbol or "") else "NIFTY")
    lot_size = 15 if "BANK" in idx_sym else 25
    qty = trade.quantity or lot_size
    lots = max(1, qty // lot_size)
    total_amount = round(trade.entry_price * qty, 2)
    current_exit = trade.exit_price if trade.exit_price is not None else trade.entry_price
    current_value = round(current_exit * qty, 2)
    pnl_pct = (
        round(((current_exit - trade.entry_price) / trade.entry_price) * 100, 2)
        if trade.entry_price > 0 and trade.pnl is not None
        else 0.0
    )

    return {
        "id": trade.id,
        "debate_id": trade.debate_id,
        "option_symbol": trade.option_symbol,
        "symbol": trade.option_symbol,
        "trading_symbol": trade.option_symbol,
        "index_symbol": idx_sym,
        "trade_type": trade.trade_type,
        "type": trade.trade_type,
        "side": trade.trade_type,
        "entry_price": trade.entry_price,
        "entry": trade.entry_price,
        "live_price": current_exit,
        "cmp": current_exit,
        "current_price": current_exit,
        "exit_price": trade.exit_price,
        "exit": trade.exit_price,
        "stop_loss": trade.stop_loss,
        "sl": trade.stop_loss,
        "target": trade.target,
        "target_price": trade.target,
        "pnl": trade.pnl,
        "realized_pnl": trade.pnl if trade.status != "OPEN" else None,
        "unrealized_pnl": trade.pnl if trade.status == "OPEN" else None,
        "current_pnl": trade.pnl,
        "status": trade.status,
        "opened_at": opened_iso,
        "buy_time": opened_iso,
        "created_at": opened_iso,
        "timestamp": opened_iso,
        "entry_time": opened_iso,
        "opened_at_display": opened_display,
        "closed_at": closed_iso,
        "closed_at_display": closed_display,
        "quantity": qty,
        "lots": lots,
        "lot_size": lot_size,
        "total_amount": total_amount,
        "invested_amount": total_amount,
        "current_value": current_value,
        "pnl_percent": pnl_pct,
        "trade_source": getattr(trade, "trade_source", "AI_DEBATE"),
    }


# ── System Health & Status Endpoints ──────────────────────────────────────────

@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    """Simple health check endpoint returning service operational status."""
    return {"status": "ok", "service": "NiftyDebateAI", "time": to_ist_iso(ist_now())}


@app.get("/api/market/candles")
async def get_market_candles(
    symbol: str = "NIFTY",
    interval: str = "5m",
    range_period: str = "5d"
) -> dict[str, Any]:
    """Return candlestick data for NIFTY or BANKNIFTY using Dhan or fallback simulator."""
    try:
        from dhan_client import get_dhan_client
        dhan = get_dhan_client()
        candles = await dhan.get_candles(symbol=symbol, interval=interval, range_period=range_period)
        return {"status": "success", "symbol": symbol, "candles": candles}
    except Exception as exc:
        logger.warning("Failed to fetch market candles for %s: %s", symbol, exc)
        return {"status": "error", "symbol": symbol, "candles": []}


@app.get("/api/status")
async def get_system_status(
    symbol: Optional[str] = Query("NIFTY"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return health, Dhan connection, OpenAlgo connection, analyzer/sandbox mode, spot prices, and scheduler status."""
    client = await get_openalgo_client()
    from dhan_client import get_dhan_client
    dhan = get_dhan_client()

    openalgo_connected = False
    analyzer_mode = False
    try:
        analyzer_mode = await client.check_analyzer_mode()
        openalgo_connected = True
    except Exception as exc:
        logger.warning("OpenAlgo status check failed: %s", exc)

    # Database health check
    database_ok = False
    try:
        await session.execute(select(1))
        database_ok = True
    except Exception as exc:
        logger.error("Database health query failed: %s", exc)

    # Real spot prices from Dhan / Real Exchange Feed
    try:
        nifty_spot = await dhan.get_ltp("NIFTY")
    except Exception:
        nifty_spot = dhan.get_ltp_sync("NIFTY")

    try:
        banknifty_spot = await dhan.get_ltp("BANKNIFTY")
    except Exception:
        banknifty_spot = dhan.get_ltp_sync("BANKNIFTY")

    sel_sym = (symbol or "NIFTY").upper()
    if sel_sym not in ("NIFTY", "BANKNIFTY"):
        sel_sym = "NIFTY"
    lot_size = 15 if "BANK" in sel_sym else 25
    spot_price = banknifty_spot if "BANK" in sel_sym else nifty_spot

    dhan_conn = await dhan.get_connection_status()
    sched = scheduler_status()
    is_sched_running = sched.get("running", False)
    next_run_val = sched.get("next_run")
    next_display = sched.get("next_run_display")
    countdown_sec = calculate_countdown_seconds(next_run_val)
    countdown_tmr = format_countdown_timer(countdown_sec)

    return {
        "status": "ok",
        "dhan_status": dhan_conn.get("status", "FALLBACK"),
        "dhan_connected": dhan_conn.get("dhan_connected", False),
        "dhan_source": dhan_conn.get("source", "TIER_3"),
        "dhan_tier": dhan_conn.get("dhan_tier", 3),
        "dhan_details": dhan_conn.get("details", "Operating normally"),
        "selected_index": sel_sym,
        "symbol": sel_sym,
        "nifty_spot": nifty_spot,
        "banknifty_spot": banknifty_spot,
        "spot_price": spot_price,
        "openalgo_connected": openalgo_connected,
        "analyzer_mode": analyzer_mode,
        "sandbox_mode": analyzer_mode,
        "paper_trading_mode": True,
        "virtual_capital": 5000.0,
        "capital": 5000.0,
        "budget_limit": 5000.0,
        "max_stop_loss_inr": 700.0,
        "max_target_inr": 1000.0,
        "lot_size": lot_size,
        "max_lots": 1,
        "scheduler_running": is_sched_running,
        "next_debate_ist": next_display or next_run_val,
        "next_run_display": next_display,
        "countdown_seconds": countdown_sec,
        "countdown_timer": countdown_tmr,
        "database_ok": database_ok,
        "market_open": is_market_open(),
        "message": "NiftyDebateAI operational — Dhan Market Data Active",
    }


# ── Debate Endpoints ──────────────────────────────────────────────────────────

@app.post("/api/debate/run")
async def run_debate_endpoint(
    payload: Optional[DebateRunRequest] = Body(None),
    symbol: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Trigger an on-demand debate session (Bull -> Bear -> Judge) and return verdict."""
    idx_symbol = "NIFTY"
    if symbol:
        idx_symbol = symbol.strip().upper()
    elif payload and payload.symbol:
        idx_symbol = payload.symbol.strip().upper()

    try:
        debate_result = await trigger_debate_now(bypass_market_hours=True, symbol=idx_symbol)
    except SafetyError as exc:
        logger.error("Safety violation during debate: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Safety Error: {exc}",
        )
    except Exception as exc:
        logger.error("Debate execution failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Debate failed: {exc}",
        )

    if debate_result is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Debate failed to produce results",
        )

    # Query latest trade associated with this debate
    trade_info = None
    if debate_result.id:
        trade_stmt = select(PaperTradeModel).where(PaperTradeModel.debate_id == debate_result.id)
        trade_row = (await session.execute(trade_stmt)).scalars().first()
        if trade_row:
            trade_info = format_trade_dict(trade_row)

    return format_debate_dict(
        session_id=debate_result.id or 0,
        ts=debate_result.timestamp,
        spot_price=debate_result.market_snapshot.spot_price,
        market_data=debate_result.market_snapshot.model_dump(),
        bull_args=debate_result.bull_output.arguments,
        bull_confidence=debate_result.bull_output.confidence,
        bull_strike=debate_result.bull_output.recommended_strike,
        bear_args=debate_result.bear_output.arguments,
        bear_confidence=debate_result.bear_output.confidence,
        bear_strike=debate_result.bear_output.recommended_strike,
        judge_dec=debate_result.judge_decision.decision,
        judge_reasoning=debate_result.judge_decision.winning_argument_summary,
        judge_full=debate_result.judge_decision.model_dump(),
        trade_info=trade_info,
        index_symbol=idx_symbol,
    )


@app.get("/api/debate/latest")
async def get_latest_debate(
    symbol: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Retrieve the most recent debate session and decision."""
    stmt = select(DebateSessionModel).order_by(desc(DebateSessionModel.id))
    if symbol:
        stmt = stmt.where(DebateSessionModel.index_symbol == symbol.strip().upper())
    stmt = stmt.limit(1)
    res = await session.execute(stmt)
    latest = res.scalars().first()
    if not latest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No debate sessions found")

    trade_stmt = select(PaperTradeModel).where(PaperTradeModel.debate_id == latest.id)
    trade_row = (await session.execute(trade_stmt)).scalars().first()
    trade_info = format_trade_dict(trade_row) if trade_row else None

    return format_db_debate_session(latest, trade_info=trade_info)


@app.get("/api/debate/history")
async def get_debate_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    symbol: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Paginated retrieval of historical debate rounds."""
    count_stmt = select(func.count(DebateSessionModel.id))
    stmt = select(DebateSessionModel).order_by(desc(DebateSessionModel.id))
    if symbol:
        count_stmt = count_stmt.where(DebateSessionModel.index_symbol == symbol.strip().upper())
        stmt = stmt.where(DebateSessionModel.index_symbol == symbol.strip().upper())
    total = (await session.execute(count_stmt)).scalar() or 0

    stmt = stmt.offset(offset).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    debates = [format_db_debate_session(row) for row in rows]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "debates": debates,
    }


@app.get("/api/debate/{id}")
async def get_debate_by_id(id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Retrieve a single debate round by primary key ID."""
    stmt = select(DebateSessionModel).where(DebateSessionModel.id == id)
    row = (await session.execute(stmt)).scalars().first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Debate session {id} not found",
        )

    trade_stmt = select(PaperTradeModel).where(PaperTradeModel.debate_id == row.id)
    trade_row = (await session.execute(trade_stmt)).scalars().first()
    trade_info = format_trade_dict(trade_row) if trade_row else None

    return format_db_debate_session(row, trade_info=trade_info)


# ── Paper Trade Endpoints ─────────────────────────────────────────────────────

@app.get("/api/trades")
async def get_all_trades(
    envelope: bool = Query(False, description="Wrap in {trades, success, count} envelope"),
    format: Optional[str] = Query(None, description="Optional format: 'envelope' or 'list'"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Retrieve all paper trades (active and closed) with P&L."""
    stmt = select(PaperTradeModel).order_by(desc(PaperTradeModel.id))
    rows = (await session.execute(stmt)).scalars().all()
    trade_list = [format_trade_dict(t) for t in rows]

    if envelope or format == "envelope":
        return {
            "success": True,
            "count": len(trade_list),
            "trades": trade_list,
        }
    return trade_list


@app.get("/api/trades/active")
async def get_active_trades(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    """Retrieve currently open paper trades with live CMP and unrealized P&L."""
    stmt = (
        select(PaperTradeModel)
        .where(PaperTradeModel.status == "OPEN")
        .order_by(desc(PaperTradeModel.id))
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return []

    from market_data import calculate_option_premium, parse_option_symbol
    from dhan_client import get_dhan_client
    dhan = get_dhan_client()

    spots = {
        "NIFTY": dhan.get_ltp_sync("NIFTY"),
        "BANKNIFTY": dhan.get_ltp_sync("BANKNIFTY"),
    }

    result = []
    for t in rows:
        d = format_trade_dict(t)
        idx_sym = d.get("index_symbol") or "NIFTY"
        spot = spots.get(idx_sym, 24986.40 if idx_sym == "NIFTY" else 52140.80)
        try:
            strike, opt_type = parse_option_symbol(t.option_symbol)
            live_ltp = calculate_option_premium(spot=spot, strike=strike, option_type=opt_type, vix=14.5)
        except Exception:
            live_ltp = t.entry_price

        qty = t.quantity or (15 if "BANK" in idx_sym else 25)
        unrealized_pnl = round((live_ltp - t.entry_price) * qty, 2)
        pnl_pct = round(((live_ltp - t.entry_price) / t.entry_price) * 100, 2) if t.entry_price > 0 else 0.0

        d["live_price"] = live_ltp
        d["cmp"] = live_ltp
        d["current_price"] = live_ltp
        d["pnl"] = unrealized_pnl
        d["unrealized_pnl"] = unrealized_pnl
        d["current_value"] = round(live_ltp * qty, 2)
        d["pnl_percent"] = pnl_pct
        result.append(d)

    return result


@app.get("/api/trades/summary")
async def get_trades_summary(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Retrieve aggregated paper trading statistics (win rate, total P&L, etc.)."""
    stmt = select(PaperTradeModel)
    rows = (await session.execute(stmt)).scalars().all()

    total_trades = len(rows)
    open_trades = sum(1 for t in rows if t.status == "OPEN" or t.exit_price is None)
    closed_trades = sum(1 for t in rows if t.status != "OPEN" and t.exit_price is not None)
    closed_pnls = [t.pnl for t in rows if t.status != "OPEN" and t.pnl is not None]

    total_pnl = round(sum(closed_pnls), 2) if closed_pnls else 0.0
    win_count = sum(1 for p in closed_pnls if p > 0)
    loss_count = sum(1 for p in closed_pnls if p < 0)
    win_rate = round(win_count / len(closed_pnls), 4) if closed_pnls else 0.0
    best_trade = round(max(closed_pnls), 2) if closed_pnls else 0.0
    worst_trade = round(min(closed_pnls), 2) if closed_pnls else 0.0

    return {
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "total_pnl": total_pnl,
        "net_pnl": total_pnl,
        "win_count": win_count,
        "wins": win_count,
        "loss_count": loss_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
    }


@app.patch("/api/trades/{id}/close")
@app.post("/api/trades/{id}/close")
async def close_trade(
    id: int,
    body: Optional[CloseTradeRequest] = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Close an active paper trade and compute realized P&L."""
    stmt = select(PaperTradeModel).where(PaperTradeModel.id == id)
    trade = (await session.execute(stmt)).scalars().first()
    if not trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Trade {id} not found")

    if trade.status != "OPEN" and trade.exit_price is not None:
        return format_trade_dict(trade)

    exit_price = (
        body.exit_price
        if (body is not None and body.exit_price is not None and body.exit_price >= 0.0)
        else None
    )
    if exit_price is None:
        client = await get_openalgo_client()
        try:
            exit_price = await client.get_ltp(trade.option_symbol)
        except Exception:
            exit_price = trade.entry_price

    pnl = round((exit_price - trade.entry_price) * trade.quantity, 2)
    trade_status = "CLOSED"
    if exit_price >= trade.target:
        trade_status = "TARGET_HIT"
    elif exit_price <= trade.stop_loss:
        trade_status = "STOPPED_OUT"

    trade.exit_price = exit_price
    trade.pnl = pnl
    trade.status = trade_status
    trade.closed_at = ist_now()

    await session.commit()
    await session.refresh(trade)
    res = format_trade_dict(trade)
    res["exit_price"] = exit_price
    res["pnl"] = pnl
    res["status"] = trade_status
    res["message"] = (
        f"Squared off {trade.option_symbol} at ₹{exit_price:.2f}. "
        f"Realized {'Profit' if pnl >= 0 else 'Loss'}: {'+' if pnl >= 0 else ''}₹{pnl:.2f} ({res['pnl_percent']}%)"
    )
    return res


@app.post("/api/trades/manual")
async def place_manual_trade(
    req: ManualTradeRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Place a manual paper trade with Angel One style lot and total amount tracking."""
    from market_data import build_market_snapshot, calculate_option_premium, get_nearest_expiry

    idx_symbol = "NIFTY"
    if req.symbol:
        idx_symbol = req.symbol.strip().upper()
    if idx_symbol not in ("NIFTY", "BANKNIFTY"):
        idx_symbol = "NIFTY"

    lot_size = 15 if "BANK" in idx_symbol else 25

    if req.lots != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only 1-lot trading allowed (requested {req.lots} lots).",
        )

    snap = await build_market_snapshot(idx_symbol)
    spot = snap.spot_price

    entry = req.entry_price
    if entry is None or entry <= 0:
        entry = calculate_option_premium(spot=spot, strike=req.strike, option_type=req.option_type, vix=snap.vix)

    qty = req.lots * lot_size
    total_amount = round(entry * qty, 2)

    if total_amount > 5000.0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Total trade outlay ₹{total_amount:.2f} exceeds Rs 5,000 budget limit.",
        )

    try:
        from config import compute_clamped_sl_target, validate_paper_trade_guardrails
        sl, tgt = compute_clamped_sl_target(idx_symbol, entry, req.stop_loss, req.target)
        guardrail = validate_paper_trade_guardrails(
            symbol=idx_symbol,
            entry_price=entry,
            lots=req.lots,
            stop_loss=sl,
            target=tgt,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    expiry = get_nearest_expiry()
    symbol = f"{idx_symbol}{expiry}{req.strike}{req.option_type.upper()}"
    trade_type = f"BUY_{req.option_type.upper()}"

    latest_stmt = select(DebateSessionModel).order_by(desc(DebateSessionModel.id)).limit(1)
    latest_sess = (await session.execute(latest_stmt)).scalars().first()

    if latest_sess is None:
        audit_sess = DebateSessionModel(
            spot_price=spot,
            index_symbol=idx_symbol,
            judge_decision=trade_type,
            judge_reasoning=f"Manual paper trade placed by user: {symbol} at Rs {entry:.2f} (Total Rs {total_amount:.2f})",
        )
        session.add(audit_sess)
        await session.commit()
        await session.refresh(audit_sess)
        debate_id = audit_sess.id
    else:
        debate_id = latest_sess.id

    trade = PaperTradeModel(
        debate_id=debate_id,
        index_symbol=idx_symbol,
        option_symbol=symbol,
        trade_type=trade_type,
        entry_price=entry,
        stop_loss=sl,
        target=tgt,
        status="OPEN",
        quantity=qty,
        trade_source="MANUAL",
    )
    session.add(trade)
    await session.commit()
    await session.refresh(trade)

    return {
        "success": True,
        "message": f"Manual paper trade placed: {symbol} at Rs {entry:.2f} ({req.lots} lot = {qty} qty, Total Rs {total_amount:.2f})",
        "trade": format_trade_dict(trade),
    }


@app.get("/api/market/option-chain")
async def get_market_option_chain(
    symbol: str = "NIFTY",
    strikes: int = Query(7, ge=3, le=15)
) -> dict[str, Any]:
    """Return Option Chain with strike-wise lot amounts, budget validation, and Greeks."""
    from market_data import build_market_snapshot, get_option_chain
    snap = await build_market_snapshot(symbol)
    lot_size = 15 if "BANK" in symbol.upper() else 25
    chain = await get_option_chain(symbol=symbol, strikes=strikes)
    return {
        "status": "success",
        "symbol": symbol,
        "spot_price": snap.spot_price,
        "atm_strike": snap.atm_strike,
        "lot_size": lot_size,
        "chain": chain,
    }


# ── Scheduler Endpoints ───────────────────────────────────────────────────────

@app.get("/api/scheduler/status")
async def get_scheduler_status_endpoint() -> dict[str, Any]:
    """Get current scheduler running state and next execution time."""
    stat = scheduler_status()
    running = stat.get("running", False)
    return {
        "running": running,
        "is_running": running,
        "paused": stat.get("paused", False),
        "is_paused": stat.get("paused", False),
        "state": stat.get("state", "STOPPED"),
        "next_run_time": stat.get("next_run_time"),
        "next_run": stat.get("next_run_time"),
        "interval_minutes": stat.get("interval_minutes", settings.DEBATE_INTERVAL_MINUTES),
        "market_open": stat.get("market_open", False),
        "message": "Scheduler running" if running else "Scheduler stopped",
    }


@app.post("/api/scheduler/start")
async def start_scheduler_endpoint() -> dict[str, Any]:
    """Start periodic debate scheduler."""
    try:
        start_scheduler()
        return {"success": True, "message": "Scheduler started", "status": scheduler_status()}
    except Exception as exc:
        logger.error("Failed to start scheduler: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@app.post("/api/scheduler/pause")
async def pause_scheduler_endpoint() -> dict[str, Any]:
    """Pause periodic debate scheduler."""
    try:
        pause_scheduler()
        return {"success": True, "message": "Scheduler paused", "status": scheduler_status()}
    except Exception as exc:
        logger.error("Failed to pause scheduler: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@app.post("/api/scheduler/resume")
async def resume_scheduler_endpoint() -> dict[str, Any]:
    """Resume periodic debate scheduler."""
    try:
        resume_scheduler()
        return {"success": True, "message": "Scheduler resumed", "status": scheduler_status()}
    except Exception as exc:
        logger.error("Failed to resume scheduler: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@app.post("/api/scheduler/stop")
async def stop_scheduler_endpoint() -> dict[str, Any]:
    """Stop periodic debate scheduler."""
    try:
        stop_scheduler()
        return {"success": True, "message": "Scheduler stopped", "status": scheduler_status()}
    except Exception as exc:
        logger.error("Failed to stop scheduler: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
