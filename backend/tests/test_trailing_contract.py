"""Contract tests for the stepped trailing stop - Issue #144 (Epic #142, Block W).

#144 delivers the `config.trailing_stop` SCHEMA, its DEFAULTS and its VALIDATION - and
deliberately nothing else: StrategyEvaluator applies the ladder in #145, the Lab writes
it in #146, the API surfaces the reason codes in #149, paper in #148, sandbox live in
#151. These tests are the guard rail of that contract (Issue #144 section 6): they read
no database, run no backtest and never mutate trading_config.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from app.analytics.backtest_models import (
    EXIT_HOLDING,
    EXIT_SESSION,
    EXIT_SIGNAL,
    EXIT_STOP,
    EXIT_TAKE,
    EXIT_TRAILING,
    VALID_EXIT_REASONS,
)
from app.analytics.trading_config import (
    TRAILING_REASON_DISABLED,
    TRAILING_REASON_NOT_MONOTONIC,
    TRAILING_REASON_STEP_INVALID,
    TRAILING_REASON_TOO_MANY_STEPS,
    TRAILING_STOP,
    get_trailing_stop_config,
    list_strategies,
    normalize_trailing_stop,
    require_valid_trailing_stop,
    resolve_trailing_stop,
    validate_trailing_steps,
)


# Product Owner decision of 2026-09-08 (#142): the shipped default ladder is
# `ultra_late_tight` of the 143-trailing-v3 lattice; `ref139` (the #139 grid) stays the
# parity anchor #147 injects explicitly. Both are cross-checked against the published
# source of the grid - analytics/issue-143-trailing-robustness/grids.json - the same
# way #155 pinned the Lab robustness metrics.
ANALYSIS_DIR = Path(__file__).resolve().parents[2] / "analytics" / "issue-143-trailing-robustness"
PO_GRID_ID = "ultra_late_tight"
REF139_GRID_ID = "ref139"


def _grids() -> dict:
    """The published 143-trailing-v3 lattice - the source the default is drawn from."""
    path = ANALYSIS_DIR / "grids.json"
    if not path.exists():
        pytest.skip("issue-143 analytics directory is not mounted")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["id"]: row for row in payload["grids"]}


def _grid(raw_id: str) -> dict:
    grids = _grids()
    assert raw_id in grids, f"grid {raw_id!r} is missing from the published lattice"
    return grids[raw_id]



def _block(steps, enabled: bool = True) -> dict:
    """A strategy config carrying a trailing_stop block."""
    return {"trailing_stop": {"enabled": enabled, "steps": steps}}


def _steps(*pairs) -> list:
    return [{"trigger": trigger, "stop": stop} for trigger, stop in pairs]


# ---------------------------------------------------------------------------
# 1. "No trailing" is the first-class default - nothing throws, nothing is armed
# ---------------------------------------------------------------------------

def test_config_without_key_resolves_to_no_trailing():
    assert normalize_trailing_stop({}) == {"enabled": False, "steps": []}
    assert normalize_trailing_stop({"min_bars": 10}) == {"enabled": False, "steps": []}
    assert resolve_trailing_stop({}) == {"enabled": False, "steps": [], "reasons": []}


def test_absent_none_and_empty_blocks_never_raise():
    for config in (None, {}, {"trailing_stop": None}, {"trailing_stop": {}},
                   {"trailing_stop": []}, "not-a-dict", 42):
        assert normalize_trailing_stop(config) == {"enabled": False, "steps": []}
        assert resolve_trailing_stop(config)["reasons"] == []


def test_shipped_registry_defaults_keep_trailing_off():
    """#144 changes no strategy behaviour: no registry entry carries the key."""
    strategies = list_strategies()
    assert strategies
    for entry in strategies:
        assert "trailing_stop" not in entry
        assert resolve_trailing_stop(entry) == {
            "enabled": False, "steps": [], "reasons": []}



def test_disabled_block_with_a_ladder_is_valid_but_inert():
    config = _block(_steps((2.0, 1.9), (2.5, 2.4)), enabled=False)
    resolved = resolve_trailing_stop(config)
    assert resolved["enabled"] is False
    assert resolved["reasons"] == []
    assert resolved["steps"] == _steps((2.0, 1.9), (2.5, 2.4))


# ---------------------------------------------------------------------------
# 2. Defaults: the PO approved grid, and nothing else
# ---------------------------------------------------------------------------

def test_default_ladder_is_the_po_approved_grid():
    assert _grid(PO_GRID_ID)["steps"] == copy.deepcopy(TRAILING_STOP["steps"])


def test_default_ladder_validates_and_is_enabled_only_by_opt_in():
    assert TRAILING_STOP["enabled"] is False
    assert validate_trailing_steps(TRAILING_STOP["steps"]) == []
    assert resolve_trailing_stop(_block(TRAILING_STOP["steps"]))["reasons"] == []


