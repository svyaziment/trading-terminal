# Position Sizing

> **Source:** project-context.md sections 12 + handover.md sections 14
> **Last refreshed:** 2026-10-04, task-346

## 12. Position Sizing

`backend/app/analytics/position_sizer.py` provides the shared `calculate_position_size()` function for live order sizing. It first calculates the capital budget implied by the configured per-trade risk and stop distance, then caps that budget by the maximum allowed portfolio concentration:

`size_rub = min(capital_rub * risk_per_trade_pct / stop_distance_pct, capital_rub * max_position_pct / 100)`

The executable quantity is the whole number of instrument lots that fit the budget: `floor(size_rub / (price * lot_size))`. If the budget is below one lot but free capital can still pay for one lot, the result is raised to one lot with reason `min_lot`. A non-positive stop distance returns `invalid_stop`; capital below one full lot returns `insufficient_capital`.

Default limits are centralized in `trading_config.py` (`POSITION_SIZING`): 1% risk per trade and 20% maximum position concentration. The result also reports whether risk, concentration, or minimum-lot handling determined the final size.

## 14. Operating Position Sizing

- Entry point: `app.analytics.position_sizer.calculate_position_size`. Pass free capital, stop distance as a percent of entry, entry price, and the instrument's `lot_size`.
- Live defaults come only from `POSITION_SIZING` in `trading_config.py`: 1% risk per trade and 20% maximum concentration. Optional function overrides are intended for tests and simulations.
- Use `size_lots` as the broker order quantity. `size_rub` is the pre-rounding budget, not a fractional-lot instruction.
- `invalid_stop` and `insufficient_capital` are rejection results (`size_lots == 0`) and must not reach the broker. `min_lot` is executable because the calculator has already confirmed that free capital covers one full lot.
- Unit test: `cd backend && python -m pytest -q tests/test_position_sizer.py`.
