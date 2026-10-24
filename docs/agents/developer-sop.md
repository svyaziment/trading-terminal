# Standard Operating Procedure (SOP) — FoxEdge Team

Mandatory for all developers (human and AI agents). Any deviation without TeamLead approval results in PR rejection.

## 🦊 Team Roster

| Role | Name | Agent |
|---|---|---|
| Product Owner | Alex | Human (Alexander Lisitsyn) |
| Team Lead | Reynard | Qwen AI |
| Backend Dev | Arctic | Qwen AI |
| Frontend Dev | Fennec | Qwen AI |
| Data Analyst | Vulpec | Qwen AI |

## 📁 Documentation Updates

**Rule:** After every successfully completed Issue that changes architecture, API, DB schema, data pipeline, or operational behavior, the developer **MUST** update the project documentation in `docs/`.

**Documentation update matrix:**

| Change in Issue | Docs to update |
|---|---|
| New module/file | `docs/project-context.md` §2 (File Structure) |
| API endpoint added/changed | `docs/project-context.md` §5 (API Endpoints) |
| DB schema change | `docs/project-context.md` §3 (Database Schema) |
| Data pipeline change | `docs/project-context.md` §4 + `docs/handover.md` §4 |
| New operational gotcha | `docs/handover.md` §10 (Operational Gotchas) |
| Roadmap status change | `docs/project-context.md` §8 (Roadmap Status) |
| Strategy change | `docs/strategy/*.md` |

**Rules:**

- Keep **BOTH** language versions in sync: `*.md` (EN) and `*.ru.md` (RU).
- Update the `Last refreshed: <date> (task-NNN)` header in every changed doc.
- Documentation update goes into the **SAME** PR (or a follow-up PR linked to the same Issue).
- Reference instead of duplicating (`handover.md` references `project-context.md` sections).

## 📂 Task Artifacts (reports/)

**Rule:** When solving **ANY** Issue, a working folder is created inside `reports/`.

**Folder naming:** `<NNN>-<branch-name>`, where:

- `<NNN>` — Issue number (with leading zeros for sorting, e.g. `042`),
- `<branch-name>` — the created branch name without the `feature/` prefix (e.g. `issue-42-fix-evaluator`).

**Example:** `reports/042-issue-42-fix-evaluator/`.

Purpose: consistent sorting by Issue number and unambiguous binding of artifacts to the branch.

**Mandatory task artifacts** (created once per Issue):

| File | Purpose |
|---|---|
| `issue-context.md` | Living document for the task. Maintained from start to finish. Contains: execution plan, step completion marks, what was done, what remains, decisions made, and deviations from the plan. |
| `report.md` | Final report for the Issue (for the PR comment). |

**Mandatory bash-script artifacts** (created anew on **EACH** script):

| File | Purpose |
|---|---|
| `log.txt` | Raw log of the current bash-script execution (commands, stdout/stderr, exit codes). **Overwritten** on each new script. |
| `context.md` | Human-readable summary of the collected context. Mandatory when the script performs context collection. |

**Free artifacts:**

