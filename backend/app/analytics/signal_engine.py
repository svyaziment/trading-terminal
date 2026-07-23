"""
SignalEngine.

Applies a list of patterns to an indicator DataFrame.
"""

import logging
from typing import Any, Dict, List

import pandas as pd

from app.analytics.patterns.base import MarketContext

logger = logging.getLogger(__name__)


class SignalEngine:
    """
    Applies patterns to each candle in a DataFrame.
    """

    def __init__(self, patterns: List[Any]):
        self.patterns = patterns

    def process_dataframe(
        self,
        df: pd.DataFrame,
        timeframe: str,
        lookback_window: int,
    ) -> List[Dict[str, Any]]:
        """
        Apply all patterns to every candle.

        Returns a list of results:

            {
                "candle": dict,
                "triggered_patterns": [
                    {
                        "name": ...,
                        "direction": "BUY" | "SELL",
                        "strength": float,
                        "reason": str,
                    }
                ],
                "summary": {
                    "buy_signals": int,
                    "sell_signals": int,
                    "total_patterns": int,
                },
            }
        """

        results: List[Dict[str, Any]] = []

        for idx in range(len(df)):
            current_row = df.iloc[idx]
            candle = current_row.to_dict()

            candle["timestamp"] = pd.to_datetime(candle.get("timestamp"))

            close_value = candle.get("close", candle.get("price"))
            try:
                candle["price"] = float(close_value) if close_value is not None else None
            except Exception:
                candle["price"] = None

            # History includes the current candle.
            # Most patterns expect history.iloc[-1] to be current,
            # and history.iloc[-2] to be previous.
            history = df.iloc[: idx + 1]

            context = MarketContext(
                timeframe=str(timeframe),
                ticker=str(candle.get("ticker", "")),
                figi=str(candle.get("figi", "")),
            )

            triggered: List[Dict[str, Any]] = []

            for pattern in self.patterns:
                pattern_name = getattr(pattern, "name", pattern.__class__.__name__)

                try:
                    if hasattr(pattern, "evaluate"):
                        signal = pattern.evaluate(current_row, history, context)
                    elif hasattr(pattern, "check"):
                        # Backward compatibility only.
                        signal = pattern.check(candle, history, lookback_window)
                    else:
                        signal = None
                except Exception as exc:
                    logger.debug("Pattern %s failed: %s", pattern_name, exc)
                    signal = None

                if not signal:
                    continue

                signal = dict(signal)
                signal.setdefault("name", pattern_name)
                signal["direction"] = str(signal.get("direction", "")).upper()

                if signal["direction"] not in {"BUY", "SELL"}:
                    continue

                triggered.append(signal)

            summary = {
                "buy_signals": sum(1 for s in triggered if s.get("direction") == "BUY"),
                "sell_signals": sum(1 for s in triggered if s.get("direction") == "SELL"),
                "total_patterns": len(triggered),
            }

            results.append(
                {
                    "candle": candle,
                    "triggered_patterns": triggered,
                    "summary": summary,
                }
            )

        return results
