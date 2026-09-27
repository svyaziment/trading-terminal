"""Issue #176 / Epic #172 task D: live equity snapshots and risk gates.

Covers the whole risk contour added by #176 in one place:

* ``trading.live_equity`` schema contract (migration + runtime DDL);
* ``get_live_risk_config()`` defaults, env overrides and range validation;
* the equity formula (``cash + market_value``, no realized-PnL double count);
* the daily drawdown basis (``session_key`` peak, not a lifetime peak);
* breach behaviour: entries blocked with ``risk_breach``, stops preserved,
  no forced flatten, auto-reset on a new MSK day, manual self-consuming reset,
  and restoration across a restart;
* the absolute notional cap (``position_size_limit``);
* rate-limit deferral, failure containment and ``get_metrics()`` exposure;
* the read-only monitoring endpoints.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from app.analytics import live_executor as module
from app.analytics.live_executor import (
    LIVE_EQUITY_INSERT_COLUMNS,
    LiveExecutor,
    RISK_SKIP_REASON_BREACH,
    RISK_SKIP_REASON_POSITION_SIZE,
)
from app.analytics.live_schema import (
    LIVE_EQUITY_SCHEMA_STATEMENTS,
    LIVE_EQUITY_TABLE,
    REQUIRED_LIVE_EQUITY_COLUMNS,
    REQUIRED_LIVE_POSITIONS_COLUMNS,
    REQUIRED_LIVE_POSITIONS_STATUSES,
    RISK_BREACH_RESET_KEY,
    ensure_live_equity_schema,
)
from app.analytics.trading_config import (
    LIVE_RISK,
    LIVE_RISK_BOUNDS,
    get_live_risk_config,
    get_live_trading_config,
)

# Monday inside the MOEX entry window (10:00-19:00 MSK).
SESSION_NOW = datetime(2026, 9, 28, 12, 0, 0)
SESSION_DAY = SESSION_NOW.date()
NEXT_DAY = SESSION_NOW + timedelta(days=1)


class Result:
    """Minimal ``DBManager.select()`` result double."""

    def __init__(self, frame=None):
        self.frame = frame if frame is not None else pd.DataFrame()

    def to_dataframe(self):
        return self.frame.copy()


class FakeCursor:
    """Cursor double that answers ``pg_try_advisory_lock`` with 'acquired'."""

    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        if self.executed and "pg_try_advisory_lock" in self.executed[-1][0]:
            return (True,)
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection:
    """Connection double for the Issue #174 dedicated advisory-lock channel."""

    def __init__(self):
        self.cursors = []

    def cursor(self):
        cursor = FakeCursor()
        self.cursors.append(cursor)
        return cursor




class RiskFakeDB:
    """In-memory stand-in for the tables the risk contour touches."""

    def __init__(
        self,
        *,
        active=None,
        instruments=None,
        closed_pnl=0.0,
        app_settings=None,
        equity_rows=None,
        live_equity_missing=False,
        select_error=None,
    ):
        self.active = active if active is not None else pd.DataFrame()
        self.instruments = (
            instruments if instruments is not None else pd.DataFrame()
        )
        self.closed_pnl = closed_pnl
        self.app_settings = dict(app_settings or {})
        self.equity_rows = list(equity_rows or [])
        self.live_equity_missing = live_equity_missing
        self.select_error = select_error
        self.select_calls = []
        self.execute_calls = []
        # Issue #174: dedicated connection used for the advisory lock.
        self._dedicated_conn = FakeConnection()
        self.dedicated_connection_calls = []

    def _frame(self, rows):
        return Result(pd.DataFrame(rows) if rows else pd.DataFrame())

    def _max(self, column, rows):
        values = [float(row[column]) for row in rows if row.get(column) is not None]
        return Result(pd.DataFrame([{column: max(values) if values else None}]))

    def _filter_equity(self, rows, params):
        """Apply the named-parameter filters the history endpoint builds."""
        if not isinstance(params, dict):
            return list(rows)
        out = list(rows)
        if params.get("session_key") is not None:
            out = [r for r in out if r.get("session_key") == params["session_key"]]
        if params.get("date_from") is not None:
            out = [r for r in out if r.get("session_key") >= params["date_from"]]
        if params.get("date_to") is not None:
            out = [r for r in out if r.get("session_key") <= params["date_to"]]
        return out

    def select(self, query, params=None):
        self.select_calls.append((query, params))
        normalized = " ".join(query.split())
        if self.select_error is not None:
            raise self.select_error
        if self.live_equity_missing and LIVE_EQUITY_TABLE in normalized:
            raise RuntimeError('relation "trading.live_equity" does not exist')
        if "information_schema.tables" in normalized:
            return Result(
                pd.DataFrame({"table_name": ["app_settings", "live_positions"]})
            )
        if "information_schema.columns" in normalized:
            return Result(
                pd.DataFrame({"column_name": list(REQUIRED_LIVE_POSITIONS_COLUMNS)})
            )
        if "pg_constraint" in normalized:
            definition = (
                "CHECK (((status)::text = ANY ((ARRAY["
                + ", ".join(
                    f"'{status}'::character varying"
                    for status in REQUIRED_LIVE_POSITIONS_STATUSES
                )
                + "])::text[])))"
            )
            return Result(pd.DataFrame({"definition": [definition]}))
        if LIVE_EQUITY_TABLE in normalized:
            return self._select_live_equity(normalized, params)
        if "SUM(pnl_rub)" in normalized:
            return Result(pd.DataFrame([{"realized_pnl_rub": self.closed_pnl}]))
        if "SELECT id FROM trading.live_positions" in normalized:
            # _insert_position reloads the row it just wrote.
            return Result(pd.DataFrame([{"id": 41}]))
        if "FROM trading.instruments" in normalized:
            return Result(self.instruments)
        if "FROM trading.live_positions" in normalized:
            return Result(self.active)
        if "FROM trading.app_settings" in normalized:
            key = params[0] if params else None
            if key in self.app_settings:
                return Result(pd.DataFrame([{"key": key, "value": self.app_settings[key]}]))
            return Result()
        return Result()

    def _select_live_equity(self, normalized, params):
        rows = list(self.equity_rows)
        if "MAX(peak_equity_all_time_rub)" in normalized:
            return self._max("peak_equity_all_time_rub", rows)
        if "MAX(peak_equity_rub)" in normalized:
            key = params[0] if params else None
            return self._max(
                "peak_equity_rub", [r for r in rows if r.get("session_key") == key]
            )
        if "COUNT(*)" in normalized:
            return Result(
                pd.DataFrame([{"total": len(self._filter_equity(rows, params))}])
            )
        if "WHERE session_key = %s" in normalized and "LIMIT 1" in normalized:
            # _restore_risk_breach_state: newest row of the current session.
            key = params[0] if params else None
            session_rows = [r for r in rows if r.get("session_key") == key]
            session_rows.sort(key=lambda row: row.get("timestamp"), reverse=True)
            return self._frame(session_rows[:1])
        # Monitoring API: full column list, newest first, optional page.
        filtered = self._filter_equity(rows, params)
        filtered.sort(key=lambda row: (row.get("timestamp"), row.get("id")), reverse=True)
        if isinstance(params, dict) and params.get("limit") is not None:
            offset = int(params.get("offset") or 0)
            filtered = filtered[offset : offset + int(params["limit"])]
        return self._frame(filtered)

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.execute_calls.append((normalized, params))
        if f"INSERT INTO {LIVE_EQUITY_TABLE}" in normalized:
            row = dict(zip(LIVE_EQUITY_INSERT_COLUMNS, params or ()))
            row["id"] = len(self.equity_rows) + 1
            self.equity_rows.append(row)
        elif "INSERT INTO trading.app_settings" in normalized:
            # The executor writes the self-consuming reset flag back as false.
            if params:
                self.app_settings[params[0]] = False
        return 1

    # -- Issue #174 advisory lock contract --------------------------------
    def get_dedicated_connection(self):
        self.dedicated_connection_calls.append(("get", None))
        return self._dedicated_conn

    def release_dedicated_connection(self, conn):
        self.dedicated_connection_calls.append(("release", conn))


