"""
Candles aggregator: aggregates 30-minute candles into higher timeframes.

Timeframes:
    1h  - hourly
    4h  - 4-hourly
    1d  - daily
    1w  - weekly
    1M  - monthly
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from app.db.db_manager import DBManager

logger = logging.getLogger(__name__)


class CandlesAggregator:
    """Aggregates 30-minute candles into higher timeframes."""

    TIMEFRAMES = ["1h", "4h", "1d", "1w", "1M"]

    def __init__(self) -> None:
        self.db = DBManager()

    def aggregate_timeframe(
        self,
        ticker: str,
        timeframe: str,
        from_date: datetime,
        to_date: datetime,
    ) -> int:
        """
        Aggregate 30-minute candles into a specific timeframe.

        Uses SQL-based aggregation for efficiency.
        Deletes existing aggregated candles for the range before inserting.

        :param ticker: Ticker symbol
        :param timeframe: Target timeframe (1h, 4h, 1d, 1w, 1M)
        :param from_date: Start date (inclusive)
        :param to_date: End date (exclusive)
        :return: Number of aggregated candles inserted
        """
        if timeframe not in self.TIMEFRAMES:
            raise ValueError(f"Unknown timeframe: {timeframe}. Must be one of {self.TIMEFRAMES}")

        # Bucket expression for each timeframe
        bucket_expr = self._get_bucket_expr(timeframe)

        # Delete existing aggregated candles for this range
        delete_sql = f"""
            DELETE FROM trading.candles_aggregated
            WHERE ticker = %s
              AND timeframe = %s
              AND timestamp >= %s
              AND timestamp < %s
        """
        self.db.execute(delete_sql, (ticker, timeframe, from_date, to_date))

        # Aggregate and insert
        insert_sql = f"""
            INSERT INTO trading.candles_aggregated (
                ticker, figi, timestamp, timeframe,
                open, high, low, close, volume, created_at
            )
            SELECT
                ticker,
                (array_agg(figi ORDER BY timestamp DESC))[1] AS figi,
                {bucket_expr} AS bucket,
                %s AS timeframe,
                (array_agg(open ORDER BY timestamp))[1] AS open,
                max(high) AS high,
                min(low) AS low,
                (array_agg(close ORDER BY timestamp DESC))[1] AS close,
                coalesce(sum(volume), 0) AS volume,
                now() AS created_at
            FROM trading.candles_30min_raw
            WHERE ticker = %s
              AND timestamp >= %s
              AND timestamp < %s
            GROUP BY ticker, {bucket_expr}
            ON CONFLICT (ticker, timestamp, timeframe) DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume, figi = EXCLUDED.figi
        """
        count = self.db.execute(insert_sql, (timeframe, ticker, from_date, to_date))
        logger.info(f"Aggregated {count} {timeframe} candles for {ticker}")
        return count

    def aggregate_all_timeframes(
        self,
        ticker: str,
        from_date: datetime,
        to_date: datetime,
    ) -> dict:
        """
        Aggregate 30-minute candles into all timeframes.

        :param ticker: Ticker symbol
        :param from_date: Start date (inclusive)
        :param to_date: End date (exclusive)
        :return: Dict with counts per timeframe
        """
        results = {}
        for tf in self.TIMEFRAMES:
            count = self.aggregate_timeframe(ticker, tf, from_date, to_date)
            results[tf] = count
        return results

    def aggregate_top30(
        self,
        from_date: datetime,
        to_date: datetime,
    ) -> dict:
        """
        Aggregate candles for top 30 tickers by volume.

        :param from_date: Start date (inclusive)
        :param to_date: End date (exclusive)
        :return: Dict with results per ticker
        """
        # Get top 30 tickers
        sql = """
            SELECT ticker
            FROM trading.top_stocks_by_volume
            ORDER BY rank
            LIMIT 30
        """
        result = self.db.select(sql)
        df = result.to_dataframe()

        if df.empty:
            logger.warning("No tickers found in top_stocks_by_volume")
            return {}

        tickers = df["ticker"].tolist()
        logger.info(f"Aggregating candles for {len(tickers)} tickers")

        results = {}
        for ticker in tickers:
            results[ticker] = self.aggregate_all_timeframes(ticker, from_date, to_date)

        return results

    @staticmethod
    def _get_bucket_expr(timeframe: str) -> str:
        """Get SQL bucket expression for a timeframe."""
        buckets = {
            "1h": "date_trunc('hour', timestamp)",
            "4h": "date_trunc('hour', timestamp) - (EXTRACT(hour FROM timestamp)::int %% 4) * interval '1 hour'",
            "1d": "date_trunc('day', timestamp)",
            "1w": "date_trunc('week', timestamp)",
            "1M": "date_trunc('month', timestamp)",
        }
        return buckets[timeframe]

    def get_stats(self) -> dict:
        """Get aggregation statistics."""
        sql = """
            SELECT
                timeframe,
                count(*) AS count,
                count(DISTINCT ticker) AS tickers,
                min(timestamp) AS min_ts,
                max(timestamp) AS max_ts
            FROM trading.candles_aggregated
            GROUP BY timeframe
            ORDER BY timeframe
        """
        result = self.db.select(sql)
        df = result.to_dataframe()
        return df.to_dict("records") if not df.empty else []
