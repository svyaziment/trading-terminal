"""Market context dataclass - unified data container for strategy plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class MarketContext:
    """Unified market data container for strategy plugins.

    Contains all data needed for entry/exit/management decisions:
    - Candles (1min required, higher TFs optional)
    - ATR values (multiple periods)
    - Support/resistance levels
    - Volume data (current + moving averages)
    - Order book (live only, None for backtest)
    - Current bar timestamp
    """

    # Current bar timestamp
    timestamp: pd.Timestamp

    # 1min candles (required)
    candles_1min: pd.DataFrame

    # Higher timeframe candles (optional)
    candles_5min: Optional[pd.DataFrame] = None
    candles_15min: Optional[pd.DataFrame] = None
    candles_1h: Optional[pd.DataFrame] = None
    candles_4h: Optional[pd.DataFrame] = None
    candles_1d: Optional[pd.DataFrame] = None

    # ATR values by period
    atr_by_period: Dict[int, float] = field(default_factory=dict)

    # ATR values by timestamp (for HTF alignment)
    atr_by_ts: Dict = field(default_factory=dict)

    # Support/resistance levels
    levels: Optional[List[Dict]] = None

    # 4h bar timestamps
    ts_4h: List = field(default_factory=list)

    # 4h BUY signal timestamps
    buy_ts: List = field(default_factory=list)

    # Multi-window confirmation series: list of (timestamps, closes)
    confirm_series: List[Tuple] = field(default_factory=list)

    # Volume data
    volume_current: Optional[float] = None
    volume_sma_20: Optional[float] = None
    volume_sma_50: Optional[float] = None

    # Order book (live only, None for backtest)
    orderbook_imbalance: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None

    # Indicators (optional, computed on 1min)
    rsi_14: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_lower: Optional[float] = None

    def get_candle(self, timeframe: str = '1min') -> Optional[pd.DataFrame]:
        """Get candles for specified timeframe."""
        tf_map = {
            '1min': self.candles_1min,
            '5min': self.candles_5min,
            '15min': self.candles_15min,
            '1h': self.candles_1h,
            '4h': self.candles_4h,
            '1d': self.candles_1d,
        }
        return tf_map.get(timeframe)

    def get_atr(self, period: int = 14) -> float:
        """Get ATR for specified period (default 14)."""
        return self.atr_by_period.get(period, 0.0)

    def has_orderbook(self) -> bool:
        """Check if order book data is available (live mode)."""
        return self.orderbook_imbalance is not None