def test_ref139_anchor_grid_still_valid_but_not_the_default():
    """#147 must inject ref139 explicitly; #144 must keep it a legal ladder."""
    steps = _grid(REF139_GRID_ID)["steps"]
    assert steps != TRAILING_STOP["steps"]
    assert validate_trailing_steps(steps) == []


def test_every_published_lattice_grid_passes():
    """The bounds must not orphan any grid the robustness study is built on."""
    grids = _grids()
    assert len(grids) >= 8
    for raw_id, row in sorted(grids.items()):
        assert validate_trailing_steps(row["steps"]) == [], raw_id



def test_get_trailing_stop_config_returns_an_isolated_copy():
    snapshot = copy.deepcopy(TRAILING_STOP)
    fresh = get_trailing_stop_config()
    fresh["steps"][0]["trigger"] = 99.0
    fresh["max_steps"] = 1
    assert TRAILING_STOP == snapshot


# ---------------------------------------------------------------------------
# 3. Invalid ladders - rejected with stable reason codes, never silently fixed
# ---------------------------------------------------------------------------

def test_non_monotonic_ladder_is_rejected():
    reasons = validate_trailing_steps(_steps((2.0, 1.9), (2.5, 1.2)))
    assert reasons == [TRAILING_REASON_NOT_MONOTONIC]


def test_stop_not_below_trigger_is_rejected():
    assert validate_trailing_steps(_steps((2.0, 2.0))) == [TRAILING_REASON_STEP_INVALID]
    assert validate_trailing_steps(_steps((2.0, 2.5))) == [TRAILING_REASON_STEP_INVALID]


def test_negative_values_and_zero_trigger_are_rejected():
    assert validate_trailing_steps(_steps((2.0, -0.5))) == [TRAILING_REASON_STEP_INVALID]
    assert validate_trailing_steps(_steps((0.0, 0.0))) == [TRAILING_REASON_STEP_INVALID]


def test_break_even_stop_is_allowed():
    """stop = 0.0R means "move to break-even" - legal by contract (#144 section 4)."""
    assert validate_trailing_steps(_steps((2.0, 0.0))) == []


def test_out_of_bounds_ladder_is_rejected():
    assert validate_trailing_steps(_steps((4.0, 3.9))) == [TRAILING_REASON_STEP_INVALID]
    assert validate_trailing_steps(_steps((2.0, 3.5))) == [TRAILING_REASON_STEP_INVALID]


def test_tiny_trigger_stop_gap_is_accepted():
    """No minimum gap: 0.01R is valid, and the shipped default lives on a 0.1R gap.

    A "trigger - stop >= 0.5" heuristic would reject ultra_late_tight - this test is
    the guard against reintroducing it.
    """
    assert validate_trailing_steps(_steps((2.0, 1.99))) == []
    assert validate_trailing_steps(_steps((3.0, 2.99))) == []



def test_nan_and_inf_are_rejected():
    for bad in (math.nan, math.inf, -math.inf):
        assert validate_trailing_steps(_steps((2.0, bad))) == [TRAILING_REASON_STEP_INVALID]
        assert validate_trailing_steps(_steps((bad, 1.0))) == [TRAILING_REASON_STEP_INVALID]


def test_duplicate_trigger_with_different_stops_is_rejected():
    assert validate_trailing_steps(_steps((2.0, 1.5), (2.0, 1.9))) == [
        TRAILING_REASON_NOT_MONOTONIC]


def test_exact_duplicate_step_is_accepted_and_collapsed():
    steps = _steps((2.0, 1.9), (2.0, 1.9), (2.5, 2.4))
    assert validate_trailing_steps(steps) == []
    assert normalize_trailing_stop(_block(steps))["steps"] == _steps((2.0, 1.9), (2.5, 2.4))


def test_ladder_longer_than_max_steps_is_rejected():
    steps = _steps((0.5, 0.1), (1.0, 0.6), (1.5, 1.1),
                   (2.0, 1.6), (2.5, 2.1), (3.0, 2.6), (3.5, 3.0))
    assert len(steps) == TRAILING_STOP["max_steps"] + 1
    assert validate_trailing_steps(steps) == [TRAILING_REASON_TOO_MANY_STEPS]
    assert validate_trailing_steps(steps, max_steps=8) == []


def test_enabled_without_steps_is_rejected():
    assert validate_trailing_steps([]) == [TRAILING_REASON_DISABLED]
    assert validate_trailing_steps(None) == [TRAILING_REASON_DISABLED]
    assert validate_trailing_steps("2.0:1.9") == [TRAILING_REASON_DISABLED]
    assert resolve_trailing_stop(_block([]))["reasons"] == [TRAILING_REASON_DISABLED]
    # the same empty ladder switched off is the shipped "no trailing" state
    assert validate_trailing_steps([], enabled=False) == []


