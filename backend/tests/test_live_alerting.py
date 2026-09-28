"""Issue #177 / Epic #172 block E: live Telegram heartbeat and monitoring.

Covers the alerting contour added by #177:

* ``get_live_alerting_config()`` defaults, env overrides and range validation;
* the ``app_settings`` contract for the persisted metrics snapshot
  (``trading.app_settings['live_executor_metrics']``, decision D1);
* the ``LiveExecutor._notify()`` wrapper: no-op without a notifier, never
  raises out of the trading loop, dedups per event key (decision D2);
* the event hooks (start/stop, entry, exit slippage, protection failure,
  invariant violation, OCO orphan risk, equity breach/clear, kill switch,
  consecutive-error threshold);
* the throttled metrics flush into ``trading.app_settings`` (decision D5).
"""

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.analytics.live_executor import (
    MAX_CONSECUTIVE_ERRORS,
    LiveExecutor,
    _alert_text,
    build_default_notifier,
    run_live_executor,
)
from app.analytics.trading_config import (
    LIVE_ALERTING,
    LIVE_ALERTING_BOUNDS,
    LIVE_ALERTING_ENV,
    get_live_alerting_bounds,
    get_live_alerting_config,
    validate_live_alerting_values,
)
from app.broker.tinkoff_sandbox import SandboxAPIError
from app.core.config_manager import Settings, TelegramConfig
from tests.test_live_executor import (
    FakeBroker,
    FakeDB,
    IN_SESSION_NOW,
    Result,
    StopFakeBroker,
    active_position,
    make_executor,
    stop_item,
)

# --- Alerting configuration ---------------------------------------------------


def test_live_alerting_defaults_match_the_shipped_policy():
    config = get_live_alerting_config()

    assert config["heartbeat_interval_seconds"] == 3600
    assert config["heartbeat_stale_seconds"] == 300
    assert config["alert_debounce_seconds"] == 300
    assert config["slippage_alert_bp"] == 50.0
    assert config["max_consecutive_errors"] == 5
    assert config["metrics_flush_seconds"] == 300
    assert config["metrics_key"] == "live_executor_metrics"
    assert config["telegram_alerts_enabled"] is True
    for key, default in LIVE_ALERTING.items():
        assert config[key] == default, key


def test_live_alerting_config_returns_an_isolated_copy():
    first = get_live_alerting_config()
    first["heartbeat_stale_seconds"] = 1
    first["metrics_key"] = "mutated"

    second = get_live_alerting_config()

    assert second["heartbeat_stale_seconds"] == LIVE_ALERTING["heartbeat_stale_seconds"]
    assert second["metrics_key"] == LIVE_ALERTING["metrics_key"]
    assert LIVE_ALERTING["heartbeat_stale_seconds"] != 1


def test_every_numeric_alerting_key_has_a_validated_bound():
    bounds = get_live_alerting_bounds()

    for key, default in LIVE_ALERTING.items():
        if isinstance(default, bool) or isinstance(default, str):
            assert key not in bounds, key
            continue
        assert key in bounds, key
        low, high = bounds[key]
        assert low <= default <= high, key
    assert bounds == LIVE_ALERTING_BOUNDS


def test_every_bounded_alerting_key_is_env_overridable():
    assert set(LIVE_ALERTING_BOUNDS) == set(LIVE_ALERTING_ENV.values())


def test_alerting_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("LIVE_HEARTBEAT_INTERVAL_SECONDS", "900")
    monkeypatch.setenv("LIVE_HEARTBEAT_STALE_SECONDS", "60")
    monkeypatch.setenv("LIVE_ALERT_DEBOUNCE_SECONDS", "0")
    monkeypatch.setenv("LIVE_SLIPPAGE_ALERT_BP", "12.5")
    monkeypatch.setenv("LIVE_MAX_CONSECUTIVE_ERRORS", "2")
    monkeypatch.setenv("LIVE_METRICS_FLUSH_SECONDS", "45")
    monkeypatch.setenv("LIVE_TELEGRAM_ALERTS", "off")

    config = get_live_alerting_config()

    assert config["heartbeat_interval_seconds"] == 900
    assert config["heartbeat_stale_seconds"] == 60
    assert config["alert_debounce_seconds"] == 0
    assert config["slippage_alert_bp"] == 12.5
    assert config["max_consecutive_errors"] == 2
    assert config["metrics_flush_seconds"] == 45
    assert config["telegram_alerts_enabled"] is False


def test_blank_alerting_env_overrides_fall_back_to_defaults(monkeypatch):
    for env_name in LIVE_ALERTING_ENV:
        monkeypatch.setenv(env_name, "   ")
    monkeypatch.setenv("LIVE_TELEGRAM_ALERTS", "")

    config = get_live_alerting_config()

    for key, default in LIVE_ALERTING.items():
        assert config[key] == default, key


@pytest.mark.parametrize(
    "env_name,value",
    [
        ("LIVE_HEARTBEAT_INTERVAL_SECONDS", "0"),
        ("LIVE_HEARTBEAT_INTERVAL_SECONDS", "-30"),
        ("LIVE_HEARTBEAT_INTERVAL_SECONDS", "86401"),
        ("LIVE_HEARTBEAT_INTERVAL_SECONDS", "soon"),
        ("LIVE_HEARTBEAT_STALE_SECONDS", "0"),
        ("LIVE_HEARTBEAT_STALE_SECONDS", "nan"),
        ("LIVE_ALERT_DEBOUNCE_SECONDS", "-1"),
        ("LIVE_ALERT_DEBOUNCE_SECONDS", "1e6"),
        ("LIVE_SLIPPAGE_ALERT_BP", "0"),
        ("LIVE_SLIPPAGE_ALERT_BP", "10001"),
        ("LIVE_SLIPPAGE_ALERT_BP", "tiny"),
        ("LIVE_MAX_CONSECUTIVE_ERRORS", "0"),
        ("LIVE_MAX_CONSECUTIVE_ERRORS", "101"),
        ("LIVE_MAX_CONSECUTIVE_ERRORS", "many"),
        ("LIVE_METRICS_FLUSH_SECONDS", "0"),
        ("LIVE_METRICS_FLUSH_SECONDS", "86401"),
    ],
)
def test_out_of_range_alerting_env_overrides_fail_fast(monkeypatch, env_name, value):
    monkeypatch.setenv(env_name, value)

    with pytest.raises(ValueError, match=env_name):
        get_live_alerting_config()


def test_staleness_window_is_shorter_than_the_heartbeat_cadence():
    """A healthy loop must never be reported stale by the default policy."""
    config = get_live_alerting_config()

    assert config["heartbeat_stale_seconds"] < config["heartbeat_interval_seconds"]


def test_metrics_key_fits_the_app_settings_column():
    """``trading.app_settings.key`` is VARCHAR(64) - the contract must fit."""
    assert len(get_live_alerting_config()["metrics_key"]) <= 64


def test_alerting_config_is_json_serialisable():
    """The persisted metrics snapshot embeds config values as JSONB."""
    config = get_live_alerting_config()

    payload = json.loads(json.dumps({"config": config}, default=str))

    assert payload["config"]["metrics_key"] == "live_executor_metrics"
    assert payload["config"]["heartbeat_stale_seconds"] == 300


def test_master_switch_is_the_only_boolean_key():
    booleans = [key for key, value in LIVE_ALERTING.items() if isinstance(value, bool)]

    assert booleans == ["telegram_alerts_enabled"]
    assert "LIVE_TELEGRAM_ALERTS" not in LIVE_ALERTING_ENV


# --- LiveExecutor._notify wrapper (decision D1) ---------------------------------


class _Stub:
    """Inert stand-in so LiveExecutor never builds a real DB/broker client."""


class FakeClock:
    """Monotonic clock the tests can move by hand."""

    def __init__(self, start: float = 1000.0):
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeNotifier:
    """Stand-in for TelegramNotifier: records text, can fail on demand."""

    def __init__(self, *, enabled=True, result=True, raise_exc=None):
        self.enabled = enabled
        self.result = result
        self.raise_exc = raise_exc
        self.messages: list[str] = []

    def send_message(self, text):
        self.messages.append(text)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


def make_alert_executor(*, notifier=None, clock=None, **alerting):
    """Executor for the alerting layer: it touches neither db nor broker."""
    kwargs = {"db": _Stub(), "broker": _Stub(), "notifier": notifier}
    if clock is not None:
        kwargs["clock"] = clock
    if alerting:
        kwargs["alerting"] = dict(get_live_alerting_config(), **alerting)
    return LiveExecutor(**kwargs)


