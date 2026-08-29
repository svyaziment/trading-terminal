"""Tests for the reproducible Issue #119 AFKS smoke analysis."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = BACKEND_ROOT.parent / "analytics/issue-119-afks-sr-breakout-smoke"


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


def test_build_configs_are_isolated():
    extract = _load("issue119_extract", "extract_inputs.py")
    config_a = extract.build_config_a({})
    config_b = extract.build_config_b({})
    extract.assert_isolated(config_a, "A")
    extract.assert_isolated(config_b, "B")
    assert "level_breakout_retest" not in config_a["patterns"]
    assert "level_breakout_retest" not in config_b["patterns"]
    assert "levels_reversal" not in config_b["patterns"]
    assert "levels_sr_breakout" not in config_a["patterns"]
    assert config_a["confirm_windows"] == [10]
    assert config_b["risk_reward"] == {"risk": 1.0, "reward": 2.0}


def test_config_sha_is_stable_and_differs():
    extract = _load("issue119_extract", "extract_inputs.py")
    config_a = extract.build_config_a({})
    config_b = extract.build_config_b({})
    sha_a = extract.config_sha(config_a)
    sha_b = extract.config_sha(config_b)
    assert sha_a == extract.config_sha(config_a)
    assert sha_a != sha_b
    assert len(sha_a) == 64


def test_source_split_and_extra_trades():
    analysis = _load("issue119_analysis", "analysis.py")
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
                "ticker": "AFKS",
                "entry_ts": "2025-02-01 10:00:00",
                "exit_ts": "2025-02-01 11:00:00",
                "entry_price": 22.0,
                "exit_price": 21.5,
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
    examples = analysis.pick_path_b_examples(frame, limit=2)
    assert len(examples) == 1
    assert examples[0]["source"] == "levels_sr_breakout_resistance"


def test_verdict_expand_when_path_b_helps():
    analysis = _load("issue119_analysis", "analysis.py")
    metrics_a = {"n": 10, "pf": 1.10, "pf_infinite": False, "exp_pct": 0.1, "wr": 30, "maxdd_pct": 8}
    metrics_b = {"n": 14, "pf": 1.20, "pf_infinite": False, "exp_pct": 0.12, "wr": 31, "maxdd_pct": 9}
    split = {
        "support": {"n": 10, "metrics": metrics_a},
        "resistance": {
            "n": 4,
            "metrics": {"n": 4, "pf": 1.40, "pf_infinite": False, "exp_pct": 0.2, "wr": 40, "maxdd_pct": 5},
        },
        "unlabeled_n": 0,
    }
    verdict = analysis.product_verdict(
        metrics_a, metrics_b, split, {"n_a": 10, "n_b": 14, "added": 4}, {"ran": False}
    )
    assert verdict["paper"] is False
    assert verdict["expand_universe"] is True
    assert verdict["tune_retest"] is False


def test_verdict_explains_extra_support_from_tracker_veto():
    analysis = _load("issue119_analysis", "analysis.py")
    metrics_a = {"n": 10, "pf": 1.10, "pf_infinite": False, "exp_pct": 0.1, "wr": 30, "maxdd_pct": 8}
    metrics_b = {"n": 16, "pf": 1.20, "pf_infinite": False, "exp_pct": 0.12, "wr": 31, "maxdd_pct": 9}
    split = {
        "support": {"n": 12, "metrics": metrics_a},
        "resistance": {
            "n": 4,
            "metrics": {"n": 4, "pf": 1.40, "pf_infinite": False, "exp_pct": 0.2, "wr": 40, "maxdd_pct": 5},
        },
        "unlabeled_n": 0,
    }
    verdict = analysis.product_verdict(
        metrics_a, metrics_b, split, {"n_a": 10, "n_b": 16, "added": 6}, {"ran": False}
    )
    assert any("Support-путь B" in item for item in verdict["reasons"])
    assert any("LevelsTracker" in item for item in verdict["reasons"])


def test_verdict_tune_retest_when_path_b_empty():
    analysis = _load("issue119_analysis", "analysis.py")
    metrics = {"n": 10, "pf": 1.10, "pf_infinite": False, "exp_pct": 0.1, "wr": 30, "maxdd_pct": 8}
    split = {
        "support": {"n": 10, "metrics": metrics},
        "resistance": {
            "n": 0,
            "metrics": {"n": 0, "pf": None, "pf_infinite": False, "exp_pct": None, "wr": None, "maxdd_pct": None},
        },
        "unlabeled_n": 0,
    }
    verdict = analysis.product_verdict(
        metrics, metrics, split, {"n_a": 10, "n_b": 10, "added": 0}, {"ran": False}
    )
    assert verdict["paper"] is False
    assert verdict["tune_retest"] is True
    assert verdict["expand_universe"] is False


def test_protected_names_are_the_three_references():
    extract = _load("issue119_extract", "extract_inputs.py")
    assert extract.LOCKED_NAME == "test_20260731"
    assert extract.SWING_ONLY_NAME == "test_20260820"
    assert extract.REFERENCE_NAME == "test_20260821"
    assert extract.TICKER == "AFKS"
    assert extract.DATE_FROM == "2024-08-01"
    assert extract.DATE_TO == "2026-08-21"
