from decimal import Decimal
from datetime import datetime, timedelta
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from app.analytics import live_executor as module
from app.analytics.live_executor import (
    LiveExecutor,
    MAX_CONSECUTIVE_ERRORS,
    TokenBucket,
    ensure_live_positions_table,
    tick_align,
)
from app.analytics.live_schema import (
    REQUIRED_LIVE_POSITIONS_COLUMNS,
    REQUIRED_LIVE_POSITIONS_STATUSES,
)
from app.broker.tinkoff_sandbox import SandboxAPIError


IN_SESSION_NOW = datetime(2026, 8, 31, 11, 0, 0)


class Result:
    def __init__(self, frame=None):
        self.frame = frame if frame is not None else pd.DataFrame()

    def to_dataframe(self):
        return self.frame.copy()


class FakeCursor:
    """Fake cursor that records executed queries."""

    def __init__(self, connection):
        self.connection = connection
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        # pg_try_advisory_lock returns (True,) when acquired
        if self.executed and "pg_try_advisory_lock" in self.executed[-1][0]:
            return (True,)
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeConnection:
    """Fake connection that tracks cursors for advisory lock tests."""

    def __init__(self):
        self.cursors = []

    def cursor(self):
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor


class FakeDB:
    def __init__(
        self,
        active=None,
        instruments=None,
        app_settings=None,
        schema_columns=None,
        schema_statuses=None,
        schema_tables=None,
    ):
        self.active = active if active is not None else pd.DataFrame()
        self.instruments = (
            instruments if instruments is not None else pd.DataFrame()
        )
        self.app_settings = app_settings if app_settings is not None else {}
        # Issue #173: information_schema answers for the live-schema contract.
        # Defaults describe a fully migrated database (30 columns / 7 statuses);
        # tests can narrow them to simulate schema drift.
        self.schema_columns = (
            list(schema_columns)
            if schema_columns is not None
            else list(REQUIRED_LIVE_POSITIONS_COLUMNS)
        )
        self.schema_statuses = (
            list(schema_statuses)
            if schema_statuses is not None
            else list(REQUIRED_LIVE_POSITIONS_STATUSES)
        )
        self.schema_tables = (
            list(schema_tables)
            if schema_tables is not None
            else ["app_settings", "live_positions"]
        )
        self.select_calls = []
        self.execute_calls = []
        # Issue #174: dedicated connection for advisory lock tests
        self._dedicated_conn = FakeConnection()
        self.dedicated_connection_calls = []

    def select(self, query, params=None):
        self.select_calls.append((query, params))
        normalized = " ".join(query.split())
        if "information_schema.tables" in normalized:
            return Result(pd.DataFrame({"table_name": self.schema_tables}))
        if "information_schema.columns" in normalized:
            return Result(pd.DataFrame({"column_name": self.schema_columns}))
        if "pg_constraint" in normalized:
            definition = (
                "CHECK (((status)::text = ANY ((ARRAY["
                + ", ".join(
                    f"'{status}'::character varying"
                    for status in self.schema_statuses
                )
                + "])::text[])))"
            )
            return Result(pd.DataFrame({"definition": [definition]}))
        if (
            "SELECT id FROM trading.live_positions" in normalized
            and "broker_order_id=%s" in normalized
        ):
            return Result(pd.DataFrame([{"id": 41}]))
        if "FROM trading.instruments" in normalized:
            return Result(self.instruments)
        if "FROM trading.live_positions" in normalized:
            frame = self.active
            if params and "ticker=%s" in normalized and not frame.empty:
                frame = frame[frame["ticker"] == params[0]]
            return Result(frame)
        if "FROM trading.app_settings" in normalized:
            # Return app_settings as DataFrame
            if params and len(params) > 0:
                key = params[0]
                if key in self.app_settings:
                    return Result(pd.DataFrame([{"key": key, "value": self.app_settings[key]}]))
            elif self.app_settings:
                return Result(pd.DataFrame([
                    {"key": k, "value": v} for k, v in self.app_settings.items()
                ]))
            return Result()
        return Result()

    def execute(self, query, params=None):
        self.execute_calls.append((" ".join(query.split()), params))
        return 1

    def get_dedicated_connection(self):
        """Issue #174: return a dedicated connection for advisory lock."""
        self.dedicated_connection_calls.append(("get", None))
        return self._dedicated_conn

    def release_dedicated_connection(self, conn):
        """Issue #174: return a dedicated connection to the pool."""
        self.dedicated_connection_calls.append(("release", conn))


class FakeBroker:
    def __init__(self, positions=None, balance=Decimal("50000")):
        self.calls = []
        self.positions = positions or []
        self.balance = balance
        self.order_number = 0

    def check_balance(self):
        self.calls.append(("check_balance", {}))
        return self.balance

    def execute_order(self, **kwargs):
        self.calls.append(("execute_order", kwargs))
        self.order_number += 1
        is_market_buy = (
            kwargs["direction"] == "buy" and kwargs["order_type"] == "market"
        )
        return SimpleNamespace(
            order_id=f"order-{self.order_number}",
            lots_executed=kwargs["quantity"] if is_market_buy else 0,
            executed_order_price=Decimal("100") if is_market_buy else None,
        )

    def get_positions(self):
        self.calls.append(("get_positions", {}))
        return self.positions

    def cancel_order(self, order_id):
        self.calls.append(("cancel_order", {"order_id": order_id}))
        return SimpleNamespace(order_id=order_id)


def make_executor(*, db=None, broker=None, now_fn=None, clock=None, sleep_fn=None, **config):
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    if sleep_fn is not None:
        kwargs["sleep_fn"] = sleep_fn
    executor = LiveExecutor(
        db=db or FakeDB(),
        broker=broker or FakeBroker(),
        config={
            "enabled": True,
            "api_rate_limit": 10,
            "max_open_positions": 5,
            "imbalance_threshold": 1.0,
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 20.0,
            # Issue #151: trailing defaults for tests
            "trailing_kill_switch": False,
            "live_trailing_enabled": True,
            "trailing_protective_ticks": 5,
            "trailing_ticker_allowlist": [],
            **config,
        },
        now_fn=now_fn or (lambda: IN_SESSION_NOW),
        **kwargs,
    )
    executor.strategy_name = "active-strategy"
    executor.strategy_config = {"patterns": ["levels_reversal"]}
    executor.instruments = {
        "SBER": {
            "instrument_id": "figi-sber",
            "lot_size": 10,
            "min_price_increment": 0.01,
        }
    }
    return executor


def active_position(**overrides):
    data = {
        "id": 41,
        "ticker": "SBER",
        "instrument_id": "figi-sber",
        "signal_ts": pd.Timestamp("2026-08-17 10:00:00"),
        "entry_price": 100.0,
        "lot_size": 10,
        "size_lots": 10,
        "stop_price": 95.0,
        "take_price": 110.0,
        "broker_order_id": "entry-1",
        "broker_stop_id": None,
        "broker_take_id": "take-1",
        "status": "open",
        "strategy_name": "active-strategy",
    }
    data.update(overrides)
    return pd.DataFrame([data])


def test_token_bucket_limits_requests_to_configured_rate():
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(
        2,
        clock=lambda: now[0],
        sleep_fn=sleep,
    )

    bucket.acquire()
    bucket.acquire()
    bucket.acquire()

    assert sleeps == [pytest.approx(0.5)]
    assert now[0] == pytest.approx(0.5)


