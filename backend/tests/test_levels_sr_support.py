"""Issue #127: levels_sr_support support-only engine with tracker veto.

Same geometry as levels_reversal plus Issue #97 veto of *active* resistance
with LevelsTracker. No check_breakout_retest / resistance path.

Locked-like configs without the new id stay on the Issue #97 path.
"""
from __future__ import annotations

import pandas as pd

from app.analytics.levels_engine import LevelState
from app.analytics.patterns.levels_sr_breakout import (
    PATTERN_ID as SR_BREAKOUT_ID,
    SOURCE_SUPPORT as COMPOSITE_SOURCE_SUPPORT,
    has_levels_entry_engine,
)
from app.analytics.patterns.levels_sr_support import PATTERN_ID, SOURCE, has_sr_support
from app.analytics.strategies.context import MarketContext
from app.analytics.strategies.registry import get_registry, register_default_strategies
from app.analytics.strategy_backtest import run_strategy_backtest
from app.analytics.strategy_engine import StrategyEvaluator
from tests.test_level_breakout_retest import (
    ATR,
    DEFINED,
    EVAL_TS,
    HTF_1,
    HTF_2,
    RESISTANCE_BREAK_CLOSE,
    SUPPORT_PRICE,
    SUPPORT_ZU,
    TAKE_PRICE,
    _htf_bars,
    _resistance_row,
    _retest_bar,
    _support_row,
    _take_row,
)
from tests.test_resistance_zone_veto import (
    LOCKED_LIKE_CONFIG,
    RESISTANCE_19_67,
    STOP_PRICE,
    SUPPORT_19_61,
    TAKE_20_90,
    TAKE_PRICE as ALRS_TAKE,
    _context as _alrs_context,
    _entry_row as _alrs_entry_row,
    _levels as _alrs_levels,
    _load as _alrs_load,
)


def _support_config(**overrides):
    cfg = {
        "patterns": [PATTERN_ID],
        "confirm_windows": [10],
        "commission_pct": 0.06,
        "slippage_pct": 0.0,
        "risk_reward": {"risk": 1.0, "reward": 2.0},
        "entry_window": (7, 19),
    }
    cfg.update(overrides)
    return cfg


def _path_a_levels() -> pd.DataFrame:
    return pd.DataFrame([_support_row(), _take_row()])


def _path_a_context(levels=None, htf_bars=None) -> dict:
    confirm_ts = [pd.Timestamp("2026-08-01 11:50:00")]
    return {
        "levels": levels if levels is not None else _path_a_levels(),
        "ts_4h": [HTF_2],
        "atr_by_ts": {HTF_2: ATR},
        "buy_ts": [],
        "confirm_series": [(confirm_ts, [100.8])],
        "htf_bars": htf_bars if htf_bars is not None else _htf_bars(
            (HTF_1, SUPPORT_PRICE), (HTF_2, SUPPORT_PRICE)
        ),
    }


def _load(ev: StrategyEvaluator, ctx: dict) -> None:
    ev.load_context(
        ctx["levels"],
        ctx["ts_4h"],
        ctx["atr_by_ts"],
        ctx["buy_ts"],
        ctx["confirm_series"],
        htf_bars=ctx.get("htf_bars"),
    )


def _support_bar(close: float = 96.5):
    """Close inside the native support zone [94, 98], above the level."""
    return pd.Series(
        {
            "timestamp": EVAL_TS,
            "open": close - 0.4,
            "high": close + 0.3,
            "low": close - 0.5,
            "close": close,
        }
    )


def _overlapping_resistance_below() -> dict:
    """Resistance below the fill whose native zone still covers the support bar."""
    return {
        "available_from_ts": DEFINED,
        "defined_ts": DEFINED,
        "level_price": 95.5,
        "type": "resistance",
        "method": "impulse",
        "atr": ATR,
        "zone_lower": 93.5,
        "zone_upper": 97.5,
    }


