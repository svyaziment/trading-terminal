#!/usr/bin/env bash
set -u

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

TASK_ID="task-035a-project-db-scan-fix"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PROJECT_ROOT="$(pwd)"
REPORT_DIR_REL="reports/${TASK_ID}"
REPORT_DIR="${PROJECT_ROOT}/${REPORT_DIR_REL}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"
FILE_REPORT_JSON="${REPORT_DIR}/file_scan_report.json"
FILE_REPORT_MD="${REPORT_DIR}/file_scan_report.md"
DB_JSON="${REPORT_DIR}/db_schema.json"
DB_MD="${REPORT_DIR}/db_schema.md"
CURRENT_TREE="${REPORT_DIR}/current_tree.txt"
SCANNER_COPY_REL="${REPORT_DIR_REL}/targeted_project_scanner_fixed.py"

: > "${LOG_TXT}"

if command -v cygpath >/dev/null 2>&1; then
  HOST_REPORT_DIR="$(cygpath -m "${REPORT_DIR}")"
else
  HOST_REPORT_DIR="./${REPORT_DIR_REL}"
fi

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"
log "Project root: ${PROJECT_ROOT}"
log "Report dir: ${REPORT_DIR}"
log "Host report dir: ${HOST_REPORT_DIR}"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  PY=""
fi

if [[ -z "${PY}" ]]; then
  log "ERROR: Python not found"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "python_not_found",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

  exit 0
fi

if [[ ! -f scripts/targeted_project_scanner.py ]]; then
  log "ERROR: scripts/targeted_project_scanner.py not found"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "targeted_project_scanner_not_found",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

  exit 0
fi

if [[ ! -f scripts/db_schema_scanner.py ]]; then
  log "ERROR: scripts/db_schema_scanner.py not found"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "db_schema_scanner_not_found",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

  exit 0
fi

log "Patching targeted_project_scanner.py copy"

if ! "${PY}" - "${SCANNER_COPY_REL}" "${TASK_ID}" <<'PY_PATCH' >>"${LOG_TXT}" 2>&1
import re
import sys
from pathlib import Path

src = Path("scripts/targeted_project_scanner.py")
dst = Path(sys.argv[1])
task_id = sys.argv[2]

dst.parent.mkdir(parents=True, exist_ok=True)

text = src.read_text(encoding="utf-8", errors="ignore")

text = re.sub(
    r'TASK_ID\s*=\s*["\']task-031c-targeted-project-scan["\']',
    'TASK_ID = "' + task_id + '"',
    text,
    count=1,
)

extra_patterns = "\n    # Added by task-035a: frontend and scripts\n"
extra_patterns += '    "frontend/package.json",\n'
extra_patterns += '    "frontend/tsconfig.json",\n'
extra_patterns += '    "frontend/vite.config.js",\n'
extra_patterns += '    "frontend/vite.config.ts",\n'
extra_patterns += '    "frontend/tailwind.config.js",\n'
extra_patterns += '    "frontend/postcss.config.js",\n'
extra_patterns += '    "frontend/index.html",\n'
extra_patterns += '    "frontend/.gitignore",\n'
extra_patterns += '    "frontend/src/**/*.ts",\n'
extra_patterns += '    "frontend/src/**/*.tsx",\n'
extra_patterns += '    "frontend/src/**/*.css",\n'
extra_patterns += '    "scripts/*.sh",\n'
extra_patterns += '    "scripts/*.py",\n'

pattern = re.compile(r'(INCLUDE_PATTERNS\s*=\s*\[)(.*?)(\r?\n\])', re.S)

def replace_include_patterns(match):
    return match.group(1) + match.group(2) + extra_patterns + match.group(3)

new_text = pattern.sub(replace_include_patterns, text, count=1)

if new_text == text:
    print("WARN: INCLUDE_PATTERNS was not patched")
else:
    print("OK: INCLUDE_PATTERNS patched")

dst.write_text(new_text, encoding="utf-8")
print("PATCH_DONE")
PY_PATCH
then
  log "WARN: Python patch failed, using cp + sed fallback"
  cp scripts/targeted_project_scanner.py "${SCANNER_COPY_REL}" >>"${LOG_TXT}" 2>&1 || true
  sed -i "s/task-031c-targeted-project-scan/${TASK_ID}/g" "${SCANNER_COPY_REL}" >>"${LOG_TXT}" 2>&1 || true
fi

if grep -q "frontend/package.json" "${SCANNER_COPY_REL}" 2>/dev/null; then
  log "Scanner copy includes frontend patterns"
else
  log "WARN: scanner copy does not include frontend patterns"
fi

log "Running targeted project scanner"
"${PY}" "${SCANNER_COPY_REL}" >>"${LOG_TXT}" 2>&1 || log "WARN: targeted project scanner exited non-zero"

