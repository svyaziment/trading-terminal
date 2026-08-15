"""Strategy Plugin System - pluggable trading strategies."""

from app.analytics.strategies.base import StrategyPlugin, EntrySignal, ExitSignal, PositionAction, Position
from app.analytics.strategies.context import MarketContext
from app.analytics.strategies.registry import StrategyRegistry, get_registry
from app.analytics.strategies.levels_reversal import LevelsReversalStrategy

__all__ = [
    'StrategyPlugin',
    'EntrySignal',
    'ExitSignal',
    'PositionAction',
    'Position',
    'MarketContext',
    'StrategyRegistry',
    'get_registry',
    'LevelsReversalStrategy',
]
