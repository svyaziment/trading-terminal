"""Issue #79: SignalEngine AND-filters in StrategyEvaluator + timeframe contract."""
from __future__ import annotations

import pandas as pd

from app.analytics.pattern_registry import (
    DEFAULT_SIGNAL_TIMEFRAME,
    SIGNAL_ENGINE_PATTERN_IDS,
    SIGNAL_ENGINE_TIMEFRAMES,
    SIGNAL_PATTERN_TIMEFRAME_PARAM,
    normalize_patterns,
)
from app.analytics.signal_pattern_filters import (
    enabled_signal_filters,
    evaluate_buy_timestamps,
    last_closed_htf_ts,
)
from app.analytics.strategy_engine import StrategyEvaluator
from app.analytics.trading_config import STRATEGIES


def _levels_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "available_from_ts": pd.Timestamp("2024-01-01"),
                "type": "support",
                "level_price": 99.0,
                "zone_lower": 99.0,
                "zone_upper": 101.0,
                "method": "swing",
            },
            {
                "available_from_ts": pd.Timestamp("2024-01-01"),
                "type": "resistance",
                "level_price": 110.0,
                "zone_lower": 109.0,
                "zone_upper": 111.0,
                "method": "swing",
            },
        ]
    )


def _passing_context():
    ts_4h = [pd.Timestamp("2024-01-02 08:00:00")]
    confirm_ts = [pd.Timestamp("2024-01-02 09:50:00")]
    return {
        "levels": _levels_df(),
        "ts_4h": ts_4h,
        "atr_by_ts": {ts_4h[0]: 2.0},
        "buy_ts": list(ts_4h),
        "confirm_series": [(confirm_ts, [102.0])],
    }


def _passing_row(ts="2024-01-02 10:00:00", price=100.0) -> pd.Series:
    return pd.Series(
        {
            "timestamp": pd.Timestamp(ts),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
        }
    )


def _load(ev: StrategyEvaluator, ctx: dict, series=None) -> None:
    ev.load_context(
        ctx["levels"],
        ctx["ts_4h"],
        ctx["atr_by_ts"],
        ctx["buy_ts"],
        ctx["confirm_series"],
        series,
    )


def test_timeframe_contract_stable_for_registry():
    assert SIGNAL_PATTERN_TIMEFRAME_PARAM["key"] == "timeframe"
    assert SIGNAL_PATTERN_TIMEFRAME_PARAM["type"] == "select"
    assert SIGNAL_PATTERN_TIMEFRAME_PARAM["default"] == DEFAULT_SIGNAL_TIMEFRAME == "4h"
    assert tuple(SIGNAL_PATTERN_TIMEFRAME_PARAM["options"]) == SIGNAL_ENGINE_TIMEFRAMES
    assert SIGNAL_ENGINE_TIMEFRAMES == ("30min", "1h", "2h", "4h", "1d", "1w")
    assert "rsi_oversold" not in SIGNAL_ENGINE_PATTERN_IDS
    assert "signal_4h_buy" not in SIGNAL_ENGINE_PATTERN_IDS
    assert "MR_RSI_Reversal" in SIGNAL_ENGINE_PATTERN_IDS
    assert "PA_Hammer" in SIGNAL_ENGINE_PATTERN_IDS


def test_normalize_injects_timeframe_for_signal_engine_ids_only():
    cfg = normalize_patterns(
        {
            "patterns": ["levels_reversal", "signal_4h_buy", "PA_Hammer", "rsi_oversold"],
            "confirm_windows": [10],
        }
    )
    assert cfg["patterns"]["signal_4h_buy"] == {}
    assert "timeframe" not in cfg["patterns"]["rsi_oversold"]
    assert cfg["patterns"]["PA_Hammer"]["timeframe"] == "4h"

    cfg_tf = normalize_patterns(
        {"patterns": {"levels_reversal": {}, "PA_Hammer": {"timeframe": "30min"}}}
    )
    assert cfg_tf["patterns"]["PA_Hammer"]["timeframe"] == "30min"

    cfg_bad = normalize_patterns(
        {"patterns": {"levels_reversal": {}, "MR_RSI_Reversal": {"timeframe": "1min"}}}
    )
    assert cfg_bad["patterns"]["MR_RSI_Reversal"]["timeframe"] == "4h"


def test_enabled_signal_filters_ignores_lab_and_4h_buy():
    specs = enabled_signal_filters(
        {
            "levels_reversal": {},
            "signal_4h_buy": {},
            "rsi_oversold": {"threshold": 30},
            "PA_Hammer": {"timeframe": "1h"},
        }
    )
    assert specs == [{"pattern_id": "PA_Hammer", "timeframe": "1h"}]
    assert enabled_signal_filters(["levels_reversal", "signal_4h_buy"]) == []


def test_last_closed_htf_has_no_lookahead():
    times = [
        pd.Timestamp("2024-01-02 00:00:00"),
        pd.Timestamp("2024-01-02 04:00:00"),
        pd.Timestamp("2024-01-02 08:00:00"),
    ]
    # 08:00 4h bar is still forming at 10:00; last closed is 04:00.
    assert last_closed_htf_ts(times, pd.Timestamp("2024-01-02 10:00:00"), "4h") == times[1]
    assert last_closed_htf_ts(times, pd.Timestamp("2024-01-02 12:00:00"), "4h") == times[2]

    m30 = [
        pd.Timestamp("2024-01-02 09:00:00"),
        pd.Timestamp("2024-01-02 09:30:00"),
        pd.Timestamp("2024-01-02 10:00:00"),
    ]
    assert last_closed_htf_ts(m30, pd.Timestamp("2024-01-02 10:00:00"), "30min") == m30[1]
    assert last_closed_htf_ts([], pd.Timestamp("2024-01-02 10:00:00"), "4h") is None

    h2 = [
        pd.Timestamp("2024-01-02 06:00:00"),
        pd.Timestamp("2024-01-02 08:00:00"),
        pd.Timestamp("2024-01-02 10:00:00"),
    ]
    assert last_closed_htf_ts(h2, pd.Timestamp("2024-01-02 10:00:00"), "2h") == h2[1]


