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
from app.analytics.paper_strategy import get_active_paper_strategy, PaperStrategyNotFoundError, PaperStrategyAmbiguousError
from app.analytics.orderbook_imbalance import (
    calculate_volume_imbalance,
    get_imbalance_threshold,
    get_recent_imbalance,
    passes_imbalance_filter,
)
from app.analytics.strategy_context import build_strategy_context
from app.analytics.strategy_engine import StrategyEvaluator
from app.analytics.strategy_backtest import compute_indicators_1min

TOP5_TICKERS = ['RUAL', 'GMKN', 'PIKK', 'GAZP', 'SIBN']
CONFIRM_TF_MINUTES = 10
ZONE_ATR = 0.5
SWING_WINDOW = 10
ENTRY_WINDOW_START = 7
ENTRY_WINDOW_END = 19
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


def check_signal(
    db: DBManager,
    ticker: str,
    levels,
    mode: str = 'base',
    config: Optional[Dict] = None,
) -> Optional[Dict]:
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
        imbalance = calculate_volume_imbalance(
            ob.get('bid_depth'),
            ob.get('ask_depth'),
        )
        if not passes_imbalance_filter(imbalance, config):
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



def _build_1m_context_for_signals(db: DBManager, ticker: str, config: Dict):
    """Build 1m context for signal generation (from online_candles_1min)."""
    confirm_windows = config.get('confirm_windows', [10])
    lookback = max(confirm_windows) * 3 + 30
    cutoff = _now_msk().replace(tzinfo=None) - timedelta(minutes=lookback)
    df = db.select(
        "SELECT timestamp, open, high, low, close FROM trading.online_candles_1min "
        "WHERE ticker=%s AND timestamp >= %s ORDER BY timestamp", (ticker, cutoff)
    ).to_dataframe()
    if df.empty:
        return None, None
    for c in ['open', 'high', 'low', 'close']:
        df[c] = pd.to_numeric(df[c], errors='coerce')

    patterns = config.get('patterns', {})
    if isinstance(patterns, dict):
        pattern_ids = list(patterns.keys())
    else:
        pattern_ids = patterns

    INDICATOR_PATTERNS = ('rsi_oversold', 'macd_bullish', 'bb_lower')
    if any(p in pattern_ids for p in INDICATOR_PATTERNS):
        df = compute_indicators_1min(df)

    confirm_series = []
    for w in confirm_windows:
        cs = build_confirm_series(aggregate_1min_to(df, w))
        confirm_series.append(([c[0] for c in cs], [c[1] for c in cs]))
    return df, confirm_series


def _rr_mode_from_config(config: Dict) -> str:
    """Determine rr_mode from config risk_reward."""
    rr = config.get('risk_reward')
    if not rr:
        return 'all'
    ratio = float(rr.get('reward', 2.0)) / float(rr.get('risk', 1.0))
    if ratio >= 2.0:
        return 'rr2'
    if ratio >= 1.5:
        return 'rr15'
    return 'all'


def _signal_source_from_config(config: Dict) -> str:
    """Determine signal_source from config patterns."""
    patterns = config.get('patterns', {})
    if isinstance(patterns, dict):
        pattern_ids = list(patterns.keys())
    else:
        pattern_ids = patterns
    if 'signal_4h_buy' in pattern_ids:
        return 'base_4hbuy'
    return 'base'