@pytest.mark.parametrize("rate", [0, -1, 10.1])
def test_token_bucket_rejects_unsafe_rate(rate):
    with pytest.raises(ValueError, match="api_rate_limit"):
        TokenBucket(rate)


def test_buy_flow_checks_balance_sizes_entry_and_places_take_limit(caplog):
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
            imbalance=1.5,
        )

    assert result == {
        "executed": True,
        "reason": "open",
        "position_id": 41,
        "size_lots": 10,
    }
    assert broker.calls[0][0] == "check_balance"
    entry = broker.calls[1]
    assert entry == (
        "execute_order",
        {
            "instrument_id": "figi-sber",
            "quantity": 10,
            "direction": "buy",
            "order_type": "market",
        },
    )
    take = broker.calls[2]
    assert take[1]["direction"] == "sell"
    assert take[1]["order_type"] == "limit"
    assert take[1]["price"] == 110
    assert all(
        call[1].get("price") != 95
        for call in broker.calls
        if call[0] == "execute_order"
    )
    assert (
        "Live BUY submitted: ticker=SBER status=open "
        "size_lots=10 position_id=41"
    ) in caplog.text


def test_buy_outside_entry_window_is_skipped_without_broker(caplog):
    broker = FakeBroker()
    executor = make_executor(
        broker=broker,
        now_fn=lambda: datetime(2026, 8, 30, 23, 0, 0),
    )

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
            imbalance=1.5,
        )

    assert result == {"executed": False, "reason": "outside_entry_window"}
    assert broker.calls == []
    assert "reason=outside_entry_window" in caplog.text
    assert "hour=23:00" in caplog.text


def test_buy_at_session_close_is_skipped_without_broker():
    broker = FakeBroker()
    executor = make_executor(
        broker=broker,
        now_fn=lambda: datetime(2026, 8, 31, 19, 0, 0),
    )

    result = executor.process_signal(
        "SBER",
        {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
        imbalance=1.5,
    )

    assert result["reason"] == "outside_entry_window"
    assert broker.calls == []


def test_imbalance_is_mandatory_before_any_broker_request(caplog):
    broker = FakeBroker()
    executor = make_executor(broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"entry_price": 100, "stop": 95, "take": 110},
            imbalance=1.0,
        )

    assert result == {
        "executed": False,
        "reason": "imbalance_below_threshold",
    }
    assert broker.calls == []
    assert "ticker=SBER reason=imbalance_below_threshold" in caplog.text
    assert "imbalance=1.0 imbalance_threshold=1.0" in caplog.text


def test_stale_orderbook_logs_age_before_any_broker_request(caplog, monkeypatch):
    broker = FakeBroker()
    executor = make_executor(broker=broker)
    monkeypatch.setattr(executor, "_latest_orderbook", lambda _ticker: (None, 420.0))

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"entry_price": 100, "stop": 95, "take": 110},
        )

    assert result == {
        "executed": False,
        "reason": "stale_or_missing_orderbook",
    }
    assert broker.calls == []
    assert "ticker=SBER reason=stale_or_missing_orderbook" in caplog.text
    assert "orderbook_age_seconds=420.0 max_age_seconds=300.0" in caplog.text


def test_max_open_positions_blocks_entry(caplog):
    db = FakeDB(active=active_position())
    broker = FakeBroker()
    executor = make_executor(
        db=db,
        broker=broker,
        max_open_positions=1,
    )

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"entry_price": 100, "stop": 95, "take": 110},
            imbalance=2,
        )

    assert result["reason"] == "max_open_positions"
    assert broker.calls == []
    assert "open_positions=1 max_open_positions=1" in caplog.text


def test_zero_free_balance_logs_insufficient_cash_without_order(caplog):
    broker = FakeBroker(balance=Decimal("0"))
    executor = make_executor(broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"entry_price": 100, "stop": 95, "take": 110},
            imbalance=2,
        )

    assert result == {"executed": False, "reason": "insufficient_cash"}
    assert broker.calls == [("check_balance", {})]
    assert "ticker=SBER reason=insufficient_cash free_rub=0" in caplog.text


@pytest.mark.parametrize("sizing_reason", ["invalid_stop", "insufficient_capital"])
def test_position_sizing_rejection_is_logged(
    caplog,
    monkeypatch,
    sizing_reason,
):
    broker = FakeBroker()
    executor = make_executor(broker=broker)
    monkeypatch.setattr(
        module,
        "calculate_position_size",
        lambda **_kwargs: {
            "size_lots": 0,
            "size_rub": 0.0,
            "risk_rub": 0.0,
            "reason": sizing_reason,
        },
    )

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"entry_price": 100, "stop": 95, "take": 110},
            imbalance=2,
        )

    assert result == {"executed": False, "reason": sizing_reason}
    assert broker.calls == [("check_balance", {})]
    assert f"ticker=SBER reason={sizing_reason}" in caplog.text
    assert "size_lots=0" in caplog.text


def test_broker_error_log_excludes_exception_message(caplog):
    class FailingBroker(FakeBroker):
        def check_balance(self):
            self.calls.append(("check_balance", {}))
            raise SandboxAPIError("token=secret account=private")

    broker = FailingBroker()
    executor = make_executor(broker=broker)

    with caplog.at_level("WARNING", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"entry_price": 100, "stop": 95, "take": 110},
            imbalance=2,
        )

    assert result == {"executed": False, "reason": "broker_error"}
    assert broker.calls == [("check_balance", {})]
    assert "ticker=SBER reason=broker_error operation=check_balance" in caplog.text
    assert "secret" not in caplog.text
    assert "private" not in caplog.text


def test_stop_is_submitted_only_after_price_crosses_trigger():
    db = FakeDB(active=active_position())
    broker_position = SimpleNamespace(
        ticker="SBER",
        figi="figi-sber",
        instrument_uid="",
        current_price=Decimal("94"),
    )
    broker = FakeBroker(positions=[broker_position])
    executor = make_executor(db=db, broker=broker)

    changes = executor.monitor_positions()

    assert changes == 1
    assert broker.calls[0][0] == "get_positions"
    assert broker.calls[1] == ("cancel_order", {"order_id": "take-1"})
    stop = broker.calls[2]
    assert stop[1]["direction"] == "sell"
    assert stop[1]["order_type"] == "limit"
    assert stop[1]["price"] == 94
    assert any(
        "SET broker_stop_id=%s, broker_take_id=NULL" in query
        for query, _ in db.execute_calls
    )


def test_missing_broker_position_is_recorded_as_take_close():
    db = FakeDB(active=active_position())
    executor = make_executor(db=db, broker=FakeBroker())

    changes = executor.monitor_positions()

    assert changes == 1
    close_call = next(
        params
        for query, params in db.execute_calls
        if "SET status=%s, exit_ts=%s" in query
    )
    assert close_call[0] == "closed_take"
    assert close_call[2] == 110
    assert close_call[3] == "take"
    assert close_call[4] == 1000


