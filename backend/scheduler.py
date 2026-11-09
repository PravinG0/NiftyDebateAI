"""
APScheduler-based scheduler for NiftyDebateAI.

Runs :meth:`DebateEngine.run_debate` every N minutes during Indian market
hours (09:20–15:15 IST, weekdays only).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING, STATE_STOPPED
from apscheduler.triggers.interval import IntervalTrigger

from config import settings
from time_utils import IST, format_ist_display, to_ist, to_ist_iso

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_IST = IST
_debate_lock: asyncio.Lock | None = None


# ── Market hours boundaries parsed once ───────────────────────────────────────

_open_h, _open_m = (int(x) for x in settings.MARKET_OPEN.split(":"))
_close_h, _close_m = (int(x) for x in settings.MARKET_CLOSE.split(":"))


def _get_debate_lock() -> asyncio.Lock:
    """Return an asyncio.Lock bound to the current running event loop.

    In Python 3.10+, asyncio.Lock binds to the active event loop upon contention.
    If the event loop changes or closes (e.g. across tests or server reload),
    the lock is cleanly re-instantiated to prevent RuntimeError: bound to a
    different event loop.
    """
    global _debate_lock
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if _debate_lock is None:
        _debate_lock = asyncio.Lock()
        return _debate_lock

    bound_loop = getattr(_debate_lock, "_loop", None)
    if bound_loop is not None and (bound_loop.is_closed() or (loop is not None and bound_loop is not loop)):
        _debate_lock = asyncio.Lock()

    return _debate_lock


def is_market_open(check_time: Optional[datetime] = None) -> bool:
    """Return True if current or provided IST time is within configured market
    hours on a weekday (Mon-Fri).

    Handles:
    - check_time is None: current time in IST
    - check_time is naive: assumed to already represent IST (tzinfo replaced)
    - check_time is timezone-aware (UTC, IST, etc.): converted to IST via .astimezone(_IST)
    """
    if check_time is None:
        now = datetime.now(_IST)
    elif check_time.tzinfo is None:
        now = check_time.replace(tzinfo=_IST)
    else:
        now = check_time.astimezone(_IST)

    # Weekday check: 0=Mon … 4=Fri
    if now.weekday() > 4:
        return False

    current_minutes = now.hour * 60 + now.minute
    open_minutes = _open_h * 60 + _open_m
    close_minutes = _close_h * 60 + _close_m
    return open_minutes <= current_minutes <= close_minutes


# Backward compatibility alias
_is_market_open = is_market_open


async def trigger_debate_now(
    bypass_market_hours: bool = True,
    engine: Optional[Any] = None,
    symbol: str = "NIFTY",
) -> Optional[Any]:
    """Execute a single debate round on-demand asynchronously.

    Parameters:
        bypass_market_hours: If True (default for manual triggers), runs even
            outside 09:20–15:15 IST or on weekends. If False, skips if market is closed.
        engine: Optional pre-configured DebateEngine instance for testing / DI.
        symbol: Underlying index symbol ("NIFTY" or "BANKNIFTY").

    Returns:
        The completed DebateSession, or None if skipped due to market closure.
    """
    if not bypass_market_hours and not is_market_open():
        logger.info("Market closed — debate execution skipped.")
        return None

    if engine is None:
        from debate_engine import DebateEngine
        engine = DebateEngine()

    lock = _get_debate_lock()
    async with lock:
        try:
            try:
                result = await engine.run_debate(symbol=symbol)
            except TypeError:
                result = await engine.run_debate()
            logger.info(
                "Debate #%s completed — decision: %s",
                getattr(result, "id", "unknown"),
                getattr(getattr(result, "judge_decision", None), "decision", "unknown"),
            )
            return result
        except Exception as exc:
            logger.error("Debate execution failed: %s", exc, exc_info=True)
            raise


async def _debate_job() -> None:
    """Periodic tick callback triggered by APScheduler every 5 minutes."""
    try:
        await trigger_debate_now(bypass_market_hours=False)
    except Exception as exc:
        logger.error("Scheduled periodic debate tick failed: %s", exc, exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_event_loop(sched: AsyncIOScheduler) -> None:
    """Ensure the scheduler's and all executors' event loop references match the current running loop."""
    try:
        loop = asyncio.get_running_loop()
        if getattr(sched, "_eventloop", None) is None or sched._eventloop.is_closed() or sched._eventloop is not loop:
            sched._eventloop = loop

        # Also update any AsyncIOExecutor instances managed by APScheduler
        executors = getattr(sched, "_executors", {})
        if isinstance(executors, dict):
            for executor in executors.values():
                exec_loop = getattr(executor, "_eventloop", None)
                if exec_loop is None or exec_loop.is_closed() or exec_loop is not loop:
                    executor._eventloop = loop
    except RuntimeError:
        pass


def get_scheduler() -> AsyncIOScheduler:
    """Return the singleton scheduler, creating it if necessary.

    If an existing scheduler was bound to an event loop that is now closed,
    it is cleanly shutdown and discarded, and a new scheduler instance is created.
    """
    global _scheduler
    if _scheduler is not None:
        sched_loop = getattr(_scheduler, "_eventloop", None)
        if sched_loop is not None and sched_loop.is_closed():
            logger.info("Existing scheduler event loop is closed; reinitializing scheduler.")
            try:
                if _scheduler.running:
                    _scheduler.shutdown(wait=False)
            except Exception:
                pass
            _scheduler = None

    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=_IST)
    _ensure_event_loop(_scheduler)
    return _scheduler


def start_scheduler() -> None:
    """Add the debate job and start the scheduler or resume if paused."""
    sched = get_scheduler()
    _ensure_event_loop(sched)

    # If currently paused, resume non-destructively
    if sched.state == STATE_PAUSED:
        sched.resume()
        logger.info("Scheduler resumed from paused state via start_scheduler().")
        return

    # Avoid duplicate jobs on repeated start calls
    if sched.get_job("debate_job") is None:
        sched.add_job(
            _debate_job,
            trigger=IntervalTrigger(
                minutes=settings.DEBATE_INTERVAL_MINUTES,
                timezone=_IST,
            ),
            id="debate_job",
            name="NiftyDebateAI periodic debate",
            replace_existing=True,
        )

    if not sched.running:
        sched.start()
        logger.info(
            "Scheduler started — interval=%d min, market window %s–%s IST",
            settings.DEBATE_INTERVAL_MINUTES,
            settings.MARKET_OPEN,
            settings.MARKET_CLOSE,
        )


def pause_scheduler() -> None:
    """Non-destructively pause scheduled debate processing."""
    global _scheduler
    if _scheduler is None or _scheduler.state == STATE_STOPPED:
        logger.warning("Cannot pause scheduler: scheduler is not running.")
        return
    if _scheduler.state == STATE_PAUSED:
        logger.warning("Scheduler is already paused.")
        return

    _ensure_event_loop(_scheduler)
    _scheduler.pause()
    logger.info("Scheduler paused non-destructively.")


def resume_scheduler() -> None:
    """Resume job processing from a paused state."""
    global _scheduler
    if _scheduler is None or _scheduler.state == STATE_STOPPED:
        logger.info("Scheduler was stopped; starting via resume_scheduler().")
        start_scheduler()
        return
    if _scheduler.state == STATE_RUNNING:
        logger.warning("Scheduler is already running.")
        return

    _ensure_event_loop(_scheduler)
    _scheduler.resume()
    logger.info("Scheduler resumed.")


def stop_scheduler() -> None:
    """Shut down the scheduler cleanly (destructive stop)."""
    global _scheduler, _debate_lock
    if _scheduler is not None:
        try:
            if getattr(_scheduler, "_eventloop", None) is not None and not _scheduler._eventloop.is_closed():
                if _scheduler.running:
                    _scheduler.shutdown(wait=False)
            elif _scheduler.running:
                _scheduler.state = STATE_STOPPED
        except Exception as exc:
            logger.debug("Scheduler stop exception: %s", exc)
        finally:
            _scheduler = None
    _debate_lock = None
    logger.info("Scheduler stopped.")


def scheduler_status() -> dict[str, Any]:
    """Return a status dict about the scheduler."""
    global _scheduler
    if _scheduler is None or _scheduler.state == STATE_STOPPED:
        return {
            "running": False,
            "is_running": False,
            "paused": False,
            "is_paused": False,
            "state": "STOPPED",
            "next_run": None,
            "next_run_time": None,
            "next_run_display": None,
            "interval_minutes": settings.DEBATE_INTERVAL_MINUTES,
            "market_open": is_market_open(),
        }

    is_running = (_scheduler.state == STATE_RUNNING)
    is_paused = (_scheduler.state == STATE_PAUSED)

    next_run: str | None = None
    next_run_display: str | None = None
    job = _scheduler.get_job("debate_job")
    if job and job.next_run_time:
        next_run = to_ist_iso(job.next_run_time)
        next_run_display = format_ist_display(job.next_run_time)

    state_name = "RUNNING" if is_running else ("PAUSED" if is_paused else "STOPPED")
    return {
        "running": is_running,
        "is_running": is_running,
        "paused": is_paused,
        "is_paused": is_paused,
        "state": state_name,
        "next_run": next_run,
        "next_run_time": next_run,
        "next_run_display": next_run_display,
        "interval_minutes": settings.DEBATE_INTERVAL_MINUTES,
        "market_open": is_market_open(),
    }
