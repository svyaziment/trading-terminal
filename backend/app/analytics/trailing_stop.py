"""Stepped trailing-stop ratchet - the single source of truth for production.

Issue #145 (Epic #142, Block W) moves the ladder that Issue #139 proved in analytics
(`analytics/issue-139-trailing-stop-new-level/trailing.py`, handover section 34) into the
live exit path. Every contour - `StrategyEvaluator.on_bar`, the `levels_reversal` plugin,
`portfolio_simulator` and, later, paper (#148) and sandbox live (#151) - calls ONLY the
functions here, so the fill convention can never drift between them.

The contract of `config.trailing_stop` (schema, defaults, normalization, reason codes)
belongs to Issue #144 and lives in `trading_config.py`; this module consumes it through
`resolve_trailing_stop()` and adds no new configuration surface.

Bar-internal order (identical to #139 and to the pre-#145 baseline):

  1. `bar_low <= live_stop`  -> exit at `live_stop`. The reason is `EXIT_TRAILING` once the
     ladder has raised the stop, otherwise the plain `EXIT_STOP` of the baseline. Keeping
     the un-armed exit on `EXIT_STOP` is what makes the `enabled=false` replay bit-for-bit.
  2. else `bar_high >= take` -> exit at `take` (`EXIT_TAKE`). The take is level-based and is
     NEVER moved by the ladder.
  3. only if neither fired, arm the ladder from THIS bar's high. The armed stop becomes
     effective from the NEXT bar - using the current bar's high to stop out the current bar
     would be intra-bar look-ahead and would inflate the result.

Because arming is a monotone maximum over the eligible steps, the state is fully described
by the highest high seen so far (see `ladder_stop`), so the bar-by-bar ratchet of #139 and
the running-max form used here give the same stop. `TrailingState.evaluate` keeps both
halves explicit (`live_stop` for the exit check, `pending_stop` for the next bar) so that a
caller may feed the same bar twice - the plugin lifecycle runs `manage_position` before
`check_exit` on one bar - without ever promoting a stop learned from that same bar.

Fail-safe: the engine refuses to arm a ladder the #144 validator rejects (reason codes from
`validate_trailing_steps`). `require_valid_trailing_stop()` is the gate for the WRITE paths
(#146 Lab, #149 API); until those land, a malformed block reaching the engine must not
silently change exits - it is reported through `resolve_trailing_stop()['reasons']` and the
position is managed exactly as it was before #145.

No I/O, no database, no hard-coded percentages: prices are always derived from `entry_exec`
and the initial risk `R = entry_exec - initial_stop`.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.analytics.backtest_models import EXIT_STOP, EXIT_TAKE, EXIT_TRAILING
from app.analytics.trading_config import resolve_trailing_stop

logger = logging.getLogger(__name__)


def _finite(value: Any) -> bool:
    """True for a real, finite number (bools and NaN/inf are not usable prices)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


@dataclass(frozen=True)
class TrailingDecision:
    """Outcome of one managed bar.

    `exit_price`/`exit_reason` are None while the position stays open. `stop` and
    `step_reached` are the values that become live on the NEXT bar (after this bar's
    arming), so a contour can mirror them into its own position record.
    """

    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    stop: float = 0.0
    step_reached: float = 0.0
    armed: bool = False

    @property
    def exits(self) -> bool:
        """True when this bar closes the position."""
        return self.exit_reason is not None


def ladder_stop(
    entry_exec: float,
    initial_stop: float,
    steps: Sequence[Dict[str, float]],
    ref_high: Optional[float],
) -> Tuple[float, float]:
    """Pure step function: the stop armed by a high-water mark of `ref_high`.

    Returns `(stop_price, step_reached)`, where `step_reached` is the R (above entry,
    measured from `entry_exec`) of the highest rung price action reached, 0.0 while no step
    is in reach. The stop is never below `initial_stop` and never moves down, so calling
    this with a non-decreasing `ref_high` is monotone and idempotent.

    `ref_high` is the highest bar high seen BEFORE the bar being evaluated; None or a
    non-finite value means "nothing observed yet" and returns the initial stop.
    """
    if not _finite(ref_high):
        return float(initial_stop), 0.0
    risk = float(entry_exec) - float(initial_stop)
    best_stop = float(initial_stop)
    best_reached = 0.0
    for step in steps or ():
        if not isinstance(step, dict):
            continue
        trigger, lock = step.get('trigger'), step.get('stop')
        if not _finite(trigger) or not _finite(lock):
            continue
        if float(ref_high) >= float(entry_exec) + float(trigger) * risk:
            candidate = float(entry_exec) + float(lock) * risk
            if candidate > best_stop:
                best_stop = candidate
            best_reached = max(best_reached, float(lock))
    return best_stop, best_reached


