"""Issue #178 / Epic #172 block F: the GLOBAL live-trading kill switch.

Three layers are covered, in the order an operator meets them:

* the contract - migration ``20260928_001`` and the runtime DDL both seed
  ``trading.app_settings.live_kill_switch``;
* the executor - the entry gate, the fail-safe read (decision D2), the
  transition alerts and the published metrics;
* the API - ``POST /api/live-trading/kill-switch`` and the
  ``global_kill_switch`` section of ``GET /api/live-trading/metrics``.

The red lines are asserted explicitly: an engaged switch rejects entries and
never flattens a position, never cancels a broker stop, and never touches the
paper contour.
"""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from app.analytics.live_executor import KILL_SWITCH_SKIP_REASON
from app.analytics.live_schema import (
    LIVE_KILL_SWITCH_KEY,
    LIVE_SCHEMA_STATEMENTS,
    REQUIRED_APP_SETTINGS_KEYS,
)
from app.analytics.trading_config import LIVE_TRADING
from app.api import live_trading_jobs as api
from tests.test_live_executor import FakeBroker, FakeDB, Result, make_executor


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260928_001_live_trading_kill_switch.py"
)


def load_migration():
    """Import the migration file the way alembic would (no package needed)."""
    spec = importlib.util.spec_from_file_location(
        "migration_20260928_001_live_trading_kill_switch", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingOp:
    """``alembic.op`` stand-in capturing the emitted SQL."""

    def __init__(self):
        self.statements = []

    def execute(self, sql):
        self.statements.append(" ".join(str(sql).split()))


def buy_decision():
    return {"action": "enter", "entry_price": 100.0, "stop": 95.0, "take": 110.0}


def executor_with_switch(value, *, db=None, broker=None, **config):
    """Executor whose ``trading.app_settings`` serves one kill-switch value.

    ``value=None`` removes the row entirely - the unmigrated database case.
    """
    settings = {} if value is None else {"live_kill_switch": value}
    database = db if db is not None else FakeDB(
        app_settings=settings,
        seeded_kill_switch=None,
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
    return make_executor(
        db=database,
        broker=broker if broker is not None else FakeBroker(),
        **config,
    )


# --- the contract: migration and runtime DDL ----------------------------------


def test_the_switch_key_is_part_of_the_schema_contract():
    assert LIVE_KILL_SWITCH_KEY == "live_kill_switch"
    assert LIVE_KILL_SWITCH_KEY in REQUIRED_APP_SETTINGS_KEYS


def test_the_runtime_ddl_seeds_the_switch_to_false():
    joined = "\n".join(LIVE_SCHEMA_STATEMENTS)

    assert f"('{LIVE_KILL_SWITCH_KEY}', 'false'::jsonb" in joined
    assert "ON CONFLICT (key) DO NOTHING" in joined
    # The runtime DDL must never drop what an operator set.
    assert "DELETE FROM trading.app_settings" not in joined


def test_the_in_memory_default_is_the_fail_safe_value():
    """Before the first DB read the executor must not be allowed to enter."""
    assert LIVE_TRADING["live_kill_switch"] is True


def test_the_migration_chains_onto_the_previous_head():
    migration = load_migration()

    assert migration.revision == "20260928_001"
    assert migration.down_revision == "20260927_001"


def test_the_migration_seeds_the_switch_idempotently(monkeypatch):
    migration = load_migration()
    recorder = RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()

    assert len(recorder.statements) == 1
    statement = recorder.statements[0]
    assert "INSERT INTO trading.app_settings" in statement
    assert f"'{LIVE_KILL_SWITCH_KEY}'" in statement
    assert "'false'::jsonb" in statement
    assert "ON CONFLICT (key) DO NOTHING" in statement


def test_the_migration_rollback_removes_only_its_own_key(monkeypatch):
    """Documented rollback: deleting the row re-engages the fail-safe, it does
    not re-enable trading."""
    migration = load_migration()
    recorder = RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)

    migration.downgrade()

    assert len(recorder.statements) == 1
    statement = recorder.statements[0]
    assert statement.startswith("DELETE FROM trading.app_settings")
    assert f"key = '{LIVE_KILL_SWITCH_KEY}'" in statement
    assert "DROP TABLE" not in statement


# --- the executor: the fail-safe read -----------------------------------------


def _boom(*_args, **_kwargs):
    raise RuntimeError("db down")


def test_a_stored_false_lets_the_contour_trade():
    executor = executor_with_switch(False)

    executor._read_live_kill_switch()

    assert executor.config["live_kill_switch"] is False
    assert executor._live_kill_switch_source == "app_settings"


def test_a_stored_true_blocks_the_contour():
    executor = executor_with_switch(True)

    executor._read_live_kill_switch()

    assert executor.config["live_kill_switch"] is True
    assert executor._live_kill_switch_source == "app_settings"


def test_a_missing_row_fails_safe_to_blocked():
    """Decision D2: no row means the emergency stop cannot be read -> ON."""
    executor = executor_with_switch(None)

    executor._read_live_kill_switch()

    assert executor.config["live_kill_switch"] is True
    assert executor._live_kill_switch_source == "app_settings:missing_key"


def test_a_null_value_fails_safe_to_blocked():
    db = FakeDB(app_settings={"live_kill_switch": None}, seeded_kill_switch=None)
    executor = make_executor(db=db)

    executor._read_live_kill_switch()

    assert executor.config["live_kill_switch"] is True
    assert executor._live_kill_switch_source == "app_settings:null_value"


def test_an_unreadable_settings_table_fails_safe_to_blocked():
    db = FakeDB(app_settings={"live_kill_switch": False})
    db.select = _boom
    executor = make_executor(db=db)

    executor._read_live_kill_switch()

    assert executor.config["live_kill_switch"] is True
    assert executor._live_kill_switch_source == "db_error:RuntimeError"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("false", False),
        ("off", False),
        ("on", True),
    ],
)
def test_the_stored_value_is_coerced_like_every_other_switch(stored, expected):
    """JSONB comes back as a bool or as text depending on the driver path."""
    executor = executor_with_switch(stored)

    executor._read_live_kill_switch()

    assert executor.config["live_kill_switch"] is expected
    assert executor._live_kill_switch_source == "app_settings"