class TestNotifyWrapper:
    """The wrapper must be safe, throttled and observable (decision D1)."""

    def test_notify_is_a_noop_without_a_notifier(self):
        """Log-only contour: missing credentials must not break the loop."""
        executor = make_alert_executor()

        assert executor._notify("entry", "Вход", [("Тикер", "SBER")]) is False
        assert executor.alerts_attempted_total == 1
        assert executor.alerts_skipped_total == 1
        assert executor.alerts_sent_total == 0

    def test_notify_sends_through_the_injected_notifier(self):
        notifier = FakeNotifier()
        executor = make_alert_executor(notifier=notifier)

        assert executor._notify("entry", "Вход", [("Тикер", "SBER")]) is True
        assert notifier.messages == ["ℹ️ *Вход*\n*Тикер:* `SBER`"]
        assert executor.alerts_sent_total == 1
        assert executor.alerts_failed_total == 0

    def test_notify_never_raises_when_the_notifier_explodes(self):
        """A Telegram outage must not stop stop/take protection."""
        notifier = FakeNotifier(raise_exc=RuntimeError("boom"))
        executor = make_alert_executor(notifier=notifier)

        assert executor._notify("exit", "Выход", critical=True) is False
        assert executor.alerts_failed_total == 1
        assert executor.alerts_sent_total == 0

    def test_notify_counts_a_rejected_delivery_as_failed(self):
        notifier = FakeNotifier(result=False)
        executor = make_alert_executor(notifier=notifier)

        assert executor._notify("exit", "Выход") is False
        assert executor.alerts_failed_total == 1
        assert executor.alerts_suppressed_total == 0

    def test_notify_is_silenced_by_telegram_alerts_enabled_false(self):
        notifier = FakeNotifier()
        executor = make_alert_executor(
            notifier=notifier, telegram_alerts_enabled=False
        )

        assert executor._notify("entry", "Вход") is False
        assert notifier.messages == []
        assert executor.alerts_skipped_total == 1

    def test_notify_skips_a_notifier_without_credentials(self):
        notifier = FakeNotifier(enabled=False)
        executor = make_alert_executor(notifier=notifier)

        assert executor._notify("entry", "Вход") is False
        assert notifier.messages == []
        assert executor.alerts_skipped_total == 1

    def test_critical_alerts_are_marked(self):
        notifier = FakeNotifier()
        executor = make_alert_executor(notifier=notifier)

        executor._notify("equity", "Просадка", [("Equity", 900.0)], critical=True)

        assert notifier.messages[0].startswith("🚨 *Просадка*")
        assert "*Уровень:* `critical`" in notifier.messages[0]

    def test_debounce_suppresses_repeats_inside_the_window(self):
        """Decision D2: one message per event per alert_debounce_seconds."""
        clock = FakeClock(start=1000.0)
        notifier = FakeNotifier()
        executor = make_alert_executor(notifier=notifier, clock=clock)

        assert executor._notify("equity", "Просадка", dedupe=True) is True
        clock.advance(299.0)
        assert executor._notify("equity", "Просадка", dedupe=True) is False
        assert executor.alerts_suppressed_total == 1
        clock.advance(1.0)
        assert executor._notify("equity", "Просадка", dedupe=True) is True
        assert len(notifier.messages) == 2

    def test_debounce_is_scoped_to_the_event_key(self):
        clock = FakeClock(start=5000.0)
        notifier = FakeNotifier()
        executor = make_alert_executor(notifier=notifier, clock=clock)

        executor._notify("equity", "Просадка", dedupe=True)

        assert executor._notify("stale", "Свежесть данных", dedupe=True) is True
        assert len(notifier.messages) == 2
        assert executor.alerts_suppressed_total == 0

    def test_debounce_zero_sends_every_repetition(self):
        """0 is the documented escape hatch for must-not-miss criticals."""
        notifier = FakeNotifier()
        executor = make_alert_executor(notifier=notifier, alert_debounce_seconds=0)

        executor._notify("kill", "Kill switch", dedupe=True)
        executor._notify("kill", "Kill switch", dedupe=True)

        assert len(notifier.messages) == 2
        assert executor.alerts_suppressed_total == 0

    def test_one_shot_events_are_never_debounced(self):
        notifier = FakeNotifier()
        executor = make_alert_executor(notifier=notifier)

        executor._notify("entry", "Вход", [("Тикер", "SBER")])
        executor._notify("entry", "Вход", [("Тикер", "GAZP")])

        assert len(notifier.messages) == 2
        assert executor.alerts_suppressed_total == 0

    def test_alert_counters_are_exposed_for_diagnostics(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_alert_executor(notifier=notifier, clock=clock)

        executor._notify("equity", "Просадка", dedupe=True)
        executor._notify("equity", "Просадка", dedupe=True)
        executor._notify("entry", "Вход")

        assert executor.alerts_attempted_total == 3
        assert executor.alerts_sent_total == 2
        assert executor.alerts_suppressed_total == 1
        assert executor._alert_last_sent["equity"] == clock.value


class TestAlertText:
    """Every dynamic value must survive Telegram's legacy Markdown parser."""

    def test_dynamic_content_is_escaped(self):
        text = _alert_text(
            "🚨",
            "Ошибка_исполнения",
            [("Тикер", "SBER*"), ("Причина", "broker_error[1]")],
            critical=True,
        )

        assert "Ошибка\\_исполнения" in text
        assert "`SBER\\*`" in text
        # ']' is not a Markdown control character for the legacy parser.
        assert "broker\\_error\\[1]" in text

    def test_none_values_are_dropped(self):
        text = _alert_text("ℹ️", "Вход", [("Тикер", "SBER"), ("Стакан", None)])

        assert text == "ℹ️ *Вход*\n*Тикер:* `SBER`"


class TestAlertingOverrides:
    """An in-memory override obeys the same bounds as .env (decision D3)."""

    def test_max_consecutive_errors_comes_from_the_alerting_config(self):
        assert make_alert_executor()._max_consecutive_errors == 5
        assert (
            make_alert_executor()._max_consecutive_errors == MAX_CONSECUTIVE_ERRORS
        )
        assert (
            make_alert_executor(max_consecutive_errors=2)._max_consecutive_errors
            == 2
        )

    @pytest.mark.parametrize(
        "key,value",
        [
            ("alert_debounce_seconds", -1),
            ("alert_debounce_seconds", 86401),
            ("max_consecutive_errors", 0),
            ("max_consecutive_errors", 101),
            ("slippage_alert_bp", 0),
            ("slippage_alert_bp", 10001),
            ("metrics_flush_seconds", 0),
            ("heartbeat_interval_seconds", 0),
            ("heartbeat_stale_seconds", 86401),
        ],
    )
    def test_out_of_range_overrides_fail_fast(self, key, value):
        with pytest.raises(ValueError) as info:
            make_alert_executor(**{key: value})

        assert key in str(info.value)

    def test_non_boolean_alerts_flag_fails_fast(self):
        with pytest.raises(ValueError, match="telegram_alerts_enabled"):
            make_alert_executor(telegram_alerts_enabled="yes")

    def test_non_numeric_override_fails_fast(self):
        with pytest.raises(ValueError, match="slippage_alert_bp"):
            make_alert_executor(slippage_alert_bp="fast")

    def test_partial_override_keeps_the_remaining_defaults(self):
        executor = make_alert_executor(max_consecutive_errors=3)

        assert executor.alerting["heartbeat_interval_seconds"] == 3600
        assert executor.alerting["metrics_key"] == "live_executor_metrics"
        assert executor.alerting["max_consecutive_errors"] == 3


class TestBuildDefaultNotifier:
    """Decision D4: credentials come from config_manager, failures are soft."""

    def test_returns_none_without_credentials(self, monkeypatch):
        monkeypatch.setattr(
            "app.analytics.live_executor.load_settings",
            lambda: Settings(telegram=TelegramConfig()),
        )

        assert build_default_notifier() is None

    def test_reads_the_configured_credentials(self, monkeypatch):
        monkeypatch.setattr(
            "app.analytics.live_executor.load_settings",
            lambda: Settings(
                telegram=TelegramConfig(token="tok-177", chat_id="chat-177")
            ),
        )

        notifier = build_default_notifier()

        assert notifier is not None
        assert notifier.enabled is True
        assert notifier.config.token == "tok-177"
        assert notifier.config.chat_id == "chat-177"

    def test_swallows_settings_failures(self, monkeypatch):
        def _boom():
            raise RuntimeError("config broken")

        monkeypatch.setattr("app.analytics.live_executor.load_settings", _boom)

        assert build_default_notifier() is None

    def test_run_live_executor_injects_the_default_notifier(self, monkeypatch):
        """start_processes.sh must reach the same wiring as `python -m`."""
        captured = {}

        class _Spy(LiveExecutor):
            def run(self, **kwargs):
                captured["kwargs"] = kwargs

        sentinel = FakeNotifier()
        monkeypatch.setattr("app.analytics.live_executor.LiveExecutor", _Spy)
        monkeypatch.setattr(
            "app.analytics.live_executor.build_default_notifier",
            lambda: sentinel,
        )

        executor = run_live_executor(duration_minutes=1)

        assert executor.notifier is sentinel
        assert captured["kwargs"] == {
            "duration_minutes": 1,
            "until_session_end": False,
        }


class TestValidateLiveAlertingValues:
    """The shared guard behind both ``.env`` and the in-memory override."""

    def test_missing_and_none_values_are_allowed(self):
        """Absent keys fall back to the shipped defaults."""
        validate_live_alerting_values({})
        validate_live_alerting_values({"slippage_alert_bp": None})

    def test_integer_keys_accept_their_lower_bound(self):
        validate_live_alerting_values({"alert_debounce_seconds": 0})
        validate_live_alerting_values({"max_consecutive_errors": 1})

    def test_float_keys_reject_their_lower_bound(self):
        """0 bp would alert on every fill, so the range stays (0, 10000]."""
        with pytest.raises(ValueError, match=r"slippage_alert_bp.*\(0, 10000\]"):
            validate_live_alerting_values({"slippage_alert_bp": 0})

    def test_a_boolean_is_not_a_number(self):
        with pytest.raises(ValueError, match="max_consecutive_errors"):
            validate_live_alerting_values({"max_consecutive_errors": True})

    def test_metrics_key_must_fit_the_app_settings_column(self):
        validate_live_alerting_values({"metrics_key": "live_executor_metrics"})

        with pytest.raises(ValueError, match="metrics_key"):
            validate_live_alerting_values({"metrics_key": "x" * 65})
        with pytest.raises(ValueError, match="metrics_key"):
            validate_live_alerting_values({"metrics_key": "   "})

    def test_non_finite_values_are_rejected(self):
        with pytest.raises(ValueError, match="heartbeat_stale_seconds"):
            validate_live_alerting_values(
                {"heartbeat_stale_seconds": float("nan")}
            )

    def test_unparsable_values_are_rejected(self):
        with pytest.raises(ValueError, match="metrics_flush_seconds"):
            validate_live_alerting_values({"metrics_flush_seconds": "often"})


# --- Issue #177 step 4: event hooks on the fake db/broker surface ---------------


BAR = {"close": 99.0, "high": 100.0, "low": 98.0, "volume": 1000}


def _boom(*_args, **_kwargs):
    raise RuntimeError("db down")


def make_hook_executor(
    *, notifier=None, clock=None, db=None, broker=None, sleep_fn=None, **alerting
):
    """Executor on the Fake db/broker surface wired to a recording notifier."""
    executor = make_executor(
        db=db if db is not None else FakeDB(),
        broker=broker if broker is not None else FakeBroker(),
        clock=clock,
        sleep_fn=sleep_fn,
    )
    executor.notifier = notifier
    executor.alerting = dict(get_live_alerting_config(), **alerting)
    return executor


class LiveEquityDB(FakeDB):
    """FakeDB that also answers the ``trading.live_equity`` reads of #176."""

    def __init__(self, frame):
        super().__init__()
        self.frame = frame

    def select(self, query, params=None):
        if "FROM trading.live_equity" in " ".join(query.split()):
            return Result(self.frame)
        return super().select(query, params)


class TestHeartbeat:
    """The paced "the loop is alive" message (decision D4)."""

    def test_heartbeat_is_paced_by_the_configured_interval(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_hook_executor(
            notifier=notifier, clock=clock, heartbeat_interval_seconds=60
        )

        assert executor._maybe_send_heartbeat() is True
        assert notifier.messages[0].startswith("💓 *Live-контур жив*")
        assert "*Открытых позиций:* `0`" in notifier.messages[0]
        assert "*Kill switch:* `OFF`" in notifier.messages[0]
        assert executor.heartbeats_sent_total == 1

        assert executor._maybe_send_heartbeat() is False
        assert len(notifier.messages) == 1

        clock.advance(60)
        assert executor._maybe_send_heartbeat() is True
        assert len(notifier.messages) == 2
        assert executor.heartbeats_sent_total == 2

    def test_heartbeat_is_disabled_when_the_interval_is_not_positive(self):
        """In-memory heartbeat tracking stays, Telegram does not (decision D4)."""
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_hook_executor(
            notifier=notifier, clock=clock, heartbeat_interval_seconds=0
        )

        for _ in range(3):
            clock.advance(3600)
            assert executor._maybe_send_heartbeat() is False

        assert notifier.messages == []
        assert executor.heartbeats_sent_total == 0

    def test_heartbeat_ignores_the_general_debounce_window(self):
        """A one-hour debounce must not silence the once-a-minute heartbeat."""
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_hook_executor(
            notifier=notifier,
            clock=clock,
            heartbeat_interval_seconds=60,
            alert_debounce_seconds=3600,
        )

        executor._maybe_send_heartbeat()
        clock.advance(60)
        executor._maybe_send_heartbeat()

        assert len(notifier.messages) == 2

    def test_heartbeat_survives_a_failing_position_read(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        db = FakeDB()
        db.select = _boom
        executor = make_hook_executor(
            notifier=notifier, clock=clock, db=db, heartbeat_interval_seconds=60
        )

        assert executor._maybe_send_heartbeat() is True
        assert "*Открытых позиций:*" not in notifier.messages[0]

    def test_a_rejected_heartbeat_keeps_its_pace(self):
        """A Telegram outage is counted, not hidden, and does not stall the pace."""
        clock = FakeClock()
        notifier = FakeNotifier(result=False)
        executor = make_hook_executor(
            notifier=notifier, clock=clock, heartbeat_interval_seconds=60
        )

        assert executor._maybe_send_heartbeat() is False
        assert executor.heartbeats_sent_total == 0
        assert executor.alerts_failed_total == 1

        clock.advance(1)
        assert executor._maybe_send_heartbeat() is False
        assert len(notifier.messages) == 1

        clock.advance(59)
        assert executor._maybe_send_heartbeat() is False
        assert len(notifier.messages) == 2
        assert executor.heartbeats_sent_total == 0


class TestKillSwitchAlerts:
    """Kill switch transitions are one-shot events (#151 / #177)."""

    def test_switching_on_is_a_critical_alert_with_its_source(self):
        notifier = FakeNotifier()
        db = FakeDB(app_settings={"trailing_kill_switch": True})
        executor = make_hook_executor(notifier=notifier, db=db)

        executor._refresh_kill_switch()

        assert executor.config["trailing_kill_switch"] is True
        assert notifier.messages == [
            "🚨 *Kill switch ВКЛЮЧЁН*\n"
            "*Уровень:* `critical`\n"
            "*Эффект:* `трейлинг-переносы остановлены, выставленные стопы сохранены`\n"
            "*Источник:* `app\\_settings`"
        ]
        assert executor.alerts_sent_total == 1

    def test_switching_off_is_informational(self):
        notifier = FakeNotifier()
        db = FakeDB(app_settings={"trailing_kill_switch": True})
        executor = make_hook_executor(notifier=notifier, db=db)
        executor._refresh_kill_switch()
        db.app_settings["trailing_kill_switch"] = False

        executor._refresh_kill_switch()

        assert executor.config["trailing_kill_switch"] is False
        assert notifier.messages[-1].startswith("ℹ️ *Kill switch ВЫКЛЮЧЕН*")
        assert "*Эффект:* `трейлинг-переносы снова разрешены`" in notifier.messages[-1]
        assert len(notifier.messages) == 2

    def test_a_steady_kill_switch_does_not_spam(self):
        notifier = FakeNotifier()
        db = FakeDB(app_settings={"trailing_kill_switch": True})
        executor = make_hook_executor(notifier=notifier, db=db)

        for _ in range(5):
            executor._refresh_kill_switch()

        assert len(notifier.messages) == 1

    def test_a_broken_settings_read_fails_safe_on_and_alerts(self):
        """Fail-safe ON must be visible: a silent freeze is the worst outcome."""
        notifier = FakeNotifier()
        db = FakeDB()
        db.select = _boom
        executor = make_hook_executor(notifier=notifier, db=db)

        executor._refresh_kill_switch()

        assert executor.config["trailing_kill_switch"] is True
        assert notifier.messages[0].startswith("🚨 *Kill switch ВКЛЮЧЁН*")
        assert "*Источник:* `db\\_error:RuntimeError`" in notifier.messages[0]


class TestRiskBreachAlerts:
    """The daily drawdown gate announces latch, release and restore (#176/#177)."""

    @staticmethod
    def _breach(executor):
        executor._activate_risk_breach(
            session_key=date(2026, 9, 27),
            equity=90_000.0,
            peak=100_000.0,
            drawdown_pct=10.0,
            max_daily_loss_pct=5.0,
        )

    def test_latching_a_breach_is_a_one_shot_critical_alert(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier)

        self._breach(executor)

        assert executor._risk_breach_active is True
        assert executor.risk_breach_total == 1
        # Issue #191: with no snapshot behind the latch the measurement fields
        # say so explicitly instead of rendering as blanks.
        assert notifier.messages == [
            "🚨 *Превышен дневной лимит убытка*\n"
            "*Уровень:* `critical`\n"
            "*Просадка:* `10.0000%`\n"
            "*Лимит:* `5.0000%`\n"
            "*Equity:* `90000.00 RUB`\n"
            "*Пик дня:* `100000.00 RUB`\n"
            "*Кэш:* `нет данных`\n"
            "*Стоимость позиций:* `нет данных`\n"
            "*Без цены:* `нет`\n"
            "*По средней цене:* `нет`\n"
            "*Сессия:* `2026-09-27`\n"
            "*Эффект:* `новые входы заблокированы, стопы сохранены`"
        ]

    def test_the_breach_alert_carries_the_measurement_behind_the_number(self):
        """A drawdown nobody can trace back to cash and positions is useless (#191)."""
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier)
        executor.last_cash_rub = 90_000.0
        executor.last_market_value_rub = 0.0
        executor.unpriced_holding_tickers = ["GAZP", "SBER"]
        executor.stale_priced_holding_tickers = ["LKOH"]

        self._breach(executor)

        message = notifier.messages[0]
        assert "*Кэш:* `90000.00 RUB`" in message
        assert "*Стоимость позиций:* `0.00 RUB`" in message
        assert "*Без цены:* `GAZP, SBER`" in message
        assert "*По средней цене:* `LKOH`" in message

    def test_latching_a_breach_publishes_the_metrics_snapshot_at_once(self):
        """The panel must not serve a pre-breach row for ``metrics_flush_seconds``."""
        db = MetricsDB()
        executor = make_hook_executor(notifier=FakeNotifier(), db=db)

        assert db.metrics_writes == []

        self._breach(executor)

        assert db.keys == ["live_executor_metrics"]
        payload = db.payloads[0]
        assert payload["risk_breach_active"] is True
        assert payload["risk_breach_total"] == 1

    def test_a_failed_metrics_flush_never_blocks_the_breach_latch(self):
        db = MetricsDB(fail=True)
        executor = make_hook_executor(notifier=FakeNotifier(), db=db)

        self._breach(executor)

        assert executor._risk_breach_active is True
        assert executor.metrics_flush_errors_total == 1
        assert len(executor.notifier.messages) == 1

    def test_clearing_a_breach_announces_the_recovery_once(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier)
        self._breach(executor)

        executor._clear_risk_breach(reason="new_msk_session_day")
        executor._clear_risk_breach(reason="new_msk_session_day")

        assert executor._risk_breach_active is False
        assert executor.risk_breach_resets_total == 1
        assert len(notifier.messages) == 2
        assert notifier.messages[-1].startswith(
            "ℹ️ *Блокировка по дневной просадке снята*"
        )
        assert "*Причина:* `new\\_msk\\_session\\_day`" in notifier.messages[-1]
        assert "*Эффект:* `новые входы снова разрешены`" in notifier.messages[-1]

    def test_a_restart_restores_an_already_latched_breach(self):
        """A restart must not silently disarm a gate that already breached."""
        notifier = FakeNotifier()
        frame = pd.DataFrame(
            [
                {
                    "risk_breach": True,
                    "drawdown_pct": 7.5,
                    "equity_rub": 92_500.0,
                    "peak_equity_rub": 100_000.0,
                }
            ]
        )
        executor = make_hook_executor(notifier=notifier, db=LiveEquityDB(frame))

        executor._restore_risk_breach_state()

        assert executor._risk_breach_active is True
        assert notifier.messages[0].startswith(
            "🚨 *После перезапуска восстановлена блокировка по просадке*"
        )
        assert "*Просадка:* `7.5`" in notifier.messages[0]
        assert "*Equity:* `92500.0`" in notifier.messages[0]

    def test_a_restart_without_a_latched_breach_stays_silent(self):
        notifier = FakeNotifier()
        frame = pd.DataFrame([{"risk_breach": False}])
        executor = make_hook_executor(notifier=notifier, db=LiveEquityDB(frame))

        executor._restore_risk_breach_state()

        assert executor._risk_breach_active is False
        assert notifier.messages == []


class TestProtectionFailureAlerts:
    """An unprotected position is the top incident of the live contour."""

    def test_a_failed_stop_arming_alerts_and_debounces_per_ticker(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_hook_executor(
            notifier=notifier, clock=clock, alert_debounce_seconds=60
        )

        executor._register_protection_failure(41, "SBER", "SandboxAPIError", 95.0)
        executor._register_protection_failure(41, "SBER", "SandboxAPIError", 95.0)

        assert executor.protection_failed_total == 2
        assert executor._protection_attempts[41] == 2
        assert len(notifier.messages) == 1
        assert notifier.messages[0].startswith("🚨 *Стоп не выставлен: позиция без защиты*")
        assert "*Уровень:* `critical`" in notifier.messages[0]
        assert "*Тикер:* `SBER`" in notifier.messages[0]
        assert "*Попытка:* `1`" in notifier.messages[0]
        assert "*Операция:* `post\\_stop\\_order`" in notifier.messages[0]
        assert "*Ошибка:* `SandboxAPIError`" in notifier.messages[0]
        assert "*Цена стопа:* `95.000000`" in notifier.messages[0]
        assert "*Повтор через:* `30 с`" in notifier.messages[0]

        clock.advance(60)
        executor._register_protection_failure(41, "SBER", "SandboxAPIError", 95.0)

        assert len(notifier.messages) == 2
        assert "*Попытка:* `3`" in notifier.messages[1]

        executor._register_protection_failure(42, "GAZP", "SandboxAPIError", 95.0)

        assert len(notifier.messages) == 3
        assert "*Тикер:* `GAZP`" in notifier.messages[2]

    def test_a_broker_stop_failure_alerts_protection_and_the_amend(self):
        """A failed trailing move must be visible: the old stop is stale now."""
        clock = FakeClock()
        notifier = FakeNotifier()
        broker = StopFakeBroker(stop_error=SandboxAPIError("post_stop_order failed"))
        executor = make_hook_executor(
            notifier=notifier, clock=clock, broker=broker, alert_debounce_seconds=60
        )
        row = active_position(broker_stop_id="stop-1").to_dict("records")[0]

        assert executor._amend_broker_stop(row, 97.0, 2) is None

        assert executor.stop_amend_failed_total == 1
        amend_alerts = [
            m
            for m in notifier.messages
            if m.startswith("🚨 *Трейлинг: брокерский стоп не перенесён*")
        ]
        assert len(amend_alerts) == 1
        assert "*Тикер:* `SBER`" in amend_alerts[0]
        assert "*Новый стоп:* `97.000000`" in amend_alerts[0]
        assert "*Ступень:* `2`" in amend_alerts[0]
        assert "*Эффект:* `действует предыдущий, более низкий стоп`" in amend_alerts[0]
        # the failed arming itself is alerted as well (protection_failed)
        assert any(
            m.startswith("🚨 *Стоп не выставлен: позиция без защиты*")
            for m in notifier.messages
        )

        executor._amend_broker_stop(row, 97.0, 2)
        assert len(amend_alerts) == 1

        clock.advance(60)
        executor._amend_broker_stop(row, 97.0, 2)
        assert executor.stop_amend_failed_total == 3
        assert (
            len(
                [
                    m
                    for m in notifier.messages
                    if m.startswith("🚨 *Трейлинг: брокерский стоп не перенесён*")
                ]
            )
            == 2
        )


class TestInvariantViolationAlerts:
    """#175 invariants, now spoken out loud (#177)."""

    def test_a_stop_id_the_broker_does_not_confirm_is_a_violation(self):
        notifier = FakeNotifier()
        broker = StopFakeBroker(stop_orders=[])
        executor = make_hook_executor(notifier=notifier, broker=broker)
        row = active_position(broker_stop_id="stop-1").to_dict("records")[0]

        executor._reconcile_protection([row], [SimpleNamespace(ticker="SBER")])

        # one pass reports both the stale id and the resulting naked position
        assert executor.invariant_violations_total == 2
        alerts = [m for m in notifier.messages if "Инвариант нарушен" in m]
        assert len(alerts) == 1
        assert alerts[0].startswith(
            "🚨 *Инвариант нарушен: стоп id есть, но у брокера стопа нет*"
        )
        assert "*Тикер:* `SBER`" in alerts[0]
        assert "*broker\\_stop\\_id:* `stop-1`" in alerts[0]
        assert "*Действие:* `id снимается, стоп будет перевыставлен`" in alerts[0]
        assert broker.cancelled_stops == ["stop-1"]
        assert broker.posted_stops  # re-armed

    def test_repeated_violations_of_one_ticker_are_debounced(self):
        """The reconcile pass runs every cycle and must not spam the operator."""
        clock = FakeClock()
        notifier = FakeNotifier()
        broker = StopFakeBroker(stop_orders=[])
        executor = make_hook_executor(
            notifier=notifier, clock=clock, broker=broker, alert_debounce_seconds=900
        )
        row = active_position(broker_stop_id="stop-1").to_dict("records")[0]

        executor._reconcile_protection([row], [SimpleNamespace(ticker="SBER")])
        first_pass = len(notifier.messages)
        clock.advance(120)
        executor._reconcile_protection([row], [SimpleNamespace(ticker="SBER")])

        assert first_pass == 1
        assert len(notifier.messages) == 1

    def test_an_open_position_without_a_stop_id_is_a_violation(self):
        notifier = FakeNotifier()
        broker = StopFakeBroker()
        executor = make_hook_executor(notifier=notifier, broker=broker)
        row = active_position(broker_stop_id=None).to_dict("records")[0]

        executor._reconcile_protection([row], [SimpleNamespace(ticker="SBER")])

        assert executor.invariant_violations_total == 1
        assert any(
            m.startswith("🚨 *Инвариант нарушен: открытая позиция без стопа*")
            for m in notifier.messages
        )
        assert broker.posted_stops


class TestOcoOrphanAlerts:
    """A leg that survived a close can fill against a flat position (#175/#177)."""

    @staticmethod
    def _check(clock, *, kind="stop", order_id="stop-1", position_id=41):
        return {
            "position_id": position_id,
            "ticker": "SBER",
            "kind": kind,
            "order_id": order_id,
            "due_at": clock(),
            "attempts": 0,
        }

    def test_an_orphaned_stop_leg_is_cancelled_with_a_critical_alert(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        broker = StopFakeBroker(stop_orders=[stop_item("stop-1")])
        executor = make_hook_executor(notifier=notifier, clock=clock, broker=broker)
        executor._oco_checks.append(self._check(clock))

        assert executor._process_oco_checks() == 1
        assert executor.oco_orphans_cancelled_total == 1
        assert broker.cancelled_stops == ["stop-1"]
        assert notifier.messages[0].startswith("🚨 *OCO: обнаружен осиротевший ордер*")
        assert "*Уровень:* `critical`" in notifier.messages[0]
        assert "*Тип ордера:* `stop`" in notifier.messages[0]
        assert "*Ордер:* `stop-1`" in notifier.messages[0]
        assert "*Снят:* `да`" in notifier.messages[0]

    def test_an_orphaned_take_leg_is_cancelled_through_the_order_api(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        broker = StopFakeBroker(
            resting_orders=[
                SimpleNamespace(order_id="take-1", instrument_uid="figi-sber")
            ]
        )
        executor = make_hook_executor(notifier=notifier, clock=clock, broker=broker)
        executor._oco_checks.append(self._check(clock, kind="take", order_id="take-1"))

        assert executor._process_oco_checks() == 1
        assert executor.oco_orphans_cancelled_total == 1
        assert ("cancel_order", {"order_id": "take-1"}) in broker.calls
        assert notifier.messages[0].startswith("🚨 *OCO: обнаружен осиротевший ордер*")
        assert "*Тип ордера:* `take`" in notifier.messages[0]

    def test_a_confirmed_absence_of_an_orphan_stays_silent(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        broker = StopFakeBroker(stop_orders=[])
        executor = make_hook_executor(notifier=notifier, clock=clock, broker=broker)
        executor._oco_checks.append(self._check(clock))

        assert executor._process_oco_checks() == 0
        assert executor.oco_orphans_cancelled_total == 0
        assert notifier.messages == []
        assert executor._oco_checks == []

    def test_exhausted_verification_escalates_to_the_operator(self):
        """Unknown is worse than absent: a human has to look at the broker."""
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier, clock=clock, broker=FakeBroker())
        executor.config["oco_check_attempts"] = 1
        executor._oco_checks.append(self._check(clock))

        assert executor._process_oco_checks() == 0
        assert executor._oco_checks == []
        assert notifier.messages[0].startswith(
            "🚨 *OCO: ордер не проверен, попытки исчерпаны*"
        )
        assert "*Попытки:* `1`" in notifier.messages[0]
        assert "*Действие:* `проверить ордер у брокера вручную`" in notifier.messages[0]

    def test_an_unverified_orphan_is_requeued_silently_below_the_limit(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier, clock=clock, broker=FakeBroker())
        executor.config["oco_check_attempts"] = 3
        executor.config["oco_check_delay_seconds"] = 45
        executor._oco_checks.append(self._check(clock))

        assert executor._process_oco_checks() == 0
        assert notifier.messages == []
        assert len(executor._oco_checks) == 1
        assert executor._oco_checks[0]["attempts"] == 1
        assert executor._oco_checks[0]["due_at"] == clock() + 45


class TestEquitySnapshotAlerts:
    """A dead equity snapshot blinds the daily drawdown gate (#176/#177)."""

    EQUITY = {
        "equity_rub": 100_000.0,
        "cash_rub": 100_000.0,
        "market_value_rub": 0.0,
        "unrealized_pnl_rub": 0.0,
    }

    def test_a_failed_snapshot_alerts_and_debounces_while_the_fault_lasts(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_hook_executor(
            notifier=notifier, clock=clock, alert_debounce_seconds=60
        )
        executor._compute_live_equity = lambda: dict(self.EQUITY)
        executor.db.execute = _boom

        assert executor._write_live_equity() is None
        assert executor.equity_snapshot_errors_total == 1
        assert notifier.messages[0].startswith(
            "🚨 *Снимок equity не записан: гейт просадки ослеплён*"
        )
        assert "*Ошибка:* `RuntimeError`" in notifier.messages[0]
        assert "*Сбоев всего:* `1`" in notifier.messages[0]
        assert (
            "*Эффект:* `дневной лимит убытка не пересчитывается`" in notifier.messages[0]
        )

        executor._write_live_equity()
        assert executor.equity_snapshot_errors_total == 2
        assert len(notifier.messages) == 1  # inside the debounce window

        clock.advance(60)
        executor._write_live_equity()
        assert len(notifier.messages) == 2
        assert "*Сбоев всего:* `3`" in notifier.messages[1]

    def test_a_healthy_snapshot_stays_silent(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier, clock=clock)
        executor._compute_live_equity = lambda: dict(self.EQUITY)

        assert executor._write_live_equity() is not None
        assert executor.equity_snapshots_total == 1
        assert notifier.messages == []


def priced_holding(ticker="SBER", quantity=100, average=95.0, current=90.0):
    """One broker portfolio holding, shaped like ``SandboxPosition`` (#191)."""
    return SimpleNamespace(
        ticker=ticker,
        quantity=quantity,
        average_price=average,
        current_price=current,
    )


class TestHoldingPriceAlerts:
    """A holding the broker cannot price used to vanish silently (#191).

    The 28.09 incident: a fresh position came back with ``current_price = 0``,
    ``market_value`` lost its whole notional while cash had already dropped, and
    the daily drawdown gate latched a breach that never happened in the account.
    Both degradations are now counted, published and announced.
    """

    def test_an_unpriced_holding_alerts_once_and_resolves_once(self):
        clock = FakeClock()
        notifier = FakeNotifier()
        broker = FakeBroker(
            positions=[priced_holding(average=0.0, current=0.0)], balance=10000.0
        )
        executor = make_hook_executor(
            notifier=notifier, clock=clock, broker=broker, alert_debounce_seconds=60
        )

        equity = executor._compute_live_equity()

        assert equity["market_value_rub"] == 0.0
        assert equity["equity_rub"] == 10000.0
        assert executor.holdings_unpriced_total == 1
        assert executor.unpriced_holding_tickers == ["SBER"]
        assert notifier.messages[0].startswith("🚨 *Позиция без цены: equity занижен*")
        assert "*Тикер:* `SBER`" in notifier.messages[0]
        assert "*Штук:* `100`" in notifier.messages[0]
        assert "*Событий всего:* `1`" in notifier.messages[0]

        # Still unpriced on the next cycle: inside the debounce window.
        clock.advance(1)
        executor._compute_live_equity()
        assert len(notifier.messages) == 1
        assert executor.holdings_unpriced_total == 2

        # The broker starts quoting again: exactly one resolution message.
        clock.advance(60)
        broker.positions = [priced_holding(average=95.0, current=90.0)]
        executor._compute_live_equity()

        assert executor.unpriced_holding_tickers == []
        assert len(notifier.messages) == 2
        assert notifier.messages[-1].startswith(
            "ℹ️ *Цена позиции появилась: equity снова полный*"
        )
        assert "*Тикер:* `SBER`" in notifier.messages[-1]

    def test_a_holding_marked_at_its_average_price_is_reported_separately(self):
        """Equity stays whole, but intraday moves are blind - say so."""
        notifier = FakeNotifier()
        broker = FakeBroker(
            positions=[priced_holding(average=95.0, current=0.0)], balance=50000.0
        )
        executor = make_hook_executor(notifier=notifier, broker=broker)

        equity = executor._compute_live_equity()

        assert equity["market_value_rub"] == 9500.0
        assert equity["equity_rub"] == 59500.0
        assert equity["unrealized_pnl_rub"] == 0.0
        assert executor.holdings_stale_priced_total == 1
        assert executor.stale_priced_holding_tickers == ["SBER"]
        assert executor.unpriced_holding_tickers == []
        assert notifier.messages[0].startswith("🚨 *Позиция отмечена по средней цене*")
        assert "*Тикер:* `SBER`" in notifier.messages[0]

    def test_a_fully_priced_portfolio_stays_silent(self):
        notifier = FakeNotifier()
        broker = FakeBroker(
            positions=[priced_holding(average=95.0, current=90.0)], balance=50000.0
        )
        executor = make_hook_executor(notifier=notifier, broker=broker)

        equity = executor._compute_live_equity()

        assert equity["market_value_rub"] == 9000.0
        assert executor.holdings_unpriced_total == 0
        assert executor.holdings_stale_priced_total == 0
        assert notifier.messages == []

    def test_the_measurement_quality_is_published_in_the_metrics(self):
        broker = FakeBroker(
            positions=[priced_holding(average=0.0, current=0.0)], balance=10000.0
        )
        executor = make_hook_executor(broker=broker)

        executor._compute_live_equity()
        metrics = executor.get_metrics()

        assert metrics["holdings_unpriced_total"] == 1
        assert metrics["unpriced_holding_tickers"] == ["SBER"]
        assert metrics["stale_priced_holding_tickers"] == []
        assert metrics["equity_last_cash_rub"] is None
        assert metrics["equity_last_market_value_rub"] is None


class TestTradingEventAlerts:
    """Entry, trailing step and exit are rare one-shot events (decision D2)."""

    SIGNAL = {"action": "enter", "entry_price": 100, "stop": 95, "take": 110}

    def test_an_entry_is_announced_with_its_exit_plan(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier, broker=StopFakeBroker())

        result = executor.process_signal("SBER", dict(self.SIGNAL), imbalance=1.5)

        assert result["executed"] is True
        assert notifier.messages[0].startswith("📈 *Открыта live-позиция (песочница)*")
        assert "*Тикер:* `SBER`" in notifier.messages[0]
        assert "*Цена входа:* `100.0000`" in notifier.messages[0]
        assert "*Стоп:* `95.0000`" in notifier.messages[0]
        assert "*Тейк:* `110.0000`" in notifier.messages[0]
        assert "*Позиция:* `41`" in notifier.messages[0]
        assert executor.alerts_sent_total == 1

    def test_a_missing_take_profit_is_reported_instead_of_the_entry(self):
        """The stop is armed, so this is a changed exit plan, not a naked position.

        The critical replaces the informational entry alert: one message already
        carries the ticker, the position id and both protection legs, so the
        operator is not woken up twice for the same trade.
        """
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier, broker=StopFakeBroker())
        executor._place_take_order = _boom

        result = executor.process_signal("SBER", dict(self.SIGNAL), imbalance=1.5)

        assert result == {
            "executed": True,
            "reason": "protection_pending",
            "position_id": 41,
        }
        assert len(notifier.messages) == 1
        assert notifier.messages[0].startswith(
            "🚨 *Вход исполнен, тейк-профит не выставлен*"
        )
        assert "*Уровень:* `critical`" in notifier.messages[0]
        assert "*Позиция:* `41`" in notifier.messages[0]
        assert "*Ошибка:* `RuntimeError`" in notifier.messages[0]
        assert "*Стоп:* `выставлен`" in notifier.messages[0]
        assert "*Тейк:* `110.0000`" in notifier.messages[0]
        assert executor.alerts_sent_total == 1


    def test_a_trailing_ratchet_is_announced_with_the_new_stop(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier)
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

        assert executor._apply_trailing(row, current_price=112.0) == pytest.approx(105.0)

        steps = [m for m in notifier.messages if "Трейлинг: стоп перенесён" in m]
        assert len(steps) == 1
        assert steps[0].startswith("🪜 *Трейлинг: стоп перенесён*")
        assert "*Ступень:* `1`" in steps[0]
        assert "*Новый стоп:* `105.000000`" in steps[0]
        assert "*Kill switch:* `OFF`" in steps[0]

    def test_a_ratchet_that_does_not_move_the_stop_stays_silent(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier)
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
            "step_reached": 1,
        }

        assert executor._apply_trailing(row, current_price=112.0) is None
        assert notifier.messages == []

    def test_an_exit_is_announced_with_its_execution_facts(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier)
        row = active_position(broker_stop_id="stop-1", risk_r=5.0).to_dict("records")[0]

        executor._close_db_position(
            row, "stop", 95.0, exit_price_actual=94.9, lots_executed=10
        )

        exits = [m for m in notifier.messages if "Закрыта live-позиция" in m]
        assert len(exits) == 1
        assert exits[0].startswith("🛑 *Закрыта live-позиция (песочница)*")
        assert "*Причина:* `stop`" in exits[0]
        assert "*Статус:* `closed\\_stop`" in exits[0]
        assert "*Цена модели:* `95.0000`" in exits[0]
        assert "*Цена факта:* `94.9000`" in exits[0]
        assert "*PnL:* `-510.00 RUB`" in exits[0]
        assert "*Slippage:* `-10.53 bp`" in exits[0]
        assert "*Slippage R:* `0.02`" in exits[0]
        assert "*Лотов исполнено:* `10`" in exits[0]
        # 10.53 bp is far below the 50 bp threshold - no escalation
        assert not any(
            "Проскальзывание выхода выше порога" in m for m in notifier.messages
        )

    def test_a_bad_fill_escalates_to_a_critical_slippage_alert(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier, slippage_alert_bp=50.0)
        row = active_position(broker_stop_id="stop-1", risk_r=5.0).to_dict("records")[0]

        executor._close_db_position(row, "stop", 95.0, exit_price_actual=94.0)

        assert notifier.messages[0].startswith("🛑 *Закрыта live-позиция (песочница)*")
        assert "*Slippage:* `-105.26 bp`" in notifier.messages[0]
        assert notifier.messages[1].startswith(
            "🚨 *Проскальзывание выхода выше порога*"
        )
        assert "*Порог:* `50.00 bp`" in notifier.messages[1]
        assert "*Факт:* `-105.26 bp`" in notifier.messages[1]
        assert "*Причина выхода:* `stop`" in notifier.messages[1]

    def test_a_zero_slippage_threshold_disables_the_escalation(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier, slippage_alert_bp=0.0)
        row = active_position(broker_stop_id="stop-1").to_dict("records")[0]

        executor._close_db_position(row, "stop", 95.0, exit_price_actual=90.0)

        assert len([m for m in notifier.messages if "Закрыта live-позиция" in m]) == 1
        assert not any("Проскальзывание" in m for m in notifier.messages)

    def test_a_take_exit_gets_its_own_icon_and_no_fill_facts(self):
        notifier = FakeNotifier()
        executor = make_hook_executor(notifier=notifier)
        row = active_position(broker_stop_id="stop-1").to_dict("records")[0]

        executor._close_db_position(row, "take", 110.0)

        assert notifier.messages[0].startswith("✅ *Закрыта live-позиция (песочница)*")
        assert "*Причина:* `take`" in notifier.messages[0]
        assert "*Статус:* `closed\\_take`" in notifier.messages[0]
        assert "*Цена факта:*" not in notifier.messages[0]
        assert "*Slippage:*" not in notifier.messages[0]
        assert "*PnL:* `+1000.00 RUB`" in notifier.messages[0]


class TestLifecycleAlerts:
    """Start, stop and the error-shutdown of the whole contour (#177)."""

    @staticmethod
    def _runnable(**kwargs):
        """Executor whose run() touches neither signals nor the equity gate."""
        executor = make_hook_executor(sleep_fn=lambda _seconds: None, **kwargs)
        executor.install_signal_handlers = lambda: None
        executor.initialize = lambda: None
        executor.config["equity_snapshot_enabled"] = False
        executor.config["check_interval_seconds"] = 0
        executor.config["context_refresh_seconds"] = 3600
        executor.config["close_positions_on_shutdown"] = False
        return executor

    def test_a_short_run_announces_start_and_stop(self):
        notifier = FakeNotifier()
        executor = self._runnable(notifier=notifier)

        executor.run(duration_minutes=0)

        assert notifier.messages[0].startswith("🚀 *Live-контур запущен (песочница)*")
        assert "*Алерты:* `Telegram`" in notifier.messages[0]
        assert "*Порог ошибок подряд:* `5`" in notifier.messages[0]
        assert notifier.messages[-1].startswith("ℹ️ *Live-контур остановлен*")
        assert "*Итераций:* `0`" in notifier.messages[-1]
        assert "*Алертов отправлено:* `1`" in notifier.messages[-1]
        assert "*Блокировка просадки:* `нет`" in notifier.messages[-1]
        assert executor.alerts_sent_total == 2

    def test_the_log_only_contour_is_announced_in_the_log(self, caplog):
        executor = self._runnable(notifier=None)

        with caplog.at_level("INFO"):
            executor.run(duration_minutes=0)

        assert executor.alerts_attempted_total == 2
        assert executor.alerts_skipped_total == 2
        assert executor.alerts_sent_total == 0
        # Issue #178: the announcement names the contour instead of hardcoding
        # "Sandbox", and a test double must never be reported as the real one.
        assert "LiveExecutor started: contour=" in caplog.text
        assert "LiveExecutor stopped cleanly: contour=" in caplog.text
        assert "contour=real" not in caplog.text

    def test_a_series_of_monitor_errors_stops_the_contour(self):
        notifier = FakeNotifier()
        executor = self._runnable(notifier=notifier)
        executor._max_consecutive_errors = 1
        executor.monitor_positions = _boom

        executor.run()

        assert notifier.messages[1].startswith(
            "🚨 *Live-контур остановлен: серия ошибок*"
        )
        assert "*Уровень:* `critical`" in notifier.messages[1]
        assert "*Фаза:* `monitor\\_positions`" in notifier.messages[1]
        assert "*Ошибок подряд:* `1`" in notifier.messages[1]
        assert "*Порог:* `1`" in notifier.messages[1]
        assert "*Ошибок всего:* `1`" in notifier.messages[1]
        assert executor.errors_total == 1
        assert notifier.messages[-1].startswith("ℹ️ *Live-контур остановлен*")

    def test_a_series_of_entry_errors_stops_the_contour_too(self, monkeypatch):
        monkeypatch.setattr(
            "app.analytics.live_executor.is_entry_window", lambda _now: True
        )
        notifier = FakeNotifier()
        executor = self._runnable(notifier=notifier)
        executor._max_consecutive_errors = 1
        executor.process_latest_bars = _boom

        executor.run()

        assert notifier.messages[1].startswith(
            "🚨 *Live-контур остановлен: серия ошибок*"
        )
        assert "*Фаза:* `process\\_latest\\_bars`" in notifier.messages[1]
        assert executor.errors_total == 1

    def test_a_single_monitor_error_does_not_stop_the_contour(self, monkeypatch):
        monkeypatch.setattr(
            "app.analytics.live_executor.is_entry_window", lambda _now: False
        )
        notifier = FakeNotifier()
        clock = FakeClock()
        executor = self._runnable(notifier=notifier, clock=clock)
        calls = []

        def flaky_monitor():
            calls.append(clock())
            if len(calls) == 1:
                raise RuntimeError("transient")

        executor.monitor_positions = flaky_monitor
        executor._max_consecutive_errors = 2

        def stop_after_two(_seconds):
            if len(calls) >= 2:
                executor.shutdown_requested.set()

        executor.sleep_fn = stop_after_two
        executor.run()

        assert executor.errors_total == 1
        assert executor._consecutive_errors == 0
        assert not any("серия ошибок" in m for m in notifier.messages)










# --- Issue #177 step 5: throttled metrics persistence (decision D5) -------------


class MetricsDB(FakeDB):
    """FakeDB that records ``trading.app_settings`` upserts and can fail them."""

    def __init__(self, fail=False, **kwargs):
        super().__init__(**kwargs)
        self.fail = fail
        self.metrics_writes = []

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        if "INSERT INTO trading.app_settings" in normalized:
            if self.fail:
                raise RuntimeError("db down")
            self.metrics_writes.append(params)
        return super().execute(query, params)

    @property
    def keys(self):
        return [params[0] for params in self.metrics_writes]

    @property
    def payloads(self):
        return [json.loads(params[1]) for params in self.metrics_writes]


def make_metrics_executor(*, db=None, clock=None, notifier=None, **alerting):
    """Executor on a recording db with a controllable flush interval."""
    return make_hook_executor(
        db=db if db is not None else MetricsDB(),
        clock=clock if clock is not None else FakeClock(),
        notifier=notifier,
        **alerting,
    )


class TestMetricsFlush:
    """The persisted snapshot behind GET /api/live-trading/metrics (#177)."""

    def test_the_snapshot_is_upserted_under_the_configured_key(self):
        db = MetricsDB()
        executor = make_metrics_executor(db=db)
        executor.iterations_total = 7
        executor.errors_total = 2
        executor.alerts_sent_total = 3

        assert executor._flush_metrics() is True

        assert db.keys == ["live_executor_metrics"]
        statement = db.execute_calls[0][0]
        assert "INSERT INTO trading.app_settings (key, value, updated_at)" in statement
        assert "ON CONFLICT (key)" in statement
        assert "DO UPDATE SET value = EXCLUDED.value" in statement
        payload = db.payloads[0]
        assert payload["schema_version"] == 1
        assert payload["iterations_total"] == 7
        assert payload["errors_total"] == 2
        assert payload["alerts_sent_total"] == 3
        assert payload["strategy"] == "active-strategy"
        assert payload["notifier_configured"] is False
        assert payload["telegram_alerts_enabled"] is True
        assert payload["kill_switch"] is False
        assert payload["kill_switch_source"] == "startup"
        assert payload["persisted_at"]
        assert executor.metrics_flushes_total == 1
        assert executor.metrics_flush_errors_total == 0

    def test_the_snapshot_keeps_the_risk_gate_fields_of_previous_issues(self):
        db = MetricsDB()
        executor = make_metrics_executor(db=db)
        executor._risk_breach_active = True
        executor.risk_breach_total = 2

        assert executor._flush_metrics() is True

        payload = db.payloads[0]
        assert payload["risk_breach_active"] is True
        assert payload["risk_breach_total"] == 2
        assert payload["max_daily_loss_pct"] == pytest.approx(
            executor.config["max_daily_loss_pct"]
        )

    def test_datetimes_survive_the_json_roundtrip(self):
        db = MetricsDB()
        executor = make_metrics_executor(db=db)
        executor.heartbeat_ts = executor.now_fn()
        executor.last_error_at = executor.now_fn()

        assert executor._flush_metrics() is True

        payload = db.payloads[0]
        assert isinstance(payload["heartbeat_ts"], str)
        assert isinstance(payload["last_error_at"], str)

    def test_the_periodic_write_is_throttled_by_the_configured_interval(self):
        clock = FakeClock()
        db = MetricsDB()
        executor = make_metrics_executor(db=db, clock=clock, metrics_flush_seconds=60)

        assert executor._flush_metrics() is True
        assert executor._flush_metrics() is False

        clock.advance(59)
        assert executor._flush_metrics() is False

        clock.advance(1)
        assert executor._flush_metrics() is True
        assert len(db.metrics_writes) == 2
        assert executor.metrics_flushes_total == 2

    def test_force_bypasses_the_throttle(self):
        clock = FakeClock()
        db = MetricsDB()
        executor = make_metrics_executor(db=db, clock=clock, metrics_flush_seconds=3600)

        assert executor._flush_metrics() is True
        assert executor._flush_metrics(force=True) is True
        assert len(db.metrics_writes) == 2

    def test_a_non_positive_interval_disables_only_the_periodic_write(self):
        db = MetricsDB()
        executor = make_metrics_executor(db=db, metrics_flush_seconds=0)

        assert executor._flush_metrics() is False
        assert db.metrics_writes == []

        assert executor._flush_metrics(force=True) is True
        assert len(db.metrics_writes) == 1

    def test_a_custom_metrics_key_is_honoured(self):
        db = MetricsDB()
        executor = make_metrics_executor(db=db, metrics_key="live_metrics_canary")

        assert executor._flush_metrics(force=True) is True
        assert db.keys == ["live_metrics_canary"]

    def test_a_failing_write_is_counted_and_never_raises(self, caplog):
        db = MetricsDB(fail=True)
        executor = make_metrics_executor(db=db)

        with caplog.at_level("WARNING"):
            assert executor._flush_metrics(force=True) is False

        assert executor.metrics_flush_errors_total == 1
        assert executor.metrics_flushes_total == 0
        assert "Failed to persist live executor metrics" in caplog.text

    def test_a_delivered_heartbeat_persists_the_snapshot(self):
        clock = FakeClock()
        db = MetricsDB()
        notifier = FakeNotifier()
        executor = make_metrics_executor(
            db=db,
            clock=clock,
            notifier=notifier,
            heartbeat_interval_seconds=60,
            metrics_flush_seconds=3600,
        )

        assert executor._maybe_send_heartbeat() is True
        assert len(db.metrics_writes) == 1
        assert db.payloads[0]["heartbeats_sent_total"] == 1

        clock.advance(60)
        assert executor._maybe_send_heartbeat() is True
        assert len(db.metrics_writes) == 2
        assert db.payloads[-1]["heartbeats_sent_total"] == 2

    def test_an_undelivered_heartbeat_does_not_persist(self):
        db = MetricsDB()
        notifier = FakeNotifier(result=False)
        executor = make_metrics_executor(
            db=db,
            notifier=notifier,
            heartbeat_interval_seconds=60,
            metrics_flush_seconds=3600,
        )

        assert executor._maybe_send_heartbeat() is False
        assert db.metrics_writes == []
        assert executor.metrics_flushes_total == 0

    def test_a_kill_switch_transition_persists_the_snapshot(self):
        db = MetricsDB(app_settings={"trailing_kill_switch": True})
        executor = make_metrics_executor(db=db, metrics_flush_seconds=3600)

        executor._refresh_kill_switch()

        assert db.keys == ["live_executor_metrics"]
        assert db.payloads[0]["kill_switch"] is True
        assert db.payloads[0]["kill_switch_source"] == "app_settings"

        # A steady state neither alerts nor writes.
        executor._refresh_kill_switch()
        assert len(db.metrics_writes) == 1

    def test_graceful_shutdown_persists_the_final_snapshot(self):
        db = MetricsDB()
        executor = make_metrics_executor(db=db, metrics_flush_seconds=3600)
        executor.config["close_positions_on_shutdown"] = False
        executor.iterations_total = 12
        executor.errors_total = 1

        executor.shutdown()

        assert len(db.metrics_writes) == 1
        payload = db.payloads[0]
        assert payload["iterations_total"] == 12
        assert payload["errors_total"] == 1
        assert executor.metrics_flushes_total == 1

# --- Issue #177 step 6: GET /api/live-trading/metrics (decision D1, reader) ----


METRICS_NOW = datetime(2026, 8, 31, 12, 0, 0)


def metrics_snapshot(**overrides) -> dict:
    """A snapshot shaped exactly like ``LiveExecutor._metrics_payload()``.

    Written out literally instead of being generated from the executor: it is
    the published contract of the reader, so a key the writer adds must surface
    as a failing test here rather than as a silently missing panel value.
    """
    data = {
        # provenance
        "schema_version": 1,
        "persisted_at": (METRICS_NOW - timedelta(seconds=30)).isoformat(
            timespec="seconds"
        ),
        # broker contour (#178)
        "broker_contour": "sandbox",
        # loop
        "strategy": "active-strategy",
        "tickers": ["GAZP", "SBER"],
        "ticker_count": 2,
        "iterations_total": 120,
        "errors_total": 3,
        "errors_consecutive": 0,
        "max_consecutive_errors": 5,
        "last_error_at": None,
        # heartbeat - persisted as str(datetime), not ISO (json default=str)
        "heartbeat_ts": str(METRICS_NOW - timedelta(seconds=60)),
        "heartbeat_interval_seconds": 3600.0,
        "heartbeat_stale_seconds": 300.0,
        "heartbeats_sent_total": 4,
        # kill switch
        "kill_switch": False,
        "kill_switch_source": "startup",
        # global entry kill switch (#178)
        "live_kill_switch": False,
        "live_kill_switch_source": "app_settings",
        "kill_switch_rejections_total": 0,
        # broker-side protection (#175)
        "stops_armed_total": 6,
        "stop_amend_total": 2,
        "stop_amend_failed_total": 0,
        "protection_failed_total": 1,
        "protection_failed_positions": ["SBER"],
        "invariant_violations_total": 0,
        "oco_orphans_cancelled_total": 1,
        "oco_checks_pending": 0,
        "fills_reconciled_total": 3,
        # equity and the daily drawdown gate (#176)
        "equity_snapshots_total": 40,
        "equity_snapshot_errors_total": 0,
        "equity_snapshot_skipped_total": 1,
        "equity_snapshot_enabled": True,
        # how that equity was measured (#191)
        "equity_last_cash_rub": 45000.0,
        "equity_last_market_value_rub": 15000.0,
        "holdings_unpriced_total": 2,
        "holdings_stale_priced_total": 1,
        "unpriced_holding_tickers": ["GAZP"],
        "stale_priced_holding_tickers": ["LKOH"],
        "last_equity_rub": 60000.0,
        "last_drawdown_pct": 1.25,
        "last_peak_equity_rub": 60760.0,
        "last_equity_session_key": "2026-08-31",
        "risk_breach_active": False,
        "risk_breach_session_key": None,
        "risk_breach_total": 0,
        "risk_breach_resets_total": 0,
        "risk_gate_rejections_total": 0,
        "position_size_rejections_total": 1,
        "max_daily_loss_pct": 2.0,
        "max_position_size": 100000.0,
        "max_open_positions": 5,
        # alerting (#177)
        "notifier_configured": True,
        "telegram_alerts_enabled": True,
        "alerts_attempted_total": 9,
        "alerts_sent_total": 7,
        "alerts_failed_total": 2,
        "alerts_suppressed_total": 1,
        "alerts_skipped_total": 0,
        "metrics_flushes_total": 12,
        "metrics_flush_errors_total": 0,
    }
    data.update(overrides)
    return data


def metrics_db(
    snapshot=...,
    *,
    kill_switch=None,
    global_kill_switch=False,
    positions=None,
    **kwargs,
):
    """FakeDB serving the metrics row (and optionally the kill-switch rows).

    ``global_kill_switch`` defaults to ``False`` - the state of a database
    migrated with 20260928_001 (Issue #178). Pass ``None`` to simulate the
    unmigrated case, where the reader must fail safe to "active".
    """
    settings = dict(kwargs.pop("app_settings", None) or {})
    if snapshot is not ...:
        settings["live_executor_metrics"] = snapshot
    if kill_switch is not None:
        settings["trailing_kill_switch"] = kill_switch
    if global_kill_switch is not None:
        settings["live_kill_switch"] = global_kill_switch
    else:
        # Simulate the unmigrated database: no seeded row either, so the reader
        # has to fail safe to "active".
        kwargs.setdefault("seeded_kill_switch", None)
    return MetricsApiDB(
        app_settings=settings,
        active=positions if positions is not None else pd.DataFrame(),
        **kwargs,
    )


def open_position(**overrides) -> dict:
    """One ``trading.live_positions`` row as the metrics endpoint sees it."""
    data = {
        "id": 41,
        "ticker": "SBER",
        "status": "open",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "current_stop_price": 96.5,
        "broker_stop_id": "stop-1",
        "trailing_enabled": True,
        "step_reached": 2,
        "size_lots": 10,
        "strategy_name": "active-strategy",
        "updated_at": pd.Timestamp("2026-08-31 11:59:00"),
    }
    data.update(overrides)
    return data


class MetricsApiDB(FakeDB):
    """FakeDB that can also fail one specific read, like a missing table."""

    def __init__(self, *, settings_error=None, positions_error=None, **kwargs):
        super().__init__(**kwargs)
        self.settings_error = settings_error
        self.positions_error = positions_error

    def select(self, query, params=None):
        normalized = " ".join(query.split())
        if "FROM trading.app_settings" in normalized and self.settings_error:
            raise self.settings_error
        if "FROM trading.live_positions" in normalized and self.positions_error:
            raise self.positions_error
        return super().select(query, params)


def read_metrics(db, monkeypatch, *, now=METRICS_NOW, alerting=None):
    """Run the endpoint body against a fake db (no HTTP round trip needed)."""
    from app.api import live_trading_jobs as api

    monkeypatch.setattr(api, "_get_db", lambda: db)
    if alerting is not None:
        config = dict(get_live_alerting_config())
        config.update(alerting)
        monkeypatch.setattr(api, "get_live_alerting_config", lambda: dict(config))
    return api._metrics_endpoint_payload(now=now)


def metrics_client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


class TestMetricsEndpointContract:
    """What the reader publishes for a healthy persisted snapshot."""

    def test_a_persisted_snapshot_is_served_whole(self, monkeypatch):
        payload = read_metrics(metrics_db(metrics_snapshot()), monkeypatch)

        assert payload["available"] is True
        assert payload["reason"] is None
        assert payload["error"] is None
        assert payload["state"] == "running"
        assert payload["generated_at"] == METRICS_NOW.isoformat(timespec="seconds")
        # Provenance: where the numbers came from and how old they are.
        assert payload["source"] == {
            "table": "trading.app_settings",
            "key": "live_executor_metrics",
            "row_updated_at": None,
            "schema_version": 1,
            "persisted_at": (METRICS_NOW - timedelta(seconds=30)).isoformat(
                timespec="seconds"
            ),
            "age_seconds": 30.0,
            "flush_stale": False,
            # Issue #178: the contour that produced the numbers.
            "broker_contour": "sandbox",
        }
        assert payload["loop"]["strategy"] == "active-strategy"
        assert payload["loop"]["tickers"] == ["GAZP", "SBER"]
        assert payload["loop"]["ticker_count"] == 2
        assert payload["loop"]["iterations_total"] == 120
        assert payload["loop"]["errors_total"] == 3
        assert payload["protection"]["stops_armed_total"] == 6
        assert payload["protection"]["protection_failed_positions"] == ["SBER"]
        assert payload["protection"]["oco_orphans_cancelled_total"] == 1
        assert payload["risk"]["last_drawdown_pct"] == 1.25
        assert payload["risk"]["last_equity_session_key"] == "2026-08-31"
        assert payload["risk"]["position_size_rejections_total"] == 1
        # Issue #191: the measurement behind the drawdown is published too, so a
        # phantom breach can be recognised from the panel without log diving.
        assert payload["risk"]["last_cash_rub"] == 45000.0
        assert payload["risk"]["last_market_value_rub"] == 15000.0
        assert payload["risk"]["holdings_unpriced_total"] == 2
        assert payload["risk"]["holdings_stale_priced_total"] == 1
        assert payload["risk"]["unpriced_holding_tickers"] == ["GAZP"]
        assert payload["risk"]["stale_priced_holding_tickers"] == ["LKOH"]
        # The panel reads the limits from the API instead of hardcoding them.
        assert payload["risk"]["limits"]["max_daily_loss_pct"] == 2.0
        assert payload["risk"]["snapshot_limits"]["max_open_positions"] == 5
        assert payload["alerting"]["alerts_sent_total"] == 7
        assert payload["alerting"]["alerts_failed_total"] == 2
        assert payload["alerting"]["notifier_configured"] is True
        assert payload["alerting"]["metrics_flushes_total"] == 12
        assert payload["alerting"]["flush_interval_seconds"] == 300.0
        assert payload["alerting"]["debounce_seconds"] == 300.0

    def test_no_persisted_field_is_left_unpublished(self, monkeypatch):
        """Every snapshot key lands in a section - nothing hides in ``extra``."""
        payload = read_metrics(metrics_db(metrics_snapshot()), monkeypatch)

        assert payload["extra"] == {}

    def test_unknown_snapshot_fields_are_published_in_extra(self, monkeypatch):
        """A field a future executor adds is served, not dropped."""
        snapshot = metrics_snapshot(future_field=5, nested={"a": 1})

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["available"] is True
        assert payload["extra"] == {"future_field": 5, "nested": {"a": 1}}

    def test_the_documented_shape_matches_the_published_fields(self):
        from app.api import live_trading_jobs as api

        assert set(metrics_snapshot()) == set(api._METRICS_SNAPSHOT_FIELDS)

    def test_the_reader_covers_every_key_the_executor_persists(self, monkeypatch):
        """Anti-drift: the step 5 writer and the step 6 reader share one shape."""
        from app.api import live_trading_jobs as api

        db = MetricsDB()
        executor = make_metrics_executor(db=db)
        executor.heartbeat_ts = executor.now_fn()
        executor.iterations_total = 9
        assert executor._flush_metrics(force=True) is True
        written = db.payloads[0]

        assert set(written) <= set(api._METRICS_SNAPSHOT_FIELDS)

        payload = read_metrics(
            metrics_db(written),
            monkeypatch,
            now=IN_SESSION_NOW + timedelta(seconds=90),
        )

        assert payload["available"] is True
        assert payload["extra"] == {}
        assert payload["loop"]["iterations_total"] == 9
        assert payload["state"] == "running"
        assert payload["heartbeat"]["known"] is True
        assert payload["heartbeat"]["age_seconds"] == 90.0
        assert payload["heartbeat"]["stale"] is False
        assert payload["source"]["age_seconds"] == 90.0


class TestMetricsEndpointDegradation:
    """A monitoring endpoint stays up while the thing it watches is down."""

    def test_a_missing_row_is_reported_not_raised(self, monkeypatch):
        payload = read_metrics(metrics_db(), monkeypatch)

        assert payload["available"] is False
        assert payload["reason"] == "no_snapshot"
        assert payload["error"] is None
        assert payload["state"] == "unknown"
        assert payload["heartbeat"]["known"] is False
        assert payload["heartbeat"]["age_seconds"] is None
        assert payload["kill_switch"]["active"] is False
        assert payload["positions"]["available"] is True
        assert payload["positions"]["open_total"] == 0

    def test_a_malformed_payload_is_reported_not_raised(self, monkeypatch):
        payload = read_metrics(metrics_db("{not json"), monkeypatch)

        assert payload["available"] is False
        assert payload["reason"] == "malformed_snapshot"
        assert "not a readable JSON object" in payload["error"]
        assert payload["state"] == "unknown"

    def test_a_json_array_is_malformed_not_a_snapshot(self, monkeypatch):
        payload = read_metrics(metrics_db("[1, 2]"), monkeypatch)

        assert payload["available"] is False
        assert payload["reason"] == "malformed_snapshot"

    def test_an_empty_object_is_reported_as_empty(self, monkeypatch):
        payload = read_metrics(metrics_db({}), monkeypatch)

        assert payload["available"] is False
        assert payload["reason"] == "empty_snapshot"

    def test_a_null_value_is_reported_as_empty(self, monkeypatch):
        payload = read_metrics(metrics_db(None), monkeypatch)

        assert payload["available"] is False
        assert payload["reason"] == "empty_snapshot"

    def test_a_text_jsonb_cell_is_accepted(self, monkeypatch):
        """psycopg2 hands back a dict, a text column would hand back a string."""
        payload = read_metrics(metrics_db(json.dumps(metrics_snapshot())), monkeypatch)

        assert payload["available"] is True
        assert payload["loop"]["iterations_total"] == 120

    def test_a_partial_snapshot_falls_back_to_the_shipped_windows(self, monkeypatch):
        """An older schema version must not turn into a 500 or a wrong claim."""
        payload = read_metrics(
            metrics_db({"schema_version": 1, "iterations_total": 4}), monkeypatch
        )

        assert payload["available"] is True
        assert payload["state"] == "no_heartbeat"
        assert payload["loop"]["iterations_total"] == 4
        assert payload["loop"]["tickers"] == []
        assert payload["loop"]["ticker_count"] == 0
        assert payload["heartbeat"]["stale_seconds"] == 300.0
        assert payload["heartbeat"]["interval_seconds"] == 3600.0

    def test_the_snapshot_key_comes_from_the_alerting_config(self, monkeypatch):
        """One source for the key: writer and reader cannot drift apart."""
        db = MetricsApiDB(
            app_settings={"live_metrics_canary": metrics_snapshot()},
            active=pd.DataFrame(),
        )

        payload = read_metrics(
            db, monkeypatch, alerting={"metrics_key": "live_metrics_canary"}
        )

        assert payload["available"] is True
        assert payload["source"]["key"] == "live_metrics_canary"
        # The default key must never be consulted for a custom contract name.
        assert all(
            params != ("live_executor_metrics",) for _, params in db.select_calls
        )

    def test_a_stale_snapshot_is_flagged_by_the_flush_window(self, monkeypatch):
        """``flush_stale`` tells 'the writer stopped' from 'the loop is idle'."""
        old = metrics_snapshot(
            persisted_at=(METRICS_NOW - timedelta(seconds=900)).isoformat(
                timespec="seconds"
            )
        )

        payload = read_metrics(metrics_db(old), monkeypatch)

        assert payload["source"]["age_seconds"] == 900.0
        assert payload["source"]["flush_stale"] is True

    def test_a_repr_snapshot_from_the_dataframe_path_is_readable(self, monkeypatch):
        """``to_dataframe()`` stringifies the JSONB cell - that is not corruption.

        ``SelectResult.to_dataframe`` normalises the column with ``astype(str)``,
        so against the real database the snapshot arrives as
        ``"{'schema_version': 1, ...}"`` (single quotes, ``None``/``True``). Reading
        that as malformed pinned ``available=false`` in the container while every
        fake stayed green - only the smoke run caught it.
        """
        payload = read_metrics(metrics_db(str(metrics_snapshot())), monkeypatch)

        assert payload["available"] is True
        assert payload["reason"] is None
        assert payload["error"] is None
        assert payload["state"] == "running"
        assert payload["loop"]["iterations_total"] == 120
        assert payload["loop"]["tickers"] == ["GAZP", "SBER"]
        assert payload["protection"]["stops_armed_total"] == 6
        assert payload["risk"]["limits"]["max_daily_loss_pct"] == 2.0
        assert payload["alerting"]["notifier_configured"] is True
        assert payload["heartbeat"]["age_seconds"] == 60.0
        assert payload["source"]["age_seconds"] == 30.0
        assert payload["extra"] == {}

    def test_the_executor_snapshot_survives_the_dataframe_round_trip(self, monkeypatch):
        """End to end: what ``_flush_metrics`` wrote is what the endpoint serves."""
        db = MetricsDB()
        executor = make_metrics_executor(db=db)
        executor.iterations_total = 7
        executor.heartbeat_ts = executor.now_fn()
        assert executor._flush_metrics(force=True) is True

        payload = read_metrics(
            metrics_db(str(db.payloads[0])),
            monkeypatch,
            now=IN_SESSION_NOW + timedelta(seconds=45),
        )

        assert payload["available"] is True
        assert payload["state"] == "running"
        assert payload["loop"]["iterations_total"] == 7
        assert payload["heartbeat"]["known"] is True
        assert payload["heartbeat"]["age_seconds"] == 45.0
        assert payload["source"]["age_seconds"] == 45.0

    def test_a_broken_repr_is_still_malformed(self, monkeypatch):
        """The literal fallback must not turn garbage into a snapshot."""
        payload = read_metrics(metrics_db("{'schema_version': }"), monkeypatch)

        assert payload["available"] is False
        assert payload["reason"] == "malformed_snapshot"
        assert payload["state"] == "unknown"


class TestMetricsHeartbeatFreshness:
    """Freshness is derived at read time - a stored age would be a lie."""

    def test_a_fresh_heartbeat_is_not_stale(self, monkeypatch):
        payload = read_metrics(metrics_db(metrics_snapshot()), monkeypatch)

        heartbeat = payload["heartbeat"]
        assert heartbeat["known"] is True
        assert heartbeat["ts"] == (METRICS_NOW - timedelta(seconds=60)).isoformat(
            timespec="seconds"
        )
        assert heartbeat["age_seconds"] == 60.0
        assert heartbeat["stale"] is False
        assert heartbeat["stale_seconds"] == 300.0
        assert heartbeat["interval_seconds"] == 3600.0
        assert heartbeat["sent_total"] == 4
        assert payload["state"] == "running"

    def test_a_heartbeat_older_than_the_window_is_stale(self, monkeypatch):
        snapshot = metrics_snapshot(
            heartbeat_ts=str(METRICS_NOW - timedelta(seconds=301))
        )

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["heartbeat"]["age_seconds"] == 301.0
        assert payload["heartbeat"]["stale"] is True
        assert payload["state"] == "stale"

    def test_the_window_is_read_from_the_snapshot_not_the_defaults(self, monkeypatch):
        """A retuned executor must not be judged by the shipped window."""
        snapshot = metrics_snapshot(
            heartbeat_ts=str(METRICS_NOW - timedelta(seconds=120)),
            heartbeat_stale_seconds=60.0,
        )

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["heartbeat"]["stale_seconds"] == 60.0
        assert payload["heartbeat"]["stale"] is True

    def test_a_missing_timestamp_is_no_heartbeat_not_stale(self, monkeypatch):
        """'Never ran' and 'stopped running' are different facts."""
        payload = read_metrics(
            metrics_db(metrics_snapshot(heartbeat_ts=None)), monkeypatch
        )

        assert payload["heartbeat"]["known"] is False
        assert payload["heartbeat"]["ts"] is None
        assert payload["heartbeat"]["stale"] is False
        assert payload["state"] == "no_heartbeat"

    def test_an_unparsable_timestamp_degrades_to_unknown(self, monkeypatch):
        payload = read_metrics(
            metrics_db(metrics_snapshot(heartbeat_ts="yesterday")), monkeypatch
        )

        assert payload["heartbeat"]["known"] is False
        assert payload["state"] == "no_heartbeat"

    def test_a_future_timestamp_never_reports_a_negative_age(self, monkeypatch):
        """Clock skew must not produce a negative age on the panel."""
        snapshot = metrics_snapshot(
            heartbeat_ts=str(METRICS_NOW + timedelta(seconds=60))
        )

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["heartbeat"]["age_seconds"] == 0.0
        assert payload["heartbeat"]["stale"] is False

    def test_an_aware_timestamp_is_folded_to_msk(self, monkeypatch):
        """A row written with an offset still compares against MSK now."""
        aware = (METRICS_NOW - timedelta(seconds=60)).replace(
            tzinfo=timezone(timedelta(hours=3))
        )

        payload = read_metrics(
            metrics_db(metrics_snapshot(heartbeat_ts=aware.isoformat())), monkeypatch
        )

        assert payload["heartbeat"]["age_seconds"] == 60.0

    def test_the_iso_and_str_datetime_formats_both_parse(self, monkeypatch):
        """``json.dumps(default=str)`` writes str(datetime); ISO must work too."""
        moment = METRICS_NOW - timedelta(seconds=45)
        for text in (moment.isoformat(), str(moment), moment.isoformat(sep=" ")):
            payload = read_metrics(
                metrics_db(metrics_snapshot(heartbeat_ts=text)), monkeypatch
            )
            assert payload["heartbeat"]["age_seconds"] == 45.0, text


class TestMetricsKillSwitchAndState:
    """The kill switch is an operator lever, so it is read live, not cached."""

    def test_the_live_row_wins_over_the_snapshot(self, monkeypatch):
        """The snapshot may be minutes old; the lever must answer immediately."""
        snapshot = metrics_snapshot(kill_switch=False, kill_switch_source="startup")

        payload = read_metrics(metrics_db(snapshot, kill_switch=True), monkeypatch)

        kill = payload["kill_switch"]
        assert kill["active"] is True
        assert kill["from_live_row"] is True
        assert kill["live_active"] is True
        # The disagreement is published, not smoothed over.
        assert kill["snapshot_active"] is False
        assert kill["snapshot_source"] == "startup"
        assert kill["error"] is None
        assert payload["state"] == "kill_switch"

    def test_the_snapshot_answers_when_the_live_row_is_gone(self, monkeypatch):
        snapshot = metrics_snapshot(kill_switch=True, kill_switch_source="app_settings")

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["kill_switch"]["active"] is True
        assert payload["kill_switch"]["from_live_row"] is False
        assert payload["kill_switch"]["live_active"] is None
        assert payload["state"] == "kill_switch"

    def test_a_text_kill_switch_row_is_understood(self, monkeypatch):
        """``app_settings.value`` is JSONB, but a hand-written 'true' must work."""
        payload = read_metrics(
            metrics_db(metrics_snapshot(), kill_switch="true"), monkeypatch
        )

        assert payload["kill_switch"]["active"] is True

    def test_the_kill_switch_outranks_a_stale_heartbeat(self, monkeypatch):
        snapshot = metrics_snapshot(
            heartbeat_ts=str(METRICS_NOW - timedelta(seconds=4000))
        )

        payload = read_metrics(metrics_db(snapshot, kill_switch=True), monkeypatch)

        assert payload["heartbeat"]["stale"] is True
        assert payload["state"] == "kill_switch"

    def test_the_error_threshold_uses_the_persisted_limit(self, monkeypatch):
        snapshot = metrics_snapshot(errors_consecutive=5, max_consecutive_errors=5)

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["loop"]["errors_consecutive"] == 5
        assert payload["state"] == "error_threshold"

    def test_a_zero_threshold_never_claims_a_breach(self, monkeypatch):
        """``max_consecutive_errors=0`` means 'unset', not 'always broken'."""
        snapshot = metrics_snapshot(errors_consecutive=0, max_consecutive_errors=0)

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["state"] == "running"
        # The shipped policy answers instead of the unusable persisted zero.
        assert payload["loop"]["max_consecutive_errors"] == 5

    def test_an_active_risk_breach_is_surfaced(self, monkeypatch):
        snapshot = metrics_snapshot(
            risk_breach_active=True,
            risk_breach_session_key="2026-08-31",
            risk_breach_total=2,
            last_drawdown_pct=2.5,
            risk_gate_rejections_total=4,
        )

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        risk = payload["risk"]
        assert risk["breach_active"] is True
        assert risk["breach_session_key"] == "2026-08-31"
        assert risk["breach_total"] == 2
        assert risk["gate_rejections_total"] == 4
        assert risk["last_drawdown_pct"] == 2.5
        assert payload["state"] == "risk_breach"

    def test_stale_heartbeat_outranks_a_risk_breach(self, monkeypatch):
        """Liveness first: a dead process is not judged on its last numbers."""
        snapshot = metrics_snapshot(
            risk_breach_active=True,
            heartbeat_ts=str(METRICS_NOW - timedelta(seconds=900)),
        )

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["state"] == "stale"
        assert payload["risk"]["breach_active"] is True

    def test_a_non_finite_number_never_reaches_the_response(self, monkeypatch):
        """JSON has no NaN; a corrupted cell must degrade to null."""
        snapshot = metrics_snapshot(last_equity_rub=float("nan"))

        payload = read_metrics(metrics_db(snapshot), monkeypatch)

        assert payload["risk"]["last_equity_rub"] is None
        assert json.loads(json.dumps(payload))["risk"]["last_equity_rub"] is None


def freeze_metrics_now(monkeypatch):
    """Pin the module clock so read-time ages are deterministic.

    ``now_msk_naive`` doubles as the MSK normaliser for a *given* timestamp
    (``clock=...``), so the frozen version must keep delegating that case to the
    real helper - otherwise every parsed snapshot timestamp collapses onto
    ``METRICS_NOW`` and all ages read as zero.
    """
    from app.api import live_trading_jobs as api

    real_now_msk_naive = api.now_msk_naive

    def frozen_now(clock=None):
        if clock is None:
            return METRICS_NOW
        return real_now_msk_naive(clock=clock)

    monkeypatch.setattr(api, "now_msk_naive", frozen_now)
    return api


class TestMetricsOpenPositions:
    """Stop-arming coverage is the part an operator can still act on."""

    def test_positions_are_split_by_broker_stop(self, monkeypatch):
        positions = pd.DataFrame(
            [
                open_position(id=1, ticker="GAZP", broker_stop_id="stop-1"),
                open_position(id=2, ticker="SBER", broker_stop_id=None),
                open_position(
                    id=3,
                    ticker="LKOH",
                    broker_stop_id="stop-3",
                    trailing_enabled=False,
                ),
            ]
        )

        payload = read_metrics(
            metrics_db(metrics_snapshot(), positions=positions), monkeypatch
        )

        block = payload["positions"]
        assert block["available"] is True
        assert block["error"] is None
        assert block["open_total"] == 3
        assert block["protected_total"] == 2
        assert block["unprotected_total"] == 1
        assert block["unprotected_tickers"] == ["SBER"]
        assert block["trailing_total"] == 2
        assert [item["id"] for item in block["items"]] == [1, 2, 3]
        assert block["items"][1] == {
            "id": 2,
            "ticker": "SBER",
            "entry_price": 100.0,
            "stop_price": 95.0,
            "current_stop_price": 96.5,
            "broker_stop_id": None,
            "protected": False,
            "trailing_enabled": True,
            "step_reached": 2,
            "size_lots": 10,
            "strategy_name": "active-strategy",
            "updated_at": "2026-08-31T11:59:00",
        }

    def test_only_the_gap_is_called_out_but_every_row_is_served(self, monkeypatch):
        """``unprotected_tickers`` is the actionable part, ``items`` the audit."""
        positions = pd.DataFrame(
            [
                open_position(id=1, broker_stop_id="stop-1"),
                open_position(id=2, ticker="GAZP", broker_stop_id=None),
            ]
        )

        payload = read_metrics(
            metrics_db(metrics_snapshot(), positions=positions), monkeypatch
        )

        block = payload["positions"]
        assert block["unprotected_tickers"] == ["GAZP"]
        assert [item["protected"] for item in block["items"]] == [True, False]

    def test_a_null_cell_from_pandas_is_not_read_as_a_stop_id(self, monkeypatch):
        """pandas hands SQL NULL back as NaN/NA - it must still mean "no stop".

        ``str(nan)`` == ``"nan"`` would be published as a broker stop id and the
        unprotected position would look protected, so the reader needs a real
        NULL guard rather than a ``is None`` check.
        """
        positions = pd.DataFrame(
            [
                open_position(
                    id=1,
                    ticker="GAZP",
                    broker_stop_id=float("nan"),
                    trailing_enabled=float("nan"),
                ),
                open_position(id=2, ticker="SBER", broker_stop_id=pd.NA),
                open_position(id=3, ticker="LKOH", broker_stop_id="stop-3"),
            ]
        )

        payload = read_metrics(
            metrics_db(metrics_snapshot(), positions=positions), monkeypatch
        )

        block = payload["positions"]
        assert block["open_total"] == 3
        assert block["protected_total"] == 1
        assert block["unprotected_tickers"] == ["GAZP", "SBER"]
        assert [item["broker_stop_id"] for item in block["items"]] == [
            None,
            None,
            "stop-3",
        ]
        # An unreadable trailing flag is "not trailing", never "trailing".
        assert block["trailing_total"] == 2
        assert block["items"][0]["trailing_enabled"] is False

    def test_a_closed_row_in_a_stale_frame_is_not_counted_as_open(self, monkeypatch):
        """The SELECT says open; a legacy frame must still be filtered here."""
        positions = pd.DataFrame(
            [
                open_position(id=1),
                open_position(id=2, ticker="GAZP", status="closed"),
            ]
        )

        payload = read_metrics(
            metrics_db(metrics_snapshot(), positions=positions), monkeypatch
        )

        assert payload["positions"]["open_total"] == 1
        assert [item["id"] for item in payload["positions"]["items"]] == [1]

    def test_a_position_query_failure_degrades_that_block_only(self, monkeypatch):
        """A broker outage must not take the whole monitoring read down."""
        db = MetricsApiDB(
            app_settings={"live_executor_metrics": metrics_snapshot()},
            positions_error=RuntimeError("broker down"),
        )

        payload = read_metrics(db, monkeypatch)

        assert payload["available"] is True
        assert payload["loop"]["iterations_total"] == 120
        block = payload["positions"]
        assert block["available"] is False
        assert block["open_total"] == 0
        assert block["items"] == []
        assert "broker down" in block["error"]

    def test_positions_are_read_even_without_a_snapshot(self, monkeypatch):
        """Coverage matters most exactly when the executor has gone silent."""
        positions = pd.DataFrame([open_position(id=7, broker_stop_id=None)])

        payload = read_metrics(metrics_db(positions=positions), monkeypatch)

        assert payload["available"] is False
        assert payload["reason"] == "no_snapshot"
        assert payload["positions"]["available"] is True
        assert payload["positions"]["open_total"] == 1
        assert payload["positions"]["unprotected_tickers"] == ["SBER"]

    def test_an_unparsable_cell_degrades_to_a_default_not_a_500(self, monkeypatch):
        """One corrupt row must not hide the rest of the coverage gap."""
        broken = open_position(id=8, ticker="GAZP", broker_stop_id=None)
        broken["updated_at"] = "not-a-timestamp"
        broken["size_lots"] = object()
        positions = pd.DataFrame([open_position(id=7, broker_stop_id=None), broken])

        payload = read_metrics(
            metrics_db(metrics_snapshot(), positions=positions), monkeypatch
        )

        block = payload["positions"]
        assert block["open_total"] == 2
        assert block["unprotected_total"] == 2
        row = block["items"][1]
        assert row["id"] == 8
        assert row["updated_at"] is None
        assert row["size_lots"] == 0


class TestMetricsRoute:
    """The HTTP surface: one GET, the whole body, and the single hard failure."""

    def test_the_route_is_registered_as_a_get(self):
        from fastapi import FastAPI

        from app.api import live_trading_jobs as api

        app = FastAPI()
        api.register_routes(app)
        routes = {
            route.path: route.methods
            for route in app.routes
            if getattr(route, "methods", None)
        }

        assert routes.get("/api/live-trading/metrics") == {"GET"}

    def test_the_endpoint_serves_the_whole_payload(self, monkeypatch):
        api = freeze_metrics_now(monkeypatch)
        monkeypatch.setattr(api, "_get_db", lambda: metrics_db(metrics_snapshot()))

        response = metrics_client().get("/api/live-trading/metrics")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "available",
            "reason",
            "error",
            "state",
            "generated_at",
            "source",
            "loop",
            "heartbeat",
            "kill_switch",
            "global_kill_switch",
            "protection",
            "risk",
            "alerting",
            "positions",
            "extra",
        }
        assert body["available"] is True
        assert body["state"] == "running"
        assert body["generated_at"] == METRICS_NOW.isoformat(timespec="seconds")
        assert body["heartbeat"]["age_seconds"] == 60.0
        assert body["kill_switch"]["active"] is False
        # Issue #178: the global entry stop is published next to the trailing one.
        assert body["global_kill_switch"]["active"] is False
        assert body["global_kill_switch"]["found"] is True
        assert body["positions"]["open_total"] == 0
        assert body["extra"] == {}

    def test_a_missing_snapshot_is_a_200_with_a_reason(self, monkeypatch):
        """Monitoring stays up while the thing it monitors is down."""
        api = freeze_metrics_now(monkeypatch)
        monkeypatch.setattr(api, "_get_db", lambda: metrics_db())

        response = metrics_client().get("/api/live-trading/metrics")

        assert response.status_code == 200
        body = response.json()
        assert body["available"] is False
        assert body["reason"] == "no_snapshot"
        assert body["state"] == "unknown"

    def test_an_unreadable_settings_table_is_the_one_hard_failure(self, monkeypatch):
        api = freeze_metrics_now(monkeypatch)
        db = MetricsApiDB(settings_error=RuntimeError("connection refused"))
        monkeypatch.setattr(api, "_get_db", lambda: db)

        response = metrics_client().get("/api/live-trading/metrics")

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "trading.app_settings is not available" in detail
        assert "RuntimeError" in detail

    def test_the_kill_switch_row_reaches_the_response(self, monkeypatch):
        """The operator lever must be visible over HTTP within one request."""
        api = freeze_metrics_now(monkeypatch)
        monkeypatch.setattr(
            api, "_get_db", lambda: metrics_db(metrics_snapshot(), kill_switch=True)
        )

        response = metrics_client().get("/api/live-trading/metrics")

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "kill_switch"
        assert body["kill_switch"]["active"] is True
        assert body["kill_switch"]["from_live_row"] is True

