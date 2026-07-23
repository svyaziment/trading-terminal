#!/usr/bin/env bash
set -u

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

TASK_ID="task-031f-generate-signals-exec"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
REPORT_DIR="reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

: > "${LOG_TXT}"

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  PY=""
fi

create_failed_report() {
  local stage="$1"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "${stage}",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "next_action": "Check ${LOG_TXT}, exec_stdout.txt, exec_stderr.txt, backend_logs.txt"
}
JSON
}

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  log "ERROR: Docker Compose is not available"
  create_failed_report "docker_not_available"
  exit 0
fi

log "Creating backend/app/analytics/run_generate_signals.py"

cat > backend/app/analytics/run_generate_signals.py <<'PY_EOF'
"""
Runner for signal generation inside Docker container.

Writes structured JSON report to a file, then prints a small summary to stdout.
"""

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

TASK_ID = "task-031f-generate-signals-exec"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_report(path: str, report: dict) -> None:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate signals and write report")
    parser.add_argument("--report-path", default="/tmp/task_report.json")
    parser.add_argument("--log-path", default="/tmp/task_log.txt")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        file_handler = logging.FileHandler(args.log_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logging.getLogger().addHandler(file_handler)
    except Exception:
        pass

    report = {
        "task_id": TASK_ID,
        "status": "failed",
        "started_at": utcnow(),
    }

    try:
        from app.analytics.signal_generator import SignalGenerator

        generator = SignalGenerator()

        cleanup = {
            "backup_table": "trading.signals_backup_task_031e",
        }

        try:
            column_df = generator.db.select(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'trading'
                  AND table_name = 'signals'
                  AND column_name = 'pattern_name'
                """
            ).to_dataframe()

            if not column_df.empty:
                generator.db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trading.signals_backup_task_031e AS
                    SELECT *
                    FROM trading.signals
                    WHERE pattern_name IS NULL
                    """
                )
                cleanup["deleted_old_signals"] = generator.db.execute(
                    "DELETE FROM trading.signals WHERE pattern_name IS NULL"
                )
            else:
                cleanup["skipped"] = "pattern_name column not present before migration"

        except Exception as exc:
            cleanup["error"] = str(exc)
            logging.warning("Cleanup error: %s", exc)

        tickers = generator.get_top_tickers(limit=30)

        if not tickers:
            report.update(
                {
                    "status": "needs_human",
                    "finished_at": utcnow(),
                    "cleanup": cleanup,
                    "error": "No top tickers found in trading.top_stocks_by_volume",
                }
            )
            write_report(args.report_path, report)
            print(
                json.dumps(
                    {
                        "status": report.get("status"),
                        "report_path": args.report_path,
                    },
                    ensure_ascii=False,
                )
            )
            return

        timeframes = ["30min", "1h", "4h", "1d"]
        lookback = 2000

        scan_report = generator.scan_and_save_signals(
            tickers=tickers,
            timeframes=timeframes,
            lookback=lookback,
        )

        signals = scan_report.pop("signals", [])
        scan_report["signals_count"] = len(signals)
        scan_report["signals_sample"] = signals[:50]

        validation = {}

        try:
            total_df = generator.db.select(
                "SELECT count(*) AS cnt FROM trading.signals"
            ).to_dataframe()
            validation["total_signals"] = (
                int(total_df.iloc[0]["cnt"]) if not total_df.empty else 0
            )

            latest_df = generator.db.select(
                "SELECT max(timestamp) AS last_ts FROM trading.signals"
            ).to_dataframe()

            latest_value = None
            if not latest_df.empty:
                latest_value = latest_df.iloc[0]["last_ts"]
                if latest_value is None or str(latest_value) in {"", "NaT"}:
                    latest_value = None

            validation["latest_signal_timestamp"] = (
                str(latest_value) if latest_value is not None else None
            )

            by_tf = generator.db.select(
                """
                SELECT timeframe, count(*) AS cnt
                FROM trading.signals
                GROUP BY timeframe
                ORDER BY timeframe
                """
            ).to_dataframe()
            validation["signals_by_timeframe"] = (
                by_tf.to_dict("records") if not by_tf.empty else []
            )

            by_signal = generator.db.select(
                """
                SELECT signal, count(*) AS cnt
                FROM trading.signals
                GROUP BY signal
                ORDER BY signal
                """
            ).to_dataframe()
            validation["signals_by_direction"] = (
                by_signal.to_dict("records") if not by_signal.empty else []
            )

            by_pattern = generator.db.select(
                """
                SELECT pattern_name, count(*) AS cnt
                FROM trading.signals
                WHERE pattern_name IS NOT NULL
                GROUP BY pattern_name
                ORDER BY cnt DESC
                LIMIT 50
                """
            ).to_dataframe()
            validation["signals_by_pattern"] = (
                by_pattern.to_dict("records") if not by_pattern.empty else []
            )

            nulls = generator.db.select(
                """
                SELECT
                    SUM(CASE WHEN macd IS NULL THEN 1 ELSE 0 END) AS macd_null,
                    SUM(CASE WHEN bb_position IS NULL THEN 1 ELSE 0 END) AS bb_position_null,
                    SUM(CASE WHEN volume_ratio IS NULL THEN 1 ELSE 0 END) AS volume_ratio_null,
                    SUM(CASE WHEN atr_pct IS NULL THEN 1 ELSE 0 END) AS atr_pct_null,
                    SUM(CASE WHEN pattern_name IS NULL THEN 1 ELSE 0 END) AS pattern_name_null,
                    SUM(CASE WHEN figi IS NULL OR figi = '' THEN 1 ELSE 0 END) AS figi_null
                FROM trading.signals
                """
            ).to_dataframe()
            validation["null_counts"] = (
                nulls.iloc[0].to_dict() if not nulls.empty else {}
            )

        except Exception as exc:
            validation["error"] = str(exc)

        generator.close()

        status = "success"

        if scan_report.get("errors"):
            status = "needs_human"

        if scan_report.get("db_statistics", {}).get("insert_error", 0) > 0:
            status = "needs_human"

        if (
            scan_report.get("total_signals_saved", 0) == 0
            and validation.get("total_signals", 0) == 0
        ):
            status = "needs_human"

        report.update(scan_report)
        report.update(
            {
                "status": status,
                "finished_at": utcnow(),
                "generation_settings": {
                    "tickers_limit": 30,
                    "timeframes": timeframes,
                    "lookback": lookback,
                },
                "cleanup": cleanup,
                "db_validation": validation,
            }
        )

    except Exception as exc:
        report.update(
            {
                "status": "failed",
                "finished_at": utcnow(),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )

    write_report(args.report_path, report)

    print(
        json.dumps(
            {
                "status": report.get("status"),
                "report_path": args.report_path,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
PY_EOF

if [[ -n "${PY}" ]]; then
  log "Checking run_generate_signals.py syntax"
  if ! "${PY}" -m py_compile backend/app/analytics/run_generate_signals.py >>"${LOG_TXT}" 2>&1; then
    log "ERROR: run_generate_signals.py syntax check failed"
    create_failed_report "runner_syntax"
    exit 0
  fi
fi

if [[ ! -f backend/requirements-dev.txt ]]; then
  log "Creating backend/requirements-dev.txt"
  cat > backend/requirements-dev.txt <<'REQ_EOF'
-r requirements.txt
pytest
httpx
REQ_EOF
fi

log "Building backend image"
if ! docker compose build backend >>"${LOG_TXT}" 2>&1; then
  log "ERROR: docker compose build backend failed"
  docker compose logs --tail=300 backend > "${REPORT_DIR}/backend_logs.txt" 2>&1 || true
  create_failed_report "docker_build"
  exit 0
fi

log "Starting backend container"
docker compose up -d --no-build backend >>"${LOG_TXT}" 2>&1 || true

log "Waiting for backend container"
for i in $(seq 1 30); do
  RUNNING=$(docker inspect -f '{{.State.Running}}' trading-terminal-backend 2>/dev/null || echo false)
  if [[ "${RUNNING}" == "true" ]]; then
    log "Backend container is running"
    break
  fi
  sleep 2
done

RUNNING=$(docker inspect -f '{{.State.Running}}' trading-terminal-backend 2>/dev/null || echo false)
if [[ "${RUNNING}" != "true" ]]; then
  log "ERROR: backend container is not running"
  docker compose logs --tail=300 backend > "${REPORT_DIR}/backend_logs.txt" 2>&1 || true
  create_failed_report "backend_not_running"
  exit 0
fi

log "Copying current backend/app into container"
if ! docker compose cp backend/app/. backend:/app/app >>"${LOG_TXT}" 2>&1; then
  log "docker compose cp failed, trying docker cp"
  docker cp backend/app/. trading-terminal-backend:/app/app >>"${LOG_TXT}" 2>&1 || true
fi

log "Removing __pycache__ inside container"
docker compose exec -T backend find /app -type d -name '__pycache__' -exec rm -rf {} + >>"${LOG_TXT}" 2>&1 || true

log "Running signal generation inside container"
docker compose exec -T backend python -m app.analytics.run_generate_signals \
  --report-path /tmp/task_report.json \
  --log-path /tmp/task_log.txt \
  > "${REPORT_DIR}/exec_stdout.txt" \
  2> "${REPORT_DIR}/exec_stderr.txt"

EXEC_STATUS=$?
log "Exec status: ${EXEC_STATUS}"

log "Copying report from container"
if ! docker compose cp backend:/tmp/task_report.json "${REPORT_JSON}" >>"${LOG_TXT}" 2>&1; then
  log "docker compose cp report failed, trying docker cp"
  docker cp trading-terminal-backend:/tmp/task_report.json "${REPORT_JSON}" >>"${LOG_TXT}" 2>&1 || true
fi

log "Copying task log from container"
docker compose cp backend:/tmp/task_log.txt "${REPORT_DIR}/task_log.txt" >>"${LOG_TXT}" 2>&1 || true
docker cp trading-terminal-backend:/tmp/task_log.txt "${REPORT_DIR}/task_log.txt" >>"${LOG_TXT}" 2>&1 || true

log "Copying backend logs"
docker compose logs --tail=300 backend > "${REPORT_DIR}/backend_logs.txt" 2>&1 || true

if [[ ! -s "${REPORT_JSON}" ]]; then
  log "ERROR: report.json was not copied from container"
  create_failed_report "report_not_copied"
  exit 0
fi

if [[ -n "${PY}" ]]; then
  if ! "${PY}" -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "${REPORT_JSON}" 2>>"${LOG_TXT}"; then
    log "ERROR: report.json is not valid JSON"
    mv "${REPORT_JSON}" "${REPORT_DIR}/invalid_report.json" 2>/dev/null || true
    create_failed_report "invalid_report_json"
    exit 0
  fi
fi

if [[ -n "${PY}" ]]; then
  "${PY}" - "${REPORT_JSON}" "${REPORT_MD}" <<'PYMD'
import json
import sys

report_path, md_path = sys.argv[1], sys.argv[2]

try:
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
except Exception as exc:
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# report\n\nCannot read report.json: {exc}\n")
    raise SystemExit(0)

lines = []
lines.append(f"# {report.get('task_id', 'task')}")
lines.append("")
lines.append(f"Status: **{report.get('status', 'unknown')}**")
lines.append(f"Started: {report.get('started_at')}")
lines.append(f"Finished: {report.get('finished_at')}")
lines.append("")

if report.get("error"):
    lines.append("## Error")
    lines.append("")
    lines.append("```")
    lines.append(str(report.get("error")))
    lines.append("```")
    lines.append("")

lines.append("## Generation settings")
lines.append("")
lines.append("```json")
lines.append(json.dumps(report.get("generation_settings", {}), ensure_ascii=False, indent=2))
lines.append("```")
lines.append("")

lines.append("## Cleanup")
lines.append("")
lines.append("```json")
lines.append(json.dumps(report.get("cleanup", {}), ensure_ascii=False, indent=2))
lines.append("```")
lines.append("")

lines.append("## Results")
lines.append("")
lines.append(f"- total_signals_saved: {report.get('total_signals_saved')}")
lines.append(f"- total_candles_analyzed: {report.get('total_candles_analyzed')}")
lines.append(f"- signals_count: {report.get('signals_count')}")
lines.append(f"- errors: {len(report.get('errors', []))}")
lines.append("")

lines.append("## DB statistics")
lines.append("")
lines.append("```json")
lines.append(json.dumps(report.get("db_statistics", {}), ensure_ascii=False, indent=2))
lines.append("```")
lines.append("")

lines.append("## DB validation")
lines.append("")
lines.append("```json")
lines.append(json.dumps(report.get("db_validation", {}), ensure_ascii=False, indent=2))
lines.append("```")
lines.append("")

lines.append("## Pattern statistics")
lines.append("")
lines.append("```json")
lines.append(json.dumps(report.get("pattern_statistics", {}), ensure_ascii=False, indent=2))
lines.append("```")
lines.append("")

with open(md_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
PYMD
else
  {
    echo "# ${TASK_ID}"
    echo
    echo "See report.json"
  } > "${REPORT_MD}"
fi

log "Report JSON: ${REPORT_JSON}"
log "Report MD: ${REPORT_MD}"
log "Done"