def test_support_entry_in_zone_no_breakout():
    """Bar in the support zone, no breakout → levels stop/take and new source."""
    ev = StrategyEvaluator(_support_config())
    _load(ev, _path_a_context())
    decision = ev.check_entry(_support_bar())
    assert decision is not None
    assert decision["action"] == "enter"
    assert decision["stop"] == SUPPORT_PRICE
    assert decision["take"] == TAKE_PRICE
    assert decision["source"] == SOURCE
    assert ev.use_sr_support is True
    assert ev.use_tracker is True
    assert ev.use_sr_breakout is False


def test_alrs_active_resistance_rejects():
    """ALRS 19.80 inside an active resistance → no entry (Issue #97)."""
    ev = StrategyEvaluator(_support_config())
    ctx = _alrs_context(_alrs_levels(SUPPORT_19_61, RESISTANCE_19_67, TAKE_20_90))
    _load(ev, ctx)
    assert ev.check_entry(_alrs_entry_row()) is None


def test_broken_resistance_allows_support_entry_tracker_veto():
    """Broken overlapping resistance + valid support → entry; same bar on
    levels_reversal without tracker is vetoed (separate from ALRS #97)."""
    levels = pd.DataFrame(
        [_support_row(), _overlapping_resistance_below(), _take_row()]
    )
    htf = _htf_bars((HTF_1, 102.6), (HTF_2, RESISTANCE_BREAK_CLOSE))
    ctx = _path_a_context(levels=levels, htf_bars=htf)
    bar = _support_bar()

    ev_support = StrategyEvaluator(_support_config())
    _load(ev_support, ctx)
    decision = ev_support.check_entry(bar)
    assert decision is not None
    assert decision["action"] == "enter"
    assert decision["source"] == SOURCE
    assert decision["stop"] == SUPPORT_PRICE
    assert decision["take"] == TAKE_PRICE
    assert ev_support._tracker is not None
    states = set(ev_support._tracker.get_levels_with_state()["state"])
    assert LevelState.BROKEN_UP.value in states or LevelState.FLIPPED_SUPPORT.value in states

    ev_classic = StrategyEvaluator(
        {
            "patterns": ["levels_reversal"],
            "confirm_windows": [10],
            "commission_pct": 0.06,
            "slippage_pct": 0.0,
            "risk_reward": {"risk": 1.0, "reward": 2.0},
            "entry_window": (7, 19),
        }
    )
    _load(ev_classic, ctx)
    assert ev_classic.use_tracker is False
    assert ev_classic.check_entry(bar) is None


def test_retest_without_native_support_does_not_enter():
    """Retest of a broken resistance above the support zone → no entry."""
    support_ceiling = SUPPORT_ZU + 0.5 * ATR
    levels = pd.DataFrame([_support_row(), _resistance_row(), _take_row()])
    htf = _htf_bars((HTF_1, 102.6), (HTF_2, RESISTANCE_BREAK_CLOSE))
    ev = StrategyEvaluator(_support_config())
    _load(
        ev,
        {
            "levels": levels,
            "ts_4h": [HTF_2],
            "atr_by_ts": {HTF_2: ATR},
            "buy_ts": [],
            "confirm_series": [([pd.Timestamp("2026-08-01 11:50:00")], [100.8])],
            "htf_bars": htf,
        },
    )
    ev._prev_high = 99.8
    bar = _retest_bar(close=100.5, open_=99.6, high=100.8, low=99.4)
    assert 100.5 > support_ceiling
    assert ev.check_entry(bar) is None


def test_run_strategy_backtest_accepts_support_without_levels_reversal():
    """Config with only levels_sr_support is a valid entry engine."""
    class _EmptyDB:
        def select(self, *_args, **_kwargs):
            class _Rows:
                def to_dataframe(self):
                    return pd.DataFrame()

            return _Rows()

    missing_engine = run_strategy_backtest(
        _EmptyDB(), "AFKS", {"patterns": ["rsi_oversold"]}
    )
    assert missing_engine["status"] == "failed"
    assert "levels_sr_support" in missing_engine["error"]

    isolated = run_strategy_backtest(
        _EmptyDB(), "AFKS", {"patterns": [PATTERN_ID]}
    )
    assert isolated["status"] == "failed"
    assert isolated["error"] == "no 1min candles"
    assert has_levels_entry_engine([PATTERN_ID]) is True
    assert has_levels_entry_engine({PATTERN_ID: {}}) is True
    assert has_sr_support([PATTERN_ID]) is True
    assert has_levels_entry_engine(["levels_reversal"]) is True
    assert has_levels_entry_engine(["rsi_oversold"]) is False


