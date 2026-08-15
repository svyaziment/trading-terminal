"""Regression test: strategy_backtest vs LevelsReversalStrategy plugin.

Compares old strategy_backtest.run_strategy_backtest with new
portfolio_backtest.run_portfolio_backtest (via LevelsReversalStrategy plugin).
Results must match bit-for-bit (same trades, same metrics).
"""
from __future__ import annotations

import sys
from pathlib import Path
# Ensure backend root is in sys.path for standalone execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import logging
# Reroute DBManager logs to stderr so stdout stays clean JSON
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

from app.db.db_manager import DBManager
from app.analytics.strategy_backtest import run_strategy_backtest, get_big_tickers
from app.analytics.portfolio_backtest import run_portfolio_backtest
from app.analytics.strategies.registry import register_default_strategies


def compare_trades(trades_old: list, trades_new: list) -> dict:
    if len(trades_old) != len(trades_new):
        return {'match': False,
                'reason': f'Trade count mismatch: {len(trades_old)} vs {len(trades_new)}',
                'first_diff': None}
    for i, (t_old, t_new) in enumerate(zip(trades_old, trades_new)):
        for key in ['entry_ts', 'exit_ts', 'entry_price', 'exit_price',
                    'exit_reason', 'bars_held', 'net_return_pct']:
            if t_old.get(key) != t_new.get(key):
                return {'match': False,
                        'reason': f'Trade {i} field {key} mismatch',
                        'first_diff': {'index': i, 'field': key,
                                       'old': t_old.get(key), 'new': t_new.get(key)}}
    return {'match': True, 'reason': 'All trades match', 'first_diff': None}


def run_regression_test(db, tickers, config, date_from=None, date_to=None) -> dict:
    results = []
    for ticker in tickers:
        print(f"Testing {ticker}...", file=sys.stderr)
        try:
            old_result = run_strategy_backtest(db, ticker, config,
                                               date_from=date_from, date_to=date_to)
        except Exception as e:
            results.append({'ticker': ticker, 'status': 'error', 'error': f'Old: {e}'})
            continue

        try:
            new_result = run_portfolio_backtest(db=db, strategy_name='levels_reversal',
                                                config=config, tickers=[ticker],
                                                date_from=date_from, date_to=date_to, n_runs=1)
        except Exception as e:
            results.append({'ticker': ticker, 'status': 'error', 'error': f'New: {e}'})
            continue

        if old_result.get('status') != 'success':
            results.append({'ticker': ticker, 'status': 'error',
                            'error': f'Old status: {old_result.get("status")}'})
            continue
        if new_result.get('status') != 'success' or not new_result.get('results'):
            results.append({'ticker': ticker, 'status': 'error',
                            'error': f'New status: {new_result.get("status")}'})
            continue

        new_ticker_result = new_result['results'][0]
        if new_ticker_result.get('status') != 'success':
            results.append({'ticker': ticker, 'status': 'error',
                            'error': f'New ticker status: {new_ticker_result.get("error")}'})
            continue

        old_trades = old_result.get('trades', [])
        new_trades = new_ticker_result.get('trades', [])
        verdict = compare_trades(old_trades, new_trades)

        # Also compare metrics
        metrics_match = old_result.get('metrics') == new_ticker_result.get('metrics')

        results.append({
            'ticker': ticker,
            'status': 'success',
            'n_trades_old': len(old_trades),
            'n_trades_new': len(new_trades),
            'match': verdict['match'] and metrics_match,
            'reason': verdict['reason'] + ('; metrics match' if metrics_match else '; METRICS MISMATCH'),
            'first_diff': verdict['first_diff'],
        })

    all_match = all(r.get('match', False) for r in results if r.get('status') == 'success')
    successful = sum(1 for r in results if r.get('status') == 'success')
    return {
        'regression_match': all_match,
        'successful_tickers': successful,
        'total_tickers': len(tickers),
        'results': results,
    }


if __name__ == '__main__':
    register_default_strategies()
    db = DBManager()

    config = {
        'patterns': ['levels_reversal'],
        'confirm_windows': [10],
        'commission_pct': 0.06,
        'slippage_pct': 0.0,
        'risk_reward': {'risk': 1.0, 'reward': 2.0},
        'n_runs': 1,
    }

    # Dev test: 1 month, top-5
    print("=== Dev test: 1 month, top-5 ===", file=sys.stderr)
    dev_tickers = ['SBER', 'LKOH', 'PIKK', 'GAZP', 'VTBR']
    dev_result = run_regression_test(db, dev_tickers, config,
                                     date_from='2026-07-01', date_to='2026-08-01')

    full_result = None
    if dev_result['regression_match']:
        # Full test: 2 years, 28 tickers
        print("=== Full test: 2 years, 28 tickers ===", file=sys.stderr)
        full_tickers = get_big_tickers(db, min_candles=250000)[:28]
        full_result = run_regression_test(db, full_tickers, config,
                                          date_from='2024-08-01', date_to='2026-08-01')

    db.close_pool()

    # Single valid JSON document on stdout
    final = {
        'regression_match': dev_result['regression_match'] and (
            full_result['regression_match'] if full_result else False),
        'dev_test': dev_result,
        'full_test': full_result,
    }
    print(json.dumps(final, default=str, indent=2))

    if not final['regression_match']:
        print("REGRESSION TEST FAILED", file=sys.stderr)
        sys.exit(1)
    print("REGRESSION TEST PASSED", file=sys.stderr)
