"""Unit tests for pattern_registry.normalize_patterns (issue #12)."""

import copy

from app.analytics.pattern_registry import (
    get_pattern_defaults,
    list_patterns,
    normalize_patterns,
)


def test_old_list_format_converts_to_dict():
    cfg = {
        "patterns": ["levels_reversal", "signal_4h_buy"],
        "confirm_windows": [10],
        "commission_pct": 0.06,
    }
    original = copy.deepcopy(cfg)

    res = normalize_patterns(cfg)

    assert cfg == original, "input config mutated"
    assert isinstance(res["patterns"], dict)

    levels = res["patterns"]["levels_reversal"]
    assert levels["level_timeframe"] == "4h"
    assert levels["level_method"] == ["swing", "impulse"]
    assert levels["swing_window"] == 10
    assert levels["impulse_body_ratio"] == 0.7
    assert levels["impulse_atr_mult"] == 1.5
    assert levels["zone_atr_mult"] == 0.5
    assert levels["confirm_windows"] == [10]

    assert res["patterns"]["signal_4h_buy"] == {}

    # StrategyEvaluator currently reads top-level confirm_windows.
    assert res["confirm_windows"] == [10]
    assert res["commission_pct"] == 0.06


def test_new_dict_format_fills_defaults_and_preserves_overrides():
    cfg = {
        "patterns": {
            "levels_reversal": {"swing_window": 20},
            "rsi_oversold": {},
        },
        "confirm_windows": [5],
    }

    res = normalize_patterns(cfg)

    assert res["patterns"]["levels_reversal"]["swing_window"] == 20
    assert res["patterns"]["levels_reversal"]["confirm_windows"] == [5]
    assert res["confirm_windows"] == [5]
    assert res["patterns"]["rsi_oversold"]["threshold"] == 30


def test_idempotent():
    cfg = {
        "patterns": ["levels_reversal", "signal_4h_buy"],
        "confirm_windows": [10],
        "commission_pct": 0.06,
    }

    once = normalize_patterns(cfg)
    twice = normalize_patterns(once)

    assert once == twice


def test_unknown_pattern_params_preserved():
    cfg = {
        "patterns": {
            "custom_pattern": {"x": 1, "y": "test"},
        }
    }

    res = normalize_patterns(cfg)

    assert res["patterns"]["custom_pattern"] == {"x": 1, "y": "test"}


def test_levels_confirm_windows_explicit_override_syncs_top_level():
    cfg = {
        "patterns": {
            "levels_reversal": {"confirm_windows": [15, 30]},
        }
    }

    res = normalize_patterns(cfg)

    assert res["patterns"]["levels_reversal"]["confirm_windows"] == [15, 30]
    assert res["confirm_windows"] == [15, 30]


def test_registry_schema_contains_levels_params():
    patterns = list_patterns()
    by_id = {p["id"]: p for p in patterns}

    assert "levels_reversal" in by_id
    assert "signal_4h_buy" in by_id
    assert "rsi_oversold" in by_id

    levels = by_id["levels_reversal"]
    keys = {param["key"] for param in levels["params"]}

    expected = {
        "level_timeframe",
        "level_method",
        "swing_window",
        "impulse_body_ratio",
        "impulse_atr_mult",
        "zone_atr_mult",
        "confirm_windows",
    }

    assert expected <= keys


def test_get_pattern_defaults():
    defaults = get_pattern_defaults("levels_reversal")

    assert defaults["level_timeframe"] == "4h"
    assert defaults["confirm_windows"] == [10]
    assert get_pattern_defaults("nonexistent") == {}


def test_signal_engine_timeframe_contract():
    from app.analytics.pattern_registry import (
        DEFAULT_SIGNAL_TIMEFRAME,
        SIGNAL_ENGINE_PATTERN_IDS,
        SIGNAL_ENGINE_TIMEFRAMES,
        SIGNAL_PATTERN_TIMEFRAME_PARAM,
        apply_signal_pattern_defaults,
        is_signal_engine_pattern,
        resolve_signal_timeframe,
    )

    assert SIGNAL_PATTERN_TIMEFRAME_PARAM["key"] == "timeframe"
    assert SIGNAL_PATTERN_TIMEFRAME_PARAM["options"] == list(SIGNAL_ENGINE_TIMEFRAMES)
    assert SIGNAL_PATTERN_TIMEFRAME_PARAM["default"] == DEFAULT_SIGNAL_TIMEFRAME
    assert is_signal_engine_pattern("PA_Hammer")
    assert not is_signal_engine_pattern("rsi_oversold")
    assert not is_signal_engine_pattern("signal_4h_buy")
    assert resolve_signal_timeframe("1h") == "1h"
    assert resolve_signal_timeframe("1min") == "4h"
    assert apply_signal_pattern_defaults("PA_Hammer", {})["timeframe"] == "4h"
    assert "timeframe" not in apply_signal_pattern_defaults("rsi_oversold", {"threshold": 30})
    assert len(SIGNAL_ENGINE_PATTERN_IDS) == 10


