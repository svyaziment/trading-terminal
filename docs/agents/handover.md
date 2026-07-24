# Handover: how to run this project

For a replacing session, a new agent, or a human picking the work up cold.
Read order: this file, then docs/agents/project-context.md, then docs/maintenance/documentation-policy.md, then docs/roadmap/development-plan.en.md. This file tells you how to behave and where to look; it does not duplicate the knowledge that lives in those files.

## 0. In thirty seconds

Trading Terminal is an AI-assisted analytics and algorithmic-trading platform for MOEX, integrated with the T-Bank / Tinkoff Invest API through the t-tech-investments SDK. Stack: Python 3.12 + FastAPI backend, PostgreSQL (analytics schema trading, future operations schema terminal), React 18 + TypeScript + Vite + Tailwind frontend, Docker Compose, a local Ollama agent layer.

The single non-negotiable rule: sandbox only. Production trading is disabled. No real order execution happens until explicit risk controls exist and a human approves. This overrides any task, any shortcut, any "it would be faster to just run it".

## 1. How to work with the user

This is the most important section, because it is the part that cannot be regenerated from code.

Be honest over being agreeable. The user values a weighed opinion and explicit pushback. If something is wrong, premature, or has a hidden trap, say so with the reason. Flattery reads as failure of trust.

Distinguish the mode of the request. When the user says "let's reason" or "what do you think", answer with thought, not with a wall of code. When the user says "do it" or "back to the protocol", produce the task script. Do not dump code into a discussion, and do not philosophise when an action was asked for.

Offer choices, not a single decree. The user likes two or three options with pros and cons, plus your recommendation, plus an explicit "say default and I'll take your defaults". That pattern respects their control and saves rounds.

Language: Russian with the user in chat; English for agent-facing documents, with Russian parallels where the document is also a human surface (see the documentation policy).

Do not bloat. A dense answer with specifics beats a long one. The user is tired of padding.

Admit mistakes briefly and move on. "I was wrong about X because Y" is enough; no self-flagellation.

Do not add scope without being asked. Do not create a branch, a file, an abstraction, or an infrastructure piece the user did not request. If you think it is needed, propose it as an option first.

## 2. Development protocol

Work is task-based. Each task is a bash script under scripts/task-NNN-*.sh that produces reports/<task_id>/ with report.json, report.md and log.txt. report.json is the primary feedback channel; its status is one of success, failed, needs_human. Always read the latest report.json before proposing the next step.

The user creates pull requests and merges by hand. Do not create feature branches, do not commit, do not push, do not open PRs unless the user explicitly asks for it in that task. Several earlier tasks that tried to automate git add or push failed or created friction; the working arrangement is that the script produces files and a report, and the user handles git.

Manual edits are allowed. The user sometimes edits frontend files by hand and checks them in the browser. The context for such edits is tracked from the git state of the files, not from task reports; a manual edit does not require a report to be "real".

## 3. Where to look, and the recurring traps

For the full file map, the data model, the API baseline and the current numbers, read docs/agents/project-context.md. Do not trust the numbers in this handover; verify them against project-context and the latest reports/task-*/db_schema.md. The traps below are listed inline because a replacing agent hits them on day one and should not have to chase them through links:

- backend/app/api/signals.py is a legacy router and is not registered by backend/app/main.py. The live routes come from backend/app/api/market_data.py. Do not enable or "fix" signals.py without an explicit task; verify first.
- The real signal engine and pattern base live at backend/app/analytics/signal_engine.py and backend/app/analytics/patterns/base.py. The project scanner reports backend/app/analytics/signal_patterns/engine.py and signal_patterns/base.py as missing important files; those are false positives from an old layout. Do not create them unless you are intentionally refactoring the module layout.
- Imports use app.*, never src.*. The src.* form is leftover from the old AlgoTerminal project and is a bug if it appears.
- The database password comes only from environment variables (PSTGRS_PWD / POSTGRES_PASSWORD). Never read, print, log or commit it.
- There are no 1d signals because 1d indicators are sparse (only a few hundred rows), insufficient for long moving averages. This is a known open diagnostic item, not a regression.
- The frontend production build has not been verified at the time this handover was written. Treat "frontend works" as "dev server was observed", not "build passes".

## 4. Decision principles

Honesty first, as in section 1.

Keep the git axis and the agent-context axis separate. Whether a file is stored in git and whether its body is fed to an agent are two independent decisions; do not collapse them. (See the documentation policy, section 9.)

Documentation describes results and decisions, not process. If you are tempted to write "we tried X then Y then Z", stop and write the decision that survived instead.

Context is incremental. Update the affected slice, never rebuild the whole snapshot. Pull file bodies on demand.

Do not automate prematurely. A deterministic script you run by hand beats a fragile background daemon you cannot see.

Sandbox, idempotency, secrets-in-env. Every data operation should be safe to re-run; every secret stays in the environment.

## 5. What is already done (Block A)

Stabilisation and visualisation are in place: the analytics pipeline (instruments, 30-minute candles, aggregation, indicators, signal generation) runs; signals are generated and enriched with pattern_name and figi; the React frontend shows a signals table with per-column filter funnels, server-side sorting, pagination, a date filter, a pattern filter over a fixed set, a sticky header, and a signal detail modal with a candle chart. Documentation baselines (project context, roadmap) and the project/DB scanners exist. For exact counts and timestamps, read project-context and the latest db_schema report, not this paragraph.