def test_shutdown_cancels_all_pending_orders_without_flattening_by_default():
    active = pd.concat(
        [
            active_position(),  # open SBER with take-1
            active_position(
                id=42,
                ticker="GAZP",
                instrument_id="figi-gazp",
                broker_order_id="entry-2",
                broker_take_id=None,
                status="pending",
            ),
        ],
        ignore_index=True,
    )
    db = FakeDB(active=active)
    broker = FakeBroker()
    executor = make_executor(
        db=db,
        broker=broker,
        close_positions_on_shutdown=False,
    )

    executor.shutdown()

    cancelled = [
        call[1]["order_id"]
        for call in broker.calls
        if call[0] == "cancel_order"
    ]
    # Issue #174: only pending entry orders are cancelled; open positions
    # keep their broker_stop_id and broker_take_id untouched.
    assert cancelled == ["entry-2"]
    assert not any(
        call[0] == "execute_order" for call in broker.calls
    )
    assert any(
        "SET status='cancelled'" in query and params[1] == "shutdown"
        for query, params in db.execute_calls
    )
    # Verify that broker_stop_id and broker_take_id are NOT cleared for open positions
    assert not any(
        "SET broker_stop_id=NULL" in query
        for query, _ in db.execute_calls
    )


def test_shutdown_can_flatten_open_sandbox_positions():
    db = FakeDB(active=active_position())
    broker = FakeBroker()
    executor = make_executor(
        db=db,
        broker=broker,
        close_positions_on_shutdown=True,
    )

    executor.request_shutdown()
    executor.shutdown()

    assert executor.shutdown_requested.is_set()
    close_order = next(
        call for call in broker.calls if call[0] == "execute_order"
    )
    assert close_order[1]["direction"] == "sell"
    assert close_order[1]["order_type"] == "market"
    close_db = next(
        params
        for query, params in db.execute_calls
        if "SET status=%s, exit_ts=%s" in query
    )
    assert close_db[0] == "cancelled"
    assert close_db[3] == "shutdown"


def test_initialize_builds_unmodified_strategy_evaluator(monkeypatch):
    instruments = pd.DataFrame(
        [{"ticker": "SBER", "figi": "figi-sber", "lot_size": 10}]
    )
    db = FakeDB(instruments=instruments)
    loaded = {}

    class Evaluator:
        def __init__(self, config):
            loaded["config"] = config

        def load_context(self, *args):
            loaded["context"] = args

    strategy = {"patterns": ["levels_reversal"], "confirm_windows": [10]}
    monkeypatch.setattr(
        module,
        "get_paper_strategy",
        lambda _db: (strategy, ["SBER"], "strategy-1"),
    )
    monkeypatch.setattr(
        module,
        "build_4h_context",
        lambda *_args: {
            "levels": ["level"],
            "ts_4h": ["ts"],
            "atr_by_ts": {"ts": 1},
            "buy_ts": [],
        },
    )
    executor = LiveExecutor(
        db=db,
        broker=FakeBroker(),
        config={"enabled": True},
        evaluator_factory=Evaluator,
    )

    executor.initialize()

    assert loaded["config"] == strategy
    assert loaded["context"] == (
        ["level"],
        ["ts"],
        {"ts": 1},
        [],
        [],
        [],
        None,
    )
    assert isinstance(executor.evaluators["SBER"], Evaluator)


def test_initialize_filters_tickers_to_live_universe(monkeypatch):
    instruments = pd.DataFrame(
        [
            {"ticker": "SBER", "figi": "figi-sber", "lot_size": 10},
            {"ticker": "CBOM", "figi": "figi-cbom", "lot_size": 10},
        ]
    )
    db = FakeDB(instruments=instruments)

    class Evaluator:
        def load_context(self, *args):
            return None

        def __init__(self, config):
            self.config = config

    strategy = {"patterns": ["levels_reversal"]}
    monkeypatch.setattr(
        module,
        "get_paper_strategy",
        lambda _db: (strategy, ["SBER", "CBOM"], "strategy-1"),
    )
    monkeypatch.setattr(module, "get_live_trading_universe", lambda _db: ["SBER"])
    monkeypatch.setattr(
        module,
        "build_4h_context",
        lambda *_args: {
            "levels": ["level"],
            "ts_4h": ["ts"],
            "atr_by_ts": {"ts": 1},
            "buy_ts": [],
        },
    )
    executor = LiveExecutor(
        db=db,
        broker=FakeBroker(),
        config={"enabled": True},
        evaluator_factory=Evaluator,
    )

    executor.initialize()

    assert executor.tickers == ["SBER"]
    assert list(executor.evaluators) == ["SBER"]


def test_initialize_uses_live_universe_when_strategy_has_no_overlap(monkeypatch):
    instruments = pd.DataFrame(
        [{"ticker": "SBER", "figi": "figi-sber", "lot_size": 10}]
    )
    db = FakeDB(instruments=instruments)

    class Evaluator:
        def __init__(self, config):
            self.config = config

        def load_context(self, *args):
            return None

    monkeypatch.setattr(
        module,
        "get_paper_strategy",
        lambda _db: ({"patterns": ["levels_reversal"]}, ["CBOM"], "strategy-1"),
    )
    monkeypatch.setattr(module, "get_live_trading_universe", lambda _db: ["SBER"])
    monkeypatch.setattr(
        module,
        "build_4h_context",
        lambda *_args: {
            "levels": ["level"],
            "ts_4h": ["ts"],
            "atr_by_ts": {"ts": 1},
            "buy_ts": [],
        },
    )
    executor = LiveExecutor(
        db=db,
        broker=FakeBroker(),
        config={"enabled": True},
        evaluator_factory=Evaluator,
    )

    executor.initialize()

    assert executor.tickers == ["SBER"]
    assert list(executor.evaluators) == ["SBER"]


def test_runtime_migration_is_idempotent():
    db = FakeDB()

    ensure_live_positions_table(db)

    statements = [query for query, _ in db.execute_calls]
    assert "CREATE SCHEMA IF NOT EXISTS trading" in statements[0]
    assert "CREATE TABLE IF NOT EXISTS trading.live_positions" in statements[1]
    assert "CREATE INDEX IF NOT EXISTS idx_live_positions_active" in statements[2]


