"""Issue #147: Cross-parity gate between analytics #139 and production trailing_stop.

Imports DEFAULT_STEPS and apply_trailing directly from the frozen analytics package
(analytics/issue-139-trailing-stop-new-level/trailing.py) and compares against the
production apply_trailing_path (backend/app/analytics/trailing_stop.py).

No database required. Run:
    cd backend && python -m pytest -q tests/test_trailing_parity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYTICS_139_DIR = REPO_ROOT / "analytics" / "issue-139-trailing-stop-new-level"
if str(ANALYTICS_139_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYTICS_139_DIR))

from trailing import DEFAULT_STEPS, apply_trailing  # noqa: E402
from app.analytics.trailing_stop import (  # noqa: E402
    apply_trailing_path,
    ladder_stop,
    TrailingState,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ENTRY = 100.0
STOP = 90.0
TAKE = 130.0
COMMISSION = 0.06
RISK = ENTRY - STOP  # 10.0

# ref139 grid (Issue #139 DEFAULT_STEPS)
REF139_STEPS = [{"trigger": 2.0, "stop": 1.5}, {"trigger": 2.5, "stop": 2.0}]

# ultra_late_tight grid (production default, Issue #144 / PO decision 2026-09-08)
ULTRA_LATE_TIGHT_STEPS = [
    {"trigger": 2.0, "stop": 1.9},
    {"trigger": 2.5, "stop": 2.4},
    {"trigger": 3.0, "stop": 2.9},
]

# Reason mapping: analytics #139 -> production
# analytics "initial_stop" == production "stop" (EXIT_STOP, ladder never raised)
# analytics "trailing"     == production "trailing" (EXIT_TRAILING, ladder raised)
ANALYTICS_TO_PROD_REASON = {
    "initial_stop": "stop",
    "trailing": "trailing",
    "take": "take",
    "stop": "stop",
    "open": "open",
}


# ---------------------------------------------------------------------------
# Synthetic price paths (high, low) — entry bar excluded, exit bar included
# ---------------------------------------------------------------------------

def path_trailing_first_step():
    """+2R reached on bar 0 (arms stop 115), bar 1 low hits it."""
    return [(121.0, 118.0), (119.0, 114.0)]


def path_trailing_second_step():
    """Both steps armed, exit on second-step stop (120)."""
    return [(121.0, 118.0), (126.0, 119.0), (124.0, 119.5)]


def path_take():
    """Take hit without trailing arming."""
    return [(119.0, 110.0), (131.0, 120.0)]


def path_initial_stop():
    """Initial stop hit, no trailing ever armed."""
    return [(105.0, 95.0), (103.0, 89.0)]


def path_no_exit():
    """Path runs out without any exit (edge case)."""
    return [(105.0, 95.0), (108.0, 100.0)]


ALL_PATHS = [
    path_trailing_first_step(),
    path_trailing_second_step(),
    path_take(),
    path_initial_stop(),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_analytics(path, steps=None):
    return apply_trailing(
        ENTRY, STOP, TAKE, path,
        commission_pct=COMMISSION,
        steps=steps if steps is not None else REF139_STEPS,
    )


def run_production(path, steps=None):
    return apply_trailing_path(
        entry_exec=ENTRY,
        initial_stop=STOP,
        take=TAKE,
        path=path,
        steps=steps if steps is not None else REF139_STEPS,
        commission_pct=COMMISSION,
    )


def map_analytics_reason(reason: str) -> str:
    return ANALYTICS_TO_PROD_REASON.get(reason, reason)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultStepsContract:
    """DEFAULT_STEPS from frozen analytics must match ref139."""

    def test_default_steps_equal_ref139(self):
        assert DEFAULT_STEPS == REF139_STEPS, (
            f"DEFAULT_STEPS {DEFAULT_STEPS} != ref139 {REF139_STEPS}"
        )

    def test_ultra_late_tight_is_not_default(self):
        assert DEFAULT_STEPS != ULTRA_LATE_TIGHT_STEPS


class TestTrailingParity:
    """Parity of exit mechanics between analytics #139 and production #145."""

    @pytest.mark.parametrize("path", ALL_PATHS, ids=[
        "trailing_1step", "trailing_2step", "take", "initial_stop",
    ])
    def test_exit_reason_parity(self, path):
        a = run_analytics(path)
        p = run_production(path)
        a_reason = a["trailing"]["exit_reason"]
        p_reason = p["trailing"]["exit_reason"]
        assert map_analytics_reason(a_reason) == p_reason, (
            f"Exit reason mismatch: analytics={a_reason!r} "
            f"(mapped={map_analytics_reason(a_reason)!r}), prod={p_reason!r}"
        )

    @pytest.mark.parametrize("path", ALL_PATHS, ids=[
        "trailing_1step", "trailing_2step", "take", "initial_stop",
    ])
    def test_exit_price_parity(self, path):
        a = run_analytics(path)
        p = run_production(path)
        a_price = a["trailing"]["exit_price"]
        p_price = p["trailing"]["exit_price"]
        assert abs(a_price - p_price) < 0.01, (
            f"Exit price mismatch: analytics={a_price}, prod={p_price}"
        )

    @pytest.mark.parametrize("path", ALL_PATHS, ids=[
        "trailing_1step", "trailing_2step", "take", "initial_stop",
    ])
    def test_net_return_parity(self, path):
        a = run_analytics(path)
        p = run_production(path)
        a_ret = a["trailing"]["net_return_pct"]
        p_ret = p["trailing"]["net_return_pct"]
        assert abs(a_ret - p_ret) < 0.01, (
            f"Net return mismatch: analytics={a_ret}, prod={p_ret}"
        )

    @pytest.mark.parametrize("path", ALL_PATHS, ids=[
        "trailing_1step", "trailing_2step", "take", "initial_stop",
    ])
    def test_baseline_parity(self, path):
        """Baseline (fixed stop/take) must be identical in both modules."""
        a = run_analytics(path)
        p = run_production(path)
        assert a["baseline"]["exit_reason"] == p["baseline"]["exit_reason"], (
            f"Baseline reason: analytics={a['baseline']['exit_reason']}, "
            f"prod={p['baseline']['exit_reason']}"
        )
        assert abs(a["baseline"]["exit_price"] - p["baseline"]["exit_price"]) < 0.01
        assert abs(a["baseline"]["net_return_pct"] - p["baseline"]["net_return_pct"]) < 0.01


