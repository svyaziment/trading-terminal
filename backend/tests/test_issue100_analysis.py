"""Tests for the reproducible Issue #100 test_20260820 analysis."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = BACKEND_ROOT.parent / "analytics/issue-100-test-20260820-resistance-veto"


def _load(name: str, filename: str):
    path = ANALYSIS_DIR / filename
    if not path.exists():
        import pytest

        pytest.skip("published analytics directory is not mounted")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_resolve_tickers_ignores_run_params_alrs_draft():
    extract = _load("issue100_extract", "extract_inputs.py")
    big = ["ALRS", "GAZP", "SBER"]
    config = {"run_params": {"tickers": ["ALRS"], "depth": "express"}}
    assert extract.resolve_tickers(big, config) == big


def test_walkforward_includes_2026h2_when_bars_reach_july():
    extract = _load("issue100_extract", "extract_inputs.py")
    names = [row[0] for row in extract.walkforward_periods("2026-08-20 12:00:00")]
    assert names[-1] == "2026-H2"
    names_short = [row[0] for row in extract.walkforward_periods("2026-06-01")]
    assert "2026-H2" not in names_short


def test_alrs_711_timestamp_is_blocked():
    analysis = _load("issue100_analysis", "analysis.py")
    trades = [
        {
            "entry_ts": "2026-08-19 10:00:00",
            "entry_price": 19.50,
            "exit_reason": "take",
            "net_return_pct": 1.0,
        }
    ]
    veto = analysis.alrs_entry_blocked(trades)
    assert veto["blocked"] is True
    assert veto["hits"] == []

    blocked = analysis.alrs_entry_blocked(
        trades
        + [
            {
                "entry_ts": "2026-08-20 11:50:24",
                "entry_price": 19.80,
                "exit_reason": "stop",
                "net_return_pct": -1.0,
            }
        ]
    )
    assert blocked["blocked"] is False
    assert blocked["hits"][0]["entry_price"] == 19.80


def test_express_baseline_is_not_treated_as_full_universe():
    analysis = _load("issue100_analysis", "analysis.py")
    comparison = analysis.compare_alrs_express(
        {"n": 80, "pf": 1.2, "exp_pct": 0.1, "wr": 40.0, "maxdd_pct": 8.0},
        {
            "id_271_present": False,
            "cited_id_271": None,
            "current_alrs_express": {
                "id": 279,
                "ticker": "ALRS",
                "test_type": "full_sample",
                "depth": "express",
                "metrics": {"n": 27, "pf": 0.93},
            },
            "express_ticker_count": 14,
        },
    )
    assert comparison["available"] is True
    assert comparison["baseline_id"] == 279
    assert comparison["id_271_present"] is False
    assert comparison["issue_cited_id_271"]["n"] == 25
    missing = analysis.compare_alrs_express({"n": 80}, {"id_271_present": False, "current_alrs_express": None})
    assert missing["available"] is False


def test_aggregate_full_sample_median_pf():
    analysis = _load("issue100_analysis", "analysis.py")
    frame = pd.DataFrame(
        [
            {"ticker": "A", "status": "success", "n": 10, "pf": 1.4, "pf_infinite": False, "exp_pct": 0.2, "wr": 40, "maxdd_pct": 5, "bars_1min": 1, "error": None},
            {"ticker": "B", "status": "success", "n": 8, "pf": 0.8, "pf_infinite": False, "exp_pct": -0.1, "wr": 30, "maxdd_pct": 9, "bars_1min": 1, "error": None},
            {"ticker": "C", "status": "failed", "n": None, "pf": None, "pf_infinite": False, "exp_pct": None, "wr": None, "maxdd_pct": None, "bars_1min": None, "error": "x"},
        ]
    )
    aggregates = analysis.aggregate_full_sample(frame)
    assert aggregates["tickers_success"] == 2
    assert aggregates["median_pf"] == 1.1
    assert aggregates["pf_gt1_count"] == 1
    assert aggregates["trades_total"] == 18
