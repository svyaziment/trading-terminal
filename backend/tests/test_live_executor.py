from decimal import Decimal
from datetime import datetime, timedelta
from types import SimpleNamespace
import json
import uuid

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
from app.analytics.trading_config import (
    CANARY,
    CANARY_ENV,
    LIVE_TRADING,
    get_canary_bounds,
    get_canary_config,
    normalize_canary_ticker,
    validate_canary_values,
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
        seeded_kill_switch=False,
    ):
        self.active = active if active is not None else pd.DataFrame()
        self.instruments = (
            instruments if instruments is not None else pd.DataFrame()
        )
        self.app_settings = app_settings if app_settings is not None else {}
        # Issue #178: the fake models a database migrated with 20260928_001, so
        # the seeded ``live_kill_switch`` row exists even when a test only cares
        # about other settings. Pass ``seeded_kill_switch=None`` to simulate the
        # unmigrated case, where the executor must fail safe to ON.
        self.seeded_kill_switch = seeded_kill_switch
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
                if (
                    key == "live_kill_switch"
                    and self.seeded_kill_switch is not None
                ):
                    return Result(
                        pd.DataFrame(
                            [{"key": key, "value": self.seeded_kill_switch}]
                        )
                    )
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


def make_executor(
    *,
    db=None,
    broker=None,
    now_fn=None,
    clock=None,
    sleep_fn=None,
    canary=None,
    confirm_fn=None,
    **config,
):
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    if sleep_fn is not None:
        kwargs["sleep_fn"] = sleep_fn
    # Issue #194: the canary policy and its operator prompts are injected, so a
    # test never touches stdin and never inherits the deployment's CANARY_* env.
    if canary is not None:
        kwargs["canary"] = canary
    if confirm_fn is not None:
        kwargs["confirm_fn"] = confirm_fn
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
            # Issue #178: the global entry stop defaults to the state of a
            # migrated database with no emergency stop pulled. The shipped
            # in-memory default is fail-safe ON, so a test that wants the
            # blocked contour passes live_kill_switch=True explicitly.
            "live_kill_switch": False,
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


# --- Issue #175: broker stop orders, amend-trailing, OCO, fill reconciliation --


