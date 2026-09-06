"""Tests for the Issue #139 stepped trailing stop (analytics only, no DB)."""
from __future__ import annotations

import importlib.util
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = BACKEND_ROOT.parent / "analytics/issue-139-trailing-stop-new-level"


def _load(name: str, filename: str):
    path = ANALYSIS_DIR / filename
    if not path.exists():
        import pytest

        pytest.skip("issue-139 analytics directory is not mounted")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_steps_match_issue_table():
    trailing = _load("issue139_trailing", "trailing.py")
    assert trailing.DEFAULT_STEPS == [
        {"trigger": 2.0, "stop": 1.5},
        {"trigger": 2.5, "stop": 2.0},
    ]


def test_trailing_first_step_locks_1_5R():
    trailing = _load("issue139_trailing", "trailing.py")
    # R = 10, take = 130. Bar 1 high 121 >= 2R(=120) -> stop to 115. Bar 2 low 114
    # <= 115 -> trailing exit at 115. Baseline rides to the take on bar 3.
    res = trailing.apply_trailing(100.0, 90.0, 130.0, [(121, 118), (120, 114), (140, 113)])
    assert res["baseline"]["exit_reason"] == "take"
    assert res["trailing"]["exit_reason"] == "trailing"
    assert abs(res["trailing"]["exit_price"] - 115.0) < 1e-6
    assert abs(res["trailing"]["step_reached"] - 1.5) < 1e-9
    # (115/100 - 1)*100 - 0.06 commission
    assert abs(res["trailing"]["net_return_pct"] - 14.94) < 1e-6


def test_trailing_second_step_locks_2R():
    trailing = _load("issue139_trailing", "trailing.py")
    # reach 2.5R (=125) -> stop to 120; next bar low 119 <= 120 -> trailing 120.
    path = [(121, 118), (126, 121), (122, 119), (130, 128)]
    res = trailing.apply_trailing(100.0, 90.0, 130.0, path)
    assert res["trailing"]["exit_reason"] == "trailing"
    assert abs(res["trailing"]["exit_price"] - 120.0) < 1e-6
    assert abs(res["trailing"]["step_reached"] - 2.0) < 1e-9


def test_no_lookahead_within_one_bar():
    trailing = _load("issue139_trailing", "trailing.py")
    # A single bar whose high hits 2R and low is at 1.5R must NOT be stopped out by
    # the stop it just armed (no intra-bar look-ahead): the trade survives this bar.
    res = trailing.apply_trailing(100.0, 90.0, 130.0, [(121, 115), (131, 120)])
    assert res["trailing"]["exit_reason"] == "take"  # rides to the take, not the trailed stop


def test_below_2r_trailing_equals_baseline():
    trailing = _load("issue139_trailing", "trailing.py")
    # never reaches +2R, so the stop never ratchets: B and A are identical stops.
    path = [(105, 104), (108, 100), (95, 89)]  # last low 89 <= 90 stop
    res = trailing.apply_trailing(100.0, 90.0, 130.0, path)
    assert res["baseline"]["exit_reason"] == "stop"
    assert res["trailing"]["exit_reason"] == "initial_stop"
    assert abs(res["baseline"]["net_return_pct"] - res["trailing"]["net_return_pct"]) < 1e-9


def test_build_candidates_share_entry_differ_exit():
    analysis = _load("issue139_analysis", "analysis.py")
    trades = [{
        "ticker": "SBER", "entry_ts": "2025-01-01 10:00:00", "entry_price": 100.0,
        "source": "levels_sr_support",
        "A": {"exit_ts": "2025-01-05 10:00:00", "exit_price": 130.0,
              "exit_reason": "take", "net_return_pct": 29.94},
        "B": {"exit_ts": "2025-01-02 10:00:00", "exit_price": 115.0,
              "exit_reason": "trailing", "net_return_pct": 14.94},
    }]
    cand_a = analysis.build_candidates(trades, "A")
    cand_b = analysis.build_candidates(trades, "B")
    assert cand_a[0]["entry_price"] == cand_b[0]["entry_price"] == 100.0
    assert cand_a[0]["entry_ts"] == cand_b[0]["entry_ts"]
    assert cand_a[0]["net_return_pct"] == 29.94
    assert cand_b[0]["net_return_pct"] == 14.94
    assert cand_b[0]["exit_ts"] < cand_a[0]["exit_ts"]  # trailing frees capital earlier


def test_verdict_math_and_trailing_share():
    analysis = _load("issue139_analysis", "analysis.py")
    ma = {"final_equity_rub": 90000.0, "n_trades": 100, "game_over": False,
          "exit_type_counts": {"stop": 60, "take": 40}}
    mb = {"final_equity_rub": 95000.0, "n_trades": 100, "game_over": False,
          "exit_type_counts": {"initial_stop": 55, "trailing": 10, "take": 35}}
    verdict = analysis.build_verdict(ma, mb, [{"trigger": 2.0, "stop": 1.5}])
    assert verdict["delta_rub"] == 5000.0
    assert verdict["trailing_improves_capital"] is True
    assert verdict["b_exit_share_trailing_pct"] == 10.0
    assert verdict["b_exit_share_take_pct"] == 35.0
    assert verdict["b_exit_share_initial_stop_pct"] == 55.0


def test_daily_equity_and_max_drawdown():
    analysis = _load("issue139_analysis", "analysis.py")
    result = {"trades": [
        {"exit_ts": "2025-01-02 10:00:00", "pnl_rub": 1000.0},
        {"exit_ts": "2025-01-03 10:00:00", "pnl_rub": -500.0},
        {"exit_ts": "2025-01-04 10:00:00", "pnl_rub": 200.0},
    ]}
    eq = analysis.daily_equity(result)
    assert float(eq.iloc[-1]) == 50700.0
    dd = analysis.max_drawdown_daily(eq)
    # peak 51000 -> trough 50500 => 0.980% drawdown
    assert abs(dd["max_drawdown_pct"] - 0.98) < 0.02