@dataclass
class TrailingState:
    """Per-position ladder state: entry geometry, live stop, stop armed for the next bar."""

    entry_exec: float
    initial_stop: float
    take: float
    risk: float
    steps: List[Dict[str, float]] = field(default_factory=list)
    live_stop: float = 0.0          # stop the exit check of the current bar uses
    step_reached: float = 0.0       # R of live_stop (0.0 before the first promotion)
    armed: bool = False             # a promotion has taken effect on the current bar
    pending_stop: float = 0.0       # stop armed through the current bar (live next bar)
    pending_reached: float = 0.0
    bar_key: Optional[Any] = None   # identity of the bar currently being evaluated

    @classmethod
    def build(
        cls,
        steps: Sequence[Dict[str, float]],
        *,
        entry_exec: float,
        initial_stop: float,
        take: float,
    ) -> Optional['TrailingState']:
        """Create the state for one position, or None when the ladder cannot apply.

        None means "manage this position exactly as before #145": an empty ladder, a
        non-positive risk unit, or non-finite prices.
        """
        usable = [
            {'trigger': float(s['trigger']), 'stop': float(s['stop'])}
            for s in (steps or ())
            if isinstance(s, dict) and _finite(s.get('trigger')) and _finite(s.get('stop'))
        ]
        if not usable:
            return None
        if not (_finite(entry_exec) and _finite(initial_stop) and _finite(take)):
            return None
        risk = float(entry_exec) - float(initial_stop)
        if risk <= 0:
            return None
        usable.sort(key=lambda s: s['trigger'])
        return cls(
            entry_exec=float(entry_exec),
            initial_stop=float(initial_stop),
            take=float(take),
            risk=risk,
            steps=usable,
            live_stop=float(initial_stop),
            pending_stop=float(initial_stop),
        )

    def arm(self, high: Any) -> None:
        """Ratchet using one bar high; the raised stop is effective from the next bar.

        `ladder_stop` always runs on the TRUE entry geometry (risk = entry_exec -
        initial_stop) - the floor is applied here as a maximum, so the stop can only ever
        move up and a lower bar high can never undo a rung.
        """
        stop_price, reached = ladder_stop(
            self.entry_exec, self.initial_stop, self.steps, high)
        if stop_price > self.pending_stop:
            self.pending_stop = stop_price
        if reached > self.pending_reached:
            self.pending_reached = reached

    def evaluate(self, *, high: Any, low: Any, bar_key: Any) -> TrailingDecision:
        """Apply the ladder to one managed bar: stop -> take -> arm."""
        if self.bar_key != bar_key:
            self.bar_key = bar_key
            # Promote what earlier bars armed; this bar's own high is never part of it.
            if self.pending_stop > self.live_stop:
                self.live_stop = self.pending_stop
                self.step_reached = self.pending_reached
                self.armed = self.live_stop > self.initial_stop
        if _finite(low) and float(low) <= self.live_stop:
            return TrailingDecision(
                exit_price=self.live_stop,
                exit_reason=EXIT_TRAILING if self.armed else EXIT_STOP,
                stop=self.pending_stop,
                step_reached=self.pending_reached,
                armed=self.armed,
            )
        if _finite(high) and float(high) >= self.take:
            return TrailingDecision(
                exit_price=self.take,
                exit_reason=EXIT_TAKE,
                stop=self.pending_stop,
                step_reached=self.pending_reached,
                armed=self.armed,
            )
        self.arm(high)
        return TrailingDecision(
            stop=self.pending_stop,
            step_reached=self.pending_reached,
            armed=self.armed,
        )

    def snapshot(self) -> Dict[str, Any]:
        """The position-visible ladder fields (Issue #145 section 2)."""
        return {
            'initial_stop': self.initial_stop,
            'risk_r': self.risk,
            'trailing_steps': [dict(s) for s in self.steps],
            'current_stop': self.live_stop,
            'step_reached': self.step_reached,
            'armed': self.armed,
        }


