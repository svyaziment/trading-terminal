"""Abstract base class for strategy plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

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
    """Position state for strategy plugin.

    Issue #145 (Epic #142 Block W) adds the stepped trailing-stop fields. `trailing` is a
    `app.analytics.trailing_stop.TrailingState` built by the contour that opens the position;
    None means "no ladder" and keeps the pre-#145 fixed stop/take exit bit-for-bit.
    `stop` is the live stop (the ladder raises it, never lowers it), `initial_stop` and
    `step_reached` mirror the rung bookkeeping for reports and the panels (#150).
    """
    entry_price: float
    entry_ts: pd.Timestamp
    stop: float
    take: float
    size: float
    unrealized_pnl: float = 0.0
    bars_held: int = 0
    metadata: Optional[dict] = None
    initial_stop: Optional[float] = None
    step_reached: float = 0.0
    trailing: Optional[Any] = None


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
