"""
Регрессионные тесты для задачи #14.
"""
import pytest
import pandas as pd
from app.analytics.strategy_context import build_strategy_context
from app.analytics.strategy_engine import StrategyEvaluator


class TestRegressionIssue14:
    def test_default_params_match_baseline(self, mocker):
        mock_db = mocker.MagicMock()
        mock_df = pd.DataFrame({
            'open': [100, 101, 102, 101, 100],
            'high': [102, 103, 104, 103, 102],
            'low': [99, 100, 101, 100, 99],
            'close': [101, 102, 101, 102, 101]
        })
        
        config = {'patterns': ['levels_reversal'], 'confirm_windows': [10]}
        context = build_strategy_context(mock_db, 'SBER', config, mock_df)
        
        assert context['params']['swing_windows'] == [10]
        assert context['params']['body_ratio'] == 0.7
        assert context['params']['impulse_atr_mult'] == 1.5
        assert context['params']['zone_atr_mult'] == 0.5
        assert context['params']['confirm_windows'] == [10]
        assert 'levels' in context
        assert not context['levels'].empty
    
    def test_changing_swing_window_affects_levels(self, mocker):
        mock_db = mocker.MagicMock()
        mock_df = pd.DataFrame({
            'open': [100, 101, 102, 101, 100],
            'high': [102, 103, 104, 103, 102],
            'low': [99, 100, 101, 100, 99],
            'close': [101, 102, 101, 102, 101]
        })
        
        config_default = {'patterns': ['levels_reversal']}
        config_changed = {
            'patterns': ['levels_reversal'],
            'levels_reversal': {'swing_windows': [20]}
        }
        
        context_default = build_strategy_context(mock_db, 'SBER', config_default, mock_df)
        context_changed = build_strategy_context(mock_db, 'SBER', config_changed, mock_df)
        
        assert not context_default['levels'].equals(context_changed['levels'])
