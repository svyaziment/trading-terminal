"""
Генератор торговых сигналов на основе SignalEngine и паттернов.
Адаптирован из старого проекта AlgoTerminal.
"""
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import logging
import json
import traceback

from app.db.db_manager import DBManager
from app.analytics.indicators_manager import IndicatorsManager

# Импорт паттернов из папки patterns (будут созданы отдельно)
from app.analytics.patterns import (
    Trend_SMA_Alignment,
    MR_RSI_Reversal,
    BO_BB_Squeeze,
    VOL_Spike,
    VOL_Low_Pullback,
    PA_Hammer,
    PA_HangingMan,
    PA_Engulfing,
    PA_ThreeWhiteSoldiers,
    PA_ThreeBlackCrows
)

# Импорт движка (будет создан в отдельном файле)
from app.analytics.signal_engine import SignalEngine

logger = logging.getLogger(__name__)

class SignalGenerator:
    """Генератор торговых сигналов на основе SignalEngine."""

    def __init__(self):
        self.db = DBManager()
        self.indicators_manager = IndicatorsManager()
        self._ensure_signals_table()

        # Инициализируем движок с нашими паттернами
        self.engine = SignalEngine(patterns=[
            Trend_SMA_Alignment(),
            MR_RSI_Reversal(),
            BO_BB_Squeeze(),
            VOL_Spike(),
            VOL_Low_Pullback(),
            PA_Hammer(),
            PA_HangingMan(),
            PA_Engulfing(),
            PA_ThreeWhiteSoldiers(),
            PA_ThreeBlackCrows()
        ])

        # Счетчики для статистики
        self.stats = {
            "db_insert_success": 0,
            "db_insert_duplicate": 0,
            "db_insert_error": 0,
            "total_errors": 0
        }

    def _ensure_signals_table(self):
        """Создает таблицу для сигналов, если её нет."""
        try:
            check_query = """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'trading' AND table_name = 'signals'
                )
            """
            result = self.db.select(check_query)
            df = result.to_dataframe()
            if df.iloc[0]['exists']:
                logger.info("✅ Таблица trading.signals уже существует")
                return

            # Создаём таблицу, если её нет
            create_query = """
                CREATE TABLE IF NOT EXISTS trading.signals (
                    ticker VARCHAR(20) NOT NULL,
                    timeframe VARCHAR(10) NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    signal VARCHAR(10) NOT NULL,
                    confidence FLOAT,
                    price FLOAT,
                    rsi FLOAT,
                    macd FLOAT,
                    bb_position FLOAT,
                    volume_ratio FLOAT,
                    atr_pct FLOAT,
                    summary TEXT,
                    buy_signals INT,
                    sell_signals INT,
                    total_signals INT,
                    PRIMARY KEY (ticker, timeframe, timestamp, signal)
                )
            """
            self.db.execute(create_query)
            logger.info("✅ Таблица trading.signals создана")
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке/создании таблицы сигналов: {e}")
            raise

    def get_top_tickers(self, limit: int = 30) -> List[str]:
        """Получает список ТОП тикеров по объему."""
        query = """
            SELECT ticker FROM trading.top_stocks_by_volume
            ORDER BY rank ASC LIMIT %(limit)s
        """
        result = self.db.select(query, params={'limit': limit})
        df = result.to_dataframe()
        return df['ticker'].tolist() if not df.empty else []

    def scan_and_save_signals(self, tickers: List[str], timeframes: List[str] = None, lookback: int = 1000) -> Dict[str, Any]:
        """
        Сканирует тикеры на указанных таймфреймах за ВСЮ доступную историю и сохраняет сигналы в БД.
        Возвращает детальный отчет о сканировании.
        """
        if timeframes is None:
            timeframes = ['30min', '1h', '4h', '1d']

        logger.info(f"🚀 Начало сканирования {len(tickers)} тикеров на таймфреймах {timeframes}...")
        logger.info(f" Анализ последних {lookback} свечей для каждого тикера/таймфрейма")

        self.stats = {"db_insert_success": 0, "db_insert_duplicate": 0, "db_insert_error": 0, "total_errors": 0}

        report = {
            "scan_started_at": datetime.now().isoformat(),
            "tickers_count": len(tickers),
            "timeframes": timeframes,
            "lookback": lookback,
            "total_signals_saved": 0,
            "total_candles_analyzed": 0,
            "tickers_scanned": [],
            "signals": [],
            "errors": [],
            "pattern_statistics": {},
            "db_statistics": {"insert_success": 0, "insert_duplicate": 0, "insert_error": 0}
        }

        for ticker in tickers:
            ticker_report = {"ticker": ticker, "timeframes_scanned": [], "signals_found": 0, "errors": []}

            for timeframe in timeframes:
                timeframe_report = {
                    "timeframe": timeframe,
                    "candles_analyzed": 0,
                    "signals_found": 0,
                    "patterns_triggered": {},
                    "db_inserts": {"success": 0, "duplicate": 0, "error": 0}
                }

                try:
                    df = self.indicators_manager.get_indicators(ticker=ticker, timeframe=timeframe, limit=lookback)
                    if df.empty:
                        logger.warning(f"⚠️ {ticker} ({timeframe}): нет данных")
                        continue
                    if len(df) < 20:
                        logger.warning(f"⚠️ {ticker} ({timeframe}): мало данных ({len(df)} свечей)")
                        continue

                    df = df.sort_values('timestamp').reset_index(drop=True)
                    logger.info(f"📈 {ticker} ({timeframe}): {len(df)} свечей для анализа")
                    timeframe_report["candles_analyzed"] = len(df)
                    report["total_candles_analyzed"] += len(df)

                    results = self.engine.process_dataframe(df, timeframe=timeframe, lookback_window=len(df))

                    for res in results:
                        if not res['triggered_patterns']:
                            continue
                        candle = res['candle']
                        for pattern in res['triggered_patterns']:
                            ts = candle['timestamp']
                            if hasattr(ts, 'isoformat'):
                                ts = ts.isoformat()

                            signal_data = {
                                'ticker': str(ticker),
                                'timeframe': str(timeframe),
                                'timestamp': ts,
                                'signal': str(pattern['direction']),
                                'confidence': float(pattern['strength']) if pattern['strength'] is not None else None,
                                'price': float(candle['price']) if candle['price'] is not None else None,
                                'rsi': float(candle.get('rsi_14')) if candle.get('rsi_14') is not None else None,
                                'macd': float(candle.get('macd')) if candle.get('macd') is not None else None,
                                'bb_position': float(candle.get('bb_position')) if candle.get('bb_position') is not None else None,
                                'volume_ratio': float(candle.get('volume_ratio')) if candle.get('volume_ratio') is not None else None,
                                'atr_pct': float(candle.get('atr_pct')) if candle.get('atr_pct') is not None else None,
                                'summary': str(f"{pattern['name']}: {pattern['reason']}"),
                                'buy_signals': int(res['summary'].get('buy_signals', 0)),
                                'sell_signals': int(res['summary'].get('sell_signals', 0)),
                                'total_signals': int(res['summary'].get('total_patterns', 0))
                            }

                            save_result = self.save_signal_to_db(signal_data)

                            if save_result[0] == 'success':
                                report["total_signals_saved"] += 1
                                timeframe_report["signals_found"] += 1
                                ticker_report["signals_found"] += 1
                                timeframe_report["db_inserts"]["success"] += 1
                                self.stats["db_insert_success"] += 1
                                report["signals"].append(signal_data)

                                pattern_name = pattern['name']
                                if pattern_name not in timeframe_report["patterns_triggered"]:
                                    timeframe_report["patterns_triggered"][pattern_name] = 0
                                timeframe_report["patterns_triggered"][pattern_name] += 1

                            elif save_result[0] == 'duplicate':
                                timeframe_report["db_inserts"]["duplicate"] += 1
                                self.stats["db_insert_duplicate"] += 1
                            elif save_result[0] == 'error':
                                timeframe_report["db_inserts"]["error"] += 1
                                self.stats["db_insert_error"] += 1

                    if timeframe_report["signals_found"] > 0:
                        logger.info(f"✅ {ticker} ({timeframe}): найдено {timeframe_report['signals_found']} сигналов")

                    ticker_report["timeframes_scanned"].append(timeframe_report)

                except Exception as e:
                    error_msg = f"❌ Ошибка при сканировании {ticker} ({timeframe}): {e}"
                    logger.error(error_msg)
                    error_report = {
                        "ticker": ticker,
                        "timeframe": timeframe,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now().isoformat()
                    }
                    report["errors"].append(error_report)
                    ticker_report["errors"].append(error_report)
                    self.stats["total_errors"] += 1
                    continue

            report["tickers_scanned"].append(ticker_report)

        report["scan_finished_at"] = datetime.now().isoformat()

        for ticker_report in report["tickers_scanned"]:
            for tf_report in ticker_report["timeframes_scanned"]:
                for pattern_name, count in tf_report["patterns_triggered"].items():
                    if pattern_name not in report["pattern_statistics"]:
                        report["pattern_statistics"][pattern_name] = 0
                    report["pattern_statistics"][pattern_name] += count

        report["db_statistics"] = {
            "insert_success": self.stats["db_insert_success"],
            "insert_duplicate": self.stats["db_insert_duplicate"],
            "insert_error": self.stats["db_insert_error"]
        }

        logger.info(f"✅ Сканирование завершено. Всего сохранено сигналов: {report['total_signals_saved']}")
        return report

    def save_signal_to_db(self, signal_data: Dict) -> Tuple[str, Optional[str]]:
        """Сохраняет один сигнал в БД. Возвращает (статус, сообщение_об_ошибке)."""
        safe_data = {}
        for key, value in signal_data.items():
            if value is None:
                safe_data[key] = None
            elif hasattr(value, 'item'):
                safe_data[key] = value.item()
            elif hasattr(value, 'tolist'):
                safe_data[key] = value.tolist()
            else:
                safe_data[key] = value

        query = """
            INSERT INTO trading.signals (
                ticker, timeframe, timestamp, signal, confidence, price,
                rsi, macd, bb_position, volume_ratio, atr_pct,
                summary, buy_signals, sell_signals, total_signals
            ) VALUES (
                %(ticker)s, %(timeframe)s, %(timestamp)s, %(signal)s, %(confidence)s, %(price)s,
                %(rsi)s, %(macd)s, %(bb_position)s, %(volume_ratio)s, %(atr_pct)s,
                %(summary)s, %(buy_signals)s, %(sell_signals)s, %(total_signals)s
            )
            ON CONFLICT (ticker, timeframe, timestamp, signal) DO NOTHING
        """
        try:
            self.db.execute(query, params=safe_data)
            return ('success', None)
        except Exception as e:
            error_str = str(e)
            if 'duplicate' in error_str.lower() or 'conflict' in error_str.lower() or 'unique' in error_str.lower():
                return ('duplicate', None)
            else:
                logger.error(f"❌ Ошибка сохранения сигнала {safe_data['ticker']}: {error_str}")
                return ('error', error_str)

    def close(self):
        self.db.close_pool()
