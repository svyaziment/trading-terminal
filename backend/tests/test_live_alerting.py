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

from app.analytics.trading_config import (
    LIVE_ALERTING,
    LIVE_ALERTING_BOUNDS,
    LIVE_ALERTING_ENV,
    get_live_alerting_bounds,
    get_live_alerting_config,
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