# --- the executor: the entry gate ---------------------------------------------


def test_an_engaged_switch_rejects_an_entry_before_any_broker_call():
    broker = FakeBroker()
    executor = executor_with_switch(True, broker=broker)
    executor._read_live_kill_switch()

    result = executor.process_signal("SBER", buy_decision(), imbalance=2.0)

    assert result == {"executed": False, "reason": KILL_SWITCH_SKIP_REASON}
    assert executor.kill_switch_rejections_total == 1
    # Not one API call: a stopped contour costs nothing and cannot leak an order.
    assert broker.calls == []


def test_the_gate_precedes_the_session_window_and_the_risk_gates():
    """The switch is the first business gate, so nothing else is consulted."""
    broker = FakeBroker()
    executor = executor_with_switch(True, broker=broker)
    executor._read_live_kill_switch()
    executor._risk_gate = _boom  # would raise if the gate ran after the risk one
    executor._latest_orderbook = _boom

    result = executor.process_signal("SBER", buy_decision())

    assert result["reason"] == KILL_SWITCH_SKIP_REASON


def test_a_disengaged_switch_lets_the_entry_through():
    broker = FakeBroker()
    executor = executor_with_switch(False, broker=broker)
    executor._read_live_kill_switch()

    result = executor.process_signal("SBER", buy_decision(), imbalance=2.0)

    assert result.get("reason") != KILL_SWITCH_SKIP_REASON
    assert executor.kill_switch_rejections_total == 0
    assert ("check_balance", {}) in broker.calls


def test_a_non_buy_signal_keeps_its_own_reason():
    """The switch must not mask the reason a signal was never an entry."""
    executor = executor_with_switch(True)
    executor._read_live_kill_switch()

    result = executor.process_signal("SBER", {"action": "hold"})

    assert result["reason"] == "not_buy_signal"
    assert executor.kill_switch_rejections_total == 0


