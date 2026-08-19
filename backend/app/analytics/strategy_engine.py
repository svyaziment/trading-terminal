"""
Unified step-by-step strategy engine (single brain for backtest / paper / live).

The entry/exit logic lives here and is IDENTICAL across all modes. What differs:
  - data source: HistoricalFeed (backtest) vs LiveFeed (paper/live);
  - execution:   PaperExecutor (paper) vs LiveExecutor (future real trading).

`StrategyEvaluator.on_bar(row, idx)` processes one 1min bar and returns a Decision:
  {'action': 'hold'}
  {'action': 'exit',  'trade': {...}}
  {'action': 'enter', 'entry_price':..., 'stop':..., 'take':...}

Usage (backtest):
    ev = StrategyEvaluator(config)
    ev.load_context(levels, ts_4h, atr_by_ts, buy_ts, confirm_series)
    for i in range(len(df_1m)):
        decision = ev.on_bar(df_1m.iloc[i], idx=i)

Live-only patterns (e.g. orderbook_imbalance) are AND-filters that only apply in
live mode; in backtest they are skipped (no historical order book).

SignalEngine pattern ids (PA_Hammer, MR_RSI_Reversal, ...) are AND-filters evaluated
inline on the selected HTF (Issue #79). ``signal_4h_buy`` remains a trading.signals
lookup and is not mixed into that path.
"""
from __future__ import annotations

import bisect
import pandas as pd

from app.analytics.levels_engine import nearest_level_at
from app.analytics.signal_pattern_filters import (
    enabled_signal_filters,
    signal_engine_filters_pass,
)


def _4h_buy_active(buy_ts, ts_4h, ts) -> bool:
    """True if a 4h BUY signal is active at ts (signal ts <= ts and within the same 4h bar)."""
    if not buy_ts:
        return False
    i = bisect.bisect_right(buy_ts, ts) - 1
    if i < 0:
        return False
    sig_ts = buy_ts[i]
    j = bisect.bisect_right(ts_4h, ts) - 1
    a4 = ts_4h[j] if j >= 0 else None
    return a4 is not None and sig_ts <= ts and sig_ts >= a4


