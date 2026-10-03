"""Monitoring API for the live contour.

Read-only by design, with one deliberate exception added by Issue #178:
``POST /api/live-trading/kill-switch`` writes the global emergency stop. It is
the only write in this module, it touches a single boolean row of
``trading.app_settings``, and it reads the value back before reporting success.
"""

from __future__ import annotations

import ast
import json
import logging
import math
from datetime import date, datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app.analytics.live_schema import LIVE_KILL_SWITCH_KEY
from app.analytics.moex_session import now_msk_naive
from app.analytics.trading_config import (
    get_live_alerting_config,
    get_live_risk_bounds,
    get_live_risk_config,
    get_live_trading_config,
)
from app.api.paper_trading_jobs import _json_safe
from app.db.db_manager import DBManager


logger = logging.getLogger(__name__)

TF_MAP = {"1h": "hour", "1d": "day", "1w": "week"}

#: Max length of the audit note accepted by the kill-switch endpoint. It is
#: logged, not stored: ``trading.app_settings`` keeps one boolean per key so the
#: executor's read stays a single-column select.
KILL_SWITCH_REASON_MAX = 200

# Issue #176: columns of trading.live_equity served to the monitoring panel.
# Listed once so the SELECT and the payload cannot drift apart.
LIVE_EQUITY_COLUMNS = (
    "id",
    "timestamp",
    "session_key",
    "equity_rub",
    "cash_rub",
    "market_value_rub",
    "realized_pnl_rub",
    "unrealized_pnl_rub",
    "peak_equity_rub",
    "peak_equity_all_time_rub",
    "drawdown_pct",
    "open_positions",
    "risk_breach",
    "account_id",
    "strategy_name",
)

_LIVE_EQUITY_SELECT = ", ".join(LIVE_EQUITY_COLUMNS)


def _equity_unavailable(exc: Exception) -> HTTPException:
    """One actionable error for a missing/unmigrated trading.live_equity."""
    return HTTPException(
        status_code=503,
        detail=(
            "trading.live_equity is not available "
            f"({type(exc).__name__}); run `alembic upgrade head` "
            "(migration 20260927_001_live_equity)"
        ),
    )


def _risk_limits() -> dict:
    """Effective live risk limits, so the panel never hardcodes a number."""
    risk = get_live_risk_config()
    live = get_live_trading_config()
    return {
        "max_daily_loss_pct": risk["max_daily_loss_pct"],
        "max_position_size": risk["max_position_size"],
        "max_open_positions": live["max_open_positions"],
        "equity_snapshot_enabled": risk["equity_snapshot_enabled"],
        "risk_breach_reset_key": risk["risk_breach_reset_key"],
        "bounds": {
            key: list(value) for key, value in get_live_risk_bounds().items()
        },
    }


def _normalize_equity_row(row: dict) -> dict:
    """Stable JSON shape for one live_equity row.

    ``session_key`` is a DATE column, but pandas hands it back as a Timestamp,
    which ``_json_safe`` renders as ``YYYY-MM-DDT00:00:00``. Trimming it keeps
    the published contract a plain date whatever the driver returns.
    """
    safe = _json_safe(row)
    value = safe.get("session_key")
    if isinstance(value, str) and len(value) > 10:
        safe["session_key"] = value[:10]
    return safe


# --- Issue #177 step 6: persisted LiveExecutor metrics ------------------------
#
# The executor runs in its own OS process, so FastAPI cannot read its memory -
# that is exactly why this endpoint exists. Decision D1 keeps the snapshot in one
# JSONB row of ``trading.app_settings``; this block reads that row, derives the
# freshness a stored snapshot cannot carry (age of the heartbeat) and adds the
# live position/protection view.
#
# The key comes from ``LIVE_ALERTING.metrics_key`` - the same single source the
# writer uses - and ``app.analytics.live_executor`` is deliberately not imported
# here: the web layer must not depend on the trading-loop module.

#: Reason codes published instead of metrics when the row cannot be served.
LIVE_METRICS_REASON_NO_SNAPSHOT = "no_snapshot"
LIVE_METRICS_REASON_MALFORMED = "malformed_snapshot"
LIVE_METRICS_REASON_EMPTY = "empty_snapshot"

#: Truth words accepted for a JSONB boolean. Same set as the executor's
#: ``_coerce_bool`` so a hand-written ``'true'`` row means the same on both
#: sides of the table.
_METRICS_TRUTH_WORDS = ("1", "true", "yes", "on")

#: Open-position columns behind the "protected / unprotected" split.
LIVE_METRICS_POSITION_COLUMNS = (
    "id",
    "ticker",
    "status",
    "entry_price",
    "stop_price",
    "current_stop_price",
    "broker_stop_id",
    "trailing_enabled",
    "step_reached",
    "size_lots",
    "strategy_name",
    "updated_at",
)

_LIVE_METRICS_POSITION_SELECT = ", ".join(LIVE_METRICS_POSITION_COLUMNS)


def _metrics_key() -> str:
    """``trading.app_settings`` key holding the snapshot (decision D1)."""
    return str(get_live_alerting_config()["metrics_key"])


def _metrics_unavailable(exc: Exception) -> HTTPException:
    """One actionable error for an unreadable ``trading.app_settings``."""
    return HTTPException(
        status_code=503,
        detail=(
            "trading.app_settings is not available "
            f"({type(exc).__name__}); the live executor metrics snapshot "
            "cannot be read"
        ),
    )


def _metrics_is_missing(value: Any) -> bool:
    """True for SQL NULL exactly as pandas hands it back (None / NaN / NA / NaT).

    ``pandas`` turns a NULL text cell into ``NaN`` (object or float columns) or
    ``pd.NA`` (the string dtype). Without this check ``str(nan)`` becomes the
    published value, so a position with *no* broker stop would be reported as
    protected - the opposite of what this panel exists for.
    """
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    if isinstance(
        value, (str, bool, int, list, tuple, dict, set, datetime, date)
    ):
        return False
    try:
        differs = value != value  # noqa: PLR0124 - NaN is the only such scalar
    except Exception:  # noqa: BLE001 - an exotic scalar must never break a read
        return False
    if isinstance(differs, bool):
        return differs
    try:
        return bool(differs)
    except (TypeError, ValueError):
        # pandas' NA stays NA through comparisons: missing, not "False".
        return True


