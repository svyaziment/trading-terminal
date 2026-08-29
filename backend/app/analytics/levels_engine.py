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
Entry veto for an opposing zone is overlapping_resistance_zone_at (Issue #97).
Issue #106: LevelsTracker tracks in-memory lifecycle
active → broken_up/down → flipped_support/resistance. Veto skips non-active
states when a `state` column is present; build_levels output has no `state`.
Issue #107: optional `tracker` uses `is_broken(level_id)` so a broken
resistance is no longer an opposing zone. StrategyEvaluator passes the
tracker when `level_breakout_retest` or `levels_sr_breakout` is enabled.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Union

import numpy as np
import pandas as pd

from app.analytics.trading_config import get_level_state_machine_config

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
    Active = available_from_ts <= bar_ts. Returns dict or None.

    One-sided on purpose: resistance below the market is never the take, and
    support above the market is never the stop. It therefore cannot veto an
    entry that sits inside an opposing zone — use overlapping_resistance_zone_at
    for that (Issue #97 / ALRS paper #711).
    """
    if levels_df is None or levels_df.empty:
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


def overlapping_resistance_zone_at(
    levels_df: pd.DataFrame,
    bar_ts,
    bar_price: float,
    tracker: Optional['LevelsTracker'] = None,
):
    """Active resistance whose native ATR zone contains bar_price, or None.

    Includes resistance BELOW the market. That is the ALRS #711 collision:
    fill 19.80 sat inside impulse resistance 19.67 [19.40, 19.94] while
    nearest_level_at(..., 'support') still returned the older 19.61 support.

    Native zone only ([zone_lower, zone_upper]); the 0.5×ATR support-side
    extension in check_entry is NOT mirrored here. If several zones overlap,
    the closest level_price wins.

    Issue #106: when a `state` column is present, only `active` (or missing /
    empty) rows participate in the veto. Broken and flipped resistances are
    no longer opposing zones. DataFrames from build_levels / get_levels have
    no `state` column, so the Issue #97 path is unchanged.

    Issue #107: when `tracker` is given, the snapshot from
    `get_levels_with_state()` is used and rows with `tracker.is_broken(level_id)`
    are skipped. Callers that omit `tracker` keep the Issue #97/#106 behaviour.
    """
    if tracker is not None:
        snapshot = tracker.get_levels_with_state()
        if snapshot is not None and not snapshot.empty:
            levels_df = snapshot
    if levels_df is None or levels_df.empty:
        return None
    active = levels_df[
        (levels_df['available_from_ts'] <= bar_ts)
        & (levels_df['type'] == 'resistance')
        & (levels_df['zone_lower'] <= bar_price)
        & (levels_df['zone_upper'] >= bar_price)
    ]
    if 'state' in levels_df.columns and not active.empty:
        raw = active['state']
        normalized = raw.where(raw.notna(), LevelState.ACTIVE.value)
        normalized = normalized.astype(str).str.strip().str.lower()
        active = active[normalized.isin(_VETO_ACTIVE_STATES)]
    if tracker is not None and not active.empty and 'level_id' in active.columns:
        keep = []
        for idx in active.index:
            lid = active.loc[idx, 'level_id']
            if lid is None or (isinstance(lid, float) and np.isnan(lid)):
                keep.append(idx)
                continue
            try:
                if tracker.is_broken(lid):
                    continue
            except KeyError:
                keep.append(idx)
                continue
            keep.append(idx)
        active = active.loc[keep] if keep else active.iloc[0:0]
    if active.empty:
        return None
    idx = (active['level_price'] - bar_price).abs().idxmin()
    row = active.loc[idx]
    return {
        'level_price': float(row['level_price']),
        'zone_lower': float(row['zone_lower']),
        'zone_upper': float(row['zone_upper']),
        'method': row['method'],
    }


# Stable alias used by LevelsTracker and Issue #106 docs. Same object as build_levels.
get_levels = build_levels


class LevelState(str, Enum):
    """Lifecycle of one support/resistance zone (Issue #106)."""

    ACTIVE = 'active'
    BROKEN_UP = 'broken_up'
    BROKEN_DOWN = 'broken_down'
    FLIPPED_SUPPORT = 'flipped_support'
    FLIPPED_RESISTANCE = 'flipped_resistance'


_BROKEN_STATES = frozenset({
    LevelState.BROKEN_UP,
    LevelState.BROKEN_DOWN,
    LevelState.FLIPPED_SUPPORT,
    LevelState.FLIPPED_RESISTANCE,
    LevelState.BROKEN_UP.value,
    LevelState.BROKEN_DOWN.value,
    LevelState.FLIPPED_SUPPORT.value,
    LevelState.FLIPPED_RESISTANCE.value,
})
_VETO_ACTIVE_STATES = frozenset({'active', '', 'nan', 'none'})
_BarLike = Union[Mapping[str, Any], pd.Series]


def _make_level_id(idx: int, row: pd.Series) -> str:
    ts = pd.Timestamp(row['defined_ts']).isoformat()
    return f"{idx}:{ts}:{float(row['level_price']):.8f}:{row['type']}:{row['method']}"


def _bar_fields(bar: _BarLike):
    if isinstance(bar, pd.Series):
        ts = bar['timestamp']
        close = float(bar['close'])
        atr = bar['atr'] if 'atr' in bar.index else None
    else:
        ts = bar['timestamp']
        close = float(bar['close'])
        atr = bar.get('atr')
    atr_val = None if atr is None or (isinstance(atr, float) and np.isnan(atr)) else float(atr)
    if atr_val is not None and (np.isnan(atr_val) or atr_val <= 0):
        atr_val = None
    return pd.Timestamp(ts), close, atr_val


class LevelsTracker:
    """In-memory state machine over a snapshot from get_levels() / build_levels().

    Feed bars of the *same* timeframe as the levels (typically 4h) via update()
    or update_bars(). No DB persistence. StrategyEvaluator wires this when
    `level_breakout_retest` or `levels_sr_breakout` is enabled.
    """

    def __init__(self, levels_df: pd.DataFrame, config: Optional[Mapping[str, Any]] = None):
        cfg = get_level_state_machine_config()
        if config:
            cfg.update(dict(config))
        self._config = cfg
        if levels_df is None or levels_df.empty:
            self._levels = pd.DataFrame(columns=list(_LEVEL_COLS) + ['level_id'])
        else:
            self._levels = levels_df.copy().reset_index(drop=True)
            self._levels['level_id'] = [
                _make_level_id(i, self._levels.iloc[i]) for i in range(len(self._levels))
            ]
        self._states: dict[str, str] = {
            str(lid): LevelState.ACTIVE.value for lid in self._levels['level_id']
        } if not self._levels.empty else {}
        self._timestamps: list[pd.Timestamp] = []
        self._closes: list[float] = []
        self._atrs: list[Optional[float]] = []
        self._broken_bar_index: dict[str, int] = {}

    def _resolve_id(self, level_id) -> str:
        if level_id in self._states:
            return level_id
        if isinstance(level_id, (int, np.integer)) and 0 <= int(level_id) < len(self._levels):
            return str(self._levels.iloc[int(level_id)]['level_id'])
        raise KeyError(f'unknown level_id: {level_id!r}')

    def is_broken(self, level_id) -> bool:
        """True once the zone has left `active` (broken or flipped)."""
        return self._states[self._resolve_id(level_id)] in _BROKEN_STATES

    def bars_since_breakout(self, level_id) -> Optional[int]:
        """HTF bars elapsed since the confirmed break, or None if still active."""
        lid = self._resolve_id(level_id)
        idx = self._broken_bar_index.get(lid)
        if idx is None:
            return None
        return len(self._timestamps) - 1 - idx

    def get_state(self, level_id) -> str:
        return self._states[self._resolve_id(level_id)]

    def get_levels_with_state(self) -> pd.DataFrame:
        out = self._levels.copy()
        if out.empty:
            out['state'] = pd.Series(dtype=str)
            return out
        out['state'] = [self._states[str(lid)] for lid in out['level_id']]
        return out

    def _closes_for_level(self, available_from_ts) -> tuple[list[float], Optional[float]]:
        avail = pd.Timestamp(available_from_ts)
        closes: list[float] = []
        last_atr: Optional[float] = None
        for ts, close, atr in zip(self._timestamps, self._closes, self._atrs):
            if ts >= avail:
                closes.append(close)
                last_atr = atr
        return closes, last_atr

    def _atr(self, level_atr: float, bar_atr: Optional[float]) -> float:
        if bar_atr is not None and bar_atr > 0:
            return float(bar_atr)
        return float(level_atr) if level_atr and level_atr > 0 else 0.0

    def _breaks_resistance(self, zone_upper: float, closes: Sequence[float], atr: float) -> bool:
        n = int(self._config['confirm_bars'])
        if n < 1 or len(closes) < n or atr <= 0:
            return False
        window = list(closes[-n:])
        if not all(c > zone_upper for c in window):
            return False
        buffer = float(self._config['breakout_buffer_atr']) * atr
        min_pen = float(self._config['min_penetration_atr']) * atr
        if window[-1] <= zone_upper + buffer:
            return False
        if max(window) < zone_upper + min_pen:
            return False
        return True

    def _breaks_support(self, zone_lower: float, closes: Sequence[float], atr: float) -> bool:
        n = int(self._config['confirm_bars'])
        if n < 1 or len(closes) < n or atr <= 0:
            return False
        window = list(closes[-n:])
        if not all(c < zone_lower for c in window):
            return False
        buffer = float(self._config['breakout_buffer_atr']) * atr
        min_pen = float(self._config['min_penetration_atr']) * atr
        if window[-1] >= zone_lower - buffer:
            return False
        if min(window) > zone_lower - min_pen:
            return False
        return True

    def update(self, bar: _BarLike) -> None:
        """Advance the state machine by one closed bar of the levels timeframe."""
        ts, close, bar_atr = _bar_fields(bar)
        self._timestamps.append(ts)
        self._closes.append(close)
        self._atrs.append(bar_atr)
        if self._levels.empty:
            return
        for i in range(len(self._levels)):
            row = self._levels.iloc[i]
            lid = str(row['level_id'])
            if pd.Timestamp(row['available_from_ts']) > ts:
                continue
            state = self._states[lid]
            zone_lower = float(row['zone_lower'])
            zone_upper = float(row['zone_upper'])
            atr = self._atr(float(row['atr']), bar_atr)
            closes, _ = self._closes_for_level(row['available_from_ts'])
            if state == LevelState.ACTIVE.value:
                if row['type'] == 'resistance' and self._breaks_resistance(zone_upper, closes, atr):
                    self._states[lid] = LevelState.BROKEN_UP.value
                    self._broken_bar_index[lid] = len(self._timestamps) - 1
                elif row['type'] == 'support' and self._breaks_support(zone_lower, closes, atr):
                    self._states[lid] = LevelState.BROKEN_DOWN.value
                    self._broken_bar_index[lid] = len(self._timestamps) - 1
            elif state == LevelState.BROKEN_UP.value:
                if zone_lower <= close <= zone_upper:
                    self._states[lid] = LevelState.FLIPPED_SUPPORT.value
            elif state == LevelState.BROKEN_DOWN.value:
                if zone_lower <= close <= zone_upper:
                    self._states[lid] = LevelState.FLIPPED_RESISTANCE.value

    def update_bars(self, bars: pd.DataFrame) -> None:
        if bars is None or bars.empty:
            return
        for i in range(len(bars)):
            self.update(bars.iloc[i])


def get_levels_with_state(
    levels_df: pd.DataFrame,
    bars: Optional[pd.DataFrame] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> pd.DataFrame:
    """Replay optional bars through LevelsTracker and return levels + state."""
    tracker = LevelsTracker(levels_df, config=config)
    if bars is not None:
        tracker.update_bars(bars)
    return tracker.get_levels_with_state()
