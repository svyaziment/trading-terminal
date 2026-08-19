"""Pattern preview overlays for Strategy Lab chart (Issue #87/#88).

Computes candles + typed overlays on the backend so the frontend does not
re-implement pattern logic. First renderer: ``levels_reversal`` (rays + bands).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.analytics.levels_backtest import compute_atr
from app.analytics.levels_engine import build_levels
from app.analytics.pattern_registry import (
    apply_signal_pattern_defaults,
    get_pattern,
    get_pattern_defaults,
    is_signal_engine_pattern,
    resolve_signal_timeframe,
)
from app.db.db_manager import DBManager

# Approximate bar duration in hours for lookback before the visible window.
_TF_LOOKBACK_HOURS: Dict[str, float] = {
    "30min": 0.5,
    "1h": 1.0,
    "2h": 2.0,
    "4h": 4.0,
    "1d": 24.0,
    "1w": 168.0,
    "1M": 720.0,
}


def _ts_iso(value: Any) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.isoformat(sep=" ", timespec="seconds")


def _date_bounds(date_from: str, date_to: str) -> Tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(date_from).normalize()
    end = pd.Timestamp(date_to).normalize() + pd.Timedelta(hours=23, minutes=59, seconds=59)
    if end < start:
        raise ValueError("date_from must be on or before date_to")
    return start, end


def resolve_preview_timeframe(pattern_id: str, params: Dict[str, Any]) -> Optional[str]:
    """Return the candle timeframe used for preview of ``pattern_id``."""
    if pattern_id == "levels_reversal":
        return str(params.get("level_timeframe") or get_pattern_defaults(pattern_id).get("level_timeframe", "4h"))
    if pattern_id == "signal_4h_buy":
        return "4h"
    if is_signal_engine_pattern(pattern_id):
        merged = apply_signal_pattern_defaults(pattern_id, params)
        return resolve_signal_timeframe(merged.get("timeframe"))
    if pattern_id in ("rsi_oversold", "macd_bullish", "bb_lower"):
        return "1min"
    return None


def _lookback_start(timeframe: str, swing_window: int, visible_start: pd.Timestamp) -> pd.Timestamp:
    bar_hours = _TF_LOOKBACK_HOURS.get(timeframe, 4.0)
    bars = max(int(swing_window) * 2 + 20, 30)
    return visible_start - pd.Timedelta(hours=bar_hours * bars)


def load_candles(
    db: DBManager,
    ticker: str,
    timeframe: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    df = db.select(
        "SELECT ticker, figi, timestamp, timeframe, open, high, low, close, volume, "
        "created_at::text AS created_at "
        "FROM trading.candles_aggregated "
        "WHERE ticker=%s AND timeframe=%s AND timestamp >= %s AND timestamp <= %s "
        "ORDER BY timestamp",
        (ticker, timeframe, start.to_pydatetime(), end.to_pydatetime()),
    ).to_dataframe()
    if df.empty:
        return df
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def candles_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    records: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        records.append(
            {
                "ticker": row.get("ticker"),
                "figi": row.get("figi"),
                "timestamp": _ts_iso(row["timestamp"]),
                "timeframe": row.get("timeframe"),
                "open": None if pd.isna(row.get("open")) else float(row["open"]),
                "high": None if pd.isna(row.get("high")) else float(row["high"]),
                "low": None if pd.isna(row.get("low")) else float(row["low"]),
                "close": None if pd.isna(row.get("close")) else float(row["close"]),
                "volume": None if pd.isna(row.get("volume")) else float(row["volume"]),
                "created_at": row.get("created_at"),
            }
        )
    return records


def levels_to_overlays(levels_df: pd.DataFrame, window_end: pd.Timestamp) -> List[Dict[str, Any]]:
    """Convert levels rows to ray + band overlays ending at ``window_end``."""
    if levels_df.empty:
        return []
    to_ts = _ts_iso(window_end)
    overlays: List[Dict[str, Any]] = []
    for row in levels_df.itertuples(index=False):
        from_ts = _ts_iso(row.defined_ts)
        level_type = str(row.type)
        method = str(row.method)
        base = {
            "from_ts": from_ts,
            "to_ts": to_ts,
            "level_type": level_type,
            "method": method,
        }
        overlays.append(
            {
                "type": "ray",
                **base,
                "price": float(row.level_price),
            }
        )
        overlays.append(
            {
                "type": "band",
                **base,
                "lower": float(row.zone_lower),
                "upper": float(row.zone_upper),
            }
        )
    return overlays


def _merge_levels_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = get_pattern_defaults("levels_reversal")
    if isinstance(params, dict):
        merged.update(params)
    return merged


def preview_levels_reversal(
    db: DBManager,
    ticker: str,
    params: Dict[str, Any],
    date_from: str,
    date_to: str,
) -> Dict[str, Any]:
    lr = _merge_levels_params(params)
    timeframe = str(lr.get("level_timeframe", "4h"))
    visible_start, visible_end = _date_bounds(date_from, date_to)

    level_method = lr.get("level_method", ["swing", "impulse"])
    if isinstance(level_method, str):
        level_method = [level_method]
    swing_window = int(lr.get("swing_window", 10))
    body_ratio = float(lr.get("impulse_body_ratio", 0.7))
    impulse_atr_mult = float(lr.get("impulse_atr_mult", 1.5))
    zone_atr_mult = float(lr.get("zone_atr_mult", 0.5))
    include_swing = "swing" in level_method
    include_impulse = "impulse" in level_method

    load_start = _lookback_start(timeframe, swing_window, visible_start)
    df_all = load_candles(db, ticker, timeframe, load_start, visible_end)
    df_visible = df_all[(df_all["timestamp"] >= visible_start) & (df_all["timestamp"] <= visible_end)].copy()

    if df_visible.empty:
        note = (
            f"no {timeframe} candles for {ticker} in selected window"
            if timeframe != "2h"
            else f"timeframe {timeframe} is not persisted by the current pipeline"
        )
        return {
            "status": "empty",
            "ticker": ticker,
            "pattern_id": "levels_reversal",
            "timeframe": timeframe,
            "date_from": date_from,
            "date_to": date_to,
            "candles": [],
            "overlays": [],
            "error": note,
        }

    if df_all.empty:
        note = (
            f"no {timeframe} candles for {ticker}"
            if timeframe != "2h"
            else f"timeframe {timeframe} is not persisted by the current pipeline"
        )
        return {
            "status": "empty",
            "ticker": ticker,
            "pattern_id": "levels_reversal",
            "timeframe": timeframe,
            "date_from": date_from,
            "date_to": date_to,
            "candles": [],
            "overlays": [],
            "error": note,
        }

    df_all = df_all.copy()
    df_all["atr"] = compute_atr(df_all, 14)
    levels = build_levels(
        df_all,
        swing_windows=(swing_window,),
        body_ratio=body_ratio,
        impulse_atr_mult=impulse_atr_mult,
        zone_atr_mult=zone_atr_mult,
        include_swing=include_swing,
        include_impulse=include_impulse,
    )

    if levels.empty:
        window_end = df_visible["timestamp"].iloc[-1]
        return {
            "status": "ok",
            "ticker": ticker,
            "pattern_id": "levels_reversal",
            "timeframe": timeframe,
            "date_from": date_from,
            "date_to": date_to,
            "candles": candles_to_records(df_visible),
            "overlays": [],
            "meta": {"levels_total": 0, "levels_in_window": 0},
        }

    levels["defined_ts"] = pd.to_datetime(levels["defined_ts"])
    in_window = levels[
        (levels["defined_ts"] >= visible_start) & (levels["defined_ts"] <= visible_end)
    ].copy()

    window_end = df_visible["timestamp"].iloc[-1]
    overlays = levels_to_overlays(in_window, window_end)

    return {
        "status": "ok",
        "ticker": ticker,
        "pattern_id": "levels_reversal",
        "timeframe": timeframe,
        "date_from": date_from,
        "date_to": date_to,
        "candles": candles_to_records(df_visible),
        "overlays": overlays,
        "meta": {
            "levels_total": int(len(levels)),
            "levels_in_window": int(len(in_window)),
        },
    }


def preview_pattern(
    db: DBManager,
    ticker: str,
    pattern_id: str,
    params: Optional[Dict[str, Any]],
    date_from: str,
    date_to: str,
) -> Dict[str, Any]:
    """Build preview payload for ``pattern_id`` (Issue #88: levels only)."""
    if get_pattern(pattern_id) is None:
        return {
            "status": "error",
            "ticker": ticker,
            "pattern_id": pattern_id,
            "timeframe": None,
            "date_from": date_from,
            "date_to": date_to,
            "candles": [],
            "overlays": [],
            "error": f"unknown pattern_id: {pattern_id}",
        }

    try:
        _date_bounds(date_from, date_to)
    except ValueError as exc:
        return {
            "status": "error",
            "ticker": ticker,
            "pattern_id": pattern_id,
            "timeframe": None,
            "date_from": date_from,
            "date_to": date_to,
            "candles": [],
            "overlays": [],
            "error": str(exc),
        }

    merged_params = params if isinstance(params, dict) else {}
    if pattern_id == "levels_reversal":
        return preview_levels_reversal(db, ticker, merged_params, date_from, date_to)

    timeframe = resolve_preview_timeframe(pattern_id, merged_params)
    if timeframe is None:
        return {
            "status": "error",
            "ticker": ticker,
            "pattern_id": pattern_id,
            "timeframe": None,
            "date_from": date_from,
            "date_to": date_to,
            "candles": [],
            "overlays": [],
            "error": f"preview timeframe could not be resolved for {pattern_id}",
        }

    visible_start, visible_end = _date_bounds(date_from, date_to)
    df_visible = load_candles(db, ticker, timeframe, visible_start, visible_end)
    if df_visible.empty:
        note = (
            f"no {timeframe} candles for {ticker} in selected window"
            if timeframe != "2h"
            else f"timeframe {timeframe} is not persisted by the current pipeline"
        )
        return {
            "status": "empty",
            "ticker": ticker,
            "pattern_id": pattern_id,
            "timeframe": timeframe,
            "date_from": date_from,
            "date_to": date_to,
            "candles": [],
            "overlays": [],
            "error": note,
        }

    return {
        "status": "unsupported",
        "ticker": ticker,
        "pattern_id": pattern_id,
        "timeframe": timeframe,
        "date_from": date_from,
        "date_to": date_to,
        "candles": candles_to_records(df_visible),
        "overlays": [],
        "error": f"preview overlays for {pattern_id} are not implemented yet",
    }
