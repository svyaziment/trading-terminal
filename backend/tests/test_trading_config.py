from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.analytics.trading_config import (
    DEFAULT_UNIVERSE,
    LIVE_UNIVERSE,
    get_live_trading_universe,
    get_trading_universe,
)


class Result:
    def __init__(self, frame=None):
        self.frame = frame if frame is not None else pd.DataFrame()

    def to_dataframe(self):
        return self.frame.copy()


class FakeDB:
    def __init__(self, tickers=None, fail=False):
        self.tickers = tickers
        self.fail = fail

    def select(self, query, params=None):
        if self.fail:
            raise RuntimeError("db unavailable")
        if self.tickers is None:
            return Result()
        return Result(pd.DataFrame({"ticker": self.tickers}))


def test_live_universe_is_top_five_and_subset_of_traded_fallback():
    assert LIVE_UNIVERSE == ["SBER", "LKOH", "RUAL", "NVTK", "GAZP"]
    assert len(LIVE_UNIVERSE) == 5
    assert len(set(LIVE_UNIVERSE)) == 5
    assert set(LIVE_UNIVERSE).issubset(set(DEFAULT_UNIVERSE))


def test_get_live_trading_universe_intersects_db_universe():
    db = FakeDB(tickers=["SBER", "GAZP", "CBOM"])
    assert get_live_trading_universe(db) == ["SBER", "GAZP"]


def test_get_live_trading_universe_keeps_selection_when_db_empty():
    assert get_live_trading_universe(FakeDB()) == list(LIVE_UNIVERSE)
    assert get_live_trading_universe(None) == list(LIVE_UNIVERSE)


def test_paper_universe_is_not_shrunk_to_live_top_five():
    traded = get_trading_universe(FakeDB(tickers=["RUAL", "GMKN", "SBER"]))
    assert traded == ["RUAL", "GMKN", "SBER"]
    assert get_live_trading_universe(FakeDB(tickers=["RUAL", "GMKN", "SBER"])) == [
        "SBER",
        "RUAL",
    ]


def test_published_summary_matches_live_universe():
    summary_path = (
        Path(__file__).resolve().parents[2]
        / "analytics/issue-66-live-universe/summary.json"
    )
    if not summary_path.exists():
        import pytest

        pytest.skip("published analytics directory is not mounted")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["selected"] == LIVE_UNIVERSE
