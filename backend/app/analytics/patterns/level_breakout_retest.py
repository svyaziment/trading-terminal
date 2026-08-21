"""Level breakout + retest (role reversal) for Strategy Lab (Issue #107).

Not a SignalEngine ``BasePattern``: it does not evaluate ``trading.indicators``.
Lives next to ``breakout.py`` (BO_BB_Squeeze) rather than under ``breakout/``
because that path would shadow the existing module.

Entry: confirmed resistance break (LevelsTracker) → price returns to the
broken level → support holds → bullish trigger. Stop/take are ATR × RR.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Union

import pandas as pd

from app.analytics.levels_engine import LevelState, LevelsTracker
from app.analytics.pattern_registry import get_pattern_defaults
from app.analytics.trading_config import get_level_breakout_retest_config

PATTERN_ID = "level_breakout_retest"

_RETEST_STATES = frozenset({
    LevelState.BROKEN_UP.value,
    LevelState.FLIPPED_SUPPORT.value,
})

# Issue #108: stable rejection codes for validation (pie chart / analytics).
REJECTION_NO_BREAKOUT = "no_breakout"
REJECTION_BREAKOUT_NOT_CONFIRMED = "breakout_not_confirmed"
REJECTION_NO_RETEST = "no_retest"
REJECTION_WINDOW_EXPIRED = "retest_window_expired"
REJECTION_SUPPORT_BREAKS = "support_breaks"
REJECTION_NO_TRIGGER = "no_entry_trigger"
REJECTION_ACCEPTED = "accepted"

_rejection_log: Optional[List[str]] = None


def enable_rejection_log(bucket: List[str]) -> None:
    """Record classify codes (or ``accepted``) on every ``check_breakout_retest`` call."""
    global _rejection_log
    _rejection_log = bucket


def disable_rejection_log() -> None:
    global _rejection_log
    _rejection_log = None

_BarLike = Union[Mapping[str, Any], pd.Series]


def resolve_params(raw: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Fill Lab defaults from pattern_registry; clamp to schema bounds."""
    out = get_pattern_defaults(PATTERN_ID)
    if raw:
        out.update(dict(raw))
    tf = str(out.get("level_timeframe") or "4h")
    if tf not in ("1h", "4h", "1d"):
        tf = "4h"
    out["level_timeframe"] = tf
    out["retest_window_bars"] = max(1, min(100, int(out.get("retest_window_bars", 20))))
    out["retest_zone_atr"] = max(0.1, min(2.0, float(out.get("retest_zone_atr", 0.5))))
    out["entry_trigger_bullish"] = bool(out.get("entry_trigger_bullish", True))
    out["stop_atr"] = max(0.5, min(3.0, float(out.get("stop_atr", 1.0))))
    out["risk_reward"] = max(1.0, min(5.0, float(out.get("risk_reward", 2.0))))
    return out


def _ohlc(bar: _BarLike) -> Dict[str, float]:
    if isinstance(bar, pd.Series):
        return {
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
        }
    return {
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
    }


def _bullish_body(ohlc: Mapping[str, float], body_ratio: float) -> bool:
    rng = ohlc["high"] - ohlc["low"]
    if rng <= 0:
        return False
    body = abs(ohlc["close"] - ohlc["open"])
    return ohlc["close"] > ohlc["open"] and (body / rng) > body_ratio


def compute_stop_take(
    entry_price: float,
    atr: float,
    stop_atr: float,
    risk_reward: float,
) -> Optional[Dict[str, float]]:
    """ATR stop below entry, take at the configured reward:risk."""
    if atr <= 0 or entry_price <= 0 or stop_atr <= 0 or risk_reward <= 0:
        return None
    stop = entry_price - stop_atr * atr
    if stop >= entry_price:
        return None
    take = entry_price + risk_reward * (entry_price - stop)
    return {"stop": float(stop), "take": float(take)}