def test_locked_like_config_without_new_id_unchanged():
    """Locked-like config (no new id) still vetoes ALRS and enters clean support."""
    colliding = _alrs_levels(SUPPORT_19_61, RESISTANCE_19_67, TAKE_20_90)
    clean = _alrs_levels(SUPPORT_19_61, TAKE_20_90)
    ev_colliding = StrategyEvaluator(LOCKED_LIKE_CONFIG)
    _alrs_load(ev_colliding, _alrs_context(colliding))
    ev_clean = StrategyEvaluator(LOCKED_LIKE_CONFIG)
    _alrs_load(ev_clean, _alrs_context(clean))
    assert ev_colliding.use_sr_support is False
    assert ev_colliding.use_tracker is False
    assert ev_colliding.check_entry(_alrs_entry_row()) is None
    clean_dec = ev_clean.check_entry(_alrs_entry_row())
    assert clean_dec is not None
    assert clean_dec["action"] == "enter"
    assert clean_dec["stop"] == STOP_PRICE
    assert clean_dec["take"] == ALRS_TAKE
    assert "source" not in clean_dec


def test_composite_wins_over_sr_support():
    """Both chips → composite path A, not the support-only source."""
    ev = StrategyEvaluator(_support_config(patterns=[PATTERN_ID, SR_BREAKOUT_ID]))
    _load(ev, _path_a_context())
    decision = ev.check_entry(_support_bar())
    assert decision is not None
    assert decision["source"] == COMPOSITE_SOURCE_SUPPORT
    assert ev.use_sr_breakout is True
    assert ev.use_sr_support is True


def test_sr_support_wins_over_levels_reversal_no_doubling():
    """Both chips → one support path marked as the new id, not levels_reversal."""
    ev = StrategyEvaluator(_support_config(patterns=["levels_reversal", PATTERN_ID]))
    _load(ev, _path_a_context())
    decision = ev.check_entry(_support_bar())
    assert decision is not None
    assert decision["source"] == SOURCE


def test_plugin_and_evaluator_match_on_sr_support() -> None:
    """SOP: StrategyEvaluator change requires bit-for-bit plugin parity."""
    register_default_strategies()
    cfg = _support_config()
    ctx = _path_a_context()
    bar = _support_bar()

    ev = StrategyEvaluator(cfg)
    _load(ev, ctx)
    eval_dec = ev.check_entry(bar)

    plugin = get_registry().get_plugin("levels_reversal", cfg)
    market = MarketContext(
        timestamp=pd.Timestamp(bar["timestamp"]),
        candles_1min=pd.DataFrame([bar]),
        levels=ctx["levels"],
        ts_4h=ctx["ts_4h"],
        atr_by_ts=ctx["atr_by_ts"],
        buy_ts=ctx["buy_ts"],
        confirm_series=ctx["confirm_series"],
        htf_bars=ctx["htf_bars"],
    )
    plugin.load_market_context(market)
    plugin_dec = plugin.check_entry(market)

    match = (
        eval_dec is not None
        and plugin_dec is not None
        and plugin_dec.entry_price == eval_dec["entry_price"]
        and plugin_dec.stop == eval_dec["stop"]
        and plugin_dec.take == eval_dec["take"]
        and plugin_dec.metadata.get("source") == eval_dec["source"]
    )
    result = {"regression_match": match, "source": eval_dec["source"] if eval_dec else None}
    assert result["regression_match"] is True
    assert eval_dec["source"] == SOURCE
    assert eval_dec["stop"] == SUPPORT_PRICE
    assert eval_dec["take"] == TAKE_PRICE
