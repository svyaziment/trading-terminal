#!/usr/bin/env python3
"""
MOEX 1min candles loader (incremental with 2-year check).
Loads 1min candles from MOEX ISS API into PostgreSQL (trading.candles_1min_raw).
Logic:
  - Checks if ticker has data around (today - 730 days) (range [check_date, check_date+7] for reliability).
  - If NO data 2 years ago (or no data at all): full load for 730 days.
  - If data 2 years ago exists: incremental load from max(timestamp)::date (reloads last day).
Updates figi from top_stocks_by_volume where figi IS NULL.
Logs progress per ticker and timing.
Produces JSON report.

Usage:
  python backend/app/api/moex_1min_loader.py [--tickers SBER,GAZP] [--days 730] [--report-path reports/moex_1min_loader/report.json]

Environment variables (required):
  POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, PSTGRS_PWD (or POSTGRES_PASSWORD), POSTGRES_DB
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import date, timedelta, datetime
import psycopg2
from psycopg2.extras import execute_values

# Defaults
TICKERS_DEFAULT = ('SBER', 'GAZP', 'VTBR', 'LKOH', 'GMKN', 'ROSN', 'MGNT', 'NVTK', 'TATN', 'SNGS',
                   'MTSS', 'PLZL', 'ALRS', 'CHMF', 'NLMK', 'MOEX', 'YNDX', 'POLY', 'RUAL', 'PHOR',
                   'IRAO', 'PIKK', 'FEES', 'RTKM', 'TRNFP', 'AFKS', 'MTLR', 'CBOM', 'SIBN', 'FLOT')
DAYS_DEFAULT = 2 * 365  # 2 года

def log(msg):
    print(f"[{datetime.now().isoformat()}] {msg}", file=sys.stderr)

def get_db_connection():
    """Create PostgreSQL connection from environment variables."""
    conn = psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', '5432')),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('PSTGRS_PWD') or os.getenv('POSTGRES_PASSWORD', ''),
        database=os.getenv('POSTGRES_DB', 'trading_terminal'),
    )
    return conn

def create_table_if_not_exists(conn):
    """Create candles_1min_raw table if not exists."""
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS trading.candles_1min_raw (
            ticker TEXT NOT NULL,
            figi TEXT,
            timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            open NUMERIC NOT NULL,
            high NUMERIC NOT NULL,
            low NUMERIC NOT NULL,
            close NUMERIC NOT NULL,
            volume BIGINT,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, timestamp)
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_candles_1min_ticker ON trading.candles_1min_raw (ticker)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_candles_1min_timestamp ON trading.candles_1min_raw (timestamp)")
    conn.commit()

def get_last_loaded_date(conn, ticker):
    """Get max(timestamp)::date from candles_1min_raw for ticker. Returns None if no data."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(timestamp)::date FROM trading.candles_1min_raw WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

def has_data_two_years_ago(conn, ticker, days_back=730):
    """Check if ticker has data around (today - days_back).
    Checks range [check_date, check_date+7] for reliability (weekends/holidays).
    Returns True if data exists."""
    check_date = date.today() - timedelta(days=days_back)
    check_end = check_date + timedelta(days=7)
    with conn.cursor() as cur:
        cur.execute("""
        SELECT count(*) FROM trading.candles_1min_raw
        WHERE ticker = %s AND timestamp::date >= %s AND timestamp::date <= %s
        """, (ticker, check_date.isoformat(), check_end.isoformat()))
        row = cur.fetchone()
        return row[0] > 0 if row else False

def fetch_candles_from_moex(ticker, date_str, interval=1):
    """Fetch 1min candles for ticker on date_str from MOEX ISS API."""
    url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}/candles.json?from={date_str}&till={date_str}&interval={interval}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            candles_data = data.get('candles', {})
            columns = candles_data.get('columns', [])
            rows = candles_data.get('data', [])
            if not columns or not rows:
                return []
            candles = []
            for row in rows:
                candle = dict(zip(columns, row))
                candles.append(candle)
            return candles
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Exception("Rate limit exceeded (429)")
        elif e.code == 500:
            raise Exception("Server error (500)")
        else:
            raise Exception(f"HTTP error {e.code}")
    except Exception as e:
        raise Exception(f"Network error: {e}")

def insert_candles_into_db(conn, ticker, candles):
    """Insert candles into candles_1min_raw idempotently (DELETE+INSERT by day)."""
    if not candles:
        return 0
    rows = []
    for candle in candles:
        rows.append((
            ticker,
            None,  # figi (updated later from top_stocks_by_volume)
            candle['begin'],  # timestamp
            candle['open'],
            candle['high'],
            candle['low'],
            candle['close'],
            candle['volume'],
        ))
    date_str = candles[0]['begin'][:10]
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM trading.candles_1min_raw WHERE ticker = %s AND timestamp >= %s AND timestamp < %s",
            (ticker, date_str, date_str + ' 23:59:59')
        )
        execute_values(
            cur,
            "INSERT INTO trading.candles_1min_raw (ticker, figi, timestamp, open, high, low, close, volume) VALUES %s",
            rows
        )
    conn.commit()
    return len(rows)

def update_figi(conn, tickers):
    """Update figi in candles_1min_raw from top_stocks_by_volume where figi IS NULL."""
    with conn.cursor() as cur:
        cur.execute("""
        UPDATE trading.candles_1min_raw c
        SET figi = t.figi
        FROM (
            SELECT DISTINCT ON (ticker) ticker, figi
            FROM trading.top_stocks_by_volume
            ORDER BY ticker, report_date DESC
        ) t
        WHERE c.ticker = t.ticker AND c.figi IS NULL AND c.ticker = ANY(%s)
        """, (list(tickers),))
        updated = cur.rowcount
    conn.commit()
    return updated

