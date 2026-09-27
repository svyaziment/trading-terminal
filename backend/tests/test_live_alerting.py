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
from app.core.config_manager import Settings, TelegramConfig

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

