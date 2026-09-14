# Standard Operating Procedure (SOP) — FoxEdge Team

Mandatory for all developers (human and AI agents). Any deviation without TeamLead approval results in PR rejection.

## 🦊 Team Roster

| Role | Name | Agent |
| --- | --- | --- |
| Product Owner | Alex | Human (Alexander Lisitsyn) |
| Team Lead | Reynard | Qwen AI |
| Backend Dev | Arctic | Qwen AI |
| Frontend Dev | Fennec | Qwen AI |
| Data Analyst | Vulpec | Qwen AI |

## 📁 Documentation Updates

**Rule:** After every successfully completed Issue that changes architecture, API, DB schema, data pipeline, or operational behavior, the developer MUST update the project documentation in `docs/`.

**Documentation update matrix:**

| Change in Issue | Docs to update |
| --- | --- |
| New module/file | `docs/project-context.md` §2 (File Structure) |
| API endpoint added/changed | `docs/project-context.md` §5 (API Endpoints) |
| DB schema change | `docs/project-context.md` §3 (Database Schema) |
| Data pipeline change | `docs/project-context.md` §4 + `docs/handover.md` §4 |
| New operational gotcha | `docs/handover.md` §10 (Operational Gotchas) |
| Roadmap status change | `docs/project-context.md` §8 (Roadmap Status) |
| Strategy change | `docs/strategy/*.md` |

**Rules:**
- Keep BOTH language versions in sync: `*.md` (EN) and `*.ru.md` (RU).
- Update the `Last refreshed: <date> (task-NNN)` header in every changed doc.
- Documentation update goes into the SAME PR (or a follow-up PR linked to the same Issue).
- Reference instead of duplicating (handover.md references project-context.md sections).

**Note on reports:** The `reports/` folder is in `.gitignore`. Agent task reports (report.json, log.txt, context.json) are LOCAL artifacts for diagnostics and are NOT committed to the repository. They are shared via PR comments, not via repo structure.

## 🔄 5-Step Task Algorithm

### Step 1. Analyze the Issue
1. Open `https://github.com/svyaziment/trading-terminal/issues/<N>`
2. Read description, acceptance criteria, related files.
3. If unclear — switch to discussion mode in Issue comments. Do NOT write code.

### Step 2. Create and Publish Branch
```bash
git checkout main
git pull origin main
git checkout -b feature/issue-<N>-<short-description>
git push -u origin feature/issue-<N>-<short-description>
```

### Step 3. Collect Context (MANDATORY for multi-element tasks)
If the task touches more than one file/module/table, guessing is forbidden.
```bash
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"
TASK_ID="task-<N>-context"
python docs/refresh/context_collector.py \
  --task-id "${TASK_ID}" \
  --files backend/app/analytics/target_file.py,backend/app/api/target_api.py \
  --tables target_table_1,target_table_2 \
  --output context.json
```

### Step 4. Create Solution Script
- Start with `export MSYS_NO_PATHCONV=1` and `export MSYS2_ARG_CONV_EXCL="*"`.
- Use quoted heredocs (`<<'PYEOF'`) for code.
- Atomic patches: verify each regex anchor finds EXACTLY 1 match before applying. If 0 or >1 — abort with `PATCH_FAIL`.
- After file creation/modification: `wc -c path/to/file`.
- After backend changes: `docker compose up -d --build backend`.
- If script exceeds ~100 lines of logic, split into sequential steps.

### Step 5. Update Documentation
After completing the Issue, update project documentation in `docs/` per the Documentation Updates matrix above. Both EN and RU versions must be updated.

## ⏳ Long Runs and Heavy Tests: Detached Mode with Workers and Liveness Checks Only

Rule: any run expected to last more than 10 minutes (parity gates, analytics extracts,
portfolio runs, regression matrices, heavy tests) runs ONLY detached, resumable and
observable. Attached launches for such tasks are forbidden.

Mandatory run attributes:
- Launch: `nohup python <script> >> <reports-dir>/log.txt 2>&1 &`, PID saved to `<reports-dir>/run.pid`. Relaunch guard: if the old PID is alive (`kill -0`), the new launch aborts with ABORT.
- Resumability: per-unit cache (ticker/book/stage) in a gitignored directory (`cache/...`); a restart continues from cache, not from zero.
- Parallelism: explicit `--workers N` (ProcessPoolExecutor) or bash-launched shard processes when Windows spawn is unreliable; N is chosen from the prior run environment (e.g. `environment.workers` in summary.json); on DB pool/connection errors reduce N and relaunch (cache preserves progress).
- Observability: progress logs with flush per unit (`[book] [k/N] <ticker> ok ...`); pipeline exit code via `${PIPESTATUS[0]}`, not `$?` after `| tee`.
- Workers initialize their own process-local globals (strategy plugin registry, logging): on Windows spawn the parent's globals are not inherited (lesson of #147: without `register_default_strategies()` in the worker the run dies with "Unknown strategy").

Standard liveness and progress check block (run in a separate terminal, creates no files):
tail -n 20 <reports-dir>/log.txt
for b in <list of books/stages>; do printf "%s: " "$b"; ls <cache-dir>/$b 2>/dev/null | wc -l; done
PID=$(cat <reports-dir>/run.pid); kill -0 "$PID" 2>/dev/null && echo "ALIVE pid=$PID" || echo "DEAD (run finished or crashed)"

Interpretation: cache counters grow toward the unit count; ALIVE with growing counters = healthy run; ALIVE with standing counters = check the process CPU (~+1 core-second per 1 s wall = computing, not hung); DEAD without a completion line in the log = crash: cause is in the log tail, relaunch with the same script — the cache preserves progress.

Reference implementation: `analytics/issue-147-trailing-production-parity/run.py` (task #147) — v4 shard run, cache `cache/<book>/<TICKER>.json`, `--workers`, PID-file guard.


## ✅ Pre-Submit Checklist

- [ ] Branch created from fresh `main` with correct name.
- [ ] Context collected and analyzed (no guessing).
- [ ] Script contains `MSYS_NO_PATHCONV=1` and `MSYS2_ARG_CONV_EXCL="*"`.
- [ ] All code heredocs use quotes (`<<'EOF'`).
- [ ] Long run (>10 min) executed detached: `nohup` + `<reports-dir>/run.pid`, resumable cache and `--workers`; liveness and progress verified with the standard monitor block (log tail, cache counters, `kill -0` PID).
- [ ] Regex patches verified (exactly 1 match) before write.
- [ ] File size checked (`wc -c`) after heredoc.
- [ ] Docker rebuild performed (if backend Python changed).
- [ ] Documentation updated (`project-context.md` + `.ru.md`, `handover.md` + `.ru.md`) if the Issue changes architecture/API/schema/pipeline.

## ⛔ Red Lines (Instant Reject)

1. Editing code without context collection for multi-element tasks.
2. Hardcoding tickers/params outside `trading_config.py` / `trading_universe`.
3. Modifying `StrategyEvaluator` logic without bit-for-bit regression test (`regression_match: true`).
4. Missing file size check (`wc -c`) after heredoc.
5. Failing to update `project-context.md` / `project-context.ru.md` after Issues that change architecture/API/schema/pipeline.
6. Running a >10-minute run attached, without a PID file, resumable cache and workers — or accepting such a run without the standard liveness monitor block.

Any task not following this SOP will be returned for rework without code review.
