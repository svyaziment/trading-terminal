from decimal import Decimal
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from app.analytics import live_executor as module
from app.analytics.live_executor import (
    LiveExecutor,
    TokenBucket,
    ensure_live_positions_table,
)
from app.broker.tinkoff_sandbox import SandboxAPIError


IN_SESSION_NOW = datetime(2026, 8, 31, 11, 0, 0)


class Result:
    def __init__(self, frame=None):
        self.frame = frame if frame is not None else pd.DataFrame()

    def to_dataframe(self):
        return self.frame.copy()


class FakeDB:
    def __init__(self, active=None, instruments=None):
        self.active = active if active is not None else pd.DataFrame()
        self.instruments = (
            instruments if instruments is not None else pd.DataFrame()
        )
        self.select_calls = []
        self.execute_calls = []

    def select(self, query, params=None):
        self.select_calls.append((query, params))
        normalized = " ".join(query.split())
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
        return Result()

    def execute(self, query, params=None):
        self.execute_calls.append((" ".join(query.split()), params))
        return 1


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
            **config,
        },
        now_fn=now_fn or (lambda: IN_SESSION_NOW),
        **kwargs,
    )
    executor.strategy_name = "active-strategy"
    executor.strategy_config = {"patterns": ["levels_reversal"]}
    executor.instruments = {
        "SBER": {"instrument_id": "figi-sber", "lot_size": 10}
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
            active_position(),
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
    assert cancelled == ["take-1", "entry-2"]
    assert not any(
        call[0] == "execute_order" for call in broker.calls
    )
    assert any(
        "SET status='cancelled'" in query and params[1] == "shutdown"
        for query, params in db.execute_calls
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


def test_until_session_end_waits_until_ten_then_stops_at_nineteen(caplog):
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

    with caplog.at_level("INFO", logger=module.__name__):
        executor.run(until_session_end=True)

    assert events["init"] == [datetime(2026, 8, 31, 10, 0)]
    assert fake.wall >= datetime(2026, 8, 31, 19, 0, 0)
    assert events["monitor"] >= 1
    assert events["bars"] >= 1
    assert "Waiting for MOEX session open at 2026-08-31 10:00 MSK" in caplog.text
    assert "MOEX session closed at 2026-08-31 19:00 MSK" in caplog.text


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
