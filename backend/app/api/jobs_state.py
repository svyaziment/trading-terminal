"""
Shared in-process state + lock for long background jobs.

Both /api/signals/regenerate and /api/data/refresh use this module so that
only ONE heavy job runs at a time (try_start fails if any job is running).
State is process-local and intentionally not persisted.
"""
from __future__ import annotations
import threading
from datetime import datetime, timezone
from typing import Any, Dict

_LOCK = threading.Lock()
_STATES: Dict[str, Dict[str, Any]] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def snapshot(name: str) -> Dict[str, Any]:
    with _LOCK:
        return dict(_STATES.get(name, {"status": "idle"}))


def all_snapshots() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        return {k: dict(v) for k, v in _STATES.items()}


def is_any_running() -> bool:
    with _LOCK:
        return any(s.get("status") == "running" for s in _STATES.values())


def try_start(name: str, **initial: Any) -> bool:
    """Reserve the single heavy-job slot. Returns False if any job is running."""
    with _LOCK:
        if any(s.get("status") == "running" for s in _STATES.values()):
            return False
        base = {
            "status": "running",
            "started_at": _utcnow(),
            "finished_at": None,
            "error": None,
        }
        base.update(initial)
        _STATES[name] = base
        return True


def update(name: str, **fields: Any) -> None:
    with _LOCK:
        _STATES.setdefault(name, {}).update(fields)


def finish(name: str, status: str = "done", **fields: Any) -> None:
    with _LOCK:
        s = _STATES.setdefault(name, {})
        s.update(fields)
        s["status"] = status
        s["finished_at"] = _utcnow()
