"""
Online signal engine: generates paper signals on live data (no trading).
Logic (consistent with levels_backtest.py):
  - 4h levels (from candles_aggregated, MOEX data) -> support/resistance zones.
  - 1min candles (from streaming, online_candles_1min) -> price in support zone?
  - BUY reversal confirmation: last CLOSED 10min candle closes above support zone.
  - Optional volume imbalance filter (from online_orderbook_aggregates).
A/B test, three factors:
  - signal_source: 'base' vs 'imbalance' (+ imbalance filter).
  - window_mode: 'window' (7-19 MSK) vs 'always' (24/7).
  - rr_mode: 'rr2' (reward >= 2*risk, like backtest) vs 'all' (no RR filter).
Dedup: one signal per (ticker, signal_source, window_mode, rr_mode, closed confirm candle).
Writes signals to trading.alerts (details incl. take_level, confirm_close_time, window_mode, rr_mode, rr_ratio).
"""
from __future__ import annotations
import time
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import pandas as pd

from app.db.db_manager import DBManager
from app.analytics.levels_engine import build_levels, nearest_level_at
from app.analytics.levels_backtest import compute_atr, aggregate_1min_to, build_confirm_series

logger = logging.getLogger(__name__)
from app.analytics.trading_config import get_trading_universe

TOP5_TICKERS = ['RUAL', 'GMKN', 'PIKK', 'GAZP', 'SIBN']
CONFIRM_TF_MINUTES = 10
ZONE_ATR = 0.5
SWING_WINDOW = 10
ENTRY_WINDOW_START = 7
ENTRY_WINDOW_END = 19
IMBALANCE_THRESHOLD = 1.0
RISK_REWARD_THRESHOLD = 2.0  # rr_mode='rr2' requires reward >= 2 * risk
RISK_REWARD_THRESHOLD_15 = 1.5  # rr_mode='rr15' requires reward >= 1.5 * risk


def _now_msk() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))


def get_4h_levels(db: DBManager, ticker: str, config: dict = None):
    """Build higher-TF levels from pattern parameters (delegates to build_strategy_context)."""
    from app.analytics.strategy_context import build_strategy_context

    if config is None:
        from app.analytics.trading_config import get_strategy
        config = get_strategy()

    # Levels-only view: avoid loading 4h BUY signals when only levels are needed.
    levels_config = dict(config)
    patterns = levels_config.get('patterns', ['levels_reversal'])

    if isinstance(patterns, list):
        levels_config['patterns'] = [p for p in patterns if p == 'levels_reversal'] or ['levels_reversal']
    elif isinstance(patterns, dict):
        levels_config['patterns'] = {'levels_reversal': patterns.get('levels_reversal', {})}
    else:
        levels_config['patterns'] = {'levels_reversal': {}}

    ctx = build_strategy_context(db, ticker, levels_config)
    if ctx.get('status') == 'failed':
        return []

    return ctx['levels']


def get_recent_1min_candles(db: DBManager, ticker: str, minutes: int = 30) -> pd.DataFrame:
    cutoff = _now_msk().replace(tzinfo=None) - timedelta(minutes=minutes)
    df = db.select("""
        SELECT timestamp, open, high, low, close FROM trading.online_candles_1min
        WHERE ticker=%s AND timestamp >= %s ORDER BY timestamp
    """, (ticker, cutoff)).to_dataframe()
    if not df.empty:
        for c in ['open', 'high', 'low', 'close']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def get_recent_orderbook(db: DBManager, ticker: str, minutes: int = 5) -> Optional[Dict]:
    cutoff = _now_msk().replace(tzinfo=None) - timedelta(minutes=minutes)
    df = db.select("""
        SELECT best_bid, best_ask, mid_price, bid_depth, ask_depth, volume_imbalance
        FROM trading.online_orderbook_aggregates
        WHERE ticker=%s AND timestamp >= %s ORDER BY timestamp DESC LIMIT 1
    """, (ticker, cutoff)).to_dataframe()
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def _active_4h_ts(db: DBManager, ticker: str):
    df = db.select("""
        SELECT max(timestamp) as max_ts FROM trading.candles_aggregated
        WHERE ticker=%s AND timeframe='4h'
    """, (ticker,)).to_dataframe()
    if df.empty or df.iloc[0]['max_ts'] is None:
        return None
    return pd.Timestamp(df.iloc[0]['max_ts'])


def _atr_estimate(db: DBManager, ticker: str) -> float:
    df_atr = db.select("""
        SELECT close FROM trading.candles_aggregated
        WHERE ticker=%s AND timeframe='4h' ORDER BY timestamp DESC LIMIT 20
    """, (ticker,)).to_dataframe()
    if df_atr.empty:
        return 0.0
    df_atr['close'] = pd.to_numeric(df_atr['close'], errors='coerce')
    return float(df_atr['close'].std()) if len(df_atr) > 1 else 0.0


