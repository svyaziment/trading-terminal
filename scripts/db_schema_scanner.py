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
