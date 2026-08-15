# Issue #36: Apply new params and restart trading processes

**Date:** 2026-08-15
**Executor:** Arctic (Backend Dev)
**Status:** Completed

## Changes Applied (Operational)

### 1. Strategy config updated (`trading.strategies`, id=36, name=`test_20260731`)

Added parameters:
| Parameter | Value | Purpose |
|---|---|---|
| `reentry_cooldown_min` | 30 | Cooldown (minutes) before re-emitting signal for same ticker after position close |
| `max_rr_ratio` | 10.0 | Reject signals with RR ratio above this threshold |
| `imbalance_threshold` | 1.0 | Minimum orderbook volume_imbalance required for signal emission |

### 2. Tables truncated

- `trading.alerts` (72 rows removed)
- `trading.paper_positions` (704 rows removed)
- `trading.paper_equity` (2855 rows removed)

### 3. Backend rebuilt

`docker compose up -d --build backend` — image `trading-terminal-backend:latest` rebuilt and healthy.

### 4. Processes restarted

All 4 background processes restarted via `stop_processes.sh` + `start_processes.sh`:
- `run_data_refresher` (1 instance)
- `run_online_data` (1 instance)
- `run_live_engine` (1 instance)
- `run_paper_trader` (1 instance)

Duration: 1200 min.

## Verification

- Live engine startup confirmed: `reports/live-engine/live.log`
- Paper trader startup confirmed: `reports/paper-trader/trader.log`
- Strategy: `test_20260731`, 28 tickers, imbalance_overlay=True

## Related Issues

- Issue #35: Added `max_rr_ratio` and `imbalance_threshold` filters to `live_engine.py`
- Issue #34: Added `reentry_cooldown_min` logic to `_can_emit_signal`