def stop_item(stop_order_id, status="STOP_ORDER_STATUS_ACTIVE", **overrides):
    data = {
        "stop_order_id": stop_order_id,
        "status": status,
        "instrument_uid": "figi-sber",
        "stop_price": Decimal("95"),
        "price": Decimal("94.95"),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def sell_operation(
    price="94.5",
    quantity=100,
    quantity_rest=0,
    trades=None,
    operation_type="OPERATION_TYPE_SELL",
    operation_id="op-1",
):
    return SimpleNamespace(
        id=operation_id,
        operation_type=operation_type,
        state="OPERATION_STATE_EXECUTED",
        quantity=quantity,
        quantity_rest=quantity_rest,
        price=Decimal(price),
        payment=Decimal("-9450"),
        figi="figi-sber",
        instrument_uid="figi-sber",
        trades=trades or [],
    )


class StopFakeBroker(FakeBroker):
    """Fake broker exposing the Issue #175 stop-order / operations surface."""

    def __init__(
        self,
        positions=None,
        balance=Decimal("50000"),
        stop_orders=None,
        operations=None,
        resting_orders=None,
        stop_error=None,
        cancel_stop_error=None,
        hide_posted_stops=False,
    ):
        super().__init__(positions=positions, balance=balance)
        self.stop_orders = list(stop_orders or [])
        self.operations = list(operations or [])
        self.resting_orders = list(resting_orders or [])
        self.stop_error = stop_error
        self.cancel_stop_error = cancel_stop_error
        self.hide_posted_stops = hide_posted_stops
        self.stop_number = 0
        self.posted_stops = []
        self.cancelled_stops = []

    def post_stop_order(self, **kwargs):
        self.calls.append(("post_stop_order", kwargs))
        if self.stop_error is not None:
            raise self.stop_error
        self.stop_number += 1
        stop_id = f"stop-{self.stop_number}"
        self.posted_stops.append(stop_id)
        if not self.hide_posted_stops:
            self.stop_orders.append(
                stop_item(stop_id, instrument_uid=kwargs["instrument_id"])
            )
        return SimpleNamespace(stop_order_id=stop_id, order_request_id="req-1")

    def get_stop_orders(self, status="active", **kwargs):
        self.calls.append(("get_stop_orders", {"status": status, **kwargs}))
        if status == "all":
            return list(self.stop_orders)
        return [
            stop
            for stop in self.stop_orders
            if stop.status == "STOP_ORDER_STATUS_ACTIVE"
        ]

    def cancel_stop_order(self, stop_order_id):
        self.calls.append(("cancel_stop_order", {"stop_order_id": stop_order_id}))
        if self.cancel_stop_error is not None:
            raise self.cancel_stop_error
        self.cancelled_stops.append(stop_order_id)
        self.stop_orders = [
            stop
            for stop in self.stop_orders
            if stop.stop_order_id != stop_order_id
        ]
        return SimpleNamespace(stop_order_id=stop_order_id, cancelled_at=None)

    def get_operations(self, **kwargs):
        self.calls.append(("get_operations", kwargs))
        return list(self.operations)

    def get_orders(self):
        self.calls.append(("get_orders", {}))
        return list(self.resting_orders)


def sber_position(current_price="100"):
    """Broker portfolio entry for SBER (lot size 10 in the fixture)."""
    return SimpleNamespace(
        ticker="SBER",
        figi="figi-sber",
        instrument_uid="",
        current_price=Decimal(current_price),
    )



def test_entry_arms_broker_stop_before_take_limit(caplog):
    """Issue #175: a filled entry posts a broker STOP_LOSS before the take."""
    db = FakeDB()
    broker = StopFakeBroker()
    executor = make_executor(db=db, broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
            imbalance=1.5,
        )

    assert result["reason"] == "open"
    assert [call[0] for call in broker.calls] == [
        "check_balance",
        "execute_order",
        "post_stop_order",
        "execute_order",
    ]
    stop_call = broker.calls[2][1]
    assert stop_call["instrument_id"] == "figi-sber"
    assert stop_call["quantity"] == 10
    assert stop_call["direction"] == "sell"
    assert stop_call["stop_order_type"] == "stop_loss"
    assert stop_call["stop_price"] == 95.0
    # trailing_protective_ticks=5 * min_price_increment=0.01 below the trigger
    assert stop_call["price"] == pytest.approx(94.95)
    # Issue #175 (PR #185): the stop order id is a random uuid4. The former
    # deterministic "live-stop-<position>-<step>-" prefix (uuid5) was dropped so
    # that every arming attempt stays unique at the broker; only the shape of
    # the id is assertable now.
    assert uuid.UUID(stop_call["order_id"]).version == 4
    assert any(
        "SET broker_stop_id=%s" in query and params == ("stop-1", 41)
        for query, params in db.execute_calls
    )
    assert "broker_stop_armed ticker=SBER position_id=41" in caplog.text
    assert executor.get_metrics()["stops_armed_total"] == 1


def test_entry_stop_failure_alerts_and_schedules_backoff(caplog):
    """Issue #175: a rejected PostStopOrder is alerted and retried with backoff."""
    db = FakeDB()
    broker = StopFakeBroker(stop_error=SandboxAPIError("invalid_price"))
    executor = make_executor(db=db, broker=broker, protection_retry_seconds=30)

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal(
            "SBER",
            {"action": "enter", "entry_price": 100, "stop": 95, "take": 110},
            imbalance=1.5,
        )

    # The entry still completed and the take-profit limit is armed.
    assert result["reason"] == "open"
    assert any(
        call[0] == "execute_order" and call[1]["order_type"] == "limit"
        for call in broker.calls
    )
    assert "protection_failed ticker=SBER position_id=41" in caplog.text
    assert "error_type=SandboxAPIError" in caplog.text
    metrics = executor.get_metrics()
    assert metrics["protection_failed_positions"] == [41]
    assert metrics["protection_failed_total"] == 1
    # Exponential backoff registered for the next arming attempt.
    assert executor._protection_retry_at[41] > executor.clock()


def test_monitor_arms_missing_broker_stop_and_alerts_invariant(caplog):
    """Issue #175: an open position without broker_stop_id is re-armed."""
    db = FakeDB(active=active_position())
    broker = StopFakeBroker(positions=[sber_position("100")])
    executor = make_executor(db=db, broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        changes = executor.monitor_positions()

    assert changes == 1
    stop_call = next(call for call in broker.calls if call[0] == "post_stop_order")
    assert stop_call[1]["stop_price"] == 95.0
    assert stop_call[1]["price"] == pytest.approx(94.95)
    assert (
        "Invariant violation: position_id=41 ticker=SBER is open without "
        "broker_stop_id" in caplog.text
    )
    metrics = executor.get_metrics()
    assert metrics["invariant_violations_total"] == 1
    assert metrics["stops_armed_total"] == 1


def test_monitor_rearms_stop_that_disappeared_at_the_broker(caplog):
    """Issue #175: broker_stop_id in DB but no active stop -> alert + re-arm."""
    db = FakeDB(active=active_position(broker_stop_id="stop-old"))
    broker = StopFakeBroker(positions=[sber_position("100")], stop_orders=[])
    executor = make_executor(db=db, broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        changes = executor.monitor_positions()

    assert (
        "Invariant violation: position_id=41 ticker=SBER has broker_stop_id="
        "stop-old but no active stop order" in caplog.text
    )
    # The stale id is cancelled first: before #175 it could be a resting limit.
    assert broker.cancelled_stops == ["stop-old"]
    assert any("SET broker_stop_id=NULL" in query for query, _ in db.execute_calls)
    assert any(
        "SET broker_stop_id=%s" in query and params == ("stop-1", 41)
        for query, params in db.execute_calls
    )
    assert changes == 1


def test_trailing_ratchet_amends_broker_stop_by_duplication(caplog):
    """Issue #175: post the higher stop, verify it, then cancel the lower one."""
    db = FakeDB(active=trailing_position())
    broker = StopFakeBroker(
        positions=[sber_position("112")],
        stop_orders=[stop_item("stop-old")],
    )
    executor = make_executor(db=db, broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        executor.monitor_positions()

    posted = [call for call in broker.calls if call[0] == "post_stop_order"]
    assert len(posted) == 1
    # Ladder step 1: trigger 2.0R (110) -> stop 1.0R (105), protective 5 ticks.
    assert posted[0][1]["stop_price"] == pytest.approx(105.0)
    assert posted[0][1]["price"] == pytest.approx(104.95)
    # Issue #175 (PR #185): random uuid4, see test_entry_arms_broker_stop.
    assert uuid.UUID(posted[0][1]["order_id"]).version == 4
    # PO mechanic: PostStopOrder -> GetStopOrders -> CancelStopOrder.
    call_names = [call[0] for call in broker.calls]
    assert call_names.index("post_stop_order") < call_names.index("get_stop_orders")
    assert call_names.index("get_stop_orders") < call_names.index("cancel_stop_order")
    assert broker.cancelled_stops == ["stop-old"]
    assert any(
        "SET broker_stop_id=%s" in query and params == ("stop-1", 41)
        for query, params in db.execute_calls
    )
    assert "trailing_amend_cancelled position_id=41" in caplog.text
    metrics = executor.get_metrics()
    assert metrics["stop_amend_total"] == 1
    assert metrics["stop_amend_failed_total"] == 0
    # Exactly one stop was armed and the invariant stayed intact.
    assert metrics["stops_armed_total"] == 1
    assert metrics["invariant_violations_total"] == 0


def test_trailing_amend_keeps_old_stop_when_post_fails(caplog):
    """Issue #175: a failed amend leaves the old stop armed (never naked)."""
    db = FakeDB(active=trailing_position())
    broker = StopFakeBroker(
        positions=[sber_position("112")],
        stop_orders=[stop_item("stop-old")],
        stop_error=SandboxAPIError("invalid_price"),
    )
    executor = make_executor(db=db, broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        executor.monitor_positions()

    assert "trailing_amend_failed position_id=41" in caplog.text
    assert broker.cancelled_stops == []
    assert executor.get_metrics()["stop_amend_failed_total"] == 1
    # The DB ratchet is kept — a stop never moves down — and the old id survives.
    assert any(
        "SET current_stop_price=%s, step_reached=%s" in query
        for query, _ in db.execute_calls
    )
    assert not any(
        "SET broker_stop_id=NULL" in query for query, _ in db.execute_calls
    )


def test_trailing_amend_defers_cancel_until_new_stop_is_visible(caplog):
    """Issue #175: an unverified amend never cancels the old stop right away."""
    clock = [0.0]
    db = FakeDB(active=trailing_position())
    broker = StopFakeBroker(
        positions=[sber_position("112")],
        stop_orders=[stop_item("stop-old")],
        hide_posted_stops=True,
    )
    executor = make_executor(db=db, broker=broker, clock=lambda: clock[0])

    with caplog.at_level("INFO", logger=module.__name__):
        executor.monitor_positions()

    assert "trailing_amend_unverified position_id=41" in caplog.text
    assert broker.cancelled_stops == []
    assert 41 in executor._pending_stop_cancels

    # A later cycle sees the new stop and only then removes the older one.
    broker.stop_orders.append(stop_item("stop-1"))
    clock[0] = 31.0
    executor._stop_ids_cache = None
    assert executor._cancel_pending_stops() == 1
    assert broker.cancelled_stops == ["stop-old"]
    assert executor._pending_stop_cancels == {}


def test_pending_amend_cancel_fires_after_the_deadline(caplog):
    """Issue #175: two active stops may not outlive the grace period."""
    clock = [0.0]
    db = FakeDB(active=trailing_position())
    broker = StopFakeBroker(
        positions=[sber_position("112")],
        stop_orders=[stop_item("stop-old")],
        hide_posted_stops=True,
    )
    executor = make_executor(
        db=db, broker=broker, clock=lambda: clock[0], oco_check_delay_seconds=60
    )

    executor.monitor_positions()
    assert broker.cancelled_stops == []

    clock[0] = 61.0
    executor._stop_ids_cache = None
    with caplog.at_level("INFO", logger=module.__name__):
        assert executor._cancel_pending_stops() == 1

    assert broker.cancelled_stops == ["stop-old"]
    assert "trailing_amend_cancel_retry position_id=41" in caplog.text


def test_stop_prices_apply_trailing_protective_ticks():
    """Issue #175: the protective limit sits trailing_protective_ticks below."""
    executor = make_executor(
        db=FakeDB(), broker=StopFakeBroker(), trailing_protective_ticks=10
    )

    assert executor._stop_prices("SBER", 95.007) == (95.0, 94.9)


def test_broker_stop_disabled_skips_stop_arming():
    """Issue #175: broker_stop_enabled=false keeps the synthetic fallback only."""
    db = FakeDB(active=active_position())
    broker = StopFakeBroker(positions=[sber_position("100")])
    executor = make_executor(db=db, broker=broker, broker_stop_enabled=False)

    executor.monitor_positions()

    assert not any(call[0] == "post_stop_order" for call in broker.calls)
    assert not any("SET broker_stop_id=NULL" in q for q, _ in db.execute_calls)


def trailing_position(**overrides):
    """Open SBER position with an armed broker stop and a trailing ladder."""
    return active_position(
        broker_stop_id="stop-old",
        take_price=115.0,
        trailing_enabled=True,
        trailing_steps=json.dumps([{"trigger": 2.0, "stop": 1.0}]),
        risk_r=5.0,
        current_stop_price=95.0,
        step_reached=0,
        **overrides,
    )


def test_vanished_position_is_closed_with_real_broker_fill(caplog):
    """Issue #175: exits are reconciled with real fills, not model prices."""
    db = FakeDB(active=active_position(broker_stop_id="stop-1"))
    broker = StopFakeBroker(
        positions=[],
        stop_orders=[stop_item("stop-1", status="STOP_ORDER_STATUS_EXECUTED")],
        operations=[sell_operation(price="94.5", quantity=100)],
    )
    executor = make_executor(db=db, broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        changes = executor.monitor_positions()

    assert changes == 1
    close = next(
        params
        for query, params in db.execute_calls
        if "SET status=%s, exit_ts=%s" in query
    )
    assert close[0] == "closed_stop"
    assert close[2] == pytest.approx(94.5)  # exit_price = the real fill
    assert close[3] == "stop"
    assert close[4] == pytest.approx(-550.0)  # (94.5 - 100) * 10 lots * 10 shares
    assert close[5] == pytest.approx(95.0)  # exit_price_model
    assert close[6] == pytest.approx(94.5)  # exit_price_actual
    assert close[7] == pytest.approx(-52.6316, abs=1e-4)  # slippage_bp
    assert close[8] is None  # slippage_r needs risk_r on the row
    assert close[9] == 10  # 100 shares / lot_size 10
    assert ("cancel_order", {"order_id": "take-1"}) in broker.calls
    assert "fill_reconciled ticker=SBER position_id=41" in caplog.text
    metrics = executor.get_metrics()
    assert metrics["fills_reconciled_total"] == 1
    # Both legs of the closed position are queued for OCO monitoring.
    assert metrics["oco_checks_pending"] == 2


def test_vanished_position_with_live_stop_is_closed_as_take():
    """Issue #175: an ACTIVE stop proves the take-profit leg fired."""
    db = FakeDB(active=active_position(broker_stop_id="stop-1"))
    broker = StopFakeBroker(
        positions=[],
        stop_orders=[stop_item("stop-1")],
        operations=[sell_operation(price="110.0", quantity=100)],
    )
    executor = make_executor(db=db, broker=broker)

    changes = executor.monitor_positions()

    assert changes == 1
    close = next(
        params
        for query, params in db.execute_calls
        if "SET status=%s, exit_ts=%s" in query
    )
    assert close[0] == "closed_take"
    assert close[3] == "take"
    assert close[5] == pytest.approx(110.0)
    assert close[6] == pytest.approx(110.0)
    # The surviving leg is the stop, cancelled through the stop API.
    assert broker.cancelled_stops == ["stop-1"]


def test_oco_monitoring_cancels_orphaned_stop_after_grace_period(caplog):
    """Issue #175: a leg that survived the close is cancelled with an alert."""
    clock = [0.0]
    db = FakeDB(active=active_position(broker_stop_id="stop-1"))
    broker = StopFakeBroker(stop_orders=[stop_item("stop-1")])
    executor = make_executor(
        db=db, broker=broker, clock=lambda: clock[0], oco_check_delay_seconds=60
    )
    row = db.active.iloc[0]

    executor._close_db_position(row, "take", 110.0)
    assert executor.get_metrics()["oco_checks_pending"] == 2

    # Inside the grace period nothing is touched.
    assert executor._process_oco_checks() == 0
    assert broker.cancelled_stops == []

    clock[0] = 61.0
    with caplog.at_level("INFO", logger=module.__name__):
        changes = executor._process_oco_checks()

    assert changes == 1
    assert broker.cancelled_stops == ["stop-1"]
    assert (
        "OCO monitoring: position_id=41 orphaned_stop_id=stop-1 cancelled=True"
        in caplog.text
    )
    assert "no orphaned take order" in caplog.text
    assert executor.get_metrics()["oco_orphans_cancelled_total"] == 1
    assert executor._oco_checks == []


def test_entry_priority_leaves_tokens_reserved_for_protection():
    """Issue #175: protection > trailing > entry when the bucket runs dry."""
    bucket = TokenBucket(rate_per_second=2.0, clock=lambda: 0.0)

    assert bucket.try_acquire(reserve=1.0) is True
    assert bucket.try_acquire(reserve=1.0) is False
    # Protection calls ignore the reserve and still get the last token.
    assert bucket.try_acquire() is True

    executor = make_executor(db=FakeDB(), broker=FakeBroker(), entry_token_reserve=1.0)
    executor.rate_limiter.tokens = 1.0
    executor.rate_limiter.updated_at = executor.rate_limiter.clock()

    assert (
        executor._broker_call("check_balance", blocking=False, priority="entry")
        is None
    )
    assert (
        executor._broker_call("check_balance", blocking=False, priority="protection")
        == Decimal("50000")
    )


def test_shutdown_cancels_broker_stop_through_the_stop_api():
    """Issue #175: flattening on shutdown cancels the stop as a stop order."""
    db = FakeDB(
        active=active_position(broker_stop_id="stop-1", broker_take_id="take-1")
    )
    broker = StopFakeBroker()
    executor = make_executor(db=db, broker=broker, close_positions_on_shutdown=True)

    executor.shutdown()

    assert broker.cancelled_stops == ["stop-1"]
    assert ("cancel_order", {"order_id": "take-1"}) in broker.calls
    assert any(
        call[0] == "execute_order" and call[1]["direction"] == "sell"
        for call in broker.calls
    )


def test_handle_sell_signal_cancels_broker_stop():
    """Issue #175: an external SELL signal removes the broker stop first."""
    db = FakeDB(active=active_position(broker_stop_id="stop-1"))
    broker = StopFakeBroker()
    executor = make_executor(db=db, broker=broker)

    closed = executor.handle_sell_signal("SBER")

    assert closed == 1
    assert broker.cancelled_stops == ["stop-1"]
    assert ("cancel_order", {"order_id": "take-1"}) in broker.calls



# --- Issue #199: account-wide sweep of orphaned broker stop orders -------------


def orphan_stop(stop_order_id, **overrides):
    """An ACTIVE SELL stop of a universe instrument, as the broker reports it.

    ``stop_item`` deliberately carries no ``direction``: the sweep must treat a
    stop whose direction the broker did not report as unknown, and unknown means
    "keep". Everything that *should* be sweepable is built through this helper.
    """
    data = {
        "direction": "INVESTMENT_DIRECTION_SELL",
        "ticker": "SBER",
        "instrument_uid": "figi-sber",
        "lots_requested": 10,
    }
    data.update(overrides)
    return stop_item(stop_order_id, **data)


class SweepFakeBroker(StopFakeBroker):
    """Stop broker that reports every stop it holds, whatever status is asked.

    ``StopFakeBroker`` filters to ACTIVE, which would hide the "not active"
    branch of the sweep behind the fake instead of testing it.
    """

    def get_stop_orders(self, status="active", **kwargs):
        self.calls.append(("get_stop_orders", {"status": status, **kwargs}))
        return list(self.stop_orders)


class RecordingNotifier:
    """Notifier double that keeps every rendered alert text."""

    enabled = True

    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)
        return True


SWEEP_SETTINGS = {
    "orphan_stop_sweep_interval_seconds": 60,
    "orphan_stop_confirmations": 2,
    "orphan_stop_grace_seconds": 0,
    "orphan_stop_max_cancels": 3,
    "orphan_stop_alert_interval_seconds": 3600,
}


def make_sweep_executor(broker=None, *, clock=None, **config):
    """Executor wired for orphan-sweep tests: short interval, empty DB book."""
    settings = dict(SWEEP_SETTINGS)
    settings.update(config)
    return make_executor(
        db=FakeDB(),
        broker=broker if broker is not None else SweepFakeBroker(),
        clock=lambda: (clock if clock is not None else [0.0])[0],
        **settings,
    )


def test_orphan_sweep_needs_two_confirmations_before_cancelling(caplog):
    """Issue #199: one sighting is a candidate, two consecutive ones an orphan."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock)

    with caplog.at_level("INFO", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor._orphan_candidates == {"stop-9": 1}
    assert "orphan_stop_candidate stop_order_id=stop-9" in caplog.text

    clock[0] = 61.0
    caplog.clear()
    with caplog.at_level("INFO", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 1

    assert broker.cancelled_stops == ["stop-9"]
    assert executor._orphan_candidates == {}
    assert "orphan_stop_cancelled stop_order_id=stop-9 ticker=SBER" in caplog.text
    metrics = executor.get_metrics()
    assert metrics["orphan_stop_sweep_runs_total"] == 2
    assert metrics["orphan_stops_cancelled_total"] == 1
    assert metrics["orphan_stop_candidates"] == 0
    assert metrics["orphan_sweep_fail_closed_total"] == 0


def test_orphan_sweep_loses_a_candidate_that_disappears_between_passes():
    """Issue #199: confirmations must be consecutive, never accumulated."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock)

    assert executor._sweep_orphan_stops([], []) == 0
    assert executor._orphan_candidates == {"stop-9": 1}

    # The stop is gone (or protected again) for one sweep...
    broker.stop_orders = []
    clock[0] = 61.0
    assert executor._sweep_orphan_stops([], []) == 0
    assert executor._orphan_candidates == {}

    # ...and coming back starts the confirmation count over.
    broker.stop_orders = [orphan_stop("stop-9")]
    clock[0] = 122.0
    assert executor._sweep_orphan_stops([], []) == 0
    assert executor._orphan_candidates == {"stop-9": 1}
    assert broker.cancelled_stops == []


def test_orphan_sweep_waits_for_the_configured_interval():
    """Issue #199: the sweep reads the broker at most once per interval."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock)

    assert executor._sweep_orphan_stops([], []) == 0
    calls_after_first = len(broker.calls)

    clock[0] = 59.0
    assert executor._sweep_orphan_stops([], []) == 0

    assert len(broker.calls) == calls_after_first
    assert executor._orphan_candidates == {"stop-9": 1}
    assert executor.orphan_sweep_runs_total == 1


def test_orphan_sweep_can_be_switched_off():
    """Issue #199: the operator can disable the net without touching the code."""
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, orphan_stop_sweep_enabled=False)

    assert executor._sweep_orphan_stops([], []) == 0
    assert broker.calls == []
    assert executor.get_metrics()["orphan_stop_sweep_enabled"] is False


def test_orphan_sweep_settings_fall_back_to_the_shipped_defaults():
    """Issue #199: a broken override degrades to the policy, never to zero."""
    executor = make_sweep_executor()
    # ``_validate_config`` rejects the garbage below at startup, so it is put
    # there behind its back: the coercion still has to land on the shipped
    # policy for a missing key, a text value, a NaN or an unexpected type.
    for key in (
        "orphan_stop_sweep_enabled",
        "orphan_stop_sweep_interval_seconds",
        "orphan_stop_max_cancels",
    ):
        executor.config.pop(key, None)
    executor.config.update(
        {
            "orphan_stop_confirmations": "two",
            "orphan_stop_grace_seconds": float("nan"),
            "orphan_stop_alert_interval_seconds": "soon",
        }
    )

    settings = executor._orphan_sweep_settings()

    assert settings["interval"] == LIVE_TRADING["orphan_stop_sweep_interval_seconds"]
    assert settings["confirmations"] == LIVE_TRADING["orphan_stop_confirmations"]
    assert settings["grace"] == LIVE_TRADING["orphan_stop_grace_seconds"]
    assert settings["max_cancels"] == LIVE_TRADING["orphan_stop_max_cancels"]
    assert settings["alert_interval"] == LIVE_TRADING[
        "orphan_stop_alert_interval_seconds"
    ]
    assert settings["enabled"] is True


def test_orphan_sweep_settings_clamp_impossible_values():
    """Issue #199: zero confirmations would cancel on first sight."""
    executor = make_sweep_executor()
    executor.config.update(
        {
            "orphan_stop_confirmations": 0,
            "orphan_stop_grace_seconds": -5,
            "orphan_stop_max_cancels": -1,
            "orphan_stop_sweep_interval_seconds": -1,
            "orphan_stop_alert_interval_seconds": -1,
        }
    )

    settings = executor._orphan_sweep_settings()

    assert settings["confirmations"] == 1
    assert settings["grace"] == 0.0
    assert settings["max_cancels"] == 0
    assert settings["interval"] == 0.0
    assert settings["alert_interval"] == 0.0


@pytest.mark.parametrize(
    "key, bad_value, fragment",
    [
        ("orphan_stop_sweep_enabled", "yes", "must be a boolean"),
        ("orphan_stop_sweep_interval_seconds", -1, "cannot be negative"),
        ("orphan_stop_grace_seconds", float("nan"), "cannot be negative"),
        ("orphan_stop_alert_interval_seconds", -30, "cannot be negative"),
        ("orphan_stop_confirmations", 0, "must be at least 1"),
        ("orphan_stop_max_cancels", -1, "cannot be negative"),
    ],
)
def test_orphan_sweep_config_rejects_bad_values(key, bad_value, fragment):
    """Issue #199: a malformed knob fails at startup, not during an incident."""
    with pytest.raises(ValueError) as excinfo:
        make_sweep_executor(**{key: bad_value})

    assert key in str(excinfo.value)
    assert fragment in str(excinfo.value)



@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"status": "STOP_ORDER_STATUS_CANCELLED"}, "not_active"),
        ({"status": "STOP_ORDER_STATUS_EXECUTED"}, "not_active"),
        ({"direction": "INVESTMENT_DIRECTION_BUY"}, "not_a_sell_stop"),
        ({"direction": None}, "not_a_sell_stop"),
        ({"ticker": "LKOH", "instrument_uid": "figi-lkoh"}, "outside_universe"),
        ({"ticker": None, "instrument_uid": None}, "outside_universe"),
    ],
)
def test_orphan_sweep_leaves_a_stop_it_cannot_prove_is_an_orphan(
    overrides, reason, caplog
):
    """Issue #199: only an ACTIVE SELL stop of a universe instrument is eligible."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9", **overrides)])
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)

    with caplog.at_level("DEBUG", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0
    clock[0] = 61.0
    with caplog.at_level("DEBUG", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor._orphan_candidates == {}
    assert executor.orphan_stops_cancelled_total == 0
    assert f"orphan_stop_kept stop_order_id=stop-9 reason={reason}" in caplog.text


def test_orphan_sweep_skips_a_pass_without_a_configured_universe(caplog):
    """Issue #199: with no universe every stop would look orphaned, so skip."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)
    executor.tickers = []
    executor.instruments = {}

    with caplog.at_level("WARNING", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0
    clock[0] = 61.0
    with caplog.at_level("WARNING", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor._orphan_candidates == {}
    assert "orphan_stop_sweep_skipped reason=empty_universe" in caplog.text


@pytest.mark.parametrize(
    "row, reason",
    [
        ({"broker_stop_id": "stop-9"}, "owned_by_oco_or_amend"),
        ({"broker_stop_id": None}, "position_row_exists"),
    ],
)
def test_orphan_sweep_leaves_a_stop_a_live_position_row_points_at(row, reason, caplog):
    """Issue #199: a DB row owning the id or the instrument means "keep"."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)
    rows = [dict(id=41, ticker="SBER", instrument_id="figi-sber", status="open", **row)]

    with caplog.at_level("DEBUG", logger=module.__name__):
        assert executor._sweep_orphan_stops(rows, []) == 0
    clock[0] = 61.0
    with caplog.at_level("DEBUG", logger=module.__name__):
        assert executor._sweep_orphan_stops(rows, []) == 0

    assert broker.cancelled_stops == []
    assert executor._orphan_candidates == {}
    assert f"orphan_stop_kept stop_order_id=stop-9 reason={reason}" in caplog.text



def test_orphan_sweep_leaves_stops_claimed_by_the_oco_or_amend_pass():
    """Issue #199: ids another pass is already reconciling must not be touched."""
    clock = [0.0]
    broker = SweepFakeBroker(
        stop_orders=[orphan_stop("stop-9"), orphan_stop("stop-8")]
    )
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)
    executor._oco_checks.append(
        {
            "position_id": 41,
            "ticker": "SBER",
            "kind": "stop",
            "order_id": "stop-9",
            "due_at": 999.0,
            "attempts": 0,
        }
    )
    executor._pending_stop_cancels[41] = ("stop-8", "stop-7", 0.0)

    assert executor._sweep_orphan_stops([], []) == 0
    clock[0] = 61.0
    assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor._orphan_candidates == {}


def test_orphan_sweep_keeps_a_stop_while_the_broker_holds_the_instrument(caplog):
    """Issue #199: a live holding behind a stop is never swept automatically."""
    clock = [0.0]
    broker = SweepFakeBroker(
        positions=[sber_position()], stop_orders=[orphan_stop("stop-9")]
    )
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)

    # broker_positions=None: the sweep must read the portfolio itself.
    with caplog.at_level("DEBUG", logger=module.__name__):
        assert executor._sweep_orphan_stops([], None) == 0

    assert broker.cancelled_stops == []
    assert ("get_positions", {}) in broker.calls
    assert (
        "orphan_stop_kept stop_order_id=stop-9 reason=broker_holding_exists"
        in caplog.text
    )

    # Once the holding is gone the very same stop becomes sweepable.
    broker.positions = []
    clock[0] = 61.0
    assert executor._sweep_orphan_stops([], None) == 1
    assert broker.cancelled_stops == ["stop-9"]


