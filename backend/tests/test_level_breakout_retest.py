"""Issue #107: level_breakout_retest pattern + StrategyEvaluator AND-filter.

Uses a mocked LevelsTracker (broken resistance) and synthetic bars.
Default locked-like configs without the pattern stay on the Issue #97 path.
"""
from __future__ import annotations

import pandas as pd

from app.analytics.levels_engine import (
    LevelState,
    LevelsTracker,
    overlapping_resistance_zone_at,
)
from app.analytics.patterns.level_breakout_retest import (
    PATTERN_ID,
    check_breakout_retest,
    compute_stop_take,
    evaluate_level_breakout_retest,
    resolve_params,
)
from app.analytics.strategy_engine import StrategyEvaluator
from app.analytics.trading_config import LEVEL_BREAKOUT_RETEST

ATR = 4.0
LEVEL_PRICE = 100.0
ZONE_LOWER = 98.0
ZONE_UPPER = 102.0
DEFINED = pd.Timestamp("2026-08-01 04:00:00")
# HTF opens; 4h close = open + 4h. Two bars confirm a resistance break.
HTF_1 = pd.Timestamp("2026-08-01 04:00:00")  # closes 08:00
HTF_2 = pd.Timestamp("2026-08-01 08:00:00")  # closes 12:00
EVAL_TS = pd.Timestamp("2026-08-01 12:00:00")
RESISTANCE_BREAK_CLOSE = 104.5
SUPPORT_PRICE = 96.0
SUPPORT_ZL = 94.0
SUPPORT_ZU = 98.0
TAKE_PRICE = 120.0


def _resistance_row() -> dict:
    return {
        "available_from_ts": DEFINED,
        "defined_ts": DEFINED,
        "level_price": LEVEL_PRICE,
        "type": "resistance",
        "method": "impulse",
        "atr": ATR,
        "zone_lower": ZONE_LOWER,
        "zone_upper": ZONE_UPPER,
    }


def _support_row() -> dict:
    return {
        "available_from_ts": DEFINED,
        "defined_ts": DEFINED,
        "level_price": SUPPORT_PRICE,
        "type": "support",
        "method": "impulse",
        "atr": ATR,
        "zone_lower": SUPPORT_ZL,
        "zone_upper": SUPPORT_ZU,
    }


def _take_row() -> dict:
    return {
        "available_from_ts": DEFINED,
        "defined_ts": DEFINED,
        "level_price": TAKE_PRICE,
        "type": "resistance",
        "method": "swing",
        "atr": ATR,
        "zone_lower": 118.0,
        "zone_upper": 122.0,
    }


def _htf_bars(*closes_and_ts) -> pd.DataFrame:
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


def _broken_tracker() -> LevelsTracker:
    tracker = LevelsTracker(pd.DataFrame([_resistance_row()]))
    tracker.update_bars(_htf_bars((HTF_1, 102.6), (HTF_2, RESISTANCE_BREAK_CLOSE)))
    return tracker


