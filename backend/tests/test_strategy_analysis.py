"""Tests for the reproducible Issue #44 analysis."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_PATH = next(
    path
    for path in (
        BACKEND_ROOT.parent / "reports/Vulpec/44_strategy-analysis/analysis.py",
        BACKEND_ROOT / "reports/Vulpec/44_strategy-analysis/analysis.py",
    )
    if path.exists()
)


def _load_analysis_module():
    spec = importlib.util.spec_from_file_location("issue44_analysis", ANALYSIS_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _result(strategy: str, pnls: list[float]) -> dict:
    trades = []
    equity = 50_000.0
    for index, pnl in enumerate(pnls, 1):
        equity += pnl
        trades.append(
            {
                "ticker": "TEST",
                "entry_ts": f"2026-01-0{index} 10:00:00",
                "exit_ts": f"2026-01-0{index} 11:00:00",
                "entry_price": 100.0,
                "exit_price": 101.0 if pnl > 0 else 99.0,
                "exit_reason": "take" if pnl > 0 else "stop",
                "allocated_rub": 10_000.0,
                "net_return_pct": pnl / 100.0,
                "pnl_rub": pnl,
                "bars_held": 60,
            }
        )
    gains = sum(value for value in pnls if value > 0)
    losses = abs(sum(value for value in pnls if value <= 0))
    return {
        "status": "success",
        "mode": "full",
        "strategy": strategy,
        "initial_capital_rub": 50_000.0,
        "slot_size_rub": 10_000.0,
        "max_positions": 5,
        "date_from": "2026-01-01",
        "date_to": "2026-01-04",
        "game_over": False,
        "game_over_ts": None,
        "skipped_entries_no_slot": 0,
        "metrics": {
            "n_trades": len(pnls),
            "win_rate": sum(value > 0 for value in pnls) / len(pnls) * 100,
            "profit_factor": gains / losses,
            "max_drawdown_pct": 0.2,
            "final_equity_rub": equity,
            "pnl_rub": sum(pnls),
            "pnl_pct": sum(pnls) / 500.0,
        },
        "trades": trades,
        "equity_curve": [],
    }


def test_run_analysis_builds_report_and_plots(tmp_path, monkeypatch):
    analysis = _load_analysis_module()
    inputs = {}
    for strategy, pnls in (
        ("levels_reversal", [100.0, -50.0]),
        ("atr_reversal", [50.0, -100.0]),
    ):
        path = tmp_path / f"{strategy}.json"
        path.write_text(
            json.dumps(_result(strategy, pnls)), encoding="utf-8"
        )
        inputs[strategy] = path
    monkeypatch.setattr(analysis, "ANALYSIS_DIR", tmp_path)
    monkeypatch.setattr(analysis, "PLOTS_DIR", tmp_path / "plots")

    output = analysis.run_analysis(inputs)

    assert output["metrics"].loc["levels_reversal", "pnl_rub"] == 50.0
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "summary.json").exists()
    assert len(list((tmp_path / "plots").glob("*.png"))) == 4


def test_load_result_rejects_wrong_capital(tmp_path):
    analysis = _load_analysis_module()
    result = _result("levels_reversal", [100.0, -50.0])
    result["initial_capital_rub"] = 10_000.0
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(ValueError, match="50,000 RUB"):
        analysis._load_result(path, "levels_reversal")