def holding(ticker="SBER", quantity="100", average="95", current="90"):
    """One broker portfolio holding, shaped like SandboxPosition."""
    return SimpleNamespace(
        figi=f"figi-{ticker.lower()}",
        ticker=ticker,
        instrument_uid=f"uid-{ticker.lower()}",
        instrument_type="share",
        quantity=Decimal(quantity),
        quantity_lots=Decimal(quantity) / Decimal("10"),
        blocked_lots=Decimal("0"),
        average_price=None if average is None else Decimal(average),
        current_price=None if current is None else Decimal(current),
        expected_yield=None,
    )


class RiskFakeBroker:
    """Broker double covering the equity snapshot and the entry path."""

    def __init__(self, *, cash=Decimal("50000"), positions=None, defer_equity=False,
                 account_id="sandbox-acc-1"):
        self.calls = []
        self.cash = cash
        self.positions = list(positions or [])
        self.defer_equity = defer_equity
        # TinkoffSandboxClient caches the (possibly discovered) account id here.
        self.account_id = account_id
        self.order_number = 0

    def check_balance(self, currency=None):
        self.calls.append(("check_balance", {"currency": currency}))
        return None if self.defer_equity else self.cash

    def get_positions(self):
        self.calls.append(("get_positions", {}))
        return None if self.defer_equity else self.positions

    def execute_order(self, **kwargs):
        self.calls.append(("execute_order", kwargs))
        self.order_number += 1
        is_market_buy = kwargs["direction"] == "buy" and kwargs["order_type"] == "market"
        return SimpleNamespace(
            order_id=f"order-{self.order_number}",
            lots_executed=kwargs["quantity"] if is_market_buy else 0,
            executed_order_price=Decimal("100") if is_market_buy else None,
            total_order_amount=None,
            executed_commission=None,
            message="",
        )

    def post_stop_order(self, **kwargs):
        self.calls.append(("post_stop_order", kwargs))
        return SimpleNamespace(stop_order_id="stop-1", order_request_id="req-1")

    def get_stop_orders(self, status="active", **kwargs):
        self.calls.append(("get_stop_orders", {"status": status, **kwargs}))
        return []

    def cancel_stop_order(self, stop_order_id):
        self.calls.append(("cancel_stop_order", {"stop_order_id": stop_order_id}))
        return SimpleNamespace(stop_order_id=stop_order_id, cancelled_at=None)

    def cancel_order(self, order_id):
        self.calls.append(("cancel_order", {"order_id": order_id}))
        return SimpleNamespace(order_id=order_id)

    def get_orders(self):
        self.calls.append(("get_orders", {}))
        return []

    def get_operations(self, **kwargs):
        self.calls.append(("get_operations", kwargs))
        return []


