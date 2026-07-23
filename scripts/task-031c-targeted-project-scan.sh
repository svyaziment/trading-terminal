#!/usr/bin/env bash
set -u

TASK_ID="task-031c-targeted-project-scan"
REPORT_DIR="reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

echo "Creating scripts/targeted_project_scanner.py"

cat > scripts/targeted_project_scanner.py <<'PY_EOF'
#!/usr/bin/env python3
"""
Targeted Project Scanner for Trading Terminal.

Сканирует не весь проект, а только файлы, нужные для понимания и разработки:
- signal_generator;
- indicators_manager;
- signal_patterns engine;
- db_manager;
- config_manager;
- API;
- тесты;
- документация;
- инфраструктура.

Не включает секреты, сертификаты, логи, отчёты и тяжёлые артефакты.
"""

import ast
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TASK_ID = "task-031c-targeted-project-scan"
PROJECT_ROOT = Path.cwd()
REPORT_DIR = PROJECT_ROOT / "reports" / TASK_ID
FILES_DIR = REPORT_DIR / "files"
CONTEXT_MD = REPORT_DIR / "context.md"
REPORT_JSON = REPORT_DIR / "report.json"
REPORT_MD = REPORT_DIR / "report.md"
LOG_TXT = REPORT_DIR / "log.txt"
TREE_TXT = REPORT_DIR / "tree.txt"

INCLUDE_PATTERNS = [
    # Analytics / signals / indicators
    "backend/app/analytics/**/*.py",

    # DB
    "backend/app/db/**/*.py",

    # Core / config
    "backend/app/core/**/*.py",

    # API
    "backend/app/api/**/*.py",

    # Main FastAPI app
    "backend/app/main.py",

    # Broker / data loader
    "backend/app/broker/**/*.py",

    # Tests
    "backend/tests/**/*.py",

    # SQL files
    "backend/**/*.sql",

    # Config
    "config/**/*.yaml",
    "config/**/*.yml",
    ".env.example",

    # Docs
    "docs/**/*.md",

    # Infra
    "backend/requirements.txt",
    "requirements.txt",
    "docker-compose.yml",
    "backend/Dockerfile",
    "README.md",
]

CRITICAL_FILES = [
    "backend/app/analytics/indicators_manager.py",
    "backend/app/db/db_manager.py",
    "backend/app/core/config_manager.py",
]

IMPORTANT_FILES = [
    "backend/app/analytics/signal_generator.py",
    "backend/app/analytics/aggregate_candles.py",
    "backend/app/analytics/signal_patterns/engine.py",
    "backend/app/analytics/signal_patterns/base.py",
    "backend/app/main.py",
]

EXCLUDE_PARTS = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "logs",
    "reports",
    "certs",
}

EXCLUDE_FILE_NAMES = {
    ".env",
    "tbank-root.pem",
}

EXCLUDE_SUFFIXES = {
    ".pem",
    ".key",
    ".crt",
    ".p12",
    ".pfx",
    ".log",
}

MAX_CONTENT_CHARS = 200_000


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat()}] {msg}"
    print(line)
    try:
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def should_exclude(path: Path) -> bool:
    try:
        rel = path.relative_to(PROJECT_ROOT)
    except ValueError:
        rel = path

    parts = set(rel.parts)

    if parts & EXCLUDE_PARTS:
        return True

    if path.name in EXCLUDE_FILE_NAMES:
        return True

    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True

    return False


def read_text_safe(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return text, None
    except Exception as exc:
        return None, str(exc)


def decorator_name(node) -> str:
    try:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Call):
            return decorator_name(node.func)
        return type(node).__name__
    except Exception:
        return "unknown"


def base_name(node) -> str:
    try:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            return base_name(node.value)
        if isinstance(node, ast.Call):
            return base_name(node.func)
        return type(node).__name__
    except Exception:
        return "unknown"


def line_count(node) -> int:
    try:
        end = getattr(node, "end_lineno", None) or node.lineno
        return end - node.lineno + 1
    except Exception:
        return 0


