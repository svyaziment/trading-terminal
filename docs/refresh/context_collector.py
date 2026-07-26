#!/usr/bin/env python3
"""
Context collector for agent tasks.
Collects content of specified files and DB table schemas/samples into a JSON,
so the agent can be loaded with up-to-date project context before implementing a task.

Usage:
  python docs/refresh/context_collector.py \
    --files backend/app/analytics/levels_backtest.py,backend/app/db/db_manager.py \
    --tables backtest_runs,backtest_trades \
    --output reports/task-XXX/context.json

  python docs/refresh/context_collector.py --update-project-context

Environment (for DB scan): POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER,
PSTGRS_PWD (or POSTGRES_PASSWORD), POSTGRES_DB
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime


def read_file(path, max_bytes=100000):
    """Read file content (capped at max_bytes)."""
    try:
        size = os.path.getsize(path)
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(max_bytes)
        return {'path': path, 'bytes': size, 'truncated': size > max_bytes, 'content': content}
    except Exception as e:
        return {'path': path, 'error': str(e)}


def get_db_connection():
    import psycopg2
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', '5432')),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('PSTGRS_PWD') or os.getenv('POSTGRES_PASSWORD', ''),
        database=os.getenv('POSTGRES_DB', 'trading_terminal'),
    )


def scan_table(conn, table, schema='trading', sample_limit=3):
    """Scan a DB table: columns+types, row count, sample rows, date range."""
    out = {'table': f"{schema}.{table}"}
    with conn.cursor() as cur:
        # Columns + types
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position
        """, (schema, table))
        out['columns'] = [{'name': r[0], 'type': r[1], 'nullable': r[2]} for r in cur.fetchall()]
        if not out['columns']:
            out['error'] = 'table not found'
            return out
        # Row count
        try:
            cur.execute(f'SELECT count(*) FROM {schema}.{table}')
            out['row_count'] = cur.fetchone()[0]
        except Exception as e:
            out['row_count_error'] = str(e); conn.rollback()
        # Sample rows
        try:
            cur.execute(f'SELECT * FROM {schema}.{table} LIMIT %s', (sample_limit,))
            cols = [d[0] for d in cur.description]
            out['sample'] = [dict(zip(cols, [str(v) for v in row])) for row in cur.fetchall()]
        except Exception as e:
            out['sample_error'] = str(e); conn.rollback()
        # Date range (first timestamp-like column)
        ts_cols = [c['name'] for c in out['columns']
                   if 'timestamp' in c['name'].lower() or c['name'] in ('ts', 'created_at', 'entry_ts', 'exit_ts')]
        if ts_cols:
            ts_col = ts_cols[0]
            try:
                cur.execute(f'SELECT min("{ts_col}"), max("{ts_col}") FROM {schema}.{table}')
                mn, mx = cur.fetchone()
                out['date_range'] = {'column': ts_col, 'min': str(mn), 'max': str(mx)}
            except Exception:
                conn.rollback()
    return out


def update_project_context():
    """Refresh project docs by calling refresh-project-docs.sh if present."""
    sh = 'scripts/refresh/refresh-project-docs.sh'
    if os.path.exists(sh):
        try:
            r = subprocess.run(['bash', sh], capture_output=True, text=True, timeout=120)
            return {'ran': sh, 'returncode': r.returncode, 'stdout_tail': r.stdout[-500:], 'stderr_tail': r.stderr[-500:]}
        except Exception as e:
            return {'ran': sh, 'error': str(e)}
    return {'error': f'{sh} not found'}


def main():
    parser = argparse.ArgumentParser(description='Collect project context (files + DB tables) for agent tasks')
    parser.add_argument('--files', type=str, default='', help='Comma-separated file paths to scan')
    parser.add_argument('--tables', type=str, default='', help='Comma-separated DB tables to scan (schema trading)')
    parser.add_argument('--schema', type=str, default='trading', help='DB schema (default trading)')
    parser.add_argument('--output', type=str, default=None, help='Output JSON path (default stdout)')
    parser.add_argument('--update-project-context', action='store_true', help='Refresh project docs')
    parser.add_argument('--max-file-bytes', type=int, default=100000, help='Max bytes per file (default 100000)')
    parser.add_argument('--sample-limit', type=int, default=3, help='Sample rows per table (default 3)')
    parser.add_argument('--task-id', type=str, default=None, help='Task ID (emitted as first field in output JSON)')
    args = parser.parse_args()

    out = {}
    if args.task_id:
        out['task_id'] = args.task_id
    out['collected_at'] = datetime.now().isoformat()
    out['files'] = []
    out['tables'] = []

    if args.files:
        for path in args.files.split(','):
            path = path.strip()
            if path:
                out['files'].append(read_file(path, args.max_file_bytes))

    if args.tables:
        try:
            conn = get_db_connection()
            for table in args.tables.split(','):
                table = table.strip()
                if table:
                    out['tables'].append(scan_table(conn, table, args.schema, args.sample_limit))
            conn.close()
        except Exception as e:
            out['db_error'] = str(e)

    if args.update_project_context:
        out['project_context_update'] = update_project_context()

    result = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(result)
        print(f"Context written to {args.output} ({len(result)} bytes)", file=sys.stderr)
    else:
        print(result)


if __name__ == '__main__':
    main()
