"""
Data refresher: periodically updates candles_1min_raw (MOEX ISS API), candles_aggregated,
trading.indicators and trading.signals (so 4h BUY signals stay fresh for the base_4hbuy arm).
Universe: trading_config.get_streaming_universe() (top-15 plus LIVE_UNIVERSE).
Sequential in one process:
  1) MOEX 1min incremental load -> candles_1min_raw
  2) update figi from trading.instruments (candles_1min_raw)
  3) aggregate 1min -> 30min/1h/4h/1d (candles_aggregated, incremental, idempotent)
  4) update figi from trading.instruments (candles_aggregated)
  5) recompute indicators for 30min/1h/4h/1d (trading.indicators)
  6) regenerate signals for 30min/1h/4h/1d (trading.signals)
Reuses moex_1min_loader, candles_1min_aggregator, IndicatorsManager, SignalGenerator.
NOTE: IndicatorsManager/SignalGenerator created once in run_data_refresher (process-wide
DBManager pool); refresh_once does NOT close the pool (kept alive across cycles).
"""
from __future__ import annotations
import time
import logging
from typing import List, Optional

from app.analytics.trading_config import get_streaming_universe
from app.db.db_manager import DBManager

logger = logging.getLogger(__name__)

TIMEFRAMES = ['30min', '1h', '4h', '1d']
SIGNAL_TIMEFRAMES = ['30min', '1h', '4h', '1d']
SIGNAL_LOOKBACK = 2000


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


def refresh_once(tickers: List[str], days_back: int = 730, im=None, gen=None) -> dict:
    """One refresh cycle: MOEX 1min -> figi -> aggregate -> figi -> indicators -> signals."""
    from app.api.moex_1min_loader import get_db_connection, load_ticker_incremental
    from app.analytics.candles_1min_aggregator import aggregate_all

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

    # 3) aggregate 1min -> all timeframes (incremental, idempotent)
    agg_result = aggregate_all(db, tickers=tuple(tickers), timeframes=TIMEFRAMES, update_figi_first=False)
    logger.info(f"Aggregated: {agg_result.get('total_rows_aggregated', 0)} rows, errors={agg_result.get('total_errors', 0)}")

    # 4) figi from trading.instruments (candles_aggregated)
    figi2 = update_figi_from_instruments(db, tickers)
    logger.info(f"FIGI (post-agg): raw={figi2['raw_updated']}, agg={figi2['agg_updated']}")

    # 5) indicators (SIGNAL_TIMEFRAMES)
    indicators_updated = 0
    if im is not None:
        for tk in tickers:
            for tf in SIGNAL_TIMEFRAMES:
                try:
                    n = im.update_indicators_for_ticker(tk, tf)
                    indicators_updated += n
                except Exception as e:
                    logger.error(f"Indicators {tk} {tf} error: {e}")
        logger.info(f"Indicators updated: {indicators_updated} rows")

    # 6) signals (SIGNAL_TIMEFRAMES)
    signals_saved = 0
    if gen is not None:
        try:
            sig_report = gen.scan_and_save_signals(tickers=list(tickers), timeframes=SIGNAL_TIMEFRAMES, lookback=SIGNAL_LOOKBACK)
            signals_saved = int(sig_report.get('total_signals_saved', 0) or 0)
            logger.info(f"Signals saved: {signals_saved}")
        except Exception as e:
            logger.error(f"Signals error: {e}")

    # NOTE: no db.close_pool() here - process-wide pool stays alive across cycles
    return {
        'total_candles_loaded': total_candles,
        'total_aggregated': agg_result.get('total_rows_aggregated', 0),
        'figi_updated': figi2,
        'indicators_updated': indicators_updated,
        'signals_saved': signals_saved,
        'load_errors': load_errors,
        'agg_errors': agg_result.get('total_errors', 0),
    }


def run_data_refresher(tickers: Optional[List[str]] = None, duration_minutes: int = 120,
                       refresh_interval_min: int = 15):
    """Run refresh cycles every refresh_interval_min for duration_minutes."""
    from app.analytics.indicators_manager import IndicatorsManager
    from app.analytics.signal_generator import SignalGenerator

    if tickers is None:
        tickers = get_streaming_universe(DBManager())
    logger.info(f"Data refresher started: {len(tickers)} tickers, every {refresh_interval_min} min, duration {duration_minutes} min (with indicators+signals)")

    # Create once (process-wide DBManager pool); reused across cycles
    im = IndicatorsManager()
    gen = SignalGenerator()

    start_time = time.time()
    cycles = 0
    while (time.time() - start_time) < (duration_minutes * 60):
        cycles += 1
        logger.info(f"=== Refresh cycle {cycles} ===")
        try:
            res = refresh_once(tickers, im=im, gen=gen)
            logger.info(f"Cycle {cycles} done: {res['total_candles_loaded']} candles, {res['total_aggregated']} agg rows, {res['indicators_updated']} indicators, {res['signals_saved']} signals")
        except Exception as e:
            logger.error(f"Cycle {cycles} error: {e}")
        next_cycle = start_time + cycles * refresh_interval_min * 60
        end_time = start_time + duration_minutes * 60
        sleep_sec = min(next_cycle, end_time) - time.time()
        if sleep_sec > 0 and time.time() < end_time:
            time.sleep(sleep_sec)
    logger.info(f"Data refresher finished: {cycles} cycles")
    try:
        DBManager().close_pool()
    except Exception:
        pass


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    run_data_refresher(duration_minutes=duration)
