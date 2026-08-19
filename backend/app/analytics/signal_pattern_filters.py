"""SignalEngine AND-filters for StrategyEvaluator (Issue #79).

Chosen path (do not mix):
  * ``signal_4h_buy`` keeps looking up ``trading.signals`` (4h BUY aggregate).
    It is not refactored here.
  * The ten SignalEngine / ``BasePattern`` ids are evaluated **inline** via
    ``SignalEngine.process_dataframe`` / ``BasePattern.evaluate`` on the
    selected higher-timeframe indicator bars. They never read ``trading.signals``
    by ``pattern_name``.

``rsi_oversold`` (1min RSI < 30) is not a substitute for ``MR_RSI_Reversal``.

Stop/take stay defined only by ``levels_reversal``. These patterns are AND-filters
on the last *closed* HTF bar so historical backtests do not look ahead into a
still-forming bucket.
"""
from __future__ import annotations

import bisect
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from app.analytics.pattern_registry import (
    SIGNAL_ENGINE_PATTERN_IDS,
    is_signal_engine_pattern,
    resolve_signal_timeframe,
)
from app.analytics.patterns import (
    BO_BB_Squeeze,
    MR_RSI_Reversal,
    PA_Engulfing,
    PA_Hammer,
    PA_HangingMan,
    PA_ThreeBlackCrows,
    PA_ThreeWhiteSoldiers,
    Trend_SMA_Alignment,
    VOL_Low_Pullback,
    VOL_Spike,
)
from app.analytics.signal_engine import SignalEngine

SIGNAL_ENGINE_PATTERN_CLASSES = {
    "Trend_SMA_Alignment": Trend_SMA_Alignment,
    "PA_Engulfing": PA_Engulfing,
    "PA_HangingMan": PA_HangingMan,
    "PA_Hammer": PA_Hammer,
    "PA_ThreeBlackCrows": PA_ThreeBlackCrows,
    "PA_ThreeWhiteSoldiers": PA_ThreeWhiteSoldiers,
    "VOL_Spike": VOL_Spike,
    "VOL_Low_Pullback": VOL_Low_Pullback,
    "MR_RSI_Reversal": MR_RSI_Reversal,
    "BO_BB_Squeeze": BO_BB_Squeeze,
}

SIGNAL_TIMEFRAME_DELTAS = {
    "30min": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "2h": pd.Timedelta(hours=2),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
    "1w": pd.Timedelta(weeks=1),
}

_INDICATOR_NUMERIC_COLS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "sma_10",
    "sma_20",
    "sma_50",
    "sma_200",
    "rsi_14",
    "bb_upper",
    "bb_lower",
    "bb_width",
    "volume_ratio",
)


