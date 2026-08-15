"""
Live strategy engine: runs the unified StrategyEvaluator on live data (paper trading).

Live counterpart of strategy_backtest.py. Uses the SAME StrategyEvaluator.check_entry
logic (single brain), so paper trading runs exactly the strategy that was backtested.
Differences: data source (live 1min candles from online_candles_1min + orderbook
aggregates) and execution (paper_trader consumes the emitted trading.alerts signals).

Live-only overlays (e.g. orderbook_imbalance) are AND-filters applied at signal
emission time; the backtest ignores them (no historical order book).
"""
from __future__ import annotations
import time
import logging
import json
import ast
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.db.db_manager import DBManager
from app.analytics.levels_engine import build_levels
from app.analytics.levels_backtest import compute_atr, aggregate_1min_to, build_confirm_series
from app.analytics.strategy_engine import StrategyEvaluator
from app.analytics.strategy_backtest import compute_indicators_1min
from app.analytics.trading_config import get_trading_universe

logger = logging.getLogger(__name__)

IMBALANCE_THRESHOLD = 1.0

MAX_RR_RATIO_DEFAULT = 10.0
IMBALANCE_THRESHOLD_DEFAULT = 1.0

INDICATOR_PATTERNS = ('rsi_oversold', 'macd_bullish', 'bb_lower')


def _now_msk() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))


