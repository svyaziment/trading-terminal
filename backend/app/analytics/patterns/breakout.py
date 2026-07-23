# src/core/signal_patterns/patterns/breakout.py
import pandas as pd
from typing import Dict, Optional, Any
from app.analytics.patterns.base import BasePattern, MarketContext

class BO_BB_Squeeze(BasePattern):
    """
    Паттерн сжатия полос Боллинджера (Bollinger Squeeze).
    Ищет моменты аномально низкой волатильности перед сильным пробоем.
    """
    name = "BO_BB_Squeeze"
    category = "Breakout"
    
    # 🟢 Работает в условиях низкой или нормальной волатильности
    allowed_vol_regimes = ['LOW_VOL', 'NORMAL_VOL']
    allowed_trend_regimes = ['TRENDING', 'RANGING'] # Сжатие может быть в любом тренде
    
    # Пороги для таймфреймов
    timeframe_thresholds = {
        '30min': {'squeeze_percentile': 20, 'lookback': 50},
        '1h':    {'squeeze_percentile': 20, 'lookback': 50},
        '2h':    {'squeeze_percentile': 20, 'lookback': 50},
        '4h':    {'squeeze_percentile': 20, 'lookback': 50},
        '1d':    {'squeeze_percentile': 20, 'lookback': 50},
        '1w':    {'squeeze_percentile': 20, 'lookback': 50},
    }

    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        thresholds = self.get_thresholds(context.timeframe)
        squeeze_pct = thresholds.get('squeeze_percentile', 20)
        lookback = thresholds.get('lookback', 50)
        
        # 1. Проверяем наличие необходимых данных
        if pd.isna(current_row.get('bb_width')) or pd.isna(current_row.get('close')):
            return None
        if pd.isna(current_row.get('bb_upper')) or pd.isna(current_row.get('bb_lower')):
            return None
            
        # 2. Берем историю bb_width
        if len(history) < lookback or 'bb_width' not in history.columns:
            return None
            
        recent_widths = history['bb_width'].tail(lookback).dropna()
        if len(recent_widths) < 20: # Нужно хотя бы 20 точек для статистики
            return None
            
        current_width = current_row['bb_width']
        
        # 3. Вычисляем перцентиль текущего bb_width относительно истории
        # (Какой процент исторических значений был МЕНЬШЕ текущего)
        percentile = (recent_widths < current_width).sum() / len(recent_widths) * 100
        
        # 4. Если ширина не в нижних X%, значит нет "сжатия пружины"
        if percentile > squeeze_pct:
            return None
            
        # 5. Определяем направление пробоя
        close = current_row['close']
        bb_upper = current_row['bb_upper']
        bb_lower = current_row['bb_lower']
        
        # Чем сильнее сжатие (ниже перцентиль), тем выше сила сигнала
        strength = min((100 - percentile) / 100, 1.0) 
        
        if close > bb_upper:
            return {
                'direction': 'BUY',
                'strength': round(strength, 2),
                'reason': f'Bollinger Squeeze (пробой вверх): bb_width перцентиль {percentile:.1f}%'
            }
        elif close < bb_lower:
            return {
                'direction': 'SELL',
                'strength': round(strength, 2),
                'reason': f'Bollinger Squeeze (пробой вниз): bb_width перцентиль {percentile:.1f}%'
            }
            
        return None