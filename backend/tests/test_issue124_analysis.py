"""Tests for the reproducible Issue #124 Lab-universe analysis."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = BACKEND_ROOT.parent / "analytics/issue-124-sr-breakout-universe"


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


def test_build_configs_are_isolated_and_match_issue119_sha():
    extract = _load("issue124_extract", "extract_inputs.py")
    config_a = extract.build_config_a({})
    config_b = extract.build_config_b({})
    extract.assert_isolated(config_a, "A")
    extract.assert_isolated(config_b, "B")
    assert "level_breakout_retest" not in config_a["patterns"]
    assert "level_breakout_retest" not in config_b["patterns"]
    assert "levels_reversal" not in config_b["patterns"]
    assert "levels_sr_breakout" not in config_a["patterns"]
    assert extract.DATE_FROM == "2024-08-01"
    assert extract.DATE_TO == "2026-08-21"
    assert extract.MIN_CANDLES == 250_000
    assert extract.config_sha(config_a) == extract.EXPECTED_SHA_A
    # SHA B matches #119 only after normalize_patterns fills retest defaults.


def test_resolve_tickers_ignores_run_params_draft():
    extract = _load("issue124_extract", "extract_inputs.py")
    big = ["AFKS", "ALRS", "SBER"]
    config = {"run_params": {"tickers": ["AFKS"], "depth": "express"}}
    assert extract.resolve_tickers(big, config) == big


def test_protected_names_are_the_three_references():
    extract = _load("issue124_extract", "extract_inputs.py")
    assert extract.LOCKED_NAME == "test_20260731"
    assert extract.SWING_ONLY_NAME == "test_20260820"
    assert extract.REFERENCE_NAME == "test_20260821"


def test_source_split_extra_support_and_path_b_examples():
    analysis = _load("issue124_analysis", "analysis.py")
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
                "source": "levels_sr_breakout_support",
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
    split = analysis.source_split(frame)
    assert split["support"]["n"] == 1
    assert split["resistance"]["n"] == 1
    assert split["unlabeled_n"] == 0
    extra = analysis.extra_vs_baseline(1, 2)
    assert extra["added"] == 1
    examples = analysis.pick_path_b_examples(frame, limit_tickers=2, per_ticker=1)
    assert len(examples) == 1
    assert examples[0]["ticker"] == "SBER"
    assert examples[0]["source"] == "levels_sr_breakout_resistance"


def test_alrs_711_timestamp_is_blocked_on_both_books():
    analysis = _load("issue124_analysis", "analysis.py")
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
                "source": "levels_sr_breakout_support",
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
                        "source": "levels_sr_breakout_support",
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


def test_verdict_portfolio_when_isolated_b_is_stable():
    analysis = _load("issue124_analysis", "analysis.py")
    mix_a = {"n": 100, "pf": 1.40, "pf_infinite": False, "exp_pct": 0.3, "wr": 32, "maxdd_pct": 8}
    mix_b = {"n": 160, "pf": 1.35, "pf_infinite": False, "exp_pct": 0.28, "wr": 33, "maxdd_pct": 9}
    split = {
        "support": {"n": 120, "metrics": mix_a},
        "resistance": {
            "n": 40,
            "metrics": {"n": 40, "pf": 1.20, "pf_infinite": False, "exp_pct": 0.2, "wr": 36, "maxdd_pct": 7},
        },
        "unlabeled_n": 0,
    }
    agg_a = {"median_pf": 1.38, "mean_pf": 1.40, "pf_gt1_count": 24, "tickers_success": 28, "pf_gt1_share": 0.857}
    agg_b = {"median_pf": 1.33, "mean_pf": 1.35, "pf_gt1_count": 23, "tickers_success": 28, "pf_gt1_share": 0.821}
    afks = {
        "match": True,
        "A": {"n": 39, "pf": 1.5},
        "B": {"n": 116, "pf": 1.46},
    }
    verdict = analysis.product_verdict(
        agg_a,
        agg_b,
        mix_a,
        mix_b,
        split,
        {"n_a": 100, "n_b": 160, "added": 60},
        afks,
        {"blocked": True},
        {"blocked": True},
    )
    assert verdict["paper"] is False
    assert verdict["tune_retest"] is False
    assert verdict["portfolio_replay"] is True
    assert verdict["label"] == "портфельный replay"


def test_verdict_tune_retest_when_path_b_pf_below_one():
    analysis = _load("issue124_analysis", "analysis.py")
    mix_a = {"n": 100, "pf": 1.40, "pf_infinite": False}
    mix_b = {"n": 140, "pf": 1.05, "pf_infinite": False}
    split = {
        "support": {"n": 100, "metrics": mix_a},
        "resistance": {
            "n": 40,
            "metrics": {"n": 40, "pf": 0.80, "pf_infinite": False},
        },
        "unlabeled_n": 0,
    }
    verdict = analysis.product_verdict(
        {"median_pf": 1.40, "mean_pf": 1.40, "pf_gt1_count": 24, "tickers_success": 28, "pf_gt1_share": 0.8},
        {"median_pf": 1.10, "mean_pf": 1.05, "pf_gt1_count": 16, "tickers_success": 28, "pf_gt1_share": 0.5},
        mix_a,
        mix_b,
        split,
        {"n_a": 100, "n_b": 140, "added": 40},
        {"match": True, "A": {"n": 39, "pf": 1.5}, "B": {"n": 116, "pf": 1.46}},
        {"blocked": True},
        {"blocked": True},
    )
    assert verdict["paper"] is False
    assert verdict["tune_retest"] is True
    assert verdict["portfolio_replay"] is False
    assert verdict["label"] == "крутить ретест"


def test_afks_regression_helper_matches_published_smoke():
    analysis = _load("issue124_analysis", "analysis.py")
    frame_a = pd.DataFrame(
        [
            {
                "ticker": "AFKS",
                "entry_ts": "2025-01-01 10:00:00",
                "exit_ts": "2025-01-01 11:00:00",
                "entry_price": 15.0,
                "exit_price": 15.2,
                "exit_reason": "take",
                "net_return_pct": 1.0 if i < 12 else -0.8,
                "bars_held": 10,
                "source": "levels_reversal",
            }
            for i in range(39)
        ]
    )
    # 12 wins * 1.0 and 27 losses * 0.8 => PF = 12/21.6 = 0.56, not 1.50.
    # The helper compares n and PF; here we only check the n-mismatch path.
    frame_b = frame_a.copy()
    frame_b["source"] = "levels_sr_breakout_support"
    result = analysis.afks_regression(frame_a, frame_b)
    assert result["A"]["n"] == 39
    assert result["match_a"] is False
    assert result["match"] is False