class _FakeWallClock:
    def __init__(self, wall: datetime):
        self.wall = wall
        self.mono = 0.0

    def now(self) -> datetime:
        return self.wall

    def clock(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        if seconds <= 1.0 and self.wall >= datetime(2026, 8, 31, 10, 0, 0):
            seconds = 15 * 60
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


def test_until_session_end_waits_until_ten_then_stops_when_flat_after_nineteen(caplog):
    fake = _FakeWallClock(datetime(2026, 8, 30, 23, 0, 0))
    events = {"init": [], "monitor": 0, "bars": 0}
    executor = make_executor(
        now_fn=fake.now,
        clock=fake.clock,
        sleep_fn=fake.sleep,
        check_interval_seconds=60,
        context_refresh_seconds=10**6,
    )
    executor.install_signal_handlers = lambda: None

    def initialize():
        events["init"].append(datetime(
            fake.wall.year, fake.wall.month, fake.wall.day,
            fake.wall.hour, fake.wall.minute,
        ))
        executor.evaluators = {"SBER": object()}
        executor.strategy_name = "active-strategy"

    executor.initialize = initialize
    executor.monitor_positions = lambda: events.__setitem__(
        "monitor", events["monitor"] + 1
    )
    executor.process_latest_bars = lambda: events.__setitem__(
        "bars", events["bars"] + 1
    )
    executor.refresh_contexts = lambda: None
    executor.shutdown = lambda: None
    executor._active_positions = lambda: pd.DataFrame()

    with caplog.at_level("INFO", logger=module.__name__):
        executor.run(until_session_end=True)

    assert events["init"] == [datetime(2026, 8, 31, 10, 0)]
    assert fake.wall >= datetime(2026, 8, 31, 19, 0, 0)
    assert events["monitor"] >= 1
    assert events["bars"] >= 1
    assert "Waiting for MOEX session open at 2026-08-31 10:00 MSK" in caplog.text
    assert "monitoring stop/take until positions close" in caplog.text
    assert "No open sandbox positions after session close" in caplog.text


def test_stop_take_monitor_continues_after_nineteen_until_position_closes(caplog):
    fake = _FakeWallClock(datetime(2026, 8, 31, 18, 30, 0))
    events = {"bars": [], "monitor": 0}

    def active():
        if fake.wall < datetime(2026, 8, 31, 20, 30, 0):
            return pd.DataFrame([{"id": 1, "ticker": "SBER"}])
        return pd.DataFrame()

    executor = make_executor(
        now_fn=fake.now,
        clock=fake.clock,
        sleep_fn=fake.sleep,
        check_interval_seconds=60,
        context_refresh_seconds=10**6,
    )
    executor.install_signal_handlers = lambda: None

    def initialize():
        executor.evaluators = {"SBER": object()}
        executor.strategy_name = "active-strategy"

    def process_bars():
        events["bars"].append(datetime(
            fake.wall.year, fake.wall.month, fake.wall.day,
            fake.wall.hour, fake.wall.minute,
        ))

    executor.initialize = initialize
    executor.monitor_positions = lambda: events.__setitem__(
        "monitor", events["monitor"] + 1
    )
    executor.process_latest_bars = process_bars
    executor.refresh_contexts = lambda: None
    executor.shutdown = lambda: None
    executor._active_positions = active

    with caplog.at_level("INFO", logger=module.__name__):
        executor.run(until_session_end=True)

    assert fake.wall >= datetime(2026, 8, 31, 20, 30, 0)
    assert events["monitor"] >= 1
    assert events["bars"]
    assert all(ts < datetime(2026, 8, 31, 19, 0, 0) for ts in events["bars"])
    assert "monitoring stop/take until positions close" in caplog.text


def test_duration_minutes_does_not_wait_for_session_open():
    fake = _FakeWallClock(datetime(2026, 8, 30, 23, 0, 0))
    events = {"init": []}
    executor = make_executor(
        now_fn=fake.now,
        clock=fake.clock,
        sleep_fn=fake.sleep,
        check_interval_seconds=60,
        context_refresh_seconds=10**6,
    )
    executor.install_signal_handlers = lambda: None

    def initialize():
        events["init"].append(datetime(
            fake.wall.year, fake.wall.month, fake.wall.day,
            fake.wall.hour, fake.wall.minute,
        ))
        executor.evaluators = {"SBER": object()}
        executor.strategy_name = "active-strategy"

    executor.initialize = initialize
    executor.monitor_positions = lambda: None
    executor.process_latest_bars = lambda: None
    executor.refresh_contexts = lambda: None
    executor.shutdown = lambda: None

    executor.run(duration_minutes=1)

    assert events["init"] == [datetime(2026, 8, 30, 23, 0)]
    assert fake.wall < datetime(2026, 8, 31, 10, 0, 0)


# --- Issue #151: trailing config validation -----------------------------------


def test_trailing_config_defaults_are_accepted():
    """Executor accepts config with default trailing switches."""
    executor = make_executor()
    # Should not raise during construction or validation
    assert executor.config.get("trailing_kill_switch") is False
    assert executor.config.get("live_trailing_enabled") is True


@pytest.mark.parametrize(
    "key,bad_value,error_fragment",
    [
        ("trailing_kill_switch", "yes", "must be a boolean"),
        ("trailing_kill_switch", 1, "must be a boolean"),
        ("live_trailing_enabled", None, "must be a boolean"),
        ("trailing_protective_ticks", -1, "non-negative integer"),
        ("trailing_protective_ticks", 2.5, "non-negative integer"),
        ("trailing_ticker_allowlist", "SBER", "must be a list"),
        ("trailing_ticker_allowlist", [123], "must contain only strings"),
    ],
)
def test_trailing_config_rejects_bad_values(key, bad_value, error_fragment):
    with pytest.raises(ValueError, match=error_fragment):
        make_executor(**{key: bad_value})


def test_trailing_config_explicit_values_are_accepted():
    executor = make_executor(
        trailing_kill_switch=True,
        live_trailing_enabled=False,
        trailing_protective_ticks=10,
        trailing_ticker_allowlist=["SBER", "LKOH"],
    )
    assert executor.config["trailing_kill_switch"] is True
    assert executor.config["live_trailing_enabled"] is False
    assert executor.config["trailing_protective_ticks"] == 10
    assert executor.config["trailing_ticker_allowlist"] == ["SBER", "LKOH"]



# --- Issue #151: tick_align tests -------------------------------------------


@pytest.mark.parametrize(
    "price,increment,direction,expected",
    [
        # Down: floor to nearest tick
        (100.37, 0.05, "down", 100.35),
        (100.35, 0.05, "down", 100.35),  # already aligned
        (95.123, 0.01, "down", 95.12),
        (95.129, 0.01, "down", 95.12),
        (100.007, 0.01, "down", 100.00),
        # Up: ceil to nearest tick
        (100.37, 0.05, "up", 100.40),
        (100.35, 0.05, "up", 100.35),  # already aligned
        (95.121, 0.01, "up", 95.13),
        (95.120, 0.01, "up", 95.12),  # already aligned
        # Real-world increments
        (15234.5, 0.5, "down", 15234.5),  # LKOH-style
        (15234.3, 0.5, "down", 15234.0),
        (15234.3, 0.5, "up", 15234.5),
    ],
)
def test_tick_align_rounds_correctly(price, increment, direction, expected):
    result = tick_align(price, increment, direction)
    assert result == pytest.approx(expected, abs=1e-9)


def test_tick_align_returns_original_on_invalid_increment():
    """tick_align is a no-op when increment is zero, negative, or non-finite."""
    assert tick_align(100.5, 0.0) == 100.5
    assert tick_align(100.5, -0.01) == 100.5
    assert tick_align(100.5, float("nan")) == 100.5
    assert tick_align(100.5, float("inf")) == 100.5



# --- Issue #151: trailing ratchet tests --------------------------------------


def test_apply_trailing_returns_none_when_disabled():
    """_apply_trailing returns None when trailing_enabled=False."""
    executor = make_executor()
    row = {
        "id": 1,
        "trailing_enabled": False,
        "trailing_steps": None,
        "entry_price": 100.0,
        "stop_price": 95.0,
        "take_price": 110.0,
    }
    result = executor._apply_trailing(row, current_price=105.0)
    assert result is None


def test_apply_trailing_returns_none_when_kill_switch_on():
    """_apply_trailing returns None when trailing_kill_switch=True."""
    executor = make_executor(trailing_kill_switch=True)
    row = {
        "id": 1,
        "trailing_enabled": True,
        "trailing_steps": json.dumps([{"trigger": 2.0, "stop": 1.0}]),
        "entry_price": 100.0,
        "stop_price": 95.0,
        "take_price": 110.0,
        "risk_r": 5.0,
        "current_stop_price": 95.0,
        "step_reached": 0,
    }
    result = executor._apply_trailing(row, current_price=115.0)
    assert result is None


def test_apply_trailing_returns_none_when_no_steps():
    """_apply_trailing returns None when trailing_steps is None."""
    executor = make_executor()
    row = {
        "id": 1,
        "trailing_enabled": True,
        "trailing_steps": None,
        "entry_price": 100.0,
        "stop_price": 95.0,
        "take_price": 110.0,
    }
    result = executor._apply_trailing(row, current_price=105.0)
    assert result is None


def test_apply_trailing_returns_none_when_price_below_next_step():
    """_apply_trailing returns None when current price hasn't reached next step trigger."""
    executor = make_executor()
    # Step triggers at 2.0R (110), stop at 1.0R (105)
    row = {
        "id": 1,
        "trailing_enabled": True,
        "trailing_steps": json.dumps([{"trigger": 2.0, "stop": 1.0}]),
        "entry_price": 100.0,
        "stop_price": 95.0,
        "take_price": 115.0,
        "risk_r": 5.0,
        "current_stop_price": 95.0,
        "step_reached": 0,
    }
    # Price 108 < trigger 110, so no ratchet
    result = executor._apply_trailing(row, current_price=108.0)
    assert result is None


def test_apply_trailing_ratchets_when_price_reaches_trigger():
    """_apply_trailing updates DB and returns new stop when trigger reached."""
    db = FakeDB()
    executor = make_executor(db=db)
    # Step triggers at 2.0R (110), stop at 1.0R (105)
    row = {
        "id": 1,
        "ticker": "SBER",
        "trailing_enabled": True,
        "trailing_steps": json.dumps([{"trigger": 2.0, "stop": 1.0}]),
        "entry_price": 100.0,
        "stop_price": 95.0,
        "take_price": 115.0,
        "risk_r": 5.0,
        "current_stop_price": 95.0,
        "step_reached": 0,
    }
    # Price 112 > trigger 110, so ratchet to stop=105
    result = executor._apply_trailing(row, current_price=112.0)
    assert result == pytest.approx(105.0, abs=0.01)
    # Verify UPDATE was executed
    assert len(db.execute_calls) == 1
    query, params = db.execute_calls[0]
    assert "UPDATE trading.live_positions" in query
    assert params[0] == pytest.approx(105.0, abs=0.01)  # new_stop
    assert params[1] == 1  # new_step
    assert params[2] == 1  # position_id
    assert params[3] == 1  # new_step (for WHERE clause)


def test_apply_trailing_returns_none_when_step_already_reached():
    """_apply_trailing returns None when step_reached >= new_step (monotonicity)."""
    db = FakeDB()
    executor = make_executor(db=db)
    row = {
        "id": 1,
        "ticker": "SBER",
        "trailing_enabled": True,
        "trailing_steps": json.dumps([{"trigger": 2.0, "stop": 1.0}]),
        "entry_price": 100.0,
        "stop_price": 95.0,
        "take_price": 115.0,
        "risk_r": 5.0,
        "current_stop_price": 105.0,
        "step_reached": 1,  # Already reached step 1
    }
    # Price 112 > trigger 110, but step_reached=1 already, so no ratchet
    result = executor._apply_trailing(row, current_price=112.0)
    assert result is None
    # Verify no UPDATE was executed
    assert len(db.execute_calls) == 0



# --- Issue #151: trailing arming on open --------------------------------------


def _find_insert_call(db):
    """Find the INSERT INTO trading.live_positions call in execute_calls."""
    for query, params in db.execute_calls:
        if "INSERT INTO trading.live_positions" in query:
            return query, params
    return None, None


def test_open_position_arms_trailing_when_enabled():
    """When trailing is enabled in strategy_config, INSERT includes trailing columns."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)
    # Set trailing_stop in strategy_config (as if loaded from DB)
    executor.strategy_config = {
        "patterns": ["levels_reversal"],
        "trailing_stop": {
            "enabled": True,
            "steps": [
                {"trigger": 2.0, "stop": 1.0},
                {"trigger": 3.0, "stop": 2.0},
            ],
        },
    }

    executor.process_signal(
        "SBER",
        {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
        imbalance=1.5,
    )

    query, params = _find_insert_call(db)
    assert query is not None, "INSERT not found in execute_calls"
    # Params: ticker, instrument_id, signal_ts, entry_price, lot_size,
    #         size_lots, stop_price, take_price, broker_order_id, status,
    #         strategy_name, trailing_enabled, trailing_steps, risk_r,
    #         current_stop_price, step_reached
    assert params[11] is True  # trailing_enabled
    assert params[12] is not None  # trailing_steps (JSON string)
    assert params[13] == pytest.approx(5.0, abs=0.01)  # risk_r = 100 - 95
    assert params[14] == pytest.approx(95.0)  # current_stop_price
    assert params[15] == 0  # step_reached
    # Verify JSON is valid
    import json
    steps = json.loads(params[12])
    assert len(steps) == 2


def test_open_position_skips_trailing_when_disabled_in_config():
    """When live_trailing_enabled=False, INSERT has trailing_enabled=False."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(
        db=db, broker=broker, live_trailing_enabled=False
    )
    executor.strategy_config = {
        "patterns": ["levels_reversal"],
        "trailing_stop": {
            "enabled": True,
            "steps": [{"trigger": 2.0, "stop": 1.0}],
        },
    }

    executor.process_signal(
        "SBER",
        {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
        imbalance=1.5,
    )

    query, params = _find_insert_call(db)
    assert query is not None
    assert params[11] is False  # trailing_enabled


def test_open_position_skips_trailing_when_kill_switch_on():
    """When trailing_kill_switch=True, INSERT has trailing_enabled=False."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(
        db=db, broker=broker, trailing_kill_switch=True
    )
    executor.strategy_config = {
        "patterns": ["levels_reversal"],
        "trailing_stop": {
            "enabled": True,
            "steps": [{"trigger": 2.0, "stop": 1.0}],
        },
    }

    executor.process_signal(
        "SBER",
        {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
        imbalance=1.5,
    )

    query, params = _find_insert_call(db)
    assert query is not None
    assert params[11] is False  # trailing_enabled


def test_open_position_no_trailing_without_strategy_config():
    """When strategy_config has no trailing_stop, INSERT has trailing_enabled=False."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)
    executor.strategy_config = {"patterns": ["levels_reversal"]}

    executor.process_signal(
        "SBER",
        {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
        imbalance=1.5,
    )

    query, params = _find_insert_call(db)
    assert query is not None
    assert params[11] is False  # trailing_enabled
    assert params[12] is None  # trailing_steps
    assert params[13] is None  # risk_r



# --- Issue #151: close position with execution facts --------------------------


def test_close_position_records_model_and_actual_price():
    """_close_db_position writes exit_price_model, exit_price_actual, slippage metrics."""
    db = FakeDB()
    executor = make_executor(db=db)
    row = {
        "id": 1,
        "ticker": "SBER",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "size_lots": 10,
        "lot_size": 10,
        "risk_r": 5.0,
    }
    executor._close_db_position(
        row,
        reason="stop",
        exit_price=95.0,  # model price
        exit_price_actual=94.8,  # actual fill (slippage)
        lots_executed=10,
    )
    query, params = db.execute_calls[0]
    assert "exit_price_model=%s" in query
    assert "exit_price_actual=%s" in query
    assert "slippage_bp=%s" in query
    assert "slippage_r=%s" in query
    assert "lots_executed=%s" in query
    # Params: status, exit_ts, exit_price, exit_reason, pnl_rub,
    #         exit_price_model, exit_price_actual, slippage_bp, slippage_r,
    #         lots_executed, id
    assert params[2] == pytest.approx(94.8)  # exit_price = actual (Variant A)
    assert params[5] == pytest.approx(95.0)  # exit_price_model
    assert params[6] == pytest.approx(94.8)  # exit_price_actual
    assert params[7] == pytest.approx(-21.0526, abs=0.01)  # slippage_bp = (94.8/95 - 1) * 1e4
    assert params[8] is not None  # slippage_r
    assert params[9] == 10  # lots_executed


def test_close_position_handles_missing_actual_price():
    """_close_db_position works when exit_price_actual is None."""
    db = FakeDB()
    executor = make_executor(db=db)
    row = {
        "id": 1,
        "ticker": "SBER",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "size_lots": 10,
        "lot_size": 10,
        "risk_r": 5.0,
    }
    executor._close_db_position(
        row,
        reason="stop",
        exit_price=95.0,
        exit_price_actual=None,
        lots_executed=None,
    )
    query, params = db.execute_calls[0]
    assert params[2] == pytest.approx(95.0)  # exit_price = model (fallback)
    assert params[5] == pytest.approx(95.0)  # exit_price_model
    assert params[6] is None  # exit_price_actual
    assert params[7] is None  # slippage_bp
    assert params[8] is None  # slippage_r
    assert params[9] is None  # lots_executed


def test_close_position_trailing_uses_closed_trailing_status():
    """_close_db_position with reason='trailing' sets status='closed_trailing'."""
    db = FakeDB()
    executor = make_executor(db=db)
    row = {
        "id": 1,
        "ticker": "SBER",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "current_stop_price": 105.0,
        "step_reached": 1,
        "risk_r": 5.0,
        "size_lots": 10,
        "lot_size": 10,
    }
    executor._close_db_position(
        row,
        reason="trailing",
        exit_price=105.0,
        exit_price_actual=104.9,
        lots_executed=10,
    )
    query, params = db.execute_calls[0]
    assert params[0] == "closed_trailing"  # status
    assert params[3] == "trailing"  # exit_reason


# --- Issue #151: TokenBucket.try_acquire() tests ------------------------------


def test_token_bucket_try_acquire_returns_true_when_token_available():
    """try_acquire() returns True when bucket has tokens."""
    bucket = TokenBucket(rate_per_second=2.0)
    assert bucket.try_acquire() is True


def test_token_bucket_try_acquire_returns_false_when_exhausted():
    """try_acquire() returns False when bucket is empty."""
    bucket = TokenBucket(rate_per_second=1.0)
    bucket.try_acquire()  # consume the only token
    assert bucket.try_acquire() is False


def test_token_bucket_try_acquire_recovers_over_time():
    """try_acquire() returns True after enough time passes."""
    now = [0.0]
    bucket = TokenBucket(
        rate_per_second=1.0,
        clock=lambda: now[0],
    )
    bucket.try_acquire()  # consume initial token
    assert bucket.try_acquire() is False  # no tokens left
    now[0] = 1.0  # advance 1 second
    assert bucket.try_acquire() is True  # token recovered


def test_broker_call_nonblocking_returns_none_when_exhausted():
    """_broker_call(blocking=False) returns None when rate limit exhausted."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)
    # Consume all tokens
    executor.rate_limiter.tokens = 0
    executor.rate_limiter.updated_at = executor.rate_limiter.clock()
    # Non-blocking call should return None
    result = executor._broker_call("check_balance", blocking=False)
    assert result is None
    # Broker method should not have been called
    assert len(broker.calls) == 0


def test_broker_call_blocking_still_works():
    """_broker_call(blocking=True) still works as before (default behavior)."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)
    # Blocking call should work
    result = executor._broker_call("check_balance")
    assert result == broker.balance
    assert len(broker.calls) == 1


# --- Issue #151: Kill switch integration tests --------------------------------


def test_kill_switch_prevents_trailing_arming():
    """When kill switch is ON, new positions are not armed with trailing."""
    db = FakeDB(app_settings={"trailing_kill_switch": True})
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)
    # Refresh kill switch from DB
    executor._refresh_kill_switch()
    
    # Set trailing config in strategy
    executor.strategy_config = {
        "patterns": ["levels_reversal"],
        "trailing_stop": {
            "enabled": True,
            "steps": [{"trigger": 2.0, "stop": 1.0}],
        },
    }
    
    # Open position
    executor.process_signal(
        "SBER",
        {"action": "enter", "entry_price": 100.0, "stop": 95.0, "take": 110.0},
        imbalance=1.5,
    )
    
    # Check INSERT params
    query, params = _find_insert_call(db)
    assert query is not None
    assert params[11] is False  # trailing_enabled=False due to kill switch


def test_kill_switch_preserves_armed_positions():
    """When kill switch is ON, already armed positions keep their state."""
    db = FakeDB(app_settings={"trailing_kill_switch": True})
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)
    
    # Position already armed with trailing
    active_pos = active_position(
        trailing_enabled=True,
        trailing_steps=json.dumps([{"trigger": 2.0, "stop": 1.0}]),
        risk_r=5.0,
        current_stop_price=105.0,
        step_reached=1,
    )
    db.active = active_pos
    
    # Kill switch ON
    executor._refresh_kill_switch()
    
    # Apply trailing should be skipped
    row = active_pos.iloc[0].to_dict()
    result = executor._apply_trailing(row, current_price=115.0)
    assert result is None  # No ratchet due to kill switch


