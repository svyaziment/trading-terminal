#!/usr/bin/env bash
set -u

TASK_ID="task-031e-generate-signals-fixed"
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
  PY_VALIDATE=python3
elif command -v python >/dev/null 2>&1; then
  PY_VALIDATE=python
else
  PY_VALIDATE=""
fi

validate_json() {
  local file_path="$1"

  if [[ ! -s "${file_path}" ]]; then
    return 1
  fi

  if [[ -n "${PY_VALIDATE}" ]]; then
    "${PY_VALIDATE}" -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "${file_path}" 2>>"${LOG_TXT}"
    return $?
  fi

  return 0
}

create_failed_report() {
  local stage="$1"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "${stage}",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "next_action": "Check ${LOG_TXT}"
}
JSON
}

PY_CODE=$(cat <<'PY_RUNNER'
import json
import sys
import logging
import traceback
from datetime import datetime, timezone

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

TASK_ID = "task-031e-generate-signals-fixed"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    started = utcnow()

    try:
        from app.analytics.signal_generator import SignalGenerator
    except Exception as exc:
        print(
            json.dumps(
                {
                    "task_id": TASK_ID,
                    "status": "failed",
                    "stage": "import",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "started_at": started,
                    "finished_at": utcnow(),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        sys.exit(1)

    try:
        generator = SignalGenerator()

        cleanup = {
            "backup_table": "trading.signals_backup_task_031e",
            "deleted_old_signals": 0,
        }

        try:
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
        except Exception as exc:
            cleanup["error"] = str(exc)
            logging.warning("Cleanup old signals failed: %s", exc)

        tickers = generator.get_top_tickers(limit=30)

        if not tickers:
            report = {
                "task_id": TASK_ID,
                "status": "needs_human",
                "started_at": started,
                "finished_at": utcnow(),
                "error": "No top tickers found in trading.top_stocks_by_volume",
                "cleanup": cleanup,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return

        timeframes = ["30min", "1h", "4h", "1d"]
        lookback = 2000

        report = generator.scan_and_save_signals(
            tickers=tickers,
            timeframes=timeframes,
            lookback=lookback,
        )

        signals = report.pop("signals", [])
        report["signals_count"] = len(signals)
        report["signals_sample"] = signals[:50]

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
            validation["latest_signal_timestamp"] = (
                str(latest_df.iloc[0]["last_ts"])
                if not latest_df.empty and latest_df.iloc[0]["last_ts"]
                else None
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
                    count(*) FILTER (WHERE macd IS NULL) AS macd_null,
                    count(*) FILTER (WHERE bb_position IS NULL) AS bb_position_null,
                    count(*) FILTER (WHERE volume_ratio IS NULL) AS volume_ratio_null,
                    count(*) FILTER (WHERE atr_pct IS NULL) AS atr_pct_null,
                    count(*) FILTER (WHERE pattern_name IS NULL) AS pattern_name_null,
                    count(*) FILTER (WHERE figi IS NULL OR figi = '') AS figi_null
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

        if report.get("errors"):
            status = "needs_human"

        if report.get("db_statistics", {}).get("insert_error", 0) > 0:
            status = "needs_human"

        if (
            report.get("total_signals_saved", 0) == 0
            and validation.get("total_signals", 0) == 0
        ):
            status = "needs_human"

        report["task_id"] = TASK_ID
        report["status"] = status
        report["started_at"] = started
        report["finished_at"] = utcnow()
        report["generation_settings"] = {
            "tickers_limit": 30,
            "timeframes": timeframes,
            "lookback": lookback,
        }
        report["cleanup"] = cleanup
        report["db_validation"] = validation

        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    except Exception as exc:
        print(
            json.dumps(
                {
                    "task_id": TASK_ID,
                    "status": "failed",
                    "started_at": started,
                    "finished_at": utcnow(),
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
PY_RUNNER
)

run_docker_with_volume() {
  printf '%s\n' "${PY_CODE}" | docker compose run --rm --no-deps -T --volume "$(pwd)/backend:/app" backend python -u - >"${REPORT_JSON}" 2>>"${LOG_TXT}"
}

run_docker_image() {
  printf '%s\n' "${PY_CODE}" | docker compose run --rm --no-deps -T backend python -u - >"${REPORT_JSON}" 2>>"${LOG_TXT}"
}

run_local_python() {
  if [[ -n "${PY_VALIDATE}" ]]; then
    printf '%s\n' "${PY_CODE}" | PYTHONPATH=backend "${PY_VALIDATE}" - >"${REPORT_JSON}" 2>>"${LOG_TXT}"
  else
    return 1
  fi
}

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "Docker Compose is available"

  log "Trying Docker run with mounted backend directory"
  run_docker_with_volume || true

  if ! validate_json "${REPORT_JSON}"; then
    log "Docker run with volume did not produce valid JSON"
    log "Trying docker compose build backend"
    docker compose build backend >>"${LOG_TXT}" 2>&1 || log "docker compose build backend failed"

    log "Trying Docker run from image"
    run_docker_image || true
  fi

  if ! validate_json "${REPORT_JSON}"; then
    log "ERROR: Docker run did not produce valid report.json"
    create_failed_report "docker_run"
  fi
else
  log "Docker Compose is not available, trying local Python"
  run_local_python || true

  if ! validate_json "${REPORT_JSON}"; then
    log "ERROR: local Python run did not produce valid report.json"
    create_failed_report "local_python"
  fi
fi

if [[ -n "${PY_VALIDATE}" ]]; then
  "${PY_VALIDATE}" - "${REPORT_JSON}" "${REPORT_MD}" <<'PYMD'
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
fi

log "Report JSON: ${REPORT_JSON}"
log "Report MD: ${REPORT_MD}"
log "Done"