def make_risk_executor(*, db=None, broker=None, now_fn=None, clock=None, sleep_fn=None, **config):
    """Executor wired for risk tests: no rate limiting, in-session clock."""
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    if sleep_fn is not None:
        kwargs["sleep_fn"] = sleep_fn
    executor = LiveExecutor(
        db=db if db is not None else RiskFakeDB(),
        broker=broker if broker is not None else RiskFakeBroker(),
        config={
            "enabled": True,
            "api_rate_limit": 10,
            "entry_token_reserve": 0.0,
            "broker_stop_enabled": False,
            **config,
        },
        now_fn=now_fn or (lambda: SESSION_NOW),
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


def buy_decision():
    return {"action": "enter", "entry_price": 100, "stop": 95, "take": 110}


def equity_row(**overrides):
    """A pre-existing trading.live_equity row (as written by the executor)."""
    row = {
        "id": 1,
        "timestamp": SESSION_NOW - timedelta(minutes=1),
        "session_key": SESSION_DAY,
        "equity_rub": 60000.0,
        "cash_rub": 60000.0,
        "market_value_rub": 0.0,
        "realized_pnl_rub": 0.0,
        "unrealized_pnl_rub": 0.0,
        "peak_equity_rub": 60000.0,
        "peak_equity_all_time_rub": 60000.0,
        "drawdown_pct": 0.0,
        "open_positions": 0,
        "risk_breach": False,
        "account_id": None,
        "strategy_name": "active-strategy",
    }
    row.update(overrides)
    return row





# --- Schema contract ----------------------------------------------------------


def test_live_equity_runtime_ddl_is_idempotent_and_never_destructive():
    joined = "\n".join(LIVE_EQUITY_SCHEMA_STATEMENTS)

    assert f"CREATE TABLE IF NOT EXISTS {LIVE_EQUITY_TABLE}" in joined
    assert "ON CONFLICT (key) DO NOTHING" in joined
    assert f"'{RISK_BREACH_RESET_KEY}'" in joined
    # A runtime DDL must never delete operator data.
    assert "DROP TABLE" not in joined
    assert "DROP COLUMN" not in joined
    assert "TRUNCATE" not in joined


def test_live_equity_runtime_ddl_covers_every_required_column():
    create_table = next(
        statement
        for statement in LIVE_EQUITY_SCHEMA_STATEMENTS
        if f"CREATE TABLE IF NOT EXISTS {LIVE_EQUITY_TABLE}" in statement
    )
    for column in REQUIRED_LIVE_EQUITY_COLUMNS:
        assert column in create_table, column
    # created_at is written server-side, so it is not part of the insert tuple.
    assert set(LIVE_EQUITY_INSERT_COLUMNS) == set(REQUIRED_LIVE_EQUITY_COLUMNS) - {
        "id",
        "created_at",
    }


def test_ensure_live_equity_schema_applies_every_statement_once():
    db = RiskFakeDB()

    ensure_live_equity_schema(db)
    first_run = list(db.execute_calls)
    ensure_live_equity_schema(db)

    assert [call[0] for call in db.execute_calls[: len(first_run)]] == [
        call[0] for call in first_run
    ]
    assert len(db.execute_calls) == 2 * len(LIVE_EQUITY_SCHEMA_STATEMENTS)


def test_live_equity_migration_chains_onto_the_current_head():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260927_001_live_equity.py"
    )
    spec = importlib.util.spec_from_file_location("migration_20260927_001", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "20260927_001"
    assert migration.down_revision == "20260916_001"
    upgrade_sql = migration._CREATE_LIVE_EQUITY_SQL
    assert "CREATE TABLE IF NOT EXISTS trading.live_equity" in upgrade_sql
    for column in REQUIRED_LIVE_EQUITY_COLUMNS:
        if column in ("id", "created_at"):
            continue
        assert column in upgrade_sql, column
    # Downgrade is the only place a drop is allowed.
    assert "DROP TABLE" not in upgrade_sql
    assert f"'{RISK_BREACH_RESET_KEY}'" in migration._SEED_RISK_SETTINGS_SQL
    assert "ON CONFLICT (key) DO NOTHING" in migration._SEED_RISK_SETTINGS_SQL


# --- Risk configuration -------------------------------------------------------


def test_live_risk_defaults_match_the_shipped_policy():
    config = get_live_risk_config()

    assert config["max_daily_loss_pct"] == LIVE_RISK["max_daily_loss_pct"]
    assert config["max_position_size"] == LIVE_RISK["max_position_size"]
    assert config["equity_snapshot_enabled"] is True
    assert config["risk_breach_reset_key"] == RISK_BREACH_RESET_KEY


def test_live_risk_config_returns_an_isolated_copy():
    first = get_live_risk_config()
    first["max_daily_loss_pct"] = 99.0

    assert get_live_risk_config()["max_daily_loss_pct"] == LIVE_RISK["max_daily_loss_pct"]
    assert LIVE_RISK["max_daily_loss_pct"] != 99.0


def test_env_overrides_apply_to_risk_limits(monkeypatch):
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "3.5")
    monkeypatch.setenv("MAX_POSITION_SIZE", "250000")
    monkeypatch.setenv("MAX_OPEN_POSITIONS", "7")
    monkeypatch.setenv("LIVE_EQUITY_SNAPSHOT", "off")

    risk = get_live_risk_config()
    live = get_live_trading_config()

    assert risk["max_daily_loss_pct"] == 3.5
    assert risk["max_position_size"] == 250000.0
    assert risk["equity_snapshot_enabled"] is False
    assert live["max_open_positions"] == 7


