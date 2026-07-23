# src/core/signal_patterns/patterns/mean_reversion.py
import pandas as pd
from typing import Dict, Optional, Any
from app.analytics.patterns.base import BasePattern, MarketContext

class MR_RSI_Reversal(BasePattern):
    """
    Паттерн разворота RSI (Mean Reversion).
    Сигнал: RSI пересекает уровень перепроданности снизу вверх (BUY)
    """
    name = "MR_RSI_Reversal"
    category = "MeanReversion"
    
    # 🟢 Работает ТОЛЬКО во флэте (RANGING) и не в шторм
    allowed_trend_regimes = ['RANGING']
    allowed_vol_regimes = ['LOW_VOL', 'NORMAL_VOL'] # HIGH_VOL отсекаем, там RSI может залипать
    
    # 🟢 Адаптация порогов под таймфрейм
    timeframe_thresholds = {
        '30min': {'oversold': 25, 'overbought': 75}, # На 30мин больше шума, сужаем зону
        '1h':    {'oversold': 30, 'overbought': 70},
        '2h':    {'oversold': 30, 'overbought': 70},
        '4h':    {'oversold': 30, 'overbought': 70},
        '1d':    {'oversold': 35, 'overbought': 65}, # На дневках движения сильнее
        '1w':    {'oversold': 35, 'overbought': 65},
    }

    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        # 1. Берем пороги для текущего таймфрейма
        thresholds = self.get_thresholds(context.timeframe)
        oversold = thresholds.get('oversold', 30)
        overbought = thresholds.get('overbought', 70)
        
        # 2. Достаем текущий RSI
        current_rsi = current_row.get('rsi_14')
        if pd.isna(current_rsi) or current_rsi is None:
            return None
            
        # 3. Проверяем наличие истории для факта пересечения
        if history.empty or 'rsi_14' not in history.columns:
            return None
            
        # Берем предыдущее значение RSI (последняя строка в history)
        if len(history) < 2:
            return None
        prev_rsi = history['rsi_14'].iloc[-2]
        if pd.isna(prev_rsi) or prev_rsi is None:
            return None
            
        # 4. Логика BUY: Предыдущий RSI < oversold, а текущий >= oversold (пересечение снизу вверх)
        if prev_rsi < oversold and current_rsi >= oversold:
            # Чем глубже был провал, тем сильнее сигнал на отскок
            depth = oversold - prev_rsi
            strength = min(max(depth / 20.0, 0.4), 1.0)
            
            return {
                'direction': 'BUY',
                'strength': round(strength, 2),
                'reason': f'Разворот RSI из перепроданности: {prev_rsi:.1f} -> {current_rsi:.1f} (порог {oversold})'
            }
            
        # 5. Логика SELL: Предыдущий RSI > overbought, а текущий <= overbought (пересечение сверху вниз)
        elif prev_rsi > overbought and current_rsi <= overbought:
            depth = prev_rsi - overbought
            strength = min(max(depth / 20.0, 0.4), 1.0)
            
            return {
                'direction': 'SELL',
                'strength': round(strength, 2),
                'reason': f'Разворот RSI из перекупленности: {prev_rsi:.1f} -> {current_rsi:.1f} (порог {overbought})'
            }
            
        return None