def _retest_bar(close: float = 100.5, open_: float = 99.6, high: float = 100.8, low: float = 99.4):
    return pd.Series(
        {
            "timestamp": EVAL_TS,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
    )


def test_breakout_retest_entry_on_confirmed_support():
    tracker = _broken_tracker()
    lid = tracker.get_levels_with_state().iloc[0]["level_id"]
    assert tracker.get_state(lid) == LevelState.BROKEN_UP.value

    bar = _retest_bar()
    hit = check_breakout_retest(bar, tracker, atr=ATR, prev_high=99.8)
    assert hit is not None
    assert hit["action"] == "enter"
    assert hit["entry_price"] == 100.5
    assert hit["level_price"] == LEVEL_PRICE
    assert hit["stop"] < ZONE_LOWER
    signal = evaluate_level_breakout_retest(
        bar, tracker, atr=ATR, prev_high=99.8, timestamp=EVAL_TS
    )
    assert signal is not None
    assert signal.stop == hit["stop"]
    assert signal.take == hit["take"]
    assert signal.metadata["source"] == PATTERN_ID


def test_no_entry_without_retest():
    tracker = _broken_tracker()
    # Close still above the retest zone (level ± 0.5×ATR = 98..102).
    bar = _retest_bar(close=110.0, open_=109.0, high=110.5, low=108.8)
    assert check_breakout_retest(bar, tracker, atr=ATR, prev_high=109.5) is None


def test_no_entry_if_support_breaks():
    tracker = _broken_tracker()
    # Close below the broken level — support does not hold.
    bar = _retest_bar(close=99.5, open_=99.0, high=99.8, low=98.9)
    assert 99.5 < LEVEL_PRICE
    assert check_breakout_retest(bar, tracker, atr=ATR, prev_high=99.2) is None


def test_veto_skips_broken_level():
    levels = pd.DataFrame([_resistance_row()])
    assert overlapping_resistance_zone_at(levels, EVAL_TS, 100.0) is not None

    tracker = _broken_tracker()
    assert tracker.is_broken(0) is True
    assert overlapping_resistance_zone_at(
        levels, EVAL_TS, 100.0, tracker=tracker
    ) is None

    # Without tracker, frames with no state column keep the Issue #97 veto.
    assert overlapping_resistance_zone_at(levels, EVAL_TS, 100.0) is not None


def test_configurable_retest_window():
    tracker = _broken_tracker()
    extra = [
        (pd.Timestamp("2026-08-01 12:00:00"), 103.0),
        (pd.Timestamp("2026-08-01 16:00:00"), 103.0),
        (pd.Timestamp("2026-08-02 08:00:00"), 103.0),
    ]
    tracker.update_bars(_htf_bars(*extra))
    lid = tracker.get_levels_with_state().iloc[0]["level_id"]
    since = tracker.bars_since_breakout(lid)
    assert since is not None and since > 2

    bar = _retest_bar()
    tight = resolve_params({"retest_window_bars": 2})
    assert check_breakout_retest(
        bar, tracker, atr=ATR, params=tight, prev_high=99.8
    ) is None
    wide = resolve_params({"retest_window_bars": 20})
    assert check_breakout_retest(
        bar, tracker, atr=ATR, params=wide, prev_high=99.8
    ) is not None


def test_stop_take_calculation():
    entry = 100.5
    st = compute_stop_take(entry, ATR, stop_atr=1.0, risk_reward=2.0)
    assert st is not None
    assert st["stop"] == entry - ATR
    assert st["take"] == entry + 2.0 * (entry - st["stop"])
    assert st["stop"] < ZONE_LOWER

    tracker = _broken_tracker()
    hit = check_breakout_retest(_retest_bar(close=entry), tracker, atr=ATR, prev_high=99.8)
    assert hit is not None
    assert hit["stop"] == st["stop"]
    assert hit["take"] == st["take"]
    assert LEVEL_BREAKOUT_RETEST["bullish_body_ratio"] == 0.6


def _and_config():
    return {
        "patterns": ["levels_reversal", PATTERN_ID],
        "confirm_windows": [10],
        "commission_pct": 0.06,
        "slippage_pct": 0.0,
        "risk_reward": {"risk": 1.0, "reward": 2.0},
        "entry_window": (7, 19),
    }


def _and_levels() -> pd.DataFrame:
    return pd.DataFrame([_support_row(), _resistance_row(), _take_row()])


def _and_context(htf_bars=None) -> dict:
    confirm_ts = [pd.Timestamp("2026-08-01 11:50:00")]
    return {
        "levels": _and_levels(),
        "ts_4h": [HTF_2],
        "atr_by_ts": {HTF_2: ATR},
        "buy_ts": [],
        "confirm_series": [(confirm_ts, [100.8])],
        "htf_bars": htf_bars if htf_bars is not None else _htf_bars(
            (HTF_1, 102.6), (HTF_2, RESISTANCE_BREAK_CLOSE)
        ),
    }


def test_evaluator_and_filter_enters_on_retest():
    """levels_reversal geometry AND retest → ATR stop/take."""
    ev = StrategyEvaluator(_and_config())
    ctx = _and_context()
    ev.load_context(
        ctx["levels"],
        ctx["ts_4h"],
        ctx["atr_by_ts"],
        ctx["buy_ts"],
        ctx["confirm_series"],
        htf_bars=ctx["htf_bars"],
    )
    # Price 100.0 sits on the 0.5×ATR support extension (zu=98 + 2=100)
    # and on the broken level (retest holds).
    bar = _retest_bar(close=100.0, open_=99.2, high=100.4, low=99.0)
    ev._prev_high = 99.8
    decision = ev.check_entry(bar)
    assert decision is not None
    assert decision["action"] == "enter"
    assert decision["stop"] == 100.0 - ATR
    assert decision["take"] == 100.0 + 2.0 * ATR
    assert decision["stop"] < ZONE_LOWER


def test_evaluator_without_pattern_keeps_veto():
    """Locked-like config (no level_breakout_retest) still vetoes overlapping resistance."""
    config = {
        "patterns": ["levels_reversal", "signal_4h_buy"],
        "confirm_windows": [10],
        "commission_pct": 0.06,
        "slippage_pct": 0.0,
        "risk_reward": {"risk": 1.0, "reward": 2.0},
        "entry_window": (7, 19),
    }
    ev = StrategyEvaluator(config)
    ctx = _and_context()
    ev.load_context(
        ctx["levels"],
        ctx["ts_4h"],
        ctx["atr_by_ts"],
        [HTF_2],
        ctx["confirm_series"],
        htf_bars=ctx["htf_bars"],
    )
    assert ev.use_breakout_retest is False
    assert ev.check_entry(_retest_bar(close=100.0, open_=99.2, high=100.4, low=99.0)) is None


_HTF_FROM_CTX = object()


def _plugin_market(ctx: dict, bar: pd.Series, htf_bars=_HTF_FROM_CTX) -> "MarketContext":
    from app.analytics.strategies.context import MarketContext

    htf = ctx["htf_bars"] if htf_bars is _HTF_FROM_CTX else htf_bars
    return MarketContext(
        timestamp=pd.Timestamp(bar["timestamp"]),
        candles_1min=pd.DataFrame([bar]),
        levels=ctx["levels"],
        ts_4h=ctx["ts_4h"],
        atr_by_ts=ctx["atr_by_ts"],
        buy_ts=ctx["buy_ts"],
        confirm_series=ctx["confirm_series"],
        htf_bars=htf,
    )


def test_plugin_path_feeds_tracker_closed_htf_bars():
    """Issue #116: Lab plugin load must push closed 4h bars into LevelsTracker."""
    from app.analytics.strategies.levels_reversal import LevelsReversalStrategy

    plugin = LevelsReversalStrategy(_and_config())
    ctx = _and_context()
    bar = _retest_bar(close=100.0, open_=99.2, high=100.4, low=99.0)
    plugin.load_market_context(_plugin_market(ctx, bar))
    plugin.check_entry(_plugin_market(ctx, bar))

    ev = plugin._evaluator
    assert ev._tracker is not None
    assert ev._htf_fed >= 2
    states = set(ev._tracker.get_levels_with_state()["state"])
    assert LevelState.BROKEN_UP.value in states or LevelState.FLIPPED_SUPPORT.value in states


def test_plugin_path_without_htf_bars_leaves_tracker_unfed():
    """Guard: missing htf_bars still skips _sync_tracker (the pre-#116 Lab bug)."""
    from app.analytics.strategies.levels_reversal import LevelsReversalStrategy

    plugin = LevelsReversalStrategy(_and_config())
    ctx = _and_context()
    bar = _retest_bar(close=100.0, open_=99.2, high=100.4, low=99.0)
    market = _plugin_market(ctx, bar, htf_bars=None)
    plugin.load_market_context(market)
    plugin.check_entry(market)
    assert plugin._evaluator._tracker is not None
    assert plugin._evaluator._htf_fed == 0
    states = set(plugin._evaluator._tracker.get_levels_with_state()["state"])
    assert states == {LevelState.ACTIVE.value}
