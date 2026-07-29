"""
Parameterizable strategy backtest engine (config-driven).

Patterns (AND logic): all selected patterns must fire on the same 1min bar for entry.
  - levels_reversal : price in 4h support zone + reversal confirmation on ALL selected
                      confirm_windows (closed higher-TF candle closes above zone).
  - rsi_oversold    : RSI-14 < 30 (computed on 1min close).
  - macd_bullish    : MACD histogram > 0 (computed on 1min close).
  - bb_lower        : close below Bollinger(20,2) lower band (computed on 1min close).

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

EXPRESS_TICKERS = ['SBER', 'LKOH', 'PIKK']

DEPTH_PRESETS = {
    'express':      {'months': 6,  'tickers': EXPRESS_TICKERS, 'n_runs': 10,  'walkforward': False},
    'serious':      {'months': 6,  'n_tickers': 15,            'n_runs': 40,  'walkforward': True},
    'very_serious': {'months': 24, 'n_tickers': None,          'n_runs': 100, 'walkforward': True},
}

ALL_PATTERNS = ['levels_reversal', 'rsi_oversold', 'macd_bullish', 'bb_lower']
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


def run_strategy_backtest(db, ticker: str, config: Dict, date_from=None, date_to=None) -> Dict:
    """Run backtest for one ticker under config. Returns metrics + trades."""
    patterns = config.get('patterns', ['levels_reversal'])
    confirm_windows = config.get('confirm_windows', [10])
    commission_pct = float(config.get('commission_pct', 0.06))
    slippage_pct = float(config.get('slippage_pct', 0.0))
    risk_reward = config.get('risk_reward')  # {'risk':..,'reward':..} or None
    entry_start, entry_end = config.get('entry_window', (7, 19))
    n_runs = int(config.get('n_runs', 1))
    round_trip = commission_pct / 100.0
    slip = slippage_pct / 100.0

    use_levels = 'levels_reversal' in patterns
    use_rsi = 'rsi_oversold' in patterns
    use_macd = 'macd_bullish' in patterns
    use_bb = 'bb_lower' in patterns

    if not use_levels:
        return {'status': 'failed', 'ticker': ticker,
                'error': 'levels_reversal required (defines stop/take); indicator patterns are AND-filters'}

    # 4h levels
    df_4h = db.select("SELECT timestamp, open, high, low, close FROM trading.candles_aggregated "
                      "WHERE ticker=%s AND timeframe='4h' ORDER BY timestamp", (ticker,)).to_dataframe()
    if df_4h.empty:
        return {'status': 'failed', 'ticker': ticker, 'error': 'no 4h candles'}
    for c in ['open', 'high', 'low', 'close']:
        df_4h[c] = pd.to_numeric(df_4h[c], errors='coerce')
    df_4h['atr'] = compute_atr(df_4h, 14)
    levels = build_levels(df_4h, swing_windows=(10,), body_ratio=0.7, impulse_atr_mult=1.5, zone_atr_mult=0.5)
    ts_4h = df_4h['timestamp'].tolist()
    atr_by_ts = dict(zip(df_4h['timestamp'], df_4h['atr']))

    # 1min candles (date-filtered)
    q = "SELECT timestamp, open, high, low, close FROM trading.candles_1min_raw WHERE ticker=%s"
    params = [ticker]
    if date_from is not None:
        q += " AND timestamp >= %s"; params.append(date_from)
    if date_to is not None:
        q += " AND timestamp < %s"; params.append(date_to)
    q += " ORDER BY timestamp"
    df_1m = db.select(q, tuple(params)).to_dataframe()
    if df_1m.empty:
        return {'status': 'failed', 'ticker': ticker, 'error': 'no 1min candles'}
    for c in ['open', 'high', 'low', 'close']:
        df_1m[c] = pd.to_numeric(df_1m[c], errors='coerce')

    if use_rsi or use_macd or use_bb:
        df_1m = compute_indicators_1min(df_1m)

    # Multi-window confirmation series (AND)
    confirm_series = []
    for w in confirm_windows:
        cs = build_confirm_series(aggregate_1min_to(df_1m, w))
        confirm_series.append(([c[0] for c in cs], [c[1] for c in cs]))

    def active_4h_ts(ts):
        i = bisect.bisect_right(ts_4h, ts) - 1
        return ts_4h[i] if i >= 0 else None

    def last_closed(times, closes, ts):
        i = bisect.bisect_right(times, ts) - 1
        return closes[i] if i >= 0 else None

    trades = []
    position = None
    for i in range(len(df_1m)):
        row = df_1m.iloc[i]
        ts = pd.Timestamp(row['timestamp'])
        price = float(row['close'])

        # --- exit ---
        if position is not None:
            exited = None
            if row['low'] <= position['stop']:
                exited = (position['stop'], 'stop')
            elif row['high'] >= position['take']:
                exited = (position['take'], 'take')
            if exited:
                exit_price, reason = exited
                gross = (exit_price * (1 - slip) / position['entry_exec'] - 1.0) * 100.0
                net = gross - round_trip * 100.0
                trades.append({'entry_ts': str(position['entry_ts']), 'exit_ts': str(ts),
                               'entry_price': float(position['entry_price']), 'exit_price': float(exit_price),
                               'exit_reason': reason, 'bars_held': i - position['idx'],
                               'net_return_pct': round(net, 5)})
                position = None
                continue

        # --- entry ---
        if position is None and entry_start <= ts.hour < entry_end:
            a4 = active_4h_ts(ts)
            if a4 is None:
                continue
            sup = nearest_level_at(levels, a4, price, 'support')
            if sup is None:
                continue
            zl, zu = sup['zone_lower'], sup['zone_upper']
            atr_val = float(atr_by_ts.get(a4, 0.0) or 0.0)
            if not ((zl <= price <= zu) or (zu < price <= zu + 0.5 * atr_val)):
                continue
            # multi-window confirmation (AND)
            ok = True
            for times, closes in confirm_series:
                hc = last_closed(times, closes, ts)
                if hc is None or hc <= zu:
                    ok = False
                    break
            if not ok:
                continue
            stop = float(sup['level_price'])
            if stop >= price:
                continue
            res = nearest_level_at(levels, a4, price, 'resistance')
            if res is None:
                continue
            take = float(res['level_price'])
            # indicator AND-filters
            if use_rsi and not (pd.notna(row.get('rsi_14')) and row['rsi_14'] < 30):
                continue
            if use_macd and not (pd.notna(row.get('macd_hist')) and row['macd_hist'] > 0):
                continue
            if use_bb and not (pd.notna(row.get('bb_lower')) and price < row['bb_lower']):
                continue
            # risk/reward filter
            if risk_reward:
                risk = price - stop
                reward = take - price
                ratio = float(risk_reward.get('reward', 2.0)) / float(risk_reward.get('risk', 1.0))
                if risk <= 0 or reward < ratio * risk + round_trip * price:
                    continue
            position = {'entry_ts': ts, 'entry_price': price, 'entry_exec': price * (1 + slip),
                        'stop': stop, 'take': take, 'idx': i}

    m = _bootstrap_metrics(trades, n_runs)
    return {'status': 'success', 'ticker': ticker, 'config': config,
            'bars_1min': len(df_1m), 'metrics': m, 'trades': trades}


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
