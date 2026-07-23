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
