import pandas as pd

from app.analytics.paper_trader import (
    close_position,
    open_market_position,
    write_equity,
)


class QueryResult:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def to_dataframe(self) -> pd.DataFrame:
        return self.frame


class RecordingDB:
    def __init__(self) -> None:
        self.executions = []

    def execute(self, query, params) -> None:
        self.executions.append((query, params))


class RecordingNotifier:
    def __init__(self) -> None:
        self.opened = []
        self.closed = []
        self.critical = []

    def notify_position_open(self, **event) -> bool:
        self.opened.append(event)
        return True

    def notify_position_close(self, **event) -> bool:
        self.closed.append(event)
        return True

    def notify_critical(self, **event) -> bool:
        self.critical.append(event)
        return True


def test_open_and_close_emit_trade_notifications_after_db_write() -> None:
    db = RecordingDB()
    notifier = RecordingNotifier()

    open_market_position(
        db,
        "SBER",
        300.0,
        294.0,
        312.0,
        10,
        2,
        6000.0,
        "base",
        "window",
        "rr2",
        2.0,
        42,
        "strategy",
        notifier,
    )
    pnl_rub = close_position(
        db,
        1,
        "SBER",
        312.0,
        "take",
        "2026-08-17T10:00:00",
        300.0,
        10,
        2,
        notifier,
    )

    assert len(db.executions) == 2
    assert notifier.opened[0]["reason"] == "market/base"
    assert notifier.closed[0]["reason"] == "take"
    assert notifier.closed[0]["pnl_rub"] == pnl_rub


class EquityDB(RecordingDB):
    def select(self, query, params=None) -> QueryResult:
        if "coalesce(sum(pnl_rub)" in query:
            return QueryResult(pd.DataFrame({"s": [-3000.0]}))
        if "status='open'" in query:
            return QueryResult(
                pd.DataFrame(columns=["ticker", "entry_price", "lot_size", "size_lots"])
            )
        if "max(equity_rub)" in query:
            return QueryResult(pd.DataFrame({"m": [100000.0]}))
        if "ORDER BY timestamp DESC" in query:
            return QueryResult(
                pd.DataFrame({"equity_rub": [99000.0], "drawdown_pct": [1.0]})
            )
        if "status='pending'" in query:
            return QueryResult(pd.DataFrame({"c": [0]}))
        raise AssertionError(f"Unexpected query: {query}")


def test_drawdown_threshold_crossing_emits_one_critical_notification() -> None:
    db = EquityDB()
    notifier = RecordingNotifier()

    equity = write_equity(
        db,
        100000.0,
        notifier=notifier,
        critical_drawdown_pct=2.0,
    )

    assert equity == 97000.0
    assert notifier.critical[0]["event"] == "LARGE DRAWDOWN"
