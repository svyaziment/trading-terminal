from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from app.analytics.online_data import ORDERBOOK_DEPTH, save_orderbook_aggregate
from app.analytics.orderbook_imbalance import (
    calculate_volume_imbalance,
    get_imbalance_threshold,
    get_recent_imbalance,
    passes_imbalance_filter,
)


def level(quantity, price=100):
    return SimpleNamespace(
        quantity=quantity,
        price=SimpleNamespace(units=price, nano=0),
    )


class FakeDB:
    def __init__(self, frame=None):
        self.frame = frame if frame is not None else pd.DataFrame()
        self.executions = []
        self.select_calls = []

    def execute(self, query, params):
        self.executions.append((query, params))

    def select(self, query, params):
        self.select_calls.append((query, params))
        return SimpleNamespace(to_dataframe=lambda: self.frame.copy())


@pytest.mark.parametrize(
    ("bid_depth", "ask_depth", "expected"),
    [
        (200, 100, 2.0),
        (0, 100, 0.0),
        (None, 100, None),
        (100, None, None),
        (100, 0, None),
        (-1, 100, None),
        (float("nan"), 100, None),
    ],
)
def test_calculate_volume_imbalance(bid_depth, ask_depth, expected):
    assert calculate_volume_imbalance(bid_depth, ask_depth) == expected


def test_threshold_comes_from_strategy_config_with_default():
    assert get_imbalance_threshold({}) == 1.0
    assert get_imbalance_threshold({"imbalance_threshold": 1.75}) == 1.75


@pytest.mark.parametrize("value", [None, "bad", float("nan"), float("inf")])
def test_missing_or_invalid_imbalance_always_skips_signal(value):
    assert not passes_imbalance_filter(value, {"imbalance_threshold": 1.0})


def test_filter_requires_value_strictly_above_strategy_threshold():
    config = {"imbalance_threshold": 1.5}

    assert not passes_imbalance_filter(1.5, config)
    assert passes_imbalance_filter(1.5001, config)


def test_invalid_threshold_fails_fast():
    with pytest.raises(ValueError, match="imbalance_threshold"):
        get_imbalance_threshold({"imbalance_threshold": "not-a-number"})


def test_recent_imbalance_is_recalculated_from_aggregate_depths():
    db = FakeDB(pd.DataFrame([{"bid_depth": 300, "ask_depth": 120}]))
    now = datetime(2026, 8, 16, 12, 0)

    result = get_recent_imbalance(db, "SBER", minutes=5, now=now)

    assert result == 2.5
    query, params = db.select_calls[0]
    assert "trading.online_orderbook_aggregates" in query
    assert "bid_depth, ask_depth" in query
    assert params[0] == "SBER"
    assert params[1] == datetime(2026, 8, 16, 11, 55)


def test_recent_imbalance_returns_none_without_fresh_aggregate():
    assert (
        get_recent_imbalance(
            FakeDB(),
            "SBER",
            now=datetime(2026, 8, 16, 12, 0),
        )
        is None
    )


def test_stream_update_calculates_configured_depth_and_persists_ratio():
    db = FakeDB()
    orderbook = SimpleNamespace(
        bids=[level(1, 100) for _ in range(ORDERBOOK_DEPTH + 2)],
        asks=[level(2, 101) for _ in range(ORDERBOOK_DEPTH + 2)],
    )

    save_orderbook_aggregate(db, "SBER", orderbook)

    assert len(db.executions) == 1
    _, params = db.executions[0]
    assert params[7] == ORDERBOOK_DEPTH
    assert params[8] == ORDERBOOK_DEPTH * 2
    assert params[9] == 0.5
    assert params[10] == ORDERBOOK_DEPTH


def test_stream_update_with_no_book_data_writes_nothing():
    db = FakeDB()

    save_orderbook_aggregate(
        db,
        "SBER",
        SimpleNamespace(bids=[], asks=[level(1)]),
    )

    assert db.executions == []


def test_zero_ask_depth_is_persisted_as_missing_imbalance():
    db = FakeDB()

    save_orderbook_aggregate(
        db,
        "SBER",
        SimpleNamespace(bids=[level(5)], asks=[level(0)]),
    )

    _, params = db.executions[0]
    assert params[9] is None
