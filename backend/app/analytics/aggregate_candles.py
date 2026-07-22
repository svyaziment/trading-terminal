from app.db.db_manager import DBManager

TIMEFRAMES = ["30min", "1h", "4h", "1d", "1w", "1M"]

BUCKET_EXPR = {
    "30min": "timestamp",
    "1h": "date_trunc('hour', timestamp)",
    "4h": "date_trunc('hour', timestamp) - (EXTRACT(hour FROM timestamp)::int %% 4) * interval '1 hour'",
    "1d": "date_trunc('day', timestamp)",
    "1w": "date_trunc('week', timestamp)",
    "1M": "date_trunc('month', timestamp)",
}


def aggregate_timeframe(db: DBManager, timeframe: str) -> int:
    bucket_expr = BUCKET_EXPR[timeframe]

    delete_sql = f"""
        DELETE FROM trading.candles_aggregated
        WHERE timeframe = %s
          AND timestamp IN (
              SELECT DISTINCT {bucket_expr}
              FROM trading.candles_30min_raw
              WHERE timestamp IS NOT NULL
          )
    """
    db.execute(delete_sql, (timeframe,))

    insert_sql = f"""
        INSERT INTO trading.candles_aggregated (
            ticker,
            figi,
            timestamp,
            timeframe,
            open,
            high,
            low,
            close,
            volume,
            created_at
        )
        SELECT
            ticker,
            figi,
            {bucket_expr} AS timestamp,
            %s,
            (array_agg(open ORDER BY timestamp ASC))[1],
            max(high),
            min(low),
            (array_agg(close ORDER BY timestamp DESC))[1],
            coalesce(sum(volume), 0),
            now()
        FROM trading.candles_30min_raw
        WHERE timestamp IS NOT NULL
        GROUP BY
            ticker,
            figi,
            {bucket_expr}
    """
    return db.execute(insert_sql, (timeframe,))


def aggregate_all() -> None:
    db = DBManager()

    raw = db.select("SELECT count(*) AS cnt FROM trading.candles_30min_raw")
    raw_df = raw.to_dataframe()
    raw_count = int(raw_df.iloc[0]["cnt"]) if not raw_df.empty else 0
    print(f"RAW_COUNT={raw_count}")

    for timeframe in TIMEFRAMES:
        count = aggregate_timeframe(db, timeframe)
        key = timeframe.upper().replace("MIN", "MIN")
        print(f"AGG_{key}={count}")

    db.close_pool()


if __name__ == "__main__":
    aggregate_all()