At most 2 free files per bash-script (at the agent's discretion): e.g. `patch.diff`, `test-output.txt`, `regression.json`.

All file names — Latin characters, no spaces.

**File limit per bash-script:**

**Rule:** During the execution of **ONE** bash-script, at most **5 files** may be created.

Rationale: the agent can meaningfully read no more than five files in one pass. Exceeding the limit means the script is doing too much and must be split into several sequential bash-scripts.

By the end of the Issue the folder may accumulate more than five files — that is fine, as long as each individual bash-script stayed within the limit.

**Important:** the `reports/` folder is in `.gitignore`. Artifacts are **NOT** committed to the repository. They are shared via PR comments, not via repo structure.

## 🔄 Task Algorithm

### Step 1. Analyze the Issue

1. Open `https://github.com/svyaziment/trading-terminal/issues/<N>`
2. Read the description, acceptance criteria, related files.
3. If unclear — switch to discussion mode in Issue comments. Do **NOT** write code.
4. Fix a `short-description` for the future branch name and artifact folder. Format: `issue-<N>-<short-description>` (Latin, hyphens, no spaces).
5. Create the task artifact folder: `reports/<NNN>-issue-<N>-<short-description>/`, where `<NNN>` is the Issue number with leading zeros. The folder is created **before the branch**, because its name is derived from the `short-description` fixed at this step, not from the fact that the branch exists.

Example for Issue #42 described as "fix evaluator":

```
reports/042-issue-42-fix-evaluator/
```

### Step 2. Create and Publish Branch

```bash
git checkout main
git pull origin main
git checkout -b feature/issue-<N>-<short-description>
git push -u origin feature/issue-<N>-<short-description>
```

The branch name must match the `short-description` fixed in Step 1. After creating the branch, create `issue-context.md` in the artifact folder with a draft plan (refined in Step 3.5).

### Step 3. Collect Context (MANDATORY for ANY task)

**Rule:** Context collection is mandatory **BEFORE** starting any task — no exceptions. Guessing is forbidden.

**How context is collected:**

Context is collected by reading and writing the necessary files via a bash-script (see `docs/refresh/context_collector.py` as reference). Manual "eyeballing" of files does not replace scripted collection — a reproducible artifact is required.

The script must start with:

```bash
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"
```

The result is saved to `<reports-dir>/context.md` — a human-readable summary of the collected context. This is the only mandatory format; machine `.json` is not used.

`log.txt` in `<reports-dir>/` is overwritten with this script's log.

**Example:**

```bash
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"
ISSUE_N=42
BRANCH="issue-42-fix-evaluator"
REPORTS_DIR="reports/$(printf '%03d' ${ISSUE_N})-${BRANCH}"
mkdir -p "${REPORTS_DIR}"
python docs/refresh/context_collector.py \
  --task-id "task-${ISSUE_N}-context" \
  --files backend/app/analytics/target_file.py,backend/app/api/target_api.py \
  --tables target_table_1,target_table_2 \
  --output "${REPORTS_DIR}/context.md"
```

### Step 3.5. Task Decomposition (MANDATORY)

**Script delivery — `.sh` file only:** the bash-script body is created in `scripts/<NNN>-<issue-name>.sh` via heredoc (`cat > scripts/<NNN>-<issue-name>.sh <<'TASK_EOF' … TASK_EOF`), then `chmod +x scripts/<NNN>-<issue-name>.sh` and run `bash scripts/<NNN>-<issue-name>.sh`. Pasting the script body directly into the interactive terminal is forbidden: large pastes crash the Git Bash session (lost blocks, session aborts). Verify results by reading files (`cat reports/<NNN>-<issue-name>/…`), not by watching the paste output.

**Logging files — `.txt` only:** all task and run logs are written with the `.txt` extension (`log.txt`, `run_v4.txt`, `shards.txt`); the `.log` extension is not used for logging files.

**Rule:** After collecting context the developer **MUST** produce an execution plan for the Issue and split it into the required number of steps.

**Plan requirements:**

- The plan is saved in `<reports-dir>/issue-context.md` under the "## Execution Plan" section.
- Each step is an atomic operation with a verifiable result (patch, rebuild, test, doc update).
- Attempting the entire task in one step is forbidden. If the task is multi-part — it is sliced into N steps, each executed and logged separately.
- If during execution it turns out more steps are needed — the plan is extended, `issue-context.md` is updated.

**Rules for maintaining `issue-context.md`:**

- `issue-context.md` is a living document. It is updated after each completed step, not only at the end of the task.
- After a step, `[x]` is marked in the plan and an entry is added to the "## Change Log" section: what was done, which files were affected, what decisions were made, what deviations from the plan occurred.
- If a step is cancelled or redone — that is also recorded.
- By the time the task is finished, `issue-context.md` must unambiguously answer: what was done per the plan, what remains, why.

**`issue-context.md` format:**

```markdown
# Issue #<N>: <short title>

## Execution Plan
- [ ] Step 1. <description> → expected artifact
- [ ] Step 2. <description> → expected artifact
- [ ] ...

## Change Log

### Step 1. <description> — <date/time>
- Status: completed
- Files changed: `path/to/file.py`
- Decisions: <why it was done this way>
- Deviations from plan: <if any>

### Step 2. ...
```

### Step 4. Create Solution Script

- Start with `export MSYS_NO_PATHCONV=1` and `export MSYS2_ARG_CONV_EXCL="*"`.
- Use quoted heredocs (`<<'PYEOF'`) for code.
- Atomic patches: verify each regex anchor finds **EXACTLY 1** match before applying. If 0 or >1 — abort with `PATCH_FAIL`.
- After file creation/modification: `wc -c path/to/file`.
- After backend changes: `docker compose up -d --build backend`.
- One bash-script — one semantic operation. If a script does more than one operation (e.g. both patches and rebuilds and tests) — split it into sequential bash-scripts.
- Within one bash-script at most 5 files are created (see "📂 Task Artifacts"). Exceeding means the script is doing too much.
- `log.txt` is overwritten on each new bash-script.
- After running the script, update `issue-context.md` (see Step 3.5).

### Step 5. Update Documentation

After completing the Issue, update the project documentation in `docs/` per the Documentation update matrix above. Both language versions (EN and RU) must be updated.

### Step 6. Create PR via CLI

> **TODO:** Describe the procedure for creating a PR using `gh` (GitHub CLI).
>
> - Authorization check: `gh auth status`
> - Commit changes: `git add <files> && git commit -m "feat(scope): description (#NNN)"`
> - Push branch: `git push -u origin <branch>`
> - Create PR: `gh pr create --base main --head <branch> --title "..." --body "..."`
> - Issue linkage: the commit body and/or PR body must contain `Closes #NNN` or `Part of #NNN`.
> - PR description language: Russian.

## ⏳ Long Runs and Heavy Tests: Detached Mode with Workers and Liveness Checks Only

**Rule:** any run expected to last more than 10 minutes (parity gates, analytics extracts,
portfolio runs, regression matrices, heavy tests) runs **ONLY** detached, resumable and
observable. Attached launches for such tasks are forbidden.

**Mandatory run attributes:**

- **Launch:** `nohup python <script> >> <reports-dir>/log.txt 2>&1 &`, PID saved to
  `<reports-dir>/run.pid`. Relaunch guard: if the old PID is alive (`kill -0`),
  the new launch aborts with `ABORT`.
- **Resumability:** per-unit cache (ticker/book/stage) in a gitignored directory (`cache/...`);
  a restart continues from cache, not from zero.
- **Parallelism:** `ProcessPoolExecutor` with explicit `--workers N`; N is chosen from the prior
  run environment (e.g. `environment.workers` in `summary.json`); on DB pool/connection errors —
  reduce N and relaunch (cache preserves progress).
- **Observability:** progress logs with flush per unit (`[book] [k/N] <ticker> ok ...`);
  pipeline exit code via `${PIPESTATUS[0]}`, not `$?` after `| tee`.
- Workers initialize their own process-local globals (strategy plugin registry, logging):
  on Windows spawn the parent's globals are not inherited (lesson of #147: without
  `register_default_strategies()` in the worker the run dies with `"Unknown strategy"`).