class TestUltraLateTightGrid:
    """Production default grid (ultra_late_tight) must also be consistent."""

    def test_trailing_exit(self):
        # +2R=120 arms stop 119; bar 1 low=118.5 <= 119 -> trailing
        path = [(121.0, 118.0), (120.0, 118.5)]
        p = run_production(path, steps=ULTRA_LATE_TIGHT_STEPS)
        assert p["trailing"]["exit_reason"] == "trailing"
        assert abs(p["trailing"]["exit_price"] - 119.0) < 0.01

    def test_three_steps(self):
        # +3R=130 arms stop 129; but take=130 is hit first on bar 2
        path = [(121.0, 118.0), (126.0, 124.0), (131.0, 128.0)]
        p = run_production(path, steps=ULTRA_LATE_TIGHT_STEPS)
        # Bar 0: high=121 >= 120 -> arm stop 119. low=118 > 90, no exit.
        # Bar 1: live_stop=119. high=126 >= 125 -> arm stop 124. low=124 > 119, no exit.
        # Bar 2: live_stop=124. high=131 >= 130 -> take exit.
        assert p["trailing"]["exit_reason"] == "take"
        assert abs(p["trailing"]["exit_price"] - TAKE) < 0.01


class TestLadderStop:
    """Unit tests for the pure ladder_stop function."""

    def test_monotonic(self):
        prev_stop = STOP
        for high in [105, 110, 115, 119, 120, 121, 124, 125, 126, 130]:
            stop, _ = ladder_stop(ENTRY, STOP, REF139_STEPS, high)
            assert stop >= prev_stop, f"Stop decreased at high={high}"
            prev_stop = stop

    def test_no_trigger_below_2r(self):
        stop, reached = ladder_stop(ENTRY, STOP, REF139_STEPS, 119.0)
        assert stop == STOP
        assert reached == 0.0

    def test_first_step_at_2r(self):
        stop, reached = ladder_stop(ENTRY, STOP, REF139_STEPS, 120.0)
        assert abs(stop - 115.0) < 1e-9  # +1.5R
        assert abs(reached - 1.5) < 1e-9

    def test_second_step_at_2_5r(self):
        stop, reached = ladder_stop(ENTRY, STOP, REF139_STEPS, 125.0)
        assert abs(stop - 120.0) < 1e-9  # +2.0R
        assert abs(reached - 2.0) < 1e-9

    def test_none_high_returns_initial(self):
        stop, reached = ladder_stop(ENTRY, STOP, REF139_STEPS, None)
        assert stop == STOP
        assert reached == 0.0


class TestNoIntraBarLookahead:
    """A bar that arms a step must NOT be stopped out by that same step."""

    def test_arming_bar_does_not_exit(self):
        # Bar 0: high=121 arms stop 115, low=114.
        # 114 > 90 (initial stop), so no exit on bar 0.
        # The armed stop 115 is only effective from bar 1.
        # Bar 1: low=113 <= 115 -> trailing exit.
        path = [(121.0, 114.0), (116.0, 113.0)]
        p = run_production(path)
        assert p["trailing"]["exit_reason"] == "trailing"
        assert abs(p["trailing"]["exit_price"] - 115.0) < 0.01
        assert p["trailing"]["exit_index"] == 1  # second bar, not first

    def test_analytics_same_convention(self):
        """Analytics #139 must also NOT exit on the arming bar."""
        path = [(121.0, 114.0), (116.0, 113.0)]
        a = run_analytics(path)
        assert a["trailing"]["exit_reason"] == "trailing"
        assert abs(a["trailing"]["exit_price"] - 115.0) < 0.01
        assert a["trailing"]["exit_index"] == 1