def test_blank_env_overrides_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "   ")
    monkeypatch.setenv("MAX_POSITION_SIZE", "")
    monkeypatch.setenv("MAX_OPEN_POSITIONS", "")

    risk = get_live_risk_config()

    assert risk["max_daily_loss_pct"] == LIVE_RISK["max_daily_loss_pct"]
    assert risk["max_position_size"] == LIVE_RISK["max_position_size"]
    assert get_live_trading_config()["max_open_positions"] == 5


@pytest.mark.parametrize(
    "env_name,value",
    [
        ("MAX_DAILY_LOSS_PCT", "0"),
        ("MAX_DAILY_LOSS_PCT", "-1"),
        ("MAX_DAILY_LOSS_PCT", "100.5"),
        ("MAX_DAILY_LOSS_PCT", "abc"),
        ("MAX_DAILY_LOSS_PCT", "nan"),
        ("MAX_POSITION_SIZE", "0"),
        ("MAX_POSITION_SIZE", "-5"),
        ("MAX_POSITION_SIZE", "1e13"),
        ("MAX_POSITION_SIZE", "huge"),
        ("MAX_OPEN_POSITIONS", "0"),
        ("MAX_OPEN_POSITIONS", "-3"),
        ("MAX_OPEN_POSITIONS", "101"),
        ("MAX_OPEN_POSITIONS", "many"),
    ],
)
def test_out_of_range_env_overrides_fail_fast(monkeypatch, env_name, value):
    monkeypatch.setenv(env_name, value)
    assert env_name.lower() in {
        name.lower() for name in LIVE_RISK_BOUNDS
    } or env_name == "MAX_OPEN_POSITIONS"

    with pytest.raises(ValueError, match=env_name):
        if env_name == "MAX_OPEN_POSITIONS":
            get_live_trading_config()
        else:
            get_live_risk_config()


def test_executor_rejects_out_of_range_risk_overrides():
    with pytest.raises(ValueError, match="max_daily_loss_pct"):
        make_risk_executor(max_daily_loss_pct=250.0)
    with pytest.raises(ValueError, match="max_position_size"):
        make_risk_executor(max_position_size=-1.0)
    with pytest.raises(ValueError, match="equity_snapshot_enabled"):
        make_risk_executor(equity_snapshot_enabled="yes")


# --- Equity snapshot ----------------------------------------------------------


def test_snapshot_persists_equity_as_cash_plus_market_value():
    db = RiskFakeDB()
    broker = RiskFakeBroker(
        cash=Decimal("50000"),
        positions=[holding(quantity="100", average="95", current="90")],
    )
    executor = make_risk_executor(db=db, broker=broker)

    snapshot = executor._write_live_equity()

    assert snapshot is not None
    # 50000 cash + 100 shares * 90 = 59000.
    assert snapshot["equity_rub"] == 59000.0
    assert snapshot["cash_rub"] == 50000.0
    assert snapshot["market_value_rub"] == 9000.0
    assert snapshot["unrealized_pnl_rub"] == -500.0
    assert snapshot["session_key"] == SESSION_DAY
    assert snapshot["timestamp"] == SESSION_NOW
    assert snapshot["strategy_name"] == "active-strategy"
    assert snapshot["account_id"] == "sandbox-acc-1"
    assert db.equity_rows[-1]["equity_rub"] == 59000.0
    assert executor.equity_snapshots_total == 1
    assert executor.last_equity is snapshot


def test_snapshot_does_not_double_count_realized_pnl():
    """``check_balance`` already contains the proceeds of every close."""
    db = RiskFakeDB(closed_pnl=1234.56)
    broker = RiskFakeBroker(cash=Decimal("50000"), positions=[])
    executor = make_risk_executor(db=db, broker=broker)

    snapshot = executor._write_live_equity()

    assert snapshot["realized_pnl_rub"] == 1234.56
    assert snapshot["equity_rub"] == 50000.0


def test_snapshot_marks_unpriced_holdings_out_instead_of_inventing_a_price():
    broker = RiskFakeBroker(
        cash=Decimal("10000"),
        positions=[holding(quantity="10", average=None, current=None)],
    )
    executor = make_risk_executor(broker=broker)

    snapshot = executor._write_live_equity()

    assert snapshot["market_value_rub"] == 0.0
    assert snapshot["equity_rub"] == 10000.0


def test_snapshot_counts_open_live_positions():
    active = pd.DataFrame(
        [{"id": 1, "ticker": "SBER", "status": "open"}, {"id": 2, "ticker": "GAZP", "status": "pending"}]
    )
    executor = make_risk_executor(db=RiskFakeDB(active=active))

    assert executor._write_live_equity()["open_positions"] == 2


def test_snapshot_is_disabled_by_config():
    db = RiskFakeDB()
    executor = make_risk_executor(db=db, equity_snapshot_enabled=False)

    assert executor._write_live_equity() is None
    assert db.equity_rows == []
    assert executor.equity_snapshots_total == 0


def test_snapshot_is_deferred_when_the_rate_limit_reserve_is_busy():
    db = RiskFakeDB()
    broker = RiskFakeBroker(defer_equity=True)
    executor = make_risk_executor(db=db, broker=broker)

    assert executor._write_live_equity() is None
    assert db.equity_rows == []
    assert executor.equity_snapshot_skipped_total == 1
    assert executor.equity_snapshot_errors_total == 0


def test_snapshot_failure_never_propagates_into_the_trading_loop():
    db = RiskFakeDB(select_error=RuntimeError("db down"))
    executor = make_risk_executor(db=db)

    assert executor._write_live_equity() is None
    assert executor.equity_snapshot_errors_total == 1
    assert executor._consecutive_errors == 0


# --- Daily drawdown basis -----------------------------------------------------


