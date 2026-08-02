"""
Единый источник контекста для стратегий.
Параметры уровней берутся из нормализованного конфига.
"""
import pandas as pd
from typing import Dict, Any, Optional

from app.analytics.levels_engine import build_levels
from app.analytics.pattern_registry import normalize_patterns


def build_strategy_context(
    db: Any,
    ticker: str,
    config: Dict[str, Any],
    df_4h: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Строит контекст для стратегии на основе параметров паттерна levels_reversal.
    """
    cfg = normalize_patterns(config)
    levels_params = cfg.get('patterns', {}).get('levels_reversal', {})
    if not levels_params:
        from app.analytics.pattern_registry import get_pattern_defaults
        levels_params = get_pattern_defaults('levels_reversal')
    
    level_timeframe = levels_params.get('level_timeframe', '4h')
    level_method = levels_params.get('level_method', 'include_swing_include_impulse')
    swing_windows = levels_params.get('swing_windows', [10])
    body_ratio = levels_params.get('impulse_body_ratio', 0.7)
    impulse_atr_mult = levels_params.get('impulse_atr_mult', 1.5)
    zone_atr_mult = levels_params.get('zone_atr_mult', 0.5)
    confirm_windows = levels_params.get('confirm_windows', [10])
    
    if df_4h is None:
        df_4h = _load_candles(db, ticker, level_timeframe)
    
    levels = build_levels(
        df_4h,
        swing_windows=swing_windows,
        body_ratio=body_ratio,
        impulse_atr_mult=impulse_atr_mult,
        zone_atr_mult=zone_atr_mult,
    )
    
    return {
        'levels': levels,
        'confirm_windows': confirm_windows,
        'timeframe': level_timeframe,
        'df_4h': df_4h,
        'params': {
            'level_timeframe': level_timeframe,
            'level_method': level_method,
            'swing_windows': swing_windows,
            'body_ratio': body_ratio,
            'impulse_atr_mult': impulse_atr_mult,
            'zone_atr_mult': zone_atr_mult,
            'confirm_windows': confirm_windows,
        }
    }


def _load_candles(db: Any, ticker: str, timeframe: str) -> pd.DataFrame:
    """Загружает свечи для указанного таймфрейма."""
    import pandas as pd
    return pd.DataFrame()
