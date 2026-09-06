"""Stepped trailing-stop exit mode — analytics only (Issue #139).

This module is NOT part of the production backtest / paper / sandbox path.
`StrategyEvaluator` and `portfolio_simulator.py` are left untouched; the logic
here re-uses their exact fill convention so that an A/B comparison differs from
the baseline by ONLY the exit rule.

Conventions (must match `StrategyEvaluator.on_bar`):
  * Round-trip commission is folded into the return as a constant percentage
    (`commission_pct`), slippage 0 by strategy config.
  * Within a managed bar the stop is checked BEFORE the take. A bar whose low
    reaches the live stop exits at that stop even if the high also reaches the
    take; a bar whose low does not reach the stop but whose high reaches the
    take exits at the take.
  * The trailing ratchet uses the CURRENT bar's high only for FUTURE bars, so
    a bar cannot both raise the stop with its high and then be stopped out by
    that same raised stop (no intra-bar look-ahead).

Risk unit:
  * R = entry_exec - initial_stop  (initial_stop = entry - 1R, so R > 0).
  * A step is {"trigger": <R above entry>, "stop": <R above entry>}. Prices are
    derived from the ENTRY, matching the table in Issue #139:

        trigger +2.0 R -> stop -> +1.5 R
        trigger +2.5 R -> stop -> +2.0 R
        +3.0 R         -> take (unchanged, level-based; RR>=3 gate guarantees it)

    Steps are expressed in R and are configurable; no hard-coded percentages.
"""
from __future__ import annotations

from typing import Any, Sequence

# Configurable stepped trailing table (Issue #139). Values are in R measured
# from the entry price. Edit this list (or pass `steps=`) to test other grids.
DEFAULT_STEPS: list[dict[str, float]] = [
    {"trigger": 2.0, "stop": 1.5},
    {"trigger": 2.5, "stop": 2.0},
]


def _net_return_pct(
    exit_price: float, entry_exec: float, commission_pct: float
) -> float:
    """Mirror `StrategyEvaluator.on_bar` return with zero slippage."""
    gross = (exit_price / entry_exec - 1.0) * 100.0
    return round(gross - commission_pct, 5)


def apply_trailing(
    entry_price: float,
    initial_stop: float,
    take: float,
    path: Sequence[Sequence[float]],
    *,
    commission_pct: float = 0.06,
    steps: Sequence[dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Replay a trade's managed-bar path under the stepped trailing rule.

    `path` is the list of (high, low) for every bar while the position was open
    (entry bar excluded, exit bar included) — identical to the bars the unified
    brain evaluates. Returns the baseline-consistent A exit and the trailing B
    exit for the SAME path so the caller can prove the single-difference rule.
    """
    step_list = sorted(steps if steps is not None else DEFAULT_STEPS,
                       key=lambda s: float(s["trigger"]))

    # --- Baseline (A): fixed stop at initial_stop, take fixed. Same loop, no ratchet.
    def _fixed() -> tuple[float, str, int]:
        for idx, (high, low) in enumerate(path):
            if low <= initial_stop:
                return initial_stop, "stop", idx
            if high >= take:
                return take, "take", idx
        last_idx = max(len(path) - 1, 0)
        last_high = max((h for h, _ in path), default=entry_price)
        return last_high, "open", last_idx  # not expected: path ends on a real exit

    a_price, a_reason, a_index = _fixed()

    # --- Trailing (B): ratchet the stop up on new highs, never below initial.
    entry_exec = entry_price  # slippage 0 per strategy config
    risk = entry_exec - initial_stop  # = 1R > 0
    cur_stop = initial_stop
    b_price: float | None = None
    b_reason = "open"
    b_index = max(len(path) - 1, 0)
    step_reached = 0.0
    for idx, (high, low) in enumerate(path):
        if low <= cur_stop:
            b_price = cur_stop
            b_reason = "initial_stop" if cur_stop <= initial_stop else "trailing"
            b_index = idx
            break
        if high >= take:
            b_price = take
            b_reason = "take"
            b_index = idx
            break
        # Raise the stop using THIS bar's high for subsequent bars.
        for step in step_list:
            trigger_price = entry_exec + float(step["trigger"]) * risk
            new_stop = entry_exec + float(step["stop"]) * risk
            if high >= trigger_price and new_stop > cur_stop:
                cur_stop = new_stop
                step_reached = max(step_reached, float(step["stop"]))
    if b_price is None:  # path ran out without an exit (should not happen)
        b_price = max((h for h, _ in path), default=entry_exec)

    return {
        "risk_rub": round(risk, 6),
        "baseline": {
            "exit_price": round(float(a_price), 4),
            "exit_reason": a_reason,
            "exit_index": a_index,
            "net_return_pct": _net_return_pct(a_price, entry_exec, commission_pct),
        },
        "trailing": {
            "exit_price": round(float(b_price), 4),
            "exit_reason": b_reason,
            "exit_index": b_index,
            "net_return_pct": _net_return_pct(b_price, entry_exec, commission_pct),
            "step_reached": step_reached,
        },
        "steps": step_list,
    }


if __name__ == "__main__":  # tiny self-check, no DB
    # +2R (=120) triggers stop -> +1.5R (=115); next bar low 114 <= 115 -> trailing.
    # Baseline still rides to the +3R take (=130) on bar3's high 140.
    entry, stop, take = 100.0, 90.0, 130.0  # R = 10, take = +3R
    demo_path = [(121, 118), (120, 114), (140, 113)]
    out = apply_trailing(entry, stop, take, demo_path)
    assert out["baseline"]["exit_reason"] == "take", out
    assert out["trailing"]["exit_reason"] == "trailing", out
    assert abs(out["trailing"]["exit_price"] - 115.0) < 1e-6, out
    assert abs(out["trailing"]["step_reached"] - 1.5) < 1e-9, out
    print("trailing.py self-check OK:", out["trailing"], out["baseline"])
