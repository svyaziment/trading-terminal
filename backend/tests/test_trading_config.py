from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.analytics.trading_config import (
    EXPECTED_LOCKED_STRATEGY,
    LIVE_UNIVERSE,
    get_live_trading_universe,
    get_streaming_universe,
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


def test_live_universe_is_po_sandbox_list():
    assert LIVE_UNIVERSE == [
        "ROSN",
        "IRAO",
        "AFKS",
        "NVTK",
        "SBER",
        "MTSS",
        "PHOR",
        "MOEX",
        "FLOT",
    ]
    assert len(LIVE_UNIVERSE) == 9
    assert len(set(LIVE_UNIVERSE)) == 9
    assert EXPECTED_LOCKED_STRATEGY == "test_20260830_new_level"


def test_get_live_trading_universe_is_not_clipped_by_paper_universe():
    db = FakeDB(tickers=["SBER", "GAZP", "CBOM"])
    assert get_live_trading_universe(db) == list(LIVE_UNIVERSE)
    assert get_live_trading_universe(FakeDB()) == list(LIVE_UNIVERSE)
    assert get_live_trading_universe(None) == list(LIVE_UNIVERSE)


def test_paper_universe_is_not_shrunk_to_live_list():
    traded = get_trading_universe(FakeDB(tickers=["RUAL", "GMKN", "SBER"]))
    assert traded == ["RUAL", "GMKN", "SBER"]
    assert get_live_trading_universe(FakeDB(tickers=["RUAL", "GMKN", "SBER"])) == list(
        LIVE_UNIVERSE
    )


def test_streaming_universe_unions_paper_and_live():
    db = FakeDB(tickers=["RUAL", "GMKN", "SBER"])
    stream = get_streaming_universe(db)
    assert stream[:3] == ["RUAL", "GMKN", "SBER"]
    for ticker in LIVE_UNIVERSE:
        assert ticker in stream
    assert stream == [
        "RUAL",
        "GMKN",
        "SBER",
        "ROSN",
        "IRAO",
        "AFKS",
        "NVTK",
        "MTSS",
        "PHOR",
        "MOEX",
        "FLOT",
    ]


def test_issue66_published_selection_stays_historical():
    summary_path = (
        Path(__file__).resolve().parents[2]
        / "analytics/issue-66-live-universe/summary.json"
    )
    if not summary_path.exists():
        import pytest

        pytest.skip("published analytics directory is not mounted")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["selected"] == ["SBER", "LKOH", "RUAL", "NVTK", "GAZP"]
    assert summary["selected"] != LIVE_UNIVERSE