def trailing_from_config(
    config: Optional[Dict[str, Any]],
    *,
    entry_exec: float,
    initial_stop: float,
    take: float,
) -> Optional[TrailingState]:
    """Build the ladder state of one position from a strategy config.

    Returns None - baseline behaviour, bit-for-bit - when the block is absent, disabled,
    carries no usable step, or is refused by the Issue #144 validator.
    """
    resolved = resolve_trailing_stop(config)
    if not resolved['enabled'] or not resolved['steps']:
        return None
    if resolved['reasons']:
        logger.warning(
            "trailing stop not armed for this position: %s",
            ", ".join(resolved['reasons']),
        )
        return None
    return TrailingState.build(
        resolved['steps'],
        entry_exec=entry_exec,
        initial_stop=initial_stop,
        take=take,
    )


def evaluate_bar(
    state: Optional[TrailingState],
    *,
    high: Any,
    low: Any,
    bar_key: Any,
) -> Optional[TrailingDecision]:
    """The single call every contour makes; a None state keeps the baseline path intact."""
    if state is None:
        return None
    return state.evaluate(high=high, low=low, bar_key=bar_key)


def apply_trailing_path(
    *,
    entry_exec: float,
    initial_stop: float,
    take: float,
    path: Sequence[Sequence[float]],
    steps: Sequence[Dict[str, float]],
    commission_pct: float = 0.06,
) -> Dict[str, Any]:
    """Replay a recorded (high, low) path through the PRODUCTION ladder.

    Same shape as `analytics/issue-139-trailing-stop-new-level/trailing.py::apply_trailing`
    (`path` = managed bars, entry bar excluded, exit bar included) but driven by the engine
    code itself, so the #145 report and the unit tests can prove the ratchet on a recorded
    path without a database. Issue #147 owns the formal parity gate.
    """
    state = TrailingState.build(
        steps, entry_exec=entry_exec, initial_stop=initial_stop, take=take)
    if state is None:
        raise ValueError('apply_trailing_path needs a usable ladder and risk > 0')

    def _net(price: float) -> float:
        return round((float(price) / float(entry_exec) - 1.0) * 100.0
                     - float(commission_pct), 5)

    def _fixed() -> Tuple[float, str, int]:
        for index, (high, low) in enumerate(path):
            if _finite(low) and float(low) <= float(initial_stop):
                return float(initial_stop), EXIT_STOP, index
            if _finite(high) and float(high) >= float(take):
                return float(take), EXIT_TAKE, index
        last_high = max((float(h) for h, _ in path if _finite(h)), default=entry_exec)
        return last_high, 'open', max(len(path) - 1, 0)

    price_a, reason_a, index_a = _fixed()
    decision: Optional[TrailingDecision] = None
    index_b = max(len(path) - 1, 0)
    for index, (high, low) in enumerate(path):
        decision = state.evaluate(high=high, low=low, bar_key=index)
        if decision.exits:
            index_b = index
            break
    if decision is None or not decision.exits:
        last_high = max((float(h) for h, _ in path if _finite(h)), default=entry_exec)
        decision = TrailingDecision(last_high, 'open', state.pending_stop,
                                   state.pending_reached, state.armed)
        index_b = max(len(path) - 1, 0)

    return {
        'risk_rub': round(state.risk, 6),
        'steps': [dict(s) for s in state.steps],
        'baseline': {
            'exit_price': round(price_a, 4),
            'exit_reason': reason_a,
            'exit_index': index_a,
            'net_return_pct': _net(price_a),
        },
        'trailing': {
            'exit_price': round(float(decision.exit_price), 4),
            'exit_reason': decision.exit_reason,
            'exit_index': index_b,
            'step_reached': decision.step_reached,
            'armed': decision.armed,
            'net_return_pct': _net(float(decision.exit_price)),
        },
    }
