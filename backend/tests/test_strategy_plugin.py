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


# --- Issue #116 unit tests (no DB) ---

LOCKED_LIKE_CONFIG = {
    'patterns': ['levels_reversal', 'signal_4h_buy'],
    'confirm_windows': [10],
    'commission_pct': 0.06,
    'slippage_pct': 0.0,
    'risk_reward': {'risk': 1.0, 'reward': 2.0},
    'entry_window': (7, 19),
}


def _locked_support_levels():
    import pandas as pd
    support = {
        'available_from_ts': pd.Timestamp('2026-07-27 08:00:00'),
        'defined_ts': pd.Timestamp('2026-07-27 08:00:00'),
        'level_price': 19.61,
        'type': 'support',
        'method': 'impulse',
        'atr': 0.3164285714285714,
        'zone_lower': 19.451785714285712,
        'zone_upper': 19.768214285714286,
    }
    take = {
        'available_from_ts': pd.Timestamp('2026-07-08 16:00:00'),
        'defined_ts': pd.Timestamp('2026-07-06 08:00:00'),
        'level_price': 20.90,
        'type': 'resistance',
        'method': 'swing',
        'atr': 0.2857142857142855,
        'zone_lower': 20.757142857142856,
        'zone_upper': 21.04285714285714,
    }
    return pd.DataFrame([support, take])


def _locked_plugin_signal(htf_bars):
    import pandas as pd
    from app.analytics.strategies.context import MarketContext
    from app.analytics.strategies.levels_reversal import LevelsReversalStrategy

    ts_4h = pd.Timestamp('2026-08-20 08:00:00')
    atr = 0.5714285714285714
    row = pd.Series({
        'timestamp': pd.Timestamp('2026-08-20 11:50:24'),
        'open': 19.80, 'high': 19.80, 'low': 19.80, 'close': 19.80,
    })
    plugin = LevelsReversalStrategy(LOCKED_LIKE_CONFIG)
    market = MarketContext(
        timestamp=row['timestamp'],
        candles_1min=pd.DataFrame([row]),
        levels=_locked_support_levels(),
        ts_4h=[ts_4h],
        atr_by_ts={ts_4h: atr},
        buy_ts=[ts_4h],
        confirm_series=[([pd.Timestamp('2026-08-20 11:40:00')], [19.85])],
        htf_bars=htf_bars,
    )
    plugin.load_market_context(market)
    return plugin, plugin.check_entry(market)


def test_locked_like_plugin_bit_for_bit_with_htf_bars():
    """Locked paper chips (no breakout) stay bit-for-bit when htf_bars is wired."""
    import pandas as pd

    dummy_htf = pd.DataFrame([{
        'timestamp': pd.Timestamp('2026-08-20 08:00:00'),
        'open': 19.7, 'high': 19.9, 'low': 19.5, 'close': 19.8, 'atr': 0.5,
    }])
    plugin_off, without = _locked_plugin_signal(None)
    plugin_on, with_htf = _locked_plugin_signal(dummy_htf)

    assert plugin_off._evaluator.use_breakout_retest is False
    assert plugin_off._evaluator._tracker is None
    assert plugin_on._evaluator._tracker is None
    assert without is not None and with_htf is not None
    assert without.entry_price == with_htf.entry_price == 19.80
    assert without.stop == with_htf.stop == 19.61
    assert without.take == with_htf.take == 20.90


def test_portfolio_plugin_forwards_htf_bars(monkeypatch):
    """_backtest_ticker_plugin must put ctx['htf_bars'] on MarketContext."""
    import pandas as pd
    from app.analytics.portfolio_backtest import _backtest_ticker_plugin

    htf = pd.DataFrame({
        'timestamp': [pd.Timestamp('2026-01-01 00:00:00')],
        'open': [100.0], 'high': [101.0], 'low': [99.0], 'close': [100.5], 'atr': [1.0],
    })
    captured = []

    class _Probe:
        def load_market_context(self, context):
            captured.append(context)

        def check_entry(self, context):
            captured.append(context)
            return None

        def get_name(self):
            return 'probe'

    class _Result:
        def __init__(self, frame):
            self._frame = frame

        def to_dataframe(self):
            return self._frame.copy()

    class _DB:
        def select(self, _query, _params):
            return _Result(pd.DataFrame({
                'timestamp': pd.date_range('2026-01-01', periods=5, freq='min'),
                'open': [100.0] * 5,
                'high': [100.2] * 5,
                'low': [99.8] * 5,
                'close': [100.0] * 5,
                'volume': [10, 11, 12, 13, 14],
            }))

    monkeypatch.setattr(
        'app.analytics.strategy_context.build_strategy_context',
        lambda *_args, **_kwargs: {
            'status': 'success',
            'levels': [],
            'ts_htf': [],
            'atr_by_ts': {},
            'buy_ts': [],
            'confirm_series': [],
            'htf_bars': htf,
        },
    )
    result = _backtest_ticker_plugin(
        db=_DB(),
        ticker='TEST',
        plugin=_Probe(),
        config={'atr_period': 14},
        date_from=None,
        date_to=None,
        n_runs=1,
    )
    assert result['status'] == 'success'
    assert captured
    assert captured[0].htf_bars is htf
    assert all(ctx.htf_bars is htf for ctx in captured)


def test_json_dumps_sanitizes_infinity_for_jsonb():
    """One winning trade → pf=Infinity must not be written into JSONB."""
    from app.api.strategy_jobs import _json_dumps

    encoded = _json_dumps({'n': 1, 'pf': float('inf'), 'exp_pct': float('nan')})
    parsed = json.loads(encoded)
    assert parsed['n'] == 1
    assert parsed['pf'] is None
    assert parsed['exp_pct'] is None
    assert 'Infinity' not in encoded
    assert 'NaN' not in encoded

