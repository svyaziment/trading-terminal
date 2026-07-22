"""
Indicators Manager.

Reads candles from trading.candles_aggregated,
calculates technical indicators and stores them in trading.indicators.

Adapted from AlgoTerminal src/core/indicators_manager.py
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.core.config_manager import setup_logger
from app.db.db_manager import DBManager

logger = setup_logger("IndicatorsManager")

SCHEMA = "trading"
TABLE_NAME = "indicators"

TIMEFRAMES = ["30min", "1h", "4h", "1d", "1w", "1M"]

TIMEFRAME_CONFIG: Dict[str, Dict[str, Any]] = {
    "30min": {
        "sma_periods": [5, 10, 20, 50],
        "ema_periods": [12, 26],
        "rsi_periods": [14],
        "bb_period": 20,
        "atr_period": 14,
        "volume_period": 20,
        "days_back": 25,
        "label": "30 минут",
    },
    "1h": {
        "sma_periods": [5, 10, 20, 50, 100, 200],
        "ema_periods": [12, 26, 50],
        "rsi_periods": [14, 21],
        "bb_period": 20,
        "atr_period": 14,
        "volume_period": 20,
        "days_back": 90,
        "label": "1 час",
    },
    "4h": {
        "sma_periods": [5, 10, 20, 50, 100, 200],
        "ema_periods": [12, 26, 50],
        "rsi_periods": [14, 21],
        "bb_period": 20,
        "atr_period": 14,
        "volume_period": 20,
        "days_back": 180,
        "label": "4 часа",
    },
    "1d": {
        "sma_periods": [5, 10, 20, 50, 100, 200],
        "ema_periods": [12, 26, 50],
        "rsi_periods": [14, 21],
        "bb_period": 20,
        "atr_period": 14,
        "volume_period": 20,
        "days_back": 365,
        "label": "1 день",
    },
    "1w": {
        "sma_periods": [5, 10, 20, 50, 100, 200],
        "ema_periods": [12, 26, 50],
        "rsi_periods": [14, 21],
        "bb_period": 20,
        "atr_period": 14,
        "volume_period": 20,
        "days_back": 730,
        "label": "1 неделя",
    },
    "1M": {
        "sma_periods": [5, 10, 20, 50],
        "ema_periods": [12, 26],
        "rsi_periods": [14],
        "bb_period": 20,
        "atr_period": 14,
        "volume_period": 20,
        "days_back": 1095,
        "label": "1 месяц",
    },
}

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.{TABLE_NAME} (
    ticker          VARCHAR(20)    NOT NULL,
    figi            VARCHAR(50)    NOT NULL,
    timeframe       VARCHAR(10)    NOT NULL,
    timestamp       TIMESTAMP      NOT NULL,

    open            DECIMAL(20,10),
    high            DECIMAL(20,10),
    low             DECIMAL(20,10),
    close           DECIMAL(20,10),
    volume          BIGINT,

    sma_5           DECIMAL(20,10),
    sma_10          DECIMAL(20,10),
    sma_20          DECIMAL(20,10),
    sma_50          DECIMAL(20,10),
    sma_100         DECIMAL(20,10),
    sma_200         DECIMAL(20,10),

    ema_12          DECIMAL(20,10),
    ema_26          DECIMAL(20,10),
    ema_50          DECIMAL(20,10),

    rsi_14          DECIMAL(10,2),
    rsi_21          DECIMAL(10,2),

    macd            DECIMAL(20,10),
    macd_signal     DECIMAL(20,10),
    macd_histogram  DECIMAL(20,10),

    atr_14          DECIMAL(20,10),

    bb_upper        DECIMAL(20,10),
    bb_middle       DECIMAL(20,10),
    bb_lower        DECIMAL(20,10),
    bb_width        DECIMAL(20,10),
    bb_position     DECIMAL(5,2),

    volume_sma_20   DECIMAL(20,2),
    volume_ratio    DECIMAL(10,2),

    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (ticker, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_indicators_ticker_timeframe
    ON {SCHEMA}.{TABLE_NAME} (ticker, timeframe, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_indicators_figi_timeframe
    ON {SCHEMA}.{TABLE_NAME} (figi, timeframe, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_indicators_timestamp
    ON {SCHEMA}.{TABLE_NAME} (timestamp);
"""


