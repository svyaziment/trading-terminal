"""Read-only monitoring API for sandbox live positions."""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from app.analytics.trading_config import (
    get_live_risk_bounds,
    get_live_risk_config,
    get_live_trading_config,
)
from app.api.paper_trading_jobs import _json_safe
from app.db.db_manager import DBManager

TF_MAP = {"1h": "hour", "1d": "day", "1w": "week"}

# Issue #176: columns of trading.live_equity served to the monitoring panel.
# Listed once so the SELECT and the payload cannot drift apart.
LIVE_EQUITY_COLUMNS = (
    "id",
    "timestamp",
    "session_key",
    "equity_rub",
    "cash_rub",
    "market_value_rub",
    "realized_pnl_rub",
    "unrealized_pnl_rub",
    "peak_equity_rub",
    "peak_equity_all_time_rub",
    "drawdown_pct",
    "open_positions",
    "risk_breach",
    "account_id",
    "strategy_name",
)

_LIVE_EQUITY_SELECT = ", ".join(LIVE_EQUITY_COLUMNS)


def _equity_unavailable(exc: Exception) -> HTTPException:
    """One actionable error for a missing/unmigrated trading.live_equity."""
    return HTTPException(
        status_code=503,
        detail=(
            "trading.live_equity is not available "
            f"({type(exc).__name__}); run `alembic upgrade head` "
            "(migration 20260927_001_live_equity)"
        ),
    )


def _risk_limits() -> dict:
    """Effective live risk limits, so the panel never hardcodes a number."""
    risk = get_live_risk_config()
    live = get_live_trading_config()
    return {
        "max_daily_loss_pct": risk["max_daily_loss_pct"],
        "max_position_size": risk["max_position_size"],
        "max_open_positions": live["max_open_positions"],
        "equity_snapshot_enabled": risk["equity_snapshot_enabled"],
        "risk_breach_reset_key": risk["risk_breach_reset_key"],
        "bounds": {
            key: list(value) for key, value in get_live_risk_bounds().items()
        },
    }


def _normalize_equity_row(row: dict) -> dict:
    """Stable JSON shape for one live_equity row.

    ``session_key`` is a DATE column, but pandas hands it back as a Timestamp,
    which ``_json_safe`` renders as ``YYYY-MM-DDT00:00:00``. Trimming it keeps
    the published contract a plain date whatever the driver returns.
    """
    safe = _json_safe(row)
    value = safe.get("session_key")
    if isinstance(value, str) and len(value) > 10:
        safe["session_key"] = value[:10]
    return safe


def _get_db() -> DBManager:
    return DBManager()


def _build_where(
    *,
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    exit_reason: Optional[str] = None,
) -> tuple[str, dict]:
    clauses = []
    params = {}
    if status == "closed":
        # Issue #149: closed includes closed_trailing for trailing stop exits
        clauses.append("status IN ('closed_stop','closed_take','closed_trailing')")
    elif status:
        clauses.append("status = %(status)s")
        params["status"] = status
    if exit_reason:
        clauses.append("exit_reason = %(exit_reason)s")
        params["exit_reason"] = exit_reason
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
        exit_reason: Optional[str] = None,
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
            exit_reason=exit_reason,
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
        # Issue #149: trailing fields added (columns from migration 20260915_002)
        frame = db.select(
            f"""
            SELECT id, ticker, signal_ts AS entry_ts, entry_price, exit_ts,
                   exit_price, stop_price, take_price, status, exit_reason,
                   pnl_rub, size_lots, lot_size, strategy_name,
                   created_at, updated_at,
                   trailing_enabled, current_stop_price, step_reached, risk_r
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
        # Issue #149: wins include closed_trailing with positive PnL (matches paper convention)
        frame = _get_db().select(
            f"""
            SELECT date_trunc('{TF_MAP[timeframe]}', exit_ts) AS bucket,
                   SUM(pnl_rub) AS pnl_rub, COUNT(*) AS closed,
                   COUNT(*) FILTER (WHERE status='closed_take' OR (status='closed_trailing' AND pnl_rub > 0)) AS wins
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

    # --- Issue #176: live equity and risk-gate monitoring --------------------

    def _equity_current_payload() -> dict:
        """Latest trading.live_equity row plus the limits it is judged against."""
        db = _get_db()
        try:
            frame = db.select(
                f"""
                SELECT {_LIVE_EQUITY_SELECT}
                FROM trading.live_equity
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """
            ).to_dataframe()
        except Exception as exc:  # noqa: BLE001 - unmigrated database
            raise _equity_unavailable(exc) from exc
        snapshot = (
            _normalize_equity_row(frame.to_dict("records")[0]) if not frame.empty else None
        )
        return {
            "available": True,
            "snapshot": snapshot,
            "risk_breach_active": bool((snapshot or {}).get("risk_breach")),
            "risk": _risk_limits(),
        }

    @app.get("/api/live-trading/equity/current")
    def equity_current():
        """Newest live equity snapshot and the effective risk limits."""
        return _equity_current_payload()

    @app.get("/api/live-trading/equity/latest")
    def equity_latest():
        """Alias of ``/equity/current`` kept for the panel's naming."""
        return _equity_current_payload()

    @app.get("/api/live-trading/equity/history")
    def equity_history(
        session_key: Optional[date] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = Query(500, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ):
        """Live equity curve, newest first, filterable by MSK trading day."""
        clauses = []
        params: dict = {}
        if session_key:
            clauses.append("session_key = %(session_key)s")
            params["session_key"] = session_key
        if date_from:
            clauses.append("session_key >= %(date_from)s")
            params["date_from"] = date_from
        if date_to:
            clauses.append("session_key <= %(date_to)s")
            params["date_to"] = date_to
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        db = _get_db()
        try:
            total = int(
                db.select(
                    f"SELECT COUNT(*) AS total FROM trading.live_equity{where}",
                    params,
                ).to_dataframe().iloc[0]["total"]
            )
            frame = db.select(
                f"""
                SELECT {_LIVE_EQUITY_SELECT}
                FROM trading.live_equity{where}
                ORDER BY timestamp DESC, id DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                {**params, "limit": limit, "offset": offset},
            ).to_dataframe()
        except Exception as exc:  # noqa: BLE001 - unmigrated database
            raise _equity_unavailable(exc) from exc
        records = frame.to_dict("records") if not frame.empty else []
        return {
            "items": [_normalize_equity_row(row) for row in records],
            "total": total,
            "limit": limit,
            "offset": offset,
            "risk": _risk_limits(),
        }

