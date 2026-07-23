import datetime
import decimal
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query


def serialize_value(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


def result_to_records(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    columns = result.get("columns", [])
    data = result.get("data", [])
    records = []

    for row in data:
        record = {}
        for column, value in zip(columns, row):
            record[column] = serialize_value(value)
        records.append(record)

    return records


def get_db():
    try:
        from app.db.db_manager import DBManager

        return DBManager()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {exc}",
        )


def register_routes(app: FastAPI) -> None:
    @app.get("/api/instruments")
    def get_instruments(
        limit: int = Query(100, ge=1, le=1000),
        ticker: Optional[str] = Query(None),
        figi: Optional[str] = Query(None),
        exchange: Optional[str] = Query(None),
        instrument_type: Optional[str] = Query(None),
    ):
        clauses = []
        params: Dict[str, Any] = {}

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        if figi:
            clauses.append("figi = %(figi)s")
            params["figi"] = figi

        if exchange:
            clauses.append("exchange = %(exchange)s")
            params["exchange"] = exchange

        if instrument_type:
            clauses.append("instrument_type = %(instrument_type)s")
            params["instrument_type"] = instrument_type

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT
                figi,
                ticker,
                name,
                instrument_type,
                class_code,
                currency,
                lot_size,
                min_price_increment,
                is_tradable,
                exchange,
                country_of_risk,
                created_at,
                updated_at
            FROM trading.instruments
            {where}
            ORDER BY ticker
            LIMIT %(limit)s
        """

        params["limit"] = limit

        try:
            db = get_db()
            result = db.select(query, params)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        items = result_to_records(result)

        return {
            "items": items,
            "count": len(items),
        }

    @app.get("/api/candles")
    def get_candles(
        limit: int = Query(100, ge=1, le=5000),
        ticker: Optional[str] = Query(None),
        figi: Optional[str] = Query(None),
        timeframe: Optional[str] = Query(None),
    ):
        clauses = []
        params: Dict[str, Any] = {}

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        if figi:
            clauses.append("figi = %(figi)s")
            params["figi"] = figi

        if timeframe:
            clauses.append("timeframe = %(timeframe)s")
            params["timeframe"] = timeframe

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT
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
            FROM trading.candles_aggregated
            {where}
            ORDER BY timestamp DESC
            LIMIT %(limit)s
        """

        params["limit"] = limit

        try:
            db = get_db()
            result = db.select(query, params)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        items = result_to_records(result)

        return {
            "items": items,
            "count": len(items),
        }

    @app.get("/api/signals/stats")
    def get_signal_stats(
        ticker: Optional[str] = Query(None),
        timeframe: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
    ):
        clauses: List[str] = []
        params: Dict[str, Any] = {}

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        if timeframe:
            clauses.append("timeframe = %(timeframe)s")
            params["timeframe"] = timeframe

        if date_from:
            clauses.append("timestamp >= %(date_from)s::timestamp")
            params["date_from"] = date_from

        if date_to:
            clauses.append("timestamp <= %(date_to)s::timestamp")
            params["date_to"] = date_to

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        try:
            db = get_db()

            total_records = result_to_records(
                db.select(
                    f"SELECT count(*) AS cnt FROM trading.signals {where}",
                    params,
                )
            )
            total = int(total_records[0]["cnt"]) if total_records else 0

            latest_records = result_to_records(
                db.select(
                    f"SELECT max(timestamp) AS latest_timestamp FROM trading.signals {where}",
                    params,
                )
            )
            latest_timestamp = latest_records[0]["latest_timestamp"] if latest_records else None

            by_direction = result_to_records(
                db.select(
                    f"""
                    SELECT signal, count(*) AS cnt
                    FROM trading.signals
                    {where}
                    GROUP BY signal
                    ORDER BY cnt DESC
                    """,
                    params,
                )
            )

            by_timeframe = result_to_records(
                db.select(
                    f"""
                    SELECT timeframe, count(*) AS cnt
                    FROM trading.signals
                    {where}
                    GROUP BY timeframe
                    ORDER BY cnt DESC
                    """,
                    params,
                )
            )

            pattern_where_clauses = clauses + [
                "pattern_name IS NOT NULL",
                "pattern_name <> ''",
            ]
            pattern_where = "WHERE " + " AND ".join(pattern_where_clauses)

            by_pattern_combined = result_to_records(
                db.select(
                    f"""
                    SELECT pattern_name, count(*) AS cnt
                    FROM trading.signals
                    {pattern_where}
                    GROUP BY pattern_name
                    ORDER BY cnt DESC
                    LIMIT 50
                    """,
                    params,
                )
            )

            atomic_query = f"""
                WITH split_patterns AS (
                    SELECT trim(unnest(string_to_array(pattern_name, ','))) AS pattern_name
                    FROM trading.signals
                    {pattern_where}
                )
                SELECT pattern_name, count(*) AS cnt
                FROM split_patterns
                WHERE pattern_name IS NOT NULL
                  AND pattern_name <> ''
                GROUP BY pattern_name
                ORDER BY cnt DESC
                LIMIT 50
            """

            by_pattern = result_to_records(db.select(atomic_query, params))

            return {
                "total": total,
                "latest_timestamp": latest_timestamp,
                "by_direction": by_direction,
                "by_timeframe": by_timeframe,
                "by_pattern": by_pattern,
                "by_pattern_combined": by_pattern_combined,
            }

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/signals")
    def get_signals(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        ticker: Optional[str] = Query(None),
        timeframe: Optional[str] = Query(None),
        signal: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        sort_by: str = Query("timestamp"),
        sort_dir: str = Query("desc"),
    ):
        allowed_sort_columns = {
            "id",
            "ticker",
            "figi",
            "timeframe",
            "timestamp",
            "signal",
            "confidence",
            "price",
            "rsi",
            "macd",
            "bb_position",
            "volume_ratio",
            "atr_pct",
            "pattern_name",
            "created_at",
        }

        if sort_by not in allowed_sort_columns:
            sort_by = "timestamp"

        sort_dir = sort_dir.lower()
        if sort_dir not in {"asc", "desc"}:
            sort_dir = "desc"

        clauses = []
        params: Dict[str, Any] = {}

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        if timeframe:
            clauses.append("timeframe = %(timeframe)s")
            params["timeframe"] = timeframe

        if signal:
            clauses.append("signal = %(signal)s")
            params["signal"] = signal

        if date_from:
            clauses.append("timestamp >= %(date_from)s::timestamp")
            params["date_from"] = date_from

        if date_to:
            clauses.append("timestamp <= %(date_to)s::timestamp")
            params["date_to"] = date_to

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        try:
            db = get_db()

            total_records = result_to_records(
                db.select(
                    f"SELECT count(*) AS cnt FROM trading.signals {where}",
                    params,
                )
            )
            total = int(total_records[0]["cnt"]) if total_records else 0

            query = f"""
                SELECT
                    id,
                    ticker,
                    figi,
                    timeframe,
                    timestamp,
                    signal,
                    confidence,
                    price,
                    rsi,
                    macd,
                    bb_position,
                    volume_ratio,
                    atr_pct,
                    summary,
                    buy_signals,
                    sell_signals,
                    total_signals,
                    pattern_name,
                    created_at
                FROM trading.signals
                {where}
                ORDER BY {sort_by} {sort_dir} NULLS LAST
                LIMIT %(limit)s OFFSET %(offset)s
            """

            params["limit"] = limit
            params["offset"] = offset

            result = db.select(query, params)

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        items = result_to_records(result)

        return {
            "items": items,
            "count": len(items),
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/top-stocks-by-volume")
    def get_top_stocks_by_volume(
        limit: int = Query(100, ge=1, le=1000),
        report_date: Optional[str] = Query(None),
        ticker: Optional[str] = Query(None),
    ):
        clauses = []
        params: Dict[str, Any] = {}

        if report_date:
            clauses.append("report_date = %(report_date)s::date")
            params["report_date"] = report_date

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT
                rank,
                report_date,
                ticker,
                figi,
                name,
                sum_volume,
                candle_count,
                first_date,
                last_date,
                period_start,
                period_end,
                created_at
            FROM trading.top_stocks_by_volume
            {where}
            ORDER BY report_date DESC, rank ASC
            LIMIT %(limit)s
        """

        params["limit"] = limit

        try:
            db = get_db()
            result = db.select(query, params)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        items = result_to_records(result)

        return {
            "items": items,
            "count": len(items),
        }