_EXPECTED_CATEGORIES = {
    "Trend_SMA_Alignment": "trend",
    "PA_Engulfing": "price_action",
    "PA_HangingMan": "price_action",
    "PA_Hammer": "price_action",
    "PA_ThreeBlackCrows": "price_action",
    "PA_ThreeWhiteSoldiers": "price_action",
    "VOL_Spike": "volume",
    "VOL_Low_Pullback": "volume",
    "MR_RSI_Reversal": "mean_reversion",
    "BO_BB_Squeeze": "breakout",
}


def test_list_patterns_includes_signal_engine_schemas():
    from app.analytics.pattern_registry import (
        SIGNAL_ENGINE_PATTERN_IDS,
        SIGNAL_ENGINE_TIMEFRAMES,
    )

    by_id = {p["id"]: p for p in list_patterns()}

    for lab_id in (
        "levels_reversal",
        "signal_4h_buy",
        "rsi_oversold",
        "macd_bullish",
        "bb_lower",
        "level_breakout_retest",
        "levels_sr_breakout",
    ):
        assert lab_id in by_id

    for pattern_id in SIGNAL_ENGINE_PATTERN_IDS:
        record = by_id[pattern_id]
        assert record["category"] == _EXPECTED_CATEGORIES[pattern_id]
        keys = [param["key"] for param in record["params"]]
        assert keys[0] == "timeframe"
        tf = record["params"][0]
        assert tf["type"] == "select"
        assert tf["options"] == list(SIGNAL_ENGINE_TIMEFRAMES)
        assert tf["default"] == "4h"


def test_signal_engine_defaults_match_4h_implementation():
    from app.analytics.patterns.breakout import BO_BB_Squeeze
    from app.analytics.patterns.mean_reversion import MR_RSI_Reversal
    from app.analytics.patterns.trend import Trend_SMA_Alignment
    from app.analytics.patterns.volume import VOL_Low_Pullback, VOL_Spike

    sma = get_pattern_defaults("Trend_SMA_Alignment")
    assert sma["fast_sma"] == Trend_SMA_Alignment().get_thresholds("4h")["fast_sma"]
    assert sma["slow_sma"] == Trend_SMA_Alignment().get_thresholds("4h")["slow_sma"]

    rsi = get_pattern_defaults("MR_RSI_Reversal")
    rsi_th = MR_RSI_Reversal().get_thresholds("4h")
    assert rsi["oversold"] == rsi_th["oversold"]
    assert rsi["overbought"] == rsi_th["overbought"]

    spike = get_pattern_defaults("VOL_Spike")
    spike_th = VOL_Spike().get_thresholds("4h")
    assert spike["spike_ratio"] == spike_th["spike_ratio"]
    assert spike["min_volume_ratio"] == spike_th["min_volume_ratio"]

    pullback = get_pattern_defaults("VOL_Low_Pullback")
    pb_th = VOL_Low_Pullback().get_thresholds("4h")
    assert pullback["low_volume_ratio"] == pb_th["low_volume_ratio"]
    assert pullback["min_trend_strength"] == pb_th["min_trend_strength"]

    squeeze = get_pattern_defaults("BO_BB_Squeeze")
    sq_th = BO_BB_Squeeze().get_thresholds("4h")
    assert squeeze["squeeze_percentile"] == sq_th["squeeze_percentile"]
    assert squeeze["lookback"] == sq_th["lookback"]

    hammer = get_pattern_defaults("PA_Hammer")
    hanging = get_pattern_defaults("PA_HangingMan")
    assert hammer["lower_shadow_mult"] == hanging["lower_shadow_mult"] == 2.0
    assert hammer["upper_shadow_mult"] == hanging["upper_shadow_mult"] == 0.5
    assert get_pattern_defaults("PA_ThreeWhiteSoldiers")["min_body_range_ratio"] == 0.7
    assert get_pattern_defaults("PA_ThreeBlackCrows")["min_body_range_ratio"] == 0.7
    assert get_pattern_defaults("PA_Engulfing") == {"timeframe": "4h"}


def test_normalize_patterns_signal_engine_dict_and_legacy_lab():
    from app.analytics.pattern_registry import apply_signal_pattern_defaults

    legacy = normalize_patterns(
        {
            "patterns": ["levels_reversal", "signal_4h_buy"],
            "confirm_windows": [10],
        }
    )
    assert set(legacy["patterns"]) == {"levels_reversal", "signal_4h_buy"}
    assert legacy["patterns"]["signal_4h_buy"] == {}
    assert legacy["patterns"]["levels_reversal"]["confirm_windows"] == [10]

    filled = normalize_patterns(
        {
            "patterns": {
                "levels_reversal": {},
                "MR_RSI_Reversal": {},
                "PA_Hammer": {"timeframe": "1h"},
                "VOL_Spike": {"min_volume_ratio": 2.2},
            }
        }
    )
    rsi = filled["patterns"]["MR_RSI_Reversal"]
    assert rsi["timeframe"] == "4h"
    assert rsi["oversold"] == 30
    assert rsi["overbought"] == 70
    assert filled["patterns"]["PA_Hammer"]["timeframe"] == "1h"
    assert filled["patterns"]["PA_Hammer"]["lower_shadow_mult"] == 2.0
    assert filled["patterns"]["VOL_Spike"]["min_volume_ratio"] == 2.2
    assert filled["patterns"]["VOL_Spike"]["spike_ratio"] == 2.0

    bad_tf = normalize_patterns(
        {"patterns": {"Trend_SMA_Alignment": {"timeframe": "1min", "fast_sma": "sma_10"}}}
    )
    trend = bad_tf["patterns"]["Trend_SMA_Alignment"]
    assert trend["timeframe"] == "4h"
    assert trend["fast_sma"] == "sma_10"
    assert trend["slow_sma"] == "sma_50"

    hammer_defaults = apply_signal_pattern_defaults("PA_Hammer", {})
    assert hammer_defaults["timeframe"] == "4h"
    assert hammer_defaults["lower_shadow_mult"] == 2.0

    once = normalize_patterns({"patterns": {"BO_BB_Squeeze": {"lookback": 80}}})
    twice = normalize_patterns(once)
    assert once == twice


