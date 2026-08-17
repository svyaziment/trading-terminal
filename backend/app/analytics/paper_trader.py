"""
Paper trading engine with LIMIT + MARKET entry A/B (no real trading).

A/B factors stored per position:
  - signal_source: base / imbalance
  - window_mode: window (7-19 MSK) / always (24/7)
  - rr_mode: all / rr15 (RR>=1.5) / rr2 (RR>=2.0)
  - entry_mode: market (fill at best_ask now, guarded entry<take) / limit (limit order at signal price)

Position lifecycle:
  - market: signal -> OPEN immediately at best_ask (skip if entry_price >= take_level).
  - limit:  signal -> PENDING (limit at signal price) -> OPEN when candle touches limit
            (low<=limit<=high) -> closed_stop (market) / closed_take (limit);
            PENDING -> CANCELLED if not filled within PENDING_TTL_MIN (20) or price ran above take.
Dedup by (ticker, signal_source, window_mode, rr_mode, entry_mode). Commission 0.06% round trip.
"""
from __future__ import annotations
import time
import logging
import json
import ast
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import pandas as pd

from app.db.db_manager import DBManager
from app.notifications.telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)
from app.analytics.trading_config import get_trading_universe
from app.analytics.paper_strategy import get_active_paper_strategy
from app.core.config_manager import load_settings

COMMISSION_PER_SIDE = 0.0003
ROUND_TRIP = 2 * COMMISSION_PER_SIDE  # 0.06%
PENDING_TTL_MIN = 20  # cancel pending limit order if not filled within N minutes


def _now_msk() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))


def get_lot_sizes(db: DBManager, tickers: List[str]) -> Dict[str, int]:
    df = db.select("""
        SELECT ticker, lot_size FROM trading.instruments
        WHERE ticker = ANY(%s) AND lot_size IS NOT NULL
    """, (tickers,)).to_dataframe()
    return dict(zip(df['ticker'], df['lot_size'].astype(int))) if not df.empty else {}


def get_last_1min_candle(db: DBManager, ticker: str) -> Optional[Dict]:
    df = db.select("""
        SELECT timestamp, open, high, low, close FROM trading.online_candles_1min
        WHERE ticker=%s ORDER BY timestamp DESC LIMIT 1
    """, (ticker,)).to_dataframe()
    if df.empty:
        return None
    r = df.iloc[0]
    return {'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
            'low': float(r['low']), 'close': float(r['close'])}


def get_candles_since(db: DBManager, ticker: str, since_ts) -> pd.DataFrame:
    return db.select("""
        SELECT timestamp, open, high, low, close FROM trading.online_candles_1min
        WHERE ticker=%s AND timestamp >= %s ORDER BY timestamp
    """, (ticker, since_ts)).to_dataframe()


def get_best_ask(db: DBManager, ticker: str) -> Optional[float]:
    df = db.select("""
        SELECT best_ask FROM trading.online_orderbook_aggregates
        WHERE ticker=%s ORDER BY timestamp DESC LIMIT 1
    """, (ticker,)).to_dataframe()
    if df.empty or df.iloc[0]['best_ask'] is None:
        return None
    return float(df.iloc[0]['best_ask'])


def get_best_bid(db: DBManager, ticker: str) -> Optional[float]:
    df = db.select("""
        SELECT best_bid FROM trading.online_orderbook_aggregates
        WHERE ticker=%s ORDER BY timestamp DESC LIMIT 1
    """, (ticker,)).to_dataframe()
    if df.empty or df.iloc[0]['best_bid'] is None:
        return None
    return float(df.iloc[0]['best_bid'])


def get_processed_signal_ids(db: DBManager) -> set:
    df = db.select("SELECT signal_id FROM trading.paper_positions WHERE signal_id IS NOT NULL").to_dataframe()
    if df.empty:
        return set()
    return set(int(x) for x in df['signal_id'].tolist())


def count_entries_today(db: DBManager) -> int:
    today = _now_msk().replace(tzinfo=None).date()
    df = db.select("SELECT count(*) c FROM trading.paper_positions WHERE date(coalesce(entry_ts, limit_ts))=%s", (today,)).to_dataframe()
    return int(df.iloc[0]['c']) if not df.empty else 0