if [[ -f "${REPORT_DIR}/report.json" ]]; then
  mv "${REPORT_DIR}/report.json" "${FILE_REPORT_JSON}" >>"${LOG_TXT}" 2>&1 || true
fi

if [[ -f "${REPORT_DIR}/report.md" ]]; then
  mv "${REPORT_DIR}/report.md" "${FILE_REPORT_MD}" >>"${LOG_TXT}" 2>&1 || true
fi

log "Creating current_tree.txt"

{
  echo "# task: ${TASK_ID}"
  echo "# generated_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo

  echo "# git branch"
  git branch --show-current 2>/dev/null || echo "unknown"
  echo

  echo "# git status --short"
  git status --short 2>/dev/null || true
  echo

  echo "# root files"
  find . -maxdepth 1 -type f -not -name ".env" 2>/dev/null | sort
  echo

  echo "# backend"
  find backend -type f \
    -not -path "*/__pycache__/*" \
    -not -path "*/certs/*" \
    -not -path "*/.pytest_cache/*" \
    2>/dev/null | sort
  echo

  echo "# frontend"
  if [[ -d frontend ]]; then
    find frontend -type f \
      -not -path "*/node_modules/*" \
      -not -path "*/dist/*" \
      2>/dev/null | sort
  else
    echo "frontend/ not found"
  fi
  echo

  echo "# scripts"
  find scripts -type f 2>/dev/null | sort
  echo

  echo "# docs"
  find docs -type f 2>/dev/null | sort
  echo

  echo "# config"
  find config -type f 2>/dev/null | sort
} > "${CURRENT_TREE}" 2>>"${LOG_TXT}" || true

DB_SCHEMA="${MARKET_DATA_SCHEMA:-trading}"
CONTAINER_NAME="trading-terminal-backend"

log "Starting backend container for DB scan"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose up -d --no-build backend >>"${LOG_TXT}" 2>&1 || true
  sleep 5

  RUNNING="$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null || echo false)"

  if [[ "${RUNNING}" != "true" ]]; then
    log "Backend container is not running, trying build"
    docker compose build backend >>"${LOG_TXT}" 2>&1 || true
    docker compose up -d --no-build backend >>"${LOG_TXT}" 2>&1 || true
    sleep 10
  fi

  log "Copying db_schema_scanner.py into backend container"
  docker compose cp scripts/db_schema_scanner.py backend:/tmp/db_schema_scanner.py >>"${LOG_TXT}" 2>&1 || \
    docker cp scripts/db_schema_scanner.py "${CONTAINER_NAME}:/tmp/db_schema_scanner.py" >>"${LOG_TXT}" 2>&1 || true

  log "Running DB schema scanner inside backend container"
  docker compose exec -T -e PYTHONPATH=/app backend python /tmp/db_schema_scanner.py \
    --schema "${DB_SCHEMA}" \
    --output-dir "/tmp/${TASK_ID}" \
    >>"${LOG_TXT}" 2>&1 || log "WARN: DB schema scanner exec failed"

  log "Copying DB schema artifacts from container"
  docker compose cp "backend:/tmp/${TASK_ID}/db_schema.json" "${HOST_REPORT_DIR}/db_schema.json" >>"${LOG_TXT}" 2>&1 || \
    docker cp "${CONTAINER_NAME}:/tmp/${TASK_ID}/db_schema.json" "${HOST_REPORT_DIR}/db_schema.json" >>"${LOG_TXT}" 2>&1 || true

  docker compose cp "backend:/tmp/${TASK_ID}/db_schema.md" "${HOST_REPORT_DIR}/db_schema.md" >>"${LOG_TXT}" 2>&1 || \
    docker cp "${CONTAINER_NAME}:/tmp/${TASK_ID}/db_schema.md" "${HOST_REPORT_DIR}/db_schema.md" >>"${LOG_TXT}" 2>&1 || true

  if [[ ! -f "${DB_JSON}" ]]; then
    log "WARN: db_schema.json was not copied"
    docker compose logs --tail=200 backend > "${REPORT_DIR}/backend_logs.txt" 2>&1 || true
  fi
else
  log "WARN: Docker Compose is not available, DB scan skipped"
fi

log "Combining report"

REPORT_DIR_REL="${REPORT_DIR_REL}" \
TASK_ID="${TASK_ID}" \
STARTED_AT="${STARTED_AT}" \
"${PY}" - <<'PY_COMBINE'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

report_dir = Path(os.environ["REPORT_DIR_REL"])
task_id = os.environ["TASK_ID"]
started_at = os.environ["STARTED_AT"]

file_report_path = report_dir / "file_scan_report.json"
db_schema_path = report_dir / "db_schema.json"
report_json_path = report_dir / "report.json"
report_md_path = report_dir / "report.md"

