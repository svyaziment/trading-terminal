"""Unified strategy context builder (single source of truth).

Builds the full context needed by StrategyEvaluator from a normalized config.
Used by backtest (strategy_backtest), live engine (live_engine) and online
signals (online_signals) so pattern parameters are never hardcoded.
"""
from __future__ import annotations

import pandas as pd
from typing import Any, Dict, List, Optional

from app.db.db_manager import DBManager
from app.analytics.levels_engine import build_levels
from app.analytics.levels_backtest import compute_atr, aggregate_1min_to, build_confirm_series


def load_4h_buy_ts(db: DBManager, ticker: str, min_signals: int = 1) -> List:
    """Sorted timestamps of 4h BUY signals (total_signals >= min_signals)."""
    df = db.select(
        "SELECT timestamp FROM trading.signals "
        "WHERE ticker=%s AND timeframe='4h' AND signal='BUY' AND coalesce(total_signals,0) >= %s "
        "ORDER BY timestamp",
        (ticker, min_signals),
    ).to_dataframe()

    if df.empty:
        return []

    return sorted(pd.to_datetime(df["timestamp"]).tolist())


def build_strategy_context(
    db: DBManager,
    ticker: str,
    config: Dict[str, Any],
    df_1m: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Build levels / ATR / BUY signals / confirmation series from pattern parameters.

    Reads parameters from the normalized config (pattern_registry format).
    Defaults reproduce the current hardcoded behaviour exactly:
      level_timeframe=4h, level_method=[swing,impulse], swing_window=10,
      impulse_body_ratio=0.7, impulse_atr_mult=1.5, zone_atr_mult=0.5,
      confirm_windows=[10].

    Args:
        db: Database manager.
        ticker: Ticker symbol.
        config: Strategy config (old or new format; will be normalized).
        df_1m: Optional 1min DataFrame (for confirmation series in backtest).

    Returns:
        Dict with keys: status, levels, ts_htf, atr_by_ts, buy_ts,
        confirm_series, confirm_windows, config (normalized).
    """
    from app.analytics.pattern_registry import normalize_patterns

    cfg = normalize_patterns(config)
    patterns = cfg.get("patterns", {})

    lr_params = patterns.get("levels_reversal", {})

    level_timeframe = lr_params.get("level_timeframe", "4h")
    level_method = lr_params.get("level_method", ["swing", "impulse"])
    swing_window = int(lr_params.get("swing_window", 10))
    body_ratio = float(lr_params.get("impulse_body_ratio", 0.7))
    impulse_atr_mult = float(lr_params.get("impulse_atr_mult", 1.5))
    zone_atr_mult = float(lr_params.get("zone_atr_mult", 0.5))

    if isinstance(level_method, str):
        level_method = [level_method]

    confirm_windows = lr_params.get("confirm_windows", cfg.get("confirm_windows", [10]))
    if isinstance(confirm_windows, int):
        confirm_windows = [confirm_windows]
    if not isinstance(confirm_windows, list):
        confirm_windows = [10]

    include_swing = "swing" in level_method
    include_impulse = "impulse" in level_method

    # Higher-TF candles for levels
    df_htf = db.select(
        "SELECT timestamp, open, high, low, close FROM trading.candles_aggregated "
        "WHERE ticker=%s AND timeframe=%s ORDER BY timestamp",
        (ticker, level_timeframe),
    ).to_dataframe()

    if df_htf.empty:
        return {"status": "failed", "error": f"no {level_timeframe} candles for {ticker}"}

    for c in ["open", "high", "low", "close"]:
        df_htf[c] = pd.to_numeric(df_htf[c], errors="coerce")

    df_htf["atr"] = compute_atr(df_htf, 14)

    levels = build_levels(
        df_htf,
        swing_windows=(swing_window,),
        body_ratio=body_ratio,
        impulse_atr_mult=impulse_atr_mult,
        zone_atr_mult=zone_atr_mult,
        include_swing=include_swing,
        include_impulse=include_impulse,
    )

    ts_htf = df_htf["timestamp"].tolist()
    atr_by_ts = dict(zip(df_htf["timestamp"], df_htf["atr"]))

    # 4h BUY signals
    use_4h_buy = "signal_4h_buy" in patterns
    buy_ts = load_4h_buy_ts(db, ticker) if use_4h_buy else []

    # Confirmation series (from 1min data, if provided)
    confirm_series = []
    if df_1m is not None and not df_1m.empty:
        for w in confirm_windows:
            cs = build_confirm_series(aggregate_1min_to(df_1m, w))
            confirm_series.append(([c[0] for c in cs], [c[1] for c in cs]))

    return {
        "status": "ok",
        "levels": levels,
        "ts_htf": ts_htf,
        "atr_by_ts": atr_by_ts,
        "buy_ts": buy_ts,
        "confirm_series": confirm_series,
        "confirm_windows": confirm_windows,
        "config": cfg,
    }
