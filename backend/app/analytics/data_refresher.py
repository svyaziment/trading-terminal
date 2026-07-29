"""
Data refresher: periodically updates candles_1min_raw (MOEX ISS API) and candles_aggregated,
including FIGI sourced from trading.instruments (NOT top_stocks_by_volume).
Sequential in one process:
  1) MOEX 1min incremental load -> candles_1min_raw
  2) update figi from trading.instruments (candles_1min_raw)
  3) aggregate 1min -> 30min/1h/4h/1d (candles_aggregated, incremental, idempotent)
  4) update figi from trading.instruments (candles_aggregated, for freshly aggregated rows)
Reuses moex_1min_loader (incremental load) and candles_1min_aggregator (aggregation).
"""
from __future__ import annotations
import time
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

TOP5_TICKERS = ['RUAL', 'GMKN', 'PIKK', 'GAZP', 'SIBN']
TIMEFRAMES = ['30min', '1h', '4h', '1d']


def update_figi_from_instruments(db, tickers: List[str]) -> dict:
    """Update figi in candles_1min_raw and candles_aggregated from trading.instruments."""
    raw_updated = db.execute("""
        UPDATE trading.candles_1min_raw c
        SET figi = i.figi
        FROM trading.instruments i
        WHERE c.ticker = i.ticker AND c.figi IS DISTINCT FROM i.figi AND c.ticker = ANY(%s)
    """, (list(tickers),))
    agg_updated = db.execute("""
        UPDATE trading.candles_aggregated c
        SET figi = i.figi
        FROM trading.instruments i
        WHERE c.ticker = i.ticker AND c.figi IS DISTINCT FROM i.figi AND c.ticker = ANY(%s)
    """, (list(tickers),))
    return {'raw_updated': raw_updated, 'agg_updated': agg_updated}


def refresh_once(tickers: List[str], days_back: int = 730) -> dict:
    """One refresh cycle: MOEX 1min load -> figi -> aggregate -> figi (post-agg)."""
    from app.api.moex_1min_loader import get_db_connection, load_ticker_incremental
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
    conn.close()

    db = DBManager()
    # 2) figi from trading.instruments (candles_1min_raw)
    figi1 = update_figi_from_instruments(db, tickers)
    logger.info(f"FIGI (pre-agg): raw={figi1['raw_updated']}, agg={figi1['agg_updated']}")

    # 3) aggregate 1min -> all timeframes (incremental, idempotent; figi already set above)
    agg_result = aggregate_all(db, tickers=tuple(tickers), timeframes=TIMEFRAMES, update_figi_first=False)
    logger.info(f"Aggregated: {agg_result.get('total_rows_aggregated', 0)} rows, errors={agg_result.get('total_errors', 0)}")

    # 4) figi from trading.instruments (candles_aggregated, for freshly aggregated rows)
    figi2 = update_figi_from_instruments(db, tickers)
    logger.info(f"FIGI (post-agg): raw={figi2['raw_updated']}, agg={figi2['agg_updated']}")

    try:
        db.close_pool()
    except Exception:
        pass
    return {
        'total_candles_loaded': total_candles,
        'total_aggregated': agg_result.get('total_rows_aggregated', 0),
        'figi_updated': figi2,
        'load_errors': load_errors,
        'agg_errors': agg_result.get('total_errors', 0),
    }


def run_data_refresher(tickers: Optional[List[str]] = None, duration_minutes: int = 120,
                       refresh_interval_min: int = 15):
    """Run refresh cycles every refresh_interval_min for duration_minutes."""
    if tickers is None:
        tickers = TOP5_TICKERS
    logger.info(f"Data refresher started: {len(tickers)} tickers, every {refresh_interval_min} min, duration {duration_minutes} min")
    start_time = time.time()
    cycles = 0
    while (time.time() - start_time) < (duration_minutes * 60):
        cycles += 1
        logger.info(f"=== Refresh cycle {cycles} ===")
        try:
            res = refresh_once(tickers)
            logger.info(f"Cycle {cycles} done: {res['total_candles_loaded']} candles, {res['total_aggregated']} agg rows")
        except Exception as e:
            logger.error(f"Cycle {cycles} error: {e}")
        next_cycle = start_time + cycles * refresh_interval_min * 60
        end_time = start_time + duration_minutes * 60
        sleep_sec = min(next_cycle, end_time) - time.time()
        if sleep_sec > 0 and time.time() < end_time:
            time.sleep(sleep_sec)
    logger.info(f"Data refresher finished: {cycles} cycles")


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    run_data_refresher(duration_minutes=duration)
