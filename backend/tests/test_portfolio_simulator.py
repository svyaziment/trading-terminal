"""Tests for portfolio_simulator (Issue #37)."""

from __future__ import annotations

import pytest

from app.analytics.portfolio_simulator import (
    DEFAULT_CAPITAL,
    MODE_PRESETS,
    _portfolio_metrics,
    _replay_portfolio_trades,
    get_tickers_by_volume,
    resolve_strategy_name,
    run_from_db,
)


def test_resolve_strategy_name_levels():
    cfg = {"patterns": {"levels_reversal": {}, "signal_4h_buy": {}}}
    assert resolve_strategy_name(cfg) == "levels_reversal"


def test_resolve_strategy_name_atr():
    cfg = {"patterns": ["atr_reversal"], "strategy_name": None}
    assert resolve_strategy_name(cfg) == "atr_reversal"


def test_resolve_strategy_name_explicit():
    assert resolve_strategy_name({"strategy_name": "custom"}) == "custom"


def test_portfolio_metrics_empty():
    m = _portfolio_metrics([], [{"equity_rub": 50000}], 50000)
    assert m["n_trades"] == 0
    assert m["final_equity_rub"] == 50000
    assert m["pnl_rub"] == 0


def test_portfolio_metrics_with_trades():
    trades = [{"pnl_rub": 100}, {"pnl_rub": -50}]
    curve = [{"equity_rub": 50000}, {"equity_rub": 50100}, {"equity_rub": 50050}]
    m = _portfolio_metrics(trades, curve, 50000)
    assert m["n_trades"] == 2
    assert m["win_rate"] == 50.0
    assert m["final_equity_rub"] == 50050


def test_replay_respects_max_positions():
    """Only max_positions entries accepted at the same timestamp."""
    candidates = [
        {"ticker": "AAA", "entry_ts": "2026-01-01 10:00:00", "exit_ts": "2026-01-01 11:00:00",
         "entry_price": 100, "exit_price": 101, "exit_reason": "take", "net_return_pct": 1.0, "bars_held": 1},
        {"ticker": "BBB", "entry_ts": "2026-01-01 10:00:00", "exit_ts": "2026-01-01 11:00:00",
         "entry_price": 100, "exit_price": 101, "exit_reason": "take", "net_return_pct": 1.0, "bars_held": 1},
        {"ticker": "CCC", "entry_ts": "2026-01-01 10:00:00", "exit_ts": "2026-01-01 11:00:00",
         "entry_price": 100, "exit_price": 99, "exit_reason": "stop", "net_return_pct": -1.0, "bars_held": 1},
    ]
    rank = {"AAA": 0, "BBB": 1, "CCC": 2}
    trades, _, game_over, _, skipped = _replay_portfolio_trades(
        candidates, rank, initial_capital=25000, slot_size=10000, max_positions=2,
    )
    assert not game_over
    assert len(trades) == 2
    assert skipped == 1
    assert {t["ticker"] for t in trades} == {"AAA", "BBB"}


def test_replay_game_over():
    candidates = [
        {"ticker": "AAA", "entry_ts": "2026-01-01 10:00:00", "exit_ts": "2026-01-01 11:00:00",
         "entry_price": 100, "exit_price": 90, "exit_reason": "stop", "net_return_pct": -100.0, "bars_held": 1},
    ]
    trades, _, game_over, go_ts, _ = _replay_portfolio_trades(
        candidates, {"AAA": 0}, initial_capital=10000, slot_size=10000, max_positions=1,
    )
    assert game_over
    assert go_ts is not None
    assert len(trades) == 1


@pytest.mark.integration
def test_dev_run_from_db():
    """Dev mode completes in reasonable time against live DB."""
    from app.db.db_manager import DBManager

    db = DBManager()
    try:
        tickers = get_tickers_by_volume(
            db,
            date_from=MODE_PRESETS["dev"]["date_from"],
            date_to=MODE_PRESETS["dev"]["date_to"],
            max_tickers=3,
        )
        assert len(tickers) >= 1

        result = run_from_db(db, mode="dev")
        assert result["status"] == "success"
        assert result["mode"] == "dev"
        assert "metrics" in result
        assert result["initial_capital_rub"] == DEFAULT_CAPITAL
        assert isinstance(result["trades"], list)
        assert isinstance(result["equity_curve"], list)
        m = result["metrics"]
        assert m["final_equity_rub"] is not None
        assert m["n_trades"] >= 0
    finally:
        db.close_pool()
