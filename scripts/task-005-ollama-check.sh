#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-005-ollama-check"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

# Required models, comma-separated.
# If you want to check only 14b, use:
# REQUIRED_MODELS="qwen2.5-coder:14b"
REQUIRED_MODELS="qwen2.5-coder:7b,qwen2.5-coder:14b"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "Required models: $REQUIRED_MODELS"

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

echo "Creating Ollama check script..."

cat > scripts/check_ollama.py <<'PY_EOF'
import json
import os
import pathlib
import sys
import urllib.request

base = os.environ.get("OLLAMA_BASE_URL", "").rstrip("/")
required_raw = os.environ.get("REQUIRED_MODELS", "qwen2.5-coder:7b")
report_dir = pathlib.Path(os.environ.get("REPORT_DIR", "reports/task-005-ollama-check"))
report_dir.mkdir(parents=True, exist_ok=True)

required_models = [
    item.strip()
    for item in required_raw.split(",")
    if item.strip()
]

result = {
    "ollama_base_url": base,
    "required_models": required_models,
    "status": "failed",
    "ollama_version": "",
    "models_available": [],
    "models_present": [],
    "models_missing": required_models,
    "all_required_models_present": False,
    "error": "",
}

try:
    if not base:
        raise RuntimeError("OLLAMA_BASE_URL is empty")

    with urllib.request.urlopen(base + "/api/version", timeout=10) as resp:
        version_data = json.load(resp)
        result["ollama_version"] = str(version_data.get("version", ""))

    with urllib.request.urlopen(base + "/api/tags", timeout=30) as resp:
        tags_data = json.load(resp)
        models = tags_data.get("models", []) or []

        names = []
        for model in models:
            name = model.get("name", "")
            if name:
                names.append(name)

        result["models_available"] = names

        present = []
        missing = []

        for required_model in required_models:
            if required_model in names:
                present.append(required_model)
            else:
                missing.append(required_model)

        result["models_present"] = present
        result["models_missing"] = missing
        result["all_required_models_present"] = len(missing) == 0

    if result["all_required_models_present"]:
        result["status"] = "success"
    else:
        result["status"] = "needs_human"
        missing_commands = " && ".join(
            f"ollama pull {name}"
            for name in result["models_missing"]
        )
        result["error"] = (
            "Missing required models: "
            + ", ".join(result["models_missing"])
            + ". Run on host: "
            + missing_commands
        )

except Exception as exc:
    result["status"] = "failed"
    result["error"] = str(exc)

(report_dir / "container_check.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("OLLAMA_CHECK_STATUS=" + result["status"])
print("OLLAMA_VERSION=" + result["ollama_version"])
print("REQUIRED_MODELS=" + ",".join(result["required_models"]))
print("MODELS_AVAILABLE=" + ",".join(result["models_available"]))
print("MODELS_PRESENT=" + ",".join(result["models_present"]))
print("MODELS_MISSING=" + ",".join(result["models_missing"]))
print("ALL_REQUIRED_MODELS_PRESENT=" + ("true" if result["all_required_models_present"] else "false"))
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

if [ -f "scripts/check_ollama.py" ]; then
  add_check "file_exists" "scripts/check_ollama.py" "true"
  echo "OK: scripts/check_ollama.py exists"
else
  add_check "file_exists" "scripts/check_ollama.py" "false"
  echo "FAIL: scripts/check_ollama.py missing"
fi

RUN_OUTPUT=""
OLLAMA_CHECK_STATUS=""
OLLAMA_VERSION=""
REQUIRED_MODELS_FROM_CONTAINER=""
MODELS_AVAILABLE=""
MODELS_PRESENT=""
MODELS_MISSING=""
ALL_REQUIRED_MODELS_PRESENT="false"
ERROR_MESSAGE=""

if [ "$STATUS" = "success" ]; then
  echo "Running Ollama check inside container..."

  if RUN_OUTPUT="$(docker compose run --rm -T \
      -e REQUIRED_MODELS="$REQUIRED_MODELS" \
      -e REPORT_DIR="reports/$TASK_ID" \
      agent python scripts/check_ollama.py 2>&1)"; then
    add_check "docker_run_check" "agent" "true"
    echo "OK: container Ollama check executed"
  else
    add_check "docker_run_check" "agent" "false" "docker compose run failed"
    add_error "Run manually: docker compose run --rm -T -e REQUIRED_MODELS=$REQUIRED_MODELS -e REPORT_DIR=reports/$TASK_ID agent python scripts/check_ollama.py"
    echo "FAIL: container Ollama check failed"
  fi

  echo "Container output:"
  echo "$RUN_OUTPUT" || true
fi

if [ -n "$RUN_OUTPUT" ]; then
  OLLAMA_CHECK_STATUS="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^OLLAMA_CHECK_STATUS=' | head -n1 | cut -d'=' -f2- || true)"
  OLLAMA_VERSION="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^OLLAMA_VERSION=' | head -n1 | cut -d'=' -f2- || true)"
  REQUIRED_MODELS_FROM_CONTAINER="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^REQUIRED_MODELS=' | head -n1 | cut -d'=' -f2- || true)"
  MODELS_AVAILABLE="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^MODELS_AVAILABLE=' | head -n1 | cut -d'=' -f2- || true)"
  MODELS_PRESENT="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^MODELS_PRESENT=' | head -n1 | cut -d'=' -f2- || true)"
  MODELS_MISSING="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^MODELS_MISSING=' | head -n1 | cut -d'=' -f2- || true)"
  ALL_REQUIRED_MODELS_PRESENT="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^ALL_REQUIRED_MODELS_PRESENT=' | head -n1 | cut -d'=' -f2- || true)"
  ERROR_MESSAGE="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^ERROR_MESSAGE=' | head -n1 | cut -d'=' -f2- || true)"
fi

if [ -z "$ALL_REQUIRED_MODELS_PRESENT" ]; then
  ALL_REQUIRED_MODELS_PRESENT="false"
fi

ERROR_MESSAGE_SAFE="$(printf '%s' "$ERROR_MESSAGE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
MODELS_AVAILABLE_SAFE="$(printf '%s' "$MODELS_AVAILABLE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
MODELS_PRESENT_SAFE="$(printf '%s' "$MODELS_PRESENT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
MODELS_MISSING_SAFE="$(printf '%s' "$MODELS_MISSING" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
REQUIRED_MODELS_SAFE="$(printf '%s' "$REQUIRED_MODELS" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

if [ "$STATUS" = "success" ]; then
  if [ -f "$REPORT_DIR/container_check.json" ]; then
    add_check "container_check_json" "reports/$TASK_ID/container_check.json" "true"
    echo "OK: container_check.json created"
  else
    add_check "container_check_json" "reports/$TASK_ID/container_check.json" "false"
    echo "FAIL: container_check.json missing"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ -n "$OLLAMA_CHECK_STATUS" ]; then
    add_check "ollama_check_status" "$OLLAMA_CHECK_STATUS" "true"
    echo "OK: Ollama check status received: $OLLAMA_CHECK_STATUS"
  else
    add_check "ollama_check_status" "OLLAMA_CHECK_STATUS" "false" "Empty Ollama check status"
    add_error "Ollama check did not return status. See log."
    echo "FAIL: empty Ollama check status"
  fi
