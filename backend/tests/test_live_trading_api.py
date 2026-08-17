from datetime import date

from app.api.live_trading_jobs import _build_where


def test_live_filters_target_closed_positions_ticker_and_dates() -> None:
    where, params = _build_where(
        status="closed",
        ticker="SBER",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 17),
    )

    assert "status IN ('closed_stop','closed_take')" in where
    assert "ticker = %(ticker)s" in where
    assert "COALESCE(signal_ts, created_at)::date >= %(date_from)s" in where
    assert params["ticker"] == "SBER"


def test_live_open_status_is_exact() -> None:
    where, params = _build_where(status="open")

    assert where == " WHERE status = %(status)s"
    assert params == {"status": "open"}
