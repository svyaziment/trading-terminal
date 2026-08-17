"""Notification integration status endpoints."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from app.core.config_manager import load_settings
from app.notifications.telegram_notifier import TelegramNotifier

_CACHE_TTL_SEC = 30.0
_cache_lock = threading.Lock()
_status_cache: dict[str, Any] | None = None
_status_cached_at = 0.0


def get_telegram_status() -> dict[str, Any]:
    """Return a short-lived connectivity probe without exposing credentials."""
    global _status_cache, _status_cached_at

    settings = load_settings()
    notifier = TelegramNotifier(settings.telegram)
    if not notifier.enabled:
        return {
            "status": "disconnected",
            "configured": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    now = time.monotonic()
    with _cache_lock:
        if _status_cache is not None and now - _status_cached_at < _CACHE_TTL_SEC:
            return dict(_status_cache)

        connected = notifier.check_connection()
        _status_cache = {
            "status": "connected" if connected else "disconnected",
            "configured": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        _status_cached_at = time.monotonic()
        return dict(_status_cache)


def register_routes(app: FastAPI) -> None:
    @app.get("/api/notifications/status")
    def notification_status() -> dict[str, Any]:
        return get_telegram_status()