def test_invalid_trailing_config_disables_arming():
    """When trailing config is invalid, trailing_enabled=False."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)
    
    # Invalid trailing config (trigger < stop)
    executor.strategy_config = {
        "patterns": ["levels_reversal"],
        "trailing_stop": {
            "enabled": True,
            "steps": [{"trigger": 1.0, "stop": 2.0}],  # Invalid: stop > trigger
        },
    }
    
    executor.process_signal(
        "SBER",
        {"action": "enter", "entry_price": 100.0, "stop": 95.0, "take": 110.0},
        imbalance=1.5,
    )
    
    query, params = _find_insert_call(db)
    assert query is not None
    assert params[11] is False  # trailing_enabled=False due to invalid config
    assert params[12] is None  # trailing_steps=None





# --- Issue #174: advisory lock on dedicated connection ------------------------


def test_advisory_lock_uses_dedicated_connection(monkeypatch):
    """Issue #174: advisory lock and unlock happen on the SAME dedicated connection."""
    instruments = pd.DataFrame(
        [{"ticker": "SBER", "figi": "figi-sber", "lot_size": 10}]
    )
    db = FakeDB(instruments=instruments)

    class Evaluator:
        def __init__(self, config):
            pass

        def load_context(self, *args):
            pass

    strategy = {"patterns": ["levels_reversal"]}
    monkeypatch.setattr(
        module,
        "get_paper_strategy",
        lambda _db: (strategy, ["SBER"], "strategy-1"),
    )
    monkeypatch.setattr(
        module,
        "build_4h_context",
        lambda *_args: {
            "levels": ["level"],
            "ts_4h": ["ts"],
            "atr_by_ts": {"ts": 1},
            "buy_ts": [],
        },
    )
    executor = LiveExecutor(
        db=db,
        broker=FakeBroker(),
        config={"enabled": True},
        evaluator_factory=Evaluator,
    )

    # Initialize acquires advisory lock on dedicated connection
    executor.initialize()

    # Verify lock was acquired on the dedicated connection
    assert executor._advisory_lock_acquired is True
    assert executor._lock_conn is db._dedicated_conn
    lock_queries = [
        q for cursor in db._dedicated_conn.cursors for q, _ in cursor.executed
        if "pg_try_advisory_lock" in q
    ]
    assert len(lock_queries) == 1

    # Shutdown releases advisory lock on the SAME connection
    executor.shutdown()

    # Verify unlock happened on the same connection
    assert executor._lock_conn is None
    assert executor._advisory_lock_acquired is False
    unlock_queries = [
        q for cursor in db._dedicated_conn.cursors for q, _ in cursor.executed
        if "pg_advisory_unlock" in q
    ]
    assert len(unlock_queries) == 1

    # Verify release_dedicated_connection was called
    release_calls = [
        action for action, conn in db.dedicated_connection_calls
        if action == "release"
    ]
    assert len(release_calls) >= 1