def test_an_engaged_switch_never_touches_open_positions():
    """Red line: no flatten, no stop cancellation - protection stays armed."""
    from tests.test_live_alerting import FakeNotifier, make_hook_executor

    notifier = FakeNotifier()
    db = FakeDB(app_settings={"live_kill_switch": True}, seeded_kill_switch=None)
    broker = FakeBroker()
    executor = make_hook_executor(notifier=notifier, db=db, broker=broker)
    executor.config["live_kill_switch"] = False

    executor._refresh_kill_switch()

    assert executor.config["live_kill_switch"] is True
    assert broker.calls == []


# --- the executor: alerts and metrics -----------------------------------------


def test_engaging_the_switch_is_a_critical_one_shot_alert():
    from tests.test_live_alerting import FakeNotifier, make_hook_executor

    notifier = FakeNotifier()
    # The trailing reader selects without a key filter, so the row order in the
    # fake decides what it sees: trailing first and off, global second and on.
    db = FakeDB(
        app_settings={"trailing_kill_switch": False, "live_kill_switch": True},
        seeded_kill_switch=None,
    )
    executor = make_hook_executor(notifier=notifier, db=db)
    executor.config["live_kill_switch"] = False

    executor._refresh_kill_switch()

    assert notifier.messages == [
        "🚨 *Глобальный kill switch ВКЛЮЧЁН*\n"
        "*Уровень:* `critical`\n"
        "*Эффект:* `новые входы отклоняются, открытые позиции и их стопы сохранены`\n"
        "*Источник:* `app\\_settings`"
    ]
    # Decision D5: the monitoring snapshot follows the alert immediately.
    assert executor.metrics_flushes_total == 1


def test_releasing_the_switch_is_informational():
    from tests.test_live_alerting import FakeNotifier, make_hook_executor

    notifier = FakeNotifier()
    db = FakeDB(app_settings={"live_kill_switch": False}, seeded_kill_switch=None)
    executor = make_hook_executor(notifier=notifier, db=db)
    executor.config["live_kill_switch"] = True

    executor._refresh_kill_switch()

    assert notifier.messages[-1].startswith("ℹ️ *Глобальный kill switch ВЫКЛЮЧЕН*")
    assert "*Эффект:* `новые входы снова разрешены`" in notifier.messages[-1]
    assert "*Уровень:* `critical`" not in notifier.messages[-1]


def test_a_steady_switch_does_not_spam():
    from tests.test_live_alerting import FakeNotifier, make_hook_executor

    notifier = FakeNotifier()
    db = FakeDB(
        app_settings={"trailing_kill_switch": False, "live_kill_switch": True},
        seeded_kill_switch=None,
    )
    executor = make_hook_executor(notifier=notifier, db=db)
    executor.config["live_kill_switch"] = True

    for _ in range(5):
        executor._refresh_kill_switch()

    assert notifier.messages == []


def test_a_broken_read_alerts_both_switches_with_their_sources():
    """One root cause, two effects: entries stop AND trailing stops ratcheting."""
    from tests.test_live_alerting import FakeNotifier, make_hook_executor

    notifier = FakeNotifier()
    db = FakeDB()
    db.select = _boom
    executor = make_hook_executor(notifier=notifier, db=db)

    executor._refresh_kill_switch()

    assert executor.config["trailing_kill_switch"] is True
    assert executor.config["live_kill_switch"] is True
    # The trailing alert keeps its historical position in the stream.
    assert notifier.messages[0].startswith("🚨 *Kill switch ВКЛЮЧЁН*")
    assert notifier.messages[1].startswith("🚨 *Глобальный kill switch ВКЛЮЧЁН*")
    assert "*Источник:* `db\\_error:RuntimeError`" in notifier.messages[1]


def test_the_switch_state_is_published_in_the_metrics():
    executor = executor_with_switch(True)
    executor._read_live_kill_switch()
    executor.kill_switch_rejections_total = 3

    metrics = executor.get_metrics()

    assert metrics["live_kill_switch"] is True
    assert metrics["live_kill_switch_source"] == "app_settings"
    assert metrics["kill_switch_rejections_total"] == 3


