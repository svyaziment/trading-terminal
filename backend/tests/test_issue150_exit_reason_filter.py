"""Issue #150: exit_reason query filter and trailing panel data tests.

Tests:
1. Paper _build_where includes exit_reason clause when provided.
2. Live _build_where includes exit_reason clause when provided.
3. Paper _build_where without exit_reason produces no exit_reason clause.
4. Live _build_where without exit_reason produces no exit_reason clause.

Run: cd backend && python -m pytest tests/test_issue150_exit_reason_filter.py -v
"""
from __future__ import annotations

from app.api.paper_trading_jobs import _build_where as paper_build_where
from app.api.live_trading_jobs import _build_where as live_build_where


class TestPaperExitReasonFilter:
    """Paper _build_where must support exit_reason query parameter."""

    def test_exit_reason_produces_clause(self):
        where, params = paper_build_where(exit_reason="trailing")
        assert "exit_reason = %(exit_reason)s" in where
        assert params["exit_reason"] == "trailing"

    def test_no_exit_reason_no_clause(self):
        where, params = paper_build_where()
        assert "exit_reason" not in where
        assert "exit_reason" not in params

    def test_exit_reason_with_other_filters(self):
        where, params = paper_build_where(status="closed", exit_reason="take", ticker="SBER")
        assert "exit_reason = %(exit_reason)s" in where
        assert params["exit_reason"] == "take"
        assert params["ticker"] == "SBER"
        assert "status IN" in where  # closed expands to IN clause


class TestLiveExitReasonFilter:
    """Live _build_where must support exit_reason query parameter."""

    def test_exit_reason_produces_clause(self):
        where, params = live_build_where(exit_reason="trailing")
        assert "exit_reason = %(exit_reason)s" in where
        assert params["exit_reason"] == "trailing"

    def test_no_exit_reason_no_clause(self):
        where, params = live_build_where()
        assert "exit_reason" not in where
        assert "exit_reason" not in params

    def test_exit_reason_with_status(self):
        where, params = live_build_where(status="closed", exit_reason="stop")
        assert "exit_reason = %(exit_reason)s" in where
        assert params["exit_reason"] == "stop"
        assert "status IN" in where