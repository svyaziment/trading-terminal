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

<!-- issue-220-risk-equity-split -->

## Issue #220 addendum: risk equity vs reporting equity (2026-11-10)

`_compute_live_equity()` now returns two equity views:

- `equity_rub` / `market_value_rub`: reporting equity for all priced broker holdings.
- `risk_equity_rub` / `risk_market_value_rub`: drawdown-gate equity for holdings inside the terminal's traded universe only.

`_write_live_equity()` persists reporting `equity_rub`, but computes `peak_equity_rub`, `drawdown_pct`, and `risk_breach` from `risk_equity_rub`.

Therefore an external holding without a usable price cannot manufacture a phantom daily breach. Unpriced holdings inside the traded universe are published in `unpriced_in_universe_tickers` and logged as data-quality warnings; they are excluded from risk equity rather than guessed at.

The traded universe used by the risk gate is primarily `self.instruments`, falling back to `get_live_trading_universe(self.db)` when the executor has not populated instruments. This keeps the gate consistent with `process_signal()`.

Deployment note: historical `peak_equity_rub` may have been computed under the old total-equity model. Prefer deploying after the MSK session close or before the next trading day, so the daily peak restarts under the new risk-equity model. If deploying inside an active session, operator-controlled peak/breach reset is required.

Red lines remain unchanged: a breach blocks new entries only, existing broker stops are preserved, there is no forced flatten, and `max_daily_loss_pct` is not changed by this fix.
