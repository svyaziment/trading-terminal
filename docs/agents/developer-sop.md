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

## ✅ Pre-Submit Checklist

- [ ] Branch created from fresh `main` with correct name.
- [ ] Context collected and analyzed (no guessing).
- [ ] Script contains `MSYS_NO_PATHCONV=1` and `MSYS2_ARG_CONV_EXCL="*"`.
- [ ] All code heredocs use quotes (`<<'EOF'`).
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

Any task not following this SOP will be returned for rework without code review.