def test_drawdown_is_measured_against_the_daily_peak():
    db = RiskFakeDB(equity_rows=[equity_row(peak_equity_rub=60000.0)])
    broker = RiskFakeBroker(cash=Decimal("59000"), positions=[])
    executor = make_risk_executor(db=db, broker=broker, max_daily_loss_pct=2.0)

    snapshot = executor._write_live_equity()

    assert snapshot["peak_equity_rub"] == 60000.0
    # (60000 - 59000) / 60000 * 100 = 1.6667%
    assert snapshot["drawdown_pct"] == pytest.approx(1000.0 / 60000.0 * 100.0, abs=1e-4)
    assert snapshot["risk_breach"] is False
    assert executor._risk_breach_active is False


def test_daily_peak_ignores_older_sessions_so_the_gate_can_release():
    """The regression this guards: a lifetime peak would block entries forever."""
    db = RiskFakeDB(
        equity_rows=[
            equity_row(
                session_key=SESSION_DAY - timedelta(days=3),
                peak_equity_rub=999999.0,
                peak_equity_all_time_rub=999999.0,
            )
        ]
    )
    broker = RiskFakeBroker(cash=Decimal("59000"), positions=[])
    executor = make_risk_executor(db=db, broker=broker, max_daily_loss_pct=2.0)

    snapshot = executor._write_live_equity()

    assert snapshot["peak_equity_rub"] == 59000.0
    assert snapshot["drawdown_pct"] == 0.0
    assert snapshot["peak_equity_all_time_rub"] == 999999.0
    assert executor._risk_breach_active is False


def test_peak_ratchets_up_within_the_same_session():
    db = RiskFakeDB(equity_rows=[equity_row(peak_equity_rub=55000.0)])
    broker = RiskFakeBroker(cash=Decimal("61000"), positions=[])
    executor = make_risk_executor(db=db, broker=broker)

    snapshot = executor._write_live_equity()

    assert snapshot["peak_equity_rub"] == 61000.0
    assert snapshot["drawdown_pct"] == 0.0


# --- Breach behaviour ---------------------------------------------------------


def breached_executor(*, app_settings=None, now_fn=None, cash="58000", peak=60000.0, **config):
    """Executor whose first snapshot crosses ``max_daily_loss_pct``."""
    db = RiskFakeDB(
        equity_rows=[
            equity_row(peak_equity_rub=peak, peak_equity_all_time_rub=peak)
        ],
        app_settings=app_settings,
    )
    broker = RiskFakeBroker(cash=Decimal(cash), positions=[])
    config.setdefault("max_daily_loss_pct", 2.0)
    executor = make_risk_executor(db=db, broker=broker, now_fn=now_fn, **config)
    return executor, db, broker


def test_breach_latches_logs_critical_and_persists_the_flag(caplog):
    executor, db, _ = breached_executor()

    with caplog.at_level("CRITICAL", logger=module.__name__):
        snapshot = executor._write_live_equity()

    # (60000 - 58000) / 60000 = 3.3333% >= 2.0
    assert snapshot["drawdown_pct"] == pytest.approx(2000.0 / 60000.0 * 100.0, abs=1e-4)
    assert snapshot["risk_breach"] is True
    assert db.equity_rows[-1]["risk_breach"] is True
    assert executor._risk_breach_active is True
    assert executor.risk_breach_total == 1
    assert executor._risk_breach_session_key == SESSION_DAY
    assert "RISK BREACH" in caplog.text
    assert "new entries blocked" in caplog.text


def test_breach_blocks_new_entries_with_the_risk_breach_reason(caplog):
    executor, _, broker = breached_executor()
    executor._write_live_equity()

    with caplog.at_level("WARNING", logger=module.__name__):
        result = executor.process_signal("SBER", buy_decision(), imbalance=1.5)

    assert result["reason"] == RISK_SKIP_REASON_BREACH
    assert result["executed"] is False
    assert executor.risk_gate_rejections_total == 1
    assert "risk_breach" in caplog.text
    # The gate runs before sizing and execution: nothing reached the broker.
    assert [call[0] for call in broker.calls if call[0] == "execute_order"] == []


def test_breach_never_flattens_or_cancels_protection():
    """Product Owner decision 2026-09-18: block entries, keep the stops."""
    executor, _, broker = breached_executor()
    executor._write_live_equity()
    executor.process_signal("SBER", buy_decision(), imbalance=1.5)

    destructive = [
        call[0]
        for call in broker.calls
        if call[0] in ("execute_order", "cancel_order", "cancel_stop_order")
    ]
    assert destructive == []


def test_breach_stays_latched_while_equity_recovers_within_the_same_day():
    executor, _, broker = breached_executor()
    executor._write_live_equity()
    assert executor._risk_breach_active is True

    broker.cash = Decimal("61000")  # full recovery, same MSK day
    snapshot = executor._write_live_equity()

    assert snapshot["drawdown_pct"] == 0.0
    assert snapshot["risk_breach"] is True
    assert executor._risk_breach_active is True
    assert executor.risk_breach_total == 1  # no duplicate alert


def test_breach_auto_resets_on_the_next_msk_day(caplog):
    now = [SESSION_NOW]
    executor, _, broker = breached_executor(now_fn=lambda: now[0])
    executor._write_live_equity()
    assert executor._risk_breach_active is True

    now[0] = NEXT_DAY
    with caplog.at_level("INFO", logger=module.__name__):
        snapshot = executor._write_live_equity()

    assert executor._risk_breach_active is False
    assert executor._risk_breach_session_key is None
    assert executor.risk_breach_resets_total == 1
    assert snapshot["session_key"] == NEXT_DAY.date()
    # A new day starts a new peak, so the drawdown restarts from zero.
    assert snapshot["peak_equity_rub"] == 58000.0
    assert snapshot["drawdown_pct"] == 0.0
    assert snapshot["risk_breach"] is False
    assert "risk breach cleared: reason=new_msk_session_day" in caplog.text
    assert executor._risk_gate() is None


