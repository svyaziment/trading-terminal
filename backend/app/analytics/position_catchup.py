"""
Position catch-up: on startup, retroactively process PENDING and OPEN positions
against historical 1min candles (candles_1min_raw from MOEX).

Mirrors the live paper_trader logic (monitor_pending + monitor_open):
  Phase 1 - resolve pending (scan candles from limit_ts):
    - price ran above take before fill -> CANCELLED ('price above take before fill');
    - candle touches limit (low <= limit_price <= high) -> OPEN (entry at limit_price);
    - TTL (PENDING_TTL_MIN) expired without fill -> CANCELLED ('expired').
  Phase 2 - check open (incl. just-filled; scan candles from entry_ts, skip entry candle):
    - low <= stop -> closed_stop (market); high >= take -> closed_take (limit).
Pulls any missing 1min days from MOEX first. Idempotent: only touches pending/open.
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
PENDING_TTL_MIN = 20  # must match paper_trader.py


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
        time.sleep(0.3)
    conn.close()
    return total


def _candles_since(db, ticker: str, since_ts) -> pd.DataFrame:
    return db.select("""
        SELECT timestamp, high, low FROM trading.candles_1min_raw
        WHERE ticker=%s AND timestamp >= %s ORDER BY timestamp
    """, (ticker, since_ts)).to_dataframe()


def fill_pending(db, pos_id, ticker, entry_price, entry_ts):
    db.execute("""
        UPDATE trading.paper_positions
        SET status='open', entry_price=%s, entry_ts=%s, updated_at=now()
        WHERE id=%s
    """, (entry_price, entry_ts, pos_id))
    logger.info(f"CATCHUP FILL #{pos_id} {ticker}: limit filled @ {entry_price:.4f} at {entry_ts}")


def cancel_pending(db, pos_id, ticker, reason):
    db.execute("UPDATE trading.paper_positions SET status='cancelled', exit_reason=%s, updated_at=now() WHERE id=%s", (reason, pos_id))
    logger.info(f"CATCHUP CANCEL #{pos_id} {ticker}: {reason}")


def close_position_at(db, pos_id, ticker, exit_price, exit_reason, exit_ts, entry_price, lot_size, size_lots):
    gross_pct = (exit_price / entry_price - 1.0) * 100.0
    net_pct = gross_pct - ROUND_TRIP * 100.0
    pnl_rub = entry_price * size_lots * lot_size * (net_pct / 100.0)
    db.execute("""
        UPDATE trading.paper_positions
        SET status=%s, exit_ts=%s, exit_price=%s, exit_reason=%s, pnl_rub=%s, pnl_pct=%s, updated_at=now()
        WHERE id=%s
    """, (f'closed_{exit_reason}', exit_ts, exit_price, exit_reason, round(pnl_rub, 2), round(net_pct, 4), pos_id))
    logger.info(f"CATCHUP CLOSE #{pos_id} {ticker} ({exit_reason}) @ {exit_price:.4f} at {exit_ts}: PnL {pnl_rub:.0f} RUB ({net_pct:.2f}%)")
    return pnl_rub


def resolve_pending(db, pos, now_ts) -> Optional[str]:
    """Resolve one pending position -> 'filled' / 'cancelled' / None (still pending)."""
    ticker = pos['ticker']
    limit_price = float(pos['limit_price']) if pos['limit_price'] is not None else None
    limit_ts = pos['limit_ts'] if pos['limit_ts'] is not None else pos['created_at']
    take = float(pos['take_price']) if pos['take_price'] is not None else None
    if limit_price is None:
        cancel_pending(db, int(pos['id']), ticker, 'no limit_price')
        return 'cancelled'
    age_min = (now_ts - pd.Timestamp(limit_ts)).total_seconds() / 60.0
    candles = _candles_since(db, ticker, limit_ts)
    if candles.empty:
        if age_min > PENDING_TTL_MIN:
            cancel_pending(db, int(pos['id']), ticker, f'expired ({int(age_min)}min, no candles)')
            return 'cancelled'
        return None
    for _, c in candles.iterrows():
        cts = pd.Timestamp(c['timestamp'])
        # cancel if price ran above take before fill
        if take is not None and float(c['high']) >= take and float(c['low']) > limit_price:
            cancel_pending(db, int(pos['id']), ticker, 'price above take before fill')
            return 'cancelled'
        # limit fill: candle touches limit price
        if float(c['low']) <= limit_price <= float(c['high']):
            fill_pending(db, int(pos['id']), ticker, limit_price, cts)
            return 'filled'
        # TTL expired mid-scan
        if (cts - pd.Timestamp(limit_ts)).total_seconds() / 60.0 > PENDING_TTL_MIN:
            cancel_pending(db, int(pos['id']), ticker, f'expired ({PENDING_TTL_MIN}min)')
            return 'cancelled'
    if age_min > PENDING_TTL_MIN:
        cancel_pending(db, int(pos['id']), ticker, f'expired ({int(age_min)}min)')
        return 'cancelled'
    return None


def check_open_stop_take(db, pos) -> Optional[str]:
    """Check one open position for stop/take (skip entry candle) -> 'stop'/'take'/None."""
    ticker = pos['ticker']
    entry_ts = pos['entry_ts']
    entry_price = float(pos['entry_price'])
    stop = float(pos['stop_price'])
    take = float(pos['take_price']) if pos['take_price'] is not None else None
    candles = _candles_since(db, ticker, entry_ts)
    if candles.empty:
        return None
    for _, c in candles.iterrows():
        if pd.Timestamp(c['timestamp']) <= pd.Timestamp(entry_ts):
            continue
        if float(c['low']) <= stop:
            close_position_at(db, int(pos['id']), ticker, stop, 'stop', c['timestamp'],
                              entry_price, int(pos['lot_size']), int(pos['size_lots']))
            return 'stop'
        if take is not None and float(c['high']) >= take:
            close_position_at(db, int(pos['id']), ticker, take, 'take', c['timestamp'],
                              entry_price, int(pos['lot_size']), int(pos['size_lots']))
            return 'take'
    return None


def catch_up_positions() -> dict:
    """Main catch-up: resolve pending, then check open (incl. just-filled) for stop/take."""
    db = DBManager()
    positions = db.select("""
        SELECT id, ticker, limit_price, limit_ts, entry_ts, entry_price, stop_price, take_price,
               lot_size, size_lots, signal_source, window_mode, rr_mode, entry_mode, status, created_at
        FROM trading.paper_positions WHERE status IN ('pending','open') ORDER BY id
    """).to_dataframe()
    if positions.empty:
        logger.info("Catch-up: no pending/open positions")
        try: db.close_pool()
        except Exception: pass
        return {'pending': 0, 'open': 0, 'filled': 0, 'cancelled': 0, 'closed': 0, 'still_open': 0, 'candles_pulled': 0}

    n_pending = int((positions['status'] == 'pending').sum())
    n_open = int((positions['status'] == 'open').sum())
    logger.info(f"Catch-up: {n_pending} pending, {n_open} open position(s)")

    today = date.today()
    total_candles = 0
    for ticker in positions['ticker'].unique():
        tp = positions[positions['ticker'] == ticker]
        ref_ts = tp['limit_ts'].combine_first(tp['entry_ts']).combine_first(tp['created_at']).min()
        from_date = pd.Timestamp(ref_ts).date()
        logger.info(f"Catch-up {ticker}: pulling 1min data {from_date} -> {today}")
        total_candles += ensure_1min_data(ticker, from_date, today)

    now_ts = pd.Timestamp.now()
    # Phase 1: resolve pending -> open/cancelled
    filled_count = 0
    cancelled_count = 0
    for _, pos in positions[positions['status'] == 'pending'].iterrows():
        result = resolve_pending(db, pos, now_ts)
        if result == 'filled':
            filled_count += 1
        elif result == 'cancelled':
            cancelled_count += 1

    # Phase 2: re-fetch open (incl. just-filled) and check stop/take
    open_pos = db.select("""
        SELECT id, ticker, entry_ts, entry_price, stop_price, take_price, lot_size, size_lots
        FROM trading.paper_positions WHERE status='open' ORDER BY id
    """).to_dataframe()
    closed_count = 0
    for _, pos in open_pos.iterrows():
        if check_open_stop_take(db, pos) is not None:
            closed_count += 1

    still_open = len(open_pos) - closed_count
    logger.info(f"Catch-up done: {filled_count} filled, {cancelled_count} cancelled, {closed_count} closed, {still_open} still open, {total_candles} candles pulled")
    try: db.close_pool()
    except Exception: pass
    return {'pending': n_pending, 'open': n_open, 'filled': filled_count, 'cancelled': cancelled_count,
            'closed': closed_count, 'still_open': still_open, 'candles_pulled': total_candles}


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    result = catch_up_positions()
    print(f"CATCHUP_RESULT: {result}")
