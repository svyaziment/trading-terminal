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
