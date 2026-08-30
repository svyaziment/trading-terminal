"""Tests for the reproducible Issue #130 portfolio analysis."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = BACKEND_ROOT.parent / "analytics/issue-130-sr-support-portfolio"


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


def test_published_c_is_isolated_and_has_stable_sha():
    generate = _load("issue130_generate", "generate_inputs.py")
    snapshot, candidates, volume = generate.load_published_c()
    generate.assert_isolated(snapshot["config"])
    assert set(snapshot["config"]["patterns"]) == {"levels_sr_support", "signal_4h_buy"}
    assert snapshot["config_sha256"] == generate.EXPECTED_SHA_C
    assert generate.config_sha(snapshot["config"]) == generate.EXPECTED_SHA_C
    assert len(candidates) == generate.EXPECTED_CANDIDATE_N
    assert volume == generate.VOLUME_ORDER_103
    assert {trade.get("source") for trade in candidates} == {generate.SOURCE_C}


def test_resolve_tickers_keeps_volume_rank():
    generate = _load("issue130_generate", "generate_inputs.py")
    big = ["PHOR", "AFKS", "SBER"]
    order = ["FEES", "AFKS", "SBER", "PHOR"]
    assert generate.resolve_tickers(big, order) == ["AFKS", "SBER", "PHOR"]


def test_slot_rules_match_issue44():
    generate = _load("issue130_generate", "generate_inputs.py")
    assert generate.INITIAL_CAPITAL == 50_000.0
    assert generate.SLOT_SIZE == 10_000.0
    assert generate.MAX_POSITIONS == 5
    assert generate.DATE_FROM == "2024-08-01"
    assert generate.DATE_TO == "2026-08-21"


def test_protected_names_are_the_three_references():
    generate = _load("issue130_generate", "generate_inputs.py")
    assert generate.LOCKED_NAME == "test_20260731"
    assert generate.SWING_ONLY_NAME == "test_20260820"
    assert generate.REFERENCE_NAME == "test_20260821"


def test_alrs_711_is_detected_and_clean_book_passes():
    generate = _load("issue130_generate", "generate_inputs.py")
    clean = [
        {
            "ticker": "ALRS",
            "entry_ts": "2026-08-19 10:00:00",
            "entry_price": 19.50,
        }
    ]
    assert generate.alrs_hits(clean) == []
    dirty = clean + [
        {
            "ticker": "ALRS",
            "entry_ts": "2026-08-20 11:50:24",
            "entry_price": 19.80,
        }
    ]
    hits = generate.alrs_hits(dirty)
    assert len(hits) == 1
    assert hits[0]["entry_price"] == 19.80


def test_resistance_split_rejects_retest_source():
    analysis = _load("issue130_analysis", "analysis.py")
    frame = pd.DataFrame(
        [
            {
                "ticker": "AFKS",
                "entry_ts": "2025-01-01 10:00:00",
                "exit_ts": "2025-01-01 11:00:00",
                "entry_price": 20.0,
                "exit_price": 21.0,
                "exit_reason": "take",
                "allocated_rub": 10000,
                "net_return_pct": 1.0,
                "pnl_rub": 100,
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
                "allocated_rub": 10000,
                "net_return_pct": -0.5,
                "pnl_rub": -50,
                "bars_held": 40,
                "source": "levels_sr_breakout_resistance",
            },
        ]
    )
    split = analysis.resistance_split(frame)
    assert split["support_n"] == 1
    assert split["resistance_n"] == 1


def test_verdict_is_not_paper_even_when_gates_pass():
    analysis = _load("issue130_analysis", "analysis.py")
    metrics = pd.Series(
        {
            "pnl_rub": 46204.63,
            "profit_factor": 1.33,
            "n_trades": 3237,
            "game_over": False,
            "candidate_trades": 4380,
            "final_equity_rub": 96204.63,
        }
    )
    verdict = analysis.product_verdict(
        metrics,
        {"blocked": True, "present": False},
        {"support_n": 3237, "resistance_n": 0, "other_n": 0},
    )
    assert verdict["paper"] is False
    assert verdict["paper_gates_pass"] is True
    assert verdict["match"] is True
    assert verdict["label"] == "не paper"


def test_verdict_fails_when_resistance_trades_present():
    analysis = _load("issue130_analysis", "analysis.py")
    metrics = pd.Series(
        {
            "pnl_rub": 1000.0,
            "profit_factor": 1.2,
            "n_trades": 100,
            "game_over": False,
            "candidate_trades": 4380,
            "final_equity_rub": 51000.0,
        }
    )
    verdict = analysis.product_verdict(
        metrics,
        {"blocked": True, "present": False},
        {"support_n": 90, "resistance_n": 10, "other_n": 0},
    )
    assert verdict["paper"] is False
    assert verdict["match"] is False


def test_published_summary_uses_c_not_exclusive_or_bmix():
    path = ANALYSIS_DIR / "summary.json"
    if not path.exists():
        import pytest

        pytest.skip("published summary is not mounted")
    summary = json.loads(path.read_text(encoding="utf-8"))
    metrics = summary["metrics"][0]
    assert summary["config_sha256"] == (
        "3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b"
    )
    assert summary["candidate_trades"] == 4380
    assert metrics["n_trades"] == 3237
    assert metrics["profit_factor"] == 1.33
    assert metrics["final_equity_rub"] == 96204.63
    assert summary["paper"] is False
    assert summary["alrs_veto_absent"] is True
    assert summary["resistance_n"] == 0
    assert summary["issue103_context"]["n_trades"] == 2070
    assert summary["issue44_context"]["n_trades"] == 3500
    assert summary["issue124_bmix"]["n_trades"] == 2837
    assert summary["issue129_isolated_c"]["n"] == 4380
