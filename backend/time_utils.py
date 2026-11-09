"""
Canonical IST Timezone and Formatting Utilities for NiftyDebateAI.

Standardizes all timestamp handling across schemas, database models,
scheduler, and API endpoints to Indian Standard Time (Asia/Kolkata, UTC+05:30).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo

# Canonical IST Timezone & Fixed Offset
IST = ZoneInfo("Asia/Kolkata")
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))


def to_ist(dt: Optional[Union[datetime, str]] = None) -> datetime:
    """Canonical IST converter: standardizes naive, timezone-aware datetimes,
    or ISO date strings to Asia/Kolkata.

    - If dt is None: returns current time in IST.
    - If dt is naive: replaces tzinfo with IST (+05:30).
    - If dt is timezone-aware: converts to IST via .astimezone(IST).
    - If dt is a string: parses ISO format or requirement format, then normalizes.
    """
    if dt is None:
        return datetime.now(IST)

    if isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt)
        except ValueError:
            cleaned = dt.replace(" IST", "").strip()
            parsed = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
        return to_ist(parsed)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def ist_now() -> datetime:
    """Return the current wall-clock datetime in Indian Standard Time (IST)."""
    return datetime.now(IST)


def to_ist_iso(dt: Optional[Union[datetime, str]] = None) -> str:
    """Return an ISO 8601 string representation with +05:30 offset in IST.

    Example:
        >>> to_ist_iso(datetime(2026, 11, 9, 10, 30, 0))
        '2026-11-09T10:30:00+05:30'
    """
    return to_ist(dt).isoformat()


def format_ist_display(dt: Optional[Union[datetime, str]] = None) -> str:
    """Format datetime into standard requirement string: 'YYYY-MM-DD HH:mm:ss IST'.

    Example:
        >>> format_ist_display(datetime(2026, 11, 9, 10, 30, 45, tzinfo=IST))
        '2026-11-09 10:30:45 IST'
    """
    return to_ist(dt).strftime("%Y-%m-%d %H:%M:%S IST")


# Canonical alias for backward compatibility with test suites
format_ist = format_ist_display


def calculate_countdown_seconds(current_time: Optional[datetime] = None, interval_minutes: int = 5) -> int:
    """Calculate remaining seconds until the next periodic interval mark in IST.

    For a 5-minute schedule, ticks occur at :00, :05, :10, ..., :55.
    """
    ist = to_ist(current_time)
    minute = ist.minute
    second = ist.second
    seconds_past_interval = (minute % interval_minutes) * 60 + second
    remaining = (interval_minutes * 60) - seconds_past_interval
    return remaining if remaining > 0 else (interval_minutes * 60)


def format_countdown_timer(seconds: int) -> str:
    """Format seconds into 'mm:ss' countdown string."""
    mins = max(0, seconds) // 60
    secs = max(0, seconds) % 60
    return f"{mins:02d}:{secs:02d}"


def is_market_open_ist(dt: Optional[datetime] = None) -> bool:
    """Check standard Indian market hours: 09:15:00 to 15:30:00 IST on weekdays."""
    now_ist = to_ist(dt)
    if now_ist.weekday() > 4:  # Saturday = 5, Sunday = 6
        return False
    return (9, 15, 0) <= (now_ist.hour, now_ist.minute, now_ist.second) <= (15, 30, 0)


def is_scheduler_window_ist(dt: Optional[datetime] = None) -> bool:
    """Check periodic debate trading window: 09:20:00 to 15:15:00 IST on weekdays."""
    now_ist = to_ist(dt)
    if now_ist.weekday() > 4:
        return False
    return (9, 20) <= (now_ist.hour, now_ist.minute) <= (15, 15)