def test_default_levels_plus_4h_buy_unaffected_without_signal_engine_filter():
    ctx = _passing_context()
    row = _passing_row()

    baseline = StrategyEvaluator(
        {"patterns": ["levels_reversal", "signal_4h_buy"], "confirm_windows": [10]}
    )
    _load(baseline, ctx)
    entered = baseline.check_entry(row)
    assert entered is not None and entered["action"] == "enter"

    # Same config plus unused series payload must not change the decision.
    same = StrategyEvaluator(
        {"patterns": ["levels_reversal", "signal_4h_buy"], "confirm_windows": [10]}
    )
    _load(
        same,
        ctx,
        [
            {
                "pattern_id": "PA_Hammer",
                "timeframe": "4h",
                "times": [pd.Timestamp("2024-01-02 04:00:00")],
                "buy_ts": [],
            }
        ],
    )
    again = same.check_entry(row)
    assert again is not None
    assert again["entry_price"] == entered["entry_price"]
    assert again["stop"] == entered["stop"]
    assert again["take"] == entered["take"]


def test_signal_engine_filter_on_off():
    ctx = _passing_context()
    row = _passing_row()
    closed_4h = pd.Timestamp("2024-01-02 04:00:00")
    times = [
        pd.Timestamp("2024-01-02 00:00:00"),
        closed_4h,
        pd.Timestamp("2024-01-02 08:00:00"),
    ]

    off = StrategyEvaluator({"patterns": ["levels_reversal", "signal_4h_buy"]})
    _load(off, ctx)
    assert off.check_entry(row) is not None

    on_miss = StrategyEvaluator(
        {"patterns": {"levels_reversal": {}, "signal_4h_buy": {}, "PA_Hammer": {"timeframe": "4h"}}}
    )
    _load(
        on_miss,
        ctx,
        [{"pattern_id": "PA_Hammer", "timeframe": "4h", "times": times, "buy_ts": []}],
    )
    assert on_miss.check_entry(row) is None

    on_hit = StrategyEvaluator(
        {"patterns": {"levels_reversal": {}, "signal_4h_buy": {}, "PA_Hammer": {"timeframe": "4h"}}}
    )
    _load(
        on_hit,
        ctx,
        [{"pattern_id": "PA_Hammer", "timeframe": "4h", "times": times, "buy_ts": [closed_4h]}],
    )
    assert on_hit.check_entry(row) is not None


def test_timeframe_selection_uses_matching_last_closed_bar():
    ctx = _passing_context()
    row = _passing_row("2024-01-02 10:00:00")
    ts_30 = [
        pd.Timestamp("2024-01-02 09:00:00"),
        pd.Timestamp("2024-01-02 09:30:00"),
        pd.Timestamp("2024-01-02 10:00:00"),
    ]
    ts_4h = [
        pd.Timestamp("2024-01-02 00:00:00"),
        pd.Timestamp("2024-01-02 04:00:00"),
        pd.Timestamp("2024-01-02 08:00:00"),
    ]

    ev_30 = StrategyEvaluator(
        {"patterns": {"levels_reversal": {}, "PA_Hammer": {"timeframe": "30min"}}}
    )
    _load(
        ev_30,
        ctx,
        [
            {
                "pattern_id": "PA_Hammer",
                "timeframe": "30min",
                "times": ts_30,
                "buy_ts": [pd.Timestamp("2024-01-02 09:30:00")],
            }
        ],
    )
    assert ev_30.check_entry(row) is not None

    ev_4h_wrong = StrategyEvaluator(
        {"patterns": {"levels_reversal": {}, "PA_Hammer": {"timeframe": "4h"}}}
    )
    _load(
        ev_4h_wrong,
        ctx,
        [
            {
                "pattern_id": "PA_Hammer",
                "timeframe": "4h",
                "times": ts_4h,
                "buy_ts": [pd.Timestamp("2024-01-02 09:30:00")],
            }
        ],
    )
    assert ev_4h_wrong.check_entry(row) is None

    ev_4h = StrategyEvaluator(
        {"patterns": {"levels_reversal": {}, "PA_Hammer": {"timeframe": "4h"}}}
    )
    _load(
        ev_4h,
        ctx,
        [
            {
                "pattern_id": "PA_Hammer",
                "timeframe": "4h",
                "times": ts_4h,
                "buy_ts": [pd.Timestamp("2024-01-02 04:00:00")],
            }
        ],
    )
    assert ev_4h.check_entry(row) is not None


def test_inline_evaluate_pa_hammer_buy_timestamps():
    df = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2024-01-02 04:00:00"),
                "open": 100.0,
                "high": 101.2,
                "low": 97.0,
                "close": 101.0,
                "ticker": "SBER",
            }
        ]
    )
    buy_ts = evaluate_buy_timestamps(df, "PA_Hammer", "4h", "SBER")
    assert buy_ts == [pd.Timestamp("2024-01-02 04:00:00")]

    hanging = df.copy()
    hanging["open"] = 101.0
    hanging["close"] = 100.0
    hanging["high"] = 101.2
    hanging["low"] = 97.0
    assert evaluate_buy_timestamps(hanging, "PA_Hammer", "4h", "SBER") == []


def test_canonical_4hbuy_strategy_patterns_unchanged():
    assert STRATEGIES["levels_reversal_4hbuy"]["patterns"] == [
        "levels_reversal",
        "signal_4h_buy",
    ]
