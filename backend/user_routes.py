"""
User-scoped operations router for NiftyDebateAI.

Covers:
1. User-scoped paper trades and performance metrics (/api/user/trades, /api/user/trades/summary).
2. User-specific watchlist and contract bookmarks (/api/user/watchlist).
3. Community debate voting and consensus (/api/debates/{id}/vote, /api/debates/{id}/votes).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import UserVoteRequest, UserWatchlistRequest, get_current_user
from database import (
    DebateSessionModel,
    PaperTradeModel,
    UserModel,
    UserVoteModel,
    UserWatchlistModel,
    get_session,
)
from time_utils import to_ist_iso

logger = logging.getLogger("niftydebate.user_routes")

user_router = APIRouter(tags=["User Operations"])


# ── User-Scoped Trades & Summary ──────────────────────────────────────────────

@user_router.get("/api/user/trades")
async def get_user_trades(
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Retrieve all paper trades belonging strictly to the authenticated user."""
    stmt = (
        select(PaperTradeModel)
        .where(PaperTradeModel.user_id == current_user.id)
        .order_by(desc(PaperTradeModel.id))
    )
    if status_filter:
        stmt = stmt.where(PaperTradeModel.status == status_filter.upper())

    rows = (await session.execute(stmt)).scalars().all()
    from main import format_trade_dict

    return [format_trade_dict(t) for t in rows]


@user_router.get("/api/user/trades/summary")
async def get_user_trades_summary(
    current_user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Retrieve aggregated trading statistics strictly for the authenticated user."""
    stmt = select(PaperTradeModel).where(PaperTradeModel.user_id == current_user.id)
    rows = (await session.execute(stmt)).scalars().all()

    total_trades = len(rows)
    open_trades = sum(1 for t in rows if t.status == "OPEN" or t.exit_price is None)
    closed_trades = sum(1 for t in rows if t.status != "OPEN" and t.exit_price is not None)
    closed_pnls = [t.pnl for t in rows if t.status != "OPEN" and t.pnl is not None]

    total_pnl = round(sum(closed_pnls), 2) if closed_pnls else 0.0
    win_count = sum(1 for p in closed_pnls if p > 0)
    loss_count = sum(1 for p in closed_pnls if p < 0)
    win_rate = round(win_count / len(closed_pnls), 4) if closed_pnls else 0.0

    return {
        "user_id": current_user.id,
        "username": current_user.username,
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "total_pnl": total_pnl,
        "net_pnl": total_pnl,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "best_trade": round(max(closed_pnls), 2) if closed_pnls else 0.0,
        "worst_trade": round(min(closed_pnls), 2) if closed_pnls else 0.0,
    }


# ── Watchlist Management ──────────────────────────────────────────────────────

@user_router.get("/api/user/watchlist")
async def get_watchlist(
    current_user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Get all watchlisted items for the current authenticated user."""
    stmt = (
        select(UserWatchlistModel)
        .where(UserWatchlistModel.user_id == current_user.id)
        .order_by(desc(UserWatchlistModel.id))
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "symbol": r.symbol,
            "strike": r.strike,
            "option_type": r.option_type,
            "notes": r.notes,
            "created_at": to_ist_iso(r.created_at),
        }
        for r in rows
    ]