# --- Issue #174: main loop protection against transient errors ----------------


def test_run_continues_after_transient_error(monkeypatch):
    """Issue #174: single error in monitor_positions does not stop the executor."""
    instruments = pd.DataFrame(
        [{"ticker": "SBER", "figi": "figi-sber", "lot_size": 10}]
    )
    db = FakeDB(instruments=instruments)

    class Evaluator:
        def __init__(self, config):
            pass

        def load_context(self, *args):
            pass

    strategy = {"patterns": ["levels_reversal"]}
    monkeypatch.setattr(
        module,
        "get_paper_strategy",
        lambda _db: (strategy, ["SBER"], "strategy-1"),
    )
    monkeypatch.setattr(
        module,
        "build_4h_context",
        lambda *_args: {
            "levels": ["level"],
            "ts_4h": ["ts"],
            "atr_by_ts": {"ts": 1},
            "buy_ts": [],
        },
    )
    executor = LiveExecutor(
        db=db,
        broker=FakeBroker(),
        config={"enabled": True, "check_interval_seconds": 0.1, "context_refresh_seconds": 60},
        evaluator_factory=Evaluator,
    )

    # Make monitor_positions fail once
    call_count = [0]
    original_monitor = executor.monitor_positions

    def failing_monitor():
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("Transient error")
        return original_monitor()

    executor.monitor_positions = failing_monitor

    # Run for 0.3 seconds (should complete 3 cycles)
    executor.run(duration_minutes=0.005)

    # Verify executor continued after error
    assert call_count[0] >= 2
    assert executor._consecutive_errors == 0  # reset after successful call


