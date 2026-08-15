"""Portfolio backtest simulator - strategy-agnostic via StrategyPlugin."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from app.db.db_manager import DBManager
from app.analytics.strategies.base import EntrySignal, ExitSignal, Position, PositionAction
from app.analytics.strategies.context import MarketContext
from app.analytics.strategies.registry import get_registry

logger = logging.getLogger(__name__)


def run_portfolio_backtest(
    db: DBManager,
    strategy_name: str,
    config: Dict,
    tickers: List[str],
    capital: float = 50000.0,
    slot_size: float = 10000.0,
    commission_pct: float = 0.06,
    slippage_pct: float = 0.0,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict:
    """Run portfolio backtest for multiple tickers using strategy plugin."""
    registry = get_registry()
    plugin = registry.get_plugin(strategy_name, config)

    round_trip = commission_pct / 100.0
    slip = slippage_pct / 100.0

    results = []

    for ticker in tickers:
        try:
            result = _backtest_ticker(
                db=db, ticker=ticker, plugin=plugin, config=config,
                capital=capital, slot_size=slot_size,
                round_trip=round_trip, slip=slip,
                date_from=date_from, date_to=date_to,
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
        'capital': capital,
        'slot_size': slot_size,
        'total_trades': total_trades,
        'successful_tickers': successful,
        'results': results,
    }


def _backtest_ticker(db, ticker, plugin, config, capital, slot_size,
                     round_trip, slip, date_from, date_to) -> Dict:
    """Backtest single ticker using strategy plugin."""
    from app.analytics.strategy_context import build_strategy_context

    ctx = build_strategy_context(db, ticker, config)
    if ctx.get('status') == 'failed':
        return {'ticker': ticker, 'status': 'failed', 'error': ctx.get('error')}

    q = "SELECT timestamp, open, high, low, close FROM trading.candles_1min_raw WHERE ticker=%s"
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

    for c in ['open', 'high', 'low', 'close']:
        df_1m[c] = pd.to_numeric(df_1m[c], errors='coerce')

    trades = []
    position: Optional[Position] = None

    for idx, row in df_1m.iterrows():
        ts = pd.Timestamp(row['timestamp'])

        context = MarketContext(
            timestamp=ts,
            candles_1min=df_1m.iloc[:idx + 1],
            levels=ctx['levels'],
            ts_4h=ctx['ts_htf'],
            atr_by_ts=ctx['atr_by_ts'],
            buy_ts=ctx['buy_ts'],
            confirm_series=ctx['confirm_series'],
        )

        if position is not None:
            exit_signal = plugin.check_exit(position, context)
            if exit_signal is not None:
                exit_price = exit_signal.exit_price
                gross_pct = (exit_price * (1 - slip) / position.entry_price - 1.0) * 100.0
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

        entry_signal = plugin.check_entry(context)
        if entry_signal is not None:
            entry_price = entry_signal.entry_price * (1 + slip)
            position = Position(
                entry_price=entry_price,
                entry_ts=ts,
                stop=entry_signal.stop,
                take=entry_signal.take,
                size=slot_size / entry_price,
                bars_held=0,
            )

    if not trades:
        return {
            'ticker': ticker, 'status': 'success', 'n_trades': 0,
            'metrics': {'n': 0, 'pf': None, 'exp_pct': None, 'wr': None},
        }

    nets = [t['net_return_pct'] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gw / gl) if gl > 0 else (float('inf') if gw > 0 else None)

    metrics = {
        'n': len(trades),
        'pf': round(pf, 2) if pf is not None and pf != float('inf') else pf,
        'exp_pct': round(sum(nets) / len(nets), 3) if nets else None,
        'wr': round(len(wins) / len(nets) * 100, 1) if nets else None,
    }

    return {
        'ticker': ticker, 'status': 'success',
        'n_trades': len(trades), 'trades': trades, 'metrics': metrics,
    }
