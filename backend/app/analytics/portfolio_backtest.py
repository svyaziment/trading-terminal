"""Portfolio backtest using the StrategyPlugin system.

Strategy-agnostic backtest that delegates entry/exit/manage to a plugin from
StrategyRegistry. For levels_reversal, the calculations mirror
StrategyEvaluator.on_bar exactly (entry_exec, slippage, round-trip commission)
to guarantee bit-for-bit regression parity with strategy_backtest.

Usage:
    from app.analytics.portfolio_backtest import run_portfolio_backtest
    result = run_portfolio_backtest(db, 'levels_reversal', config, tickers, ...)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.db.db_manager import DBManager
from app.analytics.strategies.base import EntrySignal, ExitSignal, Position, PositionAction
from app.analytics.strategies.context import MarketContext
from app.analytics.strategies.registry import get_registry
from app.analytics.levels_backtest import compute_atr
from app.analytics.strategy_backtest import compute_indicators_1min, _bootstrap_metrics

logger = logging.getLogger(__name__)


def run_portfolio_backtest(
    db: DBManager,
    strategy_name: str,
    config: Dict,
    tickers: List[str],
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    n_runs: int = 1,
) -> Dict:
    """Run backtest for multiple tickers using a strategy plugin from the registry."""
    registry = get_registry()
    plugin = registry.get_plugin(strategy_name, config)

    results = []
    for ticker in tickers:
        try:
            result = _backtest_ticker_plugin(
                db=db, ticker=ticker, plugin=plugin, config=config,
                date_from=date_from, date_to=date_to, n_runs=n_runs,
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Backtest failed for {ticker}: {e}")
            results.append({'ticker': ticker, 'status': 'failed', 'error': str(e)})

    total_trades = sum(r.get('n_trades', 0) for r in results if r.get('status') == 'success')
    successful = sum(1 for r in results if r.get('status') == 'success')

    return {
        'status': 'success',
        'strategy': strategy_name,
        'tickers': tickers,
        'total_trades': total_trades,
        'successful_tickers': successful,
        'results': results,
    }


def _backtest_ticker_plugin(
    db: DBManager,
    ticker: str,
    plugin,
    config: Dict,
    date_from: Optional[str],
    date_to: Optional[str],
    n_runs: int,
) -> Dict:
    """Backtest one ticker via the strategy plugin (mirrors strategy_backtest.run_strategy_backtest)."""
    from app.analytics.strategy_context import build_strategy_context

    patterns = config.get('patterns', ['levels_reversal'])
    use_rsi = 'rsi_oversold' in patterns
    use_macd = 'macd_bullish' in patterns
    use_bb = 'bb_lower' in patterns

    round_trip = float(config.get('commission_pct', 0.06)) / 100.0
    slip = float(config.get('slippage_pct', 0.0)) / 100.0

    # 1min candles (date-filtered) - SAME order as strategy_backtest
    q = "SELECT timestamp, open, high, low, close, volume FROM trading.candles_1min_raw WHERE ticker=%s"
    params = [ticker]
    if date_from is not None:
        q += " AND timestamp >= %s"; params.append(date_from)
    if date_to is not None:
        q += " AND timestamp < %s"; params.append(date_to)
    q += " ORDER BY timestamp"

    df_1m = db.select(q, tuple(params)).to_dataframe()
    if df_1m.empty:
        return {'ticker': ticker, 'status': 'failed', 'error': 'no 1min candles'}

    for c in ['open', 'high', 'low', 'close', 'volume']:
        df_1m[c] = pd.to_numeric(df_1m[c], errors='coerce')

    atr_period = int(config.get('atr_period', 14))
    df_1m['_atr'] = compute_atr(df_1m, period=atr_period)
    df_1m['_volume_sma_20'] = df_1m['volume'].rolling(20).mean()

    if use_rsi or use_macd or use_bb:
        df_1m = compute_indicators_1min(df_1m)

    # Build context passing df_1m (SAME as strategy_backtest for identical confirm_series)
    ctx = build_strategy_context(db, ticker, config, df_1m=df_1m)
    if ctx.get('status') == 'failed':
        return {'ticker': ticker, 'status': 'failed', 'error': ctx.get('error')}

    # Preload 4h context into the plugin once (mirrors ev.load_context in strategy_backtest)
    first_context = MarketContext(
        timestamp=df_1m.iloc[0]['timestamp'],
        candles_1min=df_1m.iloc[:1],
        atr_by_period={atr_period: float(df_1m.iloc[0]['_atr'])}
        if pd.notna(df_1m.iloc[0]['_atr']) else {},
        levels=ctx['levels'],
        ts_4h=ctx['ts_htf'],
        atr_by_ts=ctx['atr_by_ts'],
        buy_ts=ctx['buy_ts'],
        confirm_series=ctx['confirm_series'],
        signal_filter_series=ctx.get('signal_filter_series') or [],
        volume_current=float(df_1m.iloc[0]['volume'])
        if pd.notna(df_1m.iloc[0]['volume']) else None,
        volume_sma_20=float(df_1m.iloc[0]['_volume_sma_20'])
        if pd.notna(df_1m.iloc[0]['_volume_sma_20']) else None,
    )
    if hasattr(plugin, 'load_market_context'):
        plugin.load_market_context(first_context)

    trades = []
    position: Optional[Position] = None
    entry_idx: int = 0

    for i in range(len(df_1m)):
        row = df_1m.iloc[i]
        ts = pd.Timestamp(row['timestamp'])

        context = MarketContext(
            timestamp=ts,
            candles_1min=df_1m.iloc[:i + 1],
            atr_by_period={atr_period: float(row['_atr'])}
            if pd.notna(row['_atr']) else {},
            levels=ctx['levels'],
            ts_4h=ctx['ts_htf'],
            atr_by_ts=ctx['atr_by_ts'],
            buy_ts=ctx['buy_ts'],
            confirm_series=ctx['confirm_series'],
            signal_filter_series=ctx.get('signal_filter_series') or [],
            volume_current=float(row['volume']) if pd.notna(row['volume']) else None,
            volume_sma_20=float(row['_volume_sma_20'])
            if pd.notna(row['_volume_sma_20']) else None,
        )

        # --- exit branch (mirrors on_bar: exit checked first) ---
        if position is not None:
            exit_signal = plugin.check_exit(position, context)
            if exit_signal is not None:
                exit_price = exit_signal.exit_price
                entry_exec = position.metadata.get('entry_exec') if position.metadata else position.entry_price
                # Mirror of on_bar: gross uses exit*(1-slip)/entry_exec
                gross = (exit_price * (1 - slip) / entry_exec - 1.0) * 100.0
                net = gross - round_trip * 100.0
                trades.append({
                    'entry_ts': str(position.entry_ts),
                    'exit_ts': str(ts),
                    'entry_price': float(position.entry_price),
                    'exit_price': float(exit_price),
                    'exit_reason': exit_signal.reason,
                    'bars_held': i - entry_idx,
                    'net_return_pct': round(net, 5),
                })
                position = None
                continue
            position.bars_held += 1
            continue

        # --- entry branch (mirrors on_bar: entry only when no position) ---
        entry_signal = plugin.check_entry(context)
        if entry_signal is not None:
            price = entry_signal.entry_price
            entry_exec = price * (1 + slip)  # mirror of on_bar entry_exec
            position = Position(
                entry_price=price,
                entry_ts=ts,
                stop=entry_signal.stop,
                take=entry_signal.take,
                size=1.0,
                bars_held=0,
                metadata={'entry_exec': entry_exec},
            )
            entry_idx = i

    # Metrics via the SAME _bootstrap_metrics as strategy_backtest
    m = _bootstrap_metrics(trades, n_runs)
    return {
        'ticker': ticker,
        'status': 'success',
        'n_trades': len(trades),
        'bars_1min': len(df_1m),
        'trades': trades,
        'metrics': m,
    }