class IndicatorsManager:
    """Calculates and stores technical indicators."""

    def __init__(self) -> None:
        self.db = DBManager()
        self.ensure_table()

    # ------------------------------------------------------------------
    # Table management
    # ------------------------------------------------------------------

    def ensure_table(self) -> None:
        self.db.execute(CREATE_TABLE_SQL)
        logger.info("Table %s.%s ensured", SCHEMA, TABLE_NAME)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_figi(self, ticker: str) -> Optional[str]:
        result = self.db.select(
            "SELECT figi FROM trading.instruments WHERE ticker = %(ticker)s LIMIT 1",
            params={"ticker": ticker},
        )
        df = result.to_dataframe()
        if not df.empty:
            return str(df.iloc[0]["figi"])
        return None

    def _get_candles(
        self,
        ticker: str,
        timeframe: str,
        days_back: int,
    ) -> pd.DataFrame:
        """Read aggregated candles from DB."""
        query = """
            SELECT timestamp, open, high, low, close, volume
            FROM trading.candles_aggregated
            WHERE ticker = %(ticker)s
              AND timeframe = %(timeframe)s
            ORDER BY timestamp
        """
        result = self.db.select(
            query,
            params={"ticker": ticker, "timeframe": timeframe},
        )
        df = result.to_dataframe()

        if df.empty:
            return df

        # Convert Decimal -> float for calculations
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

        # Optionally filter by days_back
        if days_back:
            cutoff = datetime.now() - timedelta(days=days_back)
            df = df[df["timestamp"] >= cutoff].reset_index(drop=True)

        return df

    # ------------------------------------------------------------------
    # Indicator calculations
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_sma(df: pd.DataFrame, period: int) -> pd.Series:
        return df["close"].rolling(window=period).mean()

    @staticmethod
    def calculate_ema(df: pd.DataFrame, period: int) -> pd.Series:
        return df["close"].ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        close = df["close"]
        delta = close.diff()

        gain = pd.Series(index=close.index, dtype=float)
        loss = pd.Series(index=close.index, dtype=float)

        if len(close) <= period:
            return pd.Series(np.nan, index=close.index)

        gain.iloc[period] = delta.iloc[1 : period + 1].clip(lower=0).mean()
        loss.iloc[period] = (-delta.iloc[1 : period + 1].clip(upper=0)).mean()

        for i in range(period + 1, len(close)):
            gain.iloc[i] = (gain.iloc[i - 1] * (period - 1) + max(delta.iloc[i], 0)) / period
            loss.iloc[i] = (loss.iloc[i - 1] * (period - 1) + max(-delta.iloc[i], 0)) / period

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_macd(
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Dict[str, pd.Series]:
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def calculate_bollinger_bands(
        df: pd.DataFrame,
        period: int = 20,
        std: int = 2,
    ) -> Dict[str, pd.Series]:
        sma = df["close"].rolling(window=period).mean()
        std_dev = df["close"].rolling(window=period).std()
        upper = sma + std_dev * std
        lower = sma - std_dev * std
        width = (2 * std_dev * std) / sma * 100
        position = ((df["close"] - lower) / (upper - lower)) * 100
        return {
            "upper": upper,
            "middle": sma,
            "lower": lower,
            "width": width,
            "position": position,
        }

    # ------------------------------------------------------------------
    # Calculate all indicators for a timeframe
    # ------------------------------------------------------------------

    def calculate_all_indicators_for_timeframe(
        self,
        df: pd.DataFrame,
        timeframe: str,
    ) -> pd.DataFrame:
        config = TIMEFRAME_CONFIG.get(timeframe, TIMEFRAME_CONFIG["1h"])

        result = pd.DataFrame(index=df.index)
        result["timestamp"] = df["timestamp"]

        # OHLCV feature vector
        result["open"] = df["open"]
        result["high"] = df["high"]
        result["low"] = df["low"]
        result["close"] = df["close"]
        result["volume"] = df["volume"]

        # SMA
        for period in config["sma_periods"]:
            result[f"sma_{period}"] = self.calculate_sma(df, period)

        # EMA
        for period in config["ema_periods"]:
            result[f"ema_{period}"] = self.calculate_ema(df, period)

        # RSI
        for period in config["rsi_periods"]:
            result[f"rsi_{period}"] = self.calculate_rsi(df, period)

        # MACD
        macd_data = self.calculate_macd(df)
        result["macd"] = macd_data["macd"]
        result["macd_signal"] = macd_data["signal"]
        result["macd_histogram"] = macd_data["histogram"]

        # ATR
        result[f"atr_{config['atr_period']}"] = self.calculate_atr(df, config["atr_period"])

        # Bollinger Bands
        bb = self.calculate_bollinger_bands(df, config["bb_period"])
        result["bb_upper"] = bb["upper"]
        result["bb_middle"] = bb["middle"]
        result["bb_lower"] = bb["lower"]
        result["bb_width"] = bb["width"]
        result["bb_position"] = bb["position"]

        # Volume
        vol_period = config["volume_period"]
        result["volume_sma_20"] = df["volume"].rolling(window=vol_period).mean()
        result["volume_ratio"] = df["volume"] / result["volume_sma_20"]

        return result

    # ------------------------------------------------------------------
    # Save / update
    # ------------------------------------------------------------------

    def save_indicators(
        self,
        ticker: str,
        figi: str,
        timeframe: str,
        df_indicators: pd.DataFrame,
    ) -> int:
        if df_indicators.empty:
            return 0

        df = df_indicators.copy()
        df["ticker"] = ticker
        df["figi"] = figi
        df["timeframe"] = timeframe

        # Drop rows where key indicators are NaN (not enough data)
        df = df.dropna(subset=["sma_20"])
        if df.empty:
            return 0

        min_date = df["timestamp"].min()
        max_date = df["timestamp"].max()

        # Delete existing rows in range (idempotent)
        self.db.execute(
            f"""
            DELETE FROM {SCHEMA}.{TABLE_NAME}
            WHERE ticker = %(ticker)s
              AND timeframe = %(timeframe)s
              AND timestamp >= %(min_date)s
              AND timestamp <= %(max_date)s
            """,
            params={
                "ticker": ticker,
                "timeframe": timeframe,
                "min_date": min_date,
                "max_date": max_date,
            },
        )

        self.db.insert_with_schema(TABLE_NAME, df)
        logger.info(
            "Saved %d indicators for %s (%s)",
            len(df),
            ticker,
            timeframe,
        )
        return len(df)

    def update_indicators_for_ticker(
        self,
        ticker: str,
        timeframe: str,
        days_back: Optional[int] = None,
    ) -> int:
        figi = self.get_figi(ticker)
        if not figi:
            logger.warning("FIGI not found for %s", ticker)
            return 0

        config = TIMEFRAME_CONFIG.get(timeframe, TIMEFRAME_CONFIG["1h"])
        if days_back is None:
            days_back = config["days_back"]

        df = self._get_candles(ticker, timeframe, days_back)
        if df.empty:
            logger.warning("No candles for %s (%s)", ticker, timeframe)
            return 0

        min_required = max(config["sma_periods"]) if config["sma_periods"] else 20
        if len(df) < min_required:
            logger.warning(
                "Not enough data for %s (%s): need %d, got %d",
                ticker,
                timeframe,
                min_required,
                len(df),
            )
            return 0

        df_indicators = self.calculate_all_indicators_for_timeframe(df, timeframe)
        return self.save_indicators(ticker, figi, timeframe, df_indicators)

    def update_all_indicators(
        self,
        tickers: List[str],
        timeframes: Optional[List[str]] = None,
        days_back: Optional[int] = None,
        delay_seconds: float = 0.3,
    ) -> Dict[str, Any]:
        if timeframes is None:
            timeframes = TIMEFRAMES

        stats: Dict[str, Any] = {
            "total_tickers": len(tickers),
            "total_timeframes": len(timeframes),
            "success": 0,
            "failed": 0,
            "details": {},
        }

        for ticker in tickers:
            stats["details"][ticker] = {}
            for tf in timeframes:
                try:
                    tf_days = days_back or TIMEFRAME_CONFIG.get(tf, {}).get("days_back", 90)
                    count = self.update_indicators_for_ticker(ticker, tf, tf_days)
                    stats["details"][ticker][tf] = count
                    if count > 0:
                        stats["success"] += 1
                    else:
                        stats["failed"] += 1
                    logger.info("✅ %s (%s): %d indicators", ticker, tf, count)
                except Exception as exc:
                    logger.error("❌ %s (%s): %s", ticker, tf, exc)
                    stats["failed"] += 1
                    stats["details"][ticker][tf] = 0
                time.sleep(delay_seconds)

        logger.info(
            "Indicators done: %d/%d success",
            stats["success"],
            stats["success"] + stats["failed"],
        )
        return stats

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_indicators(
        self,
        ticker: str,
        timeframe: str = "1h",
        limit: int = 100,
        start_date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        query = f"""
            SELECT * FROM {SCHEMA}.{TABLE_NAME}
            WHERE ticker = %(ticker)s AND timeframe = %(timeframe)s
        """
        params: Dict[str, Any] = {"ticker": ticker, "timeframe": timeframe}

        if start_date:
            query += " AND timestamp >= %(start_date)s"
            params["start_date"] = start_date

        query += " ORDER BY timestamp DESC"

        if limit:
            query += " LIMIT %(limit)s"
            params["limit"] = limit

        result = self.db.select(query, params=params)
        return result.to_dataframe()

    def get_latest_indicators(
        self,
        ticker: str,
        timeframe: str = "1h",
    ) -> Dict[str, Any]:
        df = self.get_indicators(ticker, timeframe, limit=1)
        if df.empty:
            return {}
        return df.iloc[0].to_dict()

    def get_indicators_for_tickers(
        self,
        tickers: List[str],
        timeframe: str = "1h",
    ) -> pd.DataFrame:
        if not tickers:
            return pd.DataFrame()

        query = f"""
            SELECT DISTINCT ON (ticker)
                ticker, figi, timeframe, timestamp,
                close,
                sma_5, sma_10, sma_20, sma_50, sma_100, sma_200,
                ema_12, ema_26, rsi_14,
                macd, macd_signal, macd_histogram, atr_14,
                bb_upper, bb_middle, bb_lower, bb_width, bb_position,
                volume_sma_20, volume_ratio
            FROM {SCHEMA}.{TABLE_NAME}
            WHERE ticker = ANY(%(tickers)s)
              AND timeframe = %(timeframe)s
            ORDER BY ticker, timestamp DESC
        """
        result = self.db.select(
            query,
            params={"tickers": tickers, "timeframe": timeframe},
        )
        return result.to_dataframe()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats_by_timeframe(self) -> Dict[str, Any]:
        query = f"""
            SELECT
                timeframe,
                COUNT(*) AS total,
                COUNT(DISTINCT ticker) AS tickers,
                MAX(timestamp) AS last_update
            FROM {SCHEMA}.{TABLE_NAME}
            GROUP BY timeframe
            ORDER BY timeframe
        """
        result = self.db.select(query)
        df = result.to_dataframe()

        stats: Dict[str, Any] = {}
        for _, row in df.iterrows():
            stats[row["timeframe"]] = {
                "count": int(row["total"]),
                "tickers": int(row["tickers"]),
                "last_update": str(row["last_update"]),
            }
        return stats

    def get_indicators_stats(
        self,
        tickers: Optional[List[str]] = None,
        timeframe: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = f"""
            SELECT
                timeframe,
                ticker,
                COUNT(*) AS count,
                MAX(timestamp) AS last_update,
                MIN(timestamp) AS first_update
            FROM {SCHEMA}.{TABLE_NAME}
        """
        params: Dict[str, Any] = {}
        where: List[str] = []

        if tickers:
            where.append("ticker = ANY(%(tickers)s)")
            params["tickers"] = tickers

        if timeframe:
            where.append("timeframe = %(timeframe)s")
            params["timeframe"] = timeframe

        if where:
            query += " WHERE " + " AND ".join(where)

        query += " GROUP BY timeframe, ticker ORDER BY timeframe, ticker"

        result = self.db.select(query, params=params)
        df = result.to_dataframe()

        if df.empty:
            return {}

        stats: Dict[str, Any] = {}
        for _, row in df.iterrows():
            tf = row["timeframe"]
            if tf not in stats:
                stats[tf] = {}
            stats[tf][row["ticker"]] = {
                "count": int(row["count"]),
                "last_update": str(row["last_update"]),
                "first_update": str(row["first_update"]),
            }
        return stats


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Calculate indicators")
    parser.add_argument("--ticker", default="VTBR")
    parser.add_argument("--timeframes", default=",".join(TIMEFRAMES))
    parser.add_argument("--days-back", type=int, default=None)
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args()

    mgr = IndicatorsManager()

    if args.stats_only:
        stats = mgr.get_stats_by_timeframe()
        print("INDICATORS_STATS:")
        for tf, info in stats.items():
            print(f"  {tf}: {info['count']} rows, {info['tickers']} tickers, last={info['last_update']}")
        return

    timeframes = [tf.strip() for tf in args.timeframes.split(",") if tf.strip()]
    count = mgr.update_indicators_for_ticker(
        ticker=args.ticker,
        timeframe=timeframes[0] if len(timeframes) == 1 else "1h",
        days_back=args.days_back,
    )

    # If multiple timeframes, run all
    if len(timeframes) > 1:
        stats = mgr.update_all_indicators(
            tickers=[args.ticker],
            timeframes=timeframes,
            days_back=args.days_back,
            delay_seconds=0.1,
        )
        print(f"INDICATORS_TOTAL_SUCCESS={stats['success']}")
        print(f"INDICATORS_TOTAL_FAILED={stats['failed']}")
    else:
        print(f"INDICATORS_SAVED={count}")

    # Print stats
    stats = mgr.get_stats_by_timeframe()
    print("INDICATORS_STATS:")
    for tf, info in stats.items():
        print(f"  {tf}: {info['count']} rows, {info['tickers']} tickers")


if __name__ == "__main__":
    main()