def test_run_stops_after_max_errors(monkeypatch):
    """Issue #174: executor stops after MAX_CONSECUTIVE_ERRORS consecutive failures."""
    instruments = pd.DataFrame(
        [{"ticker": "SBER", "figi": "figi-sber", "lot_size": 10}]
    )
    db = FakeDB(instruments=instruments)

    class Evaluator:
        def __init__(self, config):
            pass

        def load_context(self, *args):
            pass

    strategy = {"patterns": ["levels_reversal"]}
    monkeypatch.setattr(
        module,
        "get_paper_strategy",
        lambda _db: (strategy, ["SBER"], "strategy-1"),
    )
    monkeypatch.setattr(
        module,
        "build_4h_context",
        lambda *_args: {
            "levels": ["level"],
            "ts_4h": ["ts"],
            "atr_by_ts": {"ts": 1},
            "buy_ts": [],
        },
    )
    executor = LiveExecutor(
        db=db,
        broker=FakeBroker(),
        config={"enabled": True, "check_interval_seconds": 0.1, "context_refresh_seconds": 60},
        evaluator_factory=Evaluator,
    )

    # Make monitor_positions always fail
    def failing_monitor():
        raise RuntimeError("Persistent error")

    executor.monitor_positions = failing_monitor

    # Run for 1 second (should stop after MAX_CONSECUTIVE_ERRORS)
    executor.run(duration_minutes=0.02)

    # Verify executor stopped after MAX_CONSECUTIVE_ERRORS
    assert executor._consecutive_errors >= MAX_CONSECUTIVE_ERRORS



# --- Issue #174: safe shutdown leaves positions protected -------------------


def test_shutdown_leaves_position_protected_by_default():
    """Issue #174: shutdown with close_positions=False leaves stop/take orders intact."""
    active = active_position(
        broker_stop_id="stop-1",
        broker_take_id="take-1",
    )
    db = FakeDB(active=active)
    broker = FakeBroker()
    executor = make_executor(
        db=db,
        broker=broker,
        close_positions_on_shutdown=False,
    )

    executor.shutdown()

    # No cancel_order calls for stop or take
    cancelled = [
        call[1]["order_id"]
        for call in broker.calls
        if call[0] == "cancel_order"
    ]
    assert cancelled == []

    # No execute_order calls (no flattening)
    assert not any(call[0] == "execute_order" for call in broker.calls)

    # No UPDATE clearing broker IDs for open positions
    assert not any(
        "SET broker_stop_id=NULL" in query
        for query, _ in db.execute_calls
    )
    assert not any(
        "SET broker_take_id=NULL" in query
        for query, _ in db.execute_calls
    )


def test_shutdown_still_flattens_when_explicitly_requested():
    """Issue #174: shutdown with close_positions=True still closes positions (regression)."""
    active = active_position(
        broker_stop_id="stop-1",
        broker_take_id="take-1",
    )
    db = FakeDB(active=active)
    broker = FakeBroker()
    executor = make_executor(
        db=db,
        broker=broker,
        close_positions_on_shutdown=True,
    )

    executor.request_shutdown()
    executor.shutdown()

    # Cancel stop and take before market sell
    cancelled = [
        call[1]["order_id"]
        for call in broker.calls
        if call[0] == "cancel_order"
    ]
    assert "stop-1" in cancelled
    assert "take-1" in cancelled

    # Market sell order was submitted
    assert any(
        call[0] == "execute_order"
        and call[1]["direction"] == "sell"
        and call[1]["order_type"] == "market"
        for call in broker.calls
    )



# --- Issue #174: isolation at unit-of-work level -----------------------------


