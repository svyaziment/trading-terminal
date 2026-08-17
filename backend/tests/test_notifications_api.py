from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import notifications


class FakeNotifier:
    connected = True
    checks = 0

    def __init__(self, config) -> None:
        self.enabled = bool(config.token and config.chat_id)

    def check_connection(self) -> bool:
        type(self).checks += 1
        return type(self).connected


def _reset_cache() -> None:
    notifications._status_cache = None
    notifications._status_cached_at = 0.0
    FakeNotifier.checks = 0


def test_status_reports_missing_configuration_without_probe(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setattr(
        notifications,
        "load_settings",
        lambda: SimpleNamespace(
            telegram=SimpleNamespace(token="", chat_id="")
        ),
    )
    monkeypatch.setattr(notifications, "TelegramNotifier", FakeNotifier)

    result = notifications.get_telegram_status()

    assert result["status"] == "disconnected"
    assert result["configured"] is False
    assert FakeNotifier.checks == 0


def test_status_probes_connection_and_reuses_short_cache(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setattr(
        notifications,
        "load_settings",
        lambda: SimpleNamespace(
            telegram=SimpleNamespace(token="token", chat_id="chat")
        ),
    )
    monkeypatch.setattr(notifications, "TelegramNotifier", FakeNotifier)

    first = notifications.get_telegram_status()
    second = notifications.get_telegram_status()

    assert first["status"] == "connected"
    assert first["configured"] is True
    assert second == first
    assert FakeNotifier.checks == 1


def test_notification_status_route_exposes_safe_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        notifications,
        "get_telegram_status",
        lambda: {
            "status": "connected",
            "configured": True,
            "checked_at": "2026-08-17T17:00:00+00:00",
        },
    )
    app = FastAPI()
    notifications.register_routes(app)

    response = TestClient(app).get("/api/notifications/status")

    assert response.status_code == 200
    assert response.json()["status"] == "connected"
    assert "token" not in response.json()
