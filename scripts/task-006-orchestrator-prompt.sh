#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-006-orchestrator-prompt"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

ORCHESTRATOR_MODEL="${ORCHESTRATOR_MODEL:-qwen2.5-coder:7b}"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "Orchestrator model: $ORCHESTRATOR_MODEL"

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

echo "Creating orchestrator prompt..."

mkdir -p agents/orchestrator

cat > agents/orchestrator/prompt.md <<'PROMPT_EOF'
# Orchestrator Agent

You are the Orchestrator Agent for a trading terminal project.

Project:
- AI-assisted terminal for algorithmic trading on MOEX.
- Broker API: Tinkoff/T-Bank Invest API.
- Stack: Python, FastAPI, PostgreSQL, Redis, React, TypeScript.

Your job:
- decompose user goals into safe tasks for other agents;
- produce machine-readable JSON tasks;
- never execute commands yourself;
- never propose dangerous actions.

Hard rules:
1. Sandbox only.
2. Production trading is disabled.
3. Do not propose commands that delete files.
4. Do not propose sudo, rm, curl, wget, docker, ssh, scp, eval, exec.
5. Do not propose shell redirection symbols: >, >>, |, ;, &, backticks, $().
6. Do not store secrets in files.
7. Return only valid JSON.
8. Do not wrap JSON in markdown code fences.
9. Do not add explanations before or after JSON.

Output JSON schema:

{
  "to_agent": "devops",
  "task_type": "create_file",
  "description": "string",
  "target_path": "docs/orchestrator/first-task.md",
  "file_content": "string",
  "safe_commands": [
    "mkdir -p docs/orchestrator",
    "touch docs/orchestrator/first-task.md"
  ],
  "acceptance_criteria": [
    "string"
  ],
  "warnings": [
    "string"
  ]
}
PROMPT_EOF

echo "Creating orchestrator ask script..."

cat > scripts/orchestrator_ask.py <<'PY_EOF'
import json
import os
import pathlib
import sys
import urllib.request

base = os.environ.get("OLLAMA_BASE_URL", "").rstrip("/")
model = os.environ.get("ORCHESTRATOR_MODEL", "qwen2.5-coder:7b")
report_dir = pathlib.Path(os.environ.get("REPORT_DIR", "reports/task-006-orchestrator-prompt"))
prompt_file = pathlib.Path(os.environ.get("ORCHESTRATOR_PROMPT_FILE", "agents/orchestrator/prompt.md"))

report_dir.mkdir(parents=True, exist_ok=True)

raw_response_path = report_dir / "orchestrator_response.txt"
task_json_path = report_dir / "orchestrator_task.json"

result = {
    "status": "failed",
    "model_used": model,
    "ollama_base_url": base,
    "prompt_file": str(prompt_file),
    "raw_response_received": False,
    "json_valid": False,
    "task_to_agent": "",
    "task_type": "",
    "target_path": "",
    "error": "",
}

USER_PROMPT = """
Create a safe DevOps task.

The task must create a Markdown file:

docs/orchestrator/first-task.md

The file content should say:

# First Orchestrator Task

This file was proposed by the Orchestrator Agent.

It confirms that the orchestrator can generate safe structured tasks for the DevOps Agent.

Requirements:
1. Return only valid JSON.
2. Do not wrap JSON in markdown.
3. to_agent must be devops.
4. task_type must be create_file.
5. target_path must be exactly docs/orchestrator/first-task.md.
6. safe_commands may only include mkdir and touch commands.
7. Do not use dangerous commands.
8. Do not use shell redirection.
"""


