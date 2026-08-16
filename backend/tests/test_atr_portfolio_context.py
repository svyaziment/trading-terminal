"""Coverage for ATR inputs supplied by the strategy-agnostic backtest."""

from __future__ import annotations

import pandas as pd

from app.analytics.portfolio_backtest import _backtest_ticker_plugin
from app.analytics.strategies.atr_reversal import AtrReversalStrategy
from app.analytics.strategies.context import MarketContext


class _Result:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def to_dataframe(self) -> pd.DataFrame:
        return self._frame.copy()


class _DB:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def select(self, _query, _params):
        return _Result(self._frame)


class _ContextProbe:
    def __init__(self):
        self.contexts = []

    def check_entry(self, context):
        self.contexts.append(context)
        return None

    def get_name(self):
        return "probe"


def _candles(count: int = 25) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=count, freq="min"),
        "open": [100.0] * count,
        "high": [100.2] * count,
        "low": [99.8] * count,
        "close": [100.0] * count,
        "volume": list(range(100, 100 + count)),
    })


def test_portfolio_backtest_populates_atr_and_volume_context(monkeypatch):
    monkeypatch.setattr(
        "app.analytics.strategy_context.build_strategy_context",
        lambda *_args, **_kwargs: {
            "status": "success",
            "levels": [],
            "ts_htf": [],
            "atr_by_ts": {},
            "buy_ts": [],
            "confirm_series": [],
        },
    )
    probe = _ContextProbe()

    result = _backtest_ticker_plugin(
        db=_DB(_candles()),
        ticker="TEST",
        plugin=probe,
        config={"atr_period": 14},
        date_from=None,
        date_to=None,
        n_runs=1,
    )

    assert result["status"] == "success"
    context = probe.contexts[-1]
    assert context.get_atr(14) > 0
    assert context.volume_current == 124.0
    assert context.volume_sma_20 is not None


def test_atr_strategy_uses_precomputed_context_atr(monkeypatch):
    candles = _candles()
    candles.loc[:, "low"] = 99.9
    candles.loc[5, "low"] = 99.15
    monkeypatch.setattr(
        "app.analytics.strategies.atr_reversal.compute_atr",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ATR should come from MarketContext")
        ),
    )
    monkeypatch.setattr(
        "app.analytics.strategies.atr_reversal.nearest_level_at",
        lambda *_args, **_kwargs: {"level_price": 99.8},
    )
    strategy = AtrReversalStrategy({})
    context = MarketContext(
        timestamp=candles.iloc[-1]["timestamp"],
        candles_1min=candles,
        atr_by_period={14: 1.0},
        levels=[{"level_price": 99.8}],
        volume_current=250.0,
        volume_sma_20=100.0,
    )

    signal = strategy.check_entry(context)

    assert signal is not None
    assert signal.metadata["atr"] == 1.0
