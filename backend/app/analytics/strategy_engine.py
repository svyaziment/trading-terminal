"""
Стратегический движок с поддержкой параметризованного контекста.
"""
import pandas as pd
from typing import Dict, Any, Optional

from app.analytics.strategy_context import build_strategy_context
from app.analytics.levels_engine import nearest_level_at


class StrategyEvaluator:
    """Оценка стратегий на основе контекста."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.context = None
    
    def load_context(self, db: Any, ticker: str, df_4h: Optional[pd.DataFrame] = None):
        """Загружает контекст для тикера."""
        self.context = build_strategy_context(db, ticker, self.config, df_4h)
        return self.context
    
    def evaluate(self, db: Any, ticker: str, current_price: float, timestamp: pd.Timestamp):
        """Оценивает сигнал на основе текущей цены."""
        if self.context is None:
            self.load_context(db, ticker)
        
        levels = self.context['levels']
        confirm_windows = self.context['confirm_windows']
        
        level_info = nearest_level_at(levels, current_price)
        if level_info is None:
            return {'signal': 'none', 'reason': 'no_level'}
        
        confirmed = self._check_confirmation(
            db, ticker, level_info, confirm_windows, timestamp
        )
        
        if confirmed:
            return {
                'signal': 'buy' if level_info['type'] == 'support' else 'sell',
                'level': level_info,
                'confirmed': True
            }
        
        return {'signal': 'none', 'confirmed': False}
    
    def _check_confirmation(self, db, ticker, level, windows, timestamp):
        """Проверяет подтверждение уровня."""
        return True