def test_error_in_one_ticker_does_not_break_others(monkeypatch):
    """Issue #174: error in one ticker does not break processing of others."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)

    # Set up two tickers with mock evaluators
    class MockEvaluator:
        def update_context(self, **kwargs):
            pass

        def check_entry(self, bar):
            return {"action": "enter", "entry_price": 100, "stop": 95, "take": 110}

    executor.evaluators = {
        "SBER": MockEvaluator(),
        "GAZP": MockEvaluator(),
    }
    executor.last_processed = {"SBER": None, "GAZP": None}

    # Mock build_1m_context to fail for SBER, succeed for GAZP
    def mock_build_1m_context(db, ticker, config):
        if ticker == "SBER":
            raise RuntimeError("Transient error for SBER")
        return (
            pd.DataFrame([{"timestamp": pd.Timestamp("2026-08-31 11:01:00")}]),
            [True],
        )

    monkeypatch.setattr(module, "build_1m_context", mock_build_1m_context)

    # Mock process_signal to track calls
    processed_tickers = []

    def mock_process_signal(ticker, decision, signal_ts=None, imbalance=None):
        processed_tickers.append(ticker)
        return {"executed": True, "reason": "open"}

    executor.process_signal = mock_process_signal

    # Run process_latest_bars
    executed = executor.process_latest_bars()

    # Verify GAZP was processed despite SBER error
    assert "GAZP" in processed_tickers
    assert "SBER" not in processed_tickers
    assert executed == 1


def test_error_in_one_position_does_not_break_others():
    """Issue #174: error in one position does not break monitoring of others."""
    # Two positions: SBER (will fail on _close_db_position), GAZP (will succeed)
    active = pd.concat(
        [
            active_position(),  # SBER, status="open", broker_take_id="take-1"
            active_position(
                id=42,
                ticker="GAZP",
                instrument_id="figi-gazp",
                broker_take_id=None,  # Missing take, will trigger _place_take_order
            ),
        ],
        ignore_index=True,
    )
    db = FakeDB(active=active)
    # Only GAZP has a broker position; SBER's vanished
    broker_position = SimpleNamespace(
        ticker="GAZP",
        figi="figi-gazp",
        instrument_uid="",
        current_price=Decimal("100"),
    )
    broker = FakeBroker(positions=[broker_position])
    executor = make_executor(db=db, broker=broker)

    # Make _close_db_position fail for SBER (position 41)
    original_close = executor._close_db_position

    def failing_close(row, reason, exit_price, **kwargs):
        if int(row["id"]) == 41:
            raise RuntimeError("Transient error for position 41")
        return original_close(row, reason, exit_price, **kwargs)

    executor._close_db_position = failing_close

    # Run monitor_positions
    changes = executor.monitor_positions()

    # Verify GAZP was processed despite SBER error
    # GAZP has broker_take_id=None, so _place_take_order should be called
    assert any(
        call[0] == "execute_order" and call[1]["direction"] == "sell"
        for call in broker.calls
    )
    assert changes >= 1


def test_error_in_one_ticker_refresh_does_not_break_others(monkeypatch):
    """Issue #174: error in one ticker does not break context refresh of others."""
    db = FakeDB()
    broker = FakeBroker()
    executor = make_executor(db=db, broker=broker)

    # Set up two tickers with mock evaluators
    class MockEvaluator:
        def __init__(self):
            self.updated = False

        def update_context(self, **kwargs):
            self.updated = True

    sber_eval = MockEvaluator()
    gazp_eval = MockEvaluator()
    executor.evaluators = {
        "SBER": sber_eval,
        "GAZP": gazp_eval,
    }

    # Mock build_4h_context to fail for SBER, succeed for GAZP
    def mock_build_4h_context(db, ticker, config):
        if ticker == "SBER":
            raise RuntimeError("Transient error for SBER")
        return {
            "levels": ["level"],
            "ts_4h": ["ts"],
            "atr_by_ts": {"ts": 1},
            "buy_ts": [],
        }

    monkeypatch.setattr(module, "build_4h_context", mock_build_4h_context)

    # Run refresh_contexts
    executor.refresh_contexts()

    # Verify GAZP was updated despite SBER error
    assert not sber_eval.updated
    assert gazp_eval.updated



# --- Issue #174: heartbeat and metrics ----------------------------------------


def test_heartbeat_updates_each_iteration():
    """Issue #174: heartbeat_ts and iterations_total update after each successful iteration."""
    # Use time before 10:00 to avoid _FakeWallClock.sleep() magic (replaces 1s with 900s at >=10:00)
    fake = _FakeWallClock(datetime(2026, 8, 31, 9, 0, 0))
    events = {"monitor": 0, "bars": 0}
    executor = make_executor(
        now_fn=fake.now,
        clock=fake.clock,
        sleep_fn=fake.sleep,
        check_interval_seconds=1,
        context_refresh_seconds=10 ** 6,
    )
    executor.install_signal_handlers = lambda: None

    def initialize():
        executor.evaluators = {"SBER": object()}
        executor.strategy_name = "active-strategy"

    executor.initialize = initialize
    executor.monitor_positions = lambda: events.__setitem__("monitor", events["monitor"] + 1)
    executor.process_latest_bars = lambda: events.__setitem__("bars", events["bars"] + 1)
    executor.refresh_contexts = lambda: None
    executor.shutdown = lambda: None
    executor._active_positions = lambda: pd.DataFrame()

    # Run for 0.05 minutes (3 seconds, should complete 3 iterations)
    executor.run(duration_minutes=0.05)

    # Verify heartbeat and iterations updated
    assert executor.iterations_total >= 3
    assert executor.heartbeat_ts is not None
    assert executor.errors_total == 0
    assert executor.last_error_at is None

    # Verify get_metrics returns the same values
    metrics = executor.get_metrics()
    assert metrics["iterations_total"] == executor.iterations_total
    assert metrics["heartbeat_ts"] == executor.heartbeat_ts
    assert metrics["errors_total"] == 0
    assert metrics["errors_consecutive"] == 0
    assert metrics["last_error_at"] is None


def test_metrics_track_errors():
    """Issue #174: errors_total and last_error_at update when errors occur."""
    # Use time before 10:00 to avoid _FakeWallClock.sleep() magic
    fake = _FakeWallClock(datetime(2026, 8, 31, 9, 0, 0))
    executor = make_executor(
        now_fn=fake.now,
        clock=fake.clock,
        sleep_fn=fake.sleep,
        check_interval_seconds=1,
        context_refresh_seconds=10 ** 6,
    )
    executor.install_signal_handlers = lambda: None

    def initialize():
        executor.evaluators = {"SBER": object()}
        executor.strategy_name = "active-strategy"

    executor.initialize = initialize
    executor.refresh_contexts = lambda: None
    executor.shutdown = lambda: None
    executor._active_positions = lambda: pd.DataFrame()
    executor.process_latest_bars = lambda: 0

    # Make monitor_positions fail
    def failing_monitor():
        raise RuntimeError("Test error")

    executor.monitor_positions = failing_monitor

    # Run for 0.02 minutes (should hit error multiple times and stop)
    executor.run(duration_minutes=0.02)

    # Verify error metrics updated
    assert executor.errors_total >= 1
    assert executor.last_error_at is not None

    # Verify get_metrics includes error info
    metrics = executor.get_metrics()
    assert metrics["errors_total"] == executor.errors_total
    assert metrics["last_error_at"] == executor.last_error_at
    assert metrics["errors_consecutive"] >= 1
