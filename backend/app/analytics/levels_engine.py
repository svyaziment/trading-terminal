"""
Support/resistance levels engine.
Defines horizontal levels on a higher timeframe (e.g. 4h) using two methods:
1. Swing levels: swing high/low fractals (window N bars left/right), no clustering (raw).
2. Impulse candle levels: filled impulse candles (body/range > body_ratio AND
   |close-open| > impulse_atr_mult * ATR); candle open is the level
   (green candle -> support, red candle -> resistance).
Rolling window, NO look-ahead: a swing level defined on bar i is available only from
bar i+window (after confirmation); an impulse level on bar i is available from bar i.
Zones: level_price +- zone_atr_mult * ATR(at definition).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

_LEVEL_COLS = ['available_from_ts', 'defined_ts', 'level_price', 'type', 'method',
               'atr', 'zone_lower', 'zone_upper']


def detect_swing_levels(df: pd.DataFrame, window: int):
    """Detect swing high/low fractals. df: [timestamp, high, low] sorted by timestamp."""
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    ts = df['timestamp'].to_numpy()
    n = len(df)
    out = []
    for i in range(window, n - window):
        seg_h = highs[i - window:i + window + 1]
        seg_l = lows[i - window:i + window + 1]
        avail = ts[min(i + window, n - 1)]
        if highs[i] >= seg_h.max():
            out.append({'defined_ts': ts[i], 'available_from_ts': avail,
                        'level_price': float(highs[i]), 'type': 'resistance', 'method': 'swing'})
        if lows[i] <= seg_l.min():
            out.append({'defined_ts': ts[i], 'available_from_ts': avail,
                        'level_price': float(lows[i]), 'type': 'support', 'method': 'swing'})
    return out


def detect_impulse_levels(df: pd.DataFrame, body_ratio: float = 0.7, impulse_atr_mult: float = 1.5):
    """Detect filled impulse candles; open as level (green->support, red->resistance).
    df: [timestamp, open, high, low, close, atr]."""
    out = []
    rng = (df['high'] - df['low']).to_numpy()
    body = (df['close'] - df['open']).abs().to_numpy()
    atr = df['atr'].to_numpy()
    ts = df['timestamp'].to_numpy()
    opens = df['open'].to_numpy()
    closes = df['close'].to_numpy()
    for i in range(len(df)):
        if rng[i] <= 0 or np.isnan(atr[i]) or atr[i] <= 0:
            continue
        filled = (body[i] / rng[i]) > body_ratio
        impulse = body[i] > impulse_atr_mult * atr[i]
        if filled and impulse:
            is_green = closes[i] > opens[i]
            out.append({'defined_ts': ts[i], 'available_from_ts': ts[i],
                        'level_price': float(opens[i]),
                        'type': 'support' if is_green else 'resistance', 'method': 'impulse'})
    return out


def build_levels(df: pd.DataFrame, swing_windows=(10,), body_ratio: float = 0.7,
                 impulse_atr_mult: float = 1.5, zone_atr_mult: float = 0.5,
                 include_swing: bool = True, include_impulse: bool = True) -> pd.DataFrame:
    """Build levels DataFrame. df must have [timestamp, open, high, low, close, atr]."""
    levels = []
    if include_swing:
        for w in swing_windows:
            levels.extend(detect_swing_levels(df, w))
    if include_impulse:
        levels.extend(detect_impulse_levels(df, body_ratio, impulse_atr_mult))
    if not levels:
        return pd.DataFrame(columns=_LEVEL_COLS)
    ldf = pd.DataFrame(levels)
    med_atr = float(df['atr'].median()) if 'atr' in df and not df['atr'].isna().all() else 0.0
    atr_map = dict(zip(df['timestamp'], df['atr']))
    ldf['atr'] = ldf['defined_ts'].map(atr_map).fillna(med_atr)
    ldf['zone_lower'] = ldf['level_price'] - zone_atr_mult * ldf['atr']
    ldf['zone_upper'] = ldf['level_price'] + zone_atr_mult * ldf['atr']
    return ldf[_LEVEL_COLS].sort_values('available_from_ts').reset_index(drop=True)


def nearest_level_at(levels_df: pd.DataFrame, bar_ts, bar_price: float, level_type: str):
    """Nearest active level of type at bar_ts. support: nearest below price; resistance: nearest above.
    Active = available_from_ts <= bar_ts. Returns dict or None."""
    if levels_df.empty:
        return None
    active = levels_df[(levels_df['available_from_ts'] <= bar_ts) & (levels_df['type'] == level_type)]
    if active.empty:
        return None
    if level_type == 'support':
        cand = active[active['level_price'] < bar_price]
        if cand.empty:
            return None
        idx = cand['level_price'].idxmax()
    else:
        cand = active[active['level_price'] > bar_price]
        if cand.empty:
            return None
        idx = cand['level_price'].idxmin()
    row = active.loc[idx]
    return {'level_price': float(row['level_price']), 'zone_lower': float(row['zone_lower']),
            'zone_upper': float(row['zone_upper']), 'method': row['method']}