def test_level_breakout_retest_registry_schema():
    from app.analytics.pattern_registry import (
        SIGNAL_ENGINE_PATTERN_IDS,
        SIGNAL_ENGINE_PATTERN_SCHEMAS,
        is_signal_engine_pattern,
    )

    record = {p["id"]: p for p in list_patterns()}["level_breakout_retest"]
    assert record["category"] == "breakout"
    assert record["label"] == "Пробой уровня с ретестом"
    assert record["label_en"] == "Level Breakout Retest"
    assert record["icon"] == "breakout_up"
    assert "label_en" not in {p["id"]: p for p in list_patterns()}["levels_reversal"]
    keys = {param["key"] for param in record["params"]}
    assert keys == {
        "level_timeframe",
        "retest_window_bars",
        "retest_zone_atr",
        "entry_trigger_bullish",
        "stop_atr",
        "risk_reward",
    }
    defaults = get_pattern_defaults("level_breakout_retest")
    assert defaults["level_timeframe"] == "4h"
    assert defaults["retest_window_bars"] == 20
    assert defaults["retest_zone_atr"] == 0.5
    assert defaults["entry_trigger_bullish"] is True
    assert defaults["stop_atr"] == 1.0
    assert defaults["risk_reward"] == 2.0
    assert "level_breakout_retest" in SIGNAL_ENGINE_PATTERN_SCHEMAS
    assert "level_breakout_retest" not in SIGNAL_ENGINE_PATTERN_IDS
    assert is_signal_engine_pattern("level_breakout_retest") is False

    filled = normalize_patterns({"patterns": ["level_breakout_retest"]})
    assert filled["patterns"]["level_breakout_retest"]["retest_window_bars"] == 20


def test_levels_sr_breakout_registry_schema():
    from app.analytics.pattern_registry import (
        SIGNAL_ENGINE_PATTERN_IDS,
        SIGNAL_ENGINE_PATTERN_SCHEMAS,
        is_signal_engine_pattern,
    )

    record = {p["id"]: p for p in list_patterns()}["levels_sr_breakout"]
    assert record["category"] == "levels"
    assert record["label"] == "Поддержка + пробой сопротивления"
    assert record["label_en"] == "Support Reversal + Resistance Breakout"
    assert record["icon"] == "support_breakout"
    assert record["icon"] != "breakout_up"
    keys = {param["key"] for param in record["params"]}
    levels_keys = {
        "level_timeframe",
        "level_method",
        "swing_window",
        "impulse_body_ratio",
        "impulse_atr_mult",
        "zone_atr_mult",
        "confirm_windows",
    }
    retest_keys = {
        "retest_window_bars",
        "retest_zone_atr",
        "entry_trigger_bullish",
        "stop_atr",
        "risk_reward",
    }
    assert levels_keys <= keys
    assert retest_keys <= keys
    defaults = get_pattern_defaults("levels_sr_breakout")
    assert defaults["level_timeframe"] == "4h"
    assert defaults["confirm_windows"] == [10]
    assert defaults["retest_window_bars"] == 20
    assert defaults["stop_atr"] == 1.0
    assert defaults["risk_reward"] == 2.0
    assert "levels_sr_breakout" in SIGNAL_ENGINE_PATTERN_SCHEMAS
    assert "levels_sr_breakout" not in SIGNAL_ENGINE_PATTERN_IDS
    assert is_signal_engine_pattern("levels_sr_breakout") is False

    filled = normalize_patterns(
        {"patterns": ["levels_sr_breakout"], "confirm_windows": [15]}
    )
    assert filled["patterns"]["levels_sr_breakout"]["confirm_windows"] == [15]
    assert filled["confirm_windows"] == [15]
    assert filled["patterns"]["levels_sr_breakout"]["retest_window_bars"] == 20

    both = normalize_patterns(
        {
            "patterns": {
                "levels_reversal": {"confirm_windows": [5]},
                "levels_sr_breakout": {"confirm_windows": [20]},
            }
        }
    )
    assert both["confirm_windows"] == [20]

