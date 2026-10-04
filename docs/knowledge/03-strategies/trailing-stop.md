# Stepped trailing-stop analytics (Issue #139)

> **Source:** project-context.md sections 17, 18, 19, 20, 21 + handover.md sections 34, 35, 36, 37, 38, 39, 40, 41
> **Last refreshed:** 2026-10-04, task-346

## 17. Stepped trailing-stop analytics (Issue #139)

`analytics/issue-139-trailing-stop-new-level/` is an analytics-only A/B in the 50k /
10k / max-5 / volume-priority / GAME OVER portfolio simulator: baseline fixed stop/take
1:3 (**A**) vs the same entry with a stepped trailing stop (**B**) on locked
`test_20260830_new_level` (id=126, RR 1:3, SHA `dfc855195ade…`). The single difference is
the exit rule; entries, initial 1R risk, commission 0.06%, slippage 0 and the 28-name
`run_params.tickers` universe are identical. Full period `2024-08-01` … `timestamp <
2026-08-21` (not the express window).

The trailing is a separate exit mode in `trailing.py` (`DEFAULT_STEPS`, a configurable list
of `{"trigger": <R>, "stop": <R>}`; default +2R→+1.5R, +2.5R→+2R). Steps are in R measured
from the entry, never % hardcoded. The take is unchanged; trailing only tightens the stop on
the late phase, so a trailing exit is never later than the baseline exit. The fill model
mirrors `StrategyEvaluator.on_bar` (stop before take within a bar; a step armed by the
current bar's high only affects the next bar — no intra-bar look-ahead). This module is NOT
wired into `StrategyEvaluator`, `portfolio_simulator.py`, paper or the sandbox path.

`extract_inputs.py` re-runs the unified brain over 1min candles (identical entries to the
production backtest), evaluates BOTH exits per trade on the same intra-trade path, and
reports `baseline_replay_mismatches` (must be 0). It only READS the DB (candles, 4h levels,
signals), writes no trades, verifies the four protected rows (126/36/102/118) before and
after, and is resumable via a per-ticker cache under
`reports/Vulpec/139_trailing-stop-new-level/cache/`. Price paths stay in memory;
`results.json` keeps only compact per-trade A and B outcomes, so `analysis.py` performs the
slot replay + comparison + `summary.json` + `report.md` (RU+EN) with NO database. Exact A/B
figures and the recommendation live in `summary.json` / `report.md`. Note: #129/#130 used RR
1:2 (`3b7864c4…`); #139 re-extracts for RR 1:3 — the #129 candidate book is not reused.

## 18. Trailing-grid robustness analytics (Issue #143, lattice shape by Issue #155)

`analytics/issue-143-trailing-robustness/` replays the #139 book (§17) off-engine over the 8-grid
stepped trailing lattice of schema `143-trailing-v3` (grid shape delivered by Issue #155) — `ref139`
(`+2R→+1.5R, +2.5R→+2R`, the #139 consensus), the ladders `two_step_aggressive`, `three_step_steady`,
`tight_after_take` and `late_conservative`, the stakeholder probe `ultra_late_tight` (a step 0.1R above
the lattice's latest trigger, 3R) and two single-step grids, `single_step_2_15` and `breakeven_2_0`
(stop parked at 0R) — on the same 28 tickers, locked
config id=126, 3 305 candidate trades, capital 50 000 RUB, slot 10 000 RUB, max 5 positions.
Entries come from `StrategyEvaluator` and the published 1m paths; the exit is
`analytics/issue-139-trailing-stop-new-level/trailing.apply_trailing`, so a grid delta is
attributable to the grid alone.

The shape of the lattice is machine-readable, not just prose: `summary.json.lattice` carries `groups`
(by number of steps), `pairs` (a single-step grid against the ladder that shares its first step),
`boundaries` (PO probe / single-step / break-even), `verdicts`, plus `po_grid_id`, `po_gap_r`,
`po_trigger_r` and two thresholds — `material_rub_per_trade` (50 RUB, the lattice-wide flip threshold
of #143) and `po_material_rub_per_trade` (20 RUB, the stakeholder threshold of #155 that judges the
probe grid). `backend/tests/test_issue155_analysis.py` pins the schema, the 8-grid shape, the RU report
wording and that slice.

Parity with #139 is contractual: the base grid reproduces equity 103 216.04 RUB against the
published 103 176.00 (tolerance ±206.35), PF 1.55 vs 1.54, daily MaxDD 2.72 vs 2.74 pp, with 0
exit-mechanic mismatches over 3 305 trades (max |Δ net_return| = 0.0969 pp — slot-player rounding,
not bit-for-bit).

Headline of the published run:
- Equity 95 827 … 110 434 RUB (spread 14 607 RUB — larger than the whole trailing effect measured in
  #139, +7 996 RUB); PF 1.49 … 1.60; daily MaxDD 2.42 … 4.70 pp; no GAME OVER. The best capital belongs
  to the PO probe `ultra_late_tight` (+7 218 RUB over the base grid, +1 865 RUB over the best ladder,
  ΔPnL per trade +2.0 RUB — inside the 20 RUB stakeholder threshold of #155), the worst to
  `three_step_steady`.
- Cost stress (commission 0.06 / 0.10 / 0.15 % × slippage 0 / 5 / 10 / 20 b.p., 96 runs = 8 grids × 12
  nodes): at the worst node every grid is loss-making and none reaches GAME OVER — equity drops to
  9 614 … 23 029 RUB, DD degrades by up to +79.81 pp. Costs move the book more than the choice of steps
  does.
- Walk-forward (9 three-month windows, ≥20 trades each): every grid profitable in 9/9 windows, base
  grid's worst window +745 RUB (`single_step_2_15` dips to +593 RUB) — the sign is stable, the level
  is not.
- One step vs a ladder (the shape #155 added): the only direct pair is `single_step_2_15` ↔ `ref139` —
  the added `2.5→2R` step buys +691 RUB of equity for −0.35 pp of DD and +0.8 score points; by group,
  the best single-step grid reaches 102 525 RUB against 110 434 RUB for the best ladder. The break-even
  bound `breakeven_2_0` (stop at 0R) is the lower edge of trailing's usefulness: −5 596 RUB, DD 4.70 pp,
  win rate 20.0 % — a sensitivity bound by design, not a defect.
- Composite stability score (`summary.json.grids[].robustness_score_0_100`) is effectively driven by
  absolute DD and exit-reason stability: the stress-capital and DD-degradation components collapse to
  0 for every grid in this run and walk-forward pays the same 15 to all eight (9/9 profitable windows),
  so the spread is narrow — `ref139` 47.3, `single_step_2_15` 46.5, `ultra_late_tight` 46.3,
  `two_step_aggressive` 46.2, `three_step_steady` 46.0, `tight_after_take` 45.7, `late_conservative`
  44.3, `breakeven_2_0` 43.2. The score is a summary, not an objective; no grid was optimised under it.
- Exit concordance: 69.3 % of trades keep the same outcome across all eight grids (median pairwise
  Spearman ρ 0.89, minimum 0.7497). Exit reason flips against the base grid in 1 797 of 3 305 trades
  (1 726 material at the 20 RUB threshold; `trailing` becomes the new reason 915 times, `take` 663,
  `initial_stop` 148). Against `tight_after_take` as control — the stakeholder test «worst grid not
  materially below control» — `three_step_steady` is −525 RUB.

Grid-shape debt closed: Issue #143 asked for 8–12 grids and shipped 5 multi-step ones — Issue #155
delivered the v3 shape (8 grids: six ladders + two single-step, including the break-even `stop = 0R`
bound and the `ultra_late_tight` PO probe) and made the comparison machine-readable through
`summary.json.lattice`. Still open, recorded in `report.md` §13: stress milder than specified (no MOEX
price-step / min-lot sensitivity); no `risk_reward` 1:2 sensitivity; no fixed-stop-vs-trailing
threshold at max stress (book A was not stress-run); no charts; only one direct
single-step ↔ ladder pair, so the group comparison stays indirect. Choosing the production default grid was
a Product Owner decision in #144, not a result of this package — **that decision has now been taken** (see
below). No production code path was touched by this package — engine work is #145, live-path parity gate is
#147.

### Production default grid — Product Owner decision (2026-09-08)

The Product Owner approved **`ultra_late_tight`** — `+2.0R → +1.9R`, `+2.5R → +2.4R`, `+3.0R → +2.9R` — as the
default value of `config.trailing_stop.steps` in `trading_config.TRAILING_STOP` (#144). `config.trailing_stop.enabled`
stays `false`: picking a grid switches nothing on and rewrites no locked configuration (126 / 36 / 102 / 118).
**Landed (2026-09-08, #144):** `TRAILING_STOP` + the `normalize_trailing_stop()` /
`validate_trailing_steps()` / `resolve_trailing_stop()` / `require_valid_trailing_stop()` helpers in
`backend/app/analytics/trading_config.py` — contract only: no production path calls them (this repo has no
`validate_config()` to enforce them), so the engine, the plugin and portfolio simulators, paper and live still
ignore the block (#145 / #148 / #151), a malformed ladder is still accepted at save time until #146 / #149 gate
it, and a config without the key behaves exactly as before. Full field contract: §6; tests:
`backend/tests/test_trailing_contract.py` (33).

- Why it won: best capital of the lattice, 110 434 RUB (+7 218 over `ref139`, +1 865 over the best other ladder), PF 1.60,
  3 162 trades, win rate 42.6 %, the best average walk-forward profit factor of the lattice (1.62) with a +1 366 RUB
  worst-window floor against +745 for the base grid (`three_step_steady` floors higher, at +1 512), more equity than the
  base at every node of the cost-stress lattice (19 364 vs 16 708 RUB at the worst node, commission 0.15 % + 20 b.p.;
  DD degrades +64.45 vs +68.60 pp), and one of the smallest behavioural diffs in the lattice (141 exit-reason flips
  = 4.3 % against the base grid, Spearman ρ 0.9966 — only `two_step_aggressive` is closer, at 126 flips / 3.8 %) —
  the gain comes from the shape of the rule, not from one lucky trade (ΔPnL per trade +2.0 RUB).
- What was traded away: composite stability score 46.3 versus 47.3 for `ref139` and daily MaxDD 3.06 pp versus
  2.72 pp. The score is a summary of these eight grids on one book and one period, not an objective (report §4),
  and the equity, average walk-forward PF and stress-node gains were judged worth the +0.34 pp of drawdown.
- What stays unchanged: `ref139` (the #139 grid) remains the **parity anchor** — #147 injects it explicitly, it is
  not a production default — and the published artifacts of #139 and #143 are frozen evidence. #143 and #155 stay
  closed, with a pointer comment recording the decision.
- Residual risk owned by follow-ups: the step margin is **0.1R** (not 0.5R as in `ref139`), so slippage or a gap on
  the synthetic stop of `LiveExecutor` consumes a visible share of the locked profit. #151 owes a defensive price
  step and #152 owes the break-even slippage measured against 0.1R; the leave / tune / rollback verdict of #152 can
  move the default only back through the Product Owner.

### Production-path parity of the stepped trailing stop (Issue #147, epic #142 gate)

The package `analytics/issue-147-trailing-production-parity/` runs the production path
(`StrategyEvaluator` → `portfolio_simulator`, bash-la shard processes without
multiprocessing, per-ticker cache) on locked `test_20260830_new_level` (id=126, SHA
`dfc855195ade…`), period `2024-08-01` … `timestamp < 2026-08-21`, 28 tickers,
50 000 RUB / slot 10 000 RUB / max 5, and books three runs: A_prod (trailing off),
B_prod (the `ref139` grid injected explicitly), B_default (steps imported from
`trading_config.TRAILING_STOP` = `ultra_late_tight`). Gate verdict: A_prod=PASS
(equity 95179.91 vs 95180.01 RUB, n 2649, PF 1.41,
daily MaxDD 6.49 pp, exit reasons match, candidate-level entries
0/0); B_prod=FAIL
(equity 111458.96 RUB > A, PF 1.5, daily MaxDD 4.01 pp against
2.74 pp of the #139 book B — the 1.0 pp band is not met: structural live-loop feedback,
an early trailing exit frees the ticker and adds +789 candidates
against the overlay's 3 305 with 15 missing; exit mechanics
are bit-for-bit per #145 `grid_check`, 0/3305 on both grids); B_default=PASS
(equity 120753.7 RUB, PF 1.56, daily MaxDD 3.96 pp against the
published 3.06 pp of #143 `ultra_late_tight`, split stop>trailing>take).
OVERALL=FAIL: the formal gate fails on the book-B daily MaxDD band only; the B/D criteria
were restated in `run.md` v3 BEFORE the run, tolerances were not fitted to the result, and
the verdict is escalated for TL/PO sign-off (draft comment in the package `report.md`).
Protected rows 126/36/102/118 untouched; nothing written to `paper_positions` /
`backtest_results`.

## 19. Stepped trailing stop in production (Issue #145, Epic #142)

`backend/app/analytics/trailing_stop.py` is the only ladder. Every contour that closes a
position calls it, so the fill convention cannot drift between the single-ticker backtest, the
Lab plugin replay, the portfolio simulator and walk-forward — and the same module is what #148
(paper) and #151 (sandbox live) will reuse.

- **Pure step function.** `ladder_stop(entry_exec, initial_stop, steps, ref_high)` returns
  `(stop_price, step_reached)`: the highest rung whose trigger `entry_exec + trigger × R` the
  high-water mark `ref_high` has reached, with `R = entry_exec - initial_stop`. Prices are
  derived from the entry and the initial risk — no percentages are restated anywhere in the
  engine. `TrailingState.evaluate(high, low, bar_key)` turns that into a per-bar decision, and
  `evaluate_bar(state, ...)` is the single call sites use.
- **Bar order is the contract.** (1) `low <= live_stop` → exit at `live_stop`, reason
  `trailing` once the ladder has raised the stop, plain `stop` while it has not; (2) otherwise
  `high >= take` → exit at the take; (3) only if neither fired, arm the next rung from **this**
  bar's high, effective from the **next** bar. The take never moves, the stop never lowers and
  never sits below the initial stop. This is #139's convention
  (`analytics/issue-139-trailing-stop-new-level/trailing.py`) reproduced deliberately: diverging
  from it fails the #147 parity gate.
- **Bar-keyed arming.** The evaluator keys on `idx`, the plugin keys on `context.timestamp`.
  Feeding the same bar twice is a no-op, which is what keeps the documented plugin lifecycle
  (`manage_position` before `check_exit`) free of intra-bar look-ahead.
- **Entry geometry lives on the position.** `position['initial_stop']`, `['risk_r']`,
  `['trailing_steps']`, `['stop']` / `['current_stop']` and `['step_reached']` mirror the ladder.
  `stop` / `step_reached` are the pair that becomes effective on the next bar;
  `position['trailing_state'].live_stop` is what the exit check of the current bar used.
  `step_reached` reaches the trade record and the portfolio trade, and the state is created per
  position — a walk-forward window opens its own evaluator, so nothing is remembered between
  windows.
- **Off by default, fail-safe when wrong.** `trailing_from_config()` returns None — "manage this
  position exactly as before #145" — when the key is absent, `enabled=false`, the ladder is
  empty, or `resolve_trailing_stop()['reasons']` is non-empty. The engine does not raise:
  refusing a malformed ladder is the write path's job (#146 Lab editor, #149 API), and until
  those land a bad block changes no exits and only logs a warning.
- **Reason names.** Production emits `stop` / `take` / `trailing`; the #139/#143 analytics books
  used `initial_stop` / `take` / `trailing`. The mapping is spelled out in
  `reports/Arctic/145_trailing-evaluator/grid_check.py` (`EXIT_REASON_MAP`) and #147 owns the
  formal gate.
- **Metrics.** `_portfolio_metrics` keeps every existing key and adds `exit_reason_counts`,
  `trailing_exits`, `take_exits`, `initial_stop_exits` and `trailing_exit_share_pct`.
- **Reproduce it.** Behaviour matrix: `cd backend && python -m pytest -q
  tests/test_trailing_stop.py tests/test_trailing_contract.py`. Bit-for-bit regression against
  the locked configs (read-only, needs the warehouse):
  `python reports/Arctic/145_trailing-evaluator/regression_run.py --label after --out after.json`
  on this branch; the same script with `--label baseline --backend <origin-main-worktree>/backend
  --out baseline.json` on `main`; then
  `python reports/Arctic/145_trailing-evaluator/regression_run.py --compare baseline.json after.json
  --verdict regression_verdict.json` → `regression_match: true`. Directed ladder check against the
  published #143 book (offline, path caches only):
  `python reports/Arctic/145_trailing-evaluator/grid_check.py`.
- **Grid choice is unchanged by this issue:** the production default stays `ultra_late_tight`
  (§18, handover §35), and `ref139` is not a default — it is the parity anchor #147 injects
  explicitly.
- **Out of scope for #145:** paper (#148), the Lab/API write gate (#146 / #149), the Paper/Live
  panels (#150), sandbox live (#151), the parity gate (#147) and the live-period acceptance
  verdict (#152).

## 20. Trailing-stop editor in the Strategy Lab (Issue #146, Epic #142)

- The Lab now writes `config.trailing_stop` itself: a toggle and a `trigger → stop` rung table in
  the config rail (between «Risk / Reward» and «Тест»). It is a **top-level exit block**; making it
  a `levels_reversal` parameter would break both the schema-driver and the #144 contract.
- Fully schema-driven, with no frontend fallback. The section appears only once
  `GET /api/strategies/trailing-schema` answers, and every default, bound, `max_steps`, the `0.1R`
  input resolution, the approved grid's **name and its steps**, and the reason-code vocabulary come
  from that payload — produced by `trading_config.get_trailing_stop_schema()` straight off
  `TRAILING_STOP`, which it does not mutate. If the endpoint is down the section is simply absent,
  so a stale bundle can never offer an unapproved ladder.
  #149 owns the endpoint's long-term shape and **inherits this one** — extend the function, do not
  fork a second schema object into a router.
- `frontend/src/trailingStop.ts` holds all the logic (parse, `validateLadder`, payload building) as
  pure functions; `TrailingStopFields.tsx` renders and contains no trailing number — that split is
  what makes the «zero hardcode in TSX» acceptance criterion checkable. `validateLadder` mirrors
  `validate_trailing_steps()` (#144) rung for rung, including exclusive `min_trigger`, collapsing
  exact duplicates, and checking bounds even while the toggle is off.
- Two behaviours worth knowing before editing:
  - **Untouched means untouched.** The payload builder returns `null` until the operator touches the
    block and the config memo spreads it conditionally, so a strategy that never carried
    `trailing_stop` still saves without the key. Touching it and saving with the toggle off *does*
    write an explicit `enabled: false` — an opt-out is an opinion, absence is not.
  - **The ladder must be controlled string state at `step=0.1`.** The approved `ultra_late_tight`
    grid is `2.0→1.9 / 2.5→2.4 / 3.0→2.9` on a 0.1R gap; a browser snap to 0.5R would silently turn
    1.9 into 2.0 and the Lab would ship a ladder #144 rejects. Round-trip is covered by tests.
- The trade table now renders the engine's full closed exit-reason set
  (`stop, take, trailing, holding, signal, session`) from `frontend/src/exitReasons.ts`. Before
  #146 the column was a binary take/stop, so every `trailing` exit was labelled «стоп» — the
  ladder's own output was indistinguishable from being stopped out at the initial stop.
- **Still not gated:** saving over the API does not call `require_valid_trailing_stop()`; the 422
  write-path validation is #149's deliverable, so the client check can be bypassed by a non-Lab
  client until then. The #145 engine remains the safety net (it never arms a rejected ladder).
- **`config_hash` does not exist in this repository** — requirement 4's hash-change check is
  unverifiable here; the saved JSONB `config` genuinely carries the block, which is the substance
  of it. Operational detail: handover §38.

## 21. Live trailing stop (Issue #151)

Completed 2026-09-16. Live/sandbox trailing-stop with ratchet, restart safety, and kill-switch.

**Architecture**:
- **Arming**: On position open (`process_signal`), if `strategy_config.trailing_stop.enabled=true` and kill switch is OFF, the executor arms trailing with 0 broker calls. Parameters: `trailing_enabled=true`, `trailing_steps` (JSON), `risk_r=entry-stop`, `current_stop_price=stop_price`, `step_reached=0`.
- **Ratchet**: `monitor_positions()` calls `_apply_trailing(row, current_price)` for each active position. Rebuilds `TrailingState` from DB columns, evaluates current price as single bar, conditional UPDATE `WHERE id=%s AND step_reached < %s` ensures monotonicity.
- **Exit**: When `current_price <= current_stop_price`, cancels take, submits sell-limit at current price. Records `exit_price_model` (the stop price), `exit_price_actual` (fill price), `slippage_bp`, `slippage_r`, `lots_executed`. Status `closed_trailing`, reason `trailing`.
- **Kill switch**: `_refresh_kill_switch()` reads `trading.app_settings.trailing_kill_switch` before each monitoring cycle. ON pauses arming/ratchet; armed positions keep state. Fail-safe: DB error → kill switch ON.
- **Advisory lock**: `pg_try_advisory_lock(151001)` in `initialize()`, released in `shutdown()`. Prevents multiple executor instances.
- **Restart safety**: State restored from DB columns; no in-memory state. Idempotent replay.

**Database**:
- Migration `20260916_001_live_trailing_runtime.py`: extends `live_positions.status` CHECK to include `closed_trailing`, `closed_broker`; adds `exit_price_model`, `exit_price_actual`, `slippage_bp`, `slippage_r`, `lots_executed`; creates `trading.app_settings` table.
- Columns `trailing_enabled`, `trailing_steps`, `risk_r`, `current_stop_price`, `step_reached` already exist (migration `20260915_002`).

**Configuration**:
- `LIVE_TRADING.trailing_kill_switch`: default `false`, runtime override via `trading.app_settings`.
- `LIVE_TRADING.live_trailing_enabled`: default `true`, global toggle.
- `LIVE_TRADING.trailing_protective_ticks`: default `5`, protective offset for stop execution.
- `LIVE_TRADING.trailing_ticker_allowlist`: default `[]`, optional ticker filter.

**Testing**: 68 tests in `test_live_executor.py` (43 new for #151). Coverage: arming, ratchet, monotonicity, kill switch, execution facts, slippage, rate limiter, config validation.

**Operational**: See `docs/strategy/live-trading.md` for user-facing docs, handover §41 for operations.

## 34. Stepped trailing-stop analytics on `test_20260830_new_level` (Issue #139)

- Analytics-only A/B: baseline fixed stop/take 1:3 (**A**) vs the same entry with a
  stepped trailing stop (**B**) in the portfolio simulator (50k / 10k / max 5 / volume
  priority / GAME OVER). Single difference = the exit rule; entry, initial 1R risk,
  commission and the 28-name `run_params.tickers` universe are identical. Full period
  `2024-08-01` … `timestamp < 2026-08-21` (not the express window).
- Package: `analytics/issue-139-trailing-stop-new-level/`. The trailing lives ONLY in
  `trailing.py` as a configurable stepped exit (list of `{"trigger": <R>, "stop": <R>}`,
  default `+2R→+1.5R`, `+2.5R→+2R`). Never wire it into `StrategyEvaluator`,
  `portfolio_simulator.py`, paper or sandbox. Steps are in R from the entry, not % hardcode.
- Fill model mirrors `StrategyEvaluator.on_bar`: within a bar check stop (`low<=stop`)
  before take (`high>=take`); a step armed by the current bar's high raises the stop for
  the NEXT bar only (no intra-bar look-ahead). A trailing exit is never later than the
  baseline exit (the take is unchanged and the ratchet only tightens the stop).
- `extract_inputs.py` drives the unified brain over 1min candles (identical entries to the
  production backtest) and evaluates BOTH exits on the same intra-trade path; it never
  writes trades to the DB and checks the four protected rows (126 / 36 / 102 / 118) before
  and after. `baseline_replay_mismatches` must be 0 (proves the replayed A equals the engine).
  Paths stay in memory; `results.json` stores only the compact per-trade A and B outcomes, so
  `analysis.py` runs without a DB.
- Note #129 / #130 used RR 1:2 (SHA `3b7864c4…aedb1b`); #139 targets the locked
  `test_20260830_new_level` id=126 (RR 1:3, SHA `dfc855195ade…`). Do not reuse the #129
  candidate book; #139 re-extracts from the DB.
- Run: `python analytics/issue-139-trailing-stop-new-level/extract_inputs.py --workers 4`
  then `python analytics/issue-139-trailing-stop-new-level/analysis.py`. Extract is
  resumable (per-ticker cache under `reports/Vulpec/139_trailing-stop-new-level/cache/`).
- Verdict and exact A/B numbers live in `summary.json` / `report.md` (RU+EN). Headline:
  B trailing 103,176.00 RUB vs A baseline 95,180.01 RUB (Δ +7,995.99 RUB, +8.40%); PF
  1.41→1.54, daily Max DD 6.49%→2.74%, win rate 24.2%→42.3%, no GAME OVER in either book;
  trailing closes 36.9% of B (take 6.6%, initial stop 56.5%), candidate trades 3305 of which
  1578 reached +2R, `baseline_replay_mismatches=0`. Verdict: trailing improves capital →
  consider adopting (analytics only; the production exit path is untouched, not a paper lock).
- Units (no DB): `cd backend && python -m pytest -q tests/test_issue139_analysis.py`.

## 35. Trailing-grid robustness analytics on `test_20260830_new_level` (Issue #143, lattice v3 by #155)

- Robustness lattice over the #139 book: same 28 tickers, config id=126, `StrategyEvaluator`,
  slot simulator and `apply_trailing`; only the stepped-trailing grids and the stress factors
  (commission, slippage) change. Simulation (paper mode) — no parity with the engine trailing
  path and no `trailing_grid_id` column yet.
- Package: `analytics/issue-143-trailing-robustness/` — `run.py`, `grids.json`, `README.md` plus
  the published run artifacts (`report.md`, `run.md`, `summary.json`, `report.json.gz`,
  `grids.csv`, `walkforward.csv`, `contract.json`, `exits.jsonl.gz`, `extract_summary.json`).
  Artifacts live next to the code (the `analytics/` convention); `.gitignore` excludes
  `analytics/*/cache/` (28 gzipped 1m paths, fully re-extractable) and `analytics/*/out_*/`
  (debug runs). Run logs go to `reports/Vulpec/143_trailing-robustness/` (ignored), as the
  issue text requires.
- Run from the repo root: `python analytics/issue-143-trailing-robustness/run.py --stage all`
  (extract → analyze → report, ≈2 min on a warm cache). Text-only report refresh without DB:
  `--stage report`. Debug slice: `--tickers SBER --limit 20 --out-dir out_debug` — keep
  `--out-dir` at the repo root; pointing it inside the package leaves untracked duplicates
  (`analytics/issue-143-*/reports/run.md`) that must not be committed.
- Headline of the published run (`grids.json` schema `143-trailing-v3`): 8 grids — six multi-step
  (2–4 steps, incl. the PO probe `ultra_late_tight`) and two single-step (`single_step_2_15`,
  break-even `breakeven_2_0`) — over the same 3 305 candidate trades; parity with #139 confirmed
  (0 exit-mechanic mismatches; equity 103 216 ₽ vs 103 176 ₽ in #139, PF 1.55, DD 2.72 pp).
  Equity spread — 14 607 ₽ (`ultra_late_tight` 110 434 ₽ best, `three_step_steady` 95 827 ₽ worst);
  `ref139` tops the composite stability score (47.3/100) and all eight grids stay profitable in 9/9
  walk-forward windows. Max stress (0.15 % commission + 20 b.p. slippage, 96 runs) drops the worst
  grid to 9 614 ₽ with no game-over; worst grid against the control (stakeholder test) is
  `three_step_steady` (Δequity −525 ₽). Exit concordance: identical outcome for 69.3 % of trades
  (median pairwise Spearman ρ 0.89, min 0.7497), exit reason flips vs the base grid in 1 797 of
  3 305 trades (1 726 material at the 20 ₽ threshold). See report §4–§8.
- Machine-readable lattice slice (Issue #155): `summary.json.lattice` — `groups` by step count,
  `pairs` (single step ↔ the ladder sharing its first step), `boundaries` (PO probe / single-step /
  break-even), `verdicts`, and both thresholds: `material_rub_per_trade` (50 RUB, #143 lattice-wide)
  vs `po_material_rub_per_trade` (20 RUB, the #155 stakeholder threshold that judges the probe).
  Validation is tracked, not scratch: `cd backend && python -m pytest -q
  tests/test_issue155_analysis.py` (canonical artifacts + synthetic `run.lattice_analysis`).
- Reproducibility: `--stage report` rebuilds `report.md`, `summary.json` and `run.md` from the committed
  `report.json.gz` alone — verified in a clean worktree (no `cache/`, no DB): `report.md` and
  `summary.json` come out byte-identical, `report.json.gz` matches on every value (only the gzip header
  timestamp differs), and `run.md` differs only as a protocol log (timestamp, stage, elapsed). Full
  `--stage all` of the published v3 run: exit 0 — 231.9 s of analysis (`summary.json.elapsed_sec`).
- Documented limits (report §13, keep them honest): the grid-shape debt is closed by #155 (8 grids,
  single-step and break-even bounds included); still open — milder stress than specified (0.06/0.10/0.15 %
  commission and b.p. slippage instead of 0.3/0.6/1.5 % and MOEX price steps, no min-lot sensitivity),
  no `risk_reward` sensitivity, no fixed-stop-vs-trailing threshold at max stress (book A was never
  stress-run), only one direct single-step ↔ ladder pair, and no charts. These stay in the report's
  continuation block — do not silently extend the lattice inside this package.
- Verdict: analytics only. Do not read the composite robustness score as an objective: in this run its
  stress-capital, DD-degradation and walk-forward components tie every grid (0.0 / 0.0 / 15.0 for all
  eight), so only absolute drawdown and exit-reason stability discriminate (report §4). The default grid
  and the `ultra_late_tight` probe were Product Owner decisions (#144) — **the first one has been taken**,
  see the next bullet; engine work is #145, live-path parity is #147, sandbox #151, acceptance #152.
  Mirrored in `project-context.md` §18 and roadmap block W (§8).
- **Product Owner decision, 2026-09-08: `ultra_late_tight` is the production default grid.**
  `config.trailing_stop.steps` defaults to `2.0→1.9`, `2.5→2.4`, `3.0→2.9` in `trading_config.TRAILING_STOP`
  (#144), and `config.trailing_stop.enabled` stays `false` — the choice switches nothing on and touches no
  locked configuration (126 / 36 / 102 / 118). Why this grid: 110 434 ₽ against 103 216 ₽ for `ref139`
  (+7 218 ₽), PF 1.60 vs 1.55, 3 162 vs 3 118 trades, the best average walk-forward PF of the lattice (1.62) with a
  +1 366 ₽ worst-window floor against +745 ₽ (`three_step_steady` floors higher, at +1 512 ₽), more equity than the base
  at every cost-stress node (19 364 ₽ vs 16 708 ₽ at the worst node, commission 0.15 % + 20 b.p.; DD degrades
  +64.45 vs +68.60 pp), and one of the smallest behavioural diffs in the lattice (141 exit-reason flips = 4.3 %,
  Spearman ρ 0.9966 — only `two_step_aggressive` is closer, at 126 flips / 3.8 %). Accepted trade-off:
  composite stability score 46.3 vs 47.3 for `ref139` and daily MaxDD 3.06 vs 2.72 pp — a deliberate swap,
  because the score is a summary of this lattice and not an objective (report §4).
- **What the decision does not change.** `ref139` (the #139 grid) remains the parity anchor: #147 injects it
  explicitly, and it stays out of the production defaults; the frozen evidence in
  `analytics/issue-139-trailing-stop-new-level/` and `analytics/issue-143-trailing-robustness/` is untouched,
  and #143 / #155 stay closed with a pointer comment. Residual risk: the step margin is **0.1R**, so slippage or
  a gap on a synthetic market order eats a visible share of the locked profit — #151 owes a defensive price
  step and #152 owes the break-even slippage measured against 0.1R, on which the leave / tune / rollback
  verdict is built. Decision text is recorded in the bodies of #142 (Decision section) and #144 (§1–§2).

### Production-path parity (Issue #147, epic #142 gate)

The package `analytics/issue-147-trailing-production-parity/` runs the production path
(`StrategyEvaluator` → `portfolio_simulator`, bash-launched shard processes without
multiprocessing, per-ticker cache) on locked `test_20260830_new_level` (id=126, SHA
`dfc855195ade…`), period `2024-08-01` … `timestamp < 2026-08-21`, 28 tickers, 50 000 RUB /
slot 10 000 RUB / max 5, and books three runs: A_prod (trailing off), B_prod (the `ref139`
grid injected explicitly), B_default (steps from `trading_config.TRAILING_STOP` =
`ultra_late_tight`). Gate verdict: A_prod=PASS (tight parity with the #139 book A: equity
95179.91 vs 95180.01 RUB, n 2649=2649, PF 1.41, WR 24.2%, daily MaxDD 6.49=6.49 pp, exit
reasons match, candidate-level entries 0/0); B_prod=FAIL on the daily MaxDD band only
(4.01 vs 2.74 pp, Δ1.27 > the 1.0 pp band declared before the run in run.md v3):
structural live-loop feedback — an early trailing exit frees the ticker and the engine takes
new entries (+789/−15 candidates against the overlay's 3305), which the #139/#143 overlay
holds fixed by construction; exit mechanics are bit-for-bit per #145 grid_check (0/3305 on
both grids); B_default=PASS on the directional criteria against the published
`ultra_late_tight` #143 (equity 120753.7 RUB vs 110433.68, PF 1.56 vs 1.60, n 3794 vs 3162,
daily MaxDD 3.96 vs 3.06 pp, split stop>trailing>take). OVERALL=FAIL: the B_prod
criterion verdict is escalated for TL/PO sign-off (draft comment in the package report.md);
tolerances were not fitted post-hoc. Protected rows 126/36/102/118 untouched; nothing written
to `paper_positions` / `backtest_results`.

## 36. Operating the trailing-stop configuration contract (Issue #144)

- `config.trailing_stop` is now a first-class key of `strategies.config` (JSONB, no schema
  migration): `{"enabled": bool, "steps": [{"trigger": 2.0, "stop": 1.9}, ...]}` — both numbers are R
  multiples measured from the entry. **There is no `take_partial` in this contract**, and no
  `trigger_r` / `lock_r` naming: a partial take was never part of the approved `ultra_late_tight` grid,
  so #144 does not invent one.
- The contract lives in `backend/app/analytics/trading_config.py`: `TRAILING_STOP` (defaults + bounds),
  `get_trailing_stop_config()`, `normalize_trailing_stop()`, `validate_trailing_steps()`,
  `resolve_trailing_stop()` and `require_valid_trailing_stop()`. `validate_trailing_steps()` never raises
  and never mutates its input — it returns the sorted, de-duplicated set of **stable reason codes**
  (`trailing_disabled`, `trailing_step_invalid`, `trailing_not_monotonic`, `trailing_too_many_steps`);
  an empty list means "accepted". Renaming a code is a contract break: #149 surfaces these strings
  verbatim, and `require_valid_trailing_stop()` is the only helper that turns them into `ValueError`.
- Bounds are read from `TRAILING_STOP` and are the single source of truth (engine, API and frontend must
  never restate them): `0 < trigger <= 3.5` (`min_trigger` is exclusive, so a step at 0R is not a step),
  `0 <= stop <= 3.0`, `stop < trigger` always, at most `max_steps` = 6 steps. A step must be a **dict
  with finite numeric `trigger` and `stop`**: `bool` is not a number, a list of `[trigger, stop]` pairs
  is *not* accepted, and no other key spelling works as an alias. Stops may not fall as triggers rise
  (`trailing_not_monotonic`), the same trigger twice with two different stops is
  `trailing_not_monotonic`, and an exact duplicate pair is dropped by normalization rather than
  rejected. There is deliberately **no minimum gap** between a trigger and its stop — the production
  ladder lives on 0.1R, so any "gap ≥ 0.5R" heuristic would reject the shipped default.
- `resolve_trailing_stop(config)` is the one call for consumers: `{"enabled", "steps", "reasons"}`. An
  absent key, `None`, `{}` or a non-dict block resolve to `{"enabled": false, "steps": [], "reasons": []}`,
  which is why configs without the key behave exactly as before #144. `enabled` is strict — only a real
  bool or `'1' / 'true' / 'yes' / 'on'` arm the ladder, any other truthy value normalizes to `false`.
  `normalize_trailing_stop()` is idempotent, keeps float precision (1.9 / 2.4 / 2.9 are never rounded to
  0.5R), sorts by `(trigger, stop)` and drops structurally broken steps: it is a canonicalizer, **not**
  the gatekeeper, so always validate the raw list.
- Shipped default `TRAILING_STOP`: `enabled=false`, steps `2.0→1.9 / 2.5→2.4 / 3.0→2.9` — the
  `ultra_late_tight` grid of §35 — with bound defaults `max_steps=6`, `min_trigger=0.0` (exclusive),
  `max_trigger=3.5`, `min_stop=0.0`, `max_stop=3.0`. Editing the ladder switches nothing on by itself.
- **Applied since #145 — still no write-path gate.** `validate_config()` **does not exist in this
  repository**, so nothing refuses a malformed ladder on strategy create/update or on a Lab run: an
  `enabled=true` block with no usable steps still saves and simply reports `trailing_disabled`.
  `require_valid_trailing_stop()` remains the gate #149 is expected to call — #146 shipped the Lab
  editor without it (§38): the editor validates client-side with these same codes, but POSTing a
  malformed ladder still succeeds. What changed in
  #145 is the read path: `app.analytics.trailing_stop` resolves the block and
  `StrategyEvaluator.on_bar`, the `levels_reversal` plugin, `portfolio_backtest` /
  `portfolio_simulator` and walk-forward arm the ladder from it, and `EXIT_TRAILING` is now emitted by
  the production engine (see §37). The engine fails safe — a ladder the validator refuses is never
  armed — so backtest, Lab-run and portfolio results may be described as trailing-stop-enabled only
  when the config carries a valid block with `enabled=true`; paper (`paper_trader`) and sandbox
  (`live_executor`) still ignore the block until #148 / #151.
- Legacy: the pattern-level `trailing_stop` / `trailing_step` parameters in `pattern_registry.py` are a
  different contract that #144 does not touch — do not confuse them with `config.trailing_stop`.
- Tests: `cd backend && python -m pytest -q tests/test_trailing_contract.py tests/test_trading_config.py`
  (39 together: 33 contract + 6 config). The full issue acceptance set — those two plus
  `test_levels_sr_support.py`, `test_resistance_zone_veto.py`, `test_strategy_plugin.py`,
  `test_pattern_registry.py`, `test_issue139_analysis.py` and `test_issue155_analysis.py` — stands at
  **99 passed** as of 2026-09-08; `--collect-only` still guards the Lab API import.

## 37. Operating the production trailing stop (Issue #145)

- One ladder, `backend/app/analytics/trailing_stop.py`, is the only exit-ratchet in the repo:
  `StrategyEvaluator.on_bar` (single-ticker backtest), `LevelsReversalStrategy.check_exit` /
  `manage_position` (the plugin mirror), `portfolio_backtest` → `portfolio_simulator` and
  `run_walkforward` all call `evaluate_bar(...)`. Do not re-implement the ratchet anywhere else: the
  plugin is a mirror of the brain, and a second copy is exactly how #41 parity broke before.
- Fill convention inside a managed bar: stop check → take check → arm. A rung armed by bar *i*
  applies from bar *i+1*; the entry bar never arms anything (matching #139, whose `path` excludes
  the entry bar). Stop beats take when both are reachable in one bar. Reasons: `stop` (ladder never
  moved the stop — the baseline case), `trailing` (raised stop hit), `take`.
- Turning it on is a config decision, not a code change: add
  `{"trailing_stop": {"enabled": true, "steps": [...]}}` to a strategy's `strategies.config`
  (R multiples from the entry; the shipped default ladder is `ultra_late_tight`, §35). Locked
  configs 126 / 36 / 102 / 118 are untouched — none of them carry the block, and #145 is no
  reason to edit them.
- Tests: `cd backend && python -m pytest -q tests/test_trailing_contract.py
  tests/test_trailing_stop.py`.

## 38. Operating the Lab trailing-stop editor (Issue #146)

- The Lab now edits `config.trailing_stop`: a toggle plus a `trigger → stop` rung table, in the
  config rail between «Risk / Reward» and «Тест». It is a **top-level exit block, not a pattern
  parameter** — do not move it into `PATTERN_REGISTRY['levels_reversal']`; that would break both
  the schema-driver and the #144 contract.
- Schema-driven, honestly: the section renders only after `GET /api/strategies/trailing-schema`
  answers, and reads defaults, `max_steps`, all four bounds, the `0.1R` input resolution, the
  approved grid's **name and values** and the reason-code vocabulary from that payload.
  `trading_config.get_trailing_stop_schema()` is the producer; `TRAILING_STOP` stays the single
  source of truth and is **not** mutated by it (the two new keys, `default_grid` and `input_step`,
  are labels added on the way out). There is no fallback ladder in the frontend: if the endpoint
  is unreachable the section is absent, so a stale bundle can never offer a ladder nobody approved.
  - #146 req. 1 said to *request* this endpoint rather than build it, and #149 owns its
    long-term shape («endpoint схемы … для schema-driven UI #146»). It landed here so #146 is
    demoable end-to-end; **#149 inherits it — extend `get_trailing_stop_schema()`, do not fork a
    second schema object into the router.**
- Files: `frontend/src/trailingStop.ts` (all logic — parse, validate, payload; pure, no React),
  `frontend/src/components/TrailingStopFields.tsx` (renderer, zero trailing numbers),
  `frontend/src/exitReasons.ts` (exit-reason labels/tones), wired in `StrategyLab.tsx`.
  The issue named `pages/strategies/StrategyConfigPanel.tsx`, `lib/api.ts`, a `strategiesApi`
  object, `components/ui/{Button,Input,Select}` and `lucide-react` — **none of those exist in this
  repo**; the equivalents are `components/StrategyLab.tsx`, `src/api.ts` free functions, the
  Lab-local `Section`/`numInput` primitives and inline SVG (`PatternIcon` precedent). Follow the
  repo, not the issue's paths, when touching this.
- `validateLadder()` mirrors `validate_trailing_steps()` rung for rung — exclusive `min_trigger`,
  inclusive other bounds, `stop < trigger`, sort-then-check, exact duplicates collapsed silently,
  two stops on one trigger and falling stops both `trailing_not_monotonic`, bounds enforced even
  when the toggle is **off**. `src/trailingStop.test.ts` pins the two languages to the same
  published `grids.json`. It is a pre-flight, not a gate: see the caveat below.
- Untouched configs stay untouched (the requirement most likely to regress). `buildTrailingStopPayload()`
  returns `null` unless the operator touched the block, and the `config` memo **spreads** it
  conditionally, so a strategy that never carried `trailing_stop` saves without the key. Loading a
  stored strategy sets `touched=false`; any edit flips it once, permanently. Note the deliberate
  asymmetry: touching the block and saving with the toggle off *does* write an explicit
  `enabled:false` — an opt-out is an opinion, absence is not.
- Production default renders correctly: `ultra_late_tight` is three rungs on a 0.1R gap
  (`2.0→1.9, 2.5→2.4, 3.0→2.9`), so `step={input_step}` must stay at 0.1 and the cells must be
  **controlled string state** — an uncontrolled input or a `step="0.5"` snaps 1.9 to 2.0 in the
  browser and the frontend then ships a ladder #144 rejects. Round-trip covered by tests.
- Exit reasons: the trade table used to render a binary take/stop, which silently labelled every
  `trailing` exit «стоп» — the ladder's own output was indistinguishable from being stopped out.
  `EXIT_REASON_ORDER` now covers the closed set (`stop, take, trailing, holding, signal, session`)
  with `trailing` in sky, distinct from the red of `stop`. When #147 or the engine adds a reason,
  extend that array; unknown values fall through to a neutral chip rather than disappearing.
- **Caveat / known gap: #146 does not gate the write path.** Saving a malformed ladder still
  succeeds over the API, because the POST handler does not call `require_valid_trailing_stop()` —
  that gate is #149's deliverable («валидация общей функцией #144; отказ = 422»). Until it lands,
  the client-side check can be bypassed by any non-Lab client. The #145 engine remains the safety
  net: it refuses to arm a ladder the validator rejects, so a bad config changes no exits.
- **`config_hash` does not exist in this repository** (zero hits outside issue text). Requirement
  4's «hash must change when trailing is enabled» is therefore unverifiable here; it is satisfied
  only in the trivial sense that the saved `config` JSONB genuinely carries the block. If a hash is
  wanted, it needs its own issue.
- Tests: `cd backend && python -m pytest -q tests/test_trailing_schema_endpoint.py
  tests/test_trailing_contract.py tests/test_trailing_stop.py` and
  `cd frontend && npx tsc --noEmit && npx vitest run`.
- Not done here, and not accidentally shippable: no screenshots (EN/RU) and no
  `docker compose up -d --build frontend` — this was verified against a live uvicorn instance and
  a production `vite build`, not a browser. #150 (Paper/Live panels) still has to surface trailing
  state to the operator.

## 39. Trailing stop API integration (Issue #149)

- **Write gate**: `POST /api/strategies` now calls `require_valid_trailing_stop()` before
  saving. A bad ladder returns `422` with body `{"detail": {"message": ...,
  "reason_codes": [...]}}` — stable string codes from `TRAILING_REASON_CODES`
  (`trailing_step_invalid`, `trailing_not_monotonic`, `trailing_too_many_steps`).
  The client-side check from #146 is no longer the only line of defence.
- **List metadata**: `GET /api/strategies` attaches `trailing_stop` to every item:
  `{"enabled": bool, "steps": [...], "reasons": [...]}` — the output of
  `resolve_trailing_stop()`. The UI sees validity without re-running the validator.
- **Backtest results**: `_run_job` in `strategy_jobs.py` stores `exit_reasons` in
  `backtest_results.metrics.exit_reasons` — a breakdown of closes by reason
  (`stop`/`take`/`trailing`/etc.).
- **Paper API**:
  - `GET /api/paper-trading/overview` — four new fields in `summary`: `trailing_closed`,
    `trailing_closed_pnl_rub`, `trailing_open`, `active_stop_count`.
  - `GET /api/paper-trading/positions` — SELECT includes `trailing_enabled`,
    `current_stop_price`, `step_reached`, `risk_r`.
  - `status=closed` filter already included `closed_trailing` (from #148); no regression.
- **Live API**:
  - `GET /api/live-trading/positions` — SELECT includes the same trailing fields.
  - `_build_where` extended: `status=closed` now includes `closed_trailing`.
  - `dynamics` counts wins as `closed_take OR (closed_trailing AND pnl_rub > 0)` —
    matching the paper convention.
- **Migration**: `20260915_002_live_trailing.py` — same columns as #148, but for
  `trading.live_positions`. Idempotent (`ADD COLUMN IF NOT EXISTS` + backfill).
- **Tests**: `cd backend && python -m pytest -q tests/test_trailing_api.py
  tests/test_trailing_contract.py tests/test_trailing_schema_endpoint.py
  tests/test_trailing_stop.py` — green.
- **Caveat (resolved)**: the 4 red tests in `test_paper_trailing_stop.py` that initially
  looked like #149 regressions were a **stale Docker image** artefact — the container had been
  built before `#148-fix` (`c93f39d`) landed, so `monitor_open()` inside the image still lacked
  the trailing-wiring fix. After `docker compose build backend && docker compose up -d backend`
  the host code is current and the whole paper-trailing suite (`test_trailing_api.py`,
  `test_trailing_contract.py`, `test_trailing_schema_endpoint.py`, `test_trailing_stop.py`,
  `test_paper_trailing_stop.py`) is green. 126 / 36 / 102 / 118 stay untouched — the block is
  not present there, and #145 must not be used as a reason to edit them. Lab editing is #146,
  API validation is #149.
- Reading a run: backtest trades carry `step_reached` only when a ladder was armed (no key = the
  pre-#145 shape); `portfolio_simulator.metrics` now carries `exit_reason_counts`,
  `trailing_exits`, `take_exits`, `initial_stop_exits`, `trailing_exit_share_pct`. A ladder that
  fires earlier also releases a slot earlier, so `n_trades` rises and `skipped_entries_no_slot`
  falls — that is the mechanism behind #139's 2 649 → 3 118 and #143's 3 162, not a bug.
- Reproduce the acceptance evidence (this is the `critical`-task regression, SOP red line #3):
  ```
  # after (this branch)
  python reports/Arctic/145_trailing-evaluator/regression_run.py --label after --out after.json
  # baseline (main, e.g. a scratch worktree: git worktree add <tmp>/wt origin/main)
  python reports/Arctic/145_trailing-evaluator/regression_run.py --label baseline \
      --backend <tmp>/wt/backend --out baseline.json
  python reports/Arctic/145_trailing-evaluator/regression_run.py \
      --compare baseline.json after.json --verdict regression_verdict.json   # regression_match: true
  # the ladder against the published #143 book (no DB, path caches only)
  python reports/Arctic/145_trailing-evaluator/grid_check.py
  ```
  Both scripts are **read-only** (`SELECT` on `trading.strategies` / `trading.candles_1min_raw`);
  nothing in #145 writes trades, results or strategy rows. From the host, point the warehouse at
  `--db-host 127.0.0.1` (the `.env` default `postgres` resolves only inside the compose network).
- Parity with the analytics books is deliberately *not* claimed here: #147 owns that gate. What
  #145 shows is `grid_check.json` — the production ladder reproduces #143's per-trade exits on both
  `ref139` and `ultra_late_tight` with 0 real mismatches over 3 305 trades, and replays the book to
  the published figures once #143's 4-decimal rounding convention is re-applied.
- Gotchas: `Position` (plugin dataclass) grew `initial_stop` / `step_reached` / `trailing` fields —
  always pass them by keyword; the evaluator keeps its ladder in `position['trailing_state']`, and
  `position['stop']` is the *next-bar* stop while `trailing_state.live_stop` is what the current bar
  was checked against. Metrics going into `backtest_results` still pass through `_json_safe` (#116).

## 40. Trailing metrics in Paper / Live panels (Issue #150)

### What was done
- **Backend**: `_build_where` in `paper_trading_jobs.py` and `live_trading_jobs.py` now accepts `exit_reason` (query filter on `exit_reason` in positions). Endpoints `GET /api/paper-trading/positions` and `GET /api/live-trading/positions` accept `?exit_reason=...`.
- **Backend**: `PaperOverview.summary` already includes `trailing_closed`, `trailing_closed_pnl_rub`, `trailing_open`, `active_stop_count` (added in #149).
- **Frontend i18n**: new module `frontend/src/i18n/config.ts` — single source of `AppLocale` (`"ru" | "en"`) and `resolveAppLocale()`. `LabLocale` in `patternLab.ts` and `trailingStop.ts` is now an alias of `AppLocale`.
- **Frontend trailingStatus.ts**: new pure module (`frontend/src/trailingStatus.ts`) — labels, tone classes, formatting, and summary cards for trailing metrics. No trailing label or number hardcoded in TSX.
- **PaperTradingPanel.tsx**: added columns `exit_reason` (with funnel filter), `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`. Added trailing summary cards block above factor filters.
- **LiveTradingPanel.tsx**: added columns `exit_reason` (with funnel filter), `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r` in both tables (open + history). Trailing cards computed from data (no dedicated summary endpoint for Live).
- **types.ts**: `PaperSummary` extended with trailing fields; `PaperPosition` and `LivePosition` gained `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`.
- **api.ts**: `getPaperPositions` and `getLivePositions` accept `exit_reason`.

### Tests
- `backend/tests/test_issue150_exit_reason_filter.py` — 6 tests for `_build_where` (paper + live).
- `frontend/src/trailingStatus.test.ts` — 25 tests for pure functions.
- All existing tests (91 vitest + 126+ pytest) remain green.

### Deviations from issue description
- Issue #150 description references non-existent file paths and enum values (`eod`, `error`); actual code uses `EXIT_REASON_ORDER` from `exitReasons.ts` and `_build_where` from existing modules.
- Acceptance criterion `docker compose up frontend` is inapplicable — frontend is not Dockerized (only `npm run build`).
- Full i18n migration not performed — only new labels use the i18n layer; existing Russian strings in TSX left as-is.

### Verification commands
```bash
cd backend && python -m pytest tests/test_issue150_exit_reason_filter.py -v
cd frontend && npx vitest run src/trailingStatus.test.ts
cd frontend && npx tsc --noEmit
```

## 41. Operating live trailing stop (Issue #151)

Completed 2026-09-16. Live/sandbox trailing-stop ratchet, restart, and kill-switch.

### Architecture overview

- **Arming**: `process_signal()` arms trailing on position open if `strategy_config.trailing_stop.enabled=true` and kill switch is OFF. Zero broker calls. Parameters written to `trading.live_positions`: `trailing_enabled=true`, `trailing_steps` (JSONB), `risk_r=entry-stop`, `current_stop_price=stop_price`, `step_reached=0`.
- **Ratchet**: `monitor_positions()` calls `_apply_trailing(row, current_price)` for each active position with `trailing_enabled=true`. Rebuilds `TrailingState` from DB, evaluates current price as single bar, conditional UPDATE `WHERE id=%s AND step_reached < %s` ensures monotonicity.
- **Exit**: When `current_price <= current_stop_price`, cancels take, submits sell-limit at current price (blocking=False, deferred if rate limit exhausted). Records `exit_price_model`, `exit_price_actual`, `slippage_bp`, `slippage_r`, `lots_executed`. Status `closed_trailing`, reason `trailing`.
- **Broker amend (Issue #175)**: each ratchet also moves the broker `STOP_LOSS` by duplication — `post_stop_order()` for the new stop, `get_stop_orders()` to confirm it, then `cancel_stop_order()` for the older, lower one. The DB ratchet is kept even when the amend fails (a stop never moves down) and the next cycle retries; failures log `trailing_amend_failed`, an inconclusive confirmation logs `trailing_amend_unverified` and queues the old stop for `_cancel_pending_stops()`.
- **Kill switch**: `_refresh_kill_switch()` reads `trading.app_settings.trailing_kill_switch` before each monitoring cycle. ON pauses arming/ratchet; armed positions keep state. Fail-safe: DB error → kill switch ON.
- **Advisory lock**: `pg_try_advisory_lock(151001)` in `initialize()`, released in `shutdown()`. Prevents multiple executor instances.
- **Restart safety**: State restored from DB columns; no in-memory state. Idempotent replay.

### Database

- Migration `20260916_001_live_trailing_runtime.py`: extends `live_positions.status` CHECK to include `closed_trailing`, `closed_broker`; adds `exit_price_model`, `exit_price_actual`, `slippage_bp`, `slippage_r`, `lots_executed`; creates `trading.app_settings` table.
- Columns `trailing_enabled`, `trailing_steps`, `risk_r`, `current_stop_price`, `step_reached` already exist (migration `20260915_002`).

### Configuration

- `LIVE_TRADING.trailing_kill_switch`: default `false`, runtime override via `trading.app_settings`.
- `LIVE_TRADING.live_trailing_enabled`: default `true`, global toggle.
- `LIVE_TRADING.trailing_protective_ticks`: default `5`, protective offset (in `min_price_increment` ticks) between the broker stop trigger and the limit price it activates (Issue #175).
- `LIVE_TRADING.trailing_ticker_allowlist`: default `[]`, optional ticker filter.
- `LIVE_TRADING.broker_stop_enabled`: default `true`; `false` disables broker `STOP_LOSS` arming and leaves only the synthetic fallback (Issue #175, debug switch).
- `LIVE_TRADING.protection_retry_seconds`: default `30`, base of the exponential backoff after a failed `PostStopOrder`.
- `LIVE_TRADING.broker_stop_verify_interval_seconds`: default `60`, period of the `GetStopOrders` invariant pass.
- `LIVE_TRADING.oco_check_delay_seconds`: default `60`, OCO grace period (also the deadline for a deferred amend cancel).
- `LIVE_TRADING.oco_check_attempts`: default `3`, OCO verification attempts before the final critical alert.
- `LIVE_TRADING.operations_lookback_hours`: default `24`, `GetOperations` window used for fill reconciliation.
- `LIVE_TRADING.entry_token_reserve`: default `1.0`, tokens an entry call must leave in the bucket for protection calls.
- `LIVE_TRADING.orphan_stop_sweep_enabled`: default `true`, master switch of the account-wide orphan stop sweep (Issue #199).
- `LIVE_TRADING.orphan_stop_sweep_interval_seconds`: default `300`, minimum period between two sweeps; a non-positive value means "every monitoring cycle".
- `LIVE_TRADING.orphan_stop_confirmations`: default `2`, how many consecutive sweeps must report the same orphan before it may be cancelled; below 1 is clamped to 1 and rejected at startup.
- `LIVE_TRADING.orphan_stop_grace_seconds`: default `900`, protection window for a stop this process armed recently.
- `LIVE_TRADING.orphan_stop_max_cancels`: default `3`, per-pass ceiling; `0` is watch-only, exceeding the cap makes the whole pass fail closed.
- `LIVE_TRADING.orphan_stop_alert_interval_seconds`: default `3600`, throttle of the fail-closed / cancelled Telegram alerts.


### Operations

**Enable trailing**:
1. Ensure strategy has `trailing_stop.enabled=true` in config.
2. Verify `trading.app_settings.trailing_kill_switch = false`:
   ```sql
   SELECT value FROM trading.app_settings WHERE key='trailing_kill_switch';
   ```
3. Start executor: `python -m app.analytics.run_live_trading --strategy <name> --duration-minutes 30`

**Pause trailing (kill switch)**:
```sql
UPDATE trading.app_settings SET value='true'::jsonb, updated_at=now() WHERE key='trailing_kill_switch';
```
No restart required. Armed positions keep state; new positions not armed.

**Resume trailing**:
```sql
UPDATE trading.app_settings SET value='false'::jsonb, updated_at=now() WHERE key='trailing_kill_switch';
```

**Monitor trailing exits**:
```bash
grep 'trailing_exit' reports/live-executor/live-executor-*.log
```
Structured log: `ticker, entry, initial_stop, final_stop, step_reached, risk_r, model_price, actual_price, slippage_bp, slippage_r, lots_requested, lots_executed, position_id`.

**Check advisory lock**:
```sql
SELECT pg_try_advisory_lock(151001);  -- false if another instance running
```

### Testing

68 tests in `test_live_executor.py` (43 new for #151):
- Arming: `test_open_position_arms_trailing_when_enabled`, `test_open_position_skips_trailing_when_disabled_in_config`, `test_open_position_skips_trailing_when_kill_switch_on`, `test_open_position_no_trailing_without_strategy_config`
- Ratchet: `test_apply_trailing_returns_none_when_disabled`, `test_apply_trailing_returns_none_when_kill_switch_on`, `test_apply_trailing_returns_none_when_no_steps`, `test_apply_trailing_returns_none_when_price_below_next_step`, `test_apply_trailing_ratchets_when_price_reaches_trigger`, `test_apply_trailing_returns_none_when_step_already_reached`
- Execution facts: `test_close_position_records_model_and_actual_price`, `test_close_position_handles_missing_actual_price`, `test_close_position_trailing_uses_closed_trailing_status`
- Rate limiter: `test_token_bucket_try_acquire_returns_true_when_token_available`, `test_token_bucket_try_acquire_returns_false_when_exhausted`, `test_token_bucket_try_acquire_recovers_over_time`, `test_broker_call_nonblocking_returns_none_when_exhausted`, `test_broker_call_blocking_still_works`
- Config validation: 9 tests for `trailing_kill_switch`, `live_trailing_enabled`, `trailing_protective_ticks`, `trailing_ticker_allowlist`
- Kill switch integration: `test_kill_switch_prevents_trailing_arming`, `test_kill_switch_preserves_armed_positions`, `test_invalid_trailing_config_disables_arming`

### Verification commands

```powershell
# Тесты
cd f:\GIT\trading-terminal\backend; python -m pytest tests/test_live_executor.py -v

# Локальный запуск миграций (PowerShell, без Docker):
cd f:\GIT\trading-terminal\backend; $env:APP_DATABASE_URL=""; $env:POSTGRES_HOST="localhost"; python -m alembic upgrade head

# Docker запуск:
docker compose exec backend python -m alembic upgrade head
```

### Known limitations

- `EXIT_TAKE` from replay is ignored (take is resting order, existing reconciliation handles it).
- Partial fills (`lots_executed < size_lots`) logged but not retried.
- `broker_position_vanished` event type not yet implemented (currently logs warning).
