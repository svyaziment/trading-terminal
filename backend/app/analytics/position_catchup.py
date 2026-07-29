"""
Position catch-up: on startup, retroactively check open positions against historical
1min candles (candles_1min_raw from MOEX) for stop/take fills that occurred while
the paper trader was not running.

Flow:
  1) Find open positions in trading.paper_positions.
  2) For each ticker with open positions: pull missing 1min candles from MOEX ISS API
     (from min(entry_ts)::date to today) into candles_1min_raw.
  3) For each open position: scan 1min candles from entry_ts forward; if low<=stop or
     high>=take, close the position at that candle's timestamp/price.
Idempotent: only touches status='open' positions.
"""
from __future__ import annotations
import time
import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from app.db.db_manager import DBManager

logger = logging.getLogger(__name__)

COMMISSION_PER_SIDE = 0.0003
ROUND_TRIP = 2 * COMMISSION_PER_SIDE


def ensure_1min_data(ticker: str, from_date: date, to_date: date) -> int:
    """Pull 1min candles from MOEX ISS API for [from_date, to_date] into candles_1min_raw."""
    from app.api.moex_1min_loader import get_db_connection, fetch_candles_from_moex, insert_candles_into_db
    conn = get_db_connection()
    total = 0
    current = from_date
    while current <= to_date:
        date_str = current.isoformat()
        try:
            candles = fetch_candles_from_moex(ticker, date_str, interval=1)
            if candles:
                n = insert_candles_into_db(conn, ticker, candles)
                total += n
                logger.info(f"  catchup {ticker} {date_str}: {n} candles")
        except Exception as e:
            logger.error(f"  catchup {ticker} {date_str}: ERROR {e}")
        current += timedelta(days=1)
        time.sleep(0.3)  # rate limit
    conn.close()
    return total


def close_position_at(db, pos_id, ticker, exit_price, exit_reason, exit_ts,
                      entry_price, lot_size, size_lots):
    """Close a position at a historical timestamp (catch-up)."""
    gross_pct = (exit_price / entry_price - 1.0) * 100.0
    net_pct = gross_pct - ROUND_TRIP * 100.0
    pnl_rub = entry_price * size_lots * lot_size * (net_pct / 100.0)
    db.execute("""
        UPDATE trading.paper_positions
        SET status=%s, exit_ts=%s, exit_price=%s, exit_reason=%s, pnl_rub=%s, pnl_pct=%s, updated_at=now()
        WHERE id=%s
    """, (f'closed_{exit_reason}', exit_ts, exit_price, exit_reason,
          round(pnl_rub, 2), round(net_pct, 4), pos_id))
    logger.info(f"CATCHUP CLOSE #{pos_id} {ticker} ({exit_reason}) @ {exit_price:.2f} at {exit_ts}: PnL {pnl_rub:.0f} RUB ({net_pct:.2f}%)")
    return pnl_rub


def check_position_catchup(db, pos) -> Optional[tuple]:
    """Scan 1min candles (candles_1min_raw) from entry_ts forward for stop/take fill.
    Returns (exit_reason, exit_price, exit_ts) or None."""
    ticker = pos['ticker']
    entry_ts = pos['entry_ts']
    stop = float(pos['stop_price'])
    take = float(pos['take_price']) if pos['take_price'] is not None else None
    df = db.select("""
        SELECT timestamp, high, low FROM trading.candles_1min_raw
        WHERE ticker=%s AND timestamp > %s ORDER BY timestamp
    """, (ticker, entry_ts)).to_dataframe()
    if df.empty:
        return None
    for _, candle in df.iterrows():
        if float(candle['low']) <= stop:
            return ('stop', stop, candle['timestamp'])
        if take is not None and float(candle['high']) >= take:
            return ('take', take, candle['timestamp'])
    return None


def catch_up_positions() -> dict:
    """Main catch-up: pull data, check open positions, close filled ones."""
    db = DBManager()
    open_pos = db.select("""
        SELECT id, ticker, entry_ts, entry_price, stop_price, take_price, lot_size, size_lots,
               signal_source, window_mode, rr_mode
        FROM trading.paper_positions WHERE status='open' ORDER BY id
    """).to_dataframe()
    if open_pos.empty:
        logger.info("Catch-up: no open positions")
        try: db.close_pool()
        except Exception: pass
        return {'open_positions': 0, 'closed': 0, 'candles_pulled': 0}

    logger.info(f"Catch-up: {len(open_pos)} open position(s)")
    today = date.today()
    total_candles = 0
    # Pull missing 1min data per ticker
    for ticker in open_pos['ticker'].unique():
        ticker_pos = open_pos[open_pos['ticker'] == ticker]
        from_date = pd.Timestamp(ticker_pos['entry_ts'].min()).date()
        logger.info(f"Catch-up {ticker}: pulling 1min data {from_date} -> {today}")
        total_candles += ensure_1min_data(ticker, from_date, today)

    # Check each position
    closed_count = 0
    for _, pos in open_pos.iterrows():
        result = check_position_catchup(db, pos)
        if result:
            exit_reason, exit_price, exit_ts = result
            close_position_at(db, int(pos['id']), pos['ticker'], exit_price, exit_reason, exit_ts,
                              float(pos['entry_price']), int(pos['lot_size']), int(pos['size_lots']))
            closed_count += 1

    remaining = len(open_pos) - closed_count
    logger.info(f"Catch-up done: {closed_count} closed, {remaining} still open, {total_candles} candles pulled")
    try: db.close_pool()
    except Exception: pass
    return {'open_positions': len(open_pos), 'closed': closed_count, 'still_open': remaining, 'candles_pulled': total_candles}


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    result = catch_up_positions()
    print(f"CATCHUP_RESULT: {result}")
