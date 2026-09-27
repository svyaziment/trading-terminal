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
from datetime import date
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
        assert notifier.messages == [
            "🚨 *Превышен дневной лимит убытка*\n"
            "*Уровень:* `critical`\n"
            "*Просадка:* `10.0000%`\n"
            "*Лимит:* `5.0000%`\n"
            "*Equity:* `90000.00 RUB`\n"
            "*Пик дня:* `100000.00 RUB`\n"
            "*Сессия:* `2026-09-27`\n"
            "*Эффект:* `новые входы заблокированы, стопы сохранены`"
        ]

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
        assert "Sandbox LiveExecutor started" in caplog.text
        assert "Sandbox LiveExecutor stopped cleanly" in caplog.text

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