def test_manual_reset_clears_breach_and_consumes_the_flag():
    executor, db, _ = breached_executor(
        app_settings={RISK_BREACH_RESET_KEY: True}
    )
    executor._write_live_equity()
    assert executor._risk_breach_active is True

    executor._refresh_risk_breach_reset()

    assert executor._risk_breach_active is False
    assert executor.risk_breach_resets_total == 1
    # Self-consuming: a stale true cannot disarm a later breach.
    assert db.app_settings[RISK_BREACH_RESET_KEY] is False
    assert any(
        "INSERT INTO trading.app_settings" in query for query, _ in db.execute_calls
    )
    assert executor._risk_gate() is None


def test_reset_flag_false_leaves_an_active_breach_alone():
    executor, _, _ = breached_executor(
        app_settings={RISK_BREACH_RESET_KEY: False}
    )
    executor._write_live_equity()

    executor._refresh_risk_breach_reset()

    assert executor._risk_breach_active is True
    assert executor.risk_breach_resets_total == 0


def test_reset_flag_as_jsonb_text_is_understood():
    executor, _, _ = breached_executor(
        app_settings={RISK_BREACH_RESET_KEY: "true"}
    )
    executor._write_live_equity()

    executor._refresh_risk_breach_reset()

    assert executor._risk_breach_active is False


def test_breach_survives_a_restart_through_initialize(monkeypatch, caplog):
    """Bouncing the process must not disarm a day that already breached."""
    db = RiskFakeDB(
        equity_rows=[equity_row(risk_breach=True, drawdown_pct=3.3333)],
        instruments=pd.DataFrame(
            [
                {
                    "ticker": "SBER",
                    "figi": "figi-sber",
                    "lot_size": 10,
                    "min_price_increment": 0.01,
                }
            ]
        ),
    )
    executor = make_risk_executor(db=db, max_daily_loss_pct=2.0)
    monkeypatch.setattr(
        module,
        "get_paper_strategy",
        lambda _db: ({"patterns": ["levels_reversal"]}, ["SBER"], "s1"),
    )
    monkeypatch.setattr(module, "get_live_trading_universe", lambda _db=None: ["SBER"])
    monkeypatch.setattr(
        module,
        "build_4h_context",
        lambda *_args: {
            "levels": ["l"],
            "ts_4h": ["t"],
            "atr_by_ts": {"t": 1},
            "buy_ts": [],
        },
    )

    class Evaluator:
        def __init__(self, config):
            pass

        def load_context(self, *args):
            pass

    executor.evaluator_factory = Evaluator
    with caplog.at_level("CRITICAL", logger=module.__name__):
        executor.initialize()

    assert executor._risk_breach_active is True
    assert executor._risk_breach_session_key == SESSION_DAY
    assert "Restored active risk breach" in caplog.text
    assert executor._risk_gate()["reason"] == RISK_SKIP_REASON_BREACH


def test_clean_session_leaves_the_gate_open_after_restore():
    db = RiskFakeDB(equity_rows=[equity_row(risk_breach=False)])
    executor = make_risk_executor(db=db)

    executor._restore_risk_breach_state()

    assert executor._risk_breach_active is False
    assert executor._risk_gate() is None
    assert executor._equity_session_key == SESSION_DAY


def test_restore_tolerates_an_unmigrated_live_equity_table(caplog):
    db = RiskFakeDB(live_equity_missing=True)
    executor = make_risk_executor(db=db)

    with caplog.at_level("WARNING", logger=module.__name__):
        executor._restore_risk_breach_state()

    assert executor._risk_breach_active is False
    assert "Cannot restore live risk-breach state" in caplog.text


# --- Absolute notional cap ----------------------------------------------------


def test_position_above_the_notional_cap_is_rejected():
    broker = RiskFakeBroker(cash=Decimal("500000"), positions=[])
    executor = make_risk_executor(broker=broker, max_position_size=5000.0)

    result = executor.process_signal("SBER", buy_decision(), imbalance=1.5)

    assert result["reason"] == RISK_SKIP_REASON_POSITION_SIZE
    assert result["executed"] is False
    assert executor.position_size_rejections_total == 1
    # Rejected before the order is sent.
    assert [call[0] for call in broker.calls if call[0] == "execute_order"] == []


def test_position_exactly_at_the_cap_is_accepted():
    """The comparison is strict: notional > cap, not >=."""
    broker = RiskFakeBroker(cash=Decimal("50000"), positions=[])
    executor = make_risk_executor(broker=broker, max_position_size=10000.0)

    result = executor.process_signal("SBER", buy_decision(), imbalance=1.5)

    assert result["executed"] is True
    assert result["reason"] == "open"
    assert result["size_lots"] == 10  # 10 lots * 10 shares * 100 = 10000 RUB
    assert executor.position_size_rejections_total == 0


