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
