"""Abstract base class for strategy plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from app.analytics.strategies.context import MarketContext


class PositionAction(Enum):
    """Actions for position management."""
    HOLD = 'hold'
    ADD = 'add'
    CLOSE = 'close'
    PARTIAL_CLOSE = 'partial_close'


@dataclass
class EntrySignal:
    """Entry signal from strategy plugin."""
    entry_price: float
    stop: float
    take: float
    timestamp: pd.Timestamp
    confidence: float = 1.0
    metadata: Optional[dict] = None


@dataclass
class ExitSignal:
    """Exit signal from strategy plugin."""
    exit_price: float
    reason: str
    timestamp: pd.Timestamp
    partial_pct: float = 1.0
    metadata: Optional[dict] = None


@dataclass
class Position:
    """Position state for strategy plugin."""
    entry_price: float
    entry_ts: pd.Timestamp
    stop: float
    take: float
    size: float
    unrealized_pnl: float = 0.0
    bars_held: int = 0
    metadata: Optional[dict] = None


class StrategyPlugin(ABC):
    """Abstract base class for trading strategy plugins.

    All strategy implementations must inherit from this class and implement
    the three core methods: check_entry, manage_position, check_exit.
    """

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def check_entry(self, context: MarketContext) -> Optional[EntrySignal]:
        """Check if entry conditions are met on current bar."""
        pass

    @abstractmethod
    def manage_position(self, position: Position, context: MarketContext) -> PositionAction:
        """Manage open position (hold/add/close)."""
        pass

    @abstractmethod
    def check_exit(self, position: Position, context: MarketContext) -> Optional[ExitSignal]:
        """Check if position should be exited."""
        pass

    def get_name(self) -> str:
        """Return strategy name (for registry and logging)."""
        return self.__class__.__name__

    def __repr__(self) -> str:
        return f"{self.get_name()}(config={self.config})"
