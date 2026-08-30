"""Tests for the reproducible Issue #129 Lab-universe analysis."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = BACKEND_ROOT.parent / "analytics/issue-129-sr-support-universe"


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


def test_build_config_c_is_isolated_and_has_stable_sha():
    extract = _load("issue129_extract", "extract_inputs.py")
    config_c = extract.build_config_c({})
    extract.assert_isolated(config_c, "C")
    assert set(config_c["patterns"]) == {"levels_sr_support", "signal_4h_buy"}
    assert "level_breakout_retest" not in config_c["patterns"]
    assert "levels_reversal" not in config_c["patterns"]
    assert "levels_sr_breakout" not in config_c["patterns"]
    params = config_c["patterns"]["levels_sr_support"]
    for key in extract.FORBIDDEN_RETEST_KEYS:
        assert key not in params
    assert extract.DATE_FROM == "2024-08-01"
    assert extract.DATE_TO == "2026-08-21"
    assert extract.MIN_CANDLES == 250_000
    assert extract.config_sha(config_c) == extract.EXPECTED_SHA_C


def test_resolve_tickers_ignores_run_params_draft():
    extract = _load("issue129_extract", "extract_inputs.py")
    big = ["AFKS", "ALRS", "SBER"]
    config = {"run_params": {"tickers": ["AFKS"], "depth": "express"}}
    assert extract.resolve_tickers(big, config) == big


def test_protected_names_are_the_three_references():
    extract = _load("issue129_extract", "extract_inputs.py")
    assert extract.LOCKED_NAME == "test_20260731"
    assert extract.SWING_ONLY_NAME == "test_20260820"
    assert extract.REFERENCE_NAME == "test_20260821"


def test_resistance_split_rejects_retest_source():
    analysis = _load("issue129_analysis", "analysis.py")
    frame = pd.DataFrame(
        [
            {
                "ticker": "AFKS",
                "entry_ts": "2025-01-01 10:00:00",
                "exit_ts": "2025-01-01 11:00:00",
                "entry_price": 20.0,
                "exit_price": 21.0,
                "exit_reason": "take",
                "net_return_pct": 1.0,
                "bars_held": 60,
                "source": "levels_sr_support",
            },
            {
                "ticker": "SBER",
                "entry_ts": "2025-02-01 10:00:00",
                "exit_ts": "2025-02-01 11:00:00",
                "entry_price": 220.0,
                "exit_price": 215.0,
                "exit_reason": "stop",
                "net_return_pct": -0.5,
                "bars_held": 40,
                "source": "levels_sr_breakout_resistance",
            },
        ]
    )
    split = analysis.resistance_split(frame)
    assert split["support"]["n"] == 1
    assert split["resistance"]["n"] == 1
    assert split["unlabeled_n"] == 0


def test_ticker_n_regression_matches_published_124_column():
    analysis = _load("issue129_analysis", "analysis.py")
    rows = [
        {
            "ticker": "AFKS",
            "c": {"n": 78},
            "expected_support_n": 78,
            "n_delta": 0,
            "n_match": True,
        },
        {
            "ticker": "ALRS",
            "c": {"n": 130},
            "expected_support_n": 131,
            "n_delta": -1,
            "n_match": False,
        },
    ]
    result = analysis.ticker_n_regression(rows)
    assert result["match"] is False
    assert result["tickers_match"] == 1
    assert result["mismatches"][0]["ticker"] == "ALRS"


def test_alrs_711_timestamp_is_blocked():
    analysis = _load("issue129_analysis", "analysis.py")
    clean = pd.DataFrame(
        [
            {
                "ticker": "ALRS",
                "entry_ts": "2026-08-19 10:00:00",
                "exit_ts": "2026-08-19 11:00:00",
                "entry_price": 19.50,
                "exit_price": 19.70,
                "exit_reason": "take",
                "net_return_pct": 1.0,
                "bars_held": 60,
                "source": "levels_sr_support",
            }
        ]
    )
    veto = analysis.alrs_entry_blocked(clean)
    assert veto["blocked"] is True
    dirty = pd.concat(
        [
            clean,
            pd.DataFrame(
                [
                    {
                        "ticker": "ALRS",
                        "entry_ts": "2026-08-20 11:50:24",
                        "exit_ts": "2026-08-20 12:00:00",
                        "entry_price": 19.80,
                        "exit_price": 19.40,
                        "exit_reason": "stop",
                        "net_return_pct": -1.0,
                        "bars_held": 10,
                        "source": "levels_sr_support",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    dirty["entry_ts"] = pd.to_datetime(dirty["entry_ts"])
    dirty["exit_ts"] = pd.to_datetime(dirty["exit_ts"])
    blocked = analysis.alrs_entry_blocked(dirty)
    assert blocked["blocked"] is False


def test_afks_regression_rejects_mix_116():
    analysis = _load("issue129_analysis", "analysis.py")
    mix = pd.DataFrame(
        [
            {
                "ticker": "AFKS",
                "entry_ts": f"2025-01-01 {10 + i // 60:02d}:{i % 60:02d}:00",
                "exit_ts": f"2025-01-01 {11 + i // 60:02d}:{i % 60:02d}:00",
                "entry_price": 15.0,
                "exit_price": 15.2,
                "exit_reason": "take" if i < 35 else "stop",
                "net_return_pct": 1.0 if i < 35 else -0.296,
                "bars_held": 10,
                "source": "levels_sr_support",
            }
            for i in range(116)
        ]
    )
    result = analysis.afks_regression(mix)
    assert result["C"]["n"] == 116
    assert result["matched_mix_by_mistake"] is True
    assert result["match"] is False


def test_verdict_match_when_c_copies_124_support():
    analysis = _load("issue129_analysis", "analysis.py")
    mix_c = {
        "n": 3811,
        "pf": 1.51,
        "pf_infinite": False,
        "exp_pct": 0.230,
        "wr": 30.9,
        "maxdd_pct": 27.5,
    }
    split = {
        "support": {"n": 3811, "metrics": mix_c},
        "resistance": {"n": 0, "metrics": {"n": 0, "pf": None, "pf_infinite": False}},
        "other_n": 0,
        "other_sources": [],
        "unlabeled_n": 0,
    }
    ticker_n = {"match": True, "tickers_match": 28, "tickers_compared": 28, "mismatches": []}
    afks = {
        "match": True,
        "matched_mix_by_mistake": False,
        "support_subset": True,
        "missing_support_n": 0,
        "C": {"n": 78, "pf": 1.7},
    }
    keys = {
        "compared": True,
        "match": True,
        "subset": True,
        "n_c": 3811,
        "n_124_support": 3811,
        "missing_from_c": 0,
        "extra_in_c": 0,
    }
    occupancy = {"extra_n": 0, "explained_n": 0, "unexplained_n": 0, "match": True}
    missing_slot = {"missing_n": 0, "occupied_n": 0, "near_miss_n": 0, "unexplained_n": 0, "match": True}
    verdict = analysis.product_verdict(
        mix_c,
        split,
        ticker_n,
        afks,
        {"blocked": True},
        keys,
        occupancy,
        missing_slot,
        {"median_pf": 1.48, "mean_pf": 1.51, "pf_gt1_count": 26, "tickers_success": 28},
    )
    assert verdict["paper"] is False
    assert verdict["match"] is True
    assert verdict["exclusive_n_pf"] is True
    assert verdict["portfolio_ready"] is True
    assert verdict["label"] == "совпало"


def test_verdict_match_when_occupancy_explains_extra():
    analysis = _load("issue129_analysis", "analysis.py")
    mix_c = {"n": 89, "pf": 1.49, "pf_infinite": False}
    split = {
        "support": {"n": 89, "metrics": mix_c},
        "resistance": {"n": 0, "metrics": {"n": 0, "pf": None, "pf_infinite": False}},
        "other_n": 0,
        "other_sources": [],
        "unlabeled_n": 0,
    }
    occupancy = {
        "extra_n": 11,
        "explained_n": 11,
        "unexplained_n": 0,
        "match": True,
        "metrics": {"n": 11, "pf": 0.4, "pf_infinite": False},
    }
    verdict = analysis.product_verdict(
        mix_c,
        split,
        {"match": False, "tickers_match": 0, "tickers_compared": 1, "mismatches": []},
        {
            "match": True,
            "matched_mix_by_mistake": False,
            "support_subset": True,
            "missing_support_n": 0,
            "C": {"n": 89, "pf": 1.49},
        },
        {"blocked": True},
        {
            "compared": True,
            "match": False,
            "subset": True,
            "n_c": 89,
            "n_124_support": 78,
            "missing_from_c": 0,
            "extra_in_c": 11,
        },
        occupancy,
        {"missing_n": 0, "occupied_n": 0, "near_miss_n": 0, "unexplained_n": 0, "match": True},
        {"median_pf": 1.49, "mean_pf": 1.49, "pf_gt1_count": 1, "tickers_success": 1},
    )
    assert verdict["paper"] is False
    assert verdict["exclusive_n_pf"] is False
    assert verdict["match"] is True
    assert verdict["portfolio_ready"] is True
    assert verdict["label"] == "совпало"


def test_verdict_match_when_occupancy_leftover_one():
    analysis = _load("issue129_analysis", "analysis.py")
    mix_c = {"n": 4380, "pf": 1.45, "pf_infinite": False}
    occupancy = {
        "extra_n": 611,
        "explained_n": 610,
        "unexplained_n": 1,
        "unexplained_after_near_miss": 1,
        "match": True,
        "metrics": {"n": 611, "pf": 0.95, "pf_infinite": False},
    }
    verdict = analysis.product_verdict(
        mix_c,
        {
            "support": {"n": 4380, "metrics": mix_c},
            "resistance": {"n": 0, "metrics": {"n": 0, "pf": None, "pf_infinite": False}},
            "other_n": 0,
            "other_sources": [],
            "unlabeled_n": 0,
        },
        {"match": False, "tickers_match": 0, "tickers_compared": 28, "mismatches": []},
        {
            "match": True,
            "matched_mix_by_mistake": False,
            "support_subset": True,
            "missing_support_n": 0,
            "C": {"n": 89, "pf": 1.49},
        },
        {"blocked": True},
        {
            "compared": True,
            "match": False,
            "subset": False,
            "n_c": 4380,
            "n_124_support": 3811,
            "missing_from_c": 42,
            "extra_in_c": 611,
        },
        occupancy,
        {"missing_n": 42, "occupied_n": 42, "near_miss_n": 0, "unexplained_n": 0, "match": True},
        {"median_pf": 1.48, "mean_pf": 1.47, "pf_gt1_count": 26, "tickers_success": 28},
    )
    assert verdict["match"] is True
    assert verdict["portfolio_ready"] is True
    assert verdict["label"] == "совпало"
    assert verdict["exclusive_n_pf"] is False


def test_occupancy_explained_by_open_resistance_trade():
    analysis = _load("issue129_analysis", "analysis.py")
    extras = pd.DataFrame(
        [
            {
                "ticker": "AFKS",
                "entry_ts": "2025-01-22 18:20:00",
                "exit_ts": "2025-01-22 18:32:00",
                "entry_price": 14.978,
                "exit_price": 14.5,
                "exit_reason": "stop",
                "net_return_pct": -1.0,
                "bars_held": 12,
                "source": "levels_sr_support",
            }
        ]
    )
    extras["entry_ts"] = pd.to_datetime(extras["entry_ts"])
    extras["exit_ts"] = pd.to_datetime(extras["exit_ts"])
    resistance = pd.DataFrame(
        [
            {
                "ticker": "AFKS",
                "entry_ts": "2025-01-22 18:10:00",
                "exit_ts": "2025-01-22 19:10:00",
                "entry_price": 14.931,
                "exit_price": 14.54,
                "exit_reason": "stop",
                "net_return_pct": -2.6,
                "bars_held": 60,
                "source": "levels_sr_breakout_resistance",
            }
        ]
    )
    resistance["entry_ts"] = pd.to_datetime(resistance["entry_ts"])
    resistance["exit_ts"] = pd.to_datetime(resistance["exit_ts"])
    result = analysis.occupancy_explained(extras, resistance)
    assert result["extra_n"] == 1
    assert result["explained_n"] == 1
    assert result["unexplained_n"] == 0
    assert result["match"] is True


def test_missing_explained_by_c_extra_inclusive_exit():
    analysis = _load("issue129_analysis", "analysis.py")
    support = pd.DataFrame(
        [
            {
                "ticker": "SIBN",
                "entry_ts": "2026-08-14 11:26:00",
                "exit_ts": "2026-08-14 11:27:00",
                "entry_price": 478.45,
                "exit_price": 478.0,
                "exit_reason": "stop",
                "net_return_pct": -0.1,
                "bars_held": 1,
                "source": "levels_sr_breakout_support",
            }
        ]
    )
    support["entry_ts"] = pd.to_datetime(support["entry_ts"])
    support["exit_ts"] = pd.to_datetime(support["exit_ts"])
    extras = pd.DataFrame(
        [
            {
                "ticker": "SIBN",
                "entry_ts": "2026-08-14 11:23:00",
                "exit_ts": "2026-08-14 11:26:00",
                "entry_price": 480.6,
                "exit_price": 478.45,
                "exit_reason": "stop",
                "net_return_pct": -0.4,
                "bars_held": 3,
                "source": "levels_sr_support",
            }
        ]
    )
    extras["entry_ts"] = pd.to_datetime(extras["entry_ts"])
    extras["exit_ts"] = pd.to_datetime(extras["exit_ts"])
    extra_keys = analysis.collect_trade_keys(extras)
    result = analysis.missing_explained(support, extras, extra_keys)
    assert result["missing_n"] == 1
    assert result["occupied_n"] == 1
    assert result["unexplained_n"] == 0
    assert result["match"] is True


def test_verdict_no_when_resistance_trades_present():
    analysis = _load("issue129_analysis", "analysis.py")
    mix_c = {"n": 3811, "pf": 1.51, "pf_infinite": False}
    split = {
        "support": {"n": 3811, "metrics": mix_c},
        "resistance": {"n": 12, "metrics": {"n": 12, "pf": 1.1, "pf_infinite": False}},
        "other_n": 0,
        "other_sources": [],
        "unlabeled_n": 0,
    }
    verdict = analysis.product_verdict(
        mix_c,
        split,
        {"match": True, "tickers_match": 28, "tickers_compared": 28, "mismatches": []},
        {
            "match": True,
            "matched_mix_by_mistake": False,
            "support_subset": True,
            "missing_support_n": 0,
            "C": {"n": 78, "pf": 1.7},
        },
        {"blocked": True},
        {
            "compared": True,
            "match": True,
            "subset": True,
            "n_c": 3811,
            "n_124_support": 3811,
            "missing_from_c": 0,
            "extra_in_c": 0,
        },
        {"extra_n": 0, "explained_n": 0, "unexplained_n": 0, "match": True},
        {"missing_n": 0, "occupied_n": 0, "near_miss_n": 0, "unexplained_n": 0, "match": True},
        {"median_pf": 1.48, "mean_pf": 1.51, "pf_gt1_count": 26, "tickers_success": 28},
    )
    assert verdict["paper"] is False
    assert verdict["match"] is False
    assert verdict["portfolio_ready"] is False
    assert verdict["label"] == "нет"


def test_published_124_support_n_sums_to_3811():
    analysis = _load("issue129_analysis", "analysis.py")
    assert sum(analysis.BOOK_124_SUPPORT_N.values()) == 3811
    assert analysis.BOOK_124_SUPPORT_N["AFKS"] == 78
    assert analysis.BOOK_124_SUPPORT["n"] == 3811
    assert analysis.BOOK_124_SUPPORT["pf"] == 1.51
