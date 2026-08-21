"""Issue #106: in-memory levels state machine (breakout + veto skip).

Epic #105 infrastructure only: LevelsTracker is not wired into StrategyEvaluator.
"""
from __future__ import annotations

import pandas as pd

from app.analytics.levels_engine import (
    LevelState,
    LevelsTracker,
    overlapping_resistance_zone_at,
)
from app.analytics.trading_config import LEVEL_STATE_MACHINE, get_level_state_machine_config

ATR = 4.0
LEVEL_PRICE = 100.0
ZONE_LOWER = 98.0
ZONE_UPPER = 102.0
DEFINED = pd.Timestamp("2026-08-01 08:00:00")
TS_1 = pd.Timestamp("2026-08-01 12:00:00")
TS_2 = pd.Timestamp("2026-08-01 16:00:00")
TS_3 = pd.Timestamp("2026-08-02 08:00:00")
TS_4 = pd.Timestamp("2026-08-02 12:00:00")

# Default LEVEL_STATE_MACHINE: buffer 0.25×ATR=1.0, min_penetration 0.5×ATR=2.0
# Resistance break: last close > 103 and max(window) >= 104, all > 102.
RESISTANCE_BREAK_CLOSE = 104.5
# Support break: last close < 97 and min(window) <= 96, all < 98.
SUPPORT_BREAK_CLOSE = 95.5


def _level(level_type: str) -> dict:
    return {
        "available_from_ts": DEFINED,
        "defined_ts": DEFINED,
        "level_price": LEVEL_PRICE,
        "type": level_type,
        "method": "impulse",
        "atr": ATR,
        "zone_lower": ZONE_LOWER,
        "zone_upper": ZONE_UPPER,
    }


def _bars(*closes_and_ts) -> pd.DataFrame:
    rows = []
    for ts, close in closes_and_ts:
        rows.append(
            {
                "timestamp": ts,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "atr": ATR,
            }
        )
    return pd.DataFrame(rows)


def _tracker(level_type: str, config=None) -> LevelsTracker:
    return LevelsTracker(pd.DataFrame([_level(level_type)]), config=config)


def test_resistance_breaks_on_confirmed_close_above():
    tracker = _tracker("resistance")
    lid = tracker.get_levels_with_state().iloc[0]["level_id"]
    tracker.update_bars(_bars((TS_1, 102.6), (TS_2, RESISTANCE_BREAK_CLOSE)))
    assert tracker.get_state(lid) == LevelState.BROKEN_UP.value
    assert tracker.is_broken(lid) is True
    assert tracker.is_broken(0) is True


def test_support_breaks_on_confirmed_close_below():
    tracker = _tracker("support")
    lid = tracker.get_levels_with_state().iloc[0]["level_id"]
    tracker.update_bars(_bars((TS_1, 97.4), (TS_2, SUPPORT_BREAK_CLOSE)))
    assert tracker.get_state(lid) == LevelState.BROKEN_DOWN.value
    assert tracker.is_broken(lid) is True


def test_no_break_without_confirmation():
    """One bar above the zone is not enough at confirm_bars=2."""
    tracker = _tracker("resistance")
    lid = tracker.get_levels_with_state().iloc[0]["level_id"]
    tracker.update_bars(_bars((TS_1, RESISTANCE_BREAK_CLOSE)))
    assert tracker.get_state(lid) == LevelState.ACTIVE.value
    assert tracker.is_broken(lid) is False

    tracker.update_bars(_bars((TS_2, RESISTANCE_BREAK_CLOSE)))
    assert tracker.get_state(lid) == LevelState.BROKEN_UP.value


def test_veto_skips_broken_level():
    """After a confirmed resistance break, overlapping_resistance_zone_at
    must not veto a price that still sits in the native zone."""
    levels = pd.DataFrame([_level("resistance")])
    probe_ts, probe_px = DEFINED, 100.0
    assert overlapping_resistance_zone_at(levels, probe_ts, probe_px) is not None

    tracker = LevelsTracker(levels)
    assert overlapping_resistance_zone_at(
        tracker.get_levels_with_state(), probe_ts, probe_px
    ) is not None

    tracker.update_bars(_bars((TS_1, 102.6), (TS_2, RESISTANCE_BREAK_CLOSE)))
    snapshot = tracker.get_levels_with_state()
    assert snapshot.iloc[0]["state"] == LevelState.BROKEN_UP.value
    assert overlapping_resistance_zone_at(snapshot, probe_ts, probe_px) is None
    assert tracker.is_broken(snapshot.iloc[0]["level_id"]) is True

    # DataFrames without a state column keep the Issue #97 veto.
    assert overlapping_resistance_zone_at(levels, probe_ts, probe_px) is not None


def test_configurable_confirmation_bars():
    cfg = get_level_state_machine_config()
    assert cfg["confirm_bars"] == LEVEL_STATE_MACHINE["confirm_bars"] == 2
    cfg["confirm_bars"] = 3
    tracker = _tracker("resistance", config=cfg)
    lid = tracker.get_levels_with_state().iloc[0]["level_id"]

    tracker.update_bars(_bars((TS_1, 102.6), (TS_2, RESISTANCE_BREAK_CLOSE)))
    assert tracker.get_state(lid) == LevelState.ACTIVE.value
    assert tracker.is_broken(lid) is False

    tracker.update_bars(_bars((TS_3, RESISTANCE_BREAK_CLOSE)))
    assert tracker.get_state(lid) == LevelState.BROKEN_UP.value
    assert tracker.is_broken(lid) is True


def test_broken_up_flips_to_support_on_retest_hold():
    tracker = _tracker("resistance")
    lid = tracker.get_levels_with_state().iloc[0]["level_id"]
    tracker.update_bars(
        _bars(
            (TS_1, 102.6),
            (TS_2, RESISTANCE_BREAK_CLOSE),
            (TS_3, 100.0),
        )
    )
    assert tracker.get_state(lid) == LevelState.FLIPPED_SUPPORT.value
    assert tracker.is_broken(lid) is True
    assert overlapping_resistance_zone_at(
        tracker.get_levels_with_state(), TS_4, 100.0
    ) is None
