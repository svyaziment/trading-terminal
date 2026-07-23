"""
Base classes for signal patterns.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd


@dataclass
class MarketContext:
    """
    Minimal market context passed to every pattern.
    """

    timeframe: str
    ticker: str = ""
    figi: str = ""


class BasePattern:
    """
    Base class for all trading patterns.
    """

    name: str = "BasePattern"
    category: str = "Base"

    allowed_trend_regimes = []
    allowed_vol_regimes = []
    timeframe_thresholds: Dict[str, Dict[str, Any]] = {}

    def get_thresholds(self, timeframe: str) -> Dict[str, Any]:
        """
        Return thresholds for a timeframe.

        Patterns may define `timeframe_thresholds` as:

            {
                "30min": {...},
                "1h": {...},
            }

        If a timeframe is missing, an empty dict is returned.
        """

        thresholds = getattr(self, "timeframe_thresholds", {}) or {}
        return dict(thresholds.get(timeframe, {}))

    def evaluate(
        self,
        current_row: pd.Series,
        history: pd.DataFrame,
        context: MarketContext,
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluate pattern on the current candle.

        Contract:
        - current_row: current candle/indicator row.
        - history: candles up to and including current_row.
        - context: timeframe/ticker/figi context.

        Expected return:
            {
                "direction": "BUY" | "SELL",
                "strength": float,
                "reason": str,
            }

        or None if no signal.
        """

        raise NotImplementedError
