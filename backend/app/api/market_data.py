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

    @app.get("/api/signals")
    def get_signals(
        limit: int = Query(100, ge=1, le=1000),
        ticker: Optional[str] = Query(None),
        timeframe: Optional[str] = Query(None),
        signal: Optional[str] = Query(None),
    ):
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

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT
                id,
                ticker,
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
                created_at
            FROM trading.signals
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
