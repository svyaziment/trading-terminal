"""
SignalEngine — обрабатывает данные и применяет набор паттернов.
Адаптирован из старого проекта.
"""
import pandas as pd
from typing import List, Dict, Any, Optional

class SignalEngine:
    def __init__(self, patterns: List[Any]):
        self.patterns = patterns

    def process_dataframe(self, df: pd.DataFrame, timeframe: str, lookback_window: int) -> List[Dict]:
        """
        Применяет все паттерны к каждой свече в DataFrame.
        Возвращает список результатов для каждой свечи.
        """
        results = []
        for idx in range(len(df)):
            candle = df.iloc[idx].to_dict()
            # Приводим типы
            candle['timestamp'] = pd.to_datetime(candle['timestamp'])
            candle['price'] = float(candle.get('close', candle.get('price', 0)))
            triggered = []
            for pattern in self.patterns:
                signal = pattern.check(candle, df.iloc[:idx+1], lookback_window)
                if signal:
                    triggered.append(signal)
            summary = {
                'buy_signals': sum(1 for s in triggered if s['direction'] == 'buy'),
                'sell_signals': sum(1 for s in triggered if s['direction'] == 'sell'),
                'total_patterns': len(triggered)
            }
            results.append({
                'candle': candle,
                'triggered_patterns': triggered,
                'summary': summary
            })
        return results