def iter_pattern_items(patterns: Any) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Yield ``(pattern_id, params)`` from list or dict config.patterns."""
    if isinstance(patterns, dict):
        for pattern_id, params in patterns.items():
            if isinstance(pattern_id, str):
                yield pattern_id, params if isinstance(params, dict) else {}
        return
    if isinstance(patterns, list):
        for pattern_id in patterns:
            if isinstance(pattern_id, str):
                yield pattern_id, {}


def enabled_signal_filters(patterns: Any) -> List[Dict[str, str]]:
    """Enabled SignalEngine AND-filters with resolved ``timeframe``."""
    filters: List[Dict[str, str]] = []
    for pattern_id, params in iter_pattern_items(patterns):
        if not is_signal_engine_pattern(pattern_id):
            continue
        if pattern_id not in SIGNAL_ENGINE_PATTERN_CLASSES:
            continue
        filters.append(
            {
                "pattern_id": pattern_id,
                "timeframe": resolve_signal_timeframe(params.get("timeframe")),
            }
        )
    return filters


def last_closed_htf_ts(
    times: Sequence,
    ts,
    timeframe: str,
) -> Optional[pd.Timestamp]:
    """Return the last HTF bar whose close time is ``<= ts`` (no lookahead).

    HTF ``timestamp`` is the bar open. Close = open + timeframe delta.
    Missing/unknown timeframe or empty history returns None (filter rejects).
    """
    delta = SIGNAL_TIMEFRAME_DELTAS.get(timeframe)
    if delta is None or not times:
        return None
    cutoff = pd.Timestamp(ts) - delta
    idx = bisect.bisect_right(list(times), cutoff) - 1
    if idx < 0:
        return None
    return times[idx]


def load_htf_indicator_frame(db, ticker: str, timeframe: str) -> pd.DataFrame:
    """Load ``trading.indicators`` rows for inline SignalEngine evaluation."""
    frame = db.select(
        "SELECT * FROM trading.indicators "
        "WHERE ticker=%s AND timeframe=%s ORDER BY timestamp",
        (ticker, timeframe),
    ).to_dataframe()
    if frame.empty:
        return frame

    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    for col in _INDICATOR_NUMERIC_COLS:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "ticker" not in frame.columns:
        frame["ticker"] = ticker
    return frame


def evaluate_buy_timestamps(
    df: pd.DataFrame,
    pattern_id: str,
    timeframe: str,
    ticker: str = "",
) -> List[pd.Timestamp]:
    """Run inline ``SignalEngine.evaluate`` and return HTF timestamps with BUY."""
    pattern_cls = SIGNAL_ENGINE_PATTERN_CLASSES.get(pattern_id)
    if pattern_cls is None or df.empty:
        return []

    work = df.copy()
    if "ticker" not in work.columns or work["ticker"].isna().all():
        work["ticker"] = ticker

    engine = SignalEngine([pattern_cls()])
    results = engine.process_dataframe(work, timeframe, lookback_window=50)
    timestamps = [pd.Timestamp(ts) for ts in work["timestamp"].tolist()]
    buy_ts: List[pd.Timestamp] = []
    for idx, result in enumerate(results):
        for signal in result.get("triggered_patterns") or []:
            if signal.get("direction") == "BUY" and signal.get("name") == pattern_id:
                buy_ts.append(timestamps[idx])
                break
    return buy_ts


def build_signal_filter_series(
    db,
    ticker: str,
    patterns: Any,
) -> List[Dict[str, Any]]:
    """Precompute per-filter HTF timestamps and BUY hits for StrategyEvaluator.

    One series entry per enabled SignalEngine pattern. Empty indicator history
    leaves ``times``/``buy_ts`` empty so the AND-filter rejects rather than
    silently passing.
    """
    specs = enabled_signal_filters(patterns)
    if not specs:
        return []

    frames_by_tf: Dict[str, pd.DataFrame] = {}
    needed = {spec["timeframe"] for spec in specs}
    for timeframe in needed:
        frames_by_tf[timeframe] = load_htf_indicator_frame(db, ticker, timeframe)

    series: List[Dict[str, Any]] = []
    buy_cache: Dict[Tuple[str, str], List[pd.Timestamp]] = {}
    for spec in specs:
        timeframe = spec["timeframe"]
        pattern_id = spec["pattern_id"]
        frame = frames_by_tf.get(timeframe)
        if frame is None or frame.empty:
            times: List[pd.Timestamp] = []
            buy_ts: List[pd.Timestamp] = []
        else:
            times = [pd.Timestamp(ts) for ts in frame["timestamp"].tolist()]
            cache_key = (pattern_id, timeframe)
            if cache_key not in buy_cache:
                buy_cache[cache_key] = evaluate_buy_timestamps(
                    frame, pattern_id, timeframe, ticker
                )
            buy_ts = buy_cache[cache_key]
        series.append(
            {
                "pattern_id": pattern_id,
                "timeframe": timeframe,
                "times": times,
                "buy_ts": buy_ts,
            }
        )
    return series


def signal_engine_filters_pass(
    specs: Sequence[Dict[str, str]],
    series: Sequence[Dict[str, Any]],
    ts,
) -> bool:
    """True when every enabled SignalEngine filter has a BUY on last closed HTF."""
    if not specs:
        return True
    by_key = {
        (item.get("pattern_id"), item.get("timeframe")): item for item in series
    }
    for spec in specs:
        item = by_key.get((spec["pattern_id"], spec["timeframe"]))
        if item is None:
            return False
        last_closed = last_closed_htf_ts(item.get("times") or [], ts, spec["timeframe"])
        buy_set = item.get("buy_set")
        if buy_set is None:
            buy_set = set(item.get("buy_ts") or [])
        if last_closed is None or last_closed not in buy_set:
            return False
    return True


# Keep the public id tuple imported so callers can do
# ``from app.analytics.signal_pattern_filters import SIGNAL_ENGINE_PATTERN_IDS``.
__all__ = [
    "SIGNAL_ENGINE_PATTERN_CLASSES",
    "SIGNAL_ENGINE_PATTERN_IDS",
    "SIGNAL_TIMEFRAME_DELTAS",
    "build_signal_filter_series",
    "enabled_signal_filters",
    "evaluate_buy_timestamps",
    "iter_pattern_items",
    "last_closed_htf_ts",
    "load_htf_indicator_frame",
    "signal_engine_filters_pass",
]
