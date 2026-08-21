"""
Parameterizable strategy backtest engine (config-driven).

Patterns (AND logic): all selected patterns must fire on the same 1min bar for entry.
  - levels_reversal : price in 4h support zone + reversal confirmation on ALL selected
                      confirm_windows (closed higher-TF candle closes above zone).
  - rsi_oversold    : RSI-14 < 30 (computed on 1min close).
  - macd_bullish    : MACD histogram > 0 (computed on 1min close).
  - bb_lower        : close below Bollinger(20,2) lower band (computed on 1min close).
  - SignalEngine ids: AND-filters on last closed HTF bar (inline evaluate, Issue #79).
                      ``timeframe`` select: 30min, 1h, 2h, 4h, 1d, 1w. Not a
                      trading.signals lookup; not a replacement for rsi_oversold.

Config keys:
  patterns        : list[str]   (AND). If 'levels_reversal' absent, stop/take undefined -> no trades.
  confirm_windows : list[int]   minutes for levels confirmation (AND across windows).
  commission_pct  : float       round-trip commission, e.g. 0.06.
  slippage_pct    : float       per-side slippage, e.g. 0.06.
  risk_reward     : dict|None   {'risk':1.0,'reward':2.0} -> entry only if reward/risk >= ratio.
  entry_window    : (int,int)   hours, default (7,19) MSK.
  n_runs          : int         bootstrap iterations over trades (1 = deterministic).

Depth presets: express (6mo, SBER/LKOH/PIKK, full-sample), serious (6mo, 15 tickers, +WF),
               very_serious (24mo, all >250k tickers, +WF).
Metrics: n, pf, exp_pct, wr, maxdd_pct.
"""
from __future__ import annotations
import bisect
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from app.db.db_manager import DBManager
from app.analytics.levels_engine import build_levels, nearest_level_at
from app.analytics.levels_backtest import compute_atr, aggregate_1min_to, build_confirm_series
from app.analytics.pattern_registry import SIGNAL_ENGINE_PATTERN_IDS
from app.analytics.strategy_engine import StrategyEvaluator

EXPRESS_TICKERS = ['SBER', 'LKOH', 'PIKK']

DEPTH_PRESETS = {
    'express':      {'months': 6,  'tickers': EXPRESS_TICKERS, 'n_runs': 10,  'walkforward': False},
    'serious':      {'months': 6,  'n_tickers': 15,            'n_runs': 40,  'walkforward': True},
    'very_serious': {'months': 24, 'n_tickers': None,          'n_runs': 100, 'walkforward': True},
}

LAB_PATTERNS = ['levels_reversal', 'signal_4h_buy', 'rsi_oversold', 'macd_bullish', 'bb_lower',
               'level_breakout_retest']
ALL_PATTERNS = LAB_PATTERNS + list(SIGNAL_ENGINE_PATTERN_IDS)
CONFIRM_WINDOWS = [1, 5, 10, 15, 20, 25, 30, 60, 90, 120]


def get_big_tickers(db, min_candles: int = 250000) -> List[str]:
    """Tickers with >= min_candles 1min rows (the selectable universe)."""
    df = db.select(
        "SELECT ticker FROM trading.candles_1min_raw GROUP BY ticker HAVING count(*) >= %s ORDER BY ticker",
        (min_candles,)).to_dataframe()
    return df['ticker'].tolist() if not df.empty else []


def compute_indicators_1min(df_1m: pd.DataFrame) -> pd.DataFrame:
    """RSI-14, MACD histogram, BB(20,2) lower on 1min close (for indicator patterns)."""
    df = df_1m.copy()
    close = df['close']
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    df['macd_hist'] = macd - macd.ewm(span=9, adjust=False).mean()
    sma20 = close.rolling(20).mean()
    df['bb_lower'] = sma20 - 2 * close.rolling(20).std()
    return df