def test_malformed_step_shapes_are_rejected():
    for bad in ([{"trigger": 2.0}], [{"stop": 1.9}], ["2.0:1.9"], [None],
                [{"trigger": "2.0", "stop": 1.9}], [{"trigger": True, "stop": 1.9}]):
        assert validate_trailing_steps(bad) == [TRAILING_REASON_STEP_INVALID], bad


def test_reason_codes_are_stable_strings():
    assert (TRAILING_REASON_DISABLED, TRAILING_REASON_STEP_INVALID,
            TRAILING_REASON_NOT_MONOTONIC, TRAILING_REASON_TOO_MANY_STEPS) == (
        "trailing_disabled", "trailing_step_invalid",
        "trailing_not_monotonic", "trailing_too_many_steps")


def test_write_path_refuses_an_invalid_ladder():
    with pytest.raises(ValueError, match=TRAILING_REASON_NOT_MONOTONIC):
        require_valid_trailing_stop(_block(_steps((2.0, 1.9), (2.5, 1.2))))
    with pytest.raises(ValueError, match=TRAILING_REASON_DISABLED):
        require_valid_trailing_stop(_block([]))
    assert require_valid_trailing_stop(_block(TRAILING_STOP["steps"]))["enabled"] is True
    assert require_valid_trailing_stop({"trailing_stop": None}) == {
        "enabled": False, "steps": []}


# ---------------------------------------------------------------------------
# 4. Normalization: canonical, precise, idempotent, non-destructive
# ---------------------------------------------------------------------------

def test_normalize_sorts_by_trigger():
    shuffled = _steps((3.0, 2.9), (2.0, 1.9), (2.5, 2.4))
    assert normalize_trailing_stop(_block(shuffled))["steps"] == copy.deepcopy(
        TRAILING_STOP["steps"])


def test_normalize_keeps_float_precision():
    out = normalize_trailing_stop(_block(_steps((1.75, 1.725))))["steps"][0]
    assert out["trigger"] == 1.75 and isinstance(out["trigger"], float)
    assert out["stop"] == 1.725 and isinstance(out["stop"], float)


def test_normalize_is_idempotent():
    once = normalize_trailing_stop(_block(TRAILING_STOP["steps"]))
    twice = normalize_trailing_stop({"trailing_stop": once})
    assert twice == once


def test_normalize_coerces_ints_to_float():
    out = normalize_trailing_stop(_block(_steps((2, 1))))["steps"][0]
    assert out == {"trigger": 2.0, "stop": 1.0}
    assert isinstance(out["trigger"], float) and isinstance(out["stop"], float)


def test_normalize_never_mutates_the_input_config():
    config = _block(_steps((3.0, 2.9), (2.0, 1.9)))
    before = copy.deepcopy(config)
    normalize_trailing_stop(config)
    resolve_trailing_stop(config)
    assert config == before


def test_normalize_drops_broken_steps_but_validation_still_reports_them():
    config = _block([{"trigger": 2.0, "stop": 1.9}, {"trigger": "x", "stop": 1.0}])
    assert normalize_trailing_stop(config)["steps"] == _steps((2.0, 1.9))
    assert resolve_trailing_stop(config)["reasons"] == [TRAILING_REASON_STEP_INVALID]


def test_enabled_flag_is_strict():
    for truthy in (True, "true", "1", "yes"):
        assert normalize_trailing_stop(_block([], enabled=truthy))["enabled"] is True
    for falsy in (False, "no", "0", None, 0, 1, "  "):
        assert normalize_trailing_stop(_block([], enabled=falsy))["enabled"] is False


def test_unknown_keys_do_not_change_the_verdict():
    """The block is JSONB, not a strict model: extra keys are inert, not a rejection."""
    config = {"trailing_stop": {"enabled": True, "steps": _steps((2.0, 1.9)), "note": "lab"}}
    assert resolve_trailing_stop(config)["reasons"] == []


# ---------------------------------------------------------------------------
# 5. EXIT_TRAILING is a contract value only - #144 changes no behaviour
# ---------------------------------------------------------------------------

def test_exit_trailing_joins_the_closed_set_without_touching_the_others():
    assert EXIT_TRAILING == "trailing"
    assert VALID_EXIT_REASONS == {
        EXIT_STOP, EXIT_TAKE, EXIT_HOLDING, EXIT_SIGNAL, EXIT_SESSION, EXIT_TRAILING}


def test_engine_does_not_emit_trailing_yet():
    """#145 owns the emission. Until then no backtest or paper path may return it."""
    engine = (Path(__file__).resolve().parents[1] / "app" / "analytics" /
              "strategy_engine.py").read_text(encoding="utf-8")
    assert f'"{EXIT_TRAILING}"' not in engine



