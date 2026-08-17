"""Extract Issue #66 ranking inputs from PostgreSQL.

Run from the repository root:

    python analytics/issue-66-live-universe/extract_inputs.py

Writes analytics/issue-66-live-universe/inputs.json. Secrets are never written.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
OUTPUT_PATH = ANALYSIS_DIR / "inputs.json"


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))
    if os.environ.get("POSTGRES_HOST") == "host.docker.internal":
        os.environ["POSTGRES_HOST"] = "localhost"
    os.environ.setdefault("PSTGRS_PWD", os.environ.get("POSTGRES_PASSWORD", ""))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _connect():
    _load_env()
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("PSTGRS_PWD") or os.environ.get("POSTGRES_PASSWORD", ""),
        database=os.environ.get("POSTGRES_DB", "postgres"),
    )


def _fetchall(cur, sql: str, params=None) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    return [_json_safe(dict(row)) for row in cur.fetchall()]


def _fetchone(cur, sql: str, params=None) -> dict[str, Any] | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    return _json_safe(dict(row)) if row else None


def extract(conn) -> dict[str, Any]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    extracted_at = datetime.now().isoformat(sep=" ", timespec="seconds")

    universe = _fetchall(
        cur,
        """
        SELECT ticker, rank, pf, source, notes, updated_at::text AS updated_at
        FROM trading.trading_universe
        ORDER BY rank ASC, ticker ASC
        """,
    )
    tickers = [row["ticker"] for row in universe]

    paper_positions = _fetchone(
        cur,
        """
        SELECT
            count(*)::int AS rows,
            count(*) FILTER (WHERE status LIKE 'closed%')::int AS closed,
            count(*) FILTER (WHERE status = 'open')::int AS open,
            count(*) FILTER (WHERE pnl_rub > 0)::int AS wins,
            coalesce(sum(pnl_rub) FILTER (WHERE status LIKE 'closed%'), 0) AS pnl_rub
        FROM trading.paper_positions
        """,
    )
    paper_equity = _fetchone(
        cur,
        """
        SELECT
            count(*)::int AS rows,
            min(timestamp)::text AS min_ts,
            max(timestamp)::text AS max_ts,
            min(equity_rub) AS min_equity_rub,
            max(equity_rub) AS max_equity_rub,
            min(drawdown_pct) AS min_drawdown_pct,
            max(drawdown_pct) AS max_drawdown_pct,
            min(open_positions) AS min_open_positions,
            max(open_positions) AS max_open_positions
        FROM trading.paper_equity
        """,
    )
    paper_equity_sample = _fetchall(
        cur,
        """
        SELECT timestamp::text AS timestamp, equity_rub, realized_pnl,
               open_positions, drawdown_pct
        FROM trading.paper_equity
        ORDER BY timestamp
        LIMIT 3
        """,
    )

    strategy = _fetchone(
        cur,
        """
        SELECT id, name, in_paper_test, locked, config
        FROM trading.strategies
        WHERE in_paper_test = true AND locked = true
        ORDER BY id
        LIMIT 1
        """,
    )
    strategy_id = strategy["id"] if strategy else None
    strategy_config = strategy.get("config") if strategy else {}
    if isinstance(strategy_config, str):
        import ast

        try:
            strategy_config = json.loads(strategy_config)
        except json.JSONDecodeError:
            strategy_config = ast.literal_eval(strategy_config)
        strategy["config"] = strategy_config

    locked_backtest = []
    if strategy_id is not None:
        locked_backtest = _fetchall(
            cur,
            """
            SELECT ticker, test_type, depth, metrics, created_at::text AS created_at
            FROM trading.backtest_results
            WHERE strategy_id = %s AND test_type = 'full_sample'
            ORDER BY ticker
            """,
            (strategy_id,),
        )
        for row in locked_backtest:
            metrics = row.get("metrics")
            if isinstance(metrics, dict):
                metrics = dict(metrics)
                metrics.pop("trades", None)
                row["metrics"] = metrics

    instruments = _fetchall(
        cur,
        """
        SELECT ticker, lot_size, min_price_increment, figi
        FROM trading.instruments
        WHERE ticker = ANY(%s)
        ORDER BY ticker
        """,
        (tickers,),
    )

    market = _fetchall(
        cur,
        """
        WITH last_d AS (
            SELECT DISTINCT ON (c.ticker)
                   c.ticker, c.timestamp, c.close, c.volume
            FROM trading.candles_aggregated c
            WHERE c.timeframe = '1d' AND c.ticker = ANY(%s)
            ORDER BY c.ticker, c.timestamp DESC
        ),
        atr AS (
            SELECT DISTINCT ON (i.ticker)
                   i.ticker, i.timestamp, i.atr_14, i.close
            FROM trading.indicators i
            WHERE i.timeframe = '1d' AND i.ticker = ANY(%s)
            ORDER BY i.ticker, i.timestamp DESC
        ),
        vol AS (
            SELECT ticker,
                   avg(volume) AS avg_volume_60d,
                   avg(close * volume) AS avg_turnover_60d,
                   count(*)::int AS n_days
            FROM trading.candles_aggregated
            WHERE timeframe = '1d'
              AND timestamp >= NOW() - INTERVAL '60 days'
              AND ticker = ANY(%s)
            GROUP BY ticker
        )
        SELECT l.ticker,
               l.timestamp::text AS last_1d_ts,
               l.close AS last_close,
               l.volume AS last_volume,
               a.atr_14,
               CASE WHEN l.close > 0 THEN a.atr_14 / l.close * 100 END AS atr_pct,
               v.avg_volume_60d,
               v.avg_turnover_60d,
               v.n_days
        FROM last_d l
        LEFT JOIN atr a ON a.ticker = l.ticker
        LEFT JOIN vol v ON v.ticker = l.ticker
        ORDER BY l.ticker
        """,
        (tickers, tickers, tickers),
    )

    spreads = _fetchall(
        cur,
        """
        SELECT ticker,
               count(*)::int AS n_quotes,
               min(timestamp)::text AS min_ts,
               max(timestamp)::text AS max_ts,
               avg(abs(spread_pct)) AS avg_abs_spread_pct,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(spread_pct))
                   AS median_abs_spread_pct,
               avg(bid_depth) AS avg_bid_depth,
               avg(ask_depth) AS avg_ask_depth,
               avg(bid_depth + ask_depth) AS avg_depth
        FROM trading.online_orderbook_aggregates
        WHERE ticker = ANY(%s)
        GROUP BY ticker
        ORDER BY ticker
        """,
        (tickers,),
    )

    top_stocks = _fetchall(
        cur,
        """
        SELECT report_date::text AS report_date, ticker, rank, sum_volume, candle_count
        FROM trading.top_stocks_by_volume
        WHERE report_date = (SELECT max(report_date) FROM trading.top_stocks_by_volume)
        ORDER BY rank
        """,
    )

    return {
        "extracted_at": extracted_at,
        "paper_positions": paper_positions,
        "paper_equity": paper_equity,
        "paper_equity_sample": paper_equity_sample,
        "active_strategy": {
            "id": strategy.get("id") if strategy else None,
            "name": strategy.get("name") if strategy else None,
            "in_paper_test": strategy.get("in_paper_test") if strategy else None,
            "locked": strategy.get("locked") if strategy else None,
            "run_params": (strategy_config or {}).get("run_params") if strategy else None,
            "patterns": (strategy_config or {}).get("patterns") if strategy else None,
            "confirm_windows": (strategy_config or {}).get("confirm_windows") if strategy else None,
            "risk_reward": (strategy_config or {}).get("risk_reward") if strategy else None,
            "commission_pct": (strategy_config or {}).get("commission_pct") if strategy else None,
        },
        "universe": universe,
        "locked_backtest": locked_backtest,
        "instruments": instruments,
        "market": market,
        "spreads": spreads,
        "top_stocks": top_stocks,
        "issue44_levels_reversal_ticker_pnl_rub": {
            "PIKK": 5085,
            "GMKN": 3160,
            "ROSN": 3097,
            "MTLR": 3079,
            "RUAL": 2883,
            "SBER": -257,
        },
    }


def main() -> None:
    conn = _connect()
    try:
        payload = extract(conn)
    finally:
        conn.close()
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT_PATH} bytes={OUTPUT_PATH.stat().st_size}")


if __name__ == "__main__":
    main()
