"""Regression tests for Issue #55 portfolio equity accounting."""

from __future__ import annotations

from app.analytics.portfolio_simulator import _replay_portfolio_trades


def _candidate(
    ticker: str,
    entry_ts: str,
    exit_ts: str,
    net_return_pct: float,
) -> dict:
    return {
        "ticker": ticker,
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "entry_price": 100.0,
        "exit_price": 100.0 * (1 + net_return_pct / 100.0),
        "exit_reason": "take" if net_return_pct >= 0 else "stop",
        "net_return_pct": net_return_pct,
        "bars_held": 1,
    }


def test_single_loss_equity_matches_manual_calculation():
    trades, curve, game_over, _, skipped = _replay_portfolio_trades(
        [
            _candidate(
                "AAA",
                "2026-01-01 10:00:00",
                "2026-01-01 11:00:00",
                -10.0,
            )
        ],
        {"AAA": 0},
        initial_capital=10_000.0,
        slot_size=10_000.0,
        max_positions=1,
    )

    assert not game_over
    assert skipped == 0
    assert trades[0]["pnl_rub"] == -1_000.0
    assert [point["equity_rub"] for point in curve] == [10_000.0, 9_000.0]
    assert [point["cash_rub"] for point in curve] == [10_000.0, 9_000.0]
    assert [point["open_positions"] for point in curve] == [0, 0]


def test_settlement_does_not_double_count_active_allocations():
    candidates = [
        _candidate(
            "AAA",
            "2026-01-01 10:00:00",
            "2026-01-01 11:00:00",
            -10.0,
        ),
        _candidate(
            "BBB",
            "2026-01-01 10:00:00",
            "2026-01-01 12:00:00",
            0.0,
        ),
        # This later entry makes the replay settle both earlier positions in
        # the main loop, which is where the historical double-count occurred.
        _candidate(
            "CCC",
            "2026-01-01 13:00:00",
            "2026-01-01 14:00:00",
            0.0,
        ),
    ]

    _, curve, game_over, _, skipped = _replay_portfolio_trades(
        candidates,
        {"AAA": 0, "BBB": 1, "CCC": 2},
        initial_capital=20_000.0,
        slot_size=10_000.0,
        max_positions=2,
    )

    assert not game_over
    assert skipped == 0
    assert [point["equity_rub"] for point in curve] == [
        20_000.0,
        19_000.0,
        19_000.0,
        19_000.0,
    ]
    assert [point["open_positions"] for point in curve] == [0, 1, 0, 0]
    assert [point["ts"] for point in curve] == sorted(
        point["ts"] for point in curve
    )