def extract_json(text: str):
    text = text.strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    return json.loads(text)


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
        raise ValueError("Field task_type must be create_file")

    if task["target_path"] != "docs/orchestrator/first-task.md":
        raise ValueError("Field target_path must be docs/orchestrator/first-task.md")

    if not isinstance(task["file_content"], str) or len(task["file_content"].strip()) == 0:
        raise ValueError("Field file_content must be a non-empty string")

    if not isinstance(task["safe_commands"], list):
        raise ValueError("Field safe_commands must be a list")

    if not isinstance(task["acceptance_criteria"], list):
        raise ValueError("Field acceptance_criteria must be a list")

    forbidden = [
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

    for command in task["safe_commands"]:
        if not isinstance(command, str):
            raise ValueError("Each safe_commands item must be a string")

        lowered = command.lower()

        for bad in forbidden:
            if bad in lowered:
                raise ValueError(f"Forbidden substring in safe_commands: {bad}")


try:
    if not base:
        raise RuntimeError("OLLAMA_BASE_URL is empty")

    if not prompt_file.exists():
        raise RuntimeError(f"Prompt file not found: {prompt_file}")

    system_prompt = prompt_file.read_text(encoding="utf-8")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": USER_PROMPT,
            },
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
        },
    }

    request = urllib.request.Request(
        base + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=900) as response:
        data = json.load(response)

    content = data.get("message", {}).get("content", "")

    if content:
        result["raw_response_received"] = True

    raw_response_path.write_text(content, encoding="utf-8")

    task = extract_json(content)
    validate_task(task)

    task_json_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result["status"] = "success"
    result["json_valid"] = True
    result["task_to_agent"] = str(task.get("to_agent", ""))
    result["task_type"] = str(task.get("task_type", ""))
    result["target_path"] = str(task.get("target_path", ""))

except Exception as exc:
    result["status"] = "failed"
    result["error"] = str(exc)