def test_the_persisted_snapshot_carries_the_switch():
    executor = executor_with_switch(True)
    executor._read_live_kill_switch()

    payload = executor._metrics_payload()

    assert payload["live_kill_switch"] is True
    assert payload["live_kill_switch_source"] == "app_settings"
    assert payload["kill_switch_rejections_total"] == 0
    # The trailing switch keeps its own, separate fields.
    assert payload["kill_switch"] is False
    assert set(payload) <= set(api._METRICS_SNAPSHOT_FIELDS)


# --- the monitoring API: reading the switch -----------------------------------


def test_metrics_publish_the_global_switch(monkeypatch):
    from tests.test_live_alerting import metrics_db, metrics_snapshot, read_metrics

    db = metrics_db(metrics_snapshot(), global_kill_switch=True)

    payload = read_metrics(db, monkeypatch)

    block = payload["global_kill_switch"]
    assert block["active"] is True
    assert block["found"] is True
    assert block["reason"] is None
    assert block["key"] == LIVE_KILL_SWITCH_KEY
    assert block["snapshot_active"] is False
    assert block["rejections_total"] == 0
    # One word for the panel: an operator lever is stopping the contour.
    assert payload["state"] == "kill_switch"
    # ... and the trailing switch is reported independently.
    assert payload["kill_switch"]["active"] is False


def test_a_missing_row_is_reported_as_active(monkeypatch):
    """The panel must show what the executor does, not what the database lacks."""
    from tests.test_live_alerting import metrics_db, metrics_snapshot, read_metrics

    db = metrics_db(metrics_snapshot(), global_kill_switch=None)

    payload = read_metrics(db, monkeypatch)

    block = payload["global_kill_switch"]
    assert block["active"] is True
    assert block["found"] is False
    assert block["reason"] == "missing_row"
    assert block["live_active"] is None
    assert payload["state"] == "kill_switch"


def test_an_unreadable_row_fails_safe_in_the_api_too(monkeypatch):
    """A broken switch read must not read as "entries allowed" in the panel."""
    from tests.test_live_alerting import metrics_snapshot, read_metrics

    class UnreadableSwitchDB(FakeDB):
        def select(self, query, params=None):
            if params and tuple(params)[0] == LIVE_KILL_SWITCH_KEY:
                raise RuntimeError("settings down")
            return super().select(query, params)

    db = UnreadableSwitchDB(
        app_settings={
            "live_executor_metrics": metrics_snapshot(),
            "trailing_kill_switch": False,
        },
        seeded_kill_switch=None,
    )

    payload = read_metrics(db, monkeypatch)

    block = payload["global_kill_switch"]
    assert block["active"] is True
    assert block["reason"] == "unreadable_row"
    assert "settings down" in block["error"]
    assert payload["state"] == "kill_switch"


def test_a_snapshot_without_the_field_is_not_read_as_allowed(monkeypatch):
    """An executor built before #178 publishes no field: unknown != allowed."""
    from tests.test_live_alerting import metrics_db, read_metrics

    snapshot = {"schema_version": 1, "heartbeat_ts": None}
    db = metrics_db(snapshot, global_kill_switch=False)

    payload = read_metrics(db, monkeypatch)

    assert payload["global_kill_switch"]["snapshot_active"] is True
    # The live row still wins when it is readable.
    assert payload["global_kill_switch"]["active"] is False


# --- the monitoring API: writing the switch -----------------------------------


class KillSwitchDB(FakeDB):
    """FakeDB that applies the upsert, so the read-back can confirm the write."""

    def __init__(self, *, write_error=None, **kwargs):
        super().__init__(**kwargs)
        self.write_error = write_error
        self.writes = []

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        if "INSERT INTO trading.app_settings" in normalized:
            self.writes.append((normalized, params))
            if self.write_error is not None:
                raise self.write_error
            key, raw = params
            self.app_settings[key] = raw == "true"
            self.seeded_kill_switch = None  # the row exists now
            return 1
        return super().execute(query, params)


def set_switch(db, monkeypatch, enabled, reason=None):
    monkeypatch.setattr(api, "_get_db", lambda: db)
    return api._set_kill_switch_payload(enabled, reason)


