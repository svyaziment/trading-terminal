"""Sandbox live executor using the unified strategy evaluator.

The executor deliberately uses only :class:`TinkoffSandboxClient`. A take-profit
is submitted as a resting sell limit. A stop-loss is a synthetic trigger: the
sell limit is submitted only after the monitored price reaches the stop, because
a sell limit below the market would execute immediately and is not a stop order.
"""

from __future__ import annotations

import json
import logging
import math
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

import pandas as pd

from app.analytics.live_engine import (
    build_1m_context,
    build_4h_context,
    get_paper_strategy,
)
from app.analytics.live_schema import (
    LiveSchemaError,
    assert_live_schema,
    ensure_live_positions_schema,
)
from app.analytics.moex_session import (
    is_entry_window,
    next_session_open,
    now_msk_naive,
    session_end_for_run,
)
from app.analytics.orderbook_imbalance import (
    calculate_volume_imbalance,
    get_imbalance_threshold,
    passes_imbalance_filter,
)
from app.analytics.position_sizer import calculate_position_size
from app.analytics.strategy_engine import StrategyEvaluator
from app.analytics.trailing_stop import resolve_trailing_stop
from app.analytics.trading_config import (
    get_live_trading_config,
    get_live_trading_universe,
    get_moex_session_config,
    get_orderbook_imbalance_config,
)
from app.broker.tinkoff_sandbox import SandboxAPIError, TinkoffSandboxClient
from app.db.db_manager import DBManager


logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "open")


def _filter_live_tickers(strategy_tickers: list[str], live_universe: list[str]) -> list[str]:
    """Keep strategy tickers that belong to the configured LIVE_UNIVERSE."""
    if not live_universe:
        return list(strategy_tickers)
    allowed = set(live_universe)
    filtered = [ticker for ticker in strategy_tickers if ticker in allowed]
    if filtered:
        if filtered != list(strategy_tickers):
            logger.info("Live universe filter: %s -> %s", strategy_tickers, filtered)
        return filtered
    logger.warning(
        "Paper strategy tickers %s do not intersect live universe %s; using live universe",
        strategy_tickers,
        live_universe,
    )
    return list(live_universe)


def tick_align(
    price: float, increment: float, direction: str = "down"
) -> float:
    """Round price to the nearest valid tick according to min_price_increment.

    Args:
        price: The price to align.
        increment: The minimum price step (min_price_increment from trading.instruments).
        direction: 'down' rounds toward zero (for stop prices), 'up' rounds away from zero
                   (for take prices). Defaults to 'down'.

    Returns:
        The aligned price, or the original price if increment is invalid (<=0 or NaN).

    Examples:
        >>> tick_align(100.37, 0.05, 'down')
        100.35
        >>> tick_align(100.37, 0.05, 'up')
        100.40
        >>> tick_align(95.123, 0.01, 'down')
        95.12
    """
    if increment <= 0 or not math.isfinite(increment):
        return price
    # Use Decimal for precise arithmetic to avoid floating-point drift
    from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
    
    price_d = Decimal(str(price))
    incr_d = Decimal(str(increment))
    
    if direction == "up":
        # Round up: divide, ceil, multiply back
        ticks = (price_d / incr_d).to_integral_value(rounding=ROUND_HALF_UP)
        # Check if we need to round up further
        if ticks * incr_d < price_d:
            ticks += 1
    else:
        # Round down: divide, floor, multiply back
        ticks = (price_d / incr_d).to_integral_value(rounding=ROUND_DOWN)
    
    return float(ticks * incr_d)


def _now_msk_naive() -> datetime:
    return now_msk_naive()


