# src/core/signal_patterns/patterns/trend.py
import pandas as pd
from typing import Dict, Optional, Any
from app.analytics.patterns.base import BasePattern, MarketContext

class Trend_SMA_Alignment(BasePattern):
    """
    Паттерн идеального тренда (Trend Alignment).
    Сигнал: Цена > Быстрая SMA > Медленная SMA (для BUY)
    """
    name = "Trend_SMA_Alignment"
    category = "Trend"
    
    # 🟢 Работает ТОЛЬКО когда рынок в режиме TRENDING
    allowed_trend_regimes = ['TRENDING']
    allowed_vol_regimes = ['LOW_VOL', 'NORMAL_VOL', 'HIGH_VOL']
    
    # 🟢 Адаптация под таймфрейм (какие SMA использовать)
    timeframe_thresholds = {
        '30min': {'fast_sma': 'sma_10', 'slow_sma': 'sma_20'},
        '1h':    {'fast_sma': 'sma_20', 'slow_sma': 'sma_50'},
        '2h':    {'fast_sma': 'sma_20', 'slow_sma': 'sma_50'},
        '4h':    {'fast_sma': 'sma_20', 'slow_sma': 'sma_50'},
        '1d':    {'fast_sma': 'sma_50', 'slow_sma': 'sma_200'},
        '1w':    {'fast_sma': 'sma_50', 'slow_sma': 'sma_200'},
    }

    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        # 1. Берем пороги для текущего таймфрейма
        thresholds = self.get_thresholds(context.timeframe)
        fast_col = thresholds.get('fast_sma', 'sma_20')
        slow_col = thresholds.get('slow_sma', 'sma_50')
        
        # 2. Достаем значения
        price = current_row.get('close')
        fast_sma = current_row.get(fast_col)
        slow_sma = current_row.get(slow_col)
        
        # Проверка на наличие данных (NaN)
        if pd.isna(price) or pd.isna(fast_sma) or pd.isna(slow_sma):
            return None
            
        # 3. Логика BUY: Цена > Быстрая SMA > Медленная SMA
        if price > fast_sma > slow_sma:
            # Сила сигнала зависит от "веера" между SMA (чем шире, тем сильнее тренд)
            spread_pct = (fast_sma - slow_sma) / slow_sma * 100
            strength = min(max(spread_pct / 5.0, 0.3), 1.0) # Нормализуем от 0.3 до 1.0
            
            return {
                'direction': 'BUY',
                'strength': round(strength, 2),
                'reason': f'Восходящий тренд: {price:.2f} > {fast_col}({fast_sma:.2f}) > {slow_col}({slow_sma:.2f})'
            }
            
        # 4. Логика SELL: Цена < Быстрая SMA < Медленная SMA
        elif price < fast_sma < slow_sma:
            spread_pct = (slow_sma - fast_sma) / slow_sma * 100
            strength = min(max(spread_pct / 5.0, 0.3), 1.0)
            
            return {
                'direction': 'SELL',
                'strength': round(strength, 2),
                'reason': f'Нисходящий тренд: {price:.2f} < {fast_col}({fast_sma:.2f}) < {slow_col}({slow_sma:.2f})'
            }
            
        # Если SMA переплетены или цена внутри -> сигнала нет
        return None