def test_the_write_is_an_upsert_confirmed_by_a_read_back(monkeypatch):
    db = KillSwitchDB(app_settings={}, seeded_kill_switch=None)

    payload = set_switch(db, monkeypatch, True, reason="аномальная волатильность")

    assert payload["ok"] is True
    assert payload["enabled"] is True
    assert payload["confirmed"] is True
    assert payload["reason"] == "аномальная волатильность"
    assert payload["kill_switch"]["active"] is True
    assert "open positions keep their broker stops" in payload["effect"]
    statement, params = db.writes[0]
    assert params == (LIVE_KILL_SWITCH_KEY, "true")
    assert "ON CONFLICT (key)" in statement
    assert "DO UPDATE SET value = EXCLUDED.value" in statement


def test_releasing_the_switch_is_confirmed_too(monkeypatch):
    db = KillSwitchDB(
        app_settings={"live_kill_switch": True}, seeded_kill_switch=None
    )

    payload = set_switch(db, monkeypatch, False)

    assert payload["ok"] is True
    assert payload["kill_switch"]["active"] is False
    assert payload["reason"] is None
    assert payload["effect"] == "new entries are allowed again"
    assert db.writes[0][1] == (LIVE_KILL_SWITCH_KEY, "false")


def test_an_unwritable_settings_table_answers_503(monkeypatch):
    """An emergency stop that cannot be confirmed must report an error."""
    from fastapi import HTTPException

    db = KillSwitchDB(write_error=RuntimeError("read-only replica"))

    with pytest.raises(HTTPException) as excinfo:
        set_switch(db, monkeypatch, True)

    assert excinfo.value.status_code == 503
    assert "20260928_001_live_trading_kill_switch" in excinfo.value.detail
    assert "NOT changed" in excinfo.value.detail


def test_an_unconfirmed_write_is_not_reported_as_success(monkeypatch):
    """A write the read-back cannot see is a failure, not a success."""
    db = KillSwitchDB(app_settings={}, seeded_kill_switch=None)

    def swallow(query, params=None):
        db.writes.append((query, params))
        return 0  # accepted, but nothing is stored

    db.execute = swallow

    payload = set_switch(db, monkeypatch, True)

    assert payload["ok"] is False
    assert payload["confirmed"] is False
    # Fail-safe reporting: "not confirmed" must never read as "entries allowed".
    assert payload["kill_switch"]["active"] is True
    assert payload["kill_switch"]["reason"] == "missing_row"


def test_the_audit_note_is_truncated_and_logged(monkeypatch, caplog):
    import logging

    db = KillSwitchDB(app_settings={}, seeded_kill_switch=None)

    caplog.set_level(logging.INFO)
    payload = set_switch(db, monkeypatch, True, reason="x" * 500)

    assert len(payload["reason"]) == api.KILL_SWITCH_REASON_MAX
    assert "Global live kill switch set to ON" in caplog.text


def test_the_route_is_registered_as_a_post():
    from fastapi import FastAPI

    app = FastAPI()
    api.register_routes(app)
    routes = {
        route.path: route.methods
        for route in app.routes
        if getattr(route, "methods", None)
    }

    assert routes.get("/api/live-trading/kill-switch") == {"POST"}
    # The read side stays a GET on its own path.
    assert routes.get("/api/live-trading/metrics") == {"GET"}


def test_the_http_endpoint_round_trips(monkeypatch):
    from tests.test_live_alerting import metrics_client

    db = KillSwitchDB(app_settings={}, seeded_kill_switch=None)
    monkeypatch.setattr(api, "_get_db", lambda: db)

    response = metrics_client().post(
        "/api/live-trading/kill-switch",
        json={"enabled": True, "reason": "smoke"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["kill_switch"]["active"] is True
    assert db.app_settings[LIVE_KILL_SWITCH_KEY] is True


def test_the_http_endpoint_rejects_a_missing_flag():
    from tests.test_live_alerting import metrics_client

    response = metrics_client().post("/api/live-trading/kill-switch", json={})

    assert response.status_code == 422
