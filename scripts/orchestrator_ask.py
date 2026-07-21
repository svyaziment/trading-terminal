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
