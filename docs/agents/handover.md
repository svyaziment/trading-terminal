# Agent Handover Guide: Trading Terminal

> **Status:** stub — migrated to `docs/knowledge/`.
> Moved to `docs/knowledge/` (see [docs/knowledge/index.md](../knowledge/index.md) — the single entry point).
> The original text is preserved in the `.bak` files below.
> **Last refreshed:** 2026-10-04, task-346

Use the table below to find where each former section now lives. It lists all **61** migrated sections — `pc §1`–`pc §24` from this monolith and `ho §10`–`ho §46` from [handover.md](handover.md) (`ho §1`–`ho §9` were stubs duplicating `pc §1`–`pc §9` and are omitted). `pc` = project-context, `ho` = handover.

## Section navigation — 61 sections

| Old section | Old title | New location |
|---|---|---|
| pc §1 | Project Overview | [`overview.md`](../knowledge/00-architecture/overview.md) |
| pc §2 | File Structure | [`file-structure.md`](../knowledge/00-architecture/file-structure.md) |
| pc §3 | Database Schema (PostgreSQL, schema: trading) | [`schema.md`](../knowledge/02-database/schema.md) |
| pc §4 | Data Pipeline | [`pipeline.md`](../knowledge/01-data-pipeline/pipeline.md) |
| pc §5 | API Endpoints | [`api-endpoints.md`](../knowledge/00-architecture/api-endpoints.md) |
| pc §6 | Patterns (10 total) | [`pattern-features.md`](../knowledge/03-strategies/pattern-features.md) |
| pc §7 | Known Issues & Status | [`known-issues.md`](../knowledge/00-architecture/known-issues.md) |
| pc §8 | Roadmap Status | [`roadmap.md`](../knowledge/00-architecture/roadmap.md) |
| pc §9 | Important Notes | [`important-notes.md`](../knowledge/00-architecture/important-notes.md) |
| pc §10 | T-Bank Sandbox API Integration | [`broker-streaming.md`](../knowledge/01-data-pipeline/broker-streaming.md) |
| pc §11 | Real-time Order-book Imbalance | [`moex-session.md`](../knowledge/07-operations/moex-session.md) |
| pc §12 | Position Sizing | [`equity-rules.md`](../knowledge/05-risk-management/equity-rules.md) |
| pc §13 | Sandbox Live Executor | [`live-executor.md`](../knowledge/04-execution/live-executor.md) |
| pc §14 | Telegram Alerting | [`alerts.md`](../knowledge/07-operations/alerts.md) |
| pc §15 | Live Trading Monitoring Panel | [`dashboard.md`](../knowledge/06-frontend/dashboard.md) |
| pc §16 | SignalEngine AND-filters in StrategyEvaluator | [`strategy-lab.md`](../knowledge/03-strategies/strategy-lab.md) |
| pc §17 | Stepped trailing-stop analytics (Issue #139) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| pc §18 | Trailing-grid robustness analytics (Issue #143, lattice shape by Issue #155) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| pc §19 | Stepped trailing stop in production (Issue #145, Epic #142) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| pc §20 | Trailing-stop editor in the Strategy Lab (Issue #146, Epic #142) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| pc §21 | Live trailing stop (Issue #151) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| pc §22 | Live equity and risk gates (Issue #176, Epic #172 task D) | [`risk-gates.md`](../knowledge/05-risk-management/risk-gates.md) |
| pc §23 | Live Telegram alerting and executor metrics (Issue #177, Epic #172 task E) | [`live-telemetry.md`](../knowledge/07-operations/live-telemetry.md) |
| pc §24 | The broker layer: sandbox and the real contour (Issue #178, Epic #172 block F) | [`broker-layer.md`](../knowledge/04-execution/broker-layer.md) |
| ho §10 | Operational Gotchas | [`runbook.md`](../knowledge/07-operations/runbook.md) |
| ho §11 | Collaboration Protocol (agents) | [`runbook.md`](../knowledge/07-operations/runbook.md) |
| ho §12 | Operating the Order-book Imbalance Filter | [`moex-session.md`](../knowledge/07-operations/moex-session.md) |
| ho §13 | Operating the T-Bank Sandbox Client | [`broker-ops.md`](../knowledge/07-operations/broker-ops.md) |
| ho §14 | Operating Position Sizing | [`equity-rules.md`](../knowledge/05-risk-management/equity-rules.md) |
| ho §15 | Operating the Sandbox Live Executor | [`live-executor.md`](../knowledge/04-execution/live-executor.md) |
| ho §16 | Operating Telegram Paper Alerts | [`alerts.md`](../knowledge/07-operations/alerts.md) |
| ho §17 | Operating the Live Trading Panel | [`dashboard.md`](../knowledge/06-frontend/dashboard.md) |
| ho §18 | Operating the Live Trading Universe | [`trading-universe.md`](../knowledge/03-strategies/trading-universe.md) |
| ho §19 | First Sandbox LiveExecutor Canary | [`canary.md`](../knowledge/08-decisions/canary.md) |
| ho §20 | Operating SignalEngine Strategy Lab filters | [`strategy-lab.md`](../knowledge/03-strategies/strategy-lab.md) |
| ho §21 | Operating Pattern Chart Preview (Epic #87) | [`pattern-features.md`](../knowledge/03-strategies/pattern-features.md) |
| ho §22 | Operating the Levels State Machine | [`pattern-features.md`](../knowledge/03-strategies/pattern-features.md) |
| ho §23 | Operating the Level Breakout Retest Pattern | [`pattern-features.md`](../knowledge/03-strategies/pattern-features.md) |
| ho §24 | Operating the Level Breakout Retest Lab chip | [`strategy-lab-ui.md`](../knowledge/03-strategies/strategy-lab-ui.md) |
| ho §25 | Operating the Composite S/R Pattern (`levels_sr_breakout`) | [`candle-pipeline.md`](../knowledge/01-data-pipeline/candle-pipeline.md) |
| ho §26 | Operating the Composite S/R Lab chip | [`strategy-lab-ui.md`](../knowledge/03-strategies/strategy-lab-ui.md) |
| ho §27 | Operating the AFKS composite smoke | [`pattern-features.md`](../knowledge/03-strategies/pattern-features.md) |
| ho §28 | Operating the Lab-universe composite A/B | [`pattern-features.md`](../knowledge/03-strategies/pattern-features.md) |
| ho §29 | Operating the Support-with-tracker Pattern (`levels_sr_support`) | [`pattern-features.md`](../knowledge/03-strategies/pattern-features.md) |
| ho §30 | Operating the Support-with-tracker Lab chip | [`strategy-lab-ui.md`](../knowledge/03-strategies/strategy-lab-ui.md) |
| ho §31 | Operating the isolated support-with-tracker universe | [`trading-universe.md`](../knowledge/03-strategies/trading-universe.md) |
| ho §32 | Operating the support-with-tracker portfolio | [`trading-universe.md`](../knowledge/03-strategies/trading-universe.md) |
| ho §33 | Operating sandbox LiveExecutor on `test_20260830_new_level` (Issue #135) | [`live-executor.md`](../knowledge/04-execution/live-executor.md) |
| ho §34 | Stepped trailing-stop analytics on `test_20260830_new_level` (Issue #139) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| ho §35 | Trailing-grid robustness analytics on `test_20260830_new_level` (Issue #143, lattice v3 by #155) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| ho §36 | Operating the trailing-stop configuration contract (Issue #144) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| ho §37 | Operating the production trailing stop (Issue #145) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| ho §38 | Operating the Lab trailing-stop editor (Issue #146) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| ho §39 | Trailing stop API integration (Issue #149) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| ho §40 | Trailing metrics in Paper / Live panels (Issue #150) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| ho §41 | Operating live trailing stop (Issue #151) | [`trailing-stop.md`](../knowledge/03-strategies/trailing-stop.md) |
| ho §42 | live_positions schema contract and fail-fast preflight (Issue #173) | [`schema.md`](../knowledge/02-database/schema.md) |
| ho §43 | LiveExecutor resilience (Issue #174) | [`live-executor.md`](../knowledge/04-execution/live-executor.md) |
| ho §44 | Operating the live equity risk gates (Issue #176) | [`risk-gates.md`](../knowledge/05-risk-management/risk-gates.md) |
| ho §45 | Operating live Telegram alerting and monitoring (Issue #177) | [`live-telemetry.md`](../knowledge/07-operations/live-telemetry.md) |
| ho §46 | The real T-Bank contour, deploy migrations and the global kill switch (Issue #178) | [`broker-layer.md`](../knowledge/04-execution/broker-layer.md) |

---

## Full backups (safety nets)
- Original EN: [`project-context.md.bak`](project-context.md.bak)
- Original RU: [`project-context.ru.md.bak`](project-context.ru.md.bak)
- Handover EN: [`handover.md.bak`](handover.md.bak)
- Handover RU: [`handover.ru.md.bak`](handover.ru.md.bak)

## Entry point
- Index (single entry): [`docs/knowledge/index.md`](../knowledge/index.md)
