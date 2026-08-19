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
