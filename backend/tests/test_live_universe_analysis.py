"""Tests for the reproducible Issue #66 live-universe ranking."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_PATH = (
    BACKEND_ROOT.parent / "analytics/issue-66-live-universe/analysis.py"
)


def _load_analysis_module():
    if not ANALYSIS_PATH.exists():
        import pytest

        pytest.skip("published analytics directory is not mounted")
    spec = importlib.util.spec_from_file_location("issue66_analysis", ANALYSIS_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ticker(
    ticker: str,
    *,
    rank: int,
    pf: float,
    spread: float,
    turnover: float,
    atr: float,
    lot_size: int = 1,
    close: float = 100.0,
    strategy_pf: float | None = None,
    strategy_n: float | None = None,
    strategy_wr: float | None = None,
    strategy_exp: float | None = None,
    strategy_maxdd: float | None = None,
    depth: float = 10_000.0,
) -> dict:
    universe = {
        "ticker": ticker,
        "rank": rank,
        "pf": pf,
        "source": "test",
        "notes": "",
        "updated_at": "2026-08-17",
    }
    instrument = {"ticker": ticker, "lot_size": lot_size, "min_price_increment": 0.01, "figi": f"figi-{ticker}"}
    market = {
        "ticker": ticker,
        "last_1d_ts": "2026-08-14",
        "last_close": close,
        "last_volume": 1_000_000,
        "atr_14": close * atr / 100.0,
        "atr_pct": atr,
        "avg_volume_60d": turnover / close,
        "avg_turnover_60d": turnover,
        "n_days": 60,
    }
    spread_row = {
        "ticker": ticker,
        "n_quotes": 100,
        "min_ts": "2026-08-01",
        "max_ts": "2026-08-14",
        "avg_abs_spread_pct": spread,
        "median_abs_spread_pct": spread,
        "avg_bid_depth": depth / 2,
        "avg_ask_depth": depth / 2,
        "avg_depth": depth,
    }
    locked = None
    if strategy_pf is not None:
        locked = {
            "ticker": ticker,
            "test_type": "full_sample",
            "depth": "express",
            "created_at": "2026-07-31",
            "metrics": {
                "n": strategy_n,
                "pf": strategy_pf,
                "wr": strategy_wr,
                "exp_pct": strategy_exp,
                "maxdd_pct": strategy_maxdd,
            },
        }
    return {
        "universe": universe,
        "instrument": instrument,
        "market": market,
        "spread": spread_row,
        "locked": locked,
    }


def _payload(rows: list[dict]) -> dict:
    return {
        "extracted_at": "2026-08-17 00:00:00",
        "paper_positions": {"rows": 0, "closed": 0, "open": 0, "wins": 0, "pnl_rub": 0},
        "paper_equity": {
            "rows": 2,
            "min_ts": "2026-08-15 11:00:00",
            "max_ts": "2026-08-15 12:00:00",
            "min_equity_rub": 100000,
            "max_equity_rub": 100000,
            "min_drawdown_pct": 0,
            "max_drawdown_pct": 0,
            "min_open_positions": 0,
            "max_open_positions": 0,
        },
        "active_strategy": {
            "id": 36,
            "name": "test_20260731",
            "patterns": ["levels_reversal"],
            "confirm_windows": [10],
            "risk_reward": {"risk": 1, "reward": 2},
            "commission_pct": 0.06,
            "run_params": {"date_from": "2026-07-01", "date_to": "2026-07-31", "depth": "express"},
        },
        "universe": [row["universe"] for row in rows],
        "locked_backtest": [row["locked"] for row in rows if row["locked"]],
        "instruments": [row["instrument"] for row in rows],
        "market": [row["market"] for row in rows],
        "spreads": [row["spread"] for row in rows],
        "top_stocks": [],
        "issue44_levels_reversal_ticker_pnl_rub": {},
    }


def test_run_analysis_selects_five_and_excludes_negative_pf(tmp_path, monkeypatch):
    analysis = _load_analysis_module()
    rows = [
        _ticker("SBER", rank=1, pf=1.9, spread=0.004, turnover=8e9, atr=2.0, strategy_pf=1.4, strategy_n=20, strategy_wr=35, strategy_exp=0.2, strategy_maxdd=3),
        _ticker("LKOH", rank=2, pf=2.0, spread=0.011, turnover=4e9, atr=3.3, strategy_pf=1.6, strategy_n=10, strategy_wr=40, strategy_exp=0.3, strategy_maxdd=1.5),
        _ticker("RUAL", rank=3, pf=2.4, spread=0.04, turnover=1.6e8, atr=4.1),
        _ticker("NVTK", rank=4, pf=1.7, spread=0.011, turnover=2.4e9, atr=4.0, strategy_pf=2.5, strategy_n=27, strategy_wr=37, strategy_exp=0.6, strategy_maxdd=3.1),
        _ticker("GAZP", rank=5, pf=2.0, spread=0.011, turnover=1.4e9, atr=3.5, strategy_pf=1.5, strategy_n=16, strategy_wr=31, strategy_exp=0.28, strategy_maxdd=4.2),
        _ticker("CBOM", rank=6, pf=1.8, spread=0.09, turnover=1.1e8, atr=3.9, strategy_pf=0.13, strategy_n=38, strategy_wr=10, strategy_exp=-0.3, strategy_maxdd=12),
        _ticker("SIBN", rank=7, pf=1.97, spread=0.03, turnover=3.5e8, atr=3.9),
    ]
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(_payload(rows)), encoding="utf-8")
    monkeypatch.setattr(analysis, "ANALYSIS_DIR", tmp_path)
    monkeypatch.setattr(analysis, "PLOTS_DIR", tmp_path / "plots")

    output = analysis.run_analysis(path)

    assert "CBOM" not in output["selected"]
    assert len(output["selected"]) == 5
    assert output["summary"]["excluded"]["CBOM"] == "locked_strategy_pf"
    oil_count = sum(1 for ticker in output["selected"] if ticker in {"GAZP", "LKOH", "SIBN"})
    assert oil_count <= 2
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "summary.json").exists()
    assert len(list((tmp_path / "plots").glob("*.png"))) == 4


def test_select_top_caps_sector_concentration():
    analysis = _load_analysis_module()
    scored = pd.DataFrame(
        {
            "sector": ["oil", "oil", "oil", "banks", "metals"],
            "score": [0.9, 0.8, 0.7, 0.6, 0.5],
        },
        index=["GAZP", "LKOH", "SIBN", "SBER", "RUAL"],
    )
    assert analysis.select_top(scored, top_n=5) == ["GAZP", "LKOH", "SBER", "RUAL"]