def _metrics_int(value: Any, default: int = 0) -> int:
    if _metrics_is_missing(value):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return int(number) if math.isfinite(number) else default


def _metrics_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if _metrics_is_missing(value):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _metrics_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if _metrics_is_missing(value):
        return default
    if isinstance(value, str):
        return value.strip().lower() in _METRICS_TRUTH_WORDS
    return bool(value)


def _metrics_text(value: Any) -> Optional[str]:
    if _metrics_is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _metrics_str_list(value: Any) -> list:
    if isinstance(value, (list, tuple)):
        return sorted({str(item) for item in value})
    return []


def _metrics_datetime(value: Any) -> Optional[datetime]:
    """Parse a persisted timestamp into a naive MSK datetime.

    The snapshot is written with ``json.dumps(..., default=str)``, so
    ``persisted_at`` is ISO (``2026-09-27T12:19:02``) while ``heartbeat_ts`` is
    ``str(datetime)`` (``2026-09-27 12:19:02.123456``). Both must parse, an
    unparseable value must yield ``None`` instead of a 500, and an aware
    datetime is folded to MSK by the same helper the executor uses.
    """
    if _metrics_is_missing(value):
        # NaT is a datetime subclass, so the NULL guard has to come first.
        return None
    if isinstance(value, datetime):
        return now_msk_naive(clock=lambda: value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return now_msk_naive(clock=lambda: parsed)
    return None


def _metrics_iso(value: Any) -> Optional[str]:
    moment = _metrics_datetime(value)
    return moment.isoformat(timespec="seconds") if moment else None


def _metrics_age_seconds(moment: Optional[datetime], now: datetime) -> Optional[float]:
    """Seconds between a persisted timestamp and ``now``, never negative."""
    if moment is None:
        return None
    return round(max(0.0, (now - moment).total_seconds()), 3)


def _loads_metrics_text(text: str) -> Any:
    """JSON first, then the Python ``repr`` that ``to_dataframe()`` produces.

    psycopg2 hands a JSONB cell back as a dict, and ``SelectResult.to_dataframe``
    normalises text-ish columns with ``astype(str)`` - so in the container the
    snapshot reaches this reader as ``"{'schema_version': 1}"``: single quotes,
    ``None``/``True`` instead of ``null``/``true``. Calling that corrupted would
    pin ``available=false`` forever in the only environment that matters, so
    ``ast.literal_eval`` is the fallback: it parses literals and executes nothing.
    """
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return None


def _parse_metrics_value(value: Any) -> tuple[Optional[dict], Optional[str]]:
    """JSONB cell -> ``(snapshot, reason)``.

    The cell arrives as a dict (psycopg2), as a JSON string (text/legacy driver
    path) or as the ``repr`` of that dict (DataFrame normalisation) - see
    :func:`_loads_metrics_text`. A row that is none of those degrades to
    ``available=false`` with a reason instead of breaking the endpoint.
    """
    if value is None:
        return None, LIVE_METRICS_REASON_EMPTY
    if isinstance(value, dict):
        return (value, None) if value else (None, LIVE_METRICS_REASON_EMPTY)
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None, LIVE_METRICS_REASON_MALFORMED
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, LIVE_METRICS_REASON_EMPTY
        parsed = _loads_metrics_text(text)
        if isinstance(parsed, dict) and parsed:
            return parsed, None
        return None, LIVE_METRICS_REASON_MALFORMED
    return None, LIVE_METRICS_REASON_MALFORMED


def _metrics_heartbeat(snapshot: dict, now: datetime, alerting: dict) -> dict:
    """Heartbeat freshness, derived at read time (never stored - it would age).

    ``stale`` is only true when a heartbeat timestamp exists and is older than
    the staleness window; ``known=false`` means the executor never completed a
    cycle, which :func:`_metrics_state` reports as ``no_heartbeat``.
    """
    ts = _metrics_datetime(snapshot.get("heartbeat_ts"))
    stale_seconds = _metrics_float(snapshot.get("heartbeat_stale_seconds"), 0.0) or 0.0
    if stale_seconds <= 0:
        # An executor started without the alerting config persists 0; fall back
        # to the shipped window so the flag stays meaningful.
        stale_seconds = (
            _metrics_float(alerting.get("heartbeat_stale_seconds"), 0.0) or 0.0
        )
    interval = _metrics_float(snapshot.get("heartbeat_interval_seconds"), 0.0) or 0.0
    if interval <= 0:
        interval = (
            _metrics_float(alerting.get("heartbeat_interval_seconds"), 0.0) or 0.0
        )
    age = _metrics_age_seconds(ts, now)
    return {
        "known": ts is not None,
        "ts": ts.isoformat(timespec="seconds") if ts else None,
        "age_seconds": age,
        "stale": bool(ts is not None and age is not None and age >= stale_seconds),
        "stale_seconds": stale_seconds,
        "interval_seconds": interval,
        "sent_total": _metrics_int(snapshot.get("heartbeats_sent_total")),
    }


def _metrics_kill_switch(db: DBManager, snapshot: dict) -> dict:
    """Kill switch from the live row, falling back to the snapshot.

    The snapshot is up to ``metrics_flush_seconds`` old while
    ``trailing_kill_switch`` is the operator's lever, so the endpoint reads the
    same row the executor reads and publishes both values: a difference between
    them must be visible, not silently smoothed over.
    """
    live_found = False
    live_active: Optional[bool] = None
    live_error: Optional[str] = None
    try:
        frame = db.select(
            """
            SELECT value FROM trading.app_settings
            WHERE key = %s
            """,
            ("trailing_kill_switch",),
        ).to_dataframe()
        if not frame.empty:
            live_found = True
            live_active = _metrics_bool(frame.to_dict("records")[0].get("value"))
    except Exception as exc:  # noqa: BLE001 - the snapshot value still answers
        live_error = f"{type(exc).__name__}: {exc}"
    snapshot_active = _metrics_bool(snapshot.get("kill_switch"))
    return {
        "active": live_active if live_found else snapshot_active,
        "from_live_row": live_found,
        "live_active": live_active,
        "snapshot_active": snapshot_active,
        "snapshot_source": _metrics_text(snapshot.get("kill_switch_source")),
        "error": live_error,
    }


def _metrics_global_kill_switch(db: DBManager, snapshot: dict) -> dict:
    """GLOBAL entry kill switch (Issue #178), read live with a snapshot fallback.

    Same shape as :func:`_metrics_kill_switch` - the row the executor reads is the
    row the panel shows - with one difference that matters: an absent or
    unreadable ``live_kill_switch`` row is reported as **active**, because that is
    exactly what the executor does (fail-safe, decision D2). ``found`` tells the
    operator whether ``active`` came from a stored ``true`` or from that rule, and
    ``reason`` names the rule that applied.
    """
    found = False
    live_active: Optional[bool] = None
    error: Optional[str] = None
    try:
        frame = db.select(
            """
            SELECT value FROM trading.app_settings
            WHERE key = %s
            """,
            (LIVE_KILL_SWITCH_KEY,),
        ).to_dataframe()
        if not frame.empty:
            found = True
            live_active = _metrics_bool(frame.to_dict("records")[0].get("value"))
    except Exception as exc:  # noqa: BLE001 - the snapshot value still answers
        error = f"{type(exc).__name__}: {exc}"

    # The snapshot default is True: an executor that never published the field is
    # an old build, and "unknown" must not read as "entries allowed".
    snapshot_active = _metrics_bool(snapshot.get("live_kill_switch"), True)
    if found:
        active = bool(live_active)
        reason = None
    elif error is not None:
        active = True
        reason = "unreadable_row"
    else:
        active = True
        reason = "missing_row"
    return {
        "active": active,
        "found": found,
        "reason": reason,
        "from_live_row": found,
        "live_active": live_active,
        "snapshot_active": snapshot_active,
        "snapshot_source": _metrics_text(snapshot.get("live_kill_switch_source")),
        "rejections_total": _metrics_int(snapshot.get("kill_switch_rejections_total")),
        "key": LIVE_KILL_SWITCH_KEY,
        "error": error,
    }


def _metrics_positions(db: DBManager) -> dict:
    """Open live positions split into protected / unprotected (Issue #177).

    ``broker_stop_id`` is the fact that matters: it is the broker-side stop the
    executor armed (#175). An open position without one is exactly what the
    invariant-violation alert is about, so the panel gets the tickers, not just a
    count. Best effort like the rest of the endpoint: an unreadable
    ``trading.live_positions`` degrades this block only.
    """
    empty = {
        "available": False,
        "error": None,
        "open_total": 0,
        "protected_total": 0,
        "unprotected_total": 0,
        "unprotected_tickers": [],
        "trailing_total": 0,
        "items": [],
    }
    try:
        frame = db.select(
            f"""
            SELECT {_LIVE_METRICS_POSITION_SELECT}
            FROM trading.live_positions
            WHERE status = 'open'
            ORDER BY ticker, id
            """
        ).to_dataframe()
    except Exception as exc:  # noqa: BLE001 - positions must not break metrics
        empty["error"] = f"{type(exc).__name__}: {exc}"
        return empty

    items = []
    for row in frame.to_dict("records"):
        if _metrics_text(row.get("status")) != "open":
            continue  # a stale/legacy frame may still carry closed rows
        stop_id = _metrics_text(row.get("broker_stop_id"))
        items.append(
            {
                "id": _metrics_int(row.get("id")),
                "ticker": _metrics_text(row.get("ticker")),
                "entry_price": _metrics_float(row.get("entry_price")),
                "stop_price": _metrics_float(row.get("stop_price")),
                "current_stop_price": _metrics_float(row.get("current_stop_price")),
                "broker_stop_id": stop_id,
                "protected": stop_id is not None,
                "trailing_enabled": _metrics_bool(row.get("trailing_enabled")),
                "step_reached": _metrics_int(row.get("step_reached")),
                "size_lots": _metrics_int(row.get("size_lots")),
                "strategy_name": _metrics_text(row.get("strategy_name")),
                "updated_at": _metrics_iso(row.get("updated_at")),
            }
        )
    unprotected = [item for item in items if not item["protected"]]
    return {
        "available": True,
        "error": None,
        "open_total": len(items),
        "protected_total": len(items) - len(unprotected),
        "unprotected_total": len(unprotected),
        "unprotected_tickers": sorted(
            {str(item["ticker"]) for item in unprotected if item["ticker"]}
        ),
        "trailing_total": sum(1 for item in items if item["trailing_enabled"]),
        "items": items,
    }


def _metrics_state(
    snapshot: Optional[dict],
    *,
    heartbeat: dict,
    kill_switch: dict,
    risk_breach: bool,
    global_kill_switch: Optional[dict] = None,
) -> str:
    """One word an operator (or a dashboard tile) can act on.

    Order is deliberate: first "is the process alive at all", then the trading
    states. ``unknown`` means there is no readable snapshot, so nothing about the
    loop can be claimed.

    Issue #178: both kill switches map to the same word - ``kill_switch`` means
    "an operator lever is stopping this contour". Which lever is on is answered
    by the ``kill_switch`` (trailing) and ``global_kill_switch`` (entries)
    sections, so the published state vocabulary stays stable for consumers.
    """
    if snapshot is None:
        return "unknown"
    if kill_switch["active"]:
        return "kill_switch"
    if global_kill_switch is not None and global_kill_switch.get("active"):
        return "kill_switch"
    if not heartbeat["known"]:
        return "no_heartbeat"
    if heartbeat["stale"]:
        return "stale"
    threshold = _metrics_int(snapshot.get("max_consecutive_errors"))
    if threshold > 0 and _metrics_int(snapshot.get("errors_consecutive")) >= threshold:
        return "error_threshold"
    if risk_breach:
        return "risk_breach"
    return "running"


#: Snapshot keys consumed by the sections below. Anything the executor adds
#: later lands in ``extra`` instead of being dropped, so the published contract
#: never loses a field silently.
_METRICS_SNAPSHOT_FIELDS = frozenset(
    {
        # source
        "schema_version",
        "persisted_at",
        "broker_contour",
        # loop
        "strategy",
        "tickers",
        "ticker_count",
        "iterations_total",
        "errors_total",
        "errors_consecutive",
        "max_consecutive_errors",
        "last_error_at",
        # heartbeat
        "heartbeat_ts",
        "heartbeat_interval_seconds",
        "heartbeat_stale_seconds",
        "heartbeats_sent_total",
        # kill switch
        "kill_switch",
        "kill_switch_source",
        # global entry kill switch (#178)
        "live_kill_switch",
        "live_kill_switch_source",
        "kill_switch_rejections_total",
        # broker-side protection (#175)
        "stops_armed_total",
        "stop_amend_total",
        "stop_amend_failed_total",
        "protection_failed_total",
        "protection_failed_positions",
        "invariant_violations_total",
        "oco_orphans_cancelled_total",
        "oco_checks_pending",
        "fills_reconciled_total",
        # account-wide orphan stop sweep (#199)
        "orphan_stop_sweep_enabled",
        "orphan_stop_sweep_runs_total",
        "orphan_stop_candidates",
        "orphan_stops_cancelled_total",
        "orphan_sweep_fail_closed_total",
        # canary live trading (#194)
        "canary_enabled",
        "canary_ticker",
        "canary_max_lots",
        "canary_capped_total",
        "canary_rejections_total",
        "canary_confirmations_total",
        "canary_confirm_retries_total",
        "canary_aborts_total",
        # equity and the daily drawdown gate (#176)
        "risk_breach_active",
        "risk_breach_session_key",
        "risk_breach_total",
        "risk_breach_resets_total",
        "risk_gate_rejections_total",
        "position_size_rejections_total",
        "last_equity_rub",
        "last_drawdown_pct",
        "last_peak_equity_rub",
        "last_equity_session_key",
        "equity_snapshots_total",
        "equity_snapshot_errors_total",
        "equity_snapshot_skipped_total",
        "equity_snapshot_enabled",
        # how the equity above was actually measured (#191)
        "equity_last_cash_rub",
        "equity_last_market_value_rub",
        "holdings_unpriced_total",
        "holdings_stale_priced_total",
        "unpriced_holding_tickers",
        "stale_priced_holding_tickers",
        "max_daily_loss_pct",
        "max_position_size",
        "max_open_positions",
        # alerting (#177)
        "telegram_alerts_enabled",
        "notifier_configured",
        "alerts_attempted_total",
        "alerts_sent_total",
        "alerts_failed_total",
        "alerts_suppressed_total",
        "alerts_skipped_total",
        "metrics_flushes_total",
        "metrics_flush_errors_total",
    }
)


def _metrics_source_section(
    snapshot: dict, *, key: str, row_updated_at: Any, now: datetime
) -> dict:
    """Where the numbers come from, and how old that row is."""
    persisted_at = _metrics_datetime(snapshot.get("persisted_at"))
    return {
        "table": "trading.app_settings",
        "key": key,
        "row_updated_at": _metrics_iso(row_updated_at),
        "schema_version": _metrics_int(snapshot.get("schema_version")),
        "persisted_at": persisted_at.isoformat(timespec="seconds") if persisted_at else None,
        "age_seconds": _metrics_age_seconds(persisted_at, now),
        # Issue #178: which broker contour wrote this snapshot - "sandbox" or
        # "real". An operator reading metrics must never have to guess whether
        # the numbers describe paper-like sandbox trades or real money.
        "broker_contour": _metrics_text(snapshot.get("broker_contour")),
    }


def _metrics_loop_section(snapshot: dict, alerting: dict) -> dict:
    """Iteration and error counters of the executor loop."""
    tickers = _metrics_str_list(snapshot.get("tickers"))
    max_consecutive = _metrics_int(snapshot.get("max_consecutive_errors"))
    if max_consecutive <= 0:
        max_consecutive = _metrics_int(alerting.get("max_consecutive_errors"))
    return {
        "strategy": _metrics_text(snapshot.get("strategy")),
        "tickers": tickers,
        "ticker_count": _metrics_int(snapshot.get("ticker_count"), len(tickers)),
        "iterations_total": _metrics_int(snapshot.get("iterations_total")),
        "errors_total": _metrics_int(snapshot.get("errors_total")),
        "errors_consecutive": _metrics_int(snapshot.get("errors_consecutive")),
        "max_consecutive_errors": max_consecutive,
        "last_error_at": _metrics_iso(snapshot.get("last_error_at")),
    }


def _metrics_protection_section(snapshot: dict) -> dict:
    """Broker-side stop counters and the invariant-violation facts (#175)."""
    return {
        "stops_armed_total": _metrics_int(snapshot.get("stops_armed_total")),
        "stop_amend_total": _metrics_int(snapshot.get("stop_amend_total")),
        "stop_amend_failed_total": _metrics_int(snapshot.get("stop_amend_failed_total")),
        "protection_failed_total": _metrics_int(snapshot.get("protection_failed_total")),
        "protection_failed_positions": _metrics_str_list(
            snapshot.get("protection_failed_positions")
        ),
        "invariant_violations_total": _metrics_int(
            snapshot.get("invariant_violations_total")
        ),
        "oco_orphans_cancelled_total": _metrics_int(
            snapshot.get("oco_orphans_cancelled_total")
        ),
        "oco_checks_pending": _metrics_int(snapshot.get("oco_checks_pending")),
        "fills_reconciled_total": _metrics_int(snapshot.get("fills_reconciled_total")),
        # Issue #199: the account-wide sweep of orphaned broker stops. The OCO
        # counters above only cover the legs of the closes *this* process made;
        # these cover everything else the broker still holds. ``fail_closed_total``
        # is the one an operator must never ignore: it means more orphans than the
        # cap were confirmed and the sweep deliberately cancelled nothing.
        "orphan_stop_sweep_enabled": _metrics_bool(
            snapshot.get("orphan_stop_sweep_enabled")
        ),
        "orphan_stop_sweep_runs_total": _metrics_int(
            snapshot.get("orphan_stop_sweep_runs_total")
        ),
        "orphan_stop_candidates": _metrics_int(snapshot.get("orphan_stop_candidates")),
        "orphan_stops_cancelled_total": _metrics_int(
            snapshot.get("orphan_stops_cancelled_total")
        ),
        "orphan_sweep_fail_closed_total": _metrics_int(
            snapshot.get("orphan_sweep_fail_closed_total")
        ),
    }


def _metrics_risk_section(snapshot: dict) -> dict:
    """Daily drawdown gate state plus the limits the executor actually ran with.

    ``limits`` is the current config, ``snapshot_limits`` is what the executor
    recorded - if they differ, the loop is running on stale limits and the panel
    must show that instead of hiding it.
    """
    return {
        "breach_active": _metrics_bool(snapshot.get("risk_breach_active")),
        "breach_session_key": _metrics_text(snapshot.get("risk_breach_session_key")),
        "breach_total": _metrics_int(snapshot.get("risk_breach_total")),
        "breach_resets_total": _metrics_int(snapshot.get("risk_breach_resets_total")),
        "gate_rejections_total": _metrics_int(snapshot.get("risk_gate_rejections_total")),
        "position_size_rejections_total": _metrics_int(
            snapshot.get("position_size_rejections_total")
        ),
        "last_equity_rub": _metrics_float(snapshot.get("last_equity_rub")),
        "last_drawdown_pct": _metrics_float(snapshot.get("last_drawdown_pct")),
        "last_peak_equity_rub": _metrics_float(snapshot.get("last_peak_equity_rub")),
        "last_equity_session_key": _metrics_text(snapshot.get("last_equity_session_key")),
        # Issue #191: how that equity was measured. During the 28.09 phantom
        # breach the panel showed a drawdown with no way to trace it back to
        # cash and positions, and no signal that the broker could not price part
        # of the portfolio. Publishing the split plus the unpriced / stale-priced
        # tickers turns "the account lost 13%" into "the account lost 13% because
        # two holdings were valued at their purchase price".
        "last_cash_rub": _metrics_float(snapshot.get("equity_last_cash_rub")),
        "last_market_value_rub": _metrics_float(
            snapshot.get("equity_last_market_value_rub")
        ),
        "holdings_unpriced_total": _metrics_int(snapshot.get("holdings_unpriced_total")),
        "holdings_stale_priced_total": _metrics_int(
            snapshot.get("holdings_stale_priced_total")
        ),
        "unpriced_holding_tickers": _metrics_str_list(
            snapshot.get("unpriced_holding_tickers")
        ),
        "stale_priced_holding_tickers": _metrics_str_list(
            snapshot.get("stale_priced_holding_tickers")
        ),
        "equity_snapshots_total": _metrics_int(snapshot.get("equity_snapshots_total")),
        "equity_snapshot_errors_total": _metrics_int(
            snapshot.get("equity_snapshot_errors_total")
        ),
        "equity_snapshot_skipped_total": _metrics_int(
            snapshot.get("equity_snapshot_skipped_total")
        ),
        "limits": _risk_limits(),
        "snapshot_limits": {
            "max_daily_loss_pct": _metrics_float(snapshot.get("max_daily_loss_pct")),
            "max_position_size": _metrics_float(snapshot.get("max_position_size")),
            "max_open_positions": _metrics_int(snapshot.get("max_open_positions")),
            "equity_snapshot_enabled": _metrics_bool(
                snapshot.get("equity_snapshot_enabled"), True
            ),
        },
    }


def _metrics_canary_section(snapshot: dict) -> dict:
    """Issue #194: what the canary mode was allowed to do, and what it refused.

    ``enabled`` comes first because the counters below are meaningless without
    it: a canary loop trades one ticker, one lot, behind two operator
    confirmations, so a panel that showed "3 entries" without the flag would
    look like an ordinary loop that simply trades very little.

    ``capped_total`` is the field that separates "the canary cut the size" from
    "the risk budget was already that small" - the cap is applied to the sizer's
    answer, never to the risk limits. ``rejections_total`` (an operator answered
    anything but yes), ``confirm_retries_total`` (the operator asked to re-read
    the book or re-arm the protection) and ``aborts_total`` (the canary stopped
    the stream, deliberately leaving the open position on its broker stops) are
    the three ways a human answer ended the run.
    """
    return {
        "enabled": _metrics_bool(snapshot.get("canary_enabled")),
        "ticker": _metrics_text(snapshot.get("canary_ticker")),
        "max_lots": _metrics_int(snapshot.get("canary_max_lots")) or None,
        "capped_total": _metrics_int(snapshot.get("canary_capped_total")),
        "rejections_total": _metrics_int(snapshot.get("canary_rejections_total")),
        "confirmations_total": _metrics_int(snapshot.get("canary_confirmations_total")),
        "confirm_retries_total": _metrics_int(
            snapshot.get("canary_confirm_retries_total")
        ),
        "aborts_total": _metrics_int(snapshot.get("canary_aborts_total")),
    }


def _metrics_alerting_section(snapshot: dict, alerting: dict) -> dict:
    """Telegram delivery counters and the throttle windows behind them (#177)."""
    return {
        "telegram_alerts_enabled": _metrics_bool(
            snapshot.get("telegram_alerts_enabled"),
            _metrics_bool(alerting.get("telegram_alerts_enabled"), True),
        ),
        "notifier_configured": _metrics_bool(snapshot.get("notifier_configured")),
        "alerts_attempted_total": _metrics_int(snapshot.get("alerts_attempted_total")),
        "alerts_sent_total": _metrics_int(snapshot.get("alerts_sent_total")),
        "alerts_failed_total": _metrics_int(snapshot.get("alerts_failed_total")),
        "alerts_suppressed_total": _metrics_int(snapshot.get("alerts_suppressed_total")),
        "alerts_skipped_total": _metrics_int(snapshot.get("alerts_skipped_total")),
        "metrics_flushes_total": _metrics_int(snapshot.get("metrics_flushes_total")),
        "metrics_flush_errors_total": _metrics_int(
            snapshot.get("metrics_flush_errors_total")
        ),
        "flush_interval_seconds": _metrics_float(alerting.get("metrics_flush_seconds")),
        "debounce_seconds": _metrics_float(alerting.get("alert_debounce_seconds")),
    }


def _metrics_endpoint_payload(*, now: Optional[datetime] = None) -> dict:
    """Whole response of ``GET /api/live-trading/metrics``.

    Module level (like the equity helpers) so the tests drive it without a
    TestClient and the endpoint keeps exactly one code path.

    A missing or corrupted snapshot is *not* an error: ``available=false`` plus a
    ``reason`` is the contract, because a monitoring endpoint that 500s is worse
    than one that says "I have nothing". An unreadable ``trading.app_settings``
    is the single hard failure - there is nothing left to serve then.
    """
    db = _get_db()
    moment = now if now is not None else now_msk_naive()
    alerting = get_live_alerting_config()
    key = _metrics_key()
    try:
        frame = db.select(
            """
            SELECT value, updated_at FROM trading.app_settings
            WHERE key = %s
            """,
            (key,),
        ).to_dataframe()
    except Exception as exc:  # noqa: BLE001 - unmigrated/unreachable database
        raise _metrics_unavailable(exc) from exc

    row = frame.to_dict("records")[0] if not frame.empty else None
    snapshot: Optional[dict] = None
    reason: Optional[str] = LIVE_METRICS_REASON_NO_SNAPSHOT
    error: Optional[str] = None
    if row is not None:
        snapshot, reason = _parse_metrics_value(row.get("value"))
        if snapshot is None:
            error = "trading.app_settings value is not a readable JSON object"

    snap = snapshot or {}
    heartbeat = _metrics_heartbeat(snap, moment, alerting)
    kill_switch = _metrics_kill_switch(db, snap)
    global_kill_switch = _metrics_global_kill_switch(db, snap)
    risk_breach = _metrics_bool(snap.get("risk_breach_active"))
    source = _metrics_source_section(
        snap,
        key=key,
        row_updated_at=row.get("updated_at") if row else None,
        now=moment,
    )
    flush_interval = _metrics_float(alerting.get("metrics_flush_seconds"), 0.0) or 0.0
    source["flush_stale"] = bool(
        flush_interval > 0
        and source["age_seconds"] is not None
        and source["age_seconds"] > 2 * flush_interval
    )
    return {
        "available": snapshot is not None,
        "reason": None if snapshot is not None else reason,
        "error": error,
        "state": _metrics_state(
            snapshot,
            heartbeat=heartbeat,
            kill_switch=kill_switch,
            risk_breach=risk_breach,
            global_kill_switch=global_kill_switch,
        ),
        "generated_at": moment.isoformat(timespec="seconds"),
        "source": source,
        "loop": _metrics_loop_section(snap, alerting),
        "heartbeat": heartbeat,
        "kill_switch": kill_switch,
        "global_kill_switch": global_kill_switch,
        "protection": _metrics_protection_section(snap),
        "risk": _metrics_risk_section(snap),
        # Issue #194: published as its own section, not folded into ``risk`` -
        # the canary is a mode of the whole contour, and an operator must be able
        # to see "this loop may only ever buy 1 lot of SBER" at a glance.
        "canary": _metrics_canary_section(snap),
        "alerting": _metrics_alerting_section(snap, alerting),
        "positions": _metrics_positions(db),
        # Fields a future executor version adds are published instead of dropped.
        "extra": {
            str(name): _json_safe(value)
            for name, value in snap.items()
            if str(name) not in _METRICS_SNAPSHOT_FIELDS
        },
    }


# --- Issue #178: the global emergency stop ------------------------------------
#
# One boolean row of ``trading.app_settings`` (``live_kill_switch``, seeded by
# migration 20260928_001) is the contract between the operator and the executor:
# the loop re-reads it every iteration, so a flip takes effect within
# ``LIVE_TRADING.check_interval_seconds`` without a restart. This endpoint is the
# supported way to write that row - editing SQL by hand stays possible and means
# the same thing.


class KillSwitchIn(BaseModel):
    """Body of ``POST /api/live-trading/kill-switch``."""

    #: ``true`` blocks every new entry; ``false`` lets the contour trade again.
    enabled: bool
    #: Optional audit note. Logged, not stored (see KILL_SWITCH_REASON_MAX).
    reason: Optional[str] = Field(default=None, max_length=KILL_SWITCH_REASON_MAX)


def _kill_switch_unavailable(exc: Exception) -> HTTPException:
    """One actionable error for an unwritable ``trading.app_settings``."""
    return HTTPException(
        status_code=503,
        detail=(
            "trading.app_settings is not writable "
            f"({type(exc).__name__}); the global kill switch was NOT changed - "
            "run `alembic upgrade head` "
            "(migration 20260928_001_live_trading_kill_switch)"
        ),
    )


def _set_kill_switch_payload(
    enabled: bool,
    reason: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """Write ``live_kill_switch`` and answer with the state read back.

    Module level (like the metrics helpers) so the tests drive it without a
    TestClient and the endpoint keeps exactly one code path.

    The write is an upsert: on a database where the seed row is missing the
    endpoint still works, which is exactly the situation an operator is in when
    they need the switch most. Success is reported only after the value has been
    read back - an unconfirmed emergency stop is worse than an error.
    """
    db = _get_db()
    moment = now if now is not None else now_msk_naive()
    note = (reason or "").strip()[:KILL_SWITCH_REASON_MAX] or None
    try:
        db.execute(
            """
            INSERT INTO trading.app_settings (key, value, updated_at)
            VALUES (%s, %s::jsonb, now())
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (LIVE_KILL_SWITCH_KEY, "true" if enabled else "false"),
        )
    except Exception as exc:  # noqa: BLE001 - reported as 503, never as a 500
        raise _kill_switch_unavailable(exc) from exc

    state = _metrics_global_kill_switch(db, {})
    confirmed = bool(state["found"]) and bool(state["live_active"]) == bool(enabled)
    # An emergency stop is an audit event: who/what/when belongs in the log even
    # when Telegram is not configured.
    log = logger.warning if enabled else logger.info
    log(
        "Global live kill switch set to %s (confirmed=%s reason=%s)",
        "ON" if enabled else "OFF",
        confirmed,
        note or "-",
    )
    return {
        "ok": confirmed,
        "enabled": bool(enabled),
        "confirmed": confirmed,
        "reason": note,
        "kill_switch": state,
        "generated_at": moment.isoformat(timespec="seconds"),
        "effect": (
            "new entries are rejected with reason 'kill_switch'; open positions "
            "keep their broker stops"
            if enabled
            else "new entries are allowed again"
        ),
    }


def _get_db() -> DBManager:
    return DBManager()


def _build_where(
    *,
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    exit_reason: Optional[str] = None,
) -> tuple[str, dict]:
    clauses = []
    params = {}
    if status == "closed":
        # Issue #149: closed includes closed_trailing for trailing stop exits
        clauses.append("status IN ('closed_stop','closed_take','closed_trailing')")
    elif status:
        clauses.append("status = %(status)s")
        params["status"] = status
    if exit_reason:
        clauses.append("exit_reason = %(exit_reason)s")
        params["exit_reason"] = exit_reason
    if ticker:
        clauses.append("ticker = %(ticker)s")
        params["ticker"] = ticker
    if date_from:
        clauses.append("COALESCE(signal_ts, created_at)::date >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        clauses.append("COALESCE(signal_ts, created_at)::date <= %(date_to)s")
        params["date_to"] = date_to
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _latest_prices(db: DBManager, tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    frame = db.select(
        """
        SELECT DISTINCT ON (ticker) ticker, best_bid, best_ask
        FROM trading.online_orderbook_aggregates
        WHERE ticker = ANY(%s)
        ORDER BY ticker, timestamp DESC
        """,
        (tickers,),
    ).to_dataframe()
    prices = {}
    if frame.empty:
        return prices
    for _, row in frame.iterrows():
        for candidate in (row["best_bid"], row["best_ask"]):
            try:
                price = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(price):
                prices[str(row["ticker"])] = price
                break
    return prices


def register_routes(app: FastAPI) -> None:
    @app.get("/api/live-trading/positions")
    def positions(
        status: Optional[str] = None,
        ticker: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        exit_reason: Optional[str] = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        sort_by: str = Query("signal_ts"),
        sort_dir: str = Query("desc"),
    ):
        db = _get_db()
        where, params = _build_where(
            status=status,
            ticker=ticker,
            date_from=date_from,
            date_to=date_to,
            exit_reason=exit_reason,
        )
        sort_columns = {
            "entry_ts": "signal_ts",
            "signal_ts": "signal_ts",
            "exit_ts": "exit_ts",
            "entry_price": "entry_price",
            "exit_price": "exit_price",
            "pnl_rub": "pnl_rub",
            "pnl_pct": "pnl_rub",
            "ticker": "ticker",
            "status": "status",
            "created_at": "created_at",
            "id": "id",
        }
        order_column = sort_columns.get(sort_by, "signal_ts")
        direction = sort_dir if sort_dir in ("asc", "desc") else "desc"

        count_frame = db.select(
            f"SELECT COUNT(*) c FROM trading.live_positions {where}",
            params,
        ).to_dataframe()
        total = int(count_frame.iloc[0]["c"]) if not count_frame.empty else 0
        # Issue #149: trailing fields added (columns from migration 20260915_002)
        frame = db.select(
            f"""
            SELECT id, ticker, signal_ts AS entry_ts, entry_price, exit_ts,
                   exit_price, stop_price, take_price, status, exit_reason,
                   pnl_rub, size_lots, lot_size, strategy_name,
                   created_at, updated_at,
                   trailing_enabled, current_stop_price, step_reached, risk_r
            FROM trading.live_positions {where}
            ORDER BY {order_column} {direction}, id {direction}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {**params, "limit": limit, "offset": offset},
        ).to_dataframe()
        records = frame.to_dict("records") if not frame.empty else []
        open_tickers = sorted({
            str(row["ticker"]) for row in records if row.get("status") == "open"
        })
        prices = _latest_prices(db, open_tickers)

        for row in records:
            entry_price = float(row["entry_price"])
            if row.get("status") == "open":
                current_price = prices.get(str(row["ticker"]))
                row["current_price"] = current_price
                if current_price is not None:
                    row["pnl_rub"] = round(
                        (current_price - entry_price)
                        * int(row["size_lots"])
                        * int(row["lot_size"]),
                        2,
                    )
                    row["pnl_pct"] = round(
                        (current_price / entry_price - 1.0) * 100.0,
                        4,
                    )
                else:
                    row["pnl_pct"] = None
            else:
                row["current_price"] = row.get("exit_price")
                exit_price = row.get("exit_price")
                row["pnl_pct"] = (
                    round((float(exit_price) / entry_price - 1.0) * 100.0, 4)
                    if exit_price is not None and entry_price > 0
                    else None
                )

        return {
            "items": [_json_safe(row) for row in records],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/live-trading/dynamics")
    def dynamics(
        timeframe: str = Query("1d"),
        ticker: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ):
        if timeframe not in TF_MAP:
            raise HTTPException(
                status_code=400,
                detail=f"bad timeframe {timeframe}; use {list(TF_MAP)}",
            )
        where, params = _build_where(
            status="closed",
            ticker=ticker,
            date_from=date_from,
            date_to=date_to,
        )
        # Issue #149: wins include closed_trailing with positive PnL (matches paper convention)
        frame = _get_db().select(
            f"""
            SELECT date_trunc('{TF_MAP[timeframe]}', exit_ts) AS bucket,
                   SUM(pnl_rub) AS pnl_rub, COUNT(*) AS closed,
                   COUNT(*) FILTER (WHERE status='closed_take' OR (status='closed_trailing' AND pnl_rub > 0)) AS wins
            FROM trading.live_positions {where}
              AND exit_ts IS NOT NULL
            GROUP BY bucket ORDER BY bucket
            """,
            params,
        ).to_dataframe()
        points = []
        cumulative = 0.0
        for _, row in frame.iterrows():
            pnl = float(row["pnl_rub"] or 0)
            cumulative += pnl
            points.append({
                "ts": str(row["bucket"]),
                "pnl_rub": round(pnl, 2),
                "cum_pnl_rub": round(cumulative, 2),
                "closed": int(row["closed"] or 0),
                "wins": int(row["wins"] or 0),
            })
        return {
            "timeframe": timeframe,
            "points": points,
            "cum_pnl_rub": round(cumulative, 2),
        }

    # --- Issue #176: live equity and risk-gate monitoring --------------------

    def _equity_current_payload() -> dict:
        """Latest trading.live_equity row plus the limits it is judged against."""
        db = _get_db()
        try:
            frame = db.select(
                f"""
                SELECT {_LIVE_EQUITY_SELECT}
                FROM trading.live_equity
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """
            ).to_dataframe()
        except Exception as exc:  # noqa: BLE001 - unmigrated database
            raise _equity_unavailable(exc) from exc
        snapshot = (
            _normalize_equity_row(frame.to_dict("records")[0]) if not frame.empty else None
        )
        return {
            "available": True,
            "snapshot": snapshot,
            "risk_breach_active": bool((snapshot or {}).get("risk_breach")),
            "risk": _risk_limits(),
        }

    @app.get("/api/live-trading/equity/current")
    def equity_current():
        """Newest live equity snapshot and the effective risk limits."""
        return _equity_current_payload()

    @app.get("/api/live-trading/equity/latest")
    def equity_latest():
        """Alias of ``/equity/current`` kept for the panel's naming."""
        return _equity_current_payload()

    @app.get("/api/live-trading/equity/history")
    def equity_history(
        session_key: Optional[date] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = Query(500, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ):
        """Live equity curve, newest first, filterable by MSK trading day."""
        clauses = []
        params: dict = {}
        if session_key:
            clauses.append("session_key = %(session_key)s")
            params["session_key"] = session_key
        if date_from:
            clauses.append("session_key >= %(date_from)s")
            params["date_from"] = date_from
        if date_to:
            clauses.append("session_key <= %(date_to)s")
            params["date_to"] = date_to
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        db = _get_db()
        try:
            total = int(
                db.select(
                    f"SELECT COUNT(*) AS total FROM trading.live_equity{where}",
                    params,
                ).to_dataframe().iloc[0]["total"]
            )
            frame = db.select(
                f"""
                SELECT {_LIVE_EQUITY_SELECT}
                FROM trading.live_equity{where}
                ORDER BY timestamp DESC, id DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                {**params, "limit": limit, "offset": offset},
            ).to_dataframe()
        except Exception as exc:  # noqa: BLE001 - unmigrated database
            raise _equity_unavailable(exc) from exc
        records = frame.to_dict("records") if not frame.empty else []
        return {
            "items": [_normalize_equity_row(row) for row in records],
            "total": total,
            "limit": limit,
            "offset": offset,
            "risk": _risk_limits(),
        }

    @app.get("/api/live-trading/metrics")
    def live_metrics():
        """Persisted LiveExecutor metrics: heartbeat, kill switch, alerts, risk.

        Issue #177 step 6. The executor runs in another OS process, so this
        serves the throttled snapshot it writes into ``trading.app_settings``
        (decision D1) plus the freshness that can only be derived at read time.
        A missing or corrupted snapshot answers ``200`` with ``available=false``
        and a ``reason``: monitoring must stay up while the thing it monitors is
        down.
        """
        return _metrics_endpoint_payload()

    @app.post("/api/live-trading/kill-switch")
    def set_live_kill_switch(payload: KillSwitchIn):
        """Global emergency stop of the live contour (Issue #178).

        ``{"enabled": true}`` blocks every new entry from the next loop iteration
        (``LIVE_TRADING.check_interval_seconds``, 30 s by default) without a
        restart; ``{"enabled": false}`` lets the contour trade again. Open
        positions are never touched - their broker stops stay armed and nothing
        is flattened.

        The response echoes the state read back from ``trading.app_settings``, so
        ``ok=true`` means the executor will see the new value. An unwritable
        settings table answers ``503`` with the migration to run.
        """
        return _set_kill_switch_payload(payload.enabled, payload.reason)