def _metrics(trades: List[Dict]) -> Dict:
    if not trades:
        return {'n': 0, 'pf': None, 'exp_pct': None, 'wr': None, 'maxdd_pct': None}
    nets = [t['net_return_pct'] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gw / gl) if gl > 0 else (float('inf') if gw > 0 else None)
    cum = np.cumsum(nets)
    maxdd = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0
    return {'n': len(trades),
            'pf': round(pf, 2) if pf is not None and pf != float('inf') else pf,
            'exp_pct': round(float(np.mean(nets)), 3),
            'wr': round(len(wins) / len(nets) * 100, 1),
            'maxdd_pct': round(maxdd, 1)}


def _bootstrap_metrics(trades: List[Dict], n_runs: int, seed: int = 42) -> Dict:
    """Average metrics over n_runs bootstrap resamples of trades (stability estimate)."""
    if n_runs <= 1 or not trades:
        return _metrics(trades)
    rng = np.random.default_rng(seed)
    n = len(trades)
    pfs, exps, wrs, dds = [], [], [], []
    for _ in range(n_runs):
        idx = rng.integers(0, n, size=n)
        m = _metrics([trades[i] for i in idx])
        if m['pf'] is not None:
            pfs.append(m['pf']); exps.append(m['exp_pct']); wrs.append(m['wr']); dds.append(m['maxdd_pct'])
    if not pfs:
        return _metrics(trades)
    return {'n': n, 'pf': round(float(np.mean(pfs)), 2), 'exp_pct': round(float(np.mean(exps)), 3),
            'wr': round(float(np.mean(wrs)), 1), 'maxdd_pct': round(float(np.mean(dds)), 1),
            'pf_min': round(float(np.min(pfs)), 2), 'pf_p25': round(float(np.percentile(pfs, 25)), 2)}


def _load_4h_buy_ts(db, ticker: str, min_signals: int = 1):
    """Sorted timestamps of 4h BUY signals (total_signals >= min_signals) from trading.signals."""
    df = db.select(
        "SELECT timestamp FROM trading.signals "
        "WHERE ticker=%s AND timeframe='4h' AND signal='BUY' AND coalesce(total_signals,0) >= %s "
        "ORDER BY timestamp", (ticker, min_signals)).to_dataframe()
    if df.empty:
        return []
    return sorted(pd.to_datetime(df['timestamp']).tolist())




def run_strategy_backtest(db, ticker: str, config: Dict, date_from=None, date_to=None) -> Dict:
    """Run backtest for one ticker under config. Returns metrics + trades.

    Entry/exit logic is delegated to the unified StrategyEvaluator (single brain
    shared with paper/live trading). This function only prepares the data context
    (4h levels, 1min candles, indicators, multi-window confirmation) and loops the
    evaluator over historical bars."""
    patterns = config.get('patterns', ['levels_reversal'])
    confirm_windows = config.get('confirm_windows', [10])
    n_runs = int(config.get('n_runs', 1))

    use_levels = 'levels_reversal' in patterns
    use_rsi = 'rsi_oversold' in patterns
    use_macd = 'macd_bullish' in patterns
    use_bb = 'bb_lower' in patterns
    use_4h_buy = 'signal_4h_buy' in patterns

    if not use_levels:
        return {'status': 'failed', 'ticker': ticker,
                'error': 'levels_reversal required (defines stop/take); indicator patterns are AND-filters'}

    # 1min candles (date-filtered)
    q = "SELECT timestamp, open, high, low, close FROM trading.candles_1min_raw WHERE ticker=%s "
    params = [ticker]
    if date_from is not None:
        q += " AND timestamp >= %s "; params.append(date_from)
    if date_to is not None:
        q += " AND timestamp < %s "; params.append(date_to)
    q += " ORDER BY timestamp "
    df_1m = db.select(q, tuple(params)).to_dataframe()
    if df_1m.empty:
        return {'status': 'failed', 'ticker': ticker, 'error': 'no 1min candles'}
    for c in ['open', 'high', 'low', 'close']:
        df_1m[c] = pd.to_numeric(df_1m[c], errors='coerce')
    
    if use_rsi or use_macd or use_bb:
        df_1m = compute_indicators_1min(df_1m)
    
    # Context from pattern parameters (single source of truth)
    from app.analytics.strategy_context import build_strategy_context
    ctx = build_strategy_context(db, ticker, config, df_1m=df_1m)
    if ctx.get('status') == 'failed':
        return {'status': 'failed', 'ticker': ticker, 'error': ctx.get('error')}
    
    # Unified engine (single brain shared with paper/live trading)
    ev = StrategyEvaluator(ctx['config'])
    ev.load_context(levels=ctx['levels'], ts_4h=ctx['ts_htf'], atr_by_ts=ctx['atr_by_ts'],
                    buy_ts=ctx['buy_ts'], confirm_series=ctx['confirm_series'],
                    signal_filter_series=ctx.get('signal_filter_series'),
                    htf_bars=ctx.get('htf_bars'))

    trades = []
    for i in range(len(df_1m)):
        decision = ev.on_bar(df_1m.iloc[i], idx=i)
        if decision['action'] == 'exit':
            trades.append(decision['trade'])

    m = _bootstrap_metrics(trades, n_runs)
    return {'status': 'success', 'ticker': ticker, 'config': config,
            'bars_1min': len(df_1m), 'metrics': m, 'trades': trades}