def get_4h_buy_timestamps(db, ticker, min_signals=1):
    """Sorted timestamps of 4h BUY signals (total_signals >= min_signals) from trading.signals."""
    df = db.select(
        "SELECT timestamp FROM trading.signals "
        "WHERE ticker=%s AND timeframe='4h' AND signal='BUY' AND coalesce(total_signals,0) >= %s "
        "ORDER BY timestamp", (ticker, min_signals)).to_dataframe()
    if df.empty:
        return []
    return sorted(pd.to_datetime(df['timestamp']).tolist())


def _4h_buy_active(buy_ts, a4, now_naive):
    """True if the latest 4h BUY signal falls inside the current 4h bar (parity with levels_ts1)."""
    import bisect
    if not buy_ts:
        return False
    i = bisect.bisect_right(buy_ts, now_naive) - 1
    if i < 0:
        return False
    sig_ts = buy_ts[i]
    return a4 is not None and sig_ts <= now_naive and sig_ts >= a4


def check_signal(db: DBManager, ticker: str, levels, mode: str = 'base') -> Optional[Dict]:
    """BUY signal: price in support zone + last CLOSED 10min candle above zone (+ optional imbalance).
    Generated 24/7. Returns base signal dict; window_mode and rr_mode applied by caller."""
    now_msk = _now_msk()
    df_1m = get_recent_1min_candles(db, ticker, minutes=30)
    if df_1m.empty or len(df_1m) < 15:
        return None
    price = float(df_1m.iloc[-1]['close'])
    a4 = _active_4h_ts(db, ticker)
    if a4 is None:
        return None
    sup = nearest_level_at(levels, a4, price, 'support')
    if sup is None:
        return None
    zl, zu = sup['zone_lower'], sup['zone_upper']
    atr_val = _atr_estimate(db, ticker)
    in_zone = (zl <= price <= zu) or (zu < price <= zu + 0.5 * atr_val)
    if not in_zone:
        return None
    df_htf = aggregate_1min_to(df_1m, CONFIRM_TF_MINUTES)
    if df_htf.empty:
        return None
    confirm_series = build_confirm_series(df_htf)
    if not confirm_series:
        return None
    now_naive = now_msk.replace(tzinfo=None)
    closed = [c for c in confirm_series if c[0] <= now_naive]
    if not closed:
        return None
    confirm_close_time, last_confirm_close = closed[-1][0], closed[-1][1]
    if last_confirm_close <= zu:
        return None
    # A/B arm base_4hbuy: require an active 4h BUY signal (parity with backtest levels_ts1)
    if mode == 'base_4hbuy':
        if not _4h_buy_active(get_4h_buy_timestamps(db, ticker), a4, now_naive):
            return None
    imbalance = None
    if mode == 'imbalance':
        ob = get_recent_orderbook(db, ticker, minutes=5)
        if ob is None:
            return None
        imbalance = float(ob.get('volume_imbalance', 0) or 0)
        if imbalance <= IMBALANCE_THRESHOLD:
            return None
    res = nearest_level_at(levels, a4, price, 'resistance')
    take_level = float(res['level_price']) if res is not None else None
    # Risk/reward for rr_mode factor
    support_price = float(sup['level_price'])
    risk = price - support_price
    reward = (take_level - price) if take_level is not None else None
    rr_ratio = (reward / risk) if (risk > 0 and reward is not None) else None
    return {
        'ticker': ticker, 'price': price,
        'support_level': support_price,
        'take_level': take_level,
        'zone_lower': float(zl), 'zone_upper': float(zu),
        'confirm_close': float(last_confirm_close),
        'confirm_close_time': str(confirm_close_time),
        'imbalance': imbalance,
        'signal_source': mode,
        'rr_ratio': round(rr_ratio, 4) if rr_ratio is not None else None,
        'timestamp': now_msk.isoformat(),
    }


def check_sell_signal(db: DBManager, ticker: str, levels) -> Optional[Dict]:
    """SELL signal: support-zone breakdown. Generated 24/7."""
    now_msk = _now_msk()
    df_1m = get_recent_1min_candles(db, ticker, minutes=30)
    if df_1m.empty or len(df_1m) < 2:
        return None
    prev_close = float(df_1m.iloc[-2]['close'])
    curr_close = float(df_1m.iloc[-1]['close'])
    a4 = _active_4h_ts(db, ticker)
    if a4 is None:
        return None
    sup = nearest_level_at(levels, a4, curr_close, 'support')
    if sup is None:
        return None
    zl = sup['zone_lower']
    if prev_close >= zl and curr_close < zl:
        return {
            'ticker': ticker, 'price': curr_close,
            'support_level': float(sup['level_price']),
            'zone_lower': float(zl),
            'confirm_close_time': str(df_1m.iloc[-1]['timestamp']),
            'signal_source': 'sell',
            'window_mode': 'always', 'rr_mode': 'all',
            'timestamp': now_msk.isoformat(),
        }
    return None