file_report = None
db_schema = None

if file_report_path.exists():
    try:
        file_report = json.loads(file_report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        file_report = {"error": f"Cannot read file_scan_report.json: {exc}"}

if db_schema_path.exists():
    try:
        db_schema = json.loads(db_schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        db_schema = {"error": f"Cannot read db_schema.json: {exc}"}

file_ok = False
if isinstance(file_report, dict):
    selected_files_count = file_report.get("scan", {}).get("selected_files_count", 0)
    file_ok = selected_files_count > 0

db_ok = False
tables_summary = []
analytics_summary = {}

if isinstance(db_schema, dict):
    tables = db_schema.get("tables", [])
    db_ok = (
        db_schema.get("status") == "success"
        and isinstance(tables, list)
        and len(tables) > 0
    )

    for table in tables:
        tables_summary.append(
            {
                "name": table.get("name"),
                "total_rows": table.get("total_rows"),
                "column_count": table.get("column_count"),
            }
        )

    analytics_summary = db_schema.get("analytics_summary", {})

if file_ok and db_ok:
    status = "success"
elif file_ok or db_ok:
    status = "needs_human"
else:
    status = "failed"

combined = {
    "task_id": task_id,
    "status": status,
    "started_at": started_at,
    "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "file_scan": {
        "status": file_report.get("status") if isinstance(file_report, dict) else None,
        "selected_files_count": file_report.get("scan", {}).get("selected_files_count")
        if isinstance(file_report, dict)
        else None,
        "summary": file_report.get("scan", {}).get("summary")
        if isinstance(file_report, dict)
        else None,
        "critical_files": file_report.get("scan", {}).get("critical_files")
        if isinstance(file_report, dict)
        else None,
        "important_files": file_report.get("scan", {}).get("important_files")
        if isinstance(file_report, dict)
        else None,
        "git_info": file_report.get("git_info") if isinstance(file_report, dict) else None,
        "artifacts": {
            "file_scan_report": f"reports/{task_id}/file_scan_report.json",
            "file_scan_report_md": f"reports/{task_id}/file_scan_report.md",
            "context_md": f"reports/{task_id}/context.md",
            "tree_txt": f"reports/{task_id}/tree.txt",
            "current_tree_txt": f"reports/{task_id}/current_tree.txt",
        },
    },
    "db_scan": {
        "status": db_schema.get("status") if isinstance(db_schema, dict) else None,
        "schema": db_schema.get("schema") if isinstance(db_schema, dict) else None,
        "scan_time": db_schema.get("scan_time") if isinstance(db_schema, dict) else None,
        "table_count": db_schema.get("table_count") if isinstance(db_schema, dict) else None,
        "tables_summary": tables_summary,
        "analytics_summary": analytics_summary,
        "artifacts": {
            "db_schema_json": f"reports/{task_id}/db_schema.json",
            "db_schema_md": f"reports/{task_id}/db_schema.md",
        },
    },
    "next_action": "Send report.json. If needed, send db_schema.md and current_tree.txt.",
}

report_json_path.write_text(
    json.dumps(combined, ensure_ascii=False, indent=2, default=str),
    encoding="utf-8",
)

md = []
md.append(f"# {task_id}")
md.append("")
md.append(f"Status: {status}")
md.append("")
md.append("## File scan")
md.append("")
md.append(f"Status: {combined['file_scan']['status']}")
md.append(f"Selected files: {combined['file_scan']['selected_files_count']}")
md.append("")
md.append("## DB scan")
md.append("")
md.append(f"Status: {combined['db_scan']['status']}")
md.append(f"Schema: {combined['db_scan']['schema']}")
md.append(f"Tables: {combined['db_scan']['table_count']}")
md.append("")
md.append("Tables summary:")
md.append("")
md.append("name | total_rows | column_count")
md.append("--- | --- | ---")

for table in tables_summary:
    md.append(f"{table.get('name', '')} | {table.get('total_rows', '')} | {table.get('column_count', '')}")

md.append("")
md.append("Artifacts:")
md.append("")
md.append(f"- reports/{task_id}/report.json")
md.append(f"- reports/{task_id}/db_schema.json")
md.append(f"- reports/{task_id}/db_schema.md")
md.append(f"- reports/{task_id}/file_scan_report.json")
md.append(f"- reports/{task_id}/current_tree.txt")
md.append("")

report_md_path.write_text("\n".join(md), encoding="utf-8")
PY_COMBINE

log "Report JSON: ${REPORT_JSON}"
log "Report MD: ${REPORT_MD}"
log "DB schema JSON: ${DB_JSON}"
log "DB schema MD: ${DB_MD}"
log "Done"

exit 0
