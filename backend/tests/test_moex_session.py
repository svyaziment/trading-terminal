from datetime import datetime, timezone

from app.analytics.moex_session import (
    is_entry_window,
    minutes_until_session_end,
    minutes_until_stack_end,
    next_session_open,
    now_msk_naive,
    protection_end_for_run,
    session_end_for_run,
)
from app.analytics.trading_config import MOEX_SESSION, get_moex_session_config


def test_moex_session_defaults_are_weekdays_ten_to_nineteen():
    cfg = get_moex_session_config()
    assert cfg["entry_start_hour"] == 10
    assert cfg["entry_end_hour"] == 19
    assert cfg["session_end_hour"] == 19
    assert cfg["weekdays"] == (0, 1, 2, 3, 4)
    assert MOEX_SESSION["tz_offset_hours"] == 3


def test_sunday_night_waits_for_monday_open():
    now = datetime(2026, 8, 30, 23, 0, 0)
    assert not is_entry_window(now)
    assert next_session_open(now) == datetime(2026, 8, 31, 10, 0, 0)
    assert session_end_for_run(now) == datetime(2026, 8, 31, 19, 0, 0)
    assert minutes_until_session_end(now, margin_minutes=15) == 20 * 60 + 15


def test_monday_morning_before_open_waits_same_day():
    now = datetime(2026, 8, 31, 9, 30, 0)
    assert not is_entry_window(now)
    assert next_session_open(now) == datetime(2026, 8, 31, 10, 0, 0)
    assert minutes_until_session_end(now, margin_minutes=0) == 570


def test_monday_session_is_open_until_nineteen():
    assert is_entry_window(datetime(2026, 8, 31, 10, 0, 0))
    assert is_entry_window(datetime(2026, 8, 31, 18, 59, 59))
    assert not is_entry_window(datetime(2026, 8, 31, 19, 0, 0))
    now = datetime(2026, 8, 31, 11, 0, 0)
    assert next_session_open(now) == datetime(2026, 8, 31, 10, 0, 0)
    assert session_end_for_run(now) == datetime(2026, 8, 31, 19, 0, 0)
    assert minutes_until_session_end(now, margin_minutes=15) == 8 * 60 + 15
    assert protection_end_for_run(now) == datetime(2026, 9, 1, 10, 0, 0)
    assert minutes_until_stack_end(now, margin_minutes=15) == 23 * 60 + 15


def test_sunday_night_paper_stack_covers_leftover_stop_take():
    now = datetime(2026, 8, 30, 23, 0, 0)
    assert protection_end_for_run(now) == datetime(2026, 9, 1, 10, 0, 0)
    assert minutes_until_stack_end(now, margin_minutes=15) == 35 * 60 + 15


def test_friday_evening_skips_weekend_to_monday():
    now = datetime(2026, 8, 28, 19, 30, 0)
    assert not is_entry_window(now)
    assert next_session_open(now) == datetime(2026, 8, 31, 10, 0, 0)
    assert session_end_for_run(now) == datetime(2026, 8, 31, 19, 0, 0)


def test_now_msk_naive_converts_aware_utc():
    clock = lambda: datetime(2026, 8, 31, 7, 0, 0, tzinfo=timezone.utc)
    assert now_msk_naive(clock) == datetime(2026, 8, 31, 10, 0, 0)
