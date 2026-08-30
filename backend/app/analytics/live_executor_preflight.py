"""Read-only preflight for the first sandbox LiveExecutor canary."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from app.analytics.trading_config import (
    EXPECTED_LOCKED_STRATEGY,
    LIVE_UNIVERSE,
    get_live_trading_universe,
    get_sandbox_trading_config,
    get_trading_universe,
)
from app.broker.tinkoff_sandbox import TinkoffSandboxClient
from app.db.db_manager import DBManager


CANARY_TICKERS = list(LIVE_UNIVERSE)
PAPER_PROCESSES = (
    "run_data_refresher",
    "run_online_data",
    "run_live_engine",
    "run_paper_trader",
)


def _now_msk_naive() -> datetime:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=3))
    ).replace(tzinfo=None)


def _process_counts() -> dict[str, int]:
    counts = {target: 0 for target in PAPER_PROCESSES}
    for pid_dir in os.listdir("/proc"):
        if not pid_dir.isdigit() or int(pid_dir) == os.getpid():
            continue
        try:
            with open(f"/proc/{pid_dir}/cmdline", "rb") as command_file:
                command = (
                    command_file.read()
                    .decode("utf-8", errors="replace")
                    .replace("\x00", " ")
                )
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        for target in counts:
            if target in command:
                counts[target] += 1
    return counts


def _timestamp_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()


def collect_preflight() -> dict[str, Any]:
    """Collect non-secret checks and return a comment-ready payload."""
    with urllib.request.urlopen("http://localhost:8000/health", timeout=5) as response:
        health = json.loads(response.read().decode("utf-8"))

    db = DBManager()
    try:
        universe = get_trading_universe(db)
        live_universe = get_live_trading_universe(db)
        strategies = db.select(
            "SELECT id, name FROM trading.strategies "
            "WHERE in_paper_test=true AND locked=true ORDER BY id"
        ).to_dataframe()
        books = db.select(
            """
            SELECT DISTINCT ON (ticker) ticker, timestamp
            FROM trading.online_orderbook_aggregates
            WHERE ticker = ANY(%s)
            ORDER BY ticker, timestamp DESC
            """,
            (CANARY_TICKERS,),
        ).to_dataframe()
        paper_equity = db.select(
            "SELECT timestamp, equity_rub FROM trading.paper_equity "
            "ORDER BY timestamp DESC LIMIT 1"
        ).to_dataframe()

        now = _now_msk_naive()
        orderbooks: dict[str, dict[str, Any]] = {}
        for ticker in CANARY_TICKERS:
            rows = books[books["ticker"].astype(str) == ticker]
            if rows.empty:
                orderbooks[ticker] = {"timestamp": None, "age_seconds": None}
                continue
            timestamp = pd.Timestamp(rows.iloc[0]["timestamp"]).to_pydatetime()
            if timestamp.tzinfo is not None:
                timestamp = timestamp.astimezone(
                    timezone(timedelta(hours=3))
                ).replace(tzinfo=None)
            orderbooks[ticker] = {
                "timestamp": timestamp.isoformat(),
                "age_seconds": round(max(0.0, (now - timestamp).total_seconds()), 1),
            }

        strategy_rows = [
            {"id": int(row["id"]), "name": str(row["name"])}
            for _, row in strategies.iterrows()
        ]
        process_counts = _process_counts()
        free_rub = float(TinkoffSandboxClient().check_balance())
        latest_equity = (
            None
            if paper_equity.empty
            else {
                "timestamp": _timestamp_text(paper_equity.iloc[0]["timestamp"]),
                "equity_rub": float(paper_equity.iloc[0]["equity_rub"]),
            }
        )
        checks = {
            "backend_health": health.get("status") == "ok",
            "live_universe": live_universe == CANARY_TICKERS,
            "single_locked_strategy": (
                len(strategy_rows) == 1
                and strategy_rows[0]["name"] == EXPECTED_LOCKED_STRATEGY
            ),
            "sandbox_free_rub": free_rub > 0,
            "fresh_orderbooks": (
                set(orderbooks) == set(CANARY_TICKERS)
                and all(
                    value["age_seconds"] is not None
                    and value["age_seconds"] <= 300
                    for value in orderbooks.values()
                )
            ),
            "paper_processes": all(count == 1 for count in process_counts.values()),
            "trading_universe_top15": len(universe) == 15,
            "real_trading_disabled": not bool(
                get_sandbox_trading_config()["allow_real_trading"]
            ),
        }
        return {
            "ok": all(checks.values()),
            "checked_at_msk": now.isoformat(),
            "checks": checks,
            "details": {
                "live_universe": live_universe,
                "locked_strategies": strategy_rows,
                "sandbox_free_rub": free_rub,
                "orderbooks": orderbooks,
                "paper_processes": process_counts,
                "trading_universe_count": len(universe),
                "allow_real_trading": get_sandbox_trading_config()[
                    "allow_real_trading"
                ],
                "latest_paper_equity": latest_equity,
            },
        }
    finally:
        db.close_pool()


def main() -> int:
    try:
        result = collect_preflight()
    except Exception as exc:
        result = {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": "Preflight could not complete; inspect service logs.",
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