class TokenBucket:
    """Thread-safe token bucket for outbound broker requests."""

    def __init__(
        self,
        rate_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0 or rate_per_second > 10:
            raise ValueError("api_rate_limit must be in the interval (0, 10]")
        self.rate = float(rate_per_second)
        self.capacity = float(rate_per_second)
        self.tokens = self.capacity
        self.clock = clock
        self.sleep_fn = sleep_fn
        self.updated_at = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Wait until one request token is available."""
        while True:
            with self._lock:
                now = self.clock()
                elapsed = max(0.0, now - self.updated_at)
                self.tokens = min(
                    self.capacity,
                    self.tokens + elapsed * self.rate,
                )
                self.updated_at = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait_seconds = (1 - self.tokens) / self.rate
            self.sleep_fn(wait_seconds)

    def try_acquire(self) -> bool:
        """Non-blocking attempt to acquire one token.

        Returns:
            True if token was acquired, False otherwise.

        Used by trailing exit logic to defer broker calls when bucket is exhausted.
        """
        with self._lock:
            now = self.clock()
            elapsed = max(0.0, now - self.updated_at)
            self.tokens = min(
                self.capacity,
                self.tokens + elapsed * self.rate,
            )
            self.updated_at = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


def ensure_live_positions_table(db: Any) -> None:
    """Apply the idempotent runtime form of the live-positions migrations.

    Issue #173: the DDL now lives in :mod:`app.analytics.live_schema` so the
    runtime shape matches Alembic (``20260915_002_live_trailing`` +
    ``20260916_001_live_trailing_runtime``): 30 columns, a seven-value status
    CHECK and ``trading.app_settings``. Previously this function created only
    the historical 20 columns with five statuses, so a fresh database built by
    the executor (without migrations) broke on the trailing fields and could
    not store ``closed_trailing`` / ``closed_broker``.

    Kept as a wrapper for backwards compatibility with existing callers/tests.
    """
    ensure_live_positions_schema(db)


class LiveExecutor:
    """Evaluate live bars and execute resulting trades in T-Bank Sandbox."""

    def __init__(
        self,
        *,
        db: Optional[Any] = None,
        broker: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
        evaluator_factory: Callable[[dict], StrategyEvaluator] = StrategyEvaluator,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] = _now_msk_naive,
    ) -> None:
        self.db = db or DBManager()
        self.config = {**get_live_trading_config(), **(config or {})}
        self._validate_config()
        self.rate_limiter = TokenBucket(
            float(self.config["api_rate_limit"]),
            clock=clock,
            sleep_fn=sleep_fn,
        )
        self._broker_limits_attempts = broker is None
        self.broker = broker or TinkoffSandboxClient(
            before_request=self.rate_limiter.acquire
        )
        self.evaluator_factory = evaluator_factory
        self.clock = clock
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn
        self.shutdown_requested = threading.Event()
        self.evaluators: Dict[str, StrategyEvaluator] = {}
        self.last_processed: Dict[str, Any] = {}
        self.instruments: Dict[str, Dict[str, Any]] = {}
        self.strategy_config: Dict[str, Any] = {}
        self.strategy_name = ""
        self.tickers = []

    def _validate_config(self) -> None:
        if int(self.config["max_open_positions"]) <= 0:
            raise ValueError("max_open_positions must be positive")
        rate = float(self.config["api_rate_limit"])
        if rate <= 0 or rate > 10:
            raise ValueError("api_rate_limit must be in the interval (0, 10]")
        for key in ("risk_per_trade_pct", "max_position_pct"):
            if float(self.config[key]) < 0:
                raise ValueError(f"{key} cannot be negative")
        # Issue #151: validate trailing runtime switches.
        for key in ("trailing_kill_switch", "live_trailing_enabled"):
            val = self.config.get(key, False)
            if not isinstance(val, bool):
                raise ValueError(f"{key} must be a boolean")
        ticks = self.config.get("trailing_protective_ticks", 5)
        if not isinstance(ticks, int) or ticks < 0:
            raise ValueError("trailing_protective_ticks must be a non-negative integer")
        allowlist = self.config.get("trailing_ticker_allowlist", [])
        if not isinstance(allowlist, list):
            raise ValueError("trailing_ticker_allowlist must be a list of strings")
        for item in allowlist:
            if not isinstance(item, str):
                raise ValueError("trailing_ticker_allowlist must contain only strings")

    def _broker_call(self, method: str, *args: Any, blocking: bool = True, **kwargs: Any) -> Any:
        """Execute a broker method with rate limiting.

        Args:
            method: Name of the broker method to call.
            *args: Positional arguments for the method.
            blocking: If True (default), wait for rate limit token.
                      If False, return None immediately if no token available.
            **kwargs: Keyword arguments for the method.

        Returns:
            Result of the broker method call, or None if blocking=False and
            no rate limit token was available.
        """
        if not self._broker_limits_attempts:
            if blocking:
                self.rate_limiter.acquire()
            else:
                if not self.rate_limiter.try_acquire():
                    return None
        return getattr(self.broker, method)(*args, **kwargs)

    def initialize(self) -> None:
        """Prepare persistence, strategy evaluators, and instrument metadata."""
        if not self.config["enabled"]:
            raise RuntimeError("Live trading is disabled in trading_config.py")
        ensure_live_positions_table(self.db)
        # Issue #173: fail fast on schema drift instead of dying mid-trade with
        # UndefinedColumn or a CHECK violation on closed_trailing/closed_broker.
        assert_live_schema(self.db)
        strategy_config, tickers, strategy_name = get_paper_strategy(self.db)
        if strategy_config is None or not strategy_name:
            raise RuntimeError("No active locked strategy is available")
        self.strategy_config = dict(strategy_config)
        self.strategy_name = strategy_name
        self.tickers = _filter_live_tickers(tickers, get_live_trading_universe(self.db))
        self._load_instruments()

        for ticker in self.tickers:
            if ticker not in self.instruments:
                logger.warning("No instrument metadata for %s; skipping", ticker)
                continue
            context = build_4h_context(self.db, ticker, self.strategy_config)
            if context is None:
                logger.warning("No 4h context for %s; skipping", ticker)
                continue
            evaluator = self.evaluator_factory(self.strategy_config)
            evaluator.load_context(
                context["levels"],
                context["ts_4h"],
                context["atr_by_ts"],
                context["buy_ts"],
                [],
                context.get("signal_filter_series") or [],
                context.get("htf_bars"),
            )
            self.evaluators[ticker] = evaluator
            self.last_processed[ticker] = None

        if not self.evaluators:
            raise RuntimeError("No live strategy evaluators could be initialized")

        # Issue #151: Acquire advisory lock to prevent multiple executor instances
        # Lock key: 151001 (arbitrary constant for Issue #151)
        lock_result = self.db.select(
            "SELECT pg_try_advisory_lock(151001) AS acquired"
        ).to_dataframe()
        if not lock_result.empty and not bool(lock_result.iloc[0]["acquired"]):
            logger.critical(
                "Failed to acquire advisory lock (151001): "
                "another LiveExecutor instance is already running"
            )
            raise RuntimeError(
                "Another LiveExecutor instance is already running "
                "(advisory lock 151001 is held)"
            )
        self._advisory_lock_acquired = True
        logger.info("Advisory lock 151001 acquired")

        # Issue #151: Log restored trailing positions for observability
        active = self._active_positions()
        if not active.empty and "trailing_enabled" in active.columns:
            trailing_positions = active[active["trailing_enabled"] == True]
            if not trailing_positions.empty:
                logger.info(
                    "Restored %d trailing position(s) from DB:",
                    len(trailing_positions),
                )
                for _, row in trailing_positions.iterrows():
                    logger.info(
                        "  position_id=%s ticker=%s step_reached=%s current_stop=%.6f",
                        int(row["id"]),
                        row["ticker"],
                        row.get("step_reached"),
                        float(row.get("current_stop_price") or row["stop_price"]),
                    )

    def _load_instruments(self) -> None:
        frame = self.db.select(
            """
            SELECT ticker, figi, lot_size, min_price_increment
            FROM trading.instruments
            WHERE ticker = ANY(%s)
              AND figi IS NOT NULL
              AND lot_size IS NOT NULL
            """,
            (self.tickers,),
        ).to_dataframe()
        self.instruments = {
            str(row["ticker"]): {
                "instrument_id": str(row["figi"]),
                "lot_size": int(row["lot_size"]),
                # Issue #151: load min_price_increment for tick-aligned stop prices.
                # Falls back to 0.01 if NULL (safe default for most MOEX equities).
                "min_price_increment": float(row["min_price_increment"])
                if pd.notna(row.get("min_price_increment")) and float(row["min_price_increment"]) > 0
                else 0.01,
            }
            for _, row in frame.iterrows()
            if int(row["lot_size"]) > 0 and str(row["figi"]).strip()
        }

    def _filter_config(self) -> Dict[str, Any]:
        return {
            **self.strategy_config,
            "imbalance_threshold": self.config["imbalance_threshold"],
        }

    def _refresh_kill_switch(self) -> None:
        """Read trailing_kill_switch from trading.app_settings (Issue #151).

        Updates self.config['trailing_kill_switch'] from DB.
        On error: sets kill switch ON (fail-safe) and logs warning.
        """
        try:
            result = self.db.select(
                """
                SELECT value FROM trading.app_settings
                WHERE key = 'trailing_kill_switch'
                """
            ).to_dataframe()
            if result.empty:
                logger.warning(
                    "trailing_kill_switch not found in trading.app_settings; "
                    "defaulting to False"
                )
                self.config["trailing_kill_switch"] = False
            else:
                value = result.iloc[0]["value"]
                # value is JSONB, may be bool or string
                if isinstance(value, bool):
                    self.config["trailing_kill_switch"] = value
                elif isinstance(value, str):
                    self.config["trailing_kill_switch"] = value.lower() == "true"
                else:
                    self.config["trailing_kill_switch"] = bool(value)
        except Exception as exc:
            logger.warning(
                "Failed to read trailing_kill_switch from DB: %s; "
                "defaulting to True (fail-safe)",
                exc,
            )
            self.config["trailing_kill_switch"] = True

    def _active_positions(self) -> pd.DataFrame:
        return self.db.select(
            """
            SELECT *
            FROM trading.live_positions
            WHERE status IN ('pending', 'open')
            ORDER BY id
            """
        ).to_dataframe()

    def _skip_signal(
        self,
        ticker: str,
        reason: str,
        *,
        warning: bool = False,
        **values: Any,
    ) -> Dict[str, Any]:
        details = " ".join(f"{key}={value}" for key, value in values.items())
        message = "Live signal skipped: ticker=%s reason=%s"
        args: list[Any] = [ticker, reason]
        if details:
            message += " %s"
            args.append(details)
        log = logger.warning if warning else logger.info
        log(message, *args)
        return {"executed": False, "reason": reason}

    def _latest_orderbook(self, ticker: str) -> tuple[Optional[float], Optional[float]]:
        """Return latest fresh imbalance and its age in seconds."""
        frame = self.db.select(
            """
            SELECT timestamp, bid_depth, ask_depth
            FROM trading.online_orderbook_aggregates
            WHERE ticker=%s
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (ticker,),
        ).to_dataframe()
        if frame.empty:
            return None, None

        row = frame.iloc[0]
        observed_at = pd.Timestamp(row["timestamp"]).to_pydatetime()
        if observed_at.tzinfo is not None:
            observed_at = observed_at.astimezone(
                timezone(timedelta(hours=3))
            ).replace(tzinfo=None)
        age_seconds = max(0.0, (_now_msk_naive() - observed_at).total_seconds())
        max_age_seconds = (
            float(get_orderbook_imbalance_config()["max_age_minutes"]) * 60
        )
        if age_seconds > max_age_seconds:
            return None, age_seconds
        return (
            calculate_volume_imbalance(row.get("bid_depth"), row.get("ask_depth")),
            age_seconds,
        )

    def process_signal(
        self,
        ticker: str,
        decision: Dict[str, Any],
        *,
        signal_ts: Optional[datetime] = None,
        imbalance: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Validate and execute one BUY decision from ``StrategyEvaluator``."""
        if decision.get("action") not in (None, "enter"):
            return self._skip_signal(ticker, "not_buy_signal")
        now = self.now_fn()
        if not is_entry_window(now):
            session = get_moex_session_config()
            return self._skip_signal(
                ticker,
                "outside_entry_window",
                hour=now.strftime("%H:%M"),
                entry_start=session["entry_start_hour"],
                entry_end=session["entry_end_hour"],
            )
        if ticker not in self.instruments:
            return self._skip_signal(ticker, "unknown_instrument")

        entry_price = float(decision["entry_price"])
        stop_price = float(decision["stop"])
        take_price = float(decision["take"])
        if not (0 < stop_price < entry_price < take_price):
            return self._skip_signal(
                ticker,
                "invalid_price_levels",
                entry_price=entry_price,
                stop_price=stop_price,
                take_price=take_price,
            )

        active = self._active_positions()
        if len(active) >= int(self.config["max_open_positions"]):
            return self._skip_signal(
                ticker,
                "max_open_positions",
                open_positions=len(active),
                max_open_positions=int(self.config["max_open_positions"]),
            )
        if not active.empty and ticker in set(active["ticker"].astype(str)):
            return self._skip_signal(
                ticker,
                "duplicate_ticker",
                open_positions=len(active),
            )

        orderbook_age_seconds: Optional[float] = None
        if imbalance is None:
            imbalance_value, orderbook_age_seconds = self._latest_orderbook(ticker)
        else:
            imbalance_value = imbalance
        max_age_seconds = (
            float(get_orderbook_imbalance_config()["max_age_minutes"]) * 60
        )
        if imbalance_value is None:
            return self._skip_signal(
                ticker,
                "stale_or_missing_orderbook",
                orderbook_age_seconds=(
                    "missing"
                    if orderbook_age_seconds is None
                    else round(orderbook_age_seconds, 3)
                ),
                max_age_seconds=max_age_seconds,
            )
        threshold = get_imbalance_threshold(self._filter_config())
        if not passes_imbalance_filter(imbalance_value, self._filter_config()):
            return self._skip_signal(
                ticker,
                "imbalance_below_threshold",
                imbalance=imbalance_value,
                imbalance_threshold=threshold,
                orderbook_age_seconds=(
                    "provided"
                    if orderbook_age_seconds is None
                    else round(orderbook_age_seconds, 3)
                ),
            )

        try:
            free_balance = self._broker_call("check_balance")
        except SandboxAPIError as exc:
            return self._skip_signal(
                ticker,
                "broker_error",
                warning=True,
                operation="check_balance",
                error_type=type(exc).__name__,
            )
        if float(free_balance) <= 0:
            return self._skip_signal(
                ticker,
                "insufficient_cash",
                free_rub=free_balance,
            )
        instrument = self.instruments[ticker]
        stop_distance_pct = (entry_price - stop_price) / entry_price * 100
        sizing = calculate_position_size(
            capital_rub=float(free_balance),
            stop_distance_pct=stop_distance_pct,
            price=entry_price,
            lot_size=instrument["lot_size"],
            risk_per_trade_pct=float(self.config["risk_per_trade_pct"]),
            max_position_pct=float(self.config["max_position_pct"]),
        )
        if sizing["size_lots"] <= 0:
            return self._skip_signal(
                ticker,
                sizing["reason"],
                free_rub=free_balance,
                size_lots=sizing["size_lots"],
                size_rub=round(sizing["size_rub"], 2),
                lot_size=instrument["lot_size"],
                lot_cost=entry_price * instrument["lot_size"],
                stop_distance_pct=round(stop_distance_pct, 6),
            )

        try:
            entry_order = self._broker_call(
                "execute_order",
                instrument_id=instrument["instrument_id"],
                quantity=sizing["size_lots"],
                direction="buy",
                order_type="market",
            )
        except SandboxAPIError as exc:
            return self._skip_signal(
                ticker,
                "broker_error",
                warning=True,
                operation="execute_entry_order",
                error_type=type(exc).__name__,
                size_lots=sizing["size_lots"],
            )
        executed_lots = int(getattr(entry_order, "lots_executed", 0))
        stored_lots = executed_lots or sizing["size_lots"]
        executed_price = getattr(entry_order, "executed_order_price", None)
        stored_entry_price = (
            float(executed_price) if executed_price is not None else entry_price
        )
        status = "open" if executed_lots > 0 else "pending"

        # Issue #151: Prepare trailing snapshot (0 broker calls).
        trailing_enabled = False
        trailing_steps_json = None
        risk_r = None
        current_stop = stop_price
        step_reached = 0

        if (
            self.config.get("live_trailing_enabled", True)
            and not self.config.get("trailing_kill_switch", False)
            and self.strategy_config.get("trailing_stop", {}).get("enabled", False)
        ):
            resolved = resolve_trailing_stop(self.strategy_config)
            if resolved["reasons"]:
                logger.warning(
                    "Live trailing SKIP: ticker=%s reasons=%s",
                    ticker,
                    ", ".join(resolved["reasons"]),
                )
            else:
                trailing_enabled = True
                trailing_steps_json = json.dumps(resolved["steps"])
                risk_r = round(float(stored_entry_price) - float(stop_price), 6)
                current_stop = stop_price
                logger.info(
                    "Live trailing ARMED: ticker=%s steps=%d risk_r=%.6f",
                    ticker,
                    len(resolved["steps"]),
                    risk_r,
                )

        position_id = self._insert_position(
            ticker=ticker,
            instrument_id=instrument["instrument_id"],
            signal_ts=signal_ts or decision.get("ts") or _now_msk_naive(),
            entry_price=stored_entry_price,
            lot_size=instrument["lot_size"],
            size_lots=stored_lots,
            stop_price=stop_price,
            take_price=take_price,
            broker_order_id=str(entry_order.order_id),
            status=status,
            trailing_enabled=trailing_enabled,
            trailing_steps=trailing_steps_json,
            risk_r=risk_r,
            current_stop_price=current_stop,
            step_reached=step_reached,
        )

        if status == "open":
            try:
                self._place_take_order(
                    position_id,
                    instrument["instrument_id"],
                    stored_lots,
                    take_price,
                )
            except Exception as exc:
                logger.warning(
                    "Live protection pending: ticker=%s reason=broker_error "
                    "operation=place_take_order position_id=%s error_type=%s",
                    ticker,
                    position_id,
                    type(exc).__name__,
                )
                return {
                    "executed": True,
                    "reason": "protection_pending",
                    "position_id": position_id,
                }

        logger.info(
            "Live BUY submitted: ticker=%s status=%s size_lots=%s position_id=%s",
            ticker,
            status,
            stored_lots,
            position_id,
        )
        return {
            "executed": True,
            "reason": status,
            "position_id": position_id,
            "size_lots": stored_lots,
        }

    def _insert_position(self, **position: Any) -> int:
        self.db.execute(
            """
            INSERT INTO trading.live_positions (
                ticker, instrument_id, signal_ts, entry_price, lot_size,
                size_lots, stop_price, take_price, broker_order_id,
                status, strategy_name,
                trailing_enabled, trailing_steps, risk_r,
                current_stop_price, step_reached,
                created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s, %s,
                now(), now()
            )
            """,
            (
                position["ticker"],
                position["instrument_id"],
                position["signal_ts"],
                position["entry_price"],
                position["lot_size"],
                position["size_lots"],
                position["stop_price"],
                position["take_price"],
                position["broker_order_id"],
                position["status"],
                self.strategy_name,
                position.get("trailing_enabled", False),
                position.get("trailing_steps"),
                position.get("risk_r"),
                position.get("current_stop_price", position["stop_price"]),
                position.get("step_reached", 0),
            ),
        )
        frame = self.db.select(
            """
            SELECT id
            FROM trading.live_positions
            WHERE broker_order_id=%s
            """,
            (position["broker_order_id"],),
        ).to_dataframe()
        if frame.empty:
            raise RuntimeError("Inserted live position could not be reloaded")
        return int(frame.iloc[0]["id"])

    def _place_take_order(
        self,
        position_id: int,
        instrument_id: str,
        size_lots: int,
        take_price: float,
    ) -> str:
        order = self._broker_call(
            "execute_order",
            instrument_id=instrument_id,
            quantity=size_lots,
            direction="sell",
            order_type="limit",
            price=take_price,
        )
        order_id = str(order.order_id)
        self.db.execute(
            """
            UPDATE trading.live_positions
            SET broker_take_id=%s, updated_at=now()
            WHERE id=%s
            """,
            (order_id, position_id),
        )
        return order_id

    def process_latest_bars(self) -> int:
        """Feed each new closed 1-minute bar to the shared evaluator."""
        executed = 0
        for ticker, evaluator in self.evaluators.items():
            frame, confirm_series = build_1m_context(
                self.db,
                ticker,
                self.strategy_config,
            )
            if frame is None or not confirm_series:
                continue
            evaluator.update_context(confirm_series=confirm_series)
            bar = frame.iloc[-1]
            bar_ts = bar["timestamp"]
            previous = self.last_processed[ticker]
            if previous is not None and bar_ts <= previous:
                continue
            self.last_processed[ticker] = bar_ts
            decision = evaluator.check_entry(bar)
            if decision is None:
                continue
            result = self.process_signal(
                ticker,
                decision,
                signal_ts=bar_ts,
            )
            if result["executed"]:
                executed += 1
        return executed

    def refresh_contexts(self) -> None:
        for ticker, evaluator in self.evaluators.items():
            context = build_4h_context(
                self.db,
                ticker,
                self.strategy_config,
            )
            if context is not None:
                evaluator.update_context(
                    levels=context["levels"],
                    ts_4h=context["ts_4h"],
                    atr_by_ts=context["atr_by_ts"],
                    buy_ts=context["buy_ts"],
                    signal_filter_series=context.get("signal_filter_series") or [],
                    htf_bars=context.get("htf_bars"),
                )

    @staticmethod
    def _position_keys(position: Any) -> set[str]:
        return {
            str(value)
            for value in (
                getattr(position, "ticker", ""),
                getattr(position, "figi", ""),
                getattr(position, "instrument_uid", ""),
            )
            if value
        }

    def monitor_positions(self) -> int:
        """Reconcile DB positions with broker holdings and trigger synthetic stops."""
        active = self._active_positions()
        if active.empty:
            return 0
        broker_positions = self._broker_call("get_positions")
        changes = 0

        for _, row in active.iterrows():
            position = next(
                (
                    item
                    for item in broker_positions
                    if str(row["ticker"]) in self._position_keys(item)
                    or str(row["instrument_id"]) in self._position_keys(item)
                ),
                None,
            )
            position_id = int(row["id"])
            status = str(row["status"])

            if status == "pending":
                if position is None:
                    continue
                self.db.execute(
                    """
                    UPDATE trading.live_positions
                    SET status='open', updated_at=now()
                    WHERE id=%s
                    """,
                    (position_id,),
                )
                self._place_take_order(
                    position_id,
                    str(row["instrument_id"]),
                    int(row["size_lots"]),
                    float(row["take_price"]),
                )
                changes += 1
                continue

            if position is None:
                reason = "stop" if pd.notna(row.get("broker_stop_id")) else "take"
                sibling = (
                    row.get("broker_take_id")
                    if reason == "stop"
                    else row.get("broker_stop_id")
                )
                self._safe_cancel(sibling)
                exit_price = float(
                    row["stop_price"] if reason == "stop" else row["take_price"]
                )
                self._close_db_position(row, reason, exit_price)
                changes += 1
                continue

            if pd.isna(row.get("broker_take_id")):
                self._place_take_order(
                    position_id,
                    str(row["instrument_id"]),
                    int(row["size_lots"]),
                    float(row["take_price"]),
                )
                changes += 1

            current_price = getattr(position, "current_price", None)
            if current_price is not None:
                # Issue #151: Apply trailing ratchet before stop trigger check
                ratcheted_stop = self._apply_trailing(row, float(current_price))
                effective_stop = (
                    ratcheted_stop
                    if ratcheted_stop is not None
                    else (
                        float(row["current_stop_price"])
                        if pd.notna(row.get("current_stop_price"))
                        else float(row["stop_price"])
                    )
                )
            else:
                effective_stop = float(row["stop_price"])

            if (
                current_price is not None
                and float(current_price) <= effective_stop
                and pd.isna(row.get("broker_stop_id"))
            ):
                self._safe_cancel(row.get("broker_take_id"))
                # Issue #151: Use non-blocking broker call for trailing exits.
                # If rate limit exhausted, defer to next iteration (state already in DB).
                stop_order = self._broker_call(
                    "execute_order",
                    instrument_id=str(row["instrument_id"]),
                    quantity=int(row["size_lots"]),
                    direction="sell",
                    order_type="limit",
                    price=float(current_price),
                    blocking=False,
                )
                if stop_order is None:
                    # Deferred: no rate limit token available
                    logger.warning(
                        "Trailing exit deferred (rate limit): position=%s ticker=%s "
                        "effective_stop=%.6f current_price=%.6f",
                        position_id,
                        row["ticker"],
                        effective_stop,
                        float(current_price),
                    )
                    continue
                self.db.execute(
                    """
                    UPDATE trading.live_positions
                    SET broker_stop_id=%s, broker_take_id=NULL, updated_at=now()
                    WHERE id=%s
                    """,
                    (str(stop_order.order_id), position_id),
                )
                changes += 1
        return changes

    def _apply_trailing(
        self, row: Any, current_price: float
    ) -> Optional[float]:
        """Ratchet trailing stop for one position based on current price.

        Args:
            row: Position row from _active_positions() with trailing_* columns.
            current_price: Current market price from broker.

        Returns:
            Updated current_stop_price if ratcheted, None otherwise.

        Idempotent: uses conditional UPDATE (WHERE step_reached < new_step)
        to ensure monotonicity and restart safety.
        """
        if not bool(row.get("trailing_enabled", False)):
            return None

        # Kill switch check
        if self.config.get("trailing_kill_switch", False):
            logger.debug(
                "Trailing kill switch ON, skipping ratchet for position %s",
                row["id"],
            )
            return None

        # Extract trailing state from DB
        steps_json = row.get("trailing_steps")
        if steps_json is None:
            logger.warning(
                "Position %s has trailing_enabled=true but no trailing_steps",
                row["id"],
            )
            return None

        import json
        steps = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
        entry_price = float(row["entry_price"])
        initial_stop = float(row["stop_price"])
        take_price = float(row["take_price"]) if pd.notna(row.get("take_price")) else None
        risk_r = float(row["risk_r"]) if pd.notna(row.get("risk_r")) else (entry_price - initial_stop)
        current_stop = float(row["current_stop_price"]) if pd.notna(row.get("current_stop_price")) else initial_stop
        step_reached = int(row.get("step_reached", 0))

        # Rebuild TrailingState
        from app.analytics.trailing_stop import TrailingState
        state = TrailingState.build(
            steps,
            entry_exec=entry_price,
            initial_stop=initial_stop,
            take=take_price,
        )
        if state is None:
            logger.warning(
                "Position %s: failed to rebuild TrailingState, skipping ratchet",
                row["id"],
            )
            return None

        # Evaluate current price as a single bar (high=current_price, low=current_price)
        # This is a simplified model: in live we don't have intraday bars, only current price.
        # We use current_price as both high and low to check if it triggers a new step.
        decision = state.evaluate(
            high=current_price,
            low=current_price,
            bar_key=f"live_{int(row['id'])}",
        )

        if decision.exits:
            return None

        # Ratchet: conditional UPDATE only if new step > current step_reached
        new_step = int(decision.step_reached)
        if new_step <= step_reached:
            return None

        new_stop = float(decision.stop)
        position_id = int(row["id"])

        self.db.execute(
            """
            UPDATE trading.live_positions
            SET current_stop_price=%s, step_reached=%s, updated_at=now()
            WHERE id=%s AND step_reached < %s
            """,
            (new_stop, new_step, position_id, new_step),
        )

        logger.info(
            "Trailing ratchet: position=%s ticker=%s step_reached=%d new_stop=%.6f",
            position_id,
            row["ticker"],
            new_step,
            new_stop,
        )

        return new_stop

    def _close_db_position(
        self,
        row: Any,
        reason: str,
        exit_price: float,
        *,
        status: Optional[str] = None,
        exit_price_actual: Optional[float] = None,
        lots_executed: Optional[int] = None,
    ) -> None:
        """Close a position and record execution facts (Issue #151).

        Args:
            row: Position row from _active_positions().
            reason: Exit reason ('stop', 'take', 'trailing', 'sell_signal', 'shutdown').
            exit_price: Model price (the price the executor expected at exit time).
            status: Override status (default: 'closed_{reason}').
            exit_price_actual: Actual fill price from broker (may be None if not yet known).
            lots_executed: Actual lots filled (may be None if not yet known).

        Records:
            - exit_price_model = exit_price (model price)
            - exit_price_actual = exit_price_actual (actual fill, or NULL)
            - exit_price = exit_price_actual if available, else exit_price (Variant A)
            - slippage_bp = (actual/model - 1) * 1e4 (positive = adverse for long)
            - slippage_r = (model - actual) / risk_r
            - lots_executed = lots_executed (or NULL)
        """
        # Determine effective exit price (Variant A: actual if available, else model)
        effective_exit = (
            exit_price_actual if exit_price_actual is not None else exit_price
        )
        pnl_rub = (
            effective_exit - float(row["entry_price"])
        ) * int(row["size_lots"]) * int(row["lot_size"])

        # Compute slippage metrics
        slippage_bp = None
        slippage_r = None
        if exit_price_actual is not None and exit_price > 0:
            slippage_bp = round((exit_price_actual / exit_price - 1.0) * 1e4, 4)
            risk_r = float(row.get("risk_r") or 0.0)
            if risk_r > 0:
                slippage_r = round((exit_price - exit_price_actual) / risk_r, 6)

        # Determine status
        final_status = status or f"closed_{reason}"
        if reason == "trailing":
            final_status = status or "closed_trailing"

        self.db.execute(
            """
            UPDATE trading.live_positions
            SET status=%s, exit_ts=%s, exit_price=%s, exit_reason=%s,
                pnl_rub=%s,
                exit_price_model=%s, exit_price_actual=%s,
                slippage_bp=%s, slippage_r=%s, lots_executed=%s,
                updated_at=now()
            WHERE id=%s
            """,
            (
                final_status,
                _now_msk_naive(),
                effective_exit,
                reason,
                round(pnl_rub, 2),
                exit_price,
                exit_price_actual,
                slippage_bp,
                slippage_r,
                lots_executed,
                int(row["id"]),
            ),
        )

        # Structured log for trailing exits (Issue #151 requirement 9)
        if reason == "trailing":
            logger.info(
                "trailing_exit ticker=%s entry=%.6f initial_stop=%.6f final_stop=%.6f "
                "step_reached=%s risk_r=%s model_price=%.6f actual_price=%s "
                "slippage_bp=%s slippage_r=%s lots_requested=%d lots_executed=%s "
                "position_id=%s",
                row["ticker"],
                float(row["entry_price"]),
                float(row["stop_price"]),
                float(row.get("current_stop_price") or row["stop_price"]),
                row.get("step_reached"),
                row.get("risk_r"),
                exit_price,
                exit_price_actual,
                slippage_bp,
                slippage_r,
                int(row["size_lots"]),
                lots_executed,
                int(row["id"]),
            )

    def _safe_cancel(self, order_id: Any) -> None:
        if order_id is None or pd.isna(order_id) or not str(order_id).strip():
            return
        try:
            self._broker_call("cancel_order", str(order_id))
        except SandboxAPIError:
            logger.info("Order %s is no longer cancellable", order_id)

    def handle_sell_signal(self, ticker: str) -> int:
        """Close active ticker positions when an external SELL signal arrives."""
        active = self.db.select(
            """
            SELECT *
            FROM trading.live_positions
            WHERE ticker=%s AND status IN ('pending', 'open')
            ORDER BY id
            """,
            (ticker,),
        ).to_dataframe()
        closed = 0
        for _, row in active.iterrows():
            self._safe_cancel(row.get("broker_stop_id"))
            self._safe_cancel(row.get("broker_take_id"))
            if str(row["status"]) == "pending":
                self._safe_cancel(row.get("broker_order_id"))
                self._mark_cancelled(int(row["id"]), "sell_signal")
                closed += 1
                continue
            order = self._broker_call(
                "execute_order",
                instrument_id=str(row["instrument_id"]),
                quantity=int(row["size_lots"]),
                direction="sell",
                order_type="market",
            )
            exit_price = getattr(order, "executed_order_price", None)
            self._close_db_position(
                row,
                "sell_signal",
                float(exit_price or row["entry_price"]),
                status="cancelled",
            )
            closed += 1
        return closed

    def _mark_cancelled(self, position_id: int, reason: str) -> None:
        self.db.execute(
            """
            UPDATE trading.live_positions
            SET status='cancelled', exit_ts=%s, exit_reason=%s, updated_at=now()
            WHERE id=%s
            """,
            (_now_msk_naive(), reason, position_id),
        )

    def request_shutdown(self, *_args: Any) -> None:
        """Signal-safe request; broker and DB cleanup happens in ``shutdown``."""
        self.shutdown_requested.set()

    def install_signal_handlers(self) -> None:
        try:
            signal.signal(signal.SIGTERM, self.request_shutdown)
            signal.signal(signal.SIGINT, self.request_shutdown)
        except ValueError:
            logger.warning("Signal handlers can only be installed in the main thread")

    def shutdown(self) -> None:
        """Cancel pending orders and optionally flatten sandbox holdings."""
        self.shutdown_requested.set()
        active = self._active_positions()
        close_positions = bool(self.config["close_positions_on_shutdown"])
        for _, row in active.iterrows():
            self._safe_cancel(row.get("broker_stop_id"))
            self._safe_cancel(row.get("broker_take_id"))
            if str(row["status"]) == "pending":
                self._safe_cancel(row.get("broker_order_id"))
                self._mark_cancelled(int(row["id"]), "shutdown")
                continue
            if close_positions:
                order = self._broker_call(
                    "execute_order",
                    instrument_id=str(row["instrument_id"]),
                    quantity=int(row["size_lots"]),
                    direction="sell",
                    order_type="market",
                )
                exit_price = getattr(order, "executed_order_price", None)
                self._close_db_position(
                    row,
                    "shutdown",
                    float(exit_price or row["entry_price"]),
                    status="cancelled",
                )
            else:
                self.db.execute(
                    """
                    UPDATE trading.live_positions
                    SET broker_stop_id=NULL, broker_take_id=NULL, updated_at=now()
                    WHERE id=%s
                    """,
                    (int(row["id"]),),
                )

        # Issue #151: Release advisory lock
        if getattr(self, "_advisory_lock_acquired", False):
            try:
                self.db.execute("SELECT pg_advisory_unlock(151001)")
                logger.info("Advisory lock 151001 released")
            except Exception as exc:
                logger.warning("Failed to release advisory lock: %s", exc)

    def wait_for_session_open(self) -> None:
        """Sleep until the MOEX entry window, logging progress, honoring SIGTERM."""
        session = get_moex_session_config()
        poll = float(session["wait_poll_seconds"])
        log_every = float(session["wait_log_seconds"])
        last_log = float("-inf")
        while not self.shutdown_requested.is_set():
            now = self.now_fn()
            if is_entry_window(now):
                logger.info(
                    "MOEX session is open: %s MSK",
                    now.strftime("%Y-%m-%d %H:%M"),
                )
                return
            open_at = next_session_open(now)
            remaining = max(0.0, (open_at - now).total_seconds())
            mono = self.clock()
            if mono - last_log >= log_every:
                logger.info(
                    "Waiting for MOEX session open at %s MSK (%.0f min remaining)",
                    open_at.strftime("%Y-%m-%d %H:%M"),
                    remaining / 60.0,
                )
                last_log = mono
            self.sleep_fn(min(poll, remaining if remaining > 0 else poll))

    def run(
        self,
        duration_minutes: Optional[int] = None,
        until_session_end: bool = False,
    ) -> None:
        """Run evaluation and reconciliation until timeout, flat after session, or signal.

        ``until_session_end`` only closes **entries** at 19:00 MSK. Stop/take
        stay under monitor until every sandbox position is gone.
        """
        self.install_signal_handlers()
        try:
            if until_session_end:
                self.wait_for_session_open()
                if self.shutdown_requested.is_set():
                    return
            self.initialize()
            started_at = self.clock()
            last_check = float("-inf")
            last_context_refresh = self.clock()
            check_interval = float(self.config["check_interval_seconds"])
            context_interval = float(self.config["context_refresh_seconds"])
            session_end = (
                session_end_for_run(self.now_fn()) if until_session_end else None
            )
            logger.info(
                "Sandbox LiveExecutor started: strategy=%s tickers=%s "
                "ticker_count=%s rate=%.1f/s until_session_end=%s session_end=%s",
                self.strategy_name,
                ",".join(self.evaluators),
                len(self.evaluators),
                self.rate_limiter.rate,
                until_session_end,
                session_end.strftime("%Y-%m-%d %H:%M") if session_end else "none",
            )
            entry_closed_logged = False
            while not self.shutdown_requested.is_set():
                now_msk = self.now_fn()
                now = self.clock()
                session_closed = (
                    until_session_end
                    and session_end is not None
                    and now_msk >= session_end
                )
                if (
                    duration_minutes is not None
                    and now - started_at >= duration_minutes * 60
                ):
                    break
                if now - last_context_refresh >= context_interval:
                    self.refresh_contexts()
                    last_context_refresh = now
                if now - last_check >= check_interval:
                    # Issue #151: Refresh kill switch from DB before monitoring
                    self._refresh_kill_switch()
                    self.monitor_positions()
                    if is_entry_window(now_msk):
                        self.process_latest_bars()
                    elif session_closed:
                        if not entry_closed_logged:
                            logger.info(
                                "MOEX entry window closed at %s MSK; "
                                "monitoring stop/take until positions close",
                                now_msk.strftime("%Y-%m-%d %H:%M"),
                            )
                            entry_closed_logged = True
                        if self._active_positions().empty:
                            logger.info(
                                "No open sandbox positions after session close; "
                                "stopping LiveExecutor"
                            )
                            break
                    last_check = now
                self.sleep_fn(min(1.0, check_interval))
        finally:
            try:
                self.shutdown()
            finally:
                close_pool = getattr(self.db, "close_pool", None)
                if callable(close_pool):
                    close_pool()
                logger.info("Sandbox LiveExecutor stopped cleanly")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else None
    LiveExecutor().run(
        duration_minutes=duration,
        until_session_end=duration is None,
    )