def load_ticker_incremental(conn, ticker, days_back):
    """Load 1min candles for one ticker. Checks data 2 years ago first."""
    last_date = get_last_loaded_date(conn, ticker)
    has_old_data = has_data_two_years_ago(conn, ticker, days_back)
    today = date.today()

    if last_date is None or not has_old_data:
        # Full load: N days back (no data 2 years ago, or no data at all)
        start_date = today - timedelta(days=days_back)
        reason = "no data at all" if last_date is None else "no data 2 years ago"
        log(f"{ticker}: {reason}, full load from {start_date} to {today} ({days_back} days)")
    else:
        # Incremental: from last_date (reload last day to get complete data)
        start_date = last_date
        days_to_load = (today - last_date).days + 1
        log(f"{ticker}: data 2 years ago exists, incremental load from {start_date} to {today} ({days_to_load} days; last loaded: {last_date})")

    current_date = today
    total_candles = 0
    total_days = 0
    errors = []
    ticker_start_time = time.time()

    while current_date >= start_date:
        date_str = current_date.isoformat()
        try:
            candles = fetch_candles_from_moex(ticker, date_str, interval=1)
            if candles:
                inserted = insert_candles_into_db(conn, ticker, candles)
                total_candles += inserted
                log(f"{ticker} {date_str}: {inserted} candles inserted")
            else:
                log(f"{ticker} {date_str}: no candles (weekend/holiday?)")
        except Exception as e:
            log(f"{ticker} {date_str}: ERROR {e}")
            errors.append({'date': date_str, 'error': str(e)})
            # Retry с backoff
            for attempt in range(3):
                time.sleep(2 ** attempt)
                try:
                    candles = fetch_candles_from_moex(ticker, date_str, interval=1)
                    if candles:
                        inserted = insert_candles_into_db(conn, ticker, candles)
                        total_candles += inserted
                        log(f"{ticker} {date_str}: {inserted} candles inserted (retry {attempt+1})")
                        break
                except Exception as e2:
                    log(f"{ticker} {date_str}: ERROR (retry {attempt+1}) {e2}")
            else:
                log(f"{ticker} {date_str}: FAILED after 3 retries")
        total_days += 1
        current_date -= timedelta(days=1)
        time.sleep(1)  # rate limit

    ticker_elapsed = time.time() - ticker_start_time
    log(f"Finished {ticker}: {total_candles} candles, {total_days} days, {ticker_elapsed:.1f}s elapsed, {len(errors)} errors")

    return {
        'ticker': ticker,
        'total_candles': total_candles,
        'total_days': total_days,
        'elapsed_seconds': round(ticker_elapsed, 1),
        'errors_count': len(errors),
        'errors_sample': errors[:10],
        'last_loaded_date': str(last_date) if last_date else None,
        'has_data_two_years_ago': has_old_data,
        'start_date': str(start_date),
        'mode': 'incremental' if (last_date is not None and has_old_data) else 'full',
    }

def main():
    parser = argparse.ArgumentParser(description='Load 1min candles from MOEX ISS API into PostgreSQL (incremental with 2-year check)')
    parser.add_argument('--tickers', type=str, default=','.join(TICKERS_DEFAULT),
                        help='Comma-separated list of tickers (default: all 30 from top_stocks_by_volume)')
    parser.add_argument('--days', type=int, default=DAYS_DEFAULT,
                        help=f'Number of days to load back from today for full load (default: {DAYS_DEFAULT})')
    parser.add_argument('--report-path', type=str, default='reports/moex_1min_loader/report.json',
                        help='Path to JSON report file (default: reports/moex_1min_loader/report.json)')
    args = parser.parse_args()

    tickers = tuple(args.tickers.split(','))

    log(f"MOEX 1min candles loader (incremental with 2-year check)")
    log(f"Tickers: {', '.join(tickers)}")

    conn = get_db_connection()
    create_table_if_not_exists(conn)

    results = []
    total_start_time = time.time()

    for ticker in tickers:
        result = load_ticker_incremental(conn, ticker, args.days)
        results.append(result)

    # Update figi from top_stocks_by_volume
    log("Updating figi from top_stocks_by_volume")
    figi_updated = update_figi(conn, tickers)
    log(f"Updated figi for {figi_updated} rows")

    total_elapsed = time.time() - total_start_time
    total_candles = sum(r['total_candles'] for r in results)
    total_errors = sum(r['errors_count'] for r in results)

    conn.close()

    report = {
        'task_id': 'moex_1min_loader_incremental_2year_check',
        'status': 'success' if total_errors == 0 else 'needs_human',
        'started_at': datetime.now().isoformat(),
        'finished_at': datetime.now().isoformat(),
        'tickers': list(tickers),
        'mode': 'incremental with 2-year check (full load if no data 2 years ago)',
        'total_candles': total_candles,
        'total_errors': total_errors,
        'total_elapsed_seconds': round(total_elapsed, 1),
        'figi_updated_rows': figi_updated,
        'per_ticker': results,
    }

    # Write report to file
    os.makedirs(os.path.dirname(args.report_path), exist_ok=True)
    with open(args.report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log(f"Report written to {args.report_path}")

    # Also print report to stdout
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
