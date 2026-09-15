"""Issue #149: Trailing Stop API integration tests.

Tests the API surface of trailing stop integration:
1. POST /api/strategies: 422 on invalid trailing config with stable reason codes
2. POST /api/strategies: round-trip config with valid trailing_stop
3. GET  /api/strategies: trailing_stop metadata (enabled, steps, valid, reasons)
4. Paper positions: trailing fields present (trailing_enabled, current_stop_price, step_reached, risk_r)
5. Paper overview: trailing statistics (trailing_closed, trailing_closed_pnl_rub, trailing_open, active_stop_count)
6. Paper filter: status=closed includes closed_trailing
7. Live positions: trailing fields present
8. Live filter: status=closed includes closed_trailing

Run: cd backend && python -m pytest tests/test_trailing_api.py -v
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.analytics.trading_config import (
    TRAILING_REASON_NOT_MONOTONIC,
    TRAILING_REASON_STEP_INVALID,
    TRAILING_REASON_TOO_MANY_STEPS,
    TRAILING_STOP,
)

# Use the app instance
from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. POST /api/strategies: 422 on invalid trailing config
# ---------------------------------------------------------------------------

class TestStrategySaveValidation:
    """Test that save_strategy validates trailing_stop config."""

    def test_invalid_step_shape_returns_422_with_reason_codes(self):
        """A step with non-numeric trigger must be rejected with trailing_step_invalid."""
        config = {
            'trailing_stop': {
                'enabled': True,
                'steps': [{'trigger': 'not_a_number', 'stop': 1.9}],
            }
        }
        resp = client.post('/api/strategies', json={
            'name': 'test_invalid_trailing_shape',
            'config': config,
        })
        assert resp.status_code == 422
        body = resp.json()
        detail = body.get('detail', {})
        assert 'reason_codes' in detail
        assert TRAILING_REASON_STEP_INVALID in detail['reason_codes']

    def test_non_monotonic_steps_returns_422(self):
        """Steps where stop falls as trigger rises must be rejected."""
        config = {
            'trailing_stop': {
                'enabled': True,
                'steps': [
                    {'trigger': 2.0, 'stop': 1.9},
                    {'trigger': 2.5, 'stop': 1.0},  # stop falls!
                ],
            }
        }
        resp = client.post('/api/strategies', json={
            'name': 'test_invalid_trailing_monotonic',
            'config': config,
        })
        assert resp.status_code == 422
        body = resp.json()
        detail = body.get('detail', {})
        assert TRAILING_REASON_NOT_MONOTONIC in detail.get('reason_codes', [])

    def test_too_many_steps_returns_422(self):
        """More steps than max_steps must be rejected."""
        max_steps = TRAILING_STOP['max_steps']
        # Create steps within trigger/stop bounds but exceeding max_steps count.
        # max_trigger=3.5, min_trigger=0.0 (exclusive), max_stop=3.0.
        # Use 7 steps (max_steps + 1) all within bounds:
        config = {
            'trailing_stop': {
                'enabled': True,
                'steps': [
                    {'trigger': 0.5, 'stop': 0.4},
                    {'trigger': 1.0, 'stop': 0.9},
                    {'trigger': 1.5, 'stop': 1.4},
                    {'trigger': 2.0, 'stop': 1.9},
                    {'trigger': 2.5, 'stop': 2.4},
                    {'trigger': 3.0, 'stop': 2.9},
                    {'trigger': 3.5, 'stop': 3.0},
                ],
            }
        }
        resp = client.post('/api/strategies', json={
            'name': 'test_too_many_steps',
            'config': config,
        })
        assert resp.status_code == 422
        body = resp.json()
        detail = body.get('detail', {})
        assert TRAILING_REASON_TOO_MANY_STEPS in detail.get('reason_codes', [])

    def test_valid_trailing_config_saves_successfully(self):
        """A valid trailing config must save without 422."""
        config = {
            'trailing_stop': {
                'enabled': True,
                'steps': [
                    {'trigger': 2.0, 'stop': 1.9},
                    {'trigger': 2.5, 'stop': 2.4},
                    {'trigger': 3.0, 'stop': 2.9},
                ],
            }
        }
        resp = client.post('/api/strategies', json={
            'name': 'test_valid_trailing',
            'config': config,
        })
        # Should be 200 (saved), not 422
        assert resp.status_code == 200
        body = resp.json()
        assert body['name'] == 'test_valid_trailing'
        # trailing_stop metadata should be present in response
        assert 'trailing_stop' in body
        assert body['trailing_stop']['enabled'] is True
        assert body['trailing_stop']['reasons'] == []

    def test_no_trailing_key_saves_successfully(self):
        """A config without trailing_stop key must save (disabled default)."""
        config = {'some_param': 42}
        resp = client.post('/api/strategies', json={
            'name': 'test_no_trailing',
            'config': config,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['trailing_stop']['enabled'] is False
        assert body['trailing_stop']['reasons'] == []

    def test_disabled_trailing_saves_even_with_empty_steps(self):
        """enabled=false with empty steps is the shipped default - must save."""
        config = {
            'trailing_stop': {
                'enabled': False,
                'steps': [],
            }
        }
        resp = client.post('/api/strategies', json={
            'name': 'test_disabled_trailing',
            'config': config,
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. GET /api/strategies: trailing metadata
# ---------------------------------------------------------------------------

class TestStrategyListTrailingMetadata:
    """Test that list_strategies includes trailing_stop metadata."""

    def test_list_includes_trailing_metadata(self):
        resp = client.get('/api/strategies')
        assert resp.status_code == 200
        body = resp.json()
        strategies = body.get('strategies', [])
        # Every strategy should have trailing_stop metadata
        for strat in strategies:
            assert 'trailing_stop' in strat, f"strategy {strat.get('name')} missing trailing_stop"
            ts = strat['trailing_stop']
            assert 'enabled' in ts
            assert 'steps' in ts
            assert 'reasons' in ts
            assert isinstance(ts['enabled'], bool)
            assert isinstance(ts['steps'], list)
            assert isinstance(ts['reasons'], list)


# ---------------------------------------------------------------------------
# Paper trading trailing fields
# ---------------------------------------------------------------------------

class TestPaperTrailingFields:
    """Test that paper trading endpoints include trailing fields."""

    def test_paper_overview_has_trailing_stats(self):
        resp = client.get('/api/paper-trading/overview')
        assert resp.status_code == 200
        body = resp.json()
        summary = body.get('summary', {})
        # Issue #149: trailing statistics must be present
        assert 'trailing_closed' in summary
        assert 'trailing_closed_pnl_rub' in summary
        assert 'trailing_open' in summary
        assert 'active_stop_count' in summary
        # Types
        assert isinstance(summary['trailing_closed'], int)
        assert isinstance(summary['trailing_closed_pnl_rub'], float)
        assert isinstance(summary['trailing_open'], int)
        assert isinstance(summary['active_stop_count'], int)

    def test_paper_positions_have_trailing_fields(self):
        resp = client.get('/api/paper-trading/positions', params={'limit': 5})
        assert resp.status_code == 200
        body = resp.json()
        items = body.get('items', [])
        if items:
            # Check trailing fields are present in at least one record
            item = items[0]
            assert 'trailing_enabled' in item, "paper position missing trailing_enabled"
            assert 'current_stop_price' in item, "paper position missing current_stop_price"
            assert 'step_reached' in item, "paper position missing step_reached"
            assert 'risk_r' in item, "paper position missing risk_r"

    def test_paper_status_closed_includes_trailing(self):
        """status=closed filter must include closed_trailing positions."""
        resp = client.get('/api/paper-trading/positions',
                          params={'status': 'closed', 'limit': 100})
        assert resp.status_code == 200
        body = resp.json()
        items = body.get('items', [])
        for item in items:
            assert item['status'] in ('closed_stop', 'closed_take', 'closed_trailing'), \
                f"unexpected status {item['status']} in closed filter"


# ---------------------------------------------------------------------------
# Live trading trailing fields
# ---------------------------------------------------------------------------

class TestLiveTrailingFields:
    """Test that live trading endpoints include trailing fields."""

    def test_live_positions_have_trailing_fields(self):
        resp = client.get('/api/live-trading/positions', params={'limit': 5})
        assert resp.status_code == 200
        body = resp.json()
        items = body.get('items', [])
        if items:
            item = items[0]
            assert 'trailing_enabled' in item, "live position missing trailing_enabled"
            assert 'current_stop_price' in item, "live position missing current_stop_price"
            assert 'step_reached' in item, "live position missing step_reached"
            assert 'risk_r' in item, "live position missing risk_r"

    def test_live_status_closed_includes_trailing(self):
        """status=closed filter must include closed_trailing positions."""
        resp = client.get('/api/live-trading/positions',
                          params={'status': 'closed', 'limit': 100})
        assert resp.status_code == 200
        body = resp.json()
        items = body.get('items', [])
        for item in items:
            assert item['status'] in ('closed_stop', 'closed_take', 'closed_trailing'), \
                f"unexpected status {item['status']} in live closed filter"


# ---------------------------------------------------------------------------
# Regression: old filters still work
# ---------------------------------------------------------------------------

class TestRegressionOldFilters:
    """Ensure old filter values still return the same rows."""

    def test_paper_status_open_still_works(self):
        resp = client.get('/api/paper-trading/positions',
                          params={'status': 'open', 'limit': 10})
        assert resp.status_code == 200
        body = resp.json()
        for item in body.get('items', []):
            assert item['status'] == 'open'

    def test_paper_status_closed_stop_still_works(self):
        resp = client.get('/api/paper-trading/positions',
                          params={'status': 'closed_stop', 'limit': 10})
        assert resp.status_code == 200
        body = resp.json()
        for item in body.get('items', []):
            assert item['status'] == 'closed_stop'

    def test_live_status_open_still_works(self):
        resp = client.get('/api/live-trading/positions',
                          params={'status': 'open', 'limit': 10})
        assert resp.status_code == 200
        body = resp.json()
        for item in body.get('items', []):
            assert item['status'] == 'open'