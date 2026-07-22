# src/core/signal_patterns/patterns/price_action.py
"""
Price Action patterns.
Анализируют структуру свечей для определения краткосрочных разворотов и продолжения трендов.
"""
import pandas as pd
from typing import Dict, Optional, Any
from src.core.signal_patterns.base import BasePattern, MarketContext


class PA_Hammer(BasePattern):
    """
    Паттерн "Молот" (Hammer).
    Сильный бычий разворотный паттерн в нисходящем тренде.
    """
    
    name = "PA_Hammer"
    category = "PriceAction"
    allowed_trend_regimes = ["RANGING", "TRENDING"]
    allowed_vol_regimes = ["LOW_VOL", "NORMAL_VOL", "HIGH_VOL"]
    
    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        # 1. Проверяем наличие необходимых данных
        if pd.isna(current_row.get('open')) or pd.isna(current_row.get('high')) or \
           pd.isna(current_row.get('low')) or pd.isna(current_row.get('close')):
            return None
        
        # 2. Вычисляем параметры свечи
        body = abs(current_row['close'] - current_row['open'])
        upper_shadow = current_row['high'] - max(current_row['open'], current_row['close'])
        lower_shadow = min(current_row['open'], current_row['close']) - current_row['low']
        
        # 3. Проверяем условия паттерна
        if body == 0 or lower_shadow == 0:
            return None
        
        # Длинная нижняя тень (минимум в 2 раза длиннее тела)
        if lower_shadow < 2 * body:
            return None
        
        # Короткая верхняя тень
        if upper_shadow > 0.5 * body:
            return None
        
        # Закрытие в верхней части тела
        if current_row['close'] < current_row['open']:
            return None
        
        # 4. Определяем силу (защищенное деление)
        strength = min((lower_shadow / body) * 0.3, 1.0) if body > 0 else 0.0
        reason = f"Молот: нижняя тень={lower_shadow:.2f}, тело={body:.2f}, закрытие в верхней части"
        
        return {
            'direction': 'BUY',
            'strength': round(strength, 2),
            'reason': reason
        }


class PA_HangingMan(BasePattern):
    """
    Паттерн "Повешенный" (Hanging Man).
    Сильный медвежий разворотный паттерн в восходящем тренде.
    """
    
    name = "PA_HangingMan"
    category = "PriceAction"
    allowed_trend_regimes = ["RANGING", "TRENDING"]
    allowed_vol_regimes = ["LOW_VOL", "NORMAL_VOL", "HIGH_VOL"]
    
    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        # 1. Проверяем наличие необходимых данных
        if pd.isna(current_row.get('open')) or pd.isna(current_row.get('high')) or \
           pd.isna(current_row.get('low')) or pd.isna(current_row.get('close')):
            return None
        
        # 2. Вычисляем параметры свечи
        body = abs(current_row['close'] - current_row['open'])
        upper_shadow = current_row['high'] - max(current_row['open'], current_row['close'])
        lower_shadow = min(current_row['open'], current_row['close']) - current_row['low']
        
        # 3. Проверяем условия паттерна
        if body == 0 or lower_shadow == 0:
            return None
        
        # Длинная нижняя тень (минимум в 2 раза длиннее тела)
        if lower_shadow < 2 * body:
            return None
        
        # Короткая верхняя тень
        if upper_shadow > 0.5 * body:
            return None
        
        # Закрытие в нижней части тела
        if current_row['close'] > current_row['open']:
            return None
        
        # 4. Определяем силу (защищенное деление)
        strength = min((lower_shadow / body) * 0.3, 1.0) if body > 0 else 0.0
        reason = f"Повешенный: нижняя тень={lower_shadow:.2f}, тело={body:.2f}, закрытие в нижней части"
        
        return {
            'direction': 'SELL',
            'strength': round(strength, 2),
            'reason': reason
        }


