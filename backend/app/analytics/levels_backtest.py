"""
Multi-timeframe levels backtest engine (prototype v2).
Simulates trades on 1min bars using 4h support/resistance levels and 4h BUY signals.
Entry modes: levels_only / levels_ts1 / levels_ts2.
Reversal confirmation (confirm_tf): instead of entering immediately when 1min close is
in the support zone, wait for the last CLOSED higher-TF candle (5min/10min/30min) to
close ABOVE the support zone (close > zone_upper) -> confirms bounce. Aggregated on the fly
from 1min candles.
Risk-reward filter (risk_reward): entry only if (resistance-entry) >= risk_reward*(entry-stop)
+ commission cost. risk_reward=0 disables the filter.
Exit: stop by 1min low at support level, take by 1min high at resistance level.
Entry window 7-19 MSK. Stay overnight/weekends. ATR on the fly from 4h candles.
"""
from __future__ import annotations
import bisect
import numpy as np
import pandas as pd
from app.db.db_manager import DBManager
from app.analytics.levels_engine import build_levels, nearest_level_at

COMMISSION_PER_SIDE = 0.0003
ROUND_TRIP = 2 * COMMISSION_PER_SIDE  # 0.06%


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    prev_close = close.shift()
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def aggregate_1min_to(df_1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1min candles to N-min candles. Returns [timestamp (period start), open, high, low, close]."""
    df = df_1m.copy()
    df['ts'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('ts')
    rule = f'{minutes}min'
    agg = df.resample(rule, label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna(subset=['open'])
    agg = agg.reset_index()
    return agg  # columns: ts, open, high, low, close


def build_confirm_series(df_htf: pd.DataFrame) -> list:
    """From higher-TF candles, build sorted list of (close_time, close) for closed candles.
    close_time = period_start + period (candle close moment)."""
    if df_htf.empty:
        return []
    ts = pd.to_datetime(df_htf['ts']).tolist()
    closes = df_htf['close'].tolist()
    # infer period from first two timestamps
    if len(ts) >= 2:
        period = ts[1] - ts[0]
    else:
        period = pd.Timedelta(minutes=5)
    out = []
    for i in range(len(ts)):
        close_time = ts[i] + period
        out.append((close_time, float(closes[i])))
    return out


def _metrics(trades):
    if not trades:
        return {'n_trades': 0, 'profit_factor': None, 'expectancy': None, 'win_rate': None,
                'avg_bars_held': None, 'total_net_pct': 0.0}
    nets = [t['net_return_pct'] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float('inf') if gross_win > 0 else None)
    cum = np.cumsum(nets)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(dd.max()) if len(dd) else 0.0
    return {
        'n_trades': len(trades),
        'profit_factor': round(pf, 4) if pf is not None and pf != float('inf') else pf,
        'expectancy': round(float(np.mean(nets)), 5),
        'win_rate': round(len(wins) / len(nets), 4),
        'avg_bars_held': round(float(np.mean([t['bars_held'] for t in trades])), 1),
        'total_net_pct': round(float(sum(nets)), 3),
        'max_drawdown_pct': round(max_dd, 3),
    }


def run_levels_backtest(db, ticker: str, entry_mode: str = 'levels_ts1',
                        swing_window: int = 10, zone_atr: float = 0.5,
                        confirm_tf: str = None, risk_reward: float = 2.0,
                        entry_window_start: int = 7, entry_window_end: int = 19,
                        slippage_per_side: float = 0.0):
    """confirm_tf: None/'5min'/'10min'/'30min'. risk_reward: 0 disables filter."""
    # 1. 4h candles + ATR
    df_4h = db.select("""
        SELECT timestamp, open, high, low, close FROM trading.candles_aggregated
        WHERE ticker=%s AND timeframe='4h' ORDER BY timestamp
    """, (ticker,)).to_dataframe()
    if df_4h.empty:
        return {'status': 'failed', 'error': 'no 4h candles'}
    for c in ['open', 'high', 'low', 'close']:
        df_4h[c] = pd.to_numeric(df_4h[c], errors='coerce')
    df_4h['atr'] = compute_atr(df_4h, 14)

    # 2. 4h levels
    levels = build_levels(df_4h, swing_windows=(swing_window,), body_ratio=0.7,
                          impulse_atr_mult=1.5, zone_atr_mult=zone_atr)

    # 3. 4h BUY signals
    signal_ts = None
    if entry_mode in ('levels_ts1', 'levels_ts2'):
        min_ts = 1 if entry_mode == 'levels_ts1' else 2
        df_sig = db.select("""
            SELECT timestamp, total_signals FROM trading.signals
            WHERE ticker=%s AND timeframe='4h' AND signal='BUY' ORDER BY timestamp
        """, (ticker,)).to_dataframe()
        df_sig = df_sig[df_sig['total_signals'].fillna(0) >= min_ts]
        signal_ts = sorted(df_sig['timestamp'].tolist())

    # 4. 1min candles
    df_1m = db.select("""
        SELECT timestamp, open, high, low, close FROM trading.candles_1min_raw
        WHERE ticker=%s ORDER BY timestamp
    """, (ticker,)).to_dataframe()
    if df_1m.empty:
        return {'status': 'failed', 'error': 'no 1min candles'}
    for c in ['open', 'high', 'low', 'close']:
        df_1m[c] = pd.to_numeric(df_1m[c], errors='coerce')

    # 5. Confirmation series (higher-TF closed candles) if needed
    confirm_series = None  # list of (close_time, close)
    if confirm_tf is not None:
        minutes = {'5min': 5, '10min': 10, '30min': 30}.get(confirm_tf)
        if minutes is None:
            return {'status': 'failed', 'error': f'bad confirm_tf {confirm_tf}'}
        df_htf = aggregate_1min_to(df_1m, minutes)
        confirm_series = build_confirm_series(df_htf)
        confirm_times = [c[0] for c in confirm_series]
        confirm_closes = [c[1] for c in confirm_series]

    ts_4h = df_4h['timestamp'].tolist()

    def active_4h_ts(ts):
        idx = bisect.bisect_right(ts_4h, ts) - 1
        return ts_4h[idx] if idx >= 0 else None

    def signal_active(ts):
        if signal_ts is None:
            return True
        idx = bisect.bisect_right(signal_ts, ts) - 1
        if idx < 0:
            return False
        sig_ts = signal_ts[idx]
        a4 = active_4h_ts(ts)
        return a4 is not None and sig_ts <= ts and sig_ts >= a4

    def last_closed_htf_close(ts):
        """Close of the last higher-TF candle closed at or before ts."""
        if confirm_series is None:
            return None
        idx = bisect.bisect_right(confirm_times, ts) - 1
        if idx < 0:
            return None
        return confirm_closes[idx]

    trades = []
    position = None

    for i in range(len(df_1m)):
        row = df_1m.iloc[i]
        ts = pd.Timestamp(row['timestamp'])
        hour = ts.hour

        # --- Exit ---
        if position is not None:
            exited = False
            if row['low'] <= position['stop']:
                exit_price = position['stop']; exit_reason = 'stop'; exited = True
            elif row['high'] >= position['take']:
                exit_price = position['take']; exit_reason = 'take'; exited = True
            if exited:
                exit_exec = exit_price * (1.0 - slippage_per_side)
                gross = (exit_exec / position['entry_exec'] - 1.0) * 100.0
                net = gross - ROUND_TRIP * 100.0
                trades.append({'entry_ts': str(position['entry_ts']), 'exit_ts': str(ts),
                               'entry_price': float(position['entry_price']), 'exit_price': float(exit_price),
                               'exit_reason': exit_reason, 'bars_held': i - position['entry_bar_idx'],
                               'gross_return_pct': round(gross, 5), 'net_return_pct': round(net, 5)})
                position = None
                continue

        # --- Entry ---
        if position is None and entry_window_start <= hour < entry_window_end:
            a4 = active_4h_ts(ts)
            if a4 is None:
                continue
            if not signal_active(ts):
                continue
            price = float(row['close'])
            sup = nearest_level_at(levels, a4, price, 'support')
            if sup is None:
                continue
            zl, zu = sup['zone_lower'], sup['zone_upper']
            # price must be in support zone OR just above it (within 0.5 ATR)
            atr_val = float(df_4h.loc[df_4h['timestamp'] == a4, 'atr'].iloc[0]) if a4 in ts_4h else 0.0
            in_zone = (zl <= price <= zu) or (zu < price <= zu + 0.5 * atr_val)
            if not in_zone:
                continue
            # reversal confirmation
            if confirm_series is not None:
                htf_close = last_closed_htf_close(ts)
                if htf_close is None or htf_close <= zu:
                    continue  # no confirmed bounce above zone yet
            stop = float(sup['level_price'])
            if stop >= price:
                continue
            res = nearest_level_at(levels, a4, price, 'resistance')
            if res is None:
                continue
            take = float(res['level_price'])
            risk = price - stop
            reward = take - price
            if risk_reward > 0 and reward < risk_reward * risk + ROUND_TRIP * price:
                continue
            entry_exec = price * (1.0 + slippage_per_side)
            position = {'entry_ts': ts, 'entry_price': price, 'entry_exec': entry_exec, 'stop': stop, 'take': take, 'entry_bar_idx': i}

    open_position = None
    if position is not None:
        last_close = float(df_1m.iloc[-1]['close'])
        gross = (last_close / position['entry_price'] - 1.0) * 100.0
        open_position = {'entry_ts': str(position['entry_ts']), 'entry_price': float(position['entry_price']),
                         'last_close': last_close, 'unrealized_net_pct': round(gross - ROUND_TRIP * 100.0, 5)}

    m = _metrics(trades)
    return {'status': 'success', 'ticker': ticker, 'entry_mode': entry_mode,
            'swing_window': swing_window, 'zone_atr': zone_atr,
            'confirm_tf': confirm_tf, 'risk_reward': risk_reward,
            'bars_1min': len(df_1m), 'levels_total': int(len(levels)),
            'metrics': m, 'open_position': open_position, 'trades': trades,
 'trades_sample': trades[:5]}
