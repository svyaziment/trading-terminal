# Trading Terminal — Knowledge Index

> **Last refreshed:** 2026-10-04, task-346
> **Status:** actual
> **Source:** docs/ (26 EN+RU pairs, `docs/agents/` monoliths → `docs/knowledge/`)

## 30-second map

The **Trading Terminal** is a paper trading system for MOEX stocks (no real trading).
**Stack:** FastAPI (Python 3.12), React, PostgreSQL. **Data:** T-Bank Invest API (gRPC, sandbox) + MOEX ISS API (REST, 1-min candles).
**Three pillars:** backtesting / Strategy Lab, paper trading, live execution in sandbox.

## Quick navigation

| Question | File |
|---|---|
| How is the system built? | `00-architecture/overview.md` |
| Project file structure / stack | `00-architecture/file-structure.md` |
| API endpoints | `00-architecture/api-endpoints.md` |
| Known issues and pitfalls | `00-architecture/known-issues.md` |
| What is planned? | `00-architecture/roadmap.md` |
| Operational notes (important) | `00-architecture/important-notes.md` |
| Candle flow (MOEX ISS) | `01-data-pipeline/candle-pipeline.md` |
| Overall data pipeline | `01-data-pipeline/pipeline.md` |
| Broker streaming (T-Bank) | `01-data-pipeline/broker-streaming.md` |
| Database schema and migrations | `02-database/schema.md` |
| Pattern features (signals) | `03-strategies/pattern-features.md` |
| Strategy Lab | `03-strategies/strategy-lab.md` |
| Strategy Lab UI | `03-strategies/strategy-lab-ui.md` |
| Trading universe (ticking) | `03-strategies/trading-universe.md` |
| Trailing stop (stages, ratchet, S/R) | `03-strategies/trailing-stop.md` |
| Live executor | `04-execution/live-executor.md` |
| Broker layer | `04-execution/broker-layer.md` |
| Risk gates | `05-risk-management/risk-gates.md` |
| Equity rules / sizing | `05-risk-management/equity-rules.md` |
| Secrets policy | `05-risk-management/secrets-policy.md` |
| Frontend dashboards | `06-frontend/dashboard.md` |
| Runbook (start / stop / check) | `07-operations/runbook.md` |
| MOEX trading session | `07-operations/moex-session.md` |
| Alerts | `07-operations/alerts.md` |
| Live telemetry | `07-operations/live-telemetry.md` |
| Broker operations | `07-operations/broker-ops.md` |
| Canary decisions (ADR) | `08-decisions/canary.md` |
| Epics #142 / #172 / #190, closed analytics | `09-epics-history/epics-history.md` |

## Sections

| # | Section | What is inside |
|---|---|---|
| 00 | architecture | overview, stack, file map, API, known issues, roadmap |
| 01 | data-pipeline | candles, MOEX ISS, broker streaming, overall pipeline |
| 02 | database | trading schema, migrations |
| 03 | strategies | pattern signals, Strategy Lab, trading universe, trailing stop |
| 04 | execution | live executor, broker layer |
| 05 | risk-management | risk gates, equity rules, secrets |
| 06 | frontend | dashboards and panels |
| 07 | operations | runbooks, MOEX session, alerts, telemetry, broker ops |
| 08 | decisions | ADR, canary |
| 09 | epics-history | #142 / #172 / #190, closed analytics packages |

## SOP and rules

- **SOP:** `.clinerules/developer-sop.ru.md` — project rules of the game for all agents.
- **Documentation policy:** `docs/agents/documentation-policy.md`.
- **Red lines:** locked strategies (126 / 36 / 102 / 118) and closed `analytics/` — reference only; `allow_real_trading` is never enabled; EN+RU pairs are updated together.
- **`reports/`** — working documents, not committed (epic/issue folders are kept locally).

## Cross-reference from old paths

- `docs/agents/project-context.md` → stub (moved to `docs/knowledge/`, see table inside the stub).
- `docs/agents/handover.md` → stub (moved to `docs/knowledge/`, see table inside the stub).
- `docs/knowledge/glossary.md` / `CHANGELOG.md` — terms and history of changes.

## Related

- `docs/knowledge/09-epics-history/epics-history.md` — historical wave of work.
- `workspace/context/docs-tree-before.txt` — snapshot of the tree before the migration.