WALKFORWARD_PERIODS = [
    ('2024-H2', '2024-07-01', '2025-01-01'),
    ('2025-H1', '2025-01-01', '2025-07-01'),
    ('2025-H2', '2025-07-01', '2026-01-01'),
    ('2026-H1', '2026-01-01', '2026-07-01'),
]


def run_walkforward(db, ticker: str, config: Dict, periods=None) -> Dict:
    """Walk-forward: backtest per half-year window. Returns PF per period + summary
    (pf_gt1 count, min_pf, avg_pf) - matches the reference walk-forward report."""
    if periods is None:
        periods = WALKFORWARD_PERIODS
    results = {}
    pfs = []
    for name, date_from, date_to in periods:
        try:
            r = run_strategy_backtest(db, ticker, config, date_from=date_from, date_to=date_to)
        except Exception as e:
            results[name] = {'n': 0, 'pf': None, 'wr': None, 'error': str(e)}
            continue
        if r.get('status') == 'success':
            m = r['metrics']
            results[name] = {'n': m['n'], 'pf': m['pf'], 'wr': m['wr'], 'exp_pct': m['exp_pct']}
            if m['pf'] is not None:
                pfs.append(m['pf'])
        else:
            results[name] = {'n': 0, 'pf': None, 'wr': None, 'error': r.get('error')}
    pf_gt1 = sum(1 for pf in pfs if pf > 1)
    return {
        'ticker': ticker,
        'periods': results,
        'pf_gt1': f'{pf_gt1}/{len(pfs)}' if pfs else '0/0',
        'min_pf': round(min(pfs), 2) if pfs else None,
        'avg_pf': round(sum(pfs) / len(pfs), 2) if pfs else None,
    }


def run_depth_backtest(db, config: Dict, depth: str = 'express', tickers: Optional[List[str]] = None) -> Dict:
    """Run backtest across a depth preset's universe and window. Returns per-ticker metrics."""
    preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS['express'])
    months = preset['months']
    if tickers is None:
        if 'tickers' in preset:
            tickers = preset['tickers']
        else:
            big = get_big_tickers(db)
            tickers = big[:preset['n_tickers']] if preset.get('n_tickers') else big
    cfg = dict(config)
    cfg.setdefault('n_runs', preset['n_runs'])
    date_from = (pd.Timestamp.now() - pd.DateOffset(months=months)).strftime('%Y-%m-%d')
    results = []
    for tk in tickers:
        try:
            r = run_strategy_backtest(db, tk, cfg, date_from=date_from)
            results.append(r)
        except Exception as e:
            results.append({'status': 'failed', 'ticker': tk, 'error': str(e)})
    return {'depth': depth, 'months': months, 'tickers': tickers, 'n_runs': cfg['n_runs'],
            'results': results}


if __name__ == '__main__':
    import sys, json, logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    db = DBManager()
    config = {'patterns': ['levels_reversal'], 'confirm_windows': [10],
              'commission_pct': 0.06, 'slippage_pct': 0.06, 'risk_reward': None, 'n_runs': 1}
    out = run_depth_backtest(db, config, depth='express')
    print(json.dumps(out, default=str))
    db.close_pool()
