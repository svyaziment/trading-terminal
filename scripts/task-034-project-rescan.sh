#!/usr/bin/env bash
set -u

TASK_ID="task-034-project-rescan"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PROJECT_ROOT="$(pwd)"
REPORT_DIR="${PROJECT_ROOT}/reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"
SCANNER_COPY="${REPORT_DIR}/targeted_project_scanner_task034.py"
CURRENT_TREE="${REPORT_DIR}/current_tree.txt"

: > "${LOG_TXT}"

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"
log "Project root: ${PROJECT_ROOT}"

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
  "stage": "scanner_not_found",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

  exit 0
fi

log "Creating patched scanner copy"

if ! "${PY}" - "${SCANNER_COPY}" <<'PY_PATCH' >>"${LOG_TXT}" 2>&1
import re
import sys
from pathlib import Path

src_path = Path("scripts/targeted_project_scanner.py")
dst_path = Path(sys.argv[1])

text = src_path.read_text(encoding="utf-8", errors="ignore")

# New task id
text = re.sub(
    r'TASK_ID\s*=\s*["\']task-031c-targeted-project-scan["\']',
    'TASK_ID = "task-034-project-rescan"',
    text,
    count=1,
)

# Additional include patterns
extra_patterns = '''
    # Added by task-034: frontend and scripts
    "frontend/package.json",
    "frontend/tsconfig.json",
    "frontend/vite.config.js",
    "frontend/vite.config.ts",
    "frontend/tailwind.config.js",
    "frontend/postcss.config.js",
    "frontend/index.html",
    "frontend/.gitignore",
    "frontend/src/**/*.ts",
    "frontend/src/**/*.tsx",
    "frontend/src/**/*.css",
    "scripts/*.sh",
    "scripts/*.py",
'''

pattern = re.compile(r'(INCLUDE_PATTERNS\s*=\s*\[)(.*?)(\r?\n\])', re.S)

def replace_include_patterns(match):
    return match.group(1) + match.group(2) + extra_patterns + match.group(3)

new_text = pattern.sub(replace_include_patterns, text, count=1)

if new_text == text:
    print("WARN: INCLUDE_PATTERNS was not patched")
else:
    print("OK: INCLUDE_PATTERNS patched")

dst_path.write_text(new_text, encoding="utf-8")
print("PATCH_DONE")
PY_PATCH
then
  log "WARN: Python patch failed, copying original scanner and replacing task id with sed"
  cp scripts/targeted_project_scanner.py "${SCANNER_COPY}" >>"${LOG_TXT}" 2>&1 || true
  sed -i 's/task-031c-targeted-project-scan/task-034-project-rescan/g' "${SCANNER_COPY}" >>"${LOG_TXT}" 2>&1 || true
fi

if [[ ! -f "${SCANNER_COPY}" ]]; then
  log "ERROR: cannot create scanner copy"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "scanner_copy_failed",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

  exit 0
fi

if grep -q "frontend/package.json" "${SCANNER_COPY}"; then
  log "Scanner copy includes frontend patterns"
else
  log "WARN: scanner copy does not include frontend patterns"
fi

log "Running targeted project scanner"
"${PY}" "${SCANNER_COPY}" >>"${LOG_TXT}" 2>&1
SCANNER_EXIT=$?
log "Scanner exit code: ${SCANNER_EXIT}"

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
      -not -path "*/.pytest_cache/*" \
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

if [[ -f "${REPORT_JSON}" ]] && "${PY}" -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "${REPORT_JSON}" 2>>"${LOG_TXT}"; then
  log "Augmenting report.json"

  "${PY}" - "${REPORT_JSON}" "${TASK_ID}" <<'PY_AUGMENT' >>"${LOG_TXT}" 2>&1 || log "WARN: report augment failed"
import json
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
task_id = sys.argv[2]

data = json.loads(report_path.read_text(encoding="utf-8"))

data["task_id"] = task_id
data["rescan"] = {
    "original_scanner": "scripts/targeted_project_scanner.py",
    "frontend_included": True,
    "scripts_included": True,
    "generated_by": "task-034-project-rescan",
}

artifacts = data.get("artifacts", {})
if not artifacts and isinstance(data.get("scan"), dict):
    artifacts = data["scan"].get("artifacts", {})

artifacts["current_tree_txt"] = f"reports/{task_id}/current_tree.txt"

if isinstance(data.get("scan"), dict):
    data["scan"]["artifacts"] = artifacts
else:
    data["artifacts"] = artifacts

data["next_action"] = "Send report.json and current_tree.txt. Attach context.md if it is not too large."

report_path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("AUGMENT_DONE")
PY_AUGMENT
else
  log "ERROR: scanner did not produce valid report.json"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "invalid_or_missing_report",
  "scanner_exit_code": ${SCANNER_EXIT},
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "next_action": "Check ${LOG_TXT}"
}
JSON

  cat > "${REPORT_MD}" <<MD
# ${TASK_ID}

Status: failed
Stage: invalid_or_missing_report
Scanner exit code: ${SCANNER_EXIT}

Check:

${LOG_TXT}
${CURRENT_TREE}
MD
fi

log "Report JSON: ${REPORT_JSON}"
log "Current tree: ${CURRENT_TREE}"
log "Done"

exit 0