@user_router.post("/api/user/watchlist")
async def add_to_watchlist(
    req: UserWatchlistRequest,
    current_user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Add a strike or contract to user watchlist."""
    sym = (req.symbol or "NIFTY").upper()
    opt_type = req.option_type.upper()

    stmt = select(UserWatchlistModel).where(
        UserWatchlistModel.user_id == current_user.id,
        UserWatchlistModel.symbol == sym,
        UserWatchlistModel.strike == req.strike,
        UserWatchlistModel.option_type == opt_type,
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing:
        return {"status": "exists", "id": existing.id, "message": "Already in watchlist"}

    item = UserWatchlistModel(
        user_id=current_user.id,
        symbol=sym,
        strike=req.strike,
        option_type=opt_type,
        notes=req.notes,
    )
    session.add(item)
    try:
        await session.commit()
        await session.refresh(item)
    except IntegrityError:
        await session.rollback()
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            return {"status": "exists", "id": existing.id, "message": "Already in watchlist"}
        raise
    return {
        "status": "success",
        "id": item.id,
        "symbol": item.symbol,
        "strike": item.strike,
        "option_type": item.option_type,
    }


@user_router.delete("/api/user/watchlist/{id}")
async def remove_from_watchlist(
    id: int,
    current_user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Delete an item from user watchlist."""
    stmt = select(UserWatchlistModel).where(
        UserWatchlistModel.id == id,
        UserWatchlistModel.user_id == current_user.id,
    )
    item = (await session.execute(stmt)).scalars().first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watchlist item not found",
        )

    await session.delete(item)
    await session.commit()
    return {"status": "success", "message": f"Watchlist item {id} removed"}


# ── Debate Community Consensus & Voting ────────────────────────────────────────

@user_router.post("/api/debates/{debate_id}/vote")
async def vote_on_debate(
    debate_id: int,
    req: UserVoteRequest,
    current_user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Cast or update user consensus vote on a debate."""
    clean_vote = req.vote.strip().upper()
    if clean_vote not in ("BULL", "BEAR", "AGREE_JUDGE", "DISAGREE_JUDGE"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vote must be one of: BULL, BEAR, AGREE_JUDGE, DISAGREE_JUDGE",
        )

    # Check debate exists
    d_stmt = select(DebateSessionModel).where(DebateSessionModel.id == debate_id)
    debate = (await session.execute(d_stmt)).scalars().first()
    if not debate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Debate session not found",
        )

    # Check existing vote
    v_stmt = select(UserVoteModel).where(
        UserVoteModel.user_id == current_user.id,
        UserVoteModel.debate_id == debate_id,
    )
    vote_obj = (await session.execute(v_stmt)).scalars().first()
    if vote_obj:
        vote_obj.vote = clean_vote
        vote_obj.confidence = req.confidence
        vote_obj.comment = req.comment
    else:
        vote_obj = UserVoteModel(
            user_id=current_user.id,
            debate_id=debate_id,
            vote=clean_vote,
            confidence=req.confidence,
            comment=req.comment,
        )
        session.add(vote_obj)

    try:
        await session.commit()
        await session.refresh(vote_obj)
    except IntegrityError:
        await session.rollback()
        # Handle concurrent vote insertion race: fetch existing vote and update it
        existing_vote = (await session.execute(v_stmt)).scalars().first()
        if existing_vote:
            existing_vote.vote = clean_vote
            existing_vote.confidence = req.confidence
            existing_vote.comment = req.comment
            await session.commit()
            await session.refresh(existing_vote)
            return {"status": "success", "vote_id": existing_vote.id, "vote": existing_vote.vote}
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not record vote due to concurrent conflict.",
        )
    return {"status": "success", "vote_id": vote_obj.id, "vote": vote_obj.vote}


@user_router.get("/api/debates/{debate_id}/votes")
async def get_debate_vote_stats(
    debate_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get aggregated community consensus votes for a debate session."""
    stmt = (
        select(UserVoteModel.vote, func.count(UserVoteModel.id))
        .where(UserVoteModel.debate_id == debate_id)
        .group_by(UserVoteModel.vote)
    )

    counts = dict((await session.execute(stmt)).all())
    total = sum(counts.values())

    return {
        "debate_id": debate_id,
        "total_votes": total,
        "breakdown": {
            "BULL": counts.get("BULL", 0),
            "BEAR": counts.get("BEAR", 0),
            "AGREE_JUDGE": counts.get("AGREE_JUDGE", 0),
            "DISAGREE_JUDGE": counts.get("DISAGREE_JUDGE", 0),
        },
    }