def test_cap_is_measured_on_the_order_not_on_the_sizer_budget():
    """The min_lot branch lifts size_lots to 1; the cap must still catch it."""
    broker = RiskFakeBroker(cash=Decimal("500000"), positions=[])
    executor = make_risk_executor(broker=broker, max_position_size=8000.0)
    # A 99.9% stop shrinks the risk budget to ~5005 RUB (below the cap) while
    # one lot of a 1000 RUB instrument costs 10000 RUB (above it).
    wide_stop = {"action": "enter", "entry_price": 1000, "stop": 1, "take": 2000}

    result = executor.process_signal("SBER", wide_stop, imbalance=1.5)

    assert result["reason"] == RISK_SKIP_REASON_POSITION_SIZE
    assert executor.position_size_rejections_total == 1
    assert [call[0] for call in broker.calls if call[0] == "execute_order"] == []


def test_max_open_positions_gate_still_reads_the_live_trading_value():
    """#176 adds no second source for this limit (decision D3-A)."""
    active = pd.DataFrame(
        [{"id": index, "ticker": f"T{index}", "status": "open"} for index in range(5)]
    )
    executor = make_risk_executor(db=RiskFakeDB(active=active))

    result = executor.process_signal("SBER", buy_decision(), imbalance=1.5)

    assert result["reason"] == "max_open_positions"
    assert executor.get_metrics()["max_open_positions"] == 5


# --- Gate wiring and observability --------------------------------------------


def test_gate_is_fail_open_before_the_first_snapshot_and_logs_once(caplog):
    executor = make_risk_executor()

    with caplog.at_level("WARNING", logger=module.__name__):
        assert executor._risk_gate() is None
    assert "fail-open" in caplog.text

    caplog.clear()
    with caplog.at_level("WARNING", logger=module.__name__):
        assert executor._risk_gate() is None
    assert "fail-open" not in caplog.text  # not re-logged on every signal


def test_gate_is_silent_when_snapshots_are_disabled():
    executor = make_risk_executor(equity_snapshot_enabled=False)
    executor.last_equity = None

    assert executor._risk_gate() is None
    assert executor._risk_gate_warned is False


def test_equity_calls_keep_the_entry_reserve_so_protection_never_starves():
    executor = make_risk_executor(entry_token_reserve=2.5)

    assert executor._priority_reserve("equity") == 2.5
    assert executor._priority_reserve("entry") == 2.5
    assert executor._priority_reserve("protection") == 0.0
    assert executor._priority_reserve("trailing") == 0.0


def test_metrics_expose_the_risk_contour():
    executor, _, _ = breached_executor()
    executor._write_live_equity()

    metrics = executor.get_metrics()

    assert metrics["risk_breach_active"] is True
    assert metrics["risk_breach_total"] == 1
    assert metrics["risk_breach_resets_total"] == 0
    assert metrics["risk_breach_session_key"] == str(SESSION_DAY)
    assert metrics["last_equity_rub"] == 58000.0
    assert metrics["last_peak_equity_rub"] == 60000.0
    assert metrics["last_equity_session_key"] == str(SESSION_DAY)
    assert metrics["equity_snapshots_total"] == 1
    assert metrics["equity_snapshot_errors_total"] == 0
    assert metrics["equity_snapshot_enabled"] is True
    assert metrics["max_daily_loss_pct"] == 2.0
    assert metrics["max_position_size"] == 100000.0
    assert metrics["max_open_positions"] == 5


def test_metrics_before_the_first_snapshot_are_explicitly_empty():
    metrics = make_risk_executor().get_metrics()

    assert metrics["last_equity_rub"] is None
    assert metrics["last_drawdown_pct"] is None
    assert metrics["last_equity_session_key"] is None
    assert metrics["risk_breach_active"] is False
    assert metrics["risk_breach_session_key"] is None
    assert metrics["risk_gate_rejections_total"] == 0
    assert metrics["position_size_rejections_total"] == 0




