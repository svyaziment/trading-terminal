"""Levels reversal strategy plugin - wrapper around StrategyEvaluator.

Refactored for Issue #41: check_exit delegates the exit logic that lives in
StrategyEvaluator.on_bar (stop/take checks). The exit logic here is a direct
mirror of the exit branch in on_bar, ensuring bit-for-bit regression parity.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from app.analytics.strategy_engine import StrategyEvaluator
from app.analytics.strategies.base import (
    EntrySignal,
    ExitSignal,
    Position,
    PositionAction,
    StrategyPlugin,
)
from app.analytics.strategies.context import MarketContext

logger = logging.getLogger(__name__)


def _htf_bars_from_context(context: MarketContext):
    """Prefer explicit htf_bars (levels TF). Do not bool()-coerce a DataFrame."""
    htf = getattr(context, "htf_bars", None)
    if htf is not None:
        return htf
    return context.candles_4h


class LevelsReversalStrategy(StrategyPlugin):
    """Levels reversal strategy plugin.

    Wrapper around the existing StrategyEvaluator (single brain architecture).
    - check_entry: delegates to StrategyEvaluator.check_entry
    - check_exit: mirrors the exit branch of StrategyEvaluator.on_bar (stop/take)
    - manage_position: HOLD (no averaging/pyramiding for this strategy)
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._evaluator = StrategyEvaluator(config)
        self._context_loaded = False

    def load_market_context(self, context: MarketContext) -> None:
        """Load 4h context into the internal evaluator (call once before the bar loop)."""
        self._evaluator.load_context(
            levels=context.levels,
            ts_4h=context.ts_4h,
            atr_by_ts=context.atr_by_ts,
            buy_ts=context.buy_ts,
            confirm_series=context.confirm_series,
            signal_filter_series=getattr(context, "signal_filter_series", None),
            htf_bars=_htf_bars_from_context(context),
        )
        self._context_loaded = True

    def check_entry(self, context: MarketContext) -> Optional[EntrySignal]:
        """Check entry conditions using StrategyEvaluator.check_entry."""
        if context.candles_1min is None or context.candles_1min.empty:
            return None

        if not self._context_loaded:
            self.load_market_context(context)

        # Reset position state so check_entry is pure (no open position)
        self._evaluator.position = None

        latest_bar = context.candles_1min.iloc[-1]
        decision = self._evaluator.check_entry(latest_bar)

        if decision is None:
            return None

        return EntrySignal(
            entry_price=decision['entry_price'],
            stop=decision['stop'],
            take=decision['take'],
            timestamp=decision['ts'],
            confidence=1.0,
            metadata={'source': decision.get('source', 'levels_reversal')},
        )

    def manage_position(self, position: Position, context: MarketContext) -> PositionAction:
        """No position management (no averaging/pyramiding)."""
        return PositionAction.HOLD

    def check_exit(self, position: Position, context: MarketContext) -> Optional[ExitSignal]:
        """Check exit conditions - mirrors the exit branch of StrategyEvaluator.on_bar.

        This is the exact exit logic from on_bar:
            if row['low'] <= stop: exit at stop
            elif row['high'] >= take: exit at take
        Kept as a direct mirror to guarantee bit-for-bit regression parity.
        """
        if context.candles_1min is None or context.candles_1min.empty:
            return None

        latest_bar = context.candles_1min.iloc[-1]
        bar_low = float(latest_bar['low'])
        bar_high = float(latest_bar['high'])

        # Mirror of on_bar exit branch (stop checked first, then take)
        if bar_low <= position.stop:
            return ExitSignal(
                exit_price=position.stop,
                reason='stop',
                timestamp=context.timestamp,
                partial_pct=1.0,
                metadata={'bars_held': position.bars_held},
            )

        if bar_high >= position.take:
            return ExitSignal(
                exit_price=position.take,
                reason='take',
                timestamp=context.timestamp,
                partial_pct=1.0,
                metadata={'bars_held': position.bars_held},
            )

        return None

    def get_name(self) -> str:
        return 'levels_reversal'