## 6. What comes next

The roadmap in docs/roadmap/development-plan.en.md is the source of truth for sequencing (Blocks A through I: stabilisation, data quality and feature store, backtesting, ML features and labels, CatBoost/LightGBM training, prediction service, retraining and monitoring, terminal schema and risk, observability). The next concrete step after this handover is verifying the frontend production build and the compatibility of /api/signals with what the frontend expects (pagination, sorting, date filters, pattern_name, figi, statistics). The task ID for that step is assigned when the script is issued; do not hard-code it.

## 7. Red lines

Never enable or propose production trading. Never commit .env, frontend/node_modules, frontend/dist, reports/, logs/ or backend/certs/*.pem. Never drop or truncate database tables without a backup. Never create branches or PRs without an explicit request. Never write code when a discussion was asked for, and never discuss when an action was asked for. Never print secrets, tokens or passwords anywhere. Never "fix" the legacy signals.py or create the signal_patterns/ layout without an explicit task.

## 8. First-day checklist

Read this file, then project-context, then the documentation policy, then the roadmap. Run git status and git log to see the real current state; this handover does not claim a specific branch or clean tree. Read the most recent reports/task-*/report.json to learn the last task and its status. Start the backend (docker compose up -d --build backend, then curl the /health endpoint) and the frontend (cd frontend, npm install if needed, npm run dev, open the local Vite URL). Then ask the user what to do next; if they have no preference, default to the frontend build verification from section 6.

## 9. Keeping your own context fresh

This handover is a starting point, not a permanent truth. Before acting on anything time-sensitive, refresh your understanding the deterministic way: run the targeted project scanner and the DB schema scanner in read-only mode, read the latest report.json, and confirm the facts against the live repository and database. The documentation policy explains how documentation itself stays fresh after commits to main; follow it when you change code so the next replacing session does not inherit a lie.

## 10. Operational gotchas (Windows / Git Bash / Docker)

Hard-won lessons from task-035..task-046. A replacing agent WILL hit these on day one. They are environment-specific (Windows + Git Bash + Docker Desktop) and not obvious from the code.

1. MSYS path conversion breaks absolute posix paths.
   Native Windows python and `docker compose cp` mangle paths like `/f/GIT/...` into `F:\f\GIT\...` (note the doubled `f`), causing FileNotFoundError.
   Rule: write files using RELATIVE paths (`Path("reports") / task_id / ...`); never pass absolute posix paths to python via environment variables. Bash handles `/f/...` fine for its own redirections; the breakage is when a native (non-MSYS) program receives the path.

2. `git add -A` must NOT use pathspec exclude for ignored files.
   `git add -A -- . :(exclude)reports ...` fails with "The following paths are ignored by one of your .gitignore files", because the pathspec magic forces git to consider ignored files explicitly.
   Rule: use plain `git add -A` (no pathspec). It respects `.gitignore` and does not try to add ignored files. Ensure `.gitignore` covers `.env`, `frontend/node_modules/`, `frontend/dist/`, `reports/`, `logs/`, `backend/certs/*.pem`, `*.log`, `__pycache__/`, `*.pyc`.

3. Get code into the container via stdin, not `docker compose cp`.
   `docker compose cp <host-path> <container>:/tmp/...` breaks on the host path (same MSYS issue).
   Rule: pipe code through stdin: `printf '%s' "$CODE" | docker compose exec -T backend python -` (or `cat script.py | docker compose exec -T backend python -`). No path crosses the MSYS boundary.

4. Rebuild the backend image after ANY backend code change.
   The backend container runs from a built image (no source volume mount for `backend`). Editing `backend/app/**` on the host does NOT affect the running container until you rebuild.
   Rule: after any backend change, run `docker compose up -d --build backend` and wait for `/health`. A task that edits backend code but skips the rebuild reports success while the container runs stale code (this happened in task-041: report said success, but `import app.api.signals_jobs` failed inside the container). Make the in-container import check a HARD gate, not best-effort.

5. `docker exec -T` is invalid; only `docker compose exec -T` accepts `-T`.
   Plain `docker exec` has no `-T` flag (it errors with "unknown shorthand flag: 'T'"). For stdin into a bare `docker exec`, use `-i` (`docker exec -i container python -`).
   Rule: prefer `docker compose exec -T backend ...`; if you must use bare `docker exec`, use `-i`, not `-T`.

Bonus — data idempotency (learned in task-045/046):
- `candles_aggregator` aggregation must be an idempotent upsert: `ON CONFLICT (ticker, timestamp, timeframe) DO UPDATE`. A plain `delete-range + insert` is NOT idempotent here because (a) the 4h bucket expression contains a literal `%` that must be escaped as `%%` for psycopg2 positional params, and (b) `figi` must NOT be in `GROUP BY` (the PK is `(ticker, timestamp, timeframe)`), else duplicate rows are produced. Take `figi` as `(array_agg(figi ORDER BY timestamp DESC))[1]`.
