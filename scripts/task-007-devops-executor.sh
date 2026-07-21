#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-007-devops-executor"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

TASK_FILE="reports/task-006-orchestrator-prompt/orchestrator_task.json"
TARGET_FILE="docs/orchestrator/first-task.md"

EXECUTE_MODE="${EXECUTE_MODE:-dry-run}"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "Task file: $TASK_FILE"
echo "Execute mode: $EXECUTE_MODE"

CHECKS_JSON=""
CHECKS_MD=""
STATUS="success"

ERRORS_JSON_ENTRIES=""
ERRORS_MD_LINES=""

add_check() {
  local name="$1"
  local path="$2"
  local ok="$3"
  local message="${4:-}"
  local severity="${5:-failed}"

  local check_status="failed"

  if [ "$ok" = "true" ]; then
    check_status="passed"
  fi

  if [ "$check_status" != "passed" ]; then
    if [ "$severity" = "needs_human" ]; then
      if [ "$STATUS" = "success" ]; then
        STATUS="needs_human"
      fi
    else
      STATUS="failed"
    fi
  fi

  local entry
  entry="$(printf '    {\n      "name": "%s",\n      "path": "%s",\n      "status": "%s",\n      "message": "%s"\n    }' "$name" "$path" "$check_status" "$message")"

  if [ -z "$CHECKS_JSON" ]; then
    CHECKS_JSON="$entry"
  else
    CHECKS_JSON="$CHECKS_JSON,
$entry"
  fi

  CHECKS_MD="$CHECKS_MD
- $check_status: $name \`$path\` $message"
}

add_error() {
  local message="$1"

  local entry
  entry="$(printf '    "%s"' "$message")"

  if [ -z "$ERRORS_JSON_ENTRIES" ]; then
    ERRORS_JSON_ENTRIES="$entry"
  else
    ERRORS_JSON_ENTRIES="$ERRORS_JSON_ENTRIES,
$entry"
  fi

  ERRORS_MD_LINES="$ERRORS_MD_LINES
- $message"
}

echo "Creating DevOps executor script..."

cat > scripts/devops_executor.py <<'PY_EOF'
import json
import os
import pathlib
import sys

task_file = pathlib.Path(
    os.environ.get(
        "TASK_FILE",
        "reports/task-006-orchestrator-prompt/orchestrator_task.json",
    )
)

execute_mode = os.environ.get("EXECUTE_MODE", "dry-run")
allowed_target = "docs/orchestrator/first-task.md"

report_dir = pathlib.Path(
    os.environ.get("REPORT_DIR", "reports/task-007-devops-executor")
)
report_dir.mkdir(parents=True, exist_ok=True)

result = {
    "status": "failed",
    "execute_mode": execute_mode,
    "task_file": str(task_file),
    "task_type": "",
    "to_agent": "",
    "target_path": "",
    "file_already_existed": False,
    "file_created": False,
    "error": "",
}

FORBIDDEN_COMMAND_SUBSTRINGS = [
    "rm ",
    "sudo",
    "curl",
    "wget",
    "docker",
    "kubectl",
    "ssh",
    "scp",
    "eval",
    "exec",
    ">",
    ">>",
    "|",
    ";",
    "&",
    "`",
    "$(",
]


def validate_task(task: dict) -> None:
    required_fields = [
        "to_agent",
        "task_type",
        "target_path",
        "file_content",
        "safe_commands",
        "acceptance_criteria",
    ]

    for field in required_fields:
        if field not in task:
            raise ValueError(f"Missing required field: {field}")

    if task["to_agent"] != "devops":
        raise ValueError("Field to_agent must be devops")

    if task["task_type"] != "create_file":
        raise ValueError("Only task_type create_file is supported")

    if task["target_path"] != allowed_target:
        raise ValueError(
            f"Only target_path {allowed_target} is allowed for this executor"
        )

    if not isinstance(task["file_content"], str):
        raise ValueError("Field file_content must be a string")

    if len(task["file_content"].strip()) == 0:
        raise ValueError("Field file_content must not be empty")

    if not isinstance(task["safe_commands"], list):
        raise ValueError("Field safe_commands must be a list")

    if not isinstance(task["acceptance_criteria"], list):
        raise ValueError("Field acceptance_criteria must be a list")

    for command in task["safe_commands"]:
        if not isinstance(command, str):
            raise ValueError("Each safe_commands item must be a string")

        lowered = command.lower()

        for bad in FORBIDDEN_COMMAND_SUBSTRINGS:
            if bad in lowered:
                raise ValueError(f"Forbidden substring in safe_commands: {bad}")


