# Decisions & Change Log (EN)

> **Section:** 08-decisions — changelog of knowledge base changes.
> **Last refreshed:** 2026-10-29, task-216

| Date | Task | Section | Change | Author |
|------|------|---------|--------|--------|
| 2026-10-29 | #216 | 02-database/schema.md, 04-execution/live-executor.md, 07-operations/runbook.md | Real trading transition: single real-money executor section (process separation, two-phase kill-switch read, `PostgresLogHandler` → `trading.live_executor_events`), rewritten runbook "Live Trading Operations", new table `trading.live_executor_events` in schema | Arctic |