def function_info(node) -> dict:
    return {
        "name": node.name,
        "args": [arg.arg for arg in node.args.args],
        "decorators": [decorator_name(d) for d in node.decorator_list],
        "docstring": ast.get_docstring(node),
        "line_count": line_count(node),
    }


def analyze_python(path: Path, text: str) -> dict:
    result = {
        "imports": [],
        "classes": [],
        "functions": [],
        "lines_of_code": 0,
        "todos": [],
        "parse_error": None,
    }

    lines = text.splitlines()
    result["lines_of_code"] = len(lines)

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        if stripped.startswith("# TODO") or stripped.startswith("#TODO"):
            result["todos"].append({
                "line": i,
                "type": "TODO",
                "text": stripped.lstrip("#").strip(),
            })
        elif stripped.startswith("# FIXME") or stripped.startswith("#FIXME"):
            result["todos"].append({
                "line": i,
                "type": "FIXME",
                "text": stripped.lstrip("#").strip(),
            })

    try:
        tree = ast.parse(text)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result["imports"].append({
                        "module": alias.name,
                        "asname": alias.asname,
                        "level": 0,
                    })
            elif isinstance(node, ast.ImportFrom):
                result["imports"].append({
                    "module": node.module or "",
                    "level": node.level,
                })
            elif isinstance(node, ast.ClassDef):
                class_info = {
                    "name": node.name,
                    "bases": [base_name(base) for base in node.bases],
                    "decorators": [decorator_name(d) for d in node.decorator_list],
                    "docstring": ast.get_docstring(node),
                    "methods": [],
                }

                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        class_info["methods"].append(function_info(item))

                result["classes"].append(class_info)

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result["functions"].append(function_info(node))

    except SyntaxError as exc:
        result["parse_error"] = f"SyntaxError at line {exc.lineno}: {exc.msg}"
    except Exception as exc:
        result["parse_error"] = str(exc)

    return result


def analyze_sql(path: Path, text: str) -> dict:
    result = {
        "lines_of_code": len(text.splitlines()),
        "query_type": "OTHER",
        "tables": [],
        "parameters": [],
        "has_cte": False,
        "has_join": False,
        "has_group_by": False,
        "has_order_by": False,
        "has_limit": False,
    }

    content_lower = text.lower()
    stripped = content_lower.strip()

    for query_type in ["select", "insert", "update", "delete", "create", "drop", "alter", "with"]:
        if stripped.startswith(query_type):
            result["query_type"] = query_type.upper()
            break

    result["has_cte"] = "with" in content_lower
    result["has_join"] = "join" in content_lower
    result["has_group_by"] = "group by" in content_lower
    result["has_order_by"] = "order by" in content_lower
    result["has_limit"] = "limit" in content_lower

    table_pattern = r"(?:create\s+table|from|join|into|update|drop\s+table|alter\s+table)\s+([a-zA-Z_][a-zA-Z0-9_.]*)"
    tables = re.findall(table_pattern, content_lower)
    result["tables"] = sorted(set(tables))

    param_pattern = r"%\(([a-zA-Z_][a-zA-Z0-9_]*)\)s"
    params = re.findall(param_pattern, text)
    result["parameters"] = sorted(set(params))

    return result


def fence_lang(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".py": "python",
        ".sql": "sql",
        ".md": "markdown",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".txt": "text",
        ".toml": "toml",
        ".cfg": "ini",
        ".ini": "ini",
        ".sh": "bash",
        ".dockerfile": "dockerfile",
    }

    if path.name.lower() == "dockerfile":
        return "dockerfile"

    return mapping.get(suffix, "text")


