from datetime import date

from app.api.paper_trading_jobs import _build_where


def test_monitoring_filters_support_closed_group_ticker_and_dates() -> None:
    where, params = _build_where(
        status="closed",
        ticker="SBER",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 17),
    )

    assert "status IN ('closed_stop','closed_take')" in where
    assert "ticker = %(ticker)s" in where
    assert "COALESCE(entry_ts, created_at)::date >= %(date_from)s" in where
    assert "COALESCE(entry_ts, created_at)::date <= %(date_to)s" in where
    assert params == {
        "ticker": "SBER",
        "date_from": date(2026, 8, 1),
        "date_to": date(2026, 8, 17),
    }


def test_monitoring_filter_keeps_exact_status_behavior() -> None:
    where, params = _build_where(status="open")

    assert where == " WHERE status = %(status)s"
    assert params == {"status": "open"}
