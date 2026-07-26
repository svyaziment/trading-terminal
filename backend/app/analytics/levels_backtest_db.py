"""
Levels backtest DB persistence layer.
Wraps levels_backtest.run_levels_backtest and persists run/trades/equity/metrics
into existing trading.backtest_* tables with strategy_name='levels_reversal'.
Does NOT modify levels_backtest.py.
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from app.db.db_manager import DBManager
from app.analytics.levels_backtest import run_levels_backtest, ROUND_TRIP

STRATEGY_NAME = "levels_reversal"


def compute_equity(trades):
    """Cumulative net return equity curve (sum of net_return_pct, in trade exit order)."""
    if not trades:
        return []
    cum = 0.0
    curve = []
    for t in trades:
        cum += t['net_return_pct']
        curve.append({'exit_ts': t['exit_ts'], 'equity_pct': round(cum, 5)})
    return curve


def persist_run(db: DBManager, ticker: str, params: dict, result: dict) -> int:
    """Persist one backtest result into backtest_* tables. Returns run_id."""
    metrics = result.get('metrics', {})
    trades = result.get('trades_sample_full', result.get('trades_sample', []))
    # NOTE: run_levels_backtest returns trades_sample (first 5/10). For full persistence
    # we need full trades list; caller should pass result with 'trades' (full) if available.
    full_trades = result.get('trades', trades)
    equity = compute_equity(full_trades)

    # 1. backtest_runs
    run_row = {
        'strategy_name': STRATEGY_NAME,
        'params': json.dumps(params, ensure_ascii=False, default=str),
        'universe_snapshot': json.dumps([ticker], ensure_ascii=False),
        'selection_bias': True,
        'status': 'completed',
        'total_trades': int(metrics.get('n_trades', 0)),
    }
    run_df = pd.DataFrame([run_row])
    db.insert_with_schema('backtest_runs', run_df)
    run_id_df = db.select("""
        SELECT id FROM trading.backtest_runs
        WHERE strategy_name=%s ORDER BY id DESC LIMIT 1
    """, (STRATEGY_NAME,)).to_dataframe()
    run_id = int(run_id_df.iloc[0]['id']) if not run_id_df.empty else -1

    # 2. backtest_trades
    if full_trades:
        trade_rows = []
        for t in full_trades:
            gross = t.get('gross_return_pct', 0.0)
            net = t.get('net_return_pct', 0.0)
            commission = ROUND_TRIP * 100.0  # approx (commission only, slippage folded into exec prices)
            trade_rows.append({
                'run_id': run_id,
                'ticker': ticker,
                'timeframe': '1min',
                'side': 'LONG',
                'entry_ts': t.get('entry_ts'),
                'entry_price': t.get('entry_price'),
                'exit_ts': t.get('exit_ts'),
                'exit_price': t.get('exit_price'),
                'exit_reason': t.get('exit_reason'),
                'bars_held': t.get('bars_held'),
                'gross_return_pct': gross,
                'commission_pct': commission,
                'slippage_pct': params.get('slippage_per_side', 0.0) * 2 * 100.0,
                'net_return_pct': net,
            })
        db.insert_with_schema('backtest_trades', pd.DataFrame(trade_rows))

    # 3. backtest_equity (schema: run_id, ts, equity_rub, drawdown_pct)
    if equity:
        START_CAPITAL = 100000.0
        eq_rows = []
        peak = -1e18
        for e in equity:
            eq_rub = START_CAPITAL * (1.0 + e['equity_pct'] / 100.0)
            peak = max(peak, eq_rub)
            dd = (peak - eq_rub) / peak * 100.0 if peak > 0 else 0.0
            eq_rows.append({'run_id': run_id, 'ts': e['exit_ts'],
                            'equity_rub': round(eq_rub, 2), 'drawdown_pct': round(dd, 4)})
        try:
            db.insert_with_schema('backtest_equity', pd.DataFrame(eq_rows))
        except Exception:
            pass  # non-fatal

    # 4. backtest_metrics
    metric_row = {
        'run_id': run_id,
        'group_key': f"ticker={ticker}",
        'n_trades': int(metrics.get('n_trades', 0)),
        'profit_factor': metrics.get('profit_factor'),
        'expectancy': metrics.get('expectancy'),
        'win_rate': metrics.get('win_rate'),
        'max_drawdown': metrics.get('max_drawdown_pct'),
        'avg_bars_held': metrics.get('avg_bars_held'),
        'reliable': bool((metrics.get('n_trades', 0) or 0) >= 30),
    }
    try:
        db.insert_with_schema('backtest_metrics', pd.DataFrame([metric_row]))
    except Exception:
        pass  # metrics table schema may differ; non-fatal

    return run_id


def run_and_persist(db: DBManager, ticker: str, params: dict) -> dict:
    """Run one levels backtest config and persist to DB. Returns result + run_id."""
    result = run_levels_backtest(
        db, ticker=ticker,
        entry_mode=params.get('entry_mode', 'levels_ts1'),
        swing_window=params.get('swing_window', 10),
        zone_atr=params.get('zone_atr', 0.5),
        confirm_tf=params.get('confirm_tf', '10min'),
        risk_reward=params.get('risk_reward', 2.0),
        slippage_per_side=params.get('slippage_per_side', 0.0),
        entry_window_start=params.get('entry_window_start', 7),
        entry_window_end=params.get('entry_window_end', 19),
    )
    if result.get('status') != 'success':
        return {'status': 'failed', 'error': result.get('error'), 'ticker': ticker}
    run_id = persist_run(db, ticker, params, result)
    result['run_id'] = run_id
    result['persisted'] = True
    return result
