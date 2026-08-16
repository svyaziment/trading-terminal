"""ATR Reversal strategy plugin - based on Artem Zvezdin's approach.

Entry conditions (AND logic):
- ATR 80-90% completed (price moved 80-90% of current ATR from recent low/high)
- Price approached support/resistance level (with optional breakout)
- Volume spike > 2x average volume

Exit conditions:
- Stop: 100% of ATR from entry point
- Take: 80-90% of opposite ATR (target level)

No averaging (one asset = one position).
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import numpy as np

from app.analytics.strategies.base import (
    EntrySignal,
    ExitSignal,
    Position,
    PositionAction,
    StrategyPlugin,
)
from app.analytics.strategies.context import MarketContext
from app.analytics.levels_engine import build_levels, nearest_level_at
from app.analytics.levels_backtest import compute_atr

logger = logging.getLogger(__name__)


class AtrReversalStrategy(StrategyPlugin):
    """ATR Reversal strategy plugin.

    Implements Artem Zvezdin's ATR-based reversal strategy:
    - Entry when ATR 80-90% completed + price at level + volume spike
    - Exit at stop (100% ATR) or take (80-90% opposite ATR)
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.atr_period = config.get('atr_period', 14)
        self.atr_completion_min = config.get('atr_completion_min', 0.80)  # 80%
        self.atr_completion_max = config.get('atr_completion_max', 0.90)  # 90%
        self.volume_spike_mult = config.get('volume_spike_mult', 2.0)
        self.stop_atr_mult = config.get('stop_atr_mult', 1.0)  # 100% ATR
        self.take_atr_mult = config.get('take_atr_mult', 0.85)  # 85% ATR (average of 80-90%)
        self.level_proximity_atr = config.get('level_proximity_atr', 0.5)  # within 0.5 ATR of level

    def check_entry(self, context: MarketContext) -> Optional[EntrySignal]:
        """Check ATR reversal entry conditions."""
        if context.candles_1min is None or len(context.candles_1min) < self.atr_period + 10:
            return None

        candles = context.candles_1min
        current_bar = candles.iloc[-1]
        current_atr = context.get_atr(self.atr_period)

        # Standalone callers may not precompute ATR in MarketContext.
        if current_atr <= 0:
            computed_atr = compute_atr(candles, period=self.atr_period)
            if computed_atr.isna().all():
                return None
            current_atr = computed_atr.iloc[-1]
        
        if pd.isna(current_atr) or current_atr <= 0:
            return None
        
        current_price = float(current_bar['close'])
        current_low = float(current_bar['low'])
        current_high = float(current_bar['high'])
        
        # Check volume spike
        if context.volume_current is None or context.volume_sma_20 is None:
            return None
        
        if context.volume_current < self.volume_spike_mult * context.volume_sma_20:
            return None
        
        # Determine direction: bullish (long) or bearish (short)
        # For now, focus on bullish reversals (long positions at support)
        # TODO: Add bearish reversal logic
        
        # Find recent low (lookback window for ATR completion)
        lookback = max(self.atr_period, 20)
        recent_lows = candles['low'].iloc[-lookback:]
        recent_low = float(recent_lows.min())
        
        # Check ATR completion: price moved 80-90% of ATR from recent low
        price_move = current_price - recent_low
        atr_completion = price_move / current_atr if current_atr > 0 else 0
        
        if not (self.atr_completion_min <= atr_completion <= self.atr_completion_max):
            return None
        
        # Check proximity to support level
        if context.levels is None or len(context.levels) == 0:
            return None
        
        # Build levels DataFrame for nearest_level_at
        levels_df = pd.DataFrame(context.levels)
        support = nearest_level_at(levels_df, context.timestamp, current_price, 'support')
        
        if support is None:
            return None
        
        support_price = support['level_price']
        distance_to_support = current_price - support_price
        
        # Price should be near support (within level_proximity_atr * ATR)
        if distance_to_support > self.level_proximity_atr * current_atr:
            return None
        
        # Entry signal: long position at support
        entry_price = current_price
        stop = entry_price - self.stop_atr_mult * current_atr
        take = entry_price + self.take_atr_mult * current_atr
        
        return EntrySignal(
            entry_price=entry_price,
            stop=stop,
            take=take,
            timestamp=context.timestamp,
            confidence=1.0,
            metadata={
                'source': 'atr_reversal',
                'atr': current_atr,
                'atr_completion': atr_completion,
                'volume_spike': context.volume_current / context.volume_sma_20,
                'support_level': support_price,
            },
        )

    def manage_position(self, position: Position, context: MarketContext) -> PositionAction:
        """No position management (no averaging)."""
        return PositionAction.HOLD

    def check_exit(self, position: Position, context: MarketContext) -> Optional[ExitSignal]:
        """Check stop/take conditions."""
        if context.candles_1min is None or context.candles_1min.empty:
            return None

        current_bar = context.candles_1min.iloc[-1]
        bar_low = float(current_bar['low'])
        bar_high = float(current_bar['high'])

        # Check stop loss
        if bar_low <= position.stop:
            return ExitSignal(
                exit_price=position.stop,
                reason='stop',
                timestamp=context.timestamp,
                partial_pct=1.0,
                metadata={'bars_held': position.bars_held},
            )

        # Check take profit
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
        return 'atr_reversal'
