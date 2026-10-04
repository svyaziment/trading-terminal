# API Endpoints

> **Source:** project-context.md sections 5
> **Last refreshed:** 2026-10-04, task-346

## 5. API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Health check |
| GET | /api/candles | Candles (ticker, timeframe, limit) |
| GET | /api/instruments | Instruments list |
| GET | /api/top-stocks-by-volume | Top 30 by volume |
| GET | /api/signals | Signals (ticker, timeframe, limit, filters, pagination) |
| GET | /api/signals/stats | Signal statistics |
| POST | /api/data/refresh | Background: fetch + aggregate + indicators + signals (shared lock) |
| POST | /api/signals/regenerate | Background: regenerate signals (shared lock) |
| GET | /api/jobs/status | All jobs status |
| POST | /api/backtest/run | Background: legacy pattern matrix backtest |
| POST | /api/levels-backtest/run | Levels backtest matrix |
| GET | /api/patterns | Pattern registry schemas (Strategy Lab) |
| GET | /api/strategies/trailing-schema | `config.trailing_stop` contract for the Lab editor: defaults, bounds, `max_steps`, input resolution, approved grid name, reason codes (#146; read-only, produced by `trading_config.get_trailing_stop_schema()`) |
| POST | /api/patterns/preview | Pattern chart preview: candles + typed overlays (`ray`, `band`, `line`, `marker`); #88 implements `levels_reversal` |
| POST | /api/strategies | Save strategy (rejects overwrite of locked; #149: validates `trailing_stop` → 422 with `reason_codes`) |
| GET | /api/strategies | List strategies (with in_paper_test/locked/description; #149: + `trailing_stop` metadata) |
| GET | /api/strategies/run/status | Strategy backtest job status |
| GET | /api/strategies/data-range | Min/max date of candles_1min_raw (for date pickers) |
| POST | /api/strategies/{id}/run | Run backtest (full_sample/walkforward, depth or custom date_from/date_to) |
| GET | /api/strategies/{id}/results | Backtest results (per-ticker metrics) |
| GET | /api/tickers/big | Tickers with >= N 1min candles (selectable universe) |
| GET | /api/paper-trading/overview | Strategy name + factor options + summary stats (factor filters; #149: + `trailing_closed`, `trailing_closed_pnl_rub`, `trailing_open`, `active_stop_count`) |
| GET | /api/paper-trading/positions | Positions list (filters + pagination + sort); open rows include current price and unrealized PnL; #149: + `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`; `status=closed` includes `closed_trailing` |
| GET | /api/paper-trading/dynamics | Cumulative realized PnL series by 1h/1d/1w (factor/ticker/date filters) |
| GET | /api/notifications/status | Cached Telegram configuration and Bot API connectivity status |
| GET | /api/live-trading/positions | Sandbox live positions with current price, PnL, filters, sorting, and pagination; #149: + `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`; `status=closed` includes `closed_trailing` |
| GET | /api/live-trading/dynamics | Cumulative realized sandbox PnL by 1h/1d/1w |
| GET | /api/live-trading/equity/current | Latest `live_equity` snapshot + `risk_breach_active` + the effective limits and their validated bounds, so the panel never hardcodes a number (#176) |
| GET | /api/live-trading/equity/latest | Alias of `/equity/current` (#176) |
| GET | /api/live-trading/equity/history | Live equity curve, newest first; filters `session_key`, `date_from`, `date_to`; pagination (#176). 503 with an `alembic upgrade head` hint when the table is missing |
| GET | /api/live-trading/metrics | `LiveExecutor` metrics snapshot from `trading.app_settings['live_executor_metrics']`: `state` (unknown/kill_switch/no_heartbeat/stale/error_threshold/risk_breach/running), snapshot and heartbeat age, loop/protection/risk/alerting counters, kill switch from the live row, open positions with and without broker protection (#177). Degrades to `available=false` + `reason` instead of a 500 |
| POST | /api/live-trading/kill-switch | Global emergency stop of the live contour (#178). Body `{"enabled": bool, "reason"?: str<=200}`; upserts `trading.app_settings.live_kill_switch`, confirms by reading it back (`ok`/`confirmed`) and answers `503` naming migration `20260928_001` when the table is unwritable. Entries are rejected with reason `kill_switch`; open positions keep their broker stops |

Shared lock: jobs_state.py (in-process). Only one heavy job runs at a time; others return 409.
