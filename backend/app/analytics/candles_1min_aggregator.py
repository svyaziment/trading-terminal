"""
Incremental aggregator: 1min candles from candles_1min_raw -> candles_aggregated.
Timeframes: 30min, 1h, 4h, 1d.

For each ticker/timeframe:
  - Finds max(timestamp) in candles_aggregated.
  - Aggregates only 1min candles with timestamp >= (max_timestamp - 1 bucket),
    to recompute the last (possibly incomplete) bucket and add new ones.
  - If no existing data: full aggregation.
  - Idempotent: ON CONFLICT (ticker, timestamp, timeframe) DO UPDATE.
  - Updates figi from top_stocks_by_volume where figi IS NULL.

Usage (from container):
    from app.analytics.candles_1min_aggregator import aggregate_all
    from app.db.db_manager import DBManager
    db = DBManager()
    results = aggregate_all(db, tickers=('SBER', 'GAZP', 'VTBR'))
"""
from __future__ import annotations
import time
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple
from app.db.db_manager import DBManager

# Bucket expressions and intervals for each timeframe.
# NOTE: 4h uses %% 4 (escaped) because psycopg2 interprets % as placeholder start.
TIMEFRAMES: Dict[str, Dict[str, str]] = {
    '30min': {
        'bucket': "date_trunc('hour', r.timestamp) + (floor(extract(minute from r.timestamp) / 30) * 30) * interval '1 min'",
        'interval': "interval '30 min'",
    },
    '1h': {
        'bucket': "date_trunc('hour', r.timestamp)",
        'interval': "interval '1 hour'",
    },
    '4h': {
        'bucket': "date_trunc('hour', r.timestamp) - (extract(hour from r.timestamp)::int %% 4) * interval '1 hour'",
        'interval': "interval '4 hours'",
    },
    '1d': {
        'bucket': "date_trunc('day', r.timestamp)",
        'interval': "interval '1 day'",
    },
    '1w': {
        'bucket': "date_trunc('week', r.timestamp)",
        'interval': "interval '1 week'",
    },
    '1M': {
        'bucket': "date_trunc('month', r.timestamp)",
        'interval': "interval '1 month'",
    },
}

DEFAULT_TICKERS: Tuple[str, ...] = ('SBER', 'GAZP', 'VTBR')


def update_figi(db: DBManager, tickers: Tuple[str, ...]) -> int:
    """Update figi in candles_1min_raw from top_stocks_by_volume where figi IS NULL."""
    updated = db.execute("""
    UPDATE trading.candles_1min_raw c
    SET figi = t.figi
    FROM (
        SELECT DISTINCT ON (ticker) ticker, figi
        FROM trading.top_stocks_by_volume
        ORDER BY ticker, report_date DESC
    ) t
    WHERE c.ticker = t.ticker AND c.figi IS NULL AND c.ticker = ANY(%s)
    """, (list(tickers),))
    return updated


def get_last_aggregated_timestamp(db: DBManager, ticker: str, timeframe: str) -> Optional[str]:
    """Get max(timestamp) from candles_aggregated for ticker/timeframe. Returns None if no data."""
    df = db.select(
        "SELECT max(timestamp) as max_ts FROM trading.candles_aggregated WHERE ticker=%s AND timeframe=%s",
        (ticker, timeframe)
    ).to_dataframe()
    if df.empty or pd.isna(df.iloc[0]['max_ts']):
        return None
    return str(df.iloc[0]['max_ts'])


def aggregate_ticker_timeframe(db: DBManager, ticker: str, timeframe: str) -> int:
    """Aggregate 1min candles incrementally for one ticker and timeframe.
    Returns number of rows upserted."""
    bucket_expr = TIMEFRAMES[timeframe]['bucket']
    interval_expr = TIMEFRAMES[timeframe]['interval']
    
    last_ts = get_last_aggregated_timestamp(db, ticker, timeframe)
    
    if last_ts is None:
        # Full aggregation (no existing data)
        where_clause = "WHERE r.ticker = %s"
        params = (ticker,)
    else:
        # Incremental: start from last_ts - 1 bucket (to recompute incomplete last bucket)
        where_clause = f"WHERE r.ticker = %s AND r.timestamp >= (%s::timestamp - {interval_expr})"
        params = (ticker, last_ts)
    
    sql = f"""
    INSERT INTO trading.candles_aggregated (ticker, figi, timestamp, timeframe, open, high, low, close, volume, created_at)
    SELECT
        r.ticker,
        (array_agg(r.figi ORDER BY r.timestamp DESC))[1] as figi,
        {bucket_expr} as timestamp,
        '{timeframe}' as timeframe,
        (array_agg(r.open ORDER BY r.timestamp))[1] as open,
        max(r.high) as high,
        min(r.low) as low,
        (array_agg(r.close ORDER BY r.timestamp DESC))[1] as close,
        coalesce(sum(r.volume), 0) as volume,
        now() as created_at
    FROM trading.candles_1min_raw r
    {where_clause}
    GROUP BY r.ticker, {bucket_expr}
    ON CONFLICT (ticker, timestamp, timeframe) DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume,
        figi = EXCLUDED.figi
    """
    rows = db.execute(sql, params)
    return rows


def aggregate_all(
    db: DBManager,
    tickers: Tuple[str, ...] = DEFAULT_TICKERS,
    timeframes: Optional[List[str]] = None,
    update_figi_first: bool = True,
) -> Dict[str, Any]:
    """Aggregate all tickers and timeframes incrementally. Returns results dict."""
    if timeframes is None:
        timeframes = list(TIMEFRAMES.keys())
    
    results: List[Dict[str, Any]] = []
    total_start = time.time()
    
    if update_figi_first:
        figi_updated = update_figi(db, tickers)
    else:
        figi_updated = 0
    
    for ticker in tickers:
        for tf in timeframes:
            tf_start = time.time()
            try:
                rows = aggregate_ticker_timeframe(db, ticker, tf)
                elapsed = time.time() - tf_start
                results.append({
                    'ticker': ticker,
                    'timeframe': tf,
                    'rows': rows,
                    'elapsed_seconds': round(elapsed, 2),
                    'error': None,
                })
            except Exception as e:
                elapsed = time.time() - tf_start
                results.append({
                    'ticker': ticker,
                    'timeframe': tf,
                    'rows': 0,
                    'elapsed_seconds': round(elapsed, 2),
                    'error': str(e),
                })
    
    total_elapsed = time.time() - total_start
    total_rows = sum(r['rows'] for r in results)
    total_errors = sum(1 for r in results if r['error'])
    
    return {
        'status': 'success' if total_errors == 0 else 'needs_human',
        'tickers': list(tickers),
        'timeframes': timeframes,
        'figi_updated_rows': figi_updated,
        'total_rows_aggregated': total_rows,
        'total_errors': total_errors,
        'total_elapsed_seconds': round(total_elapsed, 2),
        'per_ticker_timeframe': results,
    }
