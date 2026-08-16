"""Risk- and concentration-aware position sizing for live trading."""

from __future__ import annotations

import math
from typing import Optional, TypedDict

from app.analytics.trading_config import get_position_sizing_config


class PositionSizeResult(TypedDict):
    """Stable result contract for order executors and diagnostics."""

    size_lots: int
    size_rub: float
    risk_rub: float
    reason: str


def calculate_position_size(
    capital_rub: float,
    stop_distance_pct: float,
    price: float,
    lot_size: int,
    *,
    risk_per_trade_pct: Optional[float] = None,
    max_position_pct: Optional[float] = None,
) -> PositionSizeResult:
    """Calculate a whole-lot position under risk and concentration limits.

    ``size_rub`` is the budget selected by the hybrid formula before lot
    rounding. ``size_lots`` is the executable quantity in instrument lots.
    """
    config = get_position_sizing_config()
    risk_pct = (
        config["risk_per_trade_pct"]
        if risk_per_trade_pct is None
        else risk_per_trade_pct
    )
    position_pct = (
        config["max_position_pct"]
        if max_position_pct is None
        else max_position_pct
    )

    values = {
        "capital_rub": capital_rub,
        "stop_distance_pct": stop_distance_pct,
        "price": price,
        "lot_size": lot_size,
        "risk_per_trade_pct": risk_pct,
        "max_position_pct": position_pct,
    }
    for name, value in values.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")

    if price <= 0:
        raise ValueError("price must be greater than zero")
    if isinstance(lot_size, bool) or not isinstance(lot_size, int) or lot_size <= 0:
        raise ValueError("lot_size must be a positive integer")
    if risk_pct < 0:
        raise ValueError("risk_per_trade_pct cannot be negative")
    if position_pct < 0:
        raise ValueError("max_position_pct cannot be negative")

    risk_rub = float(capital_rub * risk_pct / 100)
    rejected = {
        "size_lots": 0,
        "size_rub": 0.0,
        "risk_rub": risk_rub,
    }

    if stop_distance_pct <= 0:
        return {**rejected, "reason": "invalid_stop"}

    lot_cost = price * lot_size
    if capital_rub < lot_cost:
        return {**rejected, "reason": "insufficient_capital"}

    size_by_risk = risk_rub / (stop_distance_pct / 100)
    size_by_cap = capital_rub * position_pct / 100
    size_rub = float(min(size_by_risk, size_by_cap))
    reason = "risk" if size_by_risk <= size_by_cap else "concentration"

    size_lots = math.floor(size_rub / lot_cost)
    if size_lots < 1:
        size_lots = 1
        reason = "min_lot"

    return {
        "size_lots": size_lots,
        "size_rub": size_rub,
        "risk_rub": risk_rub,
        "reason": reason,
    }
