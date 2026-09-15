"""Issue #148: Paper trailing stop integration tests.

Tests the trailing stop integration in paper_trader.py:
- Positions without trailing are managed as before (legacy path)
- Positions with trailing use DB state (no in-memory state)
- closed_trailing status and PnL
- Stop priority over take within bar

Run: cd backend && python -m pytest tests/test_paper_trailing_stop.py -v
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock, MagicMock
import pandas as pd
import pytest

from app.analytics.paper_trader import monitor_open


class TestLegacyPathNoTrailing:
    """Positions without trailing_enabled should be managed as before."""

    def test_stop_hit_closes_position(self):
        db = Mock()
        positions_df = pd.DataFrame([{
            'id': 1, 'ticker': 'SBER', 'entry_ts': datetime(2026, 1, 1, 10, 0),
            'entry_price': 100.0, 'stop_price': 95.0, 'take_price': 110.0,
            'lot_size': 1, 'size_lots': 1,
            'trailing_enabled': False, 'trailing_steps': None, 'risk_r': None,
            'current_stop_price': 95.0, 'step_reached': 0,
        }])
        
        candles_df = pd.DataFrame([{
            'timestamp': datetime(2026, 1, 1, 10, 5),
            'high': 102.0, 'low': 94.0,
        }])
        
        def select_side_effect(query, params=None):
            if 'paper_positions' in query:
                return MagicMock(to_dataframe=Mock(return_value=positions_df))
            if 'online_candles_1min' in query:
                return MagicMock(to_dataframe=Mock(return_value=candles_df))
            return MagicMock(to_dataframe=Mock(return_value=pd.DataFrame()))
        
        db.select.side_effect = select_side_effect
        db.execute = Mock()
        
        monitor_open(db, config={}, notifier=None)
        
        close_call = [c for c in db.execute.call_args_list if 'closed_stop' in str(c)]
        assert len(close_call) == 1


class TestTrailingPathRatchet:
    """Positions with trailing should ratchet stop in DB."""

    def test_step_armed_updates_db(self):
        db = Mock()
        import json
        steps = [{"trigger": 2.0, "stop": 1.9}]
        positions_df = pd.DataFrame([{
            'id': 1, 'ticker': 'SBER', 'entry_ts': datetime(2026, 1, 1, 10, 0),
            'entry_price': 100.0, 'stop_price': 90.0, 'take_price': 130.0,
            'lot_size': 1, 'size_lots': 1,
            'trailing_enabled': True,
            'trailing_steps': json.dumps(steps),
            'risk_r': 10.0,
            'current_stop_price': 90.0,
            'step_reached': 0,
        }])
        
        candles_df = pd.DataFrame([{
            'timestamp': datetime(2026, 1, 1, 10, 5),
            'high': 120.0, 'low': 110.0,
        }])
        
        def select_side_effect(query, params=None):
            if 'paper_positions' in query:
                return MagicMock(to_dataframe=Mock(return_value=positions_df))
            if 'online_candles_1min' in query:
                return MagicMock(to_dataframe=Mock(return_value=candles_df))
            return MagicMock(to_dataframe=Mock(return_value=pd.DataFrame()))
        
        db.select.side_effect = select_side_effect
        db.execute = Mock()
        
        monitor_open(db, config={}, notifier=None)
        
        update_calls = [c for c in db.execute.call_args_list if 'current_stop_price' in str(c)]
        assert len(update_calls) >= 1
        
        update_sql = str(update_calls[0])
        assert 'step_reached <' in update_sql


class TestTrailingExit:
    """Trailing exit should use current_stop_price and set status=closed_trailing."""

    def test_trailing_exit_closes_at_current_stop(self):
        db = Mock()
        import json
        steps = [{"trigger": 2.0, "stop": 1.9}]
        positions_df = pd.DataFrame([{
            'id': 1, 'ticker': 'SBER', 'entry_ts': datetime(2026, 1, 1, 10, 0),
            'entry_price': 100.0, 'stop_price': 90.0, 'take_price': 130.0,
            'lot_size': 1, 'size_lots': 1,
            'trailing_enabled': True,
            'trailing_steps': json.dumps(steps),
            'risk_r': 10.0,
            'current_stop_price': 90.0,
            'step_reached': 0,
        }])
        
        # Two candles: first arms step, second closes position
        candles_df = pd.DataFrame([
            {'timestamp': datetime(2026, 1, 1, 10, 5), 'high': 121.0, 'low': 110.0},
            {'timestamp': datetime(2026, 1, 1, 10, 10), 'high': 115.0, 'low': 108.0},
        ])
        
        def select_side_effect(query, params=None):
            if 'paper_positions' in query:
                return MagicMock(to_dataframe=Mock(return_value=positions_df))
            if 'online_candles_1min' in query:
                return MagicMock(to_dataframe=Mock(return_value=candles_df))
            return MagicMock(to_dataframe=Mock(return_value=pd.DataFrame()))
        
        db.select.side_effect = select_side_effect
        db.execute = Mock()
        
        monitor_open(db, config={}, notifier=None)
        
        # Should close with 'trailing' reason on second candle
        close_call = [c for c in db.execute.call_args_list if 'closed_trailing' in str(c)]
        assert len(close_call) == 1


class TestStopPriorityOverTake:
    """Within a bar, stop should be checked before take."""

    def test_stop_before_take_in_same_bar(self):
        db = Mock()
        positions_df = pd.DataFrame([{
            'id': 1, 'ticker': 'SBER', 'entry_ts': datetime(2026, 1, 1, 10, 0),
            'entry_price': 100.0, 'stop_price': 95.0, 'take_price': 110.0,
            'lot_size': 1, 'size_lots': 1,
            'trailing_enabled': False, 'trailing_steps': None, 'risk_r': None,
            'current_stop_price': 95.0, 'step_reached': 0,
        }])
        
        candles_df = pd.DataFrame([{
            'timestamp': datetime(2026, 1, 1, 10, 5),
            'high': 115.0, 'low': 94.0,
        }])
        
        def select_side_effect(query, params=None):
            if 'paper_positions' in query:
                return MagicMock(to_dataframe=Mock(return_value=positions_df))
            if 'online_candles_1min' in query:
                return MagicMock(to_dataframe=Mock(return_value=candles_df))
            return MagicMock(to_dataframe=Mock(return_value=pd.DataFrame()))
        
        db.select.side_effect = select_side_effect
        db.execute = Mock()
        
        monitor_open(db, config={}, notifier=None)
        
        close_calls = [c for c in db.execute.call_args_list if 'closed_' in str(c)]
        assert len(close_calls) == 1
        assert 'closed_stop' in str(close_calls[0])