class PA_Engulfing(BasePattern):
    """
    Паттерн "Поглощение" (Engulfing).
    Бычье и медвежье поглощение - сильные разворотные паттерны.
    """
    
    name = "PA_Engulfing"
    category = "PriceAction"
    allowed_trend_regimes = ["RANGING", "TRENDING"]
    allowed_vol_regimes = ["LOW_VOL", "NORMAL_VOL", "HIGH_VOL"]
    
    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        # 1. Нужно минимум 2 свечи
        if len(history) < 2:
            return None
        
        # 2. Проверяем наличие необходимых данных
        prev_row = history.iloc[-2]
        if pd.isna(current_row.get('open')) or pd.isna(current_row.get('close')) or \
           pd.isna(prev_row.get('open')) or pd.isna(prev_row.get('close')):
            return None
        
        # 3. Вычисляем параметры
        current_body = abs(current_row['close'] - current_row['open'])
        prev_body = abs(prev_row['close'] - prev_row['open'])
        
        # 4. Проверяем условия паттерна
        # Текущая свеча должна поглощать предыдущую
        if current_body < prev_body:
            return None
        
        # КРИТИЧНО: защита от деления на ноль
        if prev_body == 0:
            return None
        
        # Текущая свеча должна полностью покрывать тело предыдущей
        if current_row['open'] <= prev_row['open'] and current_row['close'] >= prev_row['close'] and \
           current_row['close'] > current_row['open']:
            # Бычье поглощение
            strength = min((current_body / prev_body) * 0.4, 1.0)
            reason = f"Бычье поглощение: текущее тело={current_body:.2f}, предыдущее={prev_body:.2f}"
            return {
                'direction': 'BUY',
                'strength': round(strength, 2),
                'reason': reason
            }
        
        elif current_row['open'] >= prev_row['open'] and current_row['close'] <= prev_row['close'] and \
             current_row['close'] < current_row['open']:
            # Медвежье поглощение
            strength = min((current_body / prev_body) * 0.4, 1.0)
            reason = f"Медвежье поглощение: текущее тело={current_body:.2f}, предыдущее={prev_body:.2f}"
            return {
                'direction': 'SELL',
                'strength': round(strength, 2),
                'reason': reason
            }
        
        return None


class PA_ThreeWhiteSoldiers(BasePattern):
    """
    Паттерн "Три белых солдата" (Three White Soldiers).
    Сильный бычий паттерн, показывающий продолжение восходящего тренда.
    """
    
    name = "PA_ThreeWhiteSoldiers"
    category = "PriceAction"
    allowed_trend_regimes = ["TRENDING"]
    allowed_vol_regimes = ["LOW_VOL", "NORMAL_VOL", "HIGH_VOL"]
    
    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        # 1. Нужно минимум 3 свечи
        if len(history) < 3:
            return None
        
        # 2. Проверяем наличие необходимых данных
        if pd.isna(current_row.get('open')) or pd.isna(current_row.get('close')):
            return None
        
        # 3. Берем три последние свечи
        last_three = history.iloc[-3:]
        
        # 4. Проверяем условия
        # Все свечи должны быть бычьими
        if not all(row['close'] > row['open'] for _, row in last_three.iterrows()):
            return None
        
        # Закрытие каждой свечи выше предыдущего закрытия
        if not (last_three.iloc[1]['close'] > last_three.iloc[0]['close'] and 
                last_three.iloc[2]['close'] > last_three.iloc[1]['close']):
            return None
        
        # Небольшие тени (отношение тела к диапазону свечи > 0.7)
        for _, row in last_three.iterrows():
            body = abs(row['close'] - row['open'])
            range_ = row['high'] - row['low']
            
            # КРИТИЧНО: защита от деления на ноль
            if range_ == 0 or body / range_ < 0.7:
                return None
        
        # 5. Определяем силу
        strength = 0.8  # Сильный сигнал
        reason = "Три белых солдата: три последовательные бычьи свечи с растущим закрытием"
        
        return {
            'direction': 'BUY',
            'strength': round(strength, 2),
            'reason': reason
        }


class PA_ThreeBlackCrows(BasePattern):
    """
    Паттерн "Три черных ворона" (Three Black Crows).
    Сильный медвежий паттерн, показывающий продолжение нисходящего тренда.
    """
    
    name = "PA_ThreeBlackCrows"
    category = "PriceAction"
    allowed_trend_regimes = ["TRENDING"]
    allowed_vol_regimes = ["LOW_VOL", "NORMAL_VOL", "HIGH_VOL"]
    
    def evaluate(self, current_row: pd.Series, history: pd.DataFrame, context: MarketContext) -> Optional[Dict]:
        # 1. Нужно минимум 3 свечи
        if len(history) < 3:
            return None
        
        # 2. Проверяем наличие необходимых данных
        if pd.isna(current_row.get('open')) or pd.isna(current_row.get('close')):
            return None
        
        # 3. Берем три последние свечи
        last_three = history.iloc[-3:]
        
        # 4. Проверяем условия
        # Все свечи должны быть медвежьими
        if not all(row['close'] < row['open'] for _, row in last_three.iterrows()):
            return None
        
        # Закрытие каждой свечи ниже предыдущего закрытия
        if not (last_three.iloc[1]['close'] < last_three.iloc[0]['close'] and 
                last_three.iloc[2]['close'] < last_three.iloc[1]['close']):
            return None
        
        # Небольшие тени (отношение тела к диапазону свечи > 0.7)
        for _, row in last_three.iterrows():
            body = abs(row['close'] - row['open'])
            range_ = row['high'] - row['low']
            
            # КРИТИЧНО: защита от деления на ноль
            if range_ == 0 or body / range_ < 0.7:
                return None
        
        # 5. Определяем силу
        strength = 0.8  # Сильный сигнал
        reason = "Три черных ворона: три последовательные медвежьи свечи с падающим закрытием"
        
        return {
            'direction': 'SELL',
            'strength': round(strength, 2),
            'reason': reason
        }