def test_orphan_sweep_respects_the_grace_window_of_a_freshly_armed_stop(caplog):
    """Issue #199: a stop this process armed stays untouchable for the grace."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(
        broker,
        clock=clock,
        orphan_stop_grace_seconds=900,
        orphan_stop_confirmations=1,
    )
    executor._recent_stop_ids["stop-9"] = 0.0

    with caplog.at_level("DEBUG", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor._orphan_candidates == {}
    assert (
        "orphan_stop_kept stop_order_id=stop-9 reason=inside_grace_window"
        in caplog.text
    )

    clock[0] = 900.0
    assert executor._sweep_orphan_stops([], []) == 1
    assert broker.cancelled_stops == ["stop-9"]
    # The expired arming timestamp is pruned instead of growing forever.
    assert executor._recent_stop_ids == {}



def _sweep_alerting(executor):
    """Switch the alerting contour on for a sweep test and return the notifier."""
    notifier = RecordingNotifier()
    executor.notifier = notifier
    executor.alerting = {
        **executor.alerting,
        "telegram_alerts_enabled": True,
        # Isolate the sweep's own throttle from the global alert debounce.
        "alert_debounce_seconds": 0,
    }
    return notifier


def test_orphan_sweep_fails_closed_when_more_orphans_than_the_cap(caplog):
    """Issue #199: a stop book full of orphans means the account model is wrong.

    Emptying it automatically would be the most expensive possible guess, so the
    sweep reports and cancels nothing at all.
    """
    clock = [0.0]
    broker = SweepFakeBroker(
        stop_orders=[orphan_stop(f"stop-{i}") for i in range(1, 6)]
    )
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_max_cancels=3)
    notifier = _sweep_alerting(executor)

    # Pass 1 only records the candidates, pass 2 confirms all five at once.
    executor._sweep_orphan_stops([], [])
    clock[0] = 61.0
    with caplog.at_level("CRITICAL", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor.orphan_stops_cancelled_total == 0
    assert executor.orphan_sweep_fail_closed_total == 1
    assert "orphan_stop_sweep_fail_closed orphans=5 max_cancels=3" in caplog.text
    # Every candidate is kept: nothing was resolved, so nothing is forgotten.
    assert executor._orphan_candidates == {f"stop-{i}": 2 for i in range(1, 6)}
    assert len(notifier.messages) == 1
    assert "сирот больше лимита" in notifier.messages[0]

    # The condition repeats on every pass, so the reminder is throttled...
    clock[0] = 122.0
    assert executor._sweep_orphan_stops([], []) == 0
    assert executor.orphan_sweep_fail_closed_total == 2
    assert len(notifier.messages) == 1

    # ...and comes back once orphan_stop_alert_interval_seconds has passed.
    clock[0] = 3662.0
    assert executor._sweep_orphan_stops([], []) == 0
    assert executor.orphan_sweep_fail_closed_total == 3
    assert len(notifier.messages) == 2


def test_orphan_sweep_with_zero_cap_reports_instead_of_cancelling(caplog):
    """Issue #199: max_cancels=0 is the "watch only" mode of the safety net."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(
        broker, clock=clock, orphan_stop_max_cancels=0
    )
    notifier = _sweep_alerting(executor)

    executor._sweep_orphan_stops([], [])
    clock[0] = 61.0
    with caplog.at_level("CRITICAL", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor.orphan_stops_cancelled_total == 0
    assert executor.orphan_sweep_fail_closed_total == 1
    assert "orphans=1 max_cancels=0" in caplog.text
    assert len(notifier.messages) == 1



def test_orphan_sweep_keeps_the_candidate_when_the_broker_rejects_the_cancel(caplog):
    """Issue #199: an unproven cancel is retried, never counted as done."""
    clock = [0.0]
    broker = SweepFakeBroker(
        stop_orders=[orphan_stop("stop-9")],
        cancel_stop_error=SandboxAPIError("stop order is not cancellable"),
    )

    def _reject_cancel_order(order_id):
        # _safe_cancel_stop may fall back to cancel_order for pre-#175 ids; the
        # fallback must fail too, otherwise the sweep would claim a cancel it
        # never proved.
        broker.calls.append(("cancel_order", {"order_id": order_id}))
        raise SandboxAPIError("order is not cancellable")

    broker.cancel_order = _reject_cancel_order
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)

    with caplog.at_level("WARNING", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor.orphan_stops_cancelled_total == 0
    assert executor.orphan_sweep_fail_closed_total == 0
    # The stop is still there, so the candidate stays for the next pass.
    assert executor._orphan_candidates == {"stop-9": 1}
    assert "orphan_stop_cancel_failed stop_order_id=stop-9" in caplog.text

    # As soon as the broker accepts the cancel the same candidate goes through.
    broker.cancel_stop_error = None
    clock[0] = 122.0
    assert executor._sweep_orphan_stops([], []) == 1
    assert broker.cancelled_stops == ["stop-9"]
    assert executor._orphan_candidates == {}


def test_orphan_sweep_survives_a_stop_order_outage(caplog):
    """Issue #199: an unreadable stop book means "no evidence", not "no orphans"."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock)

    # Pass 1 records the candidate; the confirmation is still pending.
    executor._sweep_orphan_stops([], [])
    assert executor._orphan_candidates == {"stop-9": 1}

    def _outage(status="active", **kwargs):
        broker.calls.append(("get_stop_orders", {"status": status}))
        raise SandboxAPIError("get_stop_orders is unavailable")

    broker.get_stop_orders = _outage
    clock[0] = 61.0
    with caplog.at_level("WARNING", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    # The pass never ran, so this is not a fail-closed verdict either.
    assert executor.orphan_sweep_fail_closed_total == 0
    assert (
        "orphan_stop_sweep_skipped reason=get_stop_orders error_type=SandboxAPIError"
        in caplog.text
    )
    # The candidates of the previous pass survive the blind spot.
    assert executor._orphan_candidates == {"stop-9": 1}


def test_orphan_sweep_skips_when_the_portfolio_cannot_be_read(caplog):
    """Issue #199: "the broker holds nothing" must be proven, not assumed."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)

    def _outage():
        broker.calls.append(("get_positions", {}))
        raise SandboxAPIError("portfolio is unavailable")

    broker.get_positions = _outage
    with caplog.at_level("WARNING", logger=module.__name__):
        assert executor._sweep_orphan_stops([], None) == 0

    assert broker.cancelled_stops == []
    assert executor._orphan_candidates == {}
    assert (
        "orphan_stop_sweep_skipped reason=get_positions error_type=SandboxAPIError"
        in caplog.text
    )


def test_orphan_sweep_never_breaks_a_monitoring_cycle(caplog, monkeypatch):
    """Issue #199: the safety net must not become the thing that breaks a cycle."""
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("unexpected sweep failure")

    monkeypatch.setattr(executor, "_fetch_active_stop_orders", _boom)
    with caplog.at_level("ERROR", logger=module.__name__):
        assert executor._sweep_orphan_stops([], []) == 0

    assert broker.cancelled_stops == []
    assert executor.orphan_sweep_fail_closed_total == 1
    assert "orphan_stop_sweep_failed error_type=RuntimeError" in caplog.text



def test_orphan_sweep_alerts_once_per_cancel_batch():
    """Issue #199: removing broker stops on our own is always reported."""
    clock = [0.0]
    broker = SweepFakeBroker(
        stop_orders=[orphan_stop("stop-9"), orphan_stop("stop-8")]
    )
    executor = make_sweep_executor(broker, clock=clock, orphan_stop_confirmations=1)
    notifier = _sweep_alerting(executor)

    assert executor._sweep_orphan_stops([], []) == 2

    assert broker.cancelled_stops == ["stop-9", "stop-8"]
    assert executor.orphan_stops_cancelled_total == 2
    assert executor.orphan_sweep_fail_closed_total == 0
    # One message for the whole batch, naming every removed stop.
    assert len(notifier.messages) == 1
    assert "Сняты бесхозные стоп-ордера" in notifier.messages[0]
    assert "stop-9, stop-8" in notifier.messages[0]


def test_orphan_sweep_reads_its_knobs_on_every_pass():
    """Issue #199: a runtime change applies to the next pass, no restart needed."""
    clock = [0.0]
    broker = SweepFakeBroker(
        stop_orders=[orphan_stop("stop-9"), orphan_stop("stop-8")]
    )
    executor = make_sweep_executor(broker, clock=clock)

    executor._sweep_orphan_stops([], [])
    clock[0] = 61.0
    # The operator switches the net to "watch only" while the executor runs.
    executor.config["orphan_stop_max_cancels"] = 0
    assert executor._sweep_orphan_stops([], []) == 0
    assert broker.cancelled_stops == []
    assert executor.orphan_sweep_fail_closed_total == 1

    # Releasing the limit lets the very same stops go on the next pass.
    executor.config["orphan_stop_max_cancels"] = 3
    clock[0] = 122.0
    assert executor._sweep_orphan_stops([], []) == 2
    assert broker.cancelled_stops == ["stop-9", "stop-8"]


def test_orphan_sweep_runs_when_the_position_book_is_empty(caplog):
    """Issue #199 (RC3): the empty-book early return must still run the sweep.

    That is exactly the moment the safety net matters most: every stop left in
    the account belongs to nobody.
    """
    clock = [0.0]
    broker = SweepFakeBroker(stop_orders=[orphan_stop("stop-9")])
    executor = make_sweep_executor(broker, clock=clock)

    with caplog.at_level("INFO", logger=module.__name__):
        assert executor.monitor_positions() == 0
    assert executor.orphan_sweep_runs_total == 1
    assert broker.cancelled_stops == []
    assert "orphan_stop_candidate stop_order_id=stop-9" in caplog.text

    clock[0] = 61.0
    assert executor.monitor_positions() == 1
    assert broker.cancelled_stops == ["stop-9"]
    assert executor.orphan_sweep_runs_total == 2
    assert executor.orphan_stops_cancelled_total == 1


def test_shutdown_drops_superseded_stops_when_positions_stay_open(caplog):
    """Issue #199 (RC2): shutdown is the last chance to drop an amend leftover."""
    clock = [0.0]
    broker = StopFakeBroker(stop_orders=[stop_item("stop-old")])
    executor = make_executor(
        broker=broker,
        clock=lambda: clock[0],
        close_positions_on_shutdown=False,
        oco_check_delay_seconds=3600,
    )
    # The replacement stop is not visible at the broker yet and the OCO deadline
    # has not expired either - only force=True may cancel here.
    executor._pending_stop_cancels[41] = ("stop-old", "stop-new", 0.0)

    with caplog.at_level("INFO", logger=module.__name__):
        executor.shutdown()

    assert broker.cancelled_stops == ["stop-old"]
    assert executor._pending_stop_cancels == {}
    assert (
        "trailing_amend_cancelled position_id=41 old_stop_id=stop-old "
        "new_stop_id=stop-new" in caplog.text
    )


# --- Issue #194 (Epic #190, block G3): the canary contour ---------------------
#
# A canary run is an ordinary live run wearing three extra belts: a one-ticker
# universe, a one-lot ceiling applied AFTER the sizer, and two operator pauses -
# one before the order reaches the broker and one after the fill is protected.
# The tests below walk the contour the way an operator does, and several of them
# also state what an ordinary (non-canary) run does instead, so a future
# refactor cannot silently drag the whole live contour into the canary rules.


CANARY_SIGNAL = {"action": "enter", "entry_price": 100, "stop": 95, "take": 110}


@pytest.fixture(autouse=True)
def _clean_canary_env(monkeypatch):
    """Keep the deployment's CANARY_* env out of this whole module.

    ``LiveExecutor`` reads :func:`get_canary_config` when a test does not inject
    a policy, so a stray ``CANARY_ENABLED=true`` in the developer's shell would
    turn every ordinary executor test into a canary test. The canary tests pass
    their policy explicitly and do not need the environment at all.
    """
    for name in (
        "CANARY_ENABLED",
        "CANARY_TICKER",
        "CANARY_MAX_LOTS",
        "CANARY_MAX_OPEN_POSITIONS",
        "CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW",
    ):
        monkeypatch.delenv(name, raising=False)


def canary_executor(*, answers=("n",), canary=None, **kwargs):
    """LiveExecutor inside the canary contour answering its pauses from a script.

    ``answers`` is the operator's script, consumed one word per pause; the LAST
    word repeats, so ``answers=["retry"]`` models an operator who keeps asking
    for a fresh book and ``answers=["y", "n"]`` one who approves the entry and
    then refuses the position. Every prompt is recorded on
    ``executor.canary_prompts``, so a test can assert what the operator was
    shown and not only that the loop stopped to ask.
    """
    prompts = []
    script = list(answers)

    def confirm_fn(prompt):
        prompts.append(prompt)
        if not script:
            return "n"
        return script[min(len(prompts), len(script)) - 1]

    executor = make_executor(
        canary=dict({"enabled": True}, **(canary or {})),
        confirm_fn=confirm_fn,
        **kwargs,
    )
    executor.canary_prompts = prompts
    return executor


class FlakyStopBroker(StopFakeBroker):
    """Fails its first ``fail_first`` stop posts, then arms normally.

    The outage the second pause's ``retry`` answer exists for: the fill landed,
    the broker STOP_LOSS did not, and the operator wants it re-posted instead of
    watching an unprotected position.
    """

    def __init__(self, *args, fail_first=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_first = fail_first
        self.stop_attempts = 0

    def post_stop_order(self, **kwargs):
        self.stop_attempts += 1
        if self.stop_attempts <= self.fail_first:
            self.calls.append(("post_stop_order", kwargs))
            raise SandboxAPIError("stop outage")
        return super().post_stop_order(**kwargs)


def test_the_canary_is_off_by_default():
    """No CANARY_ENABLED - no cap, no pauses, no narrowed universe."""
    executor = make_executor()

    assert executor.canary_enabled is False
    # the policy defaults are present but inert while the switch is off
    assert executor.canary["ticker"] == "SBER"
    assert executor.canary["max_lots"] == 1
    # the ordinary contour asks nobody for anything
    assert executor.confirm_fn is module._stdin_confirm

    metrics = executor.get_metrics()
    assert metrics["canary_enabled"] is False
    # None, not 0/False: "not a canary" must stay distinct from "a canary that
    # never traded" in the metrics snapshot the dashboard reads.
    assert metrics["canary_ticker"] is None
    assert metrics["canary_max_lots"] is None


def test_an_ordinary_run_keeps_the_sizer_answer():
    """Without the canary the sizer's 10 lots reach the broker untouched."""
    broker = FakeBroker()
    executor = make_executor(broker=broker)

    result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result == {
        "executed": True,
        "reason": "open",
        "position_id": 41,
        "size_lots": 10,
    }
    entry = next(c for c in broker.calls if c[0] == "execute_order")
    assert entry[1]["quantity"] == 10
    assert executor.canary_confirmations_total == 0
    assert executor.canary_capped_total == 0


def test_an_ordinary_run_publishes_no_canary_lines():
    """The Issue #177 alert body stays byte-identical outside the canary."""
    assert make_executor()._canary_lines(sizing_reason="risk") == []


def test_an_ordinary_run_keeps_its_whole_universe():
    executor = make_executor()

    assert executor._apply_canary_universe(
        ["GAZP", "SBER"], ["GAZP", "SBER", "LKOH"]
    ) == ["GAZP", "SBER"]


def test_the_canary_caps_the_sizer_to_one_lot(caplog):
    """The sizer wanted 10 lots; the canary ceiling sends 1 to the broker."""
    broker = StopFakeBroker()
    executor = canary_executor(answers=["y", "y"], broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result == {
        "executed": True,
        "reason": "open",
        "position_id": 41,
        "size_lots": 1,
    }
    entry = next(c for c in broker.calls if c[0] == "execute_order")
    assert entry[1]["quantity"] == 1
    assert executor.canary_capped_total == 1
    assert (
        "Live canary cap: ticker=SBER size_lots=10 -> 1 reason=canary_cap"
        in caplog.text
    )


def test_the_canary_cap_only_ever_shrinks(caplog):
    """A sizer answer of 1 lot is already inside the ceiling - no cap claimed."""
    broker = FakeBroker(balance=Decimal("5000"))
    executor = canary_executor(answers=["y", "y"], broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result["size_lots"] == 1
    assert executor.canary_capped_total == 0
    assert "Live canary cap" not in caplog.text


@pytest.mark.parametrize(
    "canary,risk_limit,expected",
    [
        (None, 5, 1),                       # the canary default is ONE position
        (None, 1, 1),
        ({"max_open_positions": 3}, 5, 3),  # an explicit canary limit is kept...
        ({"max_open_positions": 3}, 2, 2),  # ...but never widens the risk limit
    ],
)
def test_the_canary_takes_the_tighter_max_open_positions(canary, risk_limit, expected):
    """``min(canary, risk)``: an env MAX_OPEN_POSITIONS=5 must not widen the
    canary, and a canary must not widen the ordinary contour either."""
    executor = canary_executor(canary=canary or {}, max_open_positions=risk_limit)

    assert executor.config["max_open_positions"] == expected


def test_the_canary_universe_is_narrowed_to_its_single_ticker(caplog):
    executor = canary_executor()

    with caplog.at_level("INFO", logger=module.__name__):
        narrowed = executor._apply_canary_universe(
            ["GAZP", "SBER", "LKOH"], ["GAZP", "SBER", "LKOH"]
        )

    assert narrowed == ["SBER"]
    assert "Canary universe narrowed: GAZP,SBER,LKOH -> SBER" in caplog.text


@pytest.mark.parametrize(
    "tickers,live_universe",
    [
        (["GAZP", "LKOH"], ["GAZP", "SBER"]),  # the canary ticker is not traded
        (["SBER", "GAZP"], ["GAZP"]),          # the canary ticker is not live
        ([], ["SBER"]),                        # nothing is traded at all
    ],
)
def test_the_canary_universe_fails_closed(tickers, live_universe, caplog):
    """A canary whose ticker cannot trade this session trades nothing at all."""
    executor = canary_executor()

    with caplog.at_level("INFO", logger=module.__name__):
        assert executor._apply_canary_universe(tickers, live_universe) == []

    assert "Canary universe is EMPTY: ticker=SBER" in caplog.text


def test_the_canary_refuses_another_ticker_even_on_a_direct_call(caplog):
    """Belt and braces: a stray GAZP signal never reaches the broker."""
    broker = FakeBroker()
    executor = canary_executor(answers=["y", "y"], broker=broker)
    executor.instruments["GAZP"] = {
        "instrument_id": "figi-gazp",
        "lot_size": 10,
        "min_price_increment": 0.01,
    }

    with caplog.at_level("WARNING", logger=module.__name__):
        result = executor.process_signal("GAZP", CANARY_SIGNAL, imbalance=1.5)

    assert result == {"executed": False, "reason": "canary_universe"}
    assert broker.calls == []
    assert executor.canary_rejections_total == 1
    assert "reason=canary_universe canary_ticker=SBER" in caplog.text


@pytest.mark.parametrize("answer", ["y", "Y", "YES", " yes ", "да", "д"])
def test_the_yes_family_is_the_only_approval(answer):
    executor = canary_executor(answers=[answer])

    assert executor._canary_ask("?") == module.CANARY_CONFIRM_YES
    assert executor.canary_confirmations_total == 1
    assert executor.canary_rejections_total == 0


@pytest.mark.parametrize("answer", ["retry", "R", "повтор", "Повтори"])
def test_the_retry_family_is_counted(answer):
    executor = canary_executor(answers=[answer])

    assert executor._canary_ask("?") == module.CANARY_CONFIRM_RETRY
    assert executor.canary_confirm_retries_total == 1


@pytest.mark.parametrize(
    "answer", ["n", "", "   ", "maybe", "Y ES", "yesterday", "нет"]
)
def test_any_other_answer_is_a_refusal(answer):
    """A typo, an empty line or a half-typed 'yes' must never move money."""
    executor = canary_executor(answers=[answer])

    assert executor._canary_ask("?") == module.CANARY_CONFIRM_NO
    assert executor.canary_confirm_retries_total == 0


def test_a_broken_prompt_is_a_refusal_not_an_approval(caplog):
    """A confirm_fn that raises (no tty in a container) still means 'no'."""

    def boom(_prompt):
        raise RuntimeError("stdin is gone")

    executor = make_executor(canary={"enabled": True}, confirm_fn=boom)

    with caplog.at_level("ERROR", logger=module.__name__):
        assert executor._canary_ask("?") == module.CANARY_CONFIRM_NO

    assert "Canary confirmation raised (RuntimeError" in caplog.text


def test_a_closed_stdin_answers_empty_and_empty_means_no(monkeypatch):
    """A detached start reads EOF: the canary stops instead of trading."""

    def raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    assert module._stdin_confirm("[CANARY sandbox] Подтвердите: ") == ""
    executor = make_executor(canary={"enabled": True})
    assert executor._canary_ask("?") == module.CANARY_CONFIRM_NO


@pytest.mark.parametrize(
    "value,expected",
    [
        (100.0, "100.0000"),
        (Decimal("94.95"), "94.9500"),
        (None, "?"),
        (float("nan"), "?"),
        (float("inf"), "?"),
        ("abc", "?"),
    ],
)
def test_the_prompt_renders_an_unknown_price_as_a_question_mark(value, expected):
    """The prompt is the last thing read before real money moves."""
    assert module._canary_price_text(value) == expected


@pytest.mark.parametrize("answer", ["n", "", "maybe", "нет"])
def test_the_first_pause_treats_anything_but_yes_as_a_refusal(answer):
    """Confirmation 1 sits BEFORE the broker: a refusal costs only the signal."""
    broker = StopFakeBroker()
    executor = canary_executor(answers=[answer], broker=broker)

    result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result == {"executed": False, "reason": "canary_not_confirmed"}
    # the balance read of the sizing step is the ONLY broker contact: no entry
    # order, no stop, no take - nothing a human has to unwind afterwards.
    assert [c[0] for c in broker.calls] == ["check_balance"]
    assert len(executor.canary_prompts) == 1
    assert executor.canary_confirmations_total == 1
    assert executor.canary_rejections_total == 1
    # a refused entry is not an abort - the stream keeps running.
    assert executor.canary_aborts_total == 0
    assert executor.shutdown_requested.is_set() is False


def test_the_canary_enters_one_lot_after_two_yes_answers():
    """The whole happy path of the contour, in the order an operator sees it."""
    broker = StopFakeBroker()
    executor = canary_executor(answers=["y", "y"], broker=broker)

    result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result == {
        "executed": True,
        "reason": "open",
        "position_id": 41,
        "size_lots": 1,
    }
    assert [c[0] for c in broker.calls] == [
        "check_balance",
        "execute_order",
        "post_stop_order",
        "execute_order",
    ]
    assert broker.calls[1][1]["quantity"] == 1
    # the broker stop covers exactly the capped lot, not the sizer's 10
    assert broker.calls[2][1]["quantity"] == 1
    assert len(executor.canary_prompts) == 2
    assert "Готов к покупке 1 лот(ов) SBER" in executor.canary_prompts[0]
    assert "Сделка open" in executor.canary_prompts[1]
    assert executor.canary_confirmations_total == 2
    assert executor.canary_aborts_total == 0
    assert executor.shutdown_requested.is_set() is False


def test_the_first_pause_retry_rereads_the_orderbook(monkeypatch):
    """``retry`` is not a delay - it fetches a fresh book and shows it."""
    reads = []

    def fake_orderbook(self, ticker):
        reads.append(ticker)
        return (0.25, 3.0)

    monkeypatch.setattr(module.LiveExecutor, "_latest_orderbook", fake_orderbook)
    executor = canary_executor(answers=["retry", "y"])

    confirmed = executor._canary_confirm_entry(
        ticker="SBER", lots=1, entry_price=100.0, stop_price=95.0, take_price=110.0
    )

    assert confirmed is True
    assert reads == ["SBER"]
    assert executor.canary_confirmations_total == 2
    assert executor.canary_confirm_retries_total == 1
    assert executor.canary_rejections_total == 0
    # the first prompt had no book yet, the second shows the fresh imbalance
    assert "дисбаланс стакана=-." in executor.canary_prompts[0]
    assert "дисбаланс стакана=+0.250." in executor.canary_prompts[1]


def test_the_first_pause_gives_up_after_the_retry_limit(caplog):
    """A loop of ``retry`` ends in a refusal, never in an order."""
    executor = canary_executor(answers=["retry"])

    with caplog.at_level("ERROR", logger=module.__name__):
        confirmed = executor._canary_confirm_entry(ticker="SBER", lots=1)

    assert confirmed is False
    # 3 retries are allowed, so the operator is asked MAX+1 times
    assert len(executor.canary_prompts) == module.CANARY_CONFIRM_MAX_RETRIES + 1
    assert (
        executor.canary_confirm_retries_total
        == module.CANARY_CONFIRM_MAX_RETRIES + 1
    )
    assert executor.canary_rejections_total == 1
    assert "Canary entry SBER dropped: 3 retries" in caplog.text


class NoTakeBroker(StopFakeBroker):
    """Arms stops normally but never lets the take-profit leg through."""

    def execute_order(self, **kwargs):
        if kwargs.get("order_type") == "limit":
            self.calls.append(("execute_order", kwargs))
            raise SandboxAPIError("take outage")
        return super().execute_order(**kwargs)


def test_the_second_pause_retry_arms_the_protection_the_fill_missed(caplog):
    """``retry`` on pause 2 re-posts the missing leg and asks again."""
    broker = FlakyStopBroker()
    executor = canary_executor(answers=["y", "retry", "y"], broker=broker)

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result == {
        "executed": True,
        "reason": "open",
        "position_id": 41,
        "size_lots": 1,
    }
    assert broker.stop_attempts == 2
    assert broker.posted_stops == ["stop-1"]
    assert len(executor.canary_prompts) == 3
    assert "стоп=95.0000 (НЕ у брокера)" in executor.canary_prompts[1]
    assert "стоп=95.0000 (у брокера)" in executor.canary_prompts[2]
    assert executor.canary_confirm_retries_total == 1
    assert executor.canary_aborts_total == 0
    assert (
        "Canary retry: stop re-arm position_id=41 ticker=SBER -> armed"
        in caplog.text
    )


def test_a_missing_take_still_reaches_the_second_pause():
    """The ordinary contour returns ``protection_pending`` silently; the canary
    shows the gap to the operator and only then reports it."""
    broker = NoTakeBroker()
    executor = canary_executor(answers=["y", "y"], broker=broker)

    result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result == {
        "executed": True,
        "reason": "protection_pending",
        "position_id": 41,
        "size_lots": 1,
    }
    assert len(executor.canary_prompts) == 2
    assert "тейк=110.0000 (НЕ у брокера)" in executor.canary_prompts[1]
    assert executor.canary_aborts_total == 0
    assert executor.shutdown_requested.is_set() is False


def test_a_retry_reposts_only_the_missing_leg():
    broker = StopFakeBroker()
    executor = canary_executor(broker=broker)
    protection = {"stop_armed": False, "take_placed": True}

    executor._canary_rearm_protection(
        ticker="SBER",
        position_id=41,
        instrument_id="figi-sber",
        lots=1,
        stop_price=95.0,
        take_price=110.0,
        protection=protection,
    )

    # updated IN PLACE, so the caller still reports the true protection state
    assert protection == {"stop_armed": True, "take_placed": True}
    assert [c[0] for c in broker.calls] == ["post_stop_order"]
    assert broker.posted_stops == ["stop-1"]


def test_a_retry_never_duplicates_an_armed_stop():
    """A second active SELL stop on one position would over-sell it (#199)."""
    broker = StopFakeBroker()
    executor = canary_executor(broker=broker)
    protection = {"stop_armed": True, "take_placed": False}

    executor._canary_rearm_protection(
        ticker="SBER",
        position_id=41,
        instrument_id="figi-sber",
        lots=1,
        stop_price=95.0,
        take_price=110.0,
        protection=protection,
    )

    assert protection == {"stop_armed": True, "take_placed": True}
    assert [c[0] for c in broker.calls] == ["execute_order"]
    assert broker.calls[0][1]["order_type"] == "limit"
    assert broker.posted_stops == []


def test_a_refused_second_pause_stops_the_stream_without_flattening(caplog):
    """The one irreversible action the operator did NOT ask for stays undone.

    The money is already in the market, so refusing pause 2 stops the canary
    stream and hands the position to the manual runbook - it never market-sells
    the position and never cancels the broker protection behind it, even on a
    deployment that flattens on shutdown.
    """
    broker = StopFakeBroker()
    db = FakeDB()
    executor = canary_executor(
        answers=["y", "n"],
        broker=broker,
        db=db,
        close_positions_on_shutdown=True,
    )

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    # executed stays True - the order really happened - but the reason says so
    assert result == {
        "executed": True,
        "reason": "canary_aborted",
        "position_id": 41,
        "size_lots": 1,
    }
    assert executor.canary_aborts_total == 1
    assert executor.shutdown_requested.is_set() is True
    assert "CANARY ABORT: position_id=41 ticker=SBER lots=1" in caplog.text

    # the position is now visible to shutdown(), as it would be in production
    db.active = active_position(
        size_lots=1, broker_stop_id="stop-1", broker_take_id="order-2"
    )
    with caplog.at_level("WARNING", logger=module.__name__):
        executor.shutdown()

    market_sells = [
        c
        for c in broker.calls
        if c[0] == "execute_order"
        and c[1]["direction"] == "sell"
        and c[1]["order_type"] == "market"
    ]
    assert market_sells == []
    assert broker.cancelled_stops == []
    assert not any(
        "SET broker_stop_id=NULL" in query for query, _ in db.execute_calls
    )
    assert (
        "Canary abort: close_positions_on_shutdown forced OFF" in caplog.text
    )


def test_the_canary_counters_land_in_the_metrics_snapshot():
    """Everything an operator has to prove afterwards is published as canary_*."""
    broker = StopFakeBroker()
    executor = canary_executor(answers=["y", "y"], broker=broker)

    executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)
    metrics = executor.get_metrics()

    assert metrics["canary_enabled"] is True
    assert metrics["canary_ticker"] == "SBER"
    assert metrics["canary_max_lots"] == 1
    assert metrics["canary_capped_total"] == 1
    assert metrics["canary_confirmations_total"] == 2
    assert metrics["canary_confirm_retries_total"] == 0
    assert metrics["canary_rejections_total"] == 0
    assert metrics["canary_aborts_total"] == 0


def test_the_canary_alert_lines_name_the_cap_and_the_contour():
    executor = canary_executor()

    lines = dict(
        executor._canary_lines(
            sizing_reason="canary_cap", stop_armed=True, take_placed=False
        )
    )

    assert lines["Canary"] == "включён"
    assert lines["Canary-тикер"] == "SBER"
    assert lines["Canary-лимит"] == "1 лот."
    assert lines["Подтверждения"] == "2 паузы: перед ордером и после защиты"
    assert lines["Сайзер"] == "canary_cap"
    assert lines["Стоп у брокера"] == "выставлен"
    assert lines["Тейк у брокера"] == "НЕ выставлен"


# --- the canary policy in trading_config ---------------------------------------


def test_canary_defaults_match_the_shipped_policy():
    """OFF by default, and the smallest possible run: one name, one lot, one
    position. Widening it is a reviewed config change, not a code change."""
    config = get_canary_config()

    assert config == CANARY
    assert config["enabled"] is False
    assert config["ticker"] == "SBER"
    assert config["max_lots"] == 1
    assert config["max_open_positions"] == 1
    assert get_canary_bounds() == {
        "max_lots": (1, 100),
        "max_open_positions": (1, 100),
    }


def test_canary_config_returns_an_isolated_copy():
    first = get_canary_config()
    first["ticker"] = "GAZP"
    first["max_lots"] = 99

    assert get_canary_config() == CANARY
    assert CANARY["ticker"] == "SBER"
    assert CANARY["max_lots"] == 1


def test_canary_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("CANARY_ENABLED", "true")
    monkeypatch.setenv("CANARY_TICKER", " gazp ")
    monkeypatch.setenv("CANARY_MAX_LOTS", "2")
    monkeypatch.setenv("CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW", "true")

    config = get_canary_config()

    assert config["enabled"] is True
    assert config["ticker"] == "GAZP"
    assert config["max_lots"] == 2
    # no env knob on purpose: a canary of more than one position is not a canary
    assert config["max_open_positions"] == 1
    # PO decision of 2026-10-03: the weekend/off-exchange canary is opt-in
    assert config["allow_outside_entry_window"] is True
    assert set(CANARY_ENV) == {
        "CANARY_ENABLED",
        "CANARY_TICKER",
        "CANARY_MAX_LOTS",
        "CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW",
    }


def test_blank_canary_env_overrides_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("CANARY_ENABLED", "   ")
    monkeypatch.setenv("CANARY_TICKER", "")
    monkeypatch.setenv("CANARY_MAX_LOTS", "")

    assert get_canary_config() == CANARY


@pytest.mark.parametrize("word", ["ture", "maybe", "0x1", "yesplease"])
def test_canary_enabled_rejects_an_ambiguous_word(monkeypatch, word):
    """The switch that decides whether real money trades uses the strict reader
    of ALLOW_REAL_TRADING (#178): a typo must fail instead of quietly meaning
    "off"."""
    monkeypatch.setenv("CANARY_ENABLED", word)

    with pytest.raises(ValueError, match="CANARY_ENABLED"):
        get_canary_config()


@pytest.mark.parametrize("value", ["0", "-1", "101", "many", "1.5"])
def test_out_of_range_canary_max_lots_fails_fast(monkeypatch, value):
    """``CANARY_MAX_LOTS=10000`` must stop the process, never widen the cap."""
    monkeypatch.setenv("CANARY_MAX_LOTS", value)

    with pytest.raises(ValueError, match="CANARY_MAX_LOTS"):
        get_canary_config()


@pytest.mark.parametrize(
    "value",
    [
        "SBER SBERP",        # two names where exactly one may trade
        "SBER;DROP TABLE",   # a shell/SQL fragment
        "GAZP-1234",         # punctuation is not part of a MOEX name
        "СБЕР",               # Cyrillic: instrument tickers are ASCII
        "ABCDEFGHIJKLMNOPQ",  # longer than trading.instruments.ticker
    ],
)
def test_a_malformed_canary_ticker_fails_instead_of_guessing(value):
    """A present-but-malformed ticker raises rather than falling back: the
    operator must not believe the run is capped to SBER while it trades
    something else."""
    with pytest.raises(ValueError, match="canary ticker"):
        normalize_canary_ticker(value)


def test_normalize_canary_ticker_uppercases_and_strips():
    assert normalize_canary_ticker("sber") == "SBER"
    assert normalize_canary_ticker("  Gazp  ") == "GAZP"
    assert normalize_canary_ticker("LKOH1") == "LKOH1"

    with pytest.raises(ValueError, match="non-empty"):
        normalize_canary_ticker("   ")
    with pytest.raises(ValueError, match="non-empty"):
        normalize_canary_ticker(None)


def test_validate_canary_values_accepts_partial_and_none_overrides():
    validate_canary_values({})
    validate_canary_values({"enabled": True, "ticker": "SBER", "max_lots": 1})
    validate_canary_values(
        {"ticker": None, "max_lots": None, "max_open_positions": None}
    )


@pytest.mark.parametrize(
    "values,key",
    [
        ({"enabled": "yes"}, "enabled"),
        ({"max_lots": 0}, "max_lots"),
        ({"max_lots": 101}, "max_lots"),
        ({"max_lots": 1.5}, "max_lots"),
        ({"max_lots": "many"}, "max_lots"),
        ({"max_open_positions": 0}, "max_open_positions"),
        ({"ticker": "SBER SBERP"}, "ticker"),
        ({"ticker": ""}, "ticker"),
    ],
)
def test_validate_canary_values_rejects_a_smuggled_cap(values, key):
    """The executor's ``canary=`` dict obeys exactly the env ranges, so no caller
    can bypass ``CANARY_MAX_LOTS``."""
    with pytest.raises(ValueError, match=key):
        validate_canary_values(values)


def test_the_executor_rejects_an_out_of_range_canary_dict():
    """Validation happens in ``__init__``: a bad cap never reaches the sizer."""
    for canary in (
        {"enabled": True, "max_lots": 0},
        {"enabled": True, "max_lots": 101},
        {"enabled": True, "ticker": "SBER SBERP"},
        {"enabled": True, "max_open_positions": 0},
        {"enabled": "yes"},
    ):
        with pytest.raises(ValueError):
            make_executor(canary=canary)


def test_the_canary_env_reaches_the_executor(monkeypatch):
    """The go-live runbook configures the canary through env only."""
    monkeypatch.setenv("CANARY_ENABLED", "true")
    monkeypatch.setenv("CANARY_TICKER", "gazp")
    monkeypatch.setenv("CANARY_MAX_LOTS", "3")

    executor = make_executor()

    assert executor.canary == {
        "enabled": True,
        "ticker": "GAZP",
        "max_lots": 3,
        "max_open_positions": 1,
        "allow_outside_entry_window": False,
    }


def test_a_caller_canary_dict_wins_over_the_env(monkeypatch):
    """Same precedence as ``config=``: defaults, then env, then the caller."""
    monkeypatch.setenv("CANARY_ENABLED", "true")
    monkeypatch.setenv("CANARY_MAX_LOTS", "5")

    executor = make_executor(canary={"enabled": False, "max_lots": 1})

    assert executor.canary["enabled"] is False
    assert executor.canary["max_lots"] == 1
    assert executor.canary_enabled is False
    assert executor.get_metrics()["canary_enabled"] is False
    assert executor.get_metrics()["canary_ticker"] is None



# --- the canary calendar-gate bypass (PO decision of 2026-10-03, scope D) -------
#
# A weekend / off-exchange canary needs the #137 calendar gate lifted, and that
# is a scope change the PO approved on 2026-10-03. The tests below pin the three
# properties that make it safe: OFF by default, canary-only (the ordinary contour
# keeps the calendar byte-for-byte), and loud (log line, alert line, counter).

# Saturday, daytime: #137 keeps entries closed all weekend, which is exactly the
# session a weekend canary has to be allowed to trade in.
WEEKEND_NOW = datetime(2026, 10, 3, 15, 30, 0)


def _loop_ready(executor):
    """Executor whose ``run()`` touches neither signals nor the broker."""
    executor.install_signal_handlers = lambda: None
    executor.initialize = lambda: None
    executor.refresh_contexts = lambda: None
    executor.monitor_positions = lambda: None
    executor.shutdown = lambda: None
    executor._active_positions = lambda: pd.DataFrame()
    executor.config["check_interval_seconds"] = 0
    executor.config["context_refresh_seconds"] = 10**6
    executor.sleep_fn = lambda _seconds: executor.shutdown_requested.set()
    return executor


def test_the_calendar_bypass_is_off_by_default():
    """Shipped policy: a canary honours the #137 window unless PO opts in."""
    assert CANARY["allow_outside_entry_window"] is False
    assert get_canary_config()["allow_outside_entry_window"] is False
    assert make_executor().canary_allow_outside_entry_window is False
    assert canary_executor().canary_allow_outside_entry_window is False


@pytest.mark.parametrize("word", ["ture", "maybe", "0x1", "yesplease"])
def test_the_calendar_bypass_rejects_an_ambiguous_word(monkeypatch, word):
    """The knob that removes a safety gate uses the strict reader of #178: a
    typo stops the process instead of quietly deciding either way."""
    monkeypatch.setenv("CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW", word)

    with pytest.raises(ValueError, match="CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW"):
        get_canary_config()


def test_a_blank_calendar_bypass_keeps_the_gate(monkeypatch):
    """Blank = unset, the same contract as the other CANARY_* knobs."""
    monkeypatch.setenv("CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW", "   ")

    assert get_canary_config()["allow_outside_entry_window"] is False


@pytest.mark.parametrize("value", ["yes", 1, 0])
def test_validate_canary_values_rejects_a_non_boolean_bypass(value):
    """A caller dict obeys the same rules as env - no smuggled truthy value."""
    with pytest.raises(ValueError, match="allow_outside_entry_window"):
        validate_canary_values({"allow_outside_entry_window": value})


def test_the_bypass_is_announced_when_the_canary_starts(caplog):
    """The operator reads the very first log line of the run."""
    with caplog.at_level("INFO", logger=module.__name__):
        canary_executor(canary={"allow_outside_entry_window": True})

    assert (
        "CANARY: entries are allowed OUTSIDE the MOEX entry window" in caplog.text
    )

    caplog.clear()
    with caplog.at_level("INFO", logger=module.__name__):
        canary_executor()

    assert "CANARY MODE ON" in caplog.text
    assert "OUTSIDE the MOEX entry window" not in caplog.text


def test_a_canary_without_the_bypass_still_honours_the_session_calendar(caplog):
    """Default path untouched: a Saturday signal is skipped as #137 shipped it."""
    broker = StopFakeBroker()
    executor = canary_executor(
        answers=["y", "y"], broker=broker, now_fn=lambda: WEEKEND_NOW
    )

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result == {"executed": False, "reason": "outside_entry_window"}
    assert broker.calls == []
    assert executor.canary_confirmations_total == 0
    assert executor.canary_window_bypass_total == 0


def test_the_bypass_lets_one_canary_lot_through_on_a_saturday(caplog):
    """The weekend canary still trades ONE lot and still stops at both pauses -
    only the calendar gate is off, nothing else in the chain."""
    broker = StopFakeBroker()
    executor = canary_executor(
        answers=["y", "y"],
        broker=broker,
        canary={"allow_outside_entry_window": True},
        now_fn=lambda: WEEKEND_NOW,
    )

    with caplog.at_level("INFO", logger=module.__name__):
        result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result == {
        "executed": True,
        "reason": "open",
        "position_id": 41,
        "size_lots": 1,
    }
    entry = next(c for c in broker.calls if c[0] == "execute_order")
    assert entry[1]["quantity"] == 1
    assert executor.canary_window_bypass_total == 1
    assert executor.canary_confirmations_total == 2
    assert "CANARY: entry window bypassed" in caplog.text
    assert "CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true" in caplog.text


def test_the_bypass_refuses_an_operator_who_says_no():
    """Lifting the calendar gate never lifts the human gate."""
    broker = StopFakeBroker()
    executor = canary_executor(
        answers=["n"],
        broker=broker,
        canary={"allow_outside_entry_window": True},
        now_fn=lambda: WEEKEND_NOW,
    )

    result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert result["executed"] is False
    assert result["reason"] == "canary_not_confirmed"
    # Pause 1 sits after sizing, so the read-only balance probe has already run;
    # what must never happen without a "yes" is an order.
    assert not [call for call in broker.calls if call[0] == "execute_order"]
    assert executor.canary_window_bypass_total == 1
    assert executor.canary_rejections_total == 1


def test_the_bypass_logs_once_but_counts_only_signals(caplog):
    """The loop asks the same question every cycle; the counter must not inflate."""
    executor = canary_executor(
        canary={"allow_outside_entry_window": True}, now_fn=lambda: WEEKEND_NOW
    )

    with caplog.at_level("INFO", logger=module.__name__):
        assert executor._entry_window_open(WEEKEND_NOW, source="loop") is True
        assert executor._entry_window_open(WEEKEND_NOW, source="loop") is True
        assert executor._entry_window_open(WEEKEND_NOW, source="signal") is True

    assert executor.canary_window_bypass_total == 1
    assert caplog.text.count("CANARY: entry window bypassed") == 1


def test_the_bypass_never_widens_the_ordinary_contour(monkeypatch):
    """The env knob alone changes nothing: only a canary reads it, so the shipped
    sandbox/real loop keeps the #137 calendar."""
    monkeypatch.setenv("CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW", "true")
    broker = FakeBroker()
    executor = make_executor(broker=broker, now_fn=lambda: WEEKEND_NOW)

    result = executor.process_signal("SBER", CANARY_SIGNAL, imbalance=1.5)

    assert executor.canary_enabled is False
    assert result == {"executed": False, "reason": "outside_entry_window"}
    assert broker.calls == []
    assert executor.canary_window_bypass_total == 0


def test_the_bypass_is_inert_while_the_canary_switch_is_off():
    """``allow_outside_entry_window=True`` without CANARY_ENABLED is dead config."""
    executor = make_executor(
        canary={"enabled": False, "allow_outside_entry_window": True},
        now_fn=lambda: WEEKEND_NOW,
    )

    assert executor.canary_enabled is False
    assert executor._entry_window_open(WEEKEND_NOW, source="signal") is False
    assert executor.canary_window_bypass_total == 0


def test_inside_the_window_the_bypass_changes_nothing():
    """Monday 11:00 MSK needs no bypass, and using none keeps the counter at 0."""
    executor = canary_executor(canary={"allow_outside_entry_window": True})

    assert executor._entry_window_open(IN_SESSION_NOW, source="signal") is True
    assert executor.canary_window_bypass_total == 0
    assert executor._canary_window_bypass_logged is False


def test_the_loop_processes_bars_outside_the_window_for_a_bypassed_canary(caplog):
    """The main loop asks ``_entry_window_open``, so a weekend canary sees bars at
    all - without this the entry gate would never even be reached."""
    executor = _loop_ready(
        canary_executor(
            canary={"allow_outside_entry_window": True},
            now_fn=lambda: WEEKEND_NOW,
        )
    )
    bars = []
    executor.process_latest_bars = lambda: bars.append(WEEKEND_NOW)

    with caplog.at_level("INFO", logger=module.__name__):
        executor.run(duration_minutes=1)

    assert bars
    assert "CANARY: entry window bypassed" in caplog.text


def test_the_loop_keeps_the_calendar_for_a_canary_without_the_bypass():
    """Monitoring always runs (a position keeps its protection); entries do not."""
    executor = _loop_ready(canary_executor(now_fn=lambda: WEEKEND_NOW))
    bars, monitored = [], []
    executor.process_latest_bars = lambda: bars.append(1)
    executor.monitor_positions = lambda: monitored.append(1)

    executor.run(duration_minutes=1)

    assert bars == []
    assert monitored


def test_wait_for_session_open_returns_at_once_for_a_bypassed_canary(caplog):
    """Otherwise ``until_session_end`` would sleep a weekend canary until Monday."""
    executor = canary_executor(
        canary={"allow_outside_entry_window": True},
        now_fn=lambda: WEEKEND_NOW,
        sleep_fn=lambda _seconds: pytest.fail("a weekend canary must not wait"),
    )

    with caplog.at_level("INFO", logger=module.__name__):
        executor.wait_for_session_open()

    assert "CANARY: not waiting for the MOEX session open" in caplog.text


def test_wait_for_session_open_still_waits_without_the_bypass(caplog):
    """The #137 wait is untouched for every run that did not opt in."""
    executor = canary_executor(now_fn=lambda: WEEKEND_NOW)

    def stop(_seconds):
        executor.shutdown_requested.set()

    executor.sleep_fn = stop

    with caplog.at_level("INFO", logger=module.__name__):
        executor.wait_for_session_open()

    assert "CANARY: not waiting" not in caplog.text
    assert "Waiting for MOEX session open at 2026-10-05 10:00 MSK" in caplog.text


def test_the_bypass_is_published_in_the_metrics_snapshot():
    bypassed = canary_executor(
        canary={"allow_outside_entry_window": True}
    ).get_metrics()
    assert bypassed["canary_allow_outside_entry_window"] is True
    assert bypassed["canary_window_bypass_total"] == 0

    plain = canary_executor().get_metrics()
    assert plain["canary_allow_outside_entry_window"] is False
    assert plain["canary_window_bypass_total"] == 0

    ordinary = make_executor().get_metrics()
    # None, not False: "not a canary" stays distinct in the JSONB snapshot
    assert ordinary["canary_allow_outside_entry_window"] is None
    assert ordinary["canary_window_bypass_total"] == 0


def test_the_bypass_is_named_in_the_canary_alert_lines():
    lines = dict(
        canary_executor(
            canary={"allow_outside_entry_window": True}
        )._canary_lines()
    )
    assert lines["Вход вне окна сессии"] == (
        "разрешён (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true)"
    )

    quiet = dict(canary_executor()._canary_lines())
    # None is dropped by _notify, so the shipped alert body stays byte-identical
    assert quiet["Вход вне окна сессии"] is None