def classify_retest_rejection(
    bar: _BarLike,
    tracker: LevelsTracker,
    *,
    atr: float,
    params: Optional[Mapping[str, Any]] = None,
    prev_high: Optional[float] = None,
) -> Optional[str]:
    """Return a stable Issue #108 rejection code, or None if the pattern would fire.

    Category meanings (most specific geometry first, then trigger):
      no_breakout — no resistance in broken_up / flipped_support
      breakout_not_confirmed — close above an active resistance zone, not confirmed
      retest_window_expired — broken resistance exists, but bars_since_breakout > window
      support_breaks — in window, close below the broken level_price
      no_retest — support holds, close not in [level ± retest_zone_atr×ATR]
      no_entry_trigger — retest geometry holds, but no bullish body / prev-high break
    """
    cfg = resolve_params(params)
    ohlc = _ohlc(bar)
    close = ohlc["close"]
    if atr is None or atr <= 0:
        return REJECTION_NO_BREAKOUT

    snapshot = tracker.get_levels_with_state()
    if snapshot is None or snapshot.empty:
        return REJECTION_NO_BREAKOUT

    zone = float(cfg["retest_zone_atr"]) * float(atr)
    window = int(cfg["retest_window_bars"])
    has_active_above = False
    has_retest_state = False
    has_in_window = False
    has_support_hold = False
    has_in_zone = False

    for i in range(len(snapshot)):
        row = snapshot.iloc[i]
        if str(row.get("type", "")) != "resistance":
            continue
        state = str(row.get("state", ""))
        zone_upper = float(row["zone_upper"])
        if state == LevelState.ACTIVE.value and close > zone_upper:
            has_active_above = True
        if state not in _RETEST_STATES:
            continue
        lid = row.get("level_id")
        if lid is None:
            continue
        has_retest_state = True
        try:
            since = tracker.bars_since_breakout(lid)
        except KeyError:
            continue
        if since is None or since > window:
            continue
        has_in_window = True
        level_price = float(row["level_price"])
        if close < level_price:
            continue
        has_support_hold = True
        lo = level_price - zone
        hi = level_price + zone
        if lo <= close <= hi:
            has_in_zone = True

    if not has_in_zone:
        if has_support_hold:
            return REJECTION_NO_RETEST
        if has_in_window:
            return REJECTION_SUPPORT_BREAKS
        if has_retest_state:
            return REJECTION_WINDOW_EXPIRED
        if has_active_above:
            return REJECTION_BREAKOUT_NOT_CONFIRMED
        return REJECTION_NO_BREAKOUT

    if cfg["entry_trigger_bullish"]:
        trigger_cfg = get_level_breakout_retest_config()
        body_ratio = float(trigger_cfg["bullish_body_ratio"])
        broke_prev = prev_high is not None and close > float(prev_high)
        if not (broke_prev or _bullish_body(ohlc, body_ratio)):
            return REJECTION_NO_TRIGGER

    st = compute_stop_take(close, float(atr), cfg["stop_atr"], cfg["risk_reward"])
    if st is None:
        return REJECTION_NO_RETEST
    return None


