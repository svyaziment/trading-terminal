"""Issue #81: E2E save → normalize → backtest/paper evaluator for SignalEngine Lab filters."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from app.analytics.paper_strategy import get_active_paper_strategy
from app.analytics.pattern_registry import (
    SIGNAL_ENGINE_PATTERN_IDS,
    normalize_patterns,
)
from app.analytics.signal_pattern_filters import (
    build_signal_filter_series,
    evaluate_buy_timestamps,
)
from app.analytics.strategy_backtest import ALL_PATTERNS, run_strategy_backtest
from app.analytics.strategy_engine import StrategyEvaluator

# One example id from the issue; the full set lives in the registry.
SAMPLE_SIGNAL_ID = "PA_Engulfing"


def _levels_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "available_from_ts": pd.Timestamp("2024-01-01"),
                "type": "support",
                "level_price": 99.0,
                "zone_lower": 99.0,
                "zone_upper": 101.0,
                "method": "swing",
            },
            {
                "available_from_ts": pd.Timestamp("2024-01-01"),
                "type": "resistance",
                "level_price": 110.0,
                "zone_lower": 109.0,
                "zone_upper": 111.0,
                "method": "swing",
            },
        ]
    )


def _passing_levels_context():
    ts_4h = [pd.Timestamp("2024-01-02 08:00:00")]
    confirm_ts = [pd.Timestamp("2024-01-02 09:50:00")]
    return {
        "levels": _levels_df(),
        "ts_4h": ts_4h,
        "atr_by_ts": {ts_4h[0]: 2.0},
        "buy_ts": [],
        "confirm_series": [(confirm_ts, [102.0])],
    }


def _entry_row() -> pd.Series:
    return pd.Series(
        {
            "timestamp": pd.Timestamp("2024-01-02 10:00:00"),
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
        }
    )


def _engulfing_4h_indicators(ticker: str = "SBER") -> pd.DataFrame:
    """00:00 bearish + 04:00 bullish engulfing BUY; 08:00 is not an engulfing."""
    return pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2024-01-02 00:00:00"),
                "open": 102.0,
                "high": 102.5,
                "low": 99.5,
                "close": 100.0,
                "ticker": ticker,
            },
            {
                "timestamp": pd.Timestamp("2024-01-02 04:00:00"),
                "open": 99.5,
                "high": 103.0,
                "low": 99.0,
                "close": 102.5,
                "ticker": ticker,
            },
            {
                "timestamp": pd.Timestamp("2024-01-02 08:00:00"),
                "open": 102.5,
                "high": 103.0,
                "low": 102.0,
                "close": 102.6,
                "ticker": ticker,
            },
        ]
    )


class RoutingFakeDB:
    """DB stub that returns frames by table name mentioned in SQL."""

    def __init__(self, **frames):
        self.frames = frames
        self.select_calls = []

    def select(self, query, params=None):
        self.select_calls.append((query, params))
        q = " ".join(str(query).lower().split())
        if "from trading.candles_1min_raw" in q:
            key = "candles_1min_raw"
        elif "from trading.candles_aggregated" in q:
            key = "candles_aggregated"
        elif "from trading.indicators" in q:
            key = "indicators"
        elif "from trading.signals" in q:
            key = "signals"
        elif "from trading.strategies" in q:
            key = "strategies"
        else:
            key = None
        frame = self.frames.get(key, pd.DataFrame())
        return SimpleNamespace(to_dataframe=lambda f=frame: f.copy())


def _load(ev: StrategyEvaluator, ctx: dict, series=None) -> None:
    ev.load_context(
        ctx["levels"],
        ctx["ts_4h"],
        ctx["atr_by_ts"],
        ctx["buy_ts"],
        ctx["confirm_series"],
        series,
    )


def test_sample_id_comes_from_registry_not_a_local_list():
    assert SAMPLE_SIGNAL_ID in SIGNAL_ENGINE_PATTERN_IDS
    assert SAMPLE_SIGNAL_ID in ALL_PATTERNS
    for pattern_id in SIGNAL_ENGINE_PATTERN_IDS:
        assert pattern_id in ALL_PATTERNS


def test_levels_plus_engulfing_and_filter_on_off():
    assert SAMPLE_SIGNAL_ID in SIGNAL_ENGINE_PATTERN_IDS
    closed_4h = pd.Timestamp("2024-01-02 04:00:00")
    indicators = _engulfing_4h_indicators()
    buy_ts = evaluate_buy_timestamps(indicators, SAMPLE_SIGNAL_ID, "4h", "SBER")
    assert buy_ts == [closed_4h]

    times = [pd.Timestamp(ts) for ts in indicators["timestamp"].tolist()]
    ctx = _passing_levels_context()
    row = _entry_row()

    off = StrategyEvaluator(
        normalize_patterns(
            {"patterns": ["levels_reversal"], "confirm_windows": [10]}
        )
    )
    _load(off, ctx)
    baseline = off.check_entry(row)
    assert baseline is not None and baseline["action"] == "enter"

    filtered = normalize_patterns(
        {
            "patterns": {
                "levels_reversal": {},
                SAMPLE_SIGNAL_ID: {"timeframe": "4h"},
            },
            "confirm_windows": [10],
        }
    )
    on_hit = StrategyEvaluator(filtered)
    _load(
        on_hit,
        ctx,
        [
            {
                "pattern_id": SAMPLE_SIGNAL_ID,
                "timeframe": "4h",
                "times": times,
                "buy_ts": buy_ts,
            }
        ],
    )
    hit = on_hit.check_entry(row)
    assert hit is not None
    assert hit["entry_price"] == baseline["entry_price"]
    assert hit["stop"] == baseline["stop"]
    assert hit["take"] == baseline["take"]

    on_miss = StrategyEvaluator(filtered)
    _load(
        on_miss,
        ctx,
        [
            {
                "pattern_id": SAMPLE_SIGNAL_ID,
                "timeframe": "4h",
                "times": times,
                "buy_ts": [],
            }
        ],
    )
    assert on_miss.check_entry(row) is None


def test_and_filter_uses_selected_timeframe():
    ctx = _passing_levels_context()
    row = _entry_row()
    cfg_30 = normalize_patterns(
        {
            "patterns": {
                "levels_reversal": {},
                SAMPLE_SIGNAL_ID: {"timeframe": "30min"},
            }
        }
    )
    ev = StrategyEvaluator(cfg_30)
    _load(
        ev,
        ctx,
        [
            {
                "pattern_id": SAMPLE_SIGNAL_ID,
                "timeframe": "30min",
                "times": [
                    pd.Timestamp("2024-01-02 09:00:00"),
                    pd.Timestamp("2024-01-02 09:30:00"),
                    pd.Timestamp("2024-01-02 10:00:00"),
                ],
                "buy_ts": [pd.Timestamp("2024-01-02 09:30:00")],
            }
        ],
    )
    assert ev.check_entry(row) is not None
    assert ev.signal_filter_specs == [
        {"pattern_id": SAMPLE_SIGNAL_ID, "timeframe": "30min"}
    ]


def test_build_series_from_indicators_feeds_evaluator():
    indicators = _engulfing_4h_indicators()
    db = RoutingFakeDB(indicators=indicators)
    cfg = normalize_patterns(
        {
            "patterns": {
                "levels_reversal": {},
                SAMPLE_SIGNAL_ID: {"timeframe": "4h"},
            }
        }
    )
    series = build_signal_filter_series(db, "SBER", cfg["patterns"])
    assert len(series) == 1
    assert series[0]["pattern_id"] == SAMPLE_SIGNAL_ID
    assert series[0]["timeframe"] == "4h"
    assert pd.Timestamp("2024-01-02 04:00:00") in series[0]["buy_ts"]

    ev = StrategyEvaluator(cfg)
    _load(ev, _passing_levels_context(), series)
    assert ev.check_entry(_entry_row()) is not None
    assert not any("trading.signals" in q.lower() for q, _ in db.select_calls)


def test_strategy_backtest_quick_accepts_new_pattern_id():
    ticker = "SBER"
    df_1m = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2024-01-02 10:00:00"),
                "open": 100.0,
                "high": 100.2,
                "low": 99.8,
                "close": 100.0,
            }
        ]
    )
    df_4h = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2024-01-02 00:00:00"),
                "open": 98.0,
                "high": 101.0,
                "low": 97.0,
                "close": 100.0,
            },
            {
                "timestamp": pd.Timestamp("2024-01-02 04:00:00"),
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
            },
            {
                "timestamp": pd.Timestamp("2024-01-02 08:00:00"),
                "open": 101.0,
                "high": 103.0,
                "low": 100.0,
                "close": 102.0,
            },
        ]
    )
    db = RoutingFakeDB(
        candles_1min_raw=df_1m,
        candles_aggregated=df_4h,
        indicators=_engulfing_4h_indicators(ticker),
        signals=pd.DataFrame(),
    )
    cfg = {
        "patterns": {
            "levels_reversal": {},
            SAMPLE_SIGNAL_ID: {"timeframe": "4h"},
        },
        "confirm_windows": [10],
        "n_runs": 1,
    }
    result = run_strategy_backtest(db, ticker, cfg)
    assert result["status"] in {"success", "failed"}
    if result["status"] == "failed":
        assert "error" in result
    else:
        assert "metrics" in result
        assert isinstance(result.get("trades"), list)
    assert not any("trading.signals" in q.lower() for q, _ in db.select_calls)
    assert any("trading.indicators" in q.lower() for q, _ in db.select_calls)


def test_paper_path_accepts_new_keys_without_touching_locked_strategy():
    cfg = {
        "patterns": {
            "levels_reversal": {},
            SAMPLE_SIGNAL_ID: {"timeframe": "4h"},
        },
        "confirm_windows": [10],
        "commission_pct": 0.06,
    }
    db = RoutingFakeDB(
        strategies=pd.DataFrame(
            [{"id": 99, "name": "lab_engulfing_probe", "config": cfg}]
        )
    )
    active = get_active_paper_strategy(db)
    assert active["name"] == "lab_engulfing_probe"
    assert active["name"] != "test_20260731"
    assert SAMPLE_SIGNAL_ID in active["config"]["patterns"]
    assert active["config"]["patterns"][SAMPLE_SIGNAL_ID]["timeframe"] == "4h"

    ev = StrategyEvaluator(active["config"])
    assert ev.use_levels
    assert not ev.use_4h_buy
    assert ev.signal_filter_specs == [
        {"pattern_id": SAMPLE_SIGNAL_ID, "timeframe": "4h"}
    ]
    _load(ev, _passing_levels_context(), [])
    # Missing HTF series rejects rather than 500.
    assert ev.check_entry(_entry_row()) is None


def test_existing_lab_strategy_normalizes_without_signal_engine_ids():
    cfg = normalize_patterns(
        {
            "patterns": ["levels_reversal", "signal_4h_buy"],
            "confirm_windows": [10],
        }
    )
    assert set(cfg["patterns"]) == {"levels_reversal", "signal_4h_buy"}
    assert not set(cfg["patterns"]) & set(SIGNAL_ENGINE_PATTERN_IDS)
    ev = StrategyEvaluator(cfg)
    assert ev.signal_filter_specs == []
    assert ev.use_4h_buy
