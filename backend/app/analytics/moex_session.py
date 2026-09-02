"""MOEX main-session calendar for overnight LiveExecutor (Issue #137).

Wall-clock source is the computer clock, converted to MSK (UTC+3). The session
bounds themselves live in ``trading_config.MOEX_SESSION``.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from app.analytics.trading_config import get_moex_session_config


def now_msk_naive(clock: Optional[Callable[[], datetime]] = None) -> datetime:
    """Naive MSK datetime from an optional clock (already MSK) or UTC now."""
    if clock is not None:
        value = clock()
        if value.tzinfo is not None:
            offset = timedelta(hours=int(get_moex_session_config()["tz_offset_hours"]))
            return value.astimezone(timezone(offset)).replace(tzinfo=None)
        return value
    offset = timedelta(hours=int(get_moex_session_config()["tz_offset_hours"]))
    return datetime.now(timezone.utc).astimezone(timezone(offset)).replace(tzinfo=None)


def _config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return dict(cfg or get_moex_session_config())


def _at_hour(now: datetime, hour: int) -> datetime:
    return now.replace(hour=int(hour), minute=0, second=0, microsecond=0)


def is_session_weekday(now: datetime, cfg: Optional[Dict[str, Any]] = None) -> bool:
    return int(now.weekday()) in tuple(_config(cfg)["weekdays"])


def is_entry_window(now: datetime, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """True during the sandbox entry window [start, end) on a weekday."""
    cfg = _config(cfg)
    start = _at_hour(now, cfg["entry_start_hour"])
    end = _at_hour(now, cfg["entry_end_hour"])
    return is_session_weekday(now, cfg) and start <= now < end


def next_session_open(now: datetime, cfg: Optional[Dict[str, Any]] = None) -> datetime:
    """Start of the current session if still open, otherwise the next weekday open."""
    cfg = _config(cfg)
    today_open = _at_hour(now, cfg["entry_start_hour"])
    today_end = _at_hour(now, cfg["session_end_hour"])
    if is_session_weekday(now, cfg) and now < today_end:
        return today_open
    day = (now + timedelta(days=1)).replace(
        hour=int(cfg["entry_start_hour"]),
        minute=0,
        second=0,
        microsecond=0,
    )
    while not is_session_weekday(day, cfg):
        day += timedelta(days=1)
    return day


def session_end_for_run(now: datetime, cfg: Optional[Dict[str, Any]] = None) -> datetime:
    """19:00 MSK of the session that ``next_session_open`` belongs to."""
    open_at = next_session_open(now, cfg)
    return open_at.replace(
        hour=int(_config(cfg)["session_end_hour"]),
        minute=0,
        second=0,
        microsecond=0,
    )


def minutes_until_session_end(
    now: Optional[datetime] = None,
    cfg: Optional[Dict[str, Any]] = None,
    margin_minutes: Optional[int] = None,
) -> int:
    """Minutes until this run's 19:00 MSK plus a small margin."""
    cfg = _config(cfg)
    if now is None:
        now = now_msk_naive()
    remaining = (session_end_for_run(now, cfg) - now).total_seconds() / 60.0
    margin = (
        int(cfg["duration_margin_minutes"])
        if margin_minutes is None
        else int(margin_minutes)
    )
    return max(1, math.ceil(remaining) + margin)


def protection_end_for_run(
    now: datetime,
    cfg: Optional[Dict[str, Any]] = None,
) -> datetime:
    """Keep streaming until the next session open after this run's 19:00.

    Entries stop at session close; leftover stop/take still need live books.
    """
    return next_session_open(session_end_for_run(now, cfg), cfg)


def minutes_until_stack_end(
    now: Optional[datetime] = None,
    cfg: Optional[Dict[str, Any]] = None,
    margin_minutes: Optional[int] = None,
) -> int:
    """Paper-process duration: until the next open after this session, plus margin."""
    cfg = _config(cfg)
    if now is None:
        now = now_msk_naive()
    remaining = (protection_end_for_run(now, cfg) - now).total_seconds() / 60.0
    margin = (
        int(cfg["duration_margin_minutes"])
        if margin_minutes is None
        else int(margin_minutes)
    )
    return max(1, math.ceil(remaining) + margin)


if __name__ == "__main__":
    now = now_msk_naive()
    print(minutes_until_stack_end(now))
