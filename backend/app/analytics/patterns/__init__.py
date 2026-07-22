"""
Patterns module — адаптировано из старого проекта.
Импортируем реальные классы из файлов.
"""
# Попытка импортировать классы из каждого файла
try:
    from .trend import TrendSMAAlignment as Trend_SMA_Alignment
except ImportError:
    try:
        from .trend import Trend_SMA_Alignment
    except ImportError:
        class Trend_SMA_Alignment:
            def check(self, candle, df, lookback):
                return None

try:
    from .mean_reversion import RSIReversal as MR_RSI_Reversal
except ImportError:
    try:
        from .mean_reversion import MR_RSI_Reversal
    except ImportError:
        class MR_RSI_Reversal:
            def check(self, candle, df, lookback):
                return None

try:
    from .breakout import BBSqueeze as BO_BB_Squeeze
except ImportError:
    try:
        from .breakout import BO_BB_Squeeze
    except ImportError:
        class BO_BB_Squeeze:
            def check(self, candle, df, lookback):
                return None

try:
    from .volume import VolumeSpike as VOL_Spike
except ImportError:
    try:
        from .volume import VOL_Spike
    except ImportError:
        class VOL_Spike:
            def check(self, candle, df, lookback):
                return None

try:
    from .volume import VolumeLowPullback as VOL_Low_Pullback
except ImportError:
    try:
        from .volume import VOL_Low_Pullback
    except ImportError:
        class VOL_Low_Pullback:
            def check(self, candle, df, lookback):
                return None

try:
    from .price_action import Hammer as PA_Hammer
except ImportError:
    try:
        from .price_action import PA_Hammer
    except ImportError:
        class PA_Hammer:
            def check(self, candle, df, lookback):
                return None

try:
    from .price_action import HangingMan as PA_HangingMan
except ImportError:
    try:
        from .price_action import PA_HangingMan
    except ImportError:
        class PA_HangingMan:
            def check(self, candle, df, lookback):
                return None

try:
    from .price_action import Engulfing as PA_Engulfing
except ImportError:
    try:
        from .price_action import PA_Engulfing
    except ImportError:
        class PA_Engulfing:
            def check(self, candle, df, lookback):
                return None

try:
    from .price_action import ThreeWhiteSoldiers as PA_ThreeWhiteSoldiers
except ImportError:
    try:
        from .price_action import PA_ThreeWhiteSoldiers
    except ImportError:
        class PA_ThreeWhiteSoldiers:
            def check(self, candle, df, lookback):
                return None

try:
    from .price_action import ThreeBlackCrows as PA_ThreeBlackCrows
except ImportError:
    try:
        from .price_action import PA_ThreeBlackCrows
    except ImportError:
        class PA_ThreeBlackCrows:
            def check(self, candle, df, lookback):
                return None
