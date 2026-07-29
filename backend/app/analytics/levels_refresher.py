"""
Levels refresher: periodically updates 1min candles from MOEX ISS API and re-aggregates 4h,
so 4h levels (used by the signal engine) stay fresh, like in the backtest.
Reuses existing moex_1min_loader (incremental load) and candles_1min_aggregator (4h aggregation).
Confirmation of reversal stays on streaming (online_candles_1min) - not touched here.
"""
from __future__ import annotations
import time
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

TOP5_TICKERS = ['RUAL', 'GMKN', 'PIKK', 'GAZP', 'SIBN']


def refresh_once(tickers: List[str], days_back: int = 730) -> dict:
    """One refresh cycle: load recent 1min candles from MOEX -> aggregate to 4h."""
    from app.api.moex_1min_loader import get_db_connection, load_ticker_incremental, update_figi
    from app.analytics.candles_1min_aggregator import aggregate_all
    from app.db.db_manager import DBManager

    # 1) Incremental 1min load from MOEX ISS API
    conn = get_db_connection()
    total_candles = 0
    load_errors = []
    for tk in tickers:
        try:
            result = load_ticker_incremental(conn, tk, days_back)
            total_candles += int(result.get('total_candles', 0) or 0)
            logger.info(f"MOEX {tk}: {result.get('total_candles', 0)} candles (mode={result.get('mode')})")
        except Exception as e:
            load_errors.append({'ticker': tk, 'error': str(e)})
            logger.error(f"MOEX {tk} load error: {e}")
    try:
        update_figi(conn, tickers)
    except Exception as e:
        logger.error(f"update_figi error: {e}")
    conn.close()

    # 2) Aggregate 1min -> 4h (incremental, idempotent)
    db = DBManager()
    agg_result = aggregate_all(db, tickers=tuple(tickers), timeframes=['4h'], update_figi_first=False)
    try:
        db.close_pool()
    except Exception:
        pass
    logger.info(f"Aggregated 4h: {agg_result.get('total_rows_aggregated', 0)} rows, errors={agg_result.get('total_errors', 0)}")
    return {
        'total_candles_loaded': total_candles,
        'total_4h_aggregated': agg_result.get('total_rows_aggregated', 0),
        'load_errors': load_errors,
        'agg_errors': agg_result.get('total_errors', 0),
    }


def run_levels_refresher(tickers: Optional[List[str]] = None, duration_minutes: int = 120,
                         refresh_interval_min: int = 15):
    """Run refresh cycles every refresh_interval_min for duration_minutes."""
    if tickers is None:
        tickers = TOP5_TICKERS
    logger.info(f"Levels refresher started: {len(tickers)} tickers, every {refresh_interval_min} min, duration {duration_minutes} min")
    start_time = time.time()
    cycles = 0
    while (time.time() - start_time) < (duration_minutes * 60):
        cycles += 1
        logger.info(f"=== Refresh cycle {cycles} ===")
        try:
            res = refresh_once(tickers)
            logger.info(f"Cycle {cycles} done: {res['total_candles_loaded']} candles, {res['total_4h_aggregated']} 4h rows")
        except Exception as e:
            logger.error(f"Cycle {cycles} error: {e}")
        next_cycle = start_time + cycles * refresh_interval_min * 60
        end_time = start_time + duration_minutes * 60
        sleep_sec = min(next_cycle, end_time) - time.time()
        if sleep_sec > 0 and time.time() < end_time:
            time.sleep(sleep_sec)
    logger.info(f"Levels refresher finished: {cycles} cycles")


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    run_levels_refresher(duration_minutes=duration)