def _decide_breakout_retest(
    bar: _BarLike,
    tracker: LevelsTracker,
    *,
    atr: float,
    params: Optional[Mapping[str, Any]] = None,
    prev_high: Optional[float] = None,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Issue #107 accept/reject body. Keep accept path bit-for-bit with the original filter.

    Returns (hit, rejection_code). rejection_code is None when hit is not None.
    """
    cfg = resolve_params(params)
    ohlc = _ohlc(bar)
    close = ohlc["close"]
    if atr is None or atr <= 0:
        return None, REJECTION_NO_BREAKOUT

    snapshot = tracker.get_levels_with_state()
    if snapshot is None or snapshot.empty:
        return None, REJECTION_NO_BREAKOUT

    zone = float(cfg["retest_zone_atr"]) * float(atr)
    window = int(cfg["retest_window_bars"])
    best: Optional[Dict[str, Any]] = None
    best_dist: Optional[float] = None
    has_active_above = False
    has_retest_state = False
    has_in_window = False
    has_support_hold = False

    for i in range(len(snapshot)):
        row = snapshot.iloc[i]
        if str(row.get("type", "")) != "resistance":
            continue
        state = str(row.get("state", ""))
        if state == LevelState.ACTIVE.value and close > float(row["zone_upper"]):
            has_active_above = True
        if state not in _RETEST_STATES:
            continue
        lid = row.get("level_id")
        if lid is None:
            continue
        has_retest_state = True
        try:
            since = tracker.bars_since_breakout(lid)
        except KeyError:
            continue
        if since is None or since > window:
            continue
        has_in_window = True
        level_price = float(row["level_price"])
        if close < level_price:
            continue
        has_support_hold = True
        lo = level_price - zone
        hi = level_price + zone
        if not (lo <= close <= hi):
            continue
        dist = abs(close - level_price)
        if best_dist is not None and dist >= best_dist:
            continue
        best_dist = dist
        best = {
            "level_id": lid,
            "level_price": level_price,
            "zone_lower": float(row["zone_lower"]),
            "zone_upper": float(row["zone_upper"]),
            "state": str(row["state"]),
            "bars_since_breakout": int(since),
        }

    if best is None:
        if has_support_hold:
            return None, REJECTION_NO_RETEST
        if has_in_window:
            return None, REJECTION_SUPPORT_BREAKS
        if has_retest_state:
            return None, REJECTION_WINDOW_EXPIRED
        if has_active_above:
            return None, REJECTION_BREAKOUT_NOT_CONFIRMED
        return None, REJECTION_NO_BREAKOUT

    if cfg["entry_trigger_bullish"]:
        trigger_cfg = get_level_breakout_retest_config()
        body_ratio = float(trigger_cfg["bullish_body_ratio"])
        broke_prev = prev_high is not None and close > float(prev_high)
        if not (broke_prev or _bullish_body(ohlc, body_ratio)):
            return None, REJECTION_NO_TRIGGER

    st = compute_stop_take(close, float(atr), cfg["stop_atr"], cfg["risk_reward"])
    if st is None:
        return None, REJECTION_NO_RETEST
    return (
        {
            "action": "enter",
            "entry_price": close,
            "stop": st["stop"],
            "take": st["take"],
            **best,
        },
        None,
    )


def check_breakout_retest(
    bar: _BarLike,
    tracker: LevelsTracker,
    *,
    atr: float,
    params: Optional[Mapping[str, Any]] = None,
    prev_high: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Return an entry dict if a broken resistance is being retested, else None.

    Criteria (Issue #107):
      1. close in [level − retest_zone_atr×ATR, level + retest_zone_atr×ATR]
      2. support holds: close >= broken level_price
      3. trigger: close > prev_high OR bullish body (when entry_trigger_bullish)
      4. bars_since_breakout <= retest_window_bars (HTF bars)

    Issue #108: optional rejection log does not change the accept/reject result.
    """
    hit, reason = _decide_breakout_retest(
        bar, tracker, atr=atr, params=params, prev_high=prev_high
    )
    if _rejection_log is not None:
        _rejection_log.append(reason or REJECTION_ACCEPTED)
    return hit


def evaluate_level_breakout_retest(
    bar: _BarLike,
    tracker: LevelsTracker,
    *,
    atr: float,
    params: Optional[Mapping[str, Any]] = None,
    prev_high: Optional[float] = None,
    timestamp: Optional[pd.Timestamp] = None,
):
    """Plugin-facing wrapper: same criteria, returns ``EntrySignal``."""
    from app.analytics.strategies.base import EntrySignal

    hit = check_breakout_retest(
        bar, tracker, atr=atr, params=params, prev_high=prev_high
    )
    if hit is None:
        return None
    ts = timestamp
    if ts is None:
        raw = bar["timestamp"] if isinstance(bar, pd.Series) else bar.get("timestamp")
        ts = pd.Timestamp(raw) if raw is not None else pd.Timestamp.utcnow()
    return EntrySignal(
        entry_price=hit["entry_price"],
        stop=hit["stop"],
        take=hit["take"],
        timestamp=pd.Timestamp(ts),
        confidence=1.0,
        metadata={
            "source": PATTERN_ID,
            "level_id": hit["level_id"],
            "level_price": hit["level_price"],
            "state": hit["state"],
            "bars_since_breakout": hit["bars_since_breakout"],
        },
    )