try:
    if execute_mode not in ("dry-run", "execute"):
        raise ValueError("EXECUTE_MODE must be dry-run or execute")

    if not task_file.exists():
        raise FileNotFoundError(f"Task file not found: {task_file}")

    task = json.loads(task_file.read_text(encoding="utf-8"))
    validate_task(task)

    result["task_type"] = str(task.get("task_type", ""))
    result["to_agent"] = str(task.get("to_agent", ""))
    result["target_path"] = str(task.get("target_path", ""))

    target_path = pathlib.Path(task["target_path"])

    if target_path.is_absolute():
        raise ValueError("target_path must be relative")

    if ".." in target_path.parts:
        raise ValueError("target_path must not contain ..")

    result["file_already_existed"] = target_path.exists()

    if execute_mode == "execute":
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(task["file_content"], encoding="utf-8")
        result["file_created"] = target_path.is_file()

        if result["file_created"]:
            result["status"] = "success"
        else:
            result["status"] = "failed"
            result["error"] = "File was not created"
    else:
        result["status"] = "needs_human"
        result["error"] = (
            "Dry-run only. Review the task and run with EXECUTE_MODE=execute "
            "to create the file."
        )

except Exception as exc:
    result["status"] = "failed"
    result["error"] = str(exc)

(report_dir / "executor_check.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("DEVOPS_STATUS=" + result["status"])
print("EXECUTE_MODE=" + result["execute_mode"])
print("TASK_TYPE=" + result["task_type"])
print("TO_AGENT=" + result["to_agent"])
print("TARGET_PATH=" + result["target_path"])
print("FILE_ALREADY_EXISTED=" + ("true" if result["file_already_existed"] else "false"))
print("FILE_CREATED=" + ("true" if result["file_created"] else "false"))
print("ERROR_MESSAGE=" + result["error"].replace("\n", " "))

sys.exit(0)
PY_EOF

echo "Checking Docker daemon..."

if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  add_check "docker_daemon" "docker" "true"
  echo "OK: docker daemon is running"
else
  add_check "docker_daemon" "docker" "false" "Docker daemon is not running" "needs_human"
  add_error "Start Docker Desktop or Docker daemon, then rerun this script."
  echo "FAIL: docker daemon is not running"
fi

if [ -f "$TASK_FILE" ]; then
  add_check "task_file_exists" "$TASK_FILE" "true"
  echo "OK: task file exists: $TASK_FILE"
else
  add_check "task_file_exists" "$TASK_FILE" "false"
  add_error "Task file not found: $TASK_FILE. Run task-006-orchestrator-prompt first."
  echo "FAIL: task file missing: $TASK_FILE"
fi

if [ -f "scripts/devops_executor.py" ]; then
  add_check "file_exists" "scripts/devops_executor.py" "true"
  echo "OK: scripts/devops_executor.py exists"
else
  add_check "file_exists" "scripts/devops_executor.py" "false"
  echo "FAIL: scripts/devops_executor.py missing"
fi

RUN_OUTPUT=""
DEVOPS_STATUS=""
EXECUTE_MODE_FROM_CONTAINER=""
TASK_TYPE=""
TO_AGENT=""
TARGET_PATH=""
FILE_ALREADY_EXISTED="false"
FILE_CREATED="false"
ERROR_MESSAGE=""

if [ "$STATUS" = "success" ]; then
  echo "Running DevOps executor inside container..."

  if RUN_OUTPUT="$(docker compose run --rm -T \
      -e EXECUTE_MODE="$EXECUTE_MODE" \
      -e TASK_FILE="$TASK_FILE" \
      -e REPORT_DIR="reports/$TASK_ID" \
      agent python scripts/devops_executor.py 2>&1)"; then
    add_check "docker_run_check" "agent" "true"
    echo "OK: container DevOps executor executed"
  else
    add_check "docker_run_check" "agent" "false" "docker compose run failed"
    add_error "Run manually: docker compose run --rm -T -e EXECUTE_MODE=$EXECUTE_MODE -e TASK_FILE=$TASK_FILE -e REPORT_DIR=reports/$TASK_ID agent python scripts/devops_executor.py"
    echo "FAIL: container DevOps executor failed"
  fi

  echo "Container output:"
  echo "$RUN_OUTPUT" || true
fi

if [ -n "$RUN_OUTPUT" ]; then
  DEVOPS_STATUS="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^DEVOPS_STATUS=' | head -n1 | cut -d'=' -f2- || true)"
  EXECUTE_MODE_FROM_CONTAINER="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^EXECUTE_MODE=' | head -n1 | cut -d'=' -f2- || true)"
  TASK_TYPE="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^TASK_TYPE=' | head -n1 | cut -d'=' -f2- || true)"
  TO_AGENT="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^TO_AGENT=' | head -n1 | cut -d'=' -f2- || true)"
  TARGET_PATH="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^TARGET_PATH=' | head -n1 | cut -d'=' -f2- || true)"
  FILE_ALREADY_EXISTED="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^FILE_ALREADY_EXISTED=' | head -n1 | cut -d'=' -f2- || true)"
  FILE_CREATED="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^FILE_CREATED=' | head -n1 | cut -d'=' -f2- || true)"
  ERROR_MESSAGE="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^ERROR_MESSAGE=' | head -n1 | cut -d'=' -f2- || true)"
fi

if [ -z "$FILE_CREATED" ]; then
  FILE_CREATED="false"
fi

if [ -z "$FILE_ALREADY_EXISTED" ]; then
  FILE_ALREADY_EXISTED="false"
fi

ERROR_MESSAGE_SAFE="$(printf '%s' "$ERROR_MESSAGE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
TARGET_PATH_SAFE="$(printf '%s' "$TARGET_PATH" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
TASK_TYPE_SAFE="$(printf '%s' "$TASK_TYPE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
TO_AGENT_SAFE="$(printf '%s' "$TO_AGENT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

if [ "$STATUS" = "success" ]; then
  if [ -f "$REPORT_DIR/executor_check.json" ]; then
    add_check "executor_check_json" "reports/$TASK_ID/executor_check.json" "true"
    echo "OK: executor_check.json created"
  else
    add_check "executor_check_json" "reports/$TASK_ID/executor_check.json" "false"
    echo "FAIL: executor_check.json missing"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ "$DEVOPS_STATUS" = "success" ]; then
    add_check "devops_status" "success" "true"
    echo "OK: DevOps executor status success"
  elif [ "$DEVOPS_STATUS" = "needs_human" ]; then
    STATUS="needs_human"
    add_check "devops_status" "needs_human" "false" "$ERROR_MESSAGE_SAFE" "needs_human"
    add_error "$ERROR_MESSAGE_SAFE"
    echo "NEEDS HUMAN: $ERROR_MESSAGE_SAFE"
  else
    STATUS="failed"
    add_check "devops_status" "DEVOPS_STATUS" "false" "$ERROR_MESSAGE_SAFE"
    add_error "DevOps executor failed: $ERROR_MESSAGE_SAFE"
    echo "FAIL: DevOps executor failed"
  fi