def _parse_config(raw) -> Optional[Dict]:
    """Normalize JSONB config (DBManager returns Python-repr str) to a dict."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    s = str(raw)
    try:
        return json.loads(s)
    except (ValueError, json.JSONDecodeError):
        pass
    try:
        return ast.literal_eval(s)
    except Exception:
        return None


def get_paper_strategy(db: DBManager) -> Tuple[Optional[Dict], List[str], Optional[str]]:
    """Active paper strategy (in_paper_test=true AND locked=true).
    Delegates to paper_strategy.get_active_paper_strategy for validation.
    Returns (config, tickers, name)."""
    from app.analytics.paper_strategy import get_active_paper_strategy, PaperStrategyNotFoundError, PaperStrategyAmbiguousError

    try:
        strategy = get_active_paper_strategy(db)
    except (PaperStrategyNotFoundError, PaperStrategyAmbiguousError) as e:
        logger.error(f"get_paper_strategy: {e}")
        return None, [], None

    config = strategy['config']
    rp = config.get('run_params') or {}
    tickers = list(rp.get('tickers') or [])
    if not tickers:
        tickers = get_trading_universe(db)
    return config, tickers, strategy['name']

def build_4h_context(db: DBManager, ticker: str, config: Dict) -> Optional[Dict]:
    """4h levels + BUY signals (delegates to build_strategy_context)."""
    from app.analytics.strategy_context import build_strategy_context
    ctx = build_strategy_context(db, ticker, config)
    if ctx.get('status') == 'failed':
        return None
    return {
        'levels': ctx['levels'],
        'ts_4h': ctx['ts_htf'],
        'atr_by_ts': ctx['atr_by_ts'],
        'buy_ts': ctx['buy_ts'],
    }


def build_1m_context(db: DBManager, ticker: str, config: Dict):
    """Recent live 1min candles + multi-window confirmation series (+ indicators)."""
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
    if any(p in config.get('patterns', []) for p in INDICATOR_PATTERNS):
        df = compute_indicators_1min(df)
    confirm_series = []
    for w in confirm_windows:
        cs = build_confirm_series(aggregate_1min_to(df, w))
        confirm_series.append(([c[0] for c in cs], [c[1] for c in cs]))
    return df, confirm_series


def get_recent_imbalance(db: DBManager, ticker: str, minutes: int = 5) -> Optional[float]:
    cutoff = _now_msk().replace(tzinfo=None) - timedelta(minutes=minutes)
    df = db.select(
        "SELECT volume_imbalance FROM trading.online_orderbook_aggregates "
        "WHERE ticker=%s AND timestamp >= %s ORDER BY timestamp DESC LIMIT 1", (ticker, cutoff)
    ).to_dataframe()
    if df.empty or df.iloc[0]['volume_imbalance'] is None:
        return None
    return float(df.iloc[0]['volume_imbalance'])


def _rr_mode(config: Dict) -> str:
    rr = config.get('risk_reward')
    if not rr:
        return 'all'
    ratio = float(rr.get('reward', 2.0)) / float(rr.get('risk', 1.0))
    if ratio >= 2.0:
        return 'rr2'
    if ratio >= 1.5:
        return 'rr15'
    return 'all'


def _can_emit_signal(db: DBManager, ticker: str, config: Dict) -> bool:
    """Check if a signal can be emitted for this ticker.

    Rules:
    1. No signal if there is an open or pending position for this ticker.
    2. After position close (stop/take/cancel), cooldown reentry_cooldown_min.

    Returns True if signal can be emitted.
    """
    cooldown_min = int(config.get('reentry_cooldown_min', 30))

    # Rule 1: check open/pending positions
    df_active = db.select(
        "SELECT COUNT(*) AS cnt FROM trading.paper_positions "
        "WHERE ticker = %s AND status IN ('open', 'pending')",
        (ticker,)
    ).to_dataframe()
    if not df_active.empty and int(df_active.iloc[0]['cnt']) > 0:
        return False

    # Rule 2: check cooldown after last closed/cancelled position
    # For cancelled positions exit_ts is NULL, so use COALESCE
    df_last = db.select(
        "SELECT COALESCE(exit_ts, updated_at, created_at) AS close_ts "
        "FROM trading.paper_positions "
        "WHERE ticker = %s AND status IN ('closed_stop', 'closed_take', 'cancelled') "
        "ORDER BY close_ts DESC LIMIT 1",
        (ticker,)
    ).to_dataframe()

    if df_last.empty or df_last.iloc[0]['close_ts'] is None:
        # No closed positions yet, allow signal
        return True

    last_close_ts = df_last.iloc[0]['close_ts']
    if hasattr(last_close_ts, 'to_pydatetime'):
        last_close_ts = last_close_ts.to_pydatetime()
    if hasattr(last_close_ts, 'tzinfo') and last_close_ts.tzinfo is not None:
        last_close_ts = last_close_ts.replace(tzinfo=None)

    now_naive = _now_msk().replace(tzinfo=None)
    elapsed_min = (now_naive - last_close_ts).total_seconds() / 60.0

    return elapsed_min >= cooldown_min


def emit_signal(db: DBManager, ticker: str, dec: Dict, config: Dict,
                imbalance: Optional[float] = None) -> None:
    """Write a paper signal to trading.alerts (format paper_trader consumes)."""
    patterns = config.get('patterns', [])

    # Issue #35: max_rr_ratio filter
    max_rr_ratio = float(config.get('max_rr_ratio', MAX_RR_RATIO_DEFAULT))
    if risk > 0 and rr_ratio is not None and rr_ratio > max_rr_ratio:
        logger.info(f"SKIP signal {src}: {ticker} rr_ratio={rr_ratio:.2f} > max_rr_ratio={max_rr_ratio}")
        return
    if imbalance is not None:
        src = 'imbalance'
    elif 'signal_4h_buy' in patterns:
        src = 'base_4hbuy'
    else:
        src = 'base'
    price = dec['entry_price']
    stop = dec['stop']
    take = dec['take']
    risk = price - stop
    reward = take - price
    rr_ratio = (reward / risk) if risk > 0 else None
    signal = {
        'ticker': ticker, 'price': price,
        'support_level': stop, 'take_level': take,
        'rr_ratio': round(rr_ratio, 4) if rr_ratio is not None else None,
        'signal_source': src,
        'window_mode': 'window',
        'rr_mode': _rr_mode(config),
        'imbalance': imbalance,
        'timestamp': _now_msk().isoformat(),
    }
    db.execute(
        "INSERT INTO trading.alerts (alert_type, ticker, message, details, created_at) "
        "VALUES (%s, %s, %s, %s, now())",
        ('signal', ticker,
         f"Signal {src}: {ticker} @ {price:.2f} (support {stop:.2f}, take {take:.2f})",
         json.dumps(signal, default=str)))
    logger.info(f"LIVE SIGNAL {src}: {ticker} @ {price:.4f} (stop {stop:.2f}, take {take:.2f}, RR={rr_ratio})")


def run_live_engine(duration_minutes: int = 60, check_interval_sec: int = 30,
                    context_refresh_sec: int = 900) -> None:
    """Main loop: feed live 1min bars into per-ticker StrategyEvaluators (unified
    entry logic) and emit paper signals to trading.alerts."""
    db = DBManager()
    config, tickers, strat_name = get_paper_strategy(db)
    if config is None:
        logger.error("No active paper strategy (in_paper_test=true). Live engine exiting.")
        return
    logger.info(f"Live engine: strategy '{strat_name}', {len(tickers)} tickers {tickers}, "
                f"imbalance_overlay=True (mandatory), duration={duration_minutes}min")

    evaluators: Dict[str, StrategyEvaluator] = {}
    last_processed: Dict[str, object] = {}
    for tk in tickers:
        ctx4 = build_4h_context(db, tk, config)
        if ctx4 is None:
            logger.warning(f"No 4h context for {tk}; skipping ticker")
            continue
        ev = StrategyEvaluator(config)
        ev.load_context(ctx4['levels'], ctx4['ts_4h'], ctx4['atr_by_ts'], ctx4['buy_ts'], [])
        evaluators[tk] = ev
        last_processed[tk] = None
    if not evaluators:
        logger.error("No evaluators built (no 4h context). Live engine exiting.")
        return

    start_time = time.time()
    last_check = 0.0
    last_context_refresh = time.time()
    signals_emitted = 0

    while (time.time() - start_time) < (duration_minutes * 60):
        now = time.time()
        # periodic 4h context refresh (levels + BUY signals)
        if now - last_context_refresh >= context_refresh_sec:
            for tk, ev in evaluators.items():
                ctx4 = build_4h_context(db, tk, config)
                if ctx4 is not None:
                    ev.update_context(levels=ctx4['levels'], ts_4h=ctx4['ts_4h'],
                                      atr_by_ts=ctx4['atr_by_ts'], buy_ts=ctx4['buy_ts'])
            last_context_refresh = now
            logger.info("4h context refreshed")
        # per-check: fresh 1min context + entry check on the latest closed bar
        if now - last_check >= check_interval_sec:
            for tk, ev in evaluators.items():
                df_1m, confirm_series = build_1m_context(db, tk, config)
                if df_1m is None or not confirm_series:
                    continue
                ev.update_context(confirm_series=confirm_series)
                bar = df_1m.iloc[-1]
                bar_ts = bar['timestamp']
                if last_processed[tk] is not None and bar_ts <= last_processed[tk]:
                    continue  # no new closed bar
                last_processed[tk] = bar_ts
                dec = ev.check_entry(bar)
                if dec is None:
                    continue
                # Issue #35: mandatory imbalance filter (always active in live)
                imbalance_threshold = float(config.get('imbalance_threshold', IMBALANCE_THRESHOLD_DEFAULT))
                imbalance_val = get_recent_imbalance(db, tk)
                if imbalance_val is None or imbalance_val <= imbalance_threshold:
                    continue  # imbalance filter not satisfied
                if not _can_emit_signal(db, tk, config):

                    continue

                emit_signal(db, tk, dec, config, imbalance=imbalance_val)
                signals_emitted += 1
            last_check = now
        time.sleep(1)

    try:
        db.close_pool()
    except Exception:
        pass
    logger.info(f"Live engine finished: {signals_emitted} signals emitted")


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_live_engine(duration_minutes=duration)