def run_signal_engine(tickers: List[str] = None, duration_minutes: int = 60,
                      check_interval_sec: int = 30, context_refresh_sec: int = 900):
    """Main loop: StrategyEvaluator.check_entry on live data, signals to alerts.

    Reads active paper strategy from DB via get_active_paper_strategy.
    Builds context via build_strategy_context. Preserves A/B factor metadata.
    """
    db = DBManager()

    try:
        strategy = get_active_paper_strategy(db)
    except PaperStrategyNotFoundError as e:
        logger.error(f"No active paper strategy: {e}")
        return
    except PaperStrategyAmbiguousError as e:
        logger.error(f"Ambiguous paper strategy: {e}")
        return

    config = strategy['config']
    strat_name = strategy['name']
    imbalance_threshold = get_imbalance_threshold(config)

    if tickers is None:
        rp = config.get('run_params') or {}
        tickers = list(rp.get('tickers') or [])
        if not tickers:
            tickers = get_trading_universe(db)

    logger.info(
        f"Signal engine: strategy '{strat_name}', {len(tickers)} tickers, "
        f"mandatory imbalance_threshold={imbalance_threshold}, "
        f"duration {duration_minutes} min"
    )

    evaluators: Dict[str, StrategyEvaluator] = {}
    last_processed: Dict[str, object] = {}

    for tk in tickers:
        ctx = build_strategy_context(db, tk, config)
        if ctx.get('status') == 'failed':
            logger.warning(f"No 4h context for {tk}; skipping ticker")
            continue
        ev = StrategyEvaluator(config)
        ev.load_context(ctx['levels'], ctx['ts_htf'], ctx['atr_by_ts'], ctx['buy_ts'], [],
                        ctx.get('signal_filter_series') or [], ctx.get('htf_bars'))
        evaluators[tk] = ev
        last_processed[tk] = None

    if not evaluators:
        logger.error("No evaluators built (no 4h context). Signal engine exiting.")
        return

    start_time = time.time()
    last_check = 0.0
    last_context_refresh = time.time()
    signals_emitted = 0

    signal_source = _signal_source_from_config(config)
    rr_mode = _rr_mode_from_config(config)

    while (time.time() - start_time) < (duration_minutes * 60):
        now = time.time()

        if now - last_context_refresh >= context_refresh_sec:
            for tk, ev in evaluators.items():
                ctx = build_strategy_context(db, tk, config)
                if ctx.get('status') != 'failed':
                    ev.update_context(levels=ctx['levels'], ts_4h=ctx['ts_htf'],
                                      atr_by_ts=ctx['atr_by_ts'], buy_ts=ctx['buy_ts'],
                                      signal_filter_series=ctx.get('signal_filter_series') or [],
                                      htf_bars=ctx.get('htf_bars'))
            last_context_refresh = now
            logger.info("4h context refreshed")

        if now - last_check >= check_interval_sec:
            now_msk = _now_msk()
            entry_start, entry_end = config.get('entry_window', (7, 19))
            in_window = entry_start <= now_msk.hour < entry_end
            window_mode = 'window' if in_window else 'always'

            for tk, ev in evaluators.items():
                df_1m, confirm_series = _build_1m_context_for_signals(db, tk, config)
                if df_1m is None or not confirm_series:
                    continue
                ev.update_context(confirm_series=confirm_series)

                bar = df_1m.iloc[-1]
                bar_ts = bar['timestamp']
                if last_processed[tk] is not None and bar_ts <= last_processed[tk]:
                    continue
                last_processed[tk] = bar_ts

                dec = ev.check_entry(bar)
                if dec is None:
                    continue

                imbalance = get_recent_imbalance(db, tk)
                if not passes_imbalance_filter(imbalance, config):
                    logger.info(
                        "SKIP signal: %s imbalance=%s threshold=%s",
                        tk,
                        imbalance,
                        imbalance_threshold,
                    )
                    continue

                price = dec['entry_price']
                stop = dec['stop']
                take = dec['take']
                risk = price - stop
                reward = take - price
                rr_ratio = (reward / risk) if risk > 0 else None

                signal = {
                    'ticker': tk,
                    'price': price,
                    'support_level': stop,
                    'take_level': take,
                    'rr_ratio': round(rr_ratio, 4) if rr_ratio is not None else None,
                    'signal_source': signal_source,
                    'window_mode': window_mode,
                    'rr_mode': rr_mode,
                    'strategy_name': strat_name,
                    'imbalance': imbalance,
                    'timestamp': now_msk.isoformat(),
                }

                save_signal(db, signal)
                signals_emitted += 1
                logger.info(f"Signal {signal_source}/{window_mode}/{rr_mode}: {tk} @ {price:.2f} (RR={rr_ratio})")

            last_check = now

        time.sleep(1)

    try:
        db.close_pool()
    except Exception:
        pass

    logger.info(f"Signal engine finished: {signals_emitted} signals emitted")
    return signals_emitted




if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_signal_engine(duration_minutes=duration)