def process_file(path: Path):
    rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")

    record = {
        "path": rel,
        "name": path.name,
        "suffix": path.suffix,
        "size_bytes": None,
        "modified": None,
        "content_chars": 0,
        "included_content_chars": 0,
        "content_truncated": False,
        "copied_to": None,
    }

    try:
        stat = path.stat()
        record["size_bytes"] = stat.st_size
        record["modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
    except Exception as exc:
        record["stat_error"] = str(exc)

    text, read_error = read_text_safe(path)

    if read_error:
        record["read_error"] = read_error
        return record, None

    record["content_chars"] = len(text)

    suffix = path.suffix.lower()

    if suffix == ".py":
        record["analysis"] = analyze_python(path, text)
    elif suffix == ".sql":
        record["analysis"] = analyze_sql(path, text)

    try:
        dest = FILES_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        record["copied_to"] = str(dest.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except Exception as exc:
        record["copy_error"] = str(exc)

    if len(text) > MAX_CONTENT_CHARS:
        record["content_truncated"] = True
        record["included_content_chars"] = MAX_CONTENT_CHARS
        content = (
            text[:MAX_CONTENT_CHARS]
            + f"\n\n# TRUNCATED: original {len(text)} chars, included first {MAX_CONTENT_CHARS} chars\n"
        )
    else:
        record["content_truncated"] = False
        record["included_content_chars"] = len(text)
        content = text

    return record, content


def run_git(args):
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return None


def get_git_info() -> dict:
    info = {
        "is_git_repo": False,
        "branch": None,
        "last_commit_hash": None,
        "last_commit_info": None,
    }

    is_repo = run_git(["rev-parse", "--is-inside-work-tree"])
    if is_repo and is_repo.lower() == "true":
        info["is_git_repo"] = True
        info["branch"] = run_git(["branch", "--show-current"])
        info["last_commit_hash"] = run_git(["rev-parse", "HEAD"])
        info["last_commit_info"] = run_git(["log", "-1", "--format=%H %s %ai"])

    return info


def build_summary(records, critical_missing, important_missing) -> dict:
    summary = {
        "total_files": len(records),
        "by_suffix": {},
        "python_files": 0,
        "sql_files": 0,
        "markdown_files": 0,
        "yaml_files": 0,
        "total_python_lines": 0,
        "total_classes": 0,
        "total_top_level_functions": 0,
        "total_methods": 0,
        "total_imports": 0,
        "total_todos": 0,
        "critical_missing_count": len(critical_missing),
        "important_missing_count": len(important_missing),
        "context_md_bytes": CONTEXT_MD.stat().st_size if CONTEXT_MD.exists() else 0,
    }

    for record in records:
        suffix = record.get("suffix") or "no_ext"
        summary["by_suffix"][suffix] = summary["by_suffix"].get(suffix, 0) + 1

        if suffix == ".py":
            summary["python_files"] += 1
            analysis = record.get("analysis", {})
            summary["total_python_lines"] += analysis.get("lines_of_code", 0)
            summary["total_classes"] += len(analysis.get("classes", []))
            summary["total_top_level_functions"] += len(analysis.get("functions", []))
            summary["total_methods"] += sum(
                len(class_info.get("methods", []))
                for class_info in analysis.get("classes", [])
            )
            summary["total_imports"] += len(analysis.get("imports", []))
            summary["total_todos"] += len(analysis.get("todos", []))
        elif suffix == ".sql":
            summary["sql_files"] += 1
        elif suffix == ".md":
            summary["markdown_files"] += 1
        elif suffix in {".yaml", ".yml"}:
            summary["yaml_files"] += 1

    return summary


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    LOG_TXT.write_text("", encoding="utf-8")

    started = datetime.now()

    log(f"Task: {TASK_ID}")
    log(f"Project root: {PROJECT_ROOT}")
    log(f"Report dir: {REPORT_DIR}")

    files = []
    seen = set()

    for pattern in INCLUDE_PATTERNS:
        try:
            for path in PROJECT_ROOT.glob(pattern):
                if not path.is_file():
                    continue

                if should_exclude(path):
                    continue

                try:
                    resolved = path.resolve()
                except Exception:
                    resolved = path

                if resolved in seen:
                    continue

                seen.add(resolved)
                files.append(path)
        except Exception as exc:
            log(f"Pattern error '{pattern}': {exc}")

    files.sort(key=lambda p: str(p.relative_to(PROJECT_ROOT)).replace("\\", "/"))

    log(f"Selected files: {len(files)}")

    critical_found = []
    critical_missing = []

    for file_path in CRITICAL_FILES:
        if (PROJECT_ROOT / file_path).is_file():
            critical_found.append(file_path)
        else:
            critical_missing.append(file_path)

    important_found = []
    important_missing = []

    for file_path in IMPORTANT_FILES:
        if (PROJECT_ROOT / file_path).is_file():
            important_found.append(file_path)
        else:
            important_missing.append(file_path)

    context_parts = [
        "# Targeted Project Context\n\n",
        f"Task: {TASK_ID}\n",
        f"Generated: {started.isoformat()}\n\n",
    ]

    records = []

    for path in files:
        record, content = process_file(path)
        records.append(record)

        rel = record["path"]
        lang = fence_lang(path)

        context_parts.append(f"## {rel}\n\n")

        if content is not None:
            context_parts.append(f"```{lang}\n{content}\n```\n\n")
        else:
            context_parts.append("```\n<unreadable file>\n```\n\n")

    CONTEXT_MD.write_text("".join(context_parts), encoding="utf-8")

    TREE_TXT.write_text(
        "\n".join(record["path"] for record in records),
        encoding="utf-8",
    )

    summary = build_summary(records, critical_missing, important_missing)
    git_info = get_git_info()
    finished = datetime.now()

    status = "success"
    if not records:
        status = "failed"
    elif critical_missing:
        status = "needs_human"

    report = {
        "task_id": TASK_ID,
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": round((finished - started).total_seconds(), 2),
        "project_root": str(PROJECT_ROOT),
        "python_version": sys.version,
        "platform": sys.platform,
        "git_info": git_info,
        "scan": {
            "include_patterns": INCLUDE_PATTERNS,
            "selected_files_count": len(records),
            "critical_files": {
                "found": critical_found,
                "missing": critical_missing,
            },
            "important_files": {
                "found": important_found,
                "missing": important_missing,
            },
            "summary": summary,
            "artifacts": {
                "report_json": str(REPORT_JSON.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "report_md": str(REPORT_MD.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "context_md": str(CONTEXT_MD.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "tree_txt": str(TREE_TXT.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "files_dir": str(FILES_DIR.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "log_txt": str(LOG_TXT.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            },
        },
        "files": records,
        "next_action": "Send report.json. If possible, attach context.md. If context.md is too large, send tree.txt and report.json first.",
    }

    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    report_md_lines = [
        f"# {TASK_ID}",
        "",
        f"Status: **{status}**",
        f"Started: {started.isoformat()}",
        f"Finished: {finished.isoformat()}",
        f"Duration: {report['duration_sec']} sec",
        "",
        "## Critical files",
        "",
        f"Found: {len(critical_found)}",
        f"Missing: {len(critical_missing)}",
        "",
        "### Missing critical files",
        "",
        "```",
        *critical_missing,
        "```",
        "",
        "## Important files",
        "",
        f"Found: {len(important_found)}",
        f"Missing: {len(important_missing)}",
        "",
        "### Missing important files",
        "",
        "```",
        *important_missing,
        "```",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Artifacts",
        "",
        f"- {report['scan']['artifacts']['report_json']}",
        f"- {report['scan']['artifacts']['context_md']}",
        f"- {report['scan']['artifacts']['tree_txt']}",
        f"- {report['scan']['artifacts']['files_dir']}",
        f"- {report['scan']['artifacts']['log_txt']}",
        "",
    ]

    REPORT_MD.write_text("\n".join(report_md_lines), encoding="utf-8")

    log(f"Status: {status}")
    log(f"Report JSON: {REPORT_JSON}")
    log(f"Context MD: {CONTEXT_MD}")
    log(f"Tree: {TREE_TXT}")

    if status == "failed":
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
PY_EOF

if command -v python3 >/dev/null 2>&1; then
    python3 scripts/targeted_project_scanner.py
elif command -v python >/dev/null 2>&1; then
    python scripts/targeted_project_scanner.py
else
    echo "Python not found. Cannot run targeted scanner." > "${REPORT_DIR}/log.txt"

    cat > "${REPORT_DIR}/report.json" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "error": "python_not_found",
  "next_action": "Install Python 3 or run inside an environment where python3 is available."
}
JSON

    exit 1
fi