class StrategyEvaluator:
    """Stateful per-ticker strategy evaluator (one brain, bar-by-bar)."""

    def __init__(self, config: dict):
        self.config = config
        self.patterns = config.get('patterns', ['levels_reversal'])
        self.confirm_windows = config.get('confirm_windows', [10])
        self.commission_pct = float(config.get('commission_pct', 0.06))
        self.slippage_pct = float(config.get('slippage_pct', 0.0))
        self.risk_reward = config.get('risk_reward')
        self.entry_start, self.entry_end = config.get('entry_window', (7, 19))
        self.round_trip = self.commission_pct / 100.0
        self.slip = self.slippage_pct / 100.0

        self.use_levels = 'levels_reversal' in self.patterns
        self.use_rsi = 'rsi_oversold' in self.patterns
        self.use_macd = 'macd_bullish' in self.patterns
        self.use_bb = 'bb_lower' in self.patterns
        self.use_4h_buy = 'signal_4h_buy' in self.patterns
        self.signal_filter_specs = enabled_signal_filters(self.patterns)

        # context (filled via load_context / update_context)
        self.levels = None
        self.ts_4h = []
        self.atr_by_ts = {}
        self.buy_ts = []
        self.confirm_series = []  # list of (times, closes)
        self.signal_filter_series = []

        # position state
        self.position = None

    def _set_signal_filter_series(self, series) -> None:
        prepared = []
        for item in series or []:
            times = [pd.Timestamp(ts) for ts in item.get('times') or []]
            buy_set = {pd.Timestamp(ts) for ts in item.get('buy_ts') or []}
            prepared.append({
                'pattern_id': item.get('pattern_id'),
                'timeframe': item.get('timeframe'),
                'times': times,
                'buy_set': buy_set,
            })
        self.signal_filter_series = prepared

    def load_context(self, levels, ts_4h, atr_by_ts, buy_ts, confirm_series,
                     signal_filter_series=None) -> None:
        """Set the 4h context used for entry decisions. Called once for backtest;
        refreshed periodically in live mode via update_context."""
        self.levels = levels
        self.ts_4h = ts_4h
        self.atr_by_ts = atr_by_ts
        self.buy_ts = buy_ts
        self.confirm_series = confirm_series
        self._set_signal_filter_series(signal_filter_series)

    def update_context(self, **kwargs) -> None:
        """Partial context refresh for live mode (new 4h levels / BUY signals / confirms)."""
        if 'signal_filter_series' in kwargs:
            self._set_signal_filter_series(kwargs['signal_filter_series'])
        for k in ('levels', 'ts_4h', 'atr_by_ts', 'buy_ts', 'confirm_series'):
            if k in kwargs:
                setattr(self, k, kwargs[k])

    def reset(self) -> None:
        self.position = None

    def _active_4h_ts(self, ts):
        i = bisect.bisect_right(self.ts_4h, ts) - 1
        return self.ts_4h[i] if i >= 0 else None

    def _last_closed(self, times, closes, ts):
        i = bisect.bisect_right(times, ts) - 1
        return closes[i] if i >= 0 else None

    def check_entry(self, row) -> dict:
        """Pure entry check for the current bar (NO position state). Identical
        logic for backtest and live. Returns {'action':'enter',...} or None."""
        ts = pd.Timestamp(row['timestamp'])
        price = float(row['close'])
        if not (self.entry_start <= ts.hour < self.entry_end):
            return None
        a4 = self._active_4h_ts(ts)
        if a4 is None:
            return None
        sup = nearest_level_at(self.levels, a4, price, 'support')
        if sup is None:
            return None
        zl, zu = sup['zone_lower'], sup['zone_upper']
        atr_val = float(self.atr_by_ts.get(a4, 0.0) or 0.0)
        if not ((zl <= price <= zu) or (zu < price <= zu + 0.5 * atr_val)):
            return None
        # multi-window confirmation (AND)
        for times, closes in self.confirm_series:
            hc = self._last_closed(times, closes, ts)
            if hc is None or hc <= zu:
                return None
        if self.use_4h_buy and not _4h_buy_active(self.buy_ts, self.ts_4h, ts):
            return None
        # SignalEngine AND-filters on last closed HTF bar (inline evaluate).
        # No-op when no SignalEngine pattern is enabled (default strategies).
        if not signal_engine_filters_pass(
            self.signal_filter_specs, self.signal_filter_series, ts
        ):
            return None
        stop = float(sup['level_price'])
        if stop >= price:
            return None
        res = nearest_level_at(self.levels, a4, price, 'resistance')
        if res is None:
            return None
        take = float(res['level_price'])
        # indicator AND-filters
        if self.use_rsi and not (pd.notna(row.get('rsi_14')) and row['rsi_14'] < 30):
            return None
        if self.use_macd and not (pd.notna(row.get('macd_hist')) and row['macd_hist'] > 0):
            return None
        if self.use_bb and not (pd.notna(row.get('bb_lower')) and price < row['bb_lower']):
            return None
        # risk/reward filter
        if self.risk_reward:
            risk = price - stop
            reward = take - price
            ratio = float(self.risk_reward.get('reward', 2.0)) / float(self.risk_reward.get('risk', 1.0))
            if risk <= 0 or reward < ratio * risk + self.round_trip * price:
                return None
        return {'action': 'enter', 'entry_price': price, 'stop': stop, 'take': take, 'ts': ts}

    def on_bar(self, row, idx: int) -> dict:
        """Process one 1min bar (backtest mode; manages position state).
        Entry logic is delegated to the unified check_entry."""
        ts = pd.Timestamp(row['timestamp'])

        # --- exit ---
        if self.position is not None:
            exited = None
            if row['low'] <= self.position['stop']:
                exited = (self.position['stop'], 'stop')
            elif row['high'] >= self.position['take']:
                exited = (self.position['take'], 'take')
            if exited:
                exit_price, reason = exited
                gross = (exit_price * (1 - self.slip) / self.position['entry_exec'] - 1.0) * 100.0
                net = gross - self.round_trip * 100.0
                trade = {
                    'entry_ts': str(self.position['entry_ts']),
                    'exit_ts': str(ts),
                    'entry_price': float(self.position['entry_price']),
                    'exit_price': float(exit_price),
                    'exit_reason': reason,
                    'bars_held': idx - self.position['idx'],
                    'net_return_pct': round(net, 5),
                }
                self.position = None
                return {'action': 'exit', 'trade': trade}
            return {'action': 'hold'}

        # --- entry (unified check_entry) ---
        dec = self.check_entry(row)
        if dec is not None:
            price = dec['entry_price']
            self.position = {
                'entry_ts': dec['ts'], 'entry_price': price, 'entry_exec': price * (1 + self.slip),
                'stop': dec['stop'], 'take': dec['take'], 'idx': idx,
            }
            return {'action': 'enter', 'entry_price': price, 'stop': dec['stop'], 'take': dec['take']}

        return {'action': 'hold'}
