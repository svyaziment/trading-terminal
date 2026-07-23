#!/usr/bin/env bash
set -u

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

TASK_ID="task-035-project-db-scan"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PROJECT_ROOT="$(pwd)"
REPORT_DIR="${PROJECT_ROOT}/reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"
FILE_REPORT_JSON="${REPORT_DIR}/file_scan_report.json"
DB_JSON="${REPORT_DIR}/db_schema.json"
DB_MD="${REPORT_DIR}/db_schema.md"
CURRENT_TREE="${REPORT_DIR}/current_tree.txt"
SCANNER_COPY="${REPORT_DIR}/targeted_project_scanner_task035.py"

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

log "Creating scripts/db_schema_scanner.py"

cat > scripts/db_schema_scanner.py <<'DB_SCANNER_PY'
#!/usr/bin/env python3
"""
Database schema scanner for Trading Terminal.

Scans the trading schema:
- tables;
- columns;
- row counts;
- sample rows;
- analytics summary for key tables.

Writes:
- db_schema.json
- db_schema.md

This script is intended to run inside the backend container.
"""

import argparse
import datetime
import decimal
import json
import os
import re
import sys
import uuid
from pathlib import Path


def log(message: str) -> None:
    print(message, file=sys.stderr)


def serialize(value):
    if isinstance(value, decimal.Decimal):
        return float(value)

    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")

    if isinstance(value, dict):
        return {str(k): serialize(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [serialize(v) for v in value]

    return value


def validate_ident(name: str) -> str:
    name = str(name or "").strip()

    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"Invalid SQL identifier: {name}")

    return name


def query_records(db, query: str, params=None):
    result = db.select(query, params or {})
    columns = result.get("columns", [])
    data = result.get("data", [])

    records = []
    for row in data:
        record = {}
        for i, column in enumerate(columns):
            record[column] = row[i]
        records.append(record)

    return records


def safe_records(db, query: str, params=None):
    try:
        return query_records(db, query, params)
    except Exception as exc:
        return {"error": str(exc)}


def get_count(db, schema: str, table: str) -> int:
    validate_ident(table)
    records = query_records(
        db,
        f'SELECT count(*) AS cnt FROM "{schema}"."{table}"',
    )
    return int(records[0]["cnt"]) if records else 0


def get_analytics_summary(db, schema: str, tables):
    summary = {
        "generated_at": datetime.datetime.now().isoformat(),
        "tables": {},
    }

    key_tables = [
        "instruments",
        "candles_30min_raw",
        "candles_aggregated",
        "indicators",
        "signals",
        "top_stocks_by_volume",
    ]

    for table in key_tables:
        if table not in tables:
            continue

        validate_ident(table)
        entry = {}

        try:
            entry["count"] = get_count(db, schema, table)
        except Exception as exc:
            entry["count_error"] = str(exc)

        summary["tables"][table] = entry

    if "candles_aggregated" in tables:
        summary["candles_aggregated_by_timeframe"] = safe_records(
            db,
            f'SELECT timeframe, count(*) AS cnt FROM "{schema}".candles_aggregated GROUP BY timeframe ORDER BY timeframe',
        )

    if "indicators" in tables:
        summary["indicators_by_timeframe"] = safe_records(
            db,
            f'SELECT timeframe, count(*) AS cnt, count(DISTINCT ticker) AS tickers, max(timestamp) AS last_timestamp FROM "{schema}".indicators GROUP BY timeframe ORDER BY timeframe',
        )

    if "signals" in tables:
        summary["signals_by_timeframe"] = safe_records(
            db,
            f'SELECT timeframe, count(*) AS cnt FROM "{schema}".signals GROUP BY timeframe ORDER BY timeframe',
        )

        summary["signals_by_direction"] = safe_records(
            db,
            f'SELECT signal, count(*) AS cnt FROM "{schema}".signals GROUP BY signal ORDER BY signal',
        )

        summary["signals_latest"] = safe_records(
            db,
            f'SELECT max(timestamp) AS latest_timestamp FROM "{schema}".signals',
        )

        summary["signals_null_counts"] = safe_records(
            db,
            f'SELECT '
            f'SUM(CASE WHEN macd IS NULL THEN 1 ELSE 0 END) AS macd_null, '
            f'SUM(CASE WHEN bb_position IS NULL THEN 1 ELSE 0 END) AS bb_position_null, '
            f'SUM(CASE WHEN volume_ratio IS NULL THEN 1 ELSE 0 END) AS volume_ratio_null, '
            f'SUM(CASE WHEN atr_pct IS NULL THEN 1 ELSE 0 END) AS atr_pct_null, '
            f'SUM(CASE WHEN pattern_name IS NULL THEN 1 ELSE 0 END) AS pattern_name_null, '
            f'SUM(CASE WHEN figi IS NULL OR figi = \'\' THEN 1 ELSE 0 END) AS figi_null '
            f'FROM "{schema}".signals',
        )

    if "top_stocks_by_volume" in tables:
        summary["top_stocks_report_dates"] = safe_records(
            db,
            f'SELECT report_date, count(*) AS cnt FROM "{schema}".top_stocks_by_volume GROUP BY report_date ORDER BY report_date DESC LIMIT 20',
        )

    return summary


def render_markdown(info: dict) -> str:
    lines = []

    lines.append("# DB Schema: " + str(info.get("schema", "")))
    lines.append("")
    lines.append("Scan time: " + str(info.get("scan_time", "")))
    lines.append("Status: " + str(info.get("status", "")))
    lines.append("Tables: " + str(len(info.get("tables", []))))
    lines.append("")

    if info.get("error"):
        lines.append("Error:")
        lines.append("")
        lines.append(str(info.get("error")))
        lines.append("")

    lines.append("## Tables")
    lines.append("")
    lines.append("name | total_rows | column_count")
    lines.append("--- | --- | ---")

    for table in info.get("tables", []):
        lines.append(
            f"{table.get('name', '')} | {table.get('total_rows', '')} | {table.get('column_count', '')}"
        )

    lines.append("")
    lines.append("## Analytics summary")
    lines.append("")
    lines.append(
        json.dumps(
            info.get("analytics_summary", {}),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    lines.append("")

    for table in info.get("tables", []):
        lines.append(f"## Table: {table.get('name', '')}")
        lines.append("")
        lines.append(f"Total rows: {table.get('total_rows', '')}")
        lines.append("")
        lines.append("Columns:")
        lines.append("")
        lines.append("column | type | nullable | default")
        lines.append("--- | --- | --- | ---")

        for column in table.get("columns", []):
            default_value = column.get("column_default") or ""
            lines.append(
                f"{column.get('column_name', '')} | {column.get('data_type', '')} | {column.get('is_nullable', '')} | {default_value}"
            )

        lines.append("")
        lines.append("Sample data:")
        lines.append("")
        lines.append(
            json.dumps(
                table.get("sample_data", []),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan trading database schema")
    parser.add_argument("--schema", default=os.getenv("MARKET_DATA_SCHEMA", "trading"))
    parser.add_argument("--sample-limit", type=int, default=10)
    parser.add_argument("--output-dir", default="/tmp/db_schema_scan")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "db_schema.json"
    md_path = output_dir / "db_schema.md"

    try:
        schema = validate_ident(args.schema.strip() or "trading")
    except Exception as exc:
        info = {
            "schema": args.schema,
            "status": "failed",
            "scan_time": datetime.datetime.now().isoformat(),
            "error": str(exc),
        }
        json_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(render_markdown(info), encoding="utf-8")
        log("DB_SCHEMA_STATUS=failed")
        return 1

    try:
        from app.db.db_manager import DBManager
    except Exception as exc:
        info = {
            "schema": schema,
            "status": "failed",
            "scan_time": datetime.datetime.now().isoformat(),
            "error": f"Cannot import DBManager: {exc}",
        }
        json_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(render_markdown(info), encoding="utf-8")
        log("DB_SCHEMA_STATUS=failed")
        return 1

    db = DBManager()

    try:
        tables_result = query_records(
            db,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
            (schema,),
        )

        tables = [str(row["table_name"]) for row in tables_result]
        table_infos = []

        for table in tables:
            validate_ident(table)
            log(f"Scanning table: {schema}.{table}")

            columns_records = query_records(
                db,
                "SELECT column_name, data_type, is_nullable, column_default, character_maximum_length, numeric_precision, numeric_scale FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (schema, table),
            )

            total_rows = get_count(db, schema, table)

            sample_result = db.select(
                f'SELECT * FROM "{schema}"."{table}" LIMIT %s',
                (args.sample_limit,),
            )

            sample_columns = sample_result.get("columns", [])
            sample_rows = []

            for row in sample_result.get("data", []):
                sample_row = {}
                for i, column in enumerate(sample_columns):
                    sample_row[column] = row[i]
                sample_rows.append(sample_row)

            table_infos.append(
                {
                    "name": table,
                    "total_rows": total_rows,
                    "column_count": len(columns_records),
                    "columns": serialize(columns_records),
                    "sample_data": serialize(sample_rows),
                }
            )

        analytics_summary = get_analytics_summary(db, schema, tables)

        info = {
            "schema": schema,
            "status": "success",
            "scan_time": datetime.datetime.now().isoformat(),
            "sample_limit": args.sample_limit,
            "table_count": len(table_infos),
            "tables": table_infos,
            "analytics_summary": analytics_summary,
        }

        json_path.write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        md_path.write_text(render_markdown(info), encoding="utf-8")

        log("DB_SCHEMA_STATUS=success")
        log(f"DB_SCHEMA_TABLES={len(table_infos)}")
        log(f"DB_SCHEMA_JSON={json_path}")
        log(f"DB_SCHEMA_MD={md_path}")

        return 0

    except Exception as exc:
        info = {
            "schema": schema,
            "status": "failed",
            "scan_time": datetime.datetime.now().isoformat(),
            "error": str(exc),
        }

        json_path.write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        md_path.write_text(render_markdown(info), encoding="utf-8")

        log("DB_SCHEMA_STATUS=failed")
        log(f"DB_SCHEMA_ERROR={exc}")

        return 1

    finally:
        try:
            db.close_pool()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
DB_SCANNER_PY

log "Patching targeted_project_scanner.py copy"

if [[ ! -f scripts/targeted_project_scanner.py ]]; then
  log "WARN: scripts/targeted_project_scanner.py not found"
else
  if ! "${PY}" - "${SCANNER_COPY}" <<'PY_PATCH' >>"${LOG_TXT}" 2>&1
import re
import sys
from pathlib import Path

src_path = Path("scripts/targeted_project_scanner.py")
dst_path = Path(sys.argv[1])

text = src_path.read_text(encoding="utf-8", errors="ignore")

text = re.sub(
    r'TASK_ID\s*=\s*["\']task-031c-targeted-project-scan["\']',
    'TASK_ID = "task-035-project-db-scan"',
    text,
    count=1,
)

extra_patterns = "\n    # Added by task-035: frontend and scripts\n"
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

dst_path.write_text(new_text, encoding="utf-8")
print("PATCH_DONE")
PY_PATCH
  then
    log "WARN: Python patch failed, copying original scanner and replacing task id with sed"
    cp scripts/targeted_project_scanner.py "${SCANNER_COPY}" >>"${LOG_TXT}" 2>&1 || true
    sed -i 's/task-031c-targeted-project-scan/task-035-project-db-scan/g' "${SCANNER_COPY}" >>"${LOG_TXT}" 2>&1 || true
  fi
fi

if [[ -f "${SCANNER_COPY}" ]]; then
  log "Running targeted project scanner"
  "${PY}" "${SCANNER_COPY}" >>"${LOG_TXT}" 2>&1 || log "WARN: targeted project scanner exited non-zero"
else
  log "WARN: scanner copy not created"
fi

if [[ -f "${REPORT_JSON}" ]]; then
  mv "${REPORT_JSON}" "${FILE_REPORT_JSON}" >>"${LOG_TXT}" 2>&1 || true
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
  docker compose cp "backend:/tmp/${TASK_ID}/db_schema.json" "${DB_JSON}" >>"${LOG_TXT}" 2>&1 || \
    docker cp "${CONTAINER_NAME}:/tmp/${TASK_ID}/db_schema.json" "${DB_JSON}" >>"${LOG_TXT}" 2>&1 || true

  docker compose cp "backend:/tmp/${TASK_ID}/db_schema.md" "${DB_MD}" >>"${LOG_TXT}" 2>&1 || \
    docker cp "${CONTAINER_NAME}:/tmp/${TASK_ID}/db_schema.md" "${DB_MD}" >>"${LOG_TXT}" 2>&1 || true
else
  log "WARN: Docker Compose is not available, DB scan skipped"
fi

log "Combining report"

REPORT_DIR="${REPORT_DIR}" \
TASK_ID="${TASK_ID}" \
STARTED_AT="${STARTED_AT}" \
FILE_REPORT_JSON="${FILE_REPORT_JSON}" \
DB_JSON="${DB_JSON}" \
REPORT_JSON="${REPORT_JSON}" \
REPORT_MD="${REPORT_MD}" \
"${PY}" - <<'PY_COMBINE'
import json
import os
from pathlib import Path

report_dir = Path(os.environ["REPORT_DIR"])
task_id = os.environ["TASK_ID"]
started_at = os.environ["STARTED_AT"]
file_report_path = Path(os.environ["FILE_REPORT_JSON"])
db_json_path = Path(os.environ["DB_JSON"])
report_json_path = Path(os.environ["REPORT_JSON"])
report_md_path = Path(os.environ["REPORT_MD"])

file_report = {}
db_schema = {}

if file_report_path.exists():
    try:
        file_report = json.loads(file_report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        file_report = {"error": f"Cannot read file scan report: {exc}"}

if db_json_path.exists():
    try:
        db_schema = json.loads(db_json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        db_schema = {"error": f"Cannot read db schema report: {exc}"}

file_ok = (
    file_report.get("status") in {"success", "needs_human"}
    and file_report.get("scan", {}).get("selected_files_count", 0) > 0
)

db_ok = (
    db_schema.get("status") == "success"
    and isinstance(db_schema.get("tables"), list)
    and len(db_schema.get("tables", [])) > 0
)

if file_ok and db_ok:
    status = "success"
elif file_ok or db_ok:
    status = "needs_human"
else:
    status = "failed"

tables_summary = []
for table in db_schema.get("tables", []):
    tables_summary.append(
        {
            "name": table.get("name"),
            "total_rows": table.get("total_rows"),
            "column_count": table.get("column_count"),
        }
    )

combined = {
    "task_id": task_id,
    "status": status,
    "started_at": started_at,
    "finished_at": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip(),
    "file_scan": {
        "status": file_report.get("status"),
        "selected_files_count": file_report.get("scan", {}).get("selected_files_count"),
        "summary": file_report.get("scan", {}).get("summary"),
        "critical_files": file_report.get("scan", {}).get("critical_files"),
        "important_files": file_report.get("scan", {}).get("important_files"),
        "git_info": file_report.get("git_info"),
        "artifacts": {
            "file_scan_report": "reports/" + task_id + "/file_scan_report.json",
            "context_md": "reports/" + task_id + "/context.md",
            "tree_txt": "reports/" + task_id + "/tree.txt",
            "current_tree_txt": "reports/" + task_id + "/current_tree.txt",
        },
    },
    "db_scan": {
        "status": db_schema.get("status"),
        "schema": db_schema.get("schema"),
        "scan_time": db_schema.get("scan_time"),
        "table_count": db_schema.get("table_count"),
        "tables_summary": tables_summary,
        "analytics_summary": db_schema.get("analytics_summary"),
        "artifacts": {
            "db_schema_json": "reports/" + task_id + "/db_schema.json",
            "db_schema_md": "reports/" + task_id + "/db_schema.md",
        },
    },
    "next_action": "Send report.json. If needed, send db_schema.md and current_tree.txt.",
}

report_json_path.write_text(
    json.dumps(combined, ensure_ascii=False, indent=2, default=str),
    encoding="utf-8",
)

md = []
md.append("# " + task_id)
md.append("")
md.append("Status: " + status)
md.append("")
md.append("## File scan")
md.append("")
md.append("Status: " + str(file_report.get("status")))
md.append("Selected files: " + str(file_report.get("scan", {}).get("selected_files_count")))
md.append("")
md.append("## DB scan")
md.append("")
md.append("Status: " + str(db_schema.get("status")))
md.append("Schema: " + str(db_schema.get("schema")))
md.append("Tables: " + str(db_schema.get("table_count")))
md.append("")
md.append("Tables summary:")
md.append("")
md.append("name | total_rows | column_count")
md.append("--- | --- | ---")

for table in tables_summary:
    md.append(
        f"{table.get('name', '')} | {table.get('total_rows', '')} | {table.get('column_count', '')}"
    )

md.append("")
md.append("Artifacts:")
md.append("")
md.append("- reports/" + task_id + "/report.json")
md.append("- reports/" + task_id + "/db_schema.json")
md.append("- reports/" + task_id + "/db_schema.md")
md.append("- reports/" + task_id + "/file_scan_report.json")
md.append("- reports/" + task_id + "/current_tree.txt")
md.append("")

report_md_path.write_text("\n".join(md), encoding="utf-8")
PY_COMBINE

log "Report JSON: ${REPORT_JSON}"
log "Report MD: ${REPORT_MD}"
log "DB schema JSON: ${DB_JSON}"
log "DB schema MD: ${DB_MD}"
log "Done"

exit 0