fi

if [ "$STATUS" = "success" ] || [ "$STATUS" = "needs_human" ]; then
  if [ "$OLLAMA_CHECK_STATUS" = "success" ] || [ "$OLLAMA_CHECK_STATUS" = "needs_human" ]; then
    if [ -n "$OLLAMA_VERSION" ]; then
      add_check "ollama_version" "$OLLAMA_VERSION" "true"
      echo "OK: Ollama version detected: $OLLAMA_VERSION"
    else
      add_check "ollama_version" "OLLAMA_VERSION" "false" "Ollama version empty"
      echo "FAIL: Ollama version empty"
    fi

    add_check "ollama_api_available" "OLLAMA_BASE_URL" "true"
    echo "OK: Ollama API available"

    IFS=',' read -ra required_arr <<< "$REQUIRED_MODELS"

    for model in "${required_arr[@]}"; do
      model_present="false"

      if [ -n "$MODELS_PRESENT" ]; then
        IFS=',' read -ra present_arr <<< "$MODELS_PRESENT"
        for present_model in "${present_arr[@]}"; do
          if [ "$present_model" = "$model" ]; then
            model_present="true"
          fi
        done
      fi

      if [ "$model_present" = "true" ]; then
        add_check "model_present" "$model" "true"
        echo "OK: model present: $model"
      else
        add_check "model_present" "$model" "false" "Model not found" "needs_human"
        echo "FAIL: model not found: $model"
      fi
    done

    if [ "$ALL_REQUIRED_MODELS_PRESENT" = "true" ]; then
      echo "OK: all required models present"
    else
      STATUS="needs_human"
      if [ -n "$ERROR_MESSAGE_SAFE" ]; then
        add_error "$ERROR_MESSAGE_SAFE"
      else
        add_error "Missing required models: $REQUIRED_MODELS_SAFE"
      fi
      echo "NEEDS HUMAN: missing required models: $MODELS_MISSING_SAFE"
    fi

  elif [ "$OLLAMA_CHECK_STATUS" = "failed" ]; then
    STATUS="needs_human"
    add_check "ollama_connection" "OLLAMA_BASE_URL" "false" "$ERROR_MESSAGE_SAFE" "needs_human"
    add_error "Cannot reach Ollama or bad response. Check Ollama on host. Error: $ERROR_MESSAGE_SAFE"
    echo "FAIL: Ollama check failed: $ERROR_MESSAGE_SAFE"
  else
    STATUS="failed"
    add_check "ollama_check_status" "OLLAMA_CHECK_STATUS" "false" "Unknown Ollama check status"
    add_error "Unknown Ollama check status. See log."
    echo "FAIL: unknown Ollama check status"
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
    "required_models": "$REQUIRED_MODELS_SAFE",
    "ollama_check_status": "$OLLAMA_CHECK_STATUS",
    "ollama_version": "$OLLAMA_VERSION",
    "models_available": "$MODELS_AVAILABLE_SAFE",
    "models_present": "$MODELS_PRESENT_SAFE",
    "models_missing": "$MODELS_MISSING_SAFE",
    "all_required_models_present": $ALL_REQUIRED_MODELS_PRESENT
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "scripts/check_ollama.py",
    "reports/$TASK_ID/container_check.json"
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

Required models: **$REQUIRED_MODELS_SAFE**  
Ollama check status: **$OLLAMA_CHECK_STATUS**  
Ollama version: **$OLLAMA_VERSION**  
All required models present: **$ALL_REQUIRED_MODELS_PRESENT**

Models available: **$MODELS_AVAILABLE_SAFE**  
Models present: **$MODELS_PRESENT_SAFE**  
Models missing: **$MODELS_MISSING_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