def _compute_pnl(entry_price, exit_price, lot_size, size_lots):
    gross_pct = (exit_price / entry_price - 1.0) * 100.0
    net_pct = gross_pct - ROUND_TRIP * 100.0
    pnl_rub = entry_price * size_lots * lot_size * (net_pct / 100.0)
    return round(pnl_rub, 2), round(net_pct, 4)


def create_pending_order(db, ticker, limit_price, stop_price, take_price, lot_size, size_lots, size_rub,
                         signal_source, window_mode, rr_mode, rr_ratio, entry_mode, signal_id, strategy_name=None):
    db.execute("""
        INSERT INTO trading.paper_positions
            (ticker, limit_price, limit_ts, stop_price, take_price, lot_size, size_lots, size_rub,
             status, signal_source, window_mode, rr_mode, rr_ratio, entry_mode, signal_id, strategy_name, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s,%s,now(),now())
    """, (ticker, limit_price, _now_msk().replace(tzinfo=None), stop_price, take_price,
          lot_size, size_lots, size_rub, signal_source, window_mode, rr_mode, rr_ratio, entry_mode, signal_id, strategy_name))
    logger.info(f"PENDING {ticker} [{signal_source}/{window_mode}/{rr_mode}/{entry_mode}]: limit {limit_price:.4f} (stop {stop_price:.2f}, take {take_price:.2f}, signal#{signal_id})")


def open_market_position(db, ticker, entry_price, stop_price, take_price, lot_size, size_lots, size_rub,
                         signal_source, window_mode, rr_mode, rr_ratio, signal_id, strategy_name=None,
                         notifier: TelegramNotifier | None = None):
    db.execute("""
        INSERT INTO trading.paper_positions
            (ticker, entry_ts, entry_price, stop_price, take_price, lot_size, size_lots, size_rub,
             status, signal_source, window_mode, rr_mode, rr_ratio, entry_mode, signal_id, strategy_name, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,'market',%s,%s,now(),now())
    """, (ticker, _now_msk().replace(tzinfo=None), entry_price, stop_price, take_price,
          lot_size, size_lots, size_rub, signal_source, window_mode, rr_mode, rr_ratio, signal_id, strategy_name))
    logger.info(f"OPEN(market) {ticker} [{signal_source}/{window_mode}/{rr_mode}]: {size_lots} lots @ {entry_price:.4f} (stop {stop_price:.2f}, take {take_price:.2f}, signal#{signal_id})")
    if notifier:
        notifier.notify_position_open(
            ticker=ticker,
            price=entry_price,
            size_lots=size_lots,
            lot_size=lot_size,
            reason=f"market/{signal_source}",
        )


def fill_entry(db, pos_id, ticker, entry_price, entry_ts, lot_size=1, size_lots=1,
               notifier: TelegramNotifier | None = None):
    db.execute("""
        UPDATE trading.paper_positions
        SET status='open', entry_price=%s, entry_ts=%s, updated_at=now()
        WHERE id=%s
    """, (entry_price, entry_ts, pos_id))
    logger.info(f"FILLED(limit) #{pos_id} {ticker}: entry @ {entry_price:.4f} at {entry_ts}")
    if notifier:
        notifier.notify_position_open(
            ticker=ticker,
            price=entry_price,
            size_lots=size_lots,
            lot_size=lot_size,
            reason="limit fill",
        )


def close_position(db, pos_id, ticker, exit_price, exit_reason, exit_ts, entry_price, lot_size, size_lots,
                   notifier: TelegramNotifier | None = None):
    pnl_rub, net_pct = _compute_pnl(entry_price, exit_price, lot_size, size_lots)
    db.execute("""
        UPDATE trading.paper_positions
        SET status=%s, exit_ts=%s, exit_price=%s, exit_reason=%s, pnl_rub=%s, pnl_pct=%s, updated_at=now()
        WHERE id=%s
    """, (f'closed_{exit_reason}', exit_ts, exit_price, exit_reason, pnl_rub, net_pct, pos_id))
    logger.info(f"CLOSE #{pos_id} {ticker} ({exit_reason}) @ {exit_price:.4f} at {exit_ts}: PnL {pnl_rub:.0f} RUB ({net_pct:.2f}%)")
    if notifier:
        notifier.notify_position_close(
            ticker=ticker,
            price=exit_price,
            size_lots=size_lots,
            lot_size=lot_size,
            pnl_rub=pnl_rub,
            pnl_pct=net_pct,
            reason=exit_reason,
        )
    return pnl_rub