fi

if [ "$STATUS" = "success" ] || [ "$STATUS" = "needs_human" ]; then
  if [ "$EXECUTE_MODE" = "dry-run" ]; then
    add_check "dry_run" "EXECUTE_MODE" "true" "No changes were made"
    echo "OK: dry-run completed, no changes made"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$EXECUTE_MODE" = "execute" ]; then
  if [ -f "$TARGET_FILE" ]; then
    add_check "target_file_exists" "$TARGET_FILE" "true"
    echo "OK: target file exists: $TARGET_FILE"
  else
    add_check "target_file_exists" "$TARGET_FILE" "false"
    echo "FAIL: target file missing: $TARGET_FILE"
  fi

  if [ "$FILE_CREATED" = "true" ]; then
    add_check "file_created" "$TARGET_FILE" "true"
    echo "OK: file created flag is true"
  else
    add_check "file_created" "$TARGET_FILE" "false"
    echo "FAIL: file created flag is false"
  fi
fi

FINISHED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if [ -z "$ERRORS_JSON_ENTRIES" ]; then
  ERRORS_JSON="[]"
else
  ERRORS_JSON="[
$ERRORS_JSON_ENTRIES
  ]"
fi

if [ -z "$ERRORS_MD_LINES" ]; then
  ERRORS_MD="No errors."
else
  ERRORS_MD="$ERRORS_MD_LINES"
fi

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$ROOT_DIR",
    "shell": "bash",
    "execute_mode": "$EXECUTE_MODE",
    "task_file": "$TASK_FILE",
    "devops_status": "$DEVOPS_STATUS",
    "task_type": "$TASK_TYPE_SAFE",
    "to_agent": "$TO_AGENT_SAFE",
    "target_path": "$TARGET_PATH_SAFE",
    "file_already_existed": $FILE_ALREADY_EXISTED,
    "file_created": $FILE_CREATED
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "scripts/devops_executor.py",
    "reports/$TASK_ID/executor_check.json"
  ],
  "errors": $ERRORS_JSON,
  "log_file": "reports/$TASK_ID/log.txt"
}
EOF

cat > "$REPORT_MD" <<EOF
# Report $TASK_ID

Status: **$STATUS**

Started: $STARTED_AT  
Finished: $FINISHED_AT

Execute mode: **$EXECUTE_MODE**  
Task file: **$TASK_FILE**  
DevOps status: **$DEVOPS_STATUS**  
Task type: **$TASK_TYPE_SAFE**  
To agent: **$TO_AGENT_SAFE**  
Target path: **$TARGET_PATH_SAFE**  
File already existed: **$FILE_ALREADY_EXISTED**  
File created: **$FILE_CREATED**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
