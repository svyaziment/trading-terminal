# Standard Operating Procedure (SOP) — FoxEdge Team

**Mandatory for all developers (human and AI agents). Any deviation without TeamLead approval results in PR rejection.**

## 🦊 Team Roster

| Role | Name | Agent |
|---|---|---|
| Product Owner | Alex | Human (Alexander Lisitsyn) |
| Team Lead | Reynard | Qwen AI |
| Backend Dev | Arctic | Qwen AI |
| Frontend Dev | Fennec | Qwen AI |

---

## 📁 Reports Structure

### Rule

Each agent stores task reports in a **personal folder**:

```
reports/<AGENT_NAME>/<ISSUE_NUMBER>_<ISSUE_NAME>/
```

### Naming Conventions

- `AGENT_NAME`: `Reynard` | `Arctic` | `Fennec`
  - Product Owner (Alex) does not write reports here — he reviews them.
- `ISSUE_NUMBER`: GitHub Issue number (without `#`)
- `ISSUE_NAME`: short kebab-case description (latin characters)

### Examples

| Agent | Task | Path |
|---|---|---|
| Reynard | Issue #24 | `reports/Reynard/24_paper-trading-integration/` |
| Arctic | Issue #28 | `reports/Arctic/28_data-pipeline-moex-fix/` |
| Fennec | Issue #22 | `reports/Fennec/22_remove-confirm-windows-ui/` |

### File Limit (max 4 files per report folder)

1. `report.json` — structured summary (status, changes, next_action)
2. `log.txt` — human-readable execution log
3. `context.json` — collected context (if applicable)
4. One target artifact (`regression_verdict.json`, `patch.diff`, `test_output.txt`, etc.)

Intermediate files go to `_tmp/` and are deleted on success.

---

## 🔄 5-Step Task Algorithm

### Step 1. Analyze the Issue

- Open `https://github.com/svyaziment/trading-terminal/issues/<N>`
- Read description, acceptance criteria, related files.
- If unclear — switch to discussion mode in Issue comments. Do NOT write code.

### Step 2. Create and Publish Branch

```bash
git checkout main
git pull origin main
git checkout -b feature/issue-<N>-<short-description>
git push -u origin feature/issue-<N>-<short-description>
```

### Step 3. Collect Context (MANDATORY for multi-element tasks)

If the task touches more than one file/module/table, **guessing is forbidden**.

```bash
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

TASK_ID="task-<N>-context"
REPORT_DIR="reports/<AGENT_NAME>/<N>_<branch_name>"
mkdir -p "${REPORT_DIR}"

python docs/refresh/context_collector.py \
  --task-id "${TASK_ID}" \
  --files backend/app/analytics/target_file.py,backend/app/api/target_api.py \
  --tables target_table_1,target_table_2 \
  --output "${REPORT_DIR}/context.json"
```

### Step 4. Create Solution Script

- Start with `export MSYS_NO_PATHCONV=1` and `export MSYS2_ARG_CONV_EXCL="*"`.
- Use quoted heredocs (`<<'PYEOF'`) for code.
- **Atomic patches**: verify each regex anchor finds EXACTLY 1 match before applying. If 0 or >1 — abort with `PATCH_FAIL`.
- After file creation/modification: `wc -c path/to/file`.
- After backend changes: `docker compose up -d --build backend`.
- If script exceeds ~100 lines of logic, split into sequential steps.

### Step 5. Save Reports

All artifacts go to:

```
reports/<AGENT_NAME>/<ISSUE_NUMBER>_<ISSUE_NAME>/
```

---

## ✅ Pre-Submit Checklist

- [ ] Branch created from fresh `main` with correct name.
- [ ] Context collected and analyzed (no guessing).
- [ ] Script contains `MSYS_NO_PATHCONV=1` and `MSYS2_ARG_CONV_EXCL="*"`.
- [ ] All code heredocs use quotes (`<<'EOF'`).
- [ ] Regex patches verified (exactly 1 match) before write.
- [ ] File size checked (`wc -c`) after heredoc.
- [ ] Docker rebuild performed (if backend Python changed).
- [ ] Reports saved to `reports/<AGENT_NAME>/<ISSUE_NUMBER>_<ISSUE_NAME>/`.
- [ ] `report.json` contains valid JSON with `"status": "success"`.

---

## ⛔ Red Lines (Instant Reject)

1. Editing code without context collection for multi-element tasks.
2. Hardcoding tickers/params outside `trading_config.py` / `trading_universe`.
3. Modifying `StrategyEvaluator` logic without bit-for-bit regression test (`regression_match: true`).
4. Exceeding 4-file limit in report folder (trash must go to `_tmp/` and be cleaned).
5. Missing file size check (`wc -c`) after heredoc.
6. Reports saved outside `reports/<AGENT_NAME>/` structure.

---

*Any task not following this SOP will be returned for rework without code review.*
