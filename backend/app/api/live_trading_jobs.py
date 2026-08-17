"""Read-only monitoring API for sandbox live positions."""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from app.api.paper_trading_jobs import _json_safe
from app.db.db_manager import DBManager

TF_MAP = {"1h": "hour", "1d": "day", "1w": "week"}


def _get_db() -> DBManager:
    return DBManager()


def _build_where(
    *,
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> tuple[str, dict]:
    clauses = []
    params = {}
    if status == "closed":
        clauses.append("status IN ('closed_stop','closed_take')")
    elif status:
        clauses.append("status = %(status)s")
        params["status"] = status
    if ticker:
        clauses.append("ticker = %(ticker)s")
        params["ticker"] = ticker
    if date_from:
        clauses.append("COALESCE(signal_ts, created_at)::date >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        clauses.append("COALESCE(signal_ts, created_at)::date <= %(date_to)s")
        params["date_to"] = date_to
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _latest_prices(db: DBManager, tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    frame = db.select(
        """
        SELECT DISTINCT ON (ticker) ticker, best_bid, best_ask
        FROM trading.online_orderbook_aggregates
        WHERE ticker = ANY(%s)
        ORDER BY ticker, timestamp DESC
        """,
        (tickers,),
    ).to_dataframe()
    prices = {}
    if frame.empty:
        return prices
    for _, row in frame.iterrows():
        for candidate in (row["best_bid"], row["best_ask"]):
            try:
                price = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(price):
                prices[str(row["ticker"])] = price
                break
    return prices


def register_routes(app: FastAPI) -> None:
    @app.get("/api/live-trading/positions")
    def positions(
        status: Optional[str] = None,
        ticker: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        sort_by: str = Query("signal_ts"),
        sort_dir: str = Query("desc"),
    ):
        db = _get_db()
        where, params = _build_where(
            status=status,
            ticker=ticker,
            date_from=date_from,
            date_to=date_to,
        )
        sort_columns = {
            "entry_ts": "signal_ts",
            "signal_ts": "signal_ts",
            "exit_ts": "exit_ts",
            "entry_price": "entry_price",
            "exit_price": "exit_price",
            "pnl_rub": "pnl_rub",
            "pnl_pct": "pnl_rub",
            "ticker": "ticker",
            "status": "status",
            "created_at": "created_at",
            "id": "id",
        }
        order_column = sort_columns.get(sort_by, "signal_ts")
        direction = sort_dir if sort_dir in ("asc", "desc") else "desc"

        count_frame = db.select(
            f"SELECT COUNT(*) c FROM trading.live_positions {where}",
            params,
        ).to_dataframe()
        total = int(count_frame.iloc[0]["c"]) if not count_frame.empty else 0
        frame = db.select(
            f"""
            SELECT id, ticker, signal_ts AS entry_ts, entry_price, exit_ts,
                   exit_price, stop_price, take_price, status, exit_reason,
                   pnl_rub, size_lots, lot_size, strategy_name,
                   created_at, updated_at
            FROM trading.live_positions {where}
            ORDER BY {order_column} {direction}, id {direction}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {**params, "limit": limit, "offset": offset},
        ).to_dataframe()
        records = frame.to_dict("records") if not frame.empty else []
        open_tickers = sorted({
            str(row["ticker"]) for row in records if row.get("status") == "open"
        })
        prices = _latest_prices(db, open_tickers)

        for row in records:
            entry_price = float(row["entry_price"])
            if row.get("status") == "open":
                current_price = prices.get(str(row["ticker"]))
                row["current_price"] = current_price
                if current_price is not None:
                    row["pnl_rub"] = round(
                        (current_price - entry_price)
                        * int(row["size_lots"])
                        * int(row["lot_size"]),
                        2,
                    )
                    row["pnl_pct"] = round(
                        (current_price / entry_price - 1.0) * 100.0,
                        4,
                    )
                else:
                    row["pnl_pct"] = None
            else:
                row["current_price"] = row.get("exit_price")
                exit_price = row.get("exit_price")
                row["pnl_pct"] = (
                    round((float(exit_price) / entry_price - 1.0) * 100.0, 4)
                    if exit_price is not None and entry_price > 0
                    else None
                )

        return {
            "items": [_json_safe(row) for row in records],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/live-trading/dynamics")
    def dynamics(
        timeframe: str = Query("1d"),
        ticker: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ):
        if timeframe not in TF_MAP:
            raise HTTPException(
                status_code=400,
                detail=f"bad timeframe {timeframe}; use {list(TF_MAP)}",
            )
        where, params = _build_where(
            status="closed",
            ticker=ticker,
            date_from=date_from,
            date_to=date_to,
        )
        frame = _get_db().select(
            f"""
            SELECT date_trunc('{TF_MAP[timeframe]}', exit_ts) AS bucket,
                   SUM(pnl_rub) AS pnl_rub, COUNT(*) AS closed,
                   COUNT(*) FILTER (WHERE status='closed_take') AS wins
            FROM trading.live_positions {where}
              AND exit_ts IS NOT NULL
            GROUP BY bucket ORDER BY bucket
            """,
            params,
        ).to_dataframe()
        points = []
        cumulative = 0.0
        for _, row in frame.iterrows():
            pnl = float(row["pnl_rub"] or 0)
            cumulative += pnl
            points.append({
                "ts": str(row["bucket"]),
                "pnl_rub": round(pnl, 2),
                "cum_pnl_rub": round(cumulative, 2),
                "closed": int(row["closed"] or 0),
                "wins": int(row["wins"] or 0),
            })
        return {
            "timeframe": timeframe,
            "points": points,
            "cum_pnl_rub": round(cumulative, 2),
        }