**Standard liveness and progress check block** (run in a separate terminal, creates no files):

```bash
tail -n 20 <reports-dir>/log.txt
for b in <list of books/stages>; do printf "%s: " "$b"; ls <cache-dir>/$b 2>/dev/null | wc -l; done
PID=$(cat <reports-dir>/run.pid); kill -0 "$PID" 2>/dev/null && echo "ALIVE pid=$PID" || echo "DEAD (run finished or crashed)"
```

**Interpretation:** cache counters grow toward the unit count; `ALIVE` with growing counters = healthy run;
`ALIVE` with standing counters = check the process CPU (~+1 core-second per 1 s wall = computing, not hung);
`DEAD` without a completion line in the log = crash: cause is in the log tail, relaunch with the same script —
the cache preserves progress.

**Reference implementation:** `analytics/issue-147-trailing-production-parity/run.py` (task #147) —
detached run, cache `cache/<book>/<TICKER>.json`, `--workers`, PID-file guard.

## ✅ Pre-Submit Checklist

- [ ] Branch created from fresh `main` with correct name.
- [ ] Folder `reports/<NNN>-<branch-name>/` created for task artifacts.
- [ ] `issue-context.md` exists in the folder with plan and change log.
- [ ] Context collected via script **BEFORE** starting edits (not "eyeballing").
- [ ] Execution plan written in `issue-context.md` and split into steps.
- [ ] Task was **NOT** executed in a single step (decomposition followed).
- [ ] `issue-context.md` updated after each step, not only at the end.
- [ ] Each bash-script created ≤ 5 files.
- [ ] `log.txt` overwritten on each bash-script.
- [ ] Script contains `MSYS_NO_PATHCONV=1` and `MSYS2_ARG_CONV_EXCL="*"`.
- [ ] All code heredocs use quotes (`<<'EOF'`).
- [ ] Bash-script delivered as `scripts/<NNN>-<issue-name>.sh` and run via `bash scripts/<NNN>-<issue-name>.sh` (body not pasted into the terminal).
- [ ] Logging files use the `.txt` extension (not `.log`).
- [ ] Long run (>10 min) executed detached: `nohup` + `<reports-dir>/run.pid`, resumable cache and `--workers`; liveness and progress verified with the standard monitor block (log tail, cache counters, `kill -0` PID).
- [ ] Regex patches verified (exactly 1 match) before write.
- [ ] File size checked (`wc -c`) after heredoc.
- [ ] Docker rebuild performed (if backend Python changed).
- [ ] Documentation updated (`project-context.md` + `.ru.md`, `handover.md` + `.ru.md`) if the Issue changes architecture/API/schema/pipeline.
- [ ] **`report.md` exists in the artifact folder** — final report for the PR comment.
- [ ] **`issue-context.md`** contains the final status: what was done, what remains, why.

## ⛔ Red Lines (Instant Reject)

- Editing code without context collection.
- Hardcoding tickers/params outside `trading_config.py` / `trading_universe`.
- Modifying `StrategyEvaluator` logic without bit-for-bit regression test (`regression_match: true`).
- Missing file size check (`wc -c`) after heredoc.
- Failing to update `project-context.md` / `project-context.ru.md` after Issues that change architecture/API/schema/pipeline.
- Starting edits **WITHOUT** scripted context collection (even for single-element tasks).
- Executing an Issue in one step without decomposition and a plan in `issue-context.md`.
- Missing `reports/<NNN>-<branch-name>/` folder or missing `issue-context.md` in it.
- Missing `report.md` in task artifacts.
- Running a >10-minute run attached, without a PID file, resumable cache and workers — or accepting such a run without the standard liveness monitor block.

Any task not following this SOP will be returned for rework without code review.
