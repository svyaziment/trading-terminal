"""ATR Reversal strategy backtest - DEV test for Issue #42.

Runs backtest on 1min candles using AtrReversalStrategy plugin.
Reports: equity, PnL, win rate, PF, max drawdown.

Usage:
    from app.analytics.atr_backtest import run_atr_backtest
    
    result = run_atr_backtest(
        db=db,
        tickers=['SBER', 'LKOH', 'PIKK', 'GAZP', 'VTBR'],
        date_from='2026-07-01',
        date_to='2026-08-01',
    )
"""

from __future__ import annotations

import sys
from pathlib import Path
# Ensure backend root (/app) is in sys.path for standalone execution
# File lives at /app/app/analytics/atr_backtest.py -> parents[2] = /app
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import logging
# Reroute DBManager logs to stderr so stdout stays clean JSON
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.db.db_manager import DBManager
from app.analytics.strategies.base import Position
from app.analytics.strategies.context import MarketContext
from app.analytics.strategies.registry import get_registry
from app.analytics.levels_backtest import compute_atr

logger = logging.getLogger(__name__)


def run_atr_backtest(
    db: DBManager,
    tickers: List[str],
    config: Optional[Dict] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commission_pct: float = 0.06,
) -> Dict:
    """Run ATR reversal backtest for multiple tickers."""
    if config is None:
        config = {
            'atr_period': 14,
            'atr_completion_min': 0.80,
            'atr_completion_max': 0.90,
            'volume_spike_mult': 2.0,
            'stop_atr_mult': 1.0,
            'take_atr_mult': 0.85,
            'level_proximity_atr': 0.5,
        }

    from app.analytics.strategies.registry import register_default_strategies
    register_default_strategies()
    registry = get_registry()
    plugin = registry.get_plugin('atr_reversal', config)

    round_trip = commission_pct / 100.0
    results = []

    for ticker in tickers:
        try:
            result = _backtest_ticker(
                db=db,
                ticker=ticker,
                plugin=plugin,
                config=config,
                date_from=date_from,
                date_to=date_to,
                round_trip=round_trip,
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Backtest failed for {ticker}: {e}")
            results.append({
                'ticker': ticker,
                'status': 'failed',
                'error': str(e),
            })

    # Aggregate metrics
    total_trades = sum(r.get('n_trades', 0) for r in results if r.get('status') == 'success')
    successful = sum(1 for r in results if r.get('status') == 'success')
    all_nets = []
    for r in results:
        if r.get('status') == 'success' and 'trades' in r:
            all_nets.extend([t['net_return_pct'] for t in r['trades']])

    if all_nets:
        wins = [n for n in all_nets if n > 0]
        losses = [n for n in all_nets if n <= 0]
        gw = sum(wins) if wins else 0.0
        gl = abs(sum(losses)) if losses else 0.0
        pf = (gw / gl) if gl > 0 else (float('inf') if gw > 0 else None)
        cum = np.cumsum(all_nets)
        maxdd = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0
        
        aggregate_metrics = {
            'total_trades': len(all_nets),
            'profit_factor': round(pf, 2) if pf is not None and pf != float('inf') else pf,
            'win_rate': round(len(wins) / len(all_nets) * 100, 1),
            'expectancy': round(float(np.mean(all_nets)), 3),
            'max_drawdown_pct': round(maxdd, 2),
            'total_net_pct': round(sum(all_nets), 2),
        }
    else:
        aggregate_metrics = None

    return {
        'status': 'success',
        'strategy': 'atr_reversal',
        'tickers': tickers,
        'total_trades': total_trades,
        'successful_tickers': successful,
        'aggregate_metrics': aggregate_metrics,
        'results': results,
    }


def _backtest_ticker(
    db: DBManager,
    ticker: str,
    plugin,
    config: Dict,
    date_from: Optional[str],
    date_to: Optional[str],
    round_trip: float,
) -> Dict:
    """Backtest single ticker with ATR reversal strategy."""
    from app.analytics.strategy_context import build_strategy_context

    # Build strategy context (levels, ATR, BUY signals)
    ctx = build_strategy_context(db, ticker, config)
    if ctx.get('status') == 'failed':
        return {'ticker': ticker, 'status': 'failed', 'error': ctx.get('error')}

    # Load 1min candles
    q = "SELECT timestamp, open, high, low, close, volume FROM trading.candles_1min_raw WHERE ticker=%s"
    params = [ticker]
    if date_from:
        q += " AND timestamp >= %s"
        params.append(date_from)
    if date_to:
        q += " AND timestamp < %s"
        params.append(date_to)
    q += " ORDER BY timestamp"

    df_1m = db.select(q, tuple(params)).to_dataframe()
    if df_1m.empty:
        return {'ticker': ticker, 'status': 'failed', 'error': 'no 1min candles'}

    for c in ['open', 'high', 'low', 'close', 'volume']:
        df_1m[c] = pd.to_numeric(df_1m[c], errors='coerce')

    # Compute volume SMA-20 for volume spike detection
    df_1m['volume_sma_20'] = df_1m['volume'].rolling(20).mean()

    # Run backtest bar-by-bar
    trades = []
    position: Optional[Position] = None

    for idx in range(len(df_1m)):
        row = df_1m.iloc[idx]
        ts = pd.Timestamp(row['timestamp'])

        # Build market context
        context = MarketContext(
            timestamp=ts,
            candles_1min=df_1m.iloc[:idx + 1],
            levels=ctx['levels'],
            atr_by_ts=ctx['atr_by_ts'],
            buy_ts=ctx['buy_ts'],
            confirm_series=ctx['confirm_series'],
            volume_current=float(row['volume']) if pd.notna(row['volume']) else None,
            volume_sma_20=float(row['volume_sma_20']) if pd.notna(row['volume_sma_20']) else None,
        )

        # Check exit if position open
        if position is not None:
            exit_signal = plugin.check_exit(position, context)
            if exit_signal is not None:
                exit_price = exit_signal.exit_price
                gross_pct = (exit_price / position.entry_price - 1.0) * 100.0
                net_pct = gross_pct - round_trip * 100.0

                trades.append({
                    'entry_ts': str(position.entry_ts),
                    'exit_ts': str(ts),
                    'entry_price': position.entry_price,
                    'exit_price': exit_price,
                    'exit_reason': exit_signal.reason,
                    'bars_held': position.bars_held,
                    'net_return_pct': round(net_pct, 5),
                })

                position = None
                continue
            else:
                position.bars_held += 1
                continue

        # Check entry if no position
        entry_signal = plugin.check_entry(context)
        if entry_signal is not None:
            position = Position(
                entry_price=entry_signal.entry_price,
                entry_ts=ts,
                stop=entry_signal.stop,
                take=entry_signal.take,
                size=1.0,
                bars_held=0,
                metadata=entry_signal.metadata,
            )

    # Compute metrics
    if not trades:
        return {
            'ticker': ticker,
            'status': 'success',
            'n_trades': 0,
            'metrics': None,
        }

    nets = [t['net_return_pct'] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]

    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gw / gl) if gl > 0 else (float('inf') if gw > 0 else None)
    cum = np.cumsum(nets)
    maxdd = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0

    metrics = {
        'n_trades': len(trades),
        'profit_factor': round(pf, 2) if pf is not None and pf != float('inf') else pf,
        'win_rate': round(len(wins) / len(nets) * 100, 1),
        'expectancy': round(float(np.mean(nets)), 3),
        'max_drawdown_pct': round(maxdd, 2),
        'total_net_pct': round(sum(nets), 2),
    }

    return {
        'ticker': ticker,
        'status': 'success',
        'n_trades': len(trades),
        'trades': trades,
        'metrics': metrics,
    }


if __name__ == '__main__':
    import sys
    import json

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    db = DBManager()
    
    # DEV test: 1 month, top-5 tickers
    dev_tickers = ['SBER', 'LKOH', 'PIKK', 'GAZP', 'VTBR']
    
    result = run_atr_backtest(
        db=db,
        tickers=dev_tickers,
        date_from='2026-07-01',
        date_to='2026-08-01',
        commission_pct=0.06,
    )

    print(json.dumps(result, default=str, indent=2))
    db.close_pool()