def save_signal(db: DBManager, signal: Dict):
    try:
        db.execute("""
            INSERT INTO trading.alerts (alert_type, ticker, message, details, created_at)
            VALUES (%s, %s, %s, %s, now())
        """, ('signal', signal['ticker'],
              f"Signal {signal['signal_source']}/{signal.get('window_mode','always')}/{signal.get('rr_mode','all')}: {signal['ticker']} @ {signal['price']:.2f} (support {signal.get('support_level', 0):.2f})",
              json.dumps(signal, default=str)))
    except Exception as e:
        logger.error(f"save_signal error: {e}")


def _is_empty_levels(levels) -> bool:
    return (levels is None or getattr(levels, 'empty', False)
            or (isinstance(levels, (list, tuple)) and len(levels) == 0))


def run_signal_engine(tickers: List[str] = None, duration_minutes: int = 60,
                      check_interval_sec: int = 30, levels_refresh_sec: int = 900):
    """Main loop. Emits signals for all A/B factor combinations:
    signal_source x window_mode x rr_mode. Dedup per (ticker, source, window, rr, confirm candle)."""
    if tickers is None:
        tickers = get_trading_universe(DBManager())
    db = DBManager()
    logger.info(f"Signal engine started for {len(tickers)} tickers, duration {duration_minutes} min")
    levels_cache = {}
    for tk in tickers:
        levels_cache[tk] = get_4h_levels(db, tk)
        logger.info(f"4h levels for {tk}: {len(levels_cache[tk])} levels")
    start_time = time.time()
    last_check = 0
    last_levels_refresh = time.time()
    signals_generated = {'base': 0, 'imbalance': 0, 'sell': 0, 'base_4hbuy': 0}
    last_signal_confirm_time = {}  # (ticker, mode, window_mode, rr_mode) -> confirm_close_time
    while (time.time() - start_time) < (duration_minutes * 60):
        now = time.time()
        if now - last_levels_refresh >= levels_refresh_sec:
            for tk in tickers:
                levels_cache[tk] = get_4h_levels(db, tk)
            logger.info(f"Levels cache rebuilt ({len(tickers)} tickers)")
            last_levels_refresh = now
        if now - last_check >= check_interval_sec:
            in_window = ENTRY_WINDOW_START <= _now_msk().hour < ENTRY_WINDOW_END
            for tk in tickers:
                levels = levels_cache.get(tk, [])
                if _is_empty_levels(levels):
                    continue
                for mode in ('base', 'imbalance', 'base_4hbuy'):
                    sig = check_signal(db, tk, levels, mode=mode)
                    if not sig:
                        continue
                    cct = sig.get('confirm_close_time')
                    rr = sig.get('rr_ratio')
                    # Determine which rr_mode arms to emit
                    rr_modes = ['all']
                    if rr is not None and rr >= RISK_REWARD_THRESHOLD_15:
                        rr_modes.append('rr15')
                    if rr is not None and rr >= RISK_REWARD_THRESHOLD:
                        rr_modes.append('rr2')
                    # Determine which window_mode arms to emit
                    window_modes = ['always']
                    if in_window:
                        window_modes.append('window')
                    for wm in window_modes:
                        for rm in rr_modes:
                            sig_v = dict(sig)
                            sig_v['window_mode'] = wm
                            sig_v['rr_mode'] = rm
                            key = (tk, mode, wm, rm)
                            if last_signal_confirm_time.get(key) != cct:
                                save_signal(db, sig_v)
                                signals_generated[mode] += 1
                                last_signal_confirm_time[key] = cct
                                logger.info(f"Signal {mode}/{wm}/{rm}: {tk} @ {sig['price']:.2f} (RR={rr})")
                sig_sell = check_sell_signal(db, tk, levels)
                if sig_sell:
                    key = (tk, 'sell', 'always', 'all'); cct = sig_sell.get('confirm_close_time')
                    if last_signal_confirm_time.get(key) != cct:
                        save_signal(db, sig_sell); signals_generated['sell'] += 1
                        last_signal_confirm_time[key] = cct
                        logger.info(f"Signal SELL: {tk} @ {sig_sell['price']:.2f}")
            last_check = now
        time.sleep(1)
    try: db.close_pool()
    except Exception: pass
    logger.info(f"Signal engine finished: {signals_generated}")
    return signals_generated


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_signal_engine(duration_minutes=duration)
