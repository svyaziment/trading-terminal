"""Levels reversal strategy plugin - wrapper around StrategyEvaluator."""

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


class LevelsReversalStrategy(StrategyPlugin):
    """Levels reversal strategy plugin.

    Wrapper around the existing StrategyEvaluator (single brain architecture).
    Delegates entry logic to StrategyEvaluator.check_entry() while providing
    the new StrategyPlugin interface.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._evaluator = StrategyEvaluator(config)

    def check_entry(self, context: MarketContext) -> Optional[EntrySignal]:
        """Check entry conditions using StrategyEvaluator."""
        if context.candles_1min is None or context.candles_1min.empty:
            return None

        # Load context into evaluator
        self._evaluator.load_context(
            levels=context.levels,
            ts_4h=context.ts_4h,
            atr_by_ts=context.atr_by_ts,
            buy_ts=context.buy_ts,
            confirm_series=context.confirm_series,
        )

        # Get latest 1min bar
        latest_bar = context.candles_1min.iloc[-1]

        # Delegate to StrategyEvaluator
        decision = self._evaluator.check_entry(latest_bar)

        if decision is None:
            return None

        return EntrySignal(
            entry_price=decision['entry_price'],
            stop=decision['stop'],
            take=decision['take'],
            timestamp=decision['ts'],
            confidence=1.0,
            metadata={'source': 'levels_reversal'},
        )

    def manage_position(self, position: Position, context: MarketContext) -> PositionAction:
        """Manage open position. Current: HOLD (no averaging)."""
        return PositionAction.HOLD

    def check_exit(self, position: Position, context: MarketContext) -> Optional[ExitSignal]:
        """Check exit conditions (stop/take hit)."""
        if context.candles_1min is None or context.candles_1min.empty:
            return None

        latest_bar = context.candles_1min.iloc[-1]
        bar_low = float(latest_bar['low'])
        bar_high = float(latest_bar['high'])

        if bar_low <= position.stop:
            return ExitSignal(
                exit_price=position.stop,
                reason='stop',
                timestamp=context.timestamp,
                partial_pct=1.0,
            )

        if bar_high >= position.take:
            return ExitSignal(
                exit_price=position.take,
                reason='take',
                timestamp=context.timestamp,
                partial_pct=1.0,
            )

        return None

    def get_name(self) -> str:
        return 'levels_reversal'