def cancel_pending(db, pos_id, ticker, reason):
    db.execute("UPDATE trading.paper_positions SET status='cancelled', exit_reason=%s, updated_at=now() WHERE id=%s", (reason, pos_id))
    logger.info(f"CANCELLED #{pos_id} {ticker}: {reason}")


def process_signals(db, tickers, lot_sizes, max_positions, max_entries_per_day, per_trade_rub,
                    strategy_name=None, config=None, notifier: TelegramNotifier | None = None):
    """Create positions/orders from new signals: market (open now) + limit (pending) arms."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    new_signals = db.select("""
        SELECT id, ticker, details FROM trading.alerts
        WHERE alert_type='signal' AND created_at >= %s ORDER BY id
    """, (cutoff,)).to_dataframe()
    processed_ids = get_processed_signal_ids(db)
    for _, sig in new_signals.iterrows():
        sig_id = int(sig['id'])
        if sig_id in processed_ids:
            continue
        try:
            raw = sig['details']
            if isinstance(raw, dict):
                details = raw
            else:
                s = str(raw)
                try:
                    details = json.loads(s)
                except (json.JSONDecodeError, ValueError):
                    details = ast.literal_eval(s)
        except Exception as e:
            logger.info(f"SKIP signal #{sig_id}: details parse error ({e})")
            continue
        src = details.get('signal_source')
        if src not in ('base', 'imbalance', 'base_4hbuy'):
            continue
        tk = details.get('ticker')
        if tk not in tickers:
            continue
        # Issue #27: A/B factors from config

        if config and 'risk_reward' in config:

            rr = config['risk_reward']

            ratio = float(rr.get('reward', 2.0)) / float(rr.get('risk', 1.0))

            rr_ratio = round(ratio, 4) if ratio else None

            rmode = 'rr2' if ratio >= 2.0 else ('rr15' if ratio >= 1.5 else 'all')

        else:

            rr_ratio = details.get('rr_ratio')

            rmode = details.get('rr_mode', 'all')

        wmode = 'window'
        sig_price = details.get('price')
        stop_price = details.get('support_level')
        take_price = details.get('take_level')
        if sig_price is None or stop_price is None or take_price is None:
            logger.info(f"SKIP signal #{sig_id} {tk}: missing price/support/take")
            continue
        stop_price = float(stop_price); take_price = float(take_price); sig_price = float(sig_price)
        if sig_price >= take_price:
            logger.info(f"SKIP signal #{sig_id} {tk}: signal price {sig_price:.4f} >= take {take_price:.4f}")
            continue
        lot_size = lot_sizes.get(tk, 1)
        price_per_lot = sig_price * lot_size
        size_lots = int(per_trade_rub // price_per_lot) if price_per_lot > 0 else 0
        if size_lots < 1:
            logger.info(f"SKIP signal #{sig_id} {tk}: lot too expensive ({price_per_lot:.0f} RUB/lot)")
            continue
        size_rub = size_lots * price_per_lot
        # Two entry arms: market + limit
        for entry_mode in ('market',):  # Issue #27: market only
            active = db.select("""
                SELECT ticker, signal_source, window_mode, rr_mode, entry_mode
                FROM trading.paper_positions WHERE status IN ('pending','open')
            """).to_dataframe()
            if len(active) >= max_positions:
                logger.info(f"SKIP signal #{sig_id} {tk} [{entry_mode}]: max active positions ({max_positions})")
                continue
            if not active.empty:
                dup = active[(active['ticker'] == tk) & (active['signal_source'] == src) &
                             (active['window_mode'] == wmode) & (active['rr_mode'] == rmode) &
                             (active['entry_mode'] == entry_mode)]
                if not dup.empty:
                    continue
            if count_entries_today(db) >= max_entries_per_day:
                logger.info(f"SKIP signal #{sig_id} {tk}: max entries/day ({max_entries_per_day})")
                continue
            if entry_mode == 'market':
                entry_price = get_best_ask(db, tk)
                if entry_price is None:
                    c = get_last_1min_candle(db, tk)
                    if c is None:
                        logger.info(f"SKIP signal #{sig_id} {tk} [market]: no entry price")
                        continue
                    entry_price = c['close']
                if entry_price >= take_price:
                    logger.info(f"SKIP signal #{sig_id} {tk} [market]: entry {entry_price:.4f} >= take {take_price:.4f}")
                    continue
                open_market_position(db, tk, entry_price, stop_price, take_price, lot_size, size_lots, size_rub,
                                     src, wmode, rmode, rr_ratio, sig_id, strategy_name, notifier)
            else:  # limit
                create_pending_order(db, tk, sig_price, stop_price, take_price, lot_size, size_lots, size_rub,
                                     src, wmode, rmode, rr_ratio, 'limit', sig_id, strategy_name)
        processed_ids.add(sig_id)


def monitor_pending(db, notifier: TelegramNotifier | None = None):
    """Fill pending limit orders when price touches limit; cancel expired / price above take."""
    pending = db.select("""
        SELECT id, ticker, limit_price, limit_ts, take_price, lot_size, size_lots
        FROM trading.paper_positions WHERE status='pending' ORDER BY id
    """).to_dataframe()
    now_naive = _now_msk().replace(tzinfo=None)
    for _, p in pending.iterrows():
        tk = p['ticker']
        limit_price = float(p['limit_price'])
        limit_ts = p['limit_ts']
        take_price = float(p['take_price']) if p['take_price'] is not None else None
        age_sec = (now_naive - limit_ts).total_seconds()
        if age_sec > PENDING_TTL_MIN * 60:
            cancel_pending(db, int(p['id']), tk, f'expired ({int(age_sec//60)}min)')
            continue
        candles = get_candles_since(db, tk, limit_ts)
        if candles.empty:
            continue
        for _, c in candles.iterrows():
            if take_price is not None and float(c['high']) >= take_price and float(c['low']) > limit_price:
                cancel_pending(db, int(p['id']), tk, 'price above take before fill')
                break
            if float(c['low']) <= limit_price <= float(c['high']):
                fill_entry(
                    db,
                    int(p['id']),
                    tk,
                    limit_price,
                    c['timestamp'],
                    int(p['lot_size']),
                    int(p['size_lots']),
                    notifier,
                )
                break


def monitor_open(db, notifier: TelegramNotifier | None = None):
    """Check stop (market) / take (limit) for open positions."""
    open_pos = db.select("""
        SELECT id, ticker, entry_ts, entry_price, stop_price, take_price, lot_size, size_lots
        FROM trading.paper_positions WHERE status='open' ORDER BY id
    """).to_dataframe()
    for _, p in open_pos.iterrows():
        tk = p['ticker']
        entry_ts = p['entry_ts']
        entry_price = float(p['entry_price'])
        stop = float(p['stop_price'])
        take = float(p['take_price']) if p['take_price'] is not None else None
        candles = get_candles_since(db, tk, entry_ts)
        if candles.empty:
            continue
        for _, c in candles.iterrows():
            if c['timestamp'] <= entry_ts:
                continue
            if float(c['low']) <= stop:
                close_position(db, int(p['id']), tk, stop, 'stop', c['timestamp'],
                               entry_price, int(p['lot_size']), int(p['size_lots']), notifier)
                break
            if take is not None and float(c['high']) >= take:
                close_position(db, int(p['id']), tk, take, 'take', c['timestamp'],
                               entry_price, int(p['lot_size']), int(p['size_lots']), notifier)
                break


def write_equity(db, capital, notifier: TelegramNotifier | None = None,
                 critical_drawdown_pct: float = 2.0):
    realized = db.select("SELECT coalesce(sum(pnl_rub),0) s FROM trading.paper_positions WHERE status LIKE 'closed_%'").to_dataframe()
    realized_pnl = float(realized.iloc[0]['s']) if not realized.empty else 0.0
    open_pos = db.select("SELECT ticker, entry_price, lot_size, size_lots FROM trading.paper_positions WHERE status='open'").to_dataframe()
    unrealized = 0.0
    for _, p in open_pos.iterrows():
        bid = get_best_bid(db, p['ticker'])
        if bid is None:
            c = get_last_1min_candle(db, p['ticker'])
            bid = c['close'] if c else float(p['entry_price'])
        unrealized += (bid - float(p['entry_price'])) * int(p['size_lots']) * int(p['lot_size'])
    equity = capital + realized_pnl + unrealized
    peak_df = db.select("SELECT max(equity_rub) m FROM trading.paper_equity").to_dataframe()
    peak = float(peak_df.iloc[0]['m']) if not peak_df.empty and peak_df.iloc[0]['m'] is not None else equity
    peak = max(peak, equity)
    dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
    previous_df = db.select("""
        SELECT equity_rub, drawdown_pct
        FROM trading.paper_equity ORDER BY timestamp DESC LIMIT 1
    """).to_dataframe()
    previous_equity = (
        float(previous_df.iloc[0]['equity_rub']) if not previous_df.empty else None
    )
    previous_dd = (
        float(previous_df.iloc[0]['drawdown_pct']) if not previous_df.empty else None
    )
    n_open = len(open_pos)
    pend = db.select("SELECT count(*) c FROM trading.paper_positions WHERE status='pending'").to_dataframe()
    n_pending = int(pend.iloc[0]['c']) if not pend.empty else 0
    db.execute("""
        INSERT INTO trading.paper_equity (timestamp, equity_rub, realized_pnl, open_positions, drawdown_pct, created_at)
        VALUES (%s,%s,%s,%s,%s,now())
    """, (_now_msk().replace(tzinfo=None), round(equity, 2), round(realized_pnl, 2), n_open + n_pending, round(dd, 3)))
    if notifier and equity <= 0 and (previous_equity is None or previous_equity > 0):
        notifier.notify_critical(
            event="GAME OVER",
            details=f"Equity {equity:.2f} RUB, drawdown {dd:.2f}%",
        )
    elif (
        notifier
        and critical_drawdown_pct > 0
        and dd >= critical_drawdown_pct
        and (previous_dd is None or previous_dd < critical_drawdown_pct)
    ):
        notifier.notify_critical(
            event="LARGE DRAWDOWN",
            details=(
                f"Drawdown {dd:.2f}% превысил лимит "
                f"{critical_drawdown_pct:.2f}%; equity {equity:.2f} RUB"
            ),
        )
    return equity


def run_paper_trader(tickers=None, duration_minutes=60, check_interval_sec=30,
                     capital=100000.0, per_trade_rub=1000.0,
                     max_positions=80, max_entries_per_day=400):
    # Issue #27: read strategy from DB
    db = DBManager()
    settings = load_settings()
    notifier = TelegramNotifier(settings.telegram)
    strategy_name = None
    config = None
    try:
        strategy = get_active_paper_strategy(db)
        strategy_name = strategy['name']
        config = strategy['config']
    except Exception as e:
        logger.error(f"Paper strategy error: {e}")
    if tickers is None:
        tickers = get_trading_universe(db)
    lot_sizes = get_lot_sizes(db, tickers)
    logger.info(f"Paper trader started: strategy '{strategy_name}', capital {capital:.0f}, per_trade {per_trade_rub:.0f}, max_pos {max_positions}, lots {lot_sizes}, telegram={notifier.enabled}")
    start_time = time.time()
    last_check = 0
    last_equity_write = 0
    while (time.time() - start_time) < (duration_minutes * 60):
        now = time.time()
        if now - last_check >= check_interval_sec:
            process_signals(db, tickers, lot_sizes, max_positions, max_entries_per_day, per_trade_rub,
                            strategy_name=strategy_name, config=config, notifier=notifier)
            monitor_pending(db, notifier)
            monitor_open(db, notifier)
            last_check = now
        if now - last_equity_write >= 60:
            write_equity(db, capital, notifier, settings.risk.max_daily_loss_pct)
            last_equity_write = now
        time.sleep(1)
    write_equity(db, capital, notifier, settings.risk.max_daily_loss_pct)
    try: db.close_pool()
    except Exception: pass
    logger.info("Paper trader finished")


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_paper_trader(duration_minutes=duration)
