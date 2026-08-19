"""Issue #88: POST /api/patterns/preview + levels_reversal overlays."""
from __future__ import annotations

import pandas as pd

from app.analytics.levels_engine import build_levels
from app.analytics.levels_backtest import compute_atr
from app.analytics.pattern_preview import (
    candles_to_records,
    levels_to_overlays,
    preview_levels_reversal,
    preview_pattern,
    resolve_preview_timeframe,
)


def _candles_df(ticker: str = "SBER", timeframe: str = "4h") -> pd.DataFrame:
    """Synthetic 4h series long enough for swing_window=2."""
    rows = []
    base = pd.Timestamp("2024-01-01 08:00:00")
    prices = [100, 102, 105, 103, 101, 99, 98, 100, 103, 106, 104, 102]
    for i, close in enumerate(prices):
        ts = base + pd.Timedelta(hours=4 * i)
        rows.append(
            {
                "ticker": ticker,
                "figi": "FIGI",
                "timestamp": ts,
                "timeframe": timeframe,
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": float(close),
                "volume": 1000.0,
                "created_at": "2024-01-01",
            }
        )
    return pd.DataFrame(rows)


class FakeDB:
    def __init__(self, candles: pd.DataFrame):
        self._candles = candles

    def select(self, query, params=None):
        df = self._candles.copy()
        if params:
            ticker, timeframe, start, end = params
            df = df[
                (df["ticker"] == ticker)
                & (df["timeframe"] == timeframe)
                & (df["timestamp"] >= pd.Timestamp(start))
                & (df["timestamp"] <= pd.Timestamp(end))
            ]
        return type("R", (), {"to_dataframe": lambda self: df})()


def test_resolve_preview_timeframe_levels():
    tf = resolve_preview_timeframe("levels_reversal", {"level_timeframe": "1d"})
    assert tf == "1d"


def test_levels_to_overlays_ray_and_band():
    levels = pd.DataFrame(
        [
            {
                "defined_ts": pd.Timestamp("2024-01-02 08:00:00"),
                "available_from_ts": pd.Timestamp("2024-01-02 16:00:00"),
                "level_price": 99.0,
                "type": "support",
                "method": "swing",
                "atr": 1.0,
                "zone_lower": 98.5,
                "zone_upper": 99.5,
            },
            {
                "defined_ts": pd.Timestamp("2024-01-03 08:00:00"),
                "available_from_ts": pd.Timestamp("2024-01-03 16:00:00"),
                "level_price": 110.0,
                "type": "resistance",
                "method": "impulse",
                "atr": 1.0,
                "zone_lower": 109.0,
                "zone_upper": 111.0,
            },
        ]
    )
    window_end = pd.Timestamp("2024-01-05 08:00:00")
    overlays = levels_to_overlays(levels, window_end)

    assert len(overlays) == 4
    rays = [o for o in overlays if o["type"] == "ray"]
    bands = [o for o in overlays if o["type"] == "band"]
    assert len(rays) == 2
    assert len(bands) == 2

    support_ray = next(o for o in rays if o["level_type"] == "support")
    assert support_ray["from_ts"].startswith("2024-01-02")
    assert support_ray["to_ts"].startswith("2024-01-05")
    assert support_ray["price"] == 99.0
    assert "from_ts" in support_ray

    support_band = next(o for o in bands if o["level_type"] == "support")
    assert support_band["lower"] == 98.5
    assert support_band["upper"] == 99.5


def test_preview_levels_returns_all_levels_in_window_not_nearest():
    candles = _candles_df()
    db = FakeDB(candles)
    params = {
        "level_timeframe": "4h",
        "level_method": ["swing"],
        "swing_window": 2,
        "zone_atr_mult": 0.5,
    }

    result = preview_levels_reversal(
        db,
        ticker="SBER",
        params=params,
        date_from="2024-01-01",
        date_to="2024-01-03",
    )

    assert result["status"] == "ok"
    assert result["pattern_id"] == "levels_reversal"
    assert len(result["candles"]) > 0
    rays = [o for o in result["overlays"] if o["type"] == "ray"]
    assert len(rays) >= 2, "expected multiple levels in window, not a single nearest"
    assert result["meta"]["levels_in_window"] == len(rays)


def test_preview_unknown_pattern_returns_error():
    db = FakeDB(_candles_df())
    result = preview_pattern(
        db,
        ticker="SBER",
        pattern_id="not_a_pattern",
        params={},
        date_from="2024-01-01",
        date_to="2024-01-03",
    )
    assert result["status"] == "error"
    assert "unknown pattern_id" in result["error"]
    assert result["overlays"] == []


def test_preview_empty_candles_returns_empty_status():
    db = FakeDB(pd.DataFrame(columns=_candles_df().columns))
    result = preview_levels_reversal(
        db,
        ticker="SBER",
        params={"level_timeframe": "2h"},
        date_from="2024-01-01",
        date_to="2024-01-03",
    )
    assert result["status"] == "empty"
    assert result["timeframe"] == "2h"
    assert "2h" in result["error"]
    assert result["candles"] == []
    assert result["overlays"] == []


def test_preview_unsupported_pattern_returns_candles_without_overlays():
    db = FakeDB(_candles_df())
    result = preview_pattern(
        db,
        ticker="SBER",
        pattern_id="PA_Hammer",
        params={"timeframe": "4h"},
        date_from="2024-01-01",
        date_to="2024-01-03",
    )
    assert result["status"] == "unsupported"
    assert len(result["candles"]) > 0
    assert result["overlays"] == []


def test_candles_to_records_iso_timestamps():
    df = _candles_df().head(2)
    records = candles_to_records(df)
    assert len(records) == 2
    assert "T" not in records[0]["timestamp"] or records[0]["timestamp"].count(":") >= 2


def test_build_levels_produces_multiple_defined_ts_in_window():
    df = _candles_df()
    df["atr"] = compute_atr(df, 14)
    levels = build_levels(df, swing_windows=(2,), include_impulse=False)
    assert not levels.empty
    visible_start = pd.Timestamp("2024-01-01")
    visible_end = pd.Timestamp("2024-01-03 23:59:59")
    in_window = levels[
        (pd.to_datetime(levels["defined_ts"]) >= visible_start)
        & (pd.to_datetime(levels["defined_ts"]) <= visible_end)
    ]
    assert len(in_window) >= 2
