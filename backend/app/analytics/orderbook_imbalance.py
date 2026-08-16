"""Real-time order-book imbalance calculation and mandatory live filter."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.analytics.trading_config import get_orderbook_imbalance_config


def calculate_volume_imbalance(
    bid_depth: Any,
    ask_depth: Any,
) -> Optional[float]:
    """Return bid depth / ask depth, or None when the book is unusable."""
    if bid_depth is None or ask_depth is None:
        return None
    try:
        bid_value = float(bid_depth)
        ask_value = float(ask_depth)
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(bid_value)
        or not math.isfinite(ask_value)
        or bid_value < 0
        or ask_value <= 0
    ):
        return None
    return bid_value / ask_value


def get_imbalance_threshold(strategy_config: Optional[Dict[str, Any]]) -> float:
    """Read and validate the strategy threshold (default: 1.0)."""
    defaults = get_orderbook_imbalance_config()
    raw_value = (strategy_config or {}).get(
        "imbalance_threshold",
        defaults["default_threshold"],
    )
    try:
        threshold = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("imbalance_threshold must be a finite non-negative number") from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("imbalance_threshold must be a finite non-negative number")
    return threshold


def passes_imbalance_filter(
    volume_imbalance: Any,
    strategy_config: Optional[Dict[str, Any]],
) -> bool:
    """Return True only for valid order-book data above the strategy threshold."""
    if volume_imbalance is None:
        return False
    try:
        value = float(volume_imbalance)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(value) or value < 0:
        return False
    return value > get_imbalance_threshold(strategy_config)


def get_recent_imbalance(
    db: Any,
    ticker: str,
    *,
    minutes: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Recalculate imbalance from the latest fresh aggregate's bid/ask depth."""
    policy = get_orderbook_imbalance_config()
    max_age_minutes = (
        int(minutes) if minutes is not None else int(policy["max_age_minutes"])
    )
    if max_age_minutes <= 0:
        raise ValueError("minutes must be positive")

    current_time = now or datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=3))
    )
    cutoff = current_time.replace(tzinfo=None) - timedelta(minutes=max_age_minutes)
    df = db.select(
        "SELECT bid_depth, ask_depth "
        "FROM trading.online_orderbook_aggregates "
        "WHERE ticker=%s AND timestamp >= %s "
        "ORDER BY timestamp DESC LIMIT 1",
        (ticker, cutoff),
    ).to_dataframe()
    if df.empty:
        return None
    row = df.iloc[0]
    return calculate_volume_imbalance(row.get("bid_depth"), row.get("ask_depth"))
