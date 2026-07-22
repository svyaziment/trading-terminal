# src/core/signal_patterns/patterns/volume.py
"""
Volume-based patterns.
Анализируют аномалии объема для подтверждения трендов и разворотов.
"""
import pandas as pd
from typing import Dict, Optional, Any
from src.core.signal_patterns.base import BasePattern, MarketContext


class VOL_Spike(BasePattern):
    """
    Паттерн скачка объема (Volume Spike).
    Резкое увеличение объема относительно среднего часто предшествует сильному движению.
    
    Логика:
    - Если volume_ratio > threshold (по умолчанию 2.5) - аномальный объем
    - Направление определяется по закрытию свечи:
      * Закрытие в верхней половине диапазона -> BUY (покупатели активны)
      * Закрытие в нижней половине диапазона -> SELL (продавцы активны)
    - Сила сигнала зависит от величины скачка
    """
    
    name = "VOL_Spike"
    category = "Volume"
    allowed_trend_regimes = ["TRENDING", "RANGING"]
    allowed_vol_regimes = ["LOW_VOL", "NORMAL_VOL", "HIGH_VOL"]
    
    def get_thresholds(self, timeframe: str) -> Dict[str, float]:
        """Пороги для разных таймфреймов."""
        return {
            '30min': {'spike_ratio': 3.0, 'min_volume_ratio': 2.0},
            '1h': {'spike_ratio': 2.5, 'min_volume_ratio': 2.0},
            '2h': {'spike_ratio': 2.5, 'min_volume_ratio': 2.0},
            '4h': {'spike_ratio': 2.0, 'min_volume_ratio': 1.8},
            '1d': {'spike_ratio': 2.0, 'min_volume_ratio': 1.8},
            '1w': {'spike_ratio': 1.8, 'min_volume_ratio': 1.5},
        }.get(timeframe, {'spike_ratio': 2.5, 'min_volume_ratio': 2.0})
    
    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        thresholds = self.get_thresholds(context.timeframe)
        spike_ratio = thresholds.get('spike_ratio', 2.5)
        min_ratio = thresholds.get('min_volume_ratio', 2.0)
        
        # 1. Проверяем наличие необходимых данных
        volume_ratio = current_row.get('volume_ratio')
        if pd.isna(volume_ratio) or pd.isna(current_row.get('close')):
            return None
        if pd.isna(current_row.get('high')) or pd.isna(current_row.get('low')):
            return None
        
        # 2. Проверяем скачок объема
        if volume_ratio < min_ratio:
            return None
        
        # 3. Определяем направление по позиции закрытия в диапазоне свечи
        high = current_row['high']
        low = current_row['low']
        close = current_row['close']
        
        if high == low:  # Доджи/крест - неопределенность
            return None
        
        # Позиция закрытия: 0 = минимум, 1 = максимум
        close_position = (close - low) / (high - low)
        
        # 4. Определяем направление и силу
        if close_position > 0.7:
            # Закрытие в верхней части - бычий сигнал
            direction = 'BUY'
            # Сила зависит от величины скачка
            strength = min((volume_ratio / spike_ratio) * 0.6, 1.0)
            reason = f"Скачок объема (ratio={volume_ratio:.2f}), закрытие в верхней части свечи ({close_position:.0%})"
        elif close_position < 0.3:
            # Закрытие в нижней части - медвежий сигнал
            direction = 'SELL'
            strength = min((volume_ratio / spike_ratio) * 0.6, 1.0)
            reason = f"Скачок объема (ratio={volume_ratio:.2f}), закрытие в нижней части свечи ({close_position:.0%})"
        else:
            # Неопределенное закрытие - слабый сигнал
            return None
        
        return {
            'direction': direction,
            'strength': round(strength, 2),
            'reason': reason
        }


class VOL_Low_Pullback(BasePattern):
    """
    Паттерн отката на низком объеме (Low Volume Pullback).
    В тренде откат на низком объеме - признак слабости коррекции и продолжения тренда.
    
    Логика:
    - Определяем тренд по SMA (price > sma_20 для восходящего, price < sma_20 для нисходящего)
    - Проверяем, что текущая свеча - откат (цена движется против тренда)
    - Проверяем, что объем ниже среднего (volume_ratio < 0.7)
    - Сила сигнала зависит от того, насколько низкий объем
    """
    
    name = "VOL_Low_Pullback"
    category = "Volume"
    allowed_trend_regimes = ["TRENDING"]  # Работает только в тренде
    allowed_vol_regimes = ["LOW_VOL", "NORMAL_VOL"]
    
    def get_thresholds(self, timeframe: str) -> Dict[str, float]:
        """Пороги для разных таймфреймов."""
        return {
            '30min': {'low_volume_ratio': 0.6, 'min_trend_strength': 0.02},
            '1h': {'low_volume_ratio': 0.7, 'min_trend_strength': 0.02},
            '2h': {'low_volume_ratio': 0.7, 'min_trend_strength': 0.02},
            '4h': {'low_volume_ratio': 0.7, 'min_trend_strength': 0.03},
            '1d': {'low_volume_ratio': 0.7, 'min_trend_strength': 0.03},
            '1w': {'low_volume_ratio': 0.8, 'min_trend_strength': 0.05},
        }.get(timeframe, {'low_volume_ratio': 0.7, 'min_trend_strength': 0.02})
    
    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        thresholds = self.get_thresholds(context.timeframe)
        low_volume_ratio = thresholds.get('low_volume_ratio', 0.7)
        min_trend_strength = thresholds.get('min_trend_strength', 0.02)
        
        # 1. Проверяем наличие необходимых данных
        volume_ratio = current_row.get('volume_ratio')
        price = current_row.get('close')
        sma_20 = current_row.get('sma_20')
        
        if pd.isna(volume_ratio) or pd.isna(price) or pd.isna(sma_20):
            return None
        
        # 2. Проверяем низкий объем
        if volume_ratio >= low_volume_ratio:
            return None
        
        # 3. Определяем тренд и проверяем откат
        trend_strength = (price - sma_20) / sma_20 if sma_20 != 0 else 0
        
        if abs(trend_strength) < min_trend_strength:
            # Тренд слишком слабый
            return None
        
        # 4. Проверяем, что это откат (цена движется против тренда)
        # Для этого смотрим на предыдущую свечу
        if len(history) < 2:
            return None
        
        prev_row = history.iloc[-2]
        prev_close = prev_row.get('close')
        current_open = current_row.get('open')
        
        if pd.isna(prev_close) or pd.isna(current_open):
            return None
        
        if trend_strength > 0:
            # Восходящий тренд - откат это падение цены
            if current_open >= prev_close:
                # Цена не падает - не откат
                return None
            
            direction = 'BUY'  # Ожидание продолжения тренда вверх
            strength = min((1.0 - volume_ratio) * 0.8, 0.9)
            reason = f"Откат в восходящем тренде на низком объеме (ratio={volume_ratio:.2f})"
        else:
            # Нисходящий тренд - откат это рост цены
            if current_open <= prev_close:
                # Цена не растет - не откат
                return None
            
            direction = 'SELL'  # Ожидание продолжения тренда вниз
            strength = min((1.0 - volume_ratio) * 0.8, 0.9)
            reason = f"Откат в нисходящем тренде на низком объеме (ratio={volume_ratio:.2f})"
        
        return {
            'direction': direction,
            'strength': round(strength, 2),
            'reason': reason
        }