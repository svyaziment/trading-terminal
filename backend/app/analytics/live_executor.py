"""Sandbox live executor using the unified strategy evaluator.

The executor deliberately uses only :class:`TinkoffSandboxClient`. A take-profit
is submitted as a resting sell limit. A stop-loss is a synthetic trigger: the
sell limit is submitted only after the monitored price reaches the stop, because
a sell limit below the market would execute immediately and is not a stop order.
"""

from __future__ import annotations

import logging
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
from app.analytics.orderbook_imbalance import (
    calculate_volume_imbalance,
    get_imbalance_threshold,
    passes_imbalance_filter,
)
from app.analytics.position_sizer import calculate_position_size
from app.analytics.strategy_engine import StrategyEvaluator
from app.analytics.trading_config import (
    get_live_trading_config,
    get_live_trading_universe,
    get_orderbook_imbalance_config,
)
from app.broker.tinkoff_sandbox import SandboxAPIError, TinkoffSandboxClient
from app.db.db_manager import DBManager


logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "open")


def _filter_live_tickers(strategy_tickers: list[str], live_universe: list[str]) -> list[str]:
    """Keep strategy tickers that belong to the Issue #66 live universe."""
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


def _now_msk_naive() -> datetime:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=3))
    ).replace(tzinfo=None)


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


def ensure_live_positions_table(db: Any) -> None:
    """Apply the idempotent runtime form of the live-positions migration."""
    db.execute("CREATE SCHEMA IF NOT EXISTS trading")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS trading.live_positions (
            id BIGSERIAL PRIMARY KEY,
            ticker VARCHAR(32) NOT NULL,
            instrument_id VARCHAR(128) NOT NULL,
            signal_ts TIMESTAMP NOT NULL,
            entry_price NUMERIC(20, 9) NOT NULL,
            lot_size INTEGER NOT NULL CHECK (lot_size > 0),
            size_lots INTEGER NOT NULL CHECK (size_lots > 0),
            stop_price NUMERIC(20, 9) NOT NULL,
            take_price NUMERIC(20, 9) NOT NULL,
            broker_order_id VARCHAR(128) NOT NULL UNIQUE,
            broker_stop_id VARCHAR(128),
            broker_take_id VARCHAR(128),
            status VARCHAR(32) NOT NULL CHECK (
                status IN (
                    'pending', 'open', 'closed_stop',
                    'closed_take', 'cancelled'
                )
            ),
            strategy_name VARCHAR(255) NOT NULL,
            exit_ts TIMESTAMP,
            exit_price NUMERIC(20, 9),
            exit_reason VARCHAR(64),
            pnl_rub NUMERIC(20, 2),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_live_positions_active
        ON trading.live_positions (status, ticker)
        """
    )


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

    def _broker_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if not self._broker_limits_attempts:
            self.rate_limiter.acquire()
        return getattr(self.broker, method)(*args, **kwargs)

    def initialize(self) -> None:
        """Prepare persistence, strategy evaluators, and instrument metadata."""
        if not self.config["enabled"]:
            raise RuntimeError("Live trading is disabled in trading_config.py")
        ensure_live_positions_table(self.db)
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
            )
            self.evaluators[ticker] = evaluator
            self.last_processed[ticker] = None

        if not self.evaluators:
            raise RuntimeError("No live strategy evaluators could be initialized")

    def _load_instruments(self) -> None:
        frame = self.db.select(
            """
            SELECT ticker, figi, lot_size
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
            }
            for _, row in frame.iterrows()
            if int(row["lot_size"]) > 0 and str(row["figi"]).strip()
        }

    def _filter_config(self) -> Dict[str, Any]:
        return {
            **self.strategy_config,
            "imbalance_threshold": self.config["imbalance_threshold"],
        }

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
                status, strategy_name, created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, now(), now()
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
            if (
                current_price is not None
                and float(current_price) <= float(row["stop_price"])
                and pd.isna(row.get("broker_stop_id"))
            ):
                self._safe_cancel(row.get("broker_take_id"))
                stop_order = self._broker_call(
                    "execute_order",
                    instrument_id=str(row["instrument_id"]),
                    quantity=int(row["size_lots"]),
                    direction="sell",
                    order_type="limit",
                    price=float(current_price),
                )
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

    def _close_db_position(
        self,
        row: Any,
        reason: str,
        exit_price: float,
        *,
        status: Optional[str] = None,
    ) -> None:
        pnl_rub = (
            exit_price - float(row["entry_price"])
        ) * int(row["size_lots"]) * int(row["lot_size"])
        self.db.execute(
            """
            UPDATE trading.live_positions
            SET status=%s, exit_ts=%s, exit_price=%s, exit_reason=%s,
                pnl_rub=%s, updated_at=now()
            WHERE id=%s
            """,
            (
                status or f"closed_{reason}",
                _now_msk_naive(),
                exit_price,
                reason,
                round(pnl_rub, 2),
                int(row["id"]),
            ),
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

    def run(self, duration_minutes: Optional[int] = None) -> None:
        """Run evaluation and reconciliation until timeout or SIGTERM/SIGINT."""
        self.install_signal_handlers()
        self.initialize()
        started_at = self.clock()
        last_check = float("-inf")
        last_context_refresh = self.clock()
        check_interval = float(self.config["check_interval_seconds"])
        context_interval = float(self.config["context_refresh_seconds"])
        logger.info(
            "Sandbox LiveExecutor started: strategy=%s tickers=%s "
            "ticker_count=%s rate=%.1f/s",
            self.strategy_name,
            ",".join(self.evaluators),
            len(self.evaluators),
            self.rate_limiter.rate,
        )
        try:
            while not self.shutdown_requested.is_set():
                now = self.clock()
                if (
                    duration_minutes is not None
                    and now - started_at >= duration_minutes * 60
                ):
                    break
                if now - last_context_refresh >= context_interval:
                    self.refresh_contexts()
                    last_context_refresh = now
                if now - last_check >= check_interval:
                    self.monitor_positions()
                    self.process_latest_bars()
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
    LiveExecutor().run(duration_minutes=duration)