class _Clock:
    """Wall + monotonic clock advanced by the executor's own sleep calls."""

    def __init__(self, wall):
        self.wall = wall
        self.mono = 0.0

    def now(self):
        return self.wall

    def clock(self):
        return self.mono

    def sleep(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


def _loop_executor(db, broker=None, wall=None):
    """Executor with every heavy collaborator stubbed, for run() tests."""
    clock = _Clock(wall or datetime(2026, 9, 28, 9, 0, 0))
    executor = make_risk_executor(
        db=db,
        broker=broker if broker is not None else RiskFakeBroker(),
        now_fn=clock.now,
        clock=clock.clock,
        sleep_fn=clock.sleep,
        check_interval_seconds=1,
        context_refresh_seconds=10**6,
    )
    executor.install_signal_handlers = lambda: None
    executor.initialize = lambda: None
    executor.evaluators = {"SBER": object()}
    executor.refresh_contexts = lambda: None
    executor.shutdown = lambda: None
    executor._active_positions = lambda: pd.DataFrame()
    return executor, clock


def test_run_snapshots_equity_before_monitoring_every_cycle():
    db = RiskFakeDB()
    broker = RiskFakeBroker(cash=Decimal("50000"), positions=[])
    executor, _ = _loop_executor(db, broker)
    order = []
    executor.monitor_positions = lambda: order.append("monitor")
    executor.process_latest_bars = lambda: order.append("bars")
    write = executor._write_live_equity
    executor._write_live_equity = lambda: (order.append("equity"), write())[1]

    executor.run(duration_minutes=0.05)

    assert order.count("equity") >= 3
    # The snapshot must precede monitoring so the gates read a fresh drawdown.
    assert order.index("equity") < order.index("monitor")
    assert len(db.equity_rows) == order.count("equity")
    assert executor.iterations_total >= 3


def test_run_keeps_going_when_every_equity_snapshot_fails():
    """A broken risk contour must not stop protection or trading."""
    db = RiskFakeDB(select_error=RuntimeError("db down"))
    executor, _ = _loop_executor(db)
    executor.monitor_positions = lambda: 0
    executor.process_latest_bars = lambda: 0

    executor.run(duration_minutes=0.03)

    assert executor.equity_snapshot_errors_total >= 1
    assert executor._consecutive_errors == 0
    assert executor.iterations_total >= 1



# --- Monitoring endpoints -----------------------------------------------------


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_equity_current_endpoint_serves_the_snapshot_and_the_limits(monkeypatch):
    from app.api import live_trading_jobs as api

    db = RiskFakeDB(equity_rows=[equity_row(risk_breach=True, drawdown_pct=3.3333)])
    monkeypatch.setattr(api, "_get_db", lambda: db)

    payload = _client().get("/api/live-trading/equity/current").json()

    assert payload["available"] is True
    assert payload["risk_breach_active"] is True
    assert payload["snapshot"]["equity_rub"] == 60000.0
    assert payload["snapshot"]["peak_equity_rub"] == 60000.0
    assert payload["snapshot"]["drawdown_pct"] == 3.3333
    assert payload["snapshot"]["session_key"] == SESSION_DAY.isoformat()
    # The panel reads the limits from the API instead of hardcoding them.
    assert payload["risk"]["max_daily_loss_pct"] == 2.0
    assert payload["risk"]["max_position_size"] == 100000.0
    assert payload["risk"]["max_open_positions"] == 5
    assert payload["risk"]["risk_breach_reset_key"] == RISK_BREACH_RESET_KEY
    assert payload["risk"]["bounds"]["max_daily_loss_pct"] == [0.0, 100.0]


def test_equity_latest_is_an_alias_of_current(monkeypatch):
    from app.api import live_trading_jobs as api

    db = RiskFakeDB(equity_rows=[equity_row()])
    monkeypatch.setattr(api, "_get_db", lambda: db)
    client = _client()

    latest = client.get("/api/live-trading/equity/latest").json()
    current = client.get("/api/live-trading/equity/current").json()

    assert latest == current


def test_equity_current_without_rows_reports_no_snapshot(monkeypatch):
    from app.api import live_trading_jobs as api

    monkeypatch.setattr(api, "_get_db", lambda: RiskFakeDB())

    payload = _client().get("/api/live-trading/equity/current").json()

    assert payload["available"] is True
    assert payload["snapshot"] is None
    assert payload["risk_breach_active"] is False


def test_session_key_is_published_as_a_plain_date(monkeypatch):
    """pandas returns a DATE column as a Timestamp; the contract stays a date."""
    from app.api import live_trading_jobs as api

    row = equity_row()
    row["session_key"] = pd.Timestamp(SESSION_DAY)
    monkeypatch.setattr(api, "_get_db", lambda: RiskFakeDB(equity_rows=[row]))
    client = _client()

    current = client.get("/api/live-trading/equity/current").json()
    history = client.get("/api/live-trading/equity/history").json()

    assert current["snapshot"]["session_key"] == SESSION_DAY.isoformat()
    assert history["items"][0]["session_key"] == SESSION_DAY.isoformat()


def test_equity_history_filters_by_session_and_paginates(monkeypatch):
    from app.api import live_trading_jobs as api

    rows = [
        equity_row(id=1, session_key=SESSION_DAY, timestamp=SESSION_NOW),
        equity_row(
            id=2,
            session_key=SESSION_DAY - timedelta(days=1),
            timestamp=SESSION_NOW - timedelta(days=1),
        ),
    ]
    db = RiskFakeDB(equity_rows=rows)
    monkeypatch.setattr(api, "_get_db", lambda: db)

    response = _client().get(
        "/api/live-trading/equity/history",
        params={"session_key": SESSION_DAY.isoformat(), "limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 10
    assert payload["offset"] == 0
    assert payload["risk"]["max_daily_loss_pct"] == 2.0
    assert isinstance(payload["items"], list)
    # The fake does not filter rows; the contract that matters is the WHERE clause.
    where = [query for query, _ in db.select_calls if "COUNT(*)" in query][0]
    assert "session_key = %(session_key)s" in where


def test_equity_history_accepts_a_date_range(monkeypatch):
    from app.api import live_trading_jobs as api

    db = RiskFakeDB(equity_rows=[equity_row()])
    monkeypatch.setattr(api, "_get_db", lambda: db)

    response = _client().get(
        "/api/live-trading/equity/history",
        params={
            "date_from": (SESSION_DAY - timedelta(days=7)).isoformat(),
            "date_to": SESSION_DAY.isoformat(),
        },
    )

    assert response.status_code == 200
    where = [query for query, _ in db.select_calls if "COUNT(*)" in query][0]
    assert "session_key >= %(date_from)s" in where
    assert "session_key <= %(date_to)s" in where


def test_equity_endpoints_report_an_unmigrated_database(monkeypatch):
    from app.api import live_trading_jobs as api

    monkeypatch.setattr(api, "_get_db", lambda: RiskFakeDB(live_equity_missing=True))
    client = _client()

    for path in ("/api/live-trading/equity/current", "/api/live-trading/equity/history"):
        response = client.get(path)
        assert response.status_code == 503, path
        assert "alembic upgrade head" in response.json()["detail"]


def test_equity_endpoints_are_read_only():
    """The monitoring surface must not be able to mutate the risk state."""
    from app.main import app

    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if "/api/live-trading/equity" in path:
            assert getattr(route, "methods", set()) == {"GET"}, path
            checked += 1
    assert checked == 3

