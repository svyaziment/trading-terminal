# Epics History

> **Source:** summary over committed epics (new content, not a copy of monolith sections)
> **Last refreshed:** 2026-10-04, task-346
> **Status:** actual
> **Related:** `00-architecture/roadmap.md`, `03-strategies/strategy-lab.md`, `03-strategies/trailing-stop.md`, `analytics/`

## Overview

Historical view of the major work waves of the Trading Terminal (MOEX stock paper trading). Implementation details are not duplicated in this section — they live in the thematic sections of `docs/knowledge/` (00–08). Closed analytics packages are stored in `analytics/` and are referenced only.

## Epic #142 — staged trailing stop (closed)

- **Goal:** multi-step trailing stop for paper positions.
- **Deliverables:** trailing stop mechanics with levels (steps) and ratchet behaviour, configuration in `trading_config.py` (no hardcoded ticks), strategy UI in Strategy Lab.
- **Read:** `03-strategies/trailing-stop.md` (full mechanics, S/R levels), `07-operations/broker-ops.md` (operational side).
- **Status:** closed; trailing stop is a baseline risk instrument of the terminal.

## Epic #172 — production readiness (closed)

- **Goal:** bring the paper trading system to production-like state: schema, stops, risk gates.
- **Deliverables:** schema and migrations of trading tables (`strategies`, `live_positions`, `candles`, equity), risk gates, operational runbooks.
- **Read:** `02-database/schema.md`, `05-risk-management/risk-gates.md`, `07-operations/runbook.md`.
- **Status:** closed.

## Epic #190 — real trading infrastructure (in progress)

- **Goal:** infrastructure for the transition from the sandbox to real trading (epic #190).
- **Deliverables:** broker layer (T-Bank Invest API, gRPC, sandbox mode), order management, live execution.
- **Read:** `04-execution/live-executor.md`, `04-execution/broker-layer.md`, `07-operations/broker-ops.md`, `07-operations/live-telemetry.md`.
- **Status:** in progress. `allow_real_trading` is not enabled (project red line).

## Closed analytics packages

Historical analytics packages from closed issues (#139, #143, #155, etc.) are preserved in the `analytics/` directory. They are a historical fact and are not edited; from `docs/` they are referenced only.

## Rules

- Locked strategies (126 / 36 / 102 / 118) and closed analytics packages — read-only, references only.
- Epic status is maintained in `00-architecture/roadmap.md` (source of truth for the roadmap).