(report_dir / "orchestrator_check.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("ORCHESTRATOR_STATUS=" + result["status"])
print("MODEL_USED=" + result["model_used"])
print("RAW_RESPONSE_RECEIVED=" + ("true" if result["raw_response_received"] else "false"))
print("JSON_VALID=" + ("true" if result["json_valid"] else "false"))
print("TASK_TO_AGENT=" + result["task_to_agent"])
print("TASK_TYPE=" + result["task_type"])
print("TARGET_PATH=" + result["target_path"])
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

if [ -f "agents/orchestrator/prompt.md" ]; then
  add_check "file_exists" "agents/orchestrator/prompt.md" "true"
  echo "OK: agents/orchestrator/prompt.md exists"
else
  add_check "file_exists" "agents/orchestrator/prompt.md" "false"
  echo "FAIL: agents/orchestrator/prompt.md missing"
fi

if [ -f "scripts/orchestrator_ask.py" ]; then
  add_check "file_exists" "scripts/orchestrator_ask.py" "true"
  echo "OK: scripts/orchestrator_ask.py exists"
else
  add_check "file_exists" "scripts/orchestrator_ask.py" "false"
  echo "FAIL: scripts/orchestrator_ask.py missing"
fi

RUN_OUTPUT=""
ORCHESTRATOR_STATUS=""
MODEL_USED=""
RAW_RESPONSE_RECEIVED=""
JSON_VALID="false"
TASK_TO_AGENT=""
TASK_TYPE=""
TARGET_PATH=""
ERROR_MESSAGE=""

if [ "$STATUS" = "success" ]; then
  echo "Running orchestrator inside container..."
  echo "This may take some time, especially for 14B model."

  if RUN_OUTPUT="$(docker compose run --rm -T \
      -e ORCHESTRATOR_MODEL="$ORCHESTRATOR_MODEL" \
      -e REPORT_DIR="reports/$TASK_ID" \
      -e ORCHESTRATOR_PROMPT_FILE="agents/orchestrator/prompt.md" \
      agent python scripts/orchestrator_ask.py 2>&1)"; then
    add_check "docker_run_check" "agent" "true"
    echo "OK: container orchestrator check executed"
  else
    add_check "docker_run_check" "agent" "false" "docker compose run failed"
    add_error "Run manually: docker compose run --rm -T -e ORCHESTRATOR_MODEL=$ORCHESTRATOR_MODEL -e REPORT_DIR=reports/$TASK_ID agent python scripts/orchestrator_ask.py"
    echo "FAIL: container orchestrator check failed"
  fi

  echo "Container output:"
  echo "$RUN_OUTPUT" || true
fi

if [ -n "$RUN_OUTPUT" ]; then
  ORCHESTRATOR_STATUS="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^ORCHESTRATOR_STATUS=' | head -n1 | cut -d'=' -f2- || true)"
  MODEL_USED="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^MODEL_USED=' | head -n1 | cut -d'=' -f2- || true)"
  RAW_RESPONSE_RECEIVED="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^RAW_RESPONSE_RECEIVED=' | head -n1 | cut -d'=' -f2- || true)"
  JSON_VALID="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^JSON_VALID=' | head -n1 | cut -d'=' -f2- || true)"
  TASK_TO_AGENT="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^TASK_TO_AGENT=' | head -n1 | cut -d'=' -f2- || true)"
  TASK_TYPE="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^TASK_TYPE=' | head -n1 | cut -d'=' -f2- || true)"
  TARGET_PATH="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^TARGET_PATH=' | head -n1 | cut -d'=' -f2- || true)"
  ERROR_MESSAGE="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^ERROR_MESSAGE=' | head -n1 | cut -d'=' -f2- || true)"
fi

if [ -z "$JSON_VALID" ]; then
  JSON_VALID="false"
fi

ERROR_MESSAGE_SAFE="$(printf '%s' "$ERROR_MESSAGE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
TARGET_PATH_SAFE="$(printf '%s' "$TARGET_PATH" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
MODEL_USED_SAFE="$(printf '%s' "$MODEL_USED" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

if [ "$STATUS" = "success" ]; then
  if [ -f "$REPORT_DIR/orchestrator_check.json" ]; then
    add_check "orchestrator_check_json" "reports/$TASK_ID/orchestrator_check.json" "true"
    echo "OK: orchestrator_check.json created"
  else
    add_check "orchestrator_check_json" "reports/$TASK_ID/orchestrator_check.json" "false"
    echo "FAIL: orchestrator_check.json missing"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ "$ORCHESTRATOR_STATUS" = "success" ]; then
    add_check "orchestrator_status" "success" "true"
    echo "OK: orchestrator status success"
  else
    add_check "orchestrator_status" "ORCHESTRATOR_STATUS" "false" "$ERROR_MESSAGE_SAFE"
    add_error "Orchestrator failed: $ERROR_MESSAGE_SAFE"
    echo "FAIL: orchestrator status is not success"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ "$RAW_RESPONSE_RECEIVED" = "true" ]; then
    add_check "raw_response_received" "orchestrator_response.txt" "true"
    echo "OK: raw response received"
  else
    add_check "raw_response_received" "orchestrator_response.txt" "false"
    echo "FAIL: raw response not received"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ "$JSON_VALID" = "true" ]; then
    add_check "json_valid" "orchestrator_task.json" "true"
    echo "OK: orchestrator returned valid JSON"
  else
    add_check "json_valid" "orchestrator_task.json" "false"
    add_error "Orchestrator did not return valid JSON. See reports/$TASK_ID/orchestrator_response.txt"
    echo "FAIL: orchestrator JSON invalid"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ "$TASK_TO_AGENT" = "devops" ]; then
    add_check "task_to_agent" "devops" "true"
    echo "OK: task addressed to devops"
  else
    add_check "task_to_agent" "TASK_TO_AGENT" "false"
    echo "FAIL: task_to_agent is not devops"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ "$TARGET_PATH" = "docs/orchestrator/first-task.md" ]; then
    add_check "target_path" "$TARGET_PATH" "true"
    echo "OK: target path is correct"
  else
    add_check "target_path" "TARGET_PATH" "false"
    echo "FAIL: target path is incorrect"
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
    "orchestrator_model": "$MODEL_USED_SAFE",
    "orchestrator_status": "$ORCHESTRATOR_STATUS",
    "raw_response_received": $RAW_RESPONSE_RECEIVED,
    "json_valid": $JSON_VALID,
    "task_to_agent": "$TASK_TO_AGENT",
    "task_type": "$TASK_TYPE",
    "target_path": "$TARGET_PATH_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "agents/orchestrator/prompt.md",
    "scripts/orchestrator_ask.py",
    "reports/$TASK_ID/orchestrator_response.txt",
    "reports/$TASK_ID/orchestrator_task.json",
    "reports/$TASK_ID/orchestrator_check.json"
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

Model: **$MODEL_USED_SAFE**  
Orchestrator status: **$ORCHESTRATOR_STATUS**  
Raw response received: **$RAW_RESPONSE_RECEIVED**  
JSON valid: **$JSON_VALID**  
Task to agent: **$TASK_TO_AGENT**  
Task type: **$TASK_TYPE**  
Target path: **$TARGET_PATH_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
