"""Safe synchronous client for order execution in the T-Bank sandbox."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from app.analytics.trading_config import get_sandbox_trading_config
from app.core.config_manager import load_settings

try:
    from t_tech.invest import (
        AccountStatus,
        CancelStopOrderRequest,
        Client,
        GetStopOrdersRequest,
        OperationState,
        OrderDirection,
        OrderType,
        PostStopOrderRequest,
        Quotation,
        StopOrderDirection,
        StopOrderExpirationType,
        StopOrderStatusOption,
        StopOrderType,
    )
    from t_tech.invest.constants import INVEST_GRPC_API_SANDBOX
    from t_tech.invest.exceptions import RequestError

    IS_SDK_AVAILABLE = True
    SDK_RETRYABLE_ERRORS = (RequestError,)
    # Issue #175: string -> SDK enum maps. Callers (LiveExecutor) speak in plain
    # strings so the executor stays testable without the SDK installed.
    STOP_ORDER_TYPE_MAP: Dict[str, Any] = {
        "stop_loss": StopOrderType.STOP_ORDER_TYPE_STOP_LOSS,
        "take_profit": StopOrderType.STOP_ORDER_TYPE_TAKE_PROFIT,
    }
    STOP_ORDER_DIRECTION_MAP: Dict[str, Any] = {
        "buy": StopOrderDirection.STOP_ORDER_DIRECTION_BUY,
        "sell": StopOrderDirection.STOP_ORDER_DIRECTION_SELL,
    }
    STOP_ORDER_EXPIRATION_MAP: Dict[str, Any] = {
        "good_till_cancel": (
            StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL
        ),
        "good_till_date": (
            StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_DATE
        ),
    }
    STOP_ORDER_STATUS_MAP: Dict[str, Any] = {
        "all": StopOrderStatusOption.STOP_ORDER_STATUS_ALL,
        "active": StopOrderStatusOption.STOP_ORDER_STATUS_ACTIVE,
        "executed": StopOrderStatusOption.STOP_ORDER_STATUS_EXECUTED,
        "canceled": StopOrderStatusOption.STOP_ORDER_STATUS_CANCELED,
        "expired": StopOrderStatusOption.STOP_ORDER_STATUS_EXPIRED,
    }
    OPERATION_STATE_MAP: Dict[str, Any] = {
        "all": OperationState.OPERATION_STATE_UNSPECIFIED,
        "executed": OperationState.OPERATION_STATE_EXECUTED,
        "canceled": OperationState.OPERATION_STATE_CANCELED,
        "progress": OperationState.OPERATION_STATE_PROGRESS,
    }
except ImportError:  # pragma: no cover - exercised only in a broken deployment
    AccountStatus = Client = OrderDirection = OrderType = Quotation = None
    CancelStopOrderRequest = GetStopOrdersRequest = PostStopOrderRequest = None
    OperationState = StopOrderDirection = StopOrderExpirationType = None
    StopOrderStatusOption = StopOrderType = None
    INVEST_GRPC_API_SANDBOX = None
    IS_SDK_AVAILABLE = False
    SDK_RETRYABLE_ERRORS = ()
    STOP_ORDER_TYPE_MAP = {}
    STOP_ORDER_DIRECTION_MAP = {}
    STOP_ORDER_EXPIRATION_MAP = {}
    STOP_ORDER_STATUS_MAP = {}
    OPERATION_STATE_MAP = {}


logger = logging.getLogger(__name__)
NANO_FACTOR = Decimal("1000000000")
RETRYABLE_GRPC_CODES = {
    "DEADLINE_EXCEEDED",
    "INTERNAL",
    "RESOURCE_EXHAUSTED",
    "UNAVAILABLE",
}


class SandboxClientError(RuntimeError):
    """Base error exposed by the sandbox client."""


class SandboxConfigurationError(SandboxClientError):
    """The sandbox client cannot start because configuration is unsafe or incomplete."""


class SandboxAPIError(SandboxClientError):
    """A T-Bank sandbox API request failed."""


@dataclass(frozen=True)
class SandboxOrder:
    order_id: str
    status: str
    lots_requested: int
    lots_executed: int
    initial_order_price: Optional[Decimal]
    executed_order_price: Optional[Decimal]
    total_order_amount: Optional[Decimal]
    executed_commission: Optional[Decimal]
    message: str


@dataclass(frozen=True)
class SandboxPosition:
    figi: str
    ticker: str
    instrument_uid: str
    instrument_type: str
    quantity: Decimal
    quantity_lots: Decimal
    blocked_lots: Decimal
    average_price: Optional[Decimal]
    current_price: Optional[Decimal]
    expected_yield: Optional[Decimal]


@dataclass(frozen=True)
class CancelledSandboxOrder:
    order_id: str
    cancelled_at: Any


@dataclass(frozen=True)
class SandboxStopOrder:
    """Result of ``PostStopOrder`` (Issue #175)."""

    stop_order_id: str
    order_request_id: str = ""


@dataclass(frozen=True)
class SandboxStopOrderState:
    """One entry of ``GetStopOrders`` (Issue #175)."""

    stop_order_id: str
    status: str
    direction: str
    order_type: str
    lots_requested: int
    stop_price: Optional[Decimal]
    price: Optional[Decimal]
    ticker: str = ""
    figi: str = ""
    instrument_uid: str = ""
    currency: str = ""
    exchange_order_id: Optional[str] = None
    created_at: Any = None
    activation_date_time: Any = None
    expiration_time: Any = None


@dataclass(frozen=True)
class CancelledSandboxStopOrder:
    stop_order_id: str
    cancelled_at: Any


@dataclass(frozen=True)
class SandboxOrderState:
    """One entry of ``GetOrders`` — a resting (not yet executed) order (#175)."""

    order_id: str
    status: str
    direction: str
    order_type: str
    lots_requested: int
    lots_executed: int
    figi: str = ""
    instrument_uid: str = ""
    currency: str = ""
    initial_order_price: Optional[Decimal] = None
    executed_order_price: Optional[Decimal] = None
    order_date: Any = None


@dataclass(frozen=True)
class SandboxOperationTrade:
    """One fill inside an operation (Issue #175)."""

    trade_id: str
    quantity: int
    price: Optional[Decimal]
    date_time: Any = None


@dataclass(frozen=True)
class SandboxOperation:
    """One entry of ``GetOperations`` — an executed/cancelled broker operation.

    ``quantity`` / ``quantity_rest`` are expressed in **shares** (not lots) and
    ``price`` is the price per share, matching the InvestAPI contract.
    """

    id: str
    state: str
    operation_type: str
    quantity: int
    quantity_rest: int
    price: Optional[Decimal]
    payment: Optional[Decimal]
    currency: str = ""
    figi: str = ""
    instrument_uid: str = ""
    instrument_type: str = ""
    parent_operation_id: str = ""
    date: Any = None
    trades: List[SandboxOperationTrade] = field(default_factory=list)


def _decimal_value(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    units = Decimal(getattr(value, "units", 0))
    nano = Decimal(getattr(value, "nano", 0))
    return units + nano / NANO_FACTOR


def _quotation(value: Decimal) -> Any:
    normalized = value.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    units = int(normalized)
    nano = int((normalized - Decimal(units)) * NANO_FACTOR)
    return Quotation(units=units, nano=nano)


def _enum_name(value: Any) -> str:
    return str(getattr(value, "name", value))


def _masked_account(account_id: str) -> str:
    return f"***{account_id[-4:]}" if account_id else "<not-set>"


class TinkoffSandboxClient:
    """Execute orders, stop orders and inspect funds via ``client.sandbox``.

    Issue #175 added the broker protection surface: :meth:`post_stop_order`,
    :meth:`get_stop_orders`, :meth:`cancel_stop_order`, :meth:`get_orders` and
    :meth:`get_operations`. Every call goes through :meth:`_call`, so the retry
    policy, the sandbox endpoint and the ``allow_real_trading=false`` guardrail
    are shared with market/limit orders.
    """

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        account_id: Optional[str] = None,
        client_factory: Optional[Callable[..., Any]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        before_request: Optional[Callable[[], None]] = None,
    ) -> None:
        if not IS_SDK_AVAILABLE:
            raise SandboxConfigurationError("t-tech-investments SDK is not available")

        settings = load_settings()
        policy = get_sandbox_trading_config()
        self.sandbox_token = (
            token if token is not None else settings.api.sandbox_token
        ).strip()
        self.account_id = (
            account_id
            if account_id is not None
            else settings.api.sandbox_account_id
        ).strip()
        self.retry_attempts = max(1, int(policy["retry_attempts"]))
        self.retry_base_delay = max(
            0.0, float(policy["retry_base_delay_seconds"])
        )
        self.discover_account = bool(policy["discover_account_when_missing"])
        self._client_factory = client_factory or Client
        self._sleep = sleep_fn
        self._before_request = before_request or (lambda: None)

        if not policy.get("enabled"):
            raise SandboxConfigurationError("Sandbox trading is disabled")
        if policy.get("allow_real_trading"):
            raise SandboxConfigurationError(
                "Unsafe configuration: allow_real_trading must remain false"
            )
        if not self.sandbox_token:
            raise SandboxConfigurationError("TINVEST_SANDBOX is empty")
        if not self.account_id and not self.discover_account:
            raise SandboxConfigurationError(
                "TINVEST_SANDBOX_ACC is empty and account discovery is disabled"
            )

    def execute_order(
        self,
        *,
        instrument_id: str,
        quantity: int,
        direction: str = "buy",
        order_type: str = "market",
        price: Optional[Decimal | float | str] = None,
        order_id: Optional[str] = None,
    ) -> SandboxOrder:
        """Place a market or limit order in the sandbox.

        The idempotency key is generated before the first attempt and reused by retries.
        Quantity is expressed in lots.
        """
        instrument_id = instrument_id.strip()
        direction_key = direction.strip().lower()
        order_type_key = order_type.strip().lower()
        if not instrument_id:
            raise ValueError("instrument_id must not be empty")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer number of lots")
        if direction_key not in {"buy", "sell"}:
            raise ValueError("direction must be 'buy' or 'sell'")
        if order_type_key not in {"market", "limit"}:
            raise ValueError("order_type must be 'market' or 'limit'")

        decimal_price = Decimal(str(price)) if price is not None else None
        if order_type_key == "limit":
            if decimal_price is None or decimal_price <= 0:
                raise ValueError("positive price is required for a limit order")
        elif decimal_price is not None:
            raise ValueError("price must be omitted for a market order")

        request_id = (order_id or str(uuid4())).strip()
        if not request_id:
            raise ValueError("order_id must not be empty")
        sdk_direction = (
            OrderDirection.ORDER_DIRECTION_BUY
            if direction_key == "buy"
            else OrderDirection.ORDER_DIRECTION_SELL
        )
        sdk_order_type = (
            OrderType.ORDER_TYPE_MARKET
            if order_type_key == "market"
            else OrderType.ORDER_TYPE_LIMIT
        )

        def request(sandbox: Any) -> Any:
            account_id = self._resolve_account_id(sandbox)
            return sandbox.post_sandbox_order(
                instrument_id=instrument_id,
                quantity=quantity,
                price=_quotation(decimal_price) if decimal_price is not None else None,
                direction=sdk_direction,
                account_id=account_id,
                order_type=sdk_order_type,
                order_id=request_id,
            )

        response = self._call("execute_order", request)
        result = SandboxOrder(
            order_id=str(getattr(response, "order_id", request_id)),
            status=_enum_name(getattr(response, "execution_report_status", "")),
            lots_requested=int(getattr(response, "lots_requested", quantity)),
            lots_executed=int(getattr(response, "lots_executed", 0)),
            initial_order_price=_decimal_value(
                getattr(response, "initial_order_price", None)
            ),
            executed_order_price=_decimal_value(
                getattr(response, "executed_order_price", None)
            ),
            total_order_amount=_decimal_value(
                getattr(response, "total_order_amount", None)
            ),
            executed_commission=_decimal_value(
                getattr(response, "executed_commission", None)
            ),
            message=str(getattr(response, "message", "")),
        )
        logger.info(
            "Sandbox order %s submitted: instrument=%s type=%s direction=%s lots=%s",
            result.order_id,
            instrument_id,
            order_type_key,
            direction_key,
            quantity,
        )
        return result

    def check_balance(self, currency: Optional[str] = None) -> Decimal:
        """Return free cash balance for a currency (RUB by default)."""
        selected_currency = (
            currency or get_sandbox_trading_config()["default_currency"]
        ).strip().lower()
        if not selected_currency:
            raise ValueError("currency must not be empty")

        def request(sandbox: Any) -> Any:
            return sandbox.get_sandbox_positions(
                account_id=self._resolve_account_id(sandbox)
            )

        response = self._call("check_balance", request)
        return sum(
            (
                _decimal_value(item) or Decimal("0")
                for item in (getattr(response, "money", None) or [])
                if str(getattr(item, "currency", "")).lower() == selected_currency
            ),
            Decimal("0"),
        )

    def get_positions(self) -> List[SandboxPosition]:
        """Return open non-zero instrument positions from the sandbox portfolio."""

        def request(sandbox: Any) -> Any:
            return sandbox.get_sandbox_portfolio(
                account_id=self._resolve_account_id(sandbox)
            )

        response = self._call("get_positions", request)
        positions: List[SandboxPosition] = []
        for item in getattr(response, "positions", None) or []:
            quantity = _decimal_value(getattr(item, "quantity", None)) or Decimal("0")
            quantity_lots = _decimal_value(
                getattr(item, "quantity_lots", None)
            ) or Decimal("0")
            if quantity == 0 and quantity_lots == 0:
                continue
            positions.append(
                SandboxPosition(
                    figi=str(getattr(item, "figi", "")),
                    ticker=str(getattr(item, "ticker", "")),
                    instrument_uid=str(getattr(item, "instrument_uid", "")),
                    instrument_type=str(getattr(item, "instrument_type", "")),
                    quantity=quantity,
                    quantity_lots=quantity_lots,
                    blocked_lots=_decimal_value(
                        getattr(item, "blocked_lots", None)
                    )
                    or Decimal("0"),
                    average_price=_decimal_value(
                        getattr(item, "average_position_price", None)
                    ),
                    current_price=_decimal_value(
                        getattr(item, "current_price", None)
                    ),
                    expected_yield=_decimal_value(
                        getattr(item, "expected_yield", None)
                    ),
                )
            )
        return positions

    def cancel_order(self, order_id: str) -> CancelledSandboxOrder:
        """Cancel an active sandbox order."""
        order_id = order_id.strip()
        if not order_id:
            raise ValueError("order_id must not be empty")

        def request(sandbox: Any) -> Any:
            return sandbox.cancel_sandbox_order(
                account_id=self._resolve_account_id(sandbox),
                order_id=order_id,
            )

        response = self._call("cancel_order", request)
        logger.info("Sandbox order %s cancelled", order_id)
        return CancelledSandboxOrder(
            order_id=order_id,
            cancelled_at=getattr(response, "time", None),
        )

    def post_stop_order(
        self,
        *,
        instrument_id: str,
        quantity: int,
        stop_price: Decimal | float | str,
        direction: str = "sell",
        stop_order_type: str = "stop_loss",
        price: Optional[Decimal | float | str] = None,
        expiration_type: str = "good_till_cancel",
        expire_date: Optional[datetime] = None,
        order_id: Optional[str] = None,
    ) -> SandboxStopOrder:
        """Place a broker stop order (``PostStopOrder``) in the sandbox.

        Issue #175: real broker-side protection that replaces the synthetic
        "cancel take + marketable sell limit" stop.

        Args:
            instrument_id: Instrument uid (the executor stores FIGI/uid here).
            quantity: Number of **lots** the stop trades when triggered.
            stop_price: Trigger price per share.
            direction: ``sell`` (long protection) or ``buy`` (short protection).
            stop_order_type: ``stop_loss`` (default) or ``take_profit``.
            price: Optional limit price of the order placed after activation.
                Omitting it asks the broker for a market order on activation.
            expiration_type: ``good_till_cancel`` (default) or ``good_till_date``.
            expire_date: Required when ``expiration_type='good_till_date'``.
            order_id: Client idempotency key; generated when omitted and reused
                by every retry of the same logical call.

        Returns:
            :class:`SandboxStopOrder` with the broker ``stop_order_id``.
        """
        instrument_id = instrument_id.strip()
        direction_key = direction.strip().lower()
        type_key = stop_order_type.strip().lower()
        expiration_key = expiration_type.strip().lower()
        if not instrument_id:
            raise ValueError("instrument_id must not be empty")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer number of lots")
        if direction_key not in STOP_ORDER_DIRECTION_MAP:
            raise ValueError("direction must be 'buy' or 'sell'")
        if type_key not in STOP_ORDER_TYPE_MAP:
            raise ValueError("stop_order_type must be 'stop_loss' or 'take_profit'")
        if expiration_key not in STOP_ORDER_EXPIRATION_MAP:
            raise ValueError(
                "expiration_type must be 'good_till_cancel' or 'good_till_date'"
            )
        if expiration_key == "good_till_date" and expire_date is None:
            raise ValueError("expire_date is required for good_till_date stops")

        decimal_stop = Decimal(str(stop_price))
        if decimal_stop <= 0:
            raise ValueError("stop_price must be positive")
        decimal_price = Decimal(str(price)) if price is not None else None
        if decimal_price is not None and decimal_price <= 0:
            raise ValueError("price must be positive when provided")

        request_id = (order_id or str(uuid4())).strip()
        if not request_id:
            raise ValueError("order_id must not be empty")

        def request(sandbox: Any) -> Any:
            payload = PostStopOrderRequest()
            payload.account_id = self._resolve_account_id(sandbox)
            payload.instrument_id = instrument_id
            payload.quantity = quantity
            payload.stop_price = _quotation(decimal_stop)
            if decimal_price is not None:
                payload.price = _quotation(decimal_price)
            payload.direction = STOP_ORDER_DIRECTION_MAP[direction_key]
            payload.stop_order_type = STOP_ORDER_TYPE_MAP[type_key]
            payload.expiration_type = STOP_ORDER_EXPIRATION_MAP[expiration_key]
            if expire_date is not None:
                payload.expire_date = expire_date
            payload.order_id = request_id
            return sandbox.post_sandbox_stop_order(request=payload)

        response = self._call("post_stop_order", request)
        result = SandboxStopOrder(
            stop_order_id=str(getattr(response, "stop_order_id", "") or request_id),
            order_request_id=str(getattr(response, "order_request_id", "") or ""),
        )
        logger.info(
            "Sandbox stop order %s submitted: instrument=%s type=%s direction=%s "
            "lots=%s stop_price=%s price=%s",
            result.stop_order_id,
            instrument_id,
            type_key,
            direction_key,
            quantity,
            decimal_stop,
            decimal_price,
        )
        return result


    def get_stop_orders(
        self,
        *,
        status: str = "active",
        instrument_id: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> List[SandboxStopOrderState]:
        """Return broker stop orders (``GetStopOrders``).

        Issue #175: used to verify that protection is still armed, to detect
        orphaned stops after a close (OCO monitoring) and to confirm the new
        stop of an amend-trailing step before cancelling the older one.

        The sandbox API has no instrument filter, so ``instrument_id`` narrows
        the returned list client-side by uid / FIGI / ticker.
        """
        status_key = status.strip().lower()
        if status_key not in STOP_ORDER_STATUS_MAP:
            raise ValueError(
                "status must be one of %s" % ", ".join(sorted(STOP_ORDER_STATUS_MAP))
            )
        needle = (instrument_id or "").strip()

        def request(sandbox: Any) -> Any:
            payload = GetStopOrdersRequest()
            payload.account_id = self._resolve_account_id(sandbox)
            payload.status = STOP_ORDER_STATUS_MAP[status_key]
            if from_date is not None:
                payload.from_ = from_date
            if to_date is not None:
                payload.to = to_date
            return sandbox.get_sandbox_stop_orders(request=payload)

        response = self._call("get_stop_orders", request)
        stops: List[SandboxStopOrderState] = []
        for item in getattr(response, "stop_orders", None) or []:
            exchange_order_id = getattr(item, "exchange_order_id", None)
            state = SandboxStopOrderState(
                stop_order_id=str(getattr(item, "stop_order_id", "")),
                status=_enum_name(getattr(item, "status", "")),
                direction=_enum_name(getattr(item, "direction", "")),
                order_type=_enum_name(getattr(item, "order_type", "")),
                lots_requested=int(getattr(item, "lots_requested", 0) or 0),
                stop_price=_decimal_value(getattr(item, "stop_price", None)),
                price=_decimal_value(getattr(item, "price", None)),
                ticker=str(getattr(item, "ticker", "") or ""),
                figi=str(getattr(item, "figi", "") or ""),
                instrument_uid=str(getattr(item, "instrument_uid", "") or ""),
                currency=str(getattr(item, "currency", "") or ""),
                exchange_order_id=str(exchange_order_id) if exchange_order_id else None,
                created_at=getattr(item, "create_date", None),
                activation_date_time=getattr(item, "activation_date_time", None),
                expiration_time=getattr(item, "expiration_time", None),
            )
            if needle and needle not in {
                state.instrument_uid,
                state.figi,
                state.ticker,
            }:
                continue
            stops.append(state)
        return stops

    def cancel_stop_order(self, stop_order_id: str) -> CancelledSandboxStopOrder:
        """Cancel a broker stop order (``CancelStopOrder``).

        Issue #175: used by amend-trailing (cancel the older, lower stop once the
        new one is confirmed) and by OCO monitoring of orphaned protections.
        """
        stop_order_id = stop_order_id.strip()
        if not stop_order_id:
            raise ValueError("stop_order_id must not be empty")

        def request(sandbox: Any) -> Any:
            payload = CancelStopOrderRequest()
            payload.account_id = self._resolve_account_id(sandbox)
            payload.stop_order_id = stop_order_id
            return sandbox.cancel_sandbox_stop_order(request=payload)

        response = self._call("cancel_stop_order", request)
        logger.info("Sandbox stop order %s cancelled", stop_order_id)
        return CancelledSandboxStopOrder(
            stop_order_id=stop_order_id,
            cancelled_at=getattr(response, "time", None),
        )

    def get_orders(self) -> List[SandboxOrderState]:
        """Return resting (not yet executed) sandbox orders (``GetOrders``).

        Issue #175: OCO monitoring uses this to detect a take-profit limit that
        survived the close of its position.
        """

        def request(sandbox: Any) -> Any:
            return sandbox.get_sandbox_orders(
                account_id=self._resolve_account_id(sandbox)
            )

        response = self._call("get_orders", request)
        orders: List[SandboxOrderState] = []
        for item in getattr(response, "orders", None) or []:
            orders.append(
                SandboxOrderState(
                    order_id=str(getattr(item, "order_id", "")),
                    status=_enum_name(getattr(item, "execution_report_status", "")),
                    direction=_enum_name(getattr(item, "direction", "")),
                    order_type=_enum_name(getattr(item, "order_type", "")),
                    lots_requested=int(getattr(item, "lots_requested", 0) or 0),
                    lots_executed=int(getattr(item, "lots_executed", 0) or 0),
                    figi=str(getattr(item, "figi", "") or ""),
                    instrument_uid=str(getattr(item, "instrument_uid", "") or ""),
                    currency=str(getattr(item, "currency", "") or ""),
                    initial_order_price=_decimal_value(
                        getattr(item, "initial_order_price", None)
                    ),
                    executed_order_price=_decimal_value(
                        getattr(item, "executed_order_price", None)
                    ),
                    order_date=getattr(item, "order_date", None),
                )
            )
        return orders

    def get_operations(
        self,
        *,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        state: str = "all",
        figi: str = "",
    ) -> List[SandboxOperation]:
        """Return broker operations/fills (``GetOperations``).

        Issue #175: the executor reconciles real exits with this call instead of
        trusting model prices — ``exit_price_actual``, ``lots_executed`` and the
        derived slippage come from executed operations.

        Args:
            from_date: Window start (UTC aware datetime recommended).
            to_date: Window end.
            state: ``all`` (default), ``executed``, ``canceled`` or ``progress``.
            figi: Optional FIGI/instrument uid filter passed to the API.
        """
        state_key = state.strip().lower()
        if state_key not in OPERATION_STATE_MAP:
            raise ValueError(
                "state must be one of %s" % ", ".join(sorted(OPERATION_STATE_MAP))
            )

        def request(sandbox: Any) -> Any:
            return sandbox.get_sandbox_operations(
                account_id=self._resolve_account_id(sandbox),
                from_=from_date,
                to=to_date,
                state=OPERATION_STATE_MAP[state_key],
                figi=(figi or "").strip(),
            )

        response = self._call("get_operations", request)
        operations: List[SandboxOperation] = []
        for item in getattr(response, "operations", None) or []:
            trades: List[SandboxOperationTrade] = []
            for trade in getattr(item, "trades", None) or []:
                trades.append(
                    SandboxOperationTrade(
                        trade_id=str(getattr(trade, "trade_id", "") or ""),
                        quantity=int(getattr(trade, "quantity", 0) or 0),
                        price=_decimal_value(getattr(trade, "price", None)),
                        date_time=getattr(trade, "date_time", None),
                    )
                )
            operations.append(
                SandboxOperation(
                    id=str(getattr(item, "id", "") or ""),
                    state=_enum_name(getattr(item, "state", "")),
                    operation_type=_enum_name(getattr(item, "operation_type", "")),
                    quantity=int(getattr(item, "quantity", 0) or 0),
                    quantity_rest=int(getattr(item, "quantity_rest", 0) or 0),
                    price=_decimal_value(getattr(item, "price", None)),
                    payment=_decimal_value(getattr(item, "payment", None)),
                    currency=str(getattr(item, "currency", "") or ""),
                    figi=str(getattr(item, "figi", "") or ""),
                    instrument_uid=str(getattr(item, "instrument_uid", "") or ""),
                    instrument_type=str(getattr(item, "instrument_type", "") or ""),
                    parent_operation_id=str(
                        getattr(item, "parent_operation_id", "") or ""
                    ),
                    date=getattr(item, "date", None),
                    trades=trades,
                )
            )
        return operations

    def _resolve_account_id(self, sandbox: Any) -> str:
        if self.account_id:
            return self.account_id
        if not self.discover_account:
            raise SandboxConfigurationError("Sandbox account id is not configured")

        self._before_request()
        response = sandbox.get_sandbox_accounts()
        open_status = AccountStatus.ACCOUNT_STATUS_OPEN
        accounts = [
            account
            for account in (getattr(response, "accounts", None) or [])
            if getattr(account, "status", None) == open_status
        ]
        if not accounts:
            raise SandboxConfigurationError(
                "No open sandbox account found; set TINVEST_SANDBOX_ACC "
                "or open an account"
            )
        self.account_id = str(accounts[0].id)
        logger.info(
            "Using discovered sandbox account %s",
            _masked_account(self.account_id),
        )
        return self.account_id

    def _call(self, operation: str, request: Callable[[Any], Any]) -> Any:
        for attempt in range(1, self.retry_attempts + 1):
            try:
                self._before_request()
                with self._client_factory(
                    self.sandbox_token,
                    target=INVEST_GRPC_API_SANDBOX,
                    sandbox_token=self.sandbox_token,
                ) as services:
                    return request(services.sandbox)
            except SandboxClientError:
                raise
            except SDK_RETRYABLE_ERRORS as exc:
                code_name = _enum_name(getattr(exc, "code", ""))
                account_not_found = (
                    code_name == "NOT_FOUND"
                    and str(getattr(exc, "details", "")) == "50004"
                    and self.discover_account
                    and bool(self.account_id)
                    and attempt < self.retry_attempts
                )
                if account_not_found:
                    logger.warning(
                        "Configured account %s is not a sandbox account; "
                        "falling back to sandbox account discovery",
                        _masked_account(self.account_id),
                    )
                    self.account_id = ""
                    continue
                can_retry = (
                    code_name in RETRYABLE_GRPC_CODES
                    and attempt < self.retry_attempts
                )
                if not can_retry:
                    logger.error(
                        "T-Bank sandbox %s failed: grpc_code=%s attempts=%s",
                        operation,
                        code_name,
                        attempt,
                    )
                    raise SandboxAPIError(
                        f"T-Bank sandbox {operation} failed ({code_name})"
                    ) from exc
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Retrying T-Bank sandbox %s: grpc_code=%s attempt=%s/%s delay=%.2fs",
                    operation,
                    code_name,
                    attempt + 1,
                    self.retry_attempts,
                    delay,
                )
                self._sleep(delay)
            except Exception as exc:
                logger.error(
                    "T-Bank sandbox %s failed with %s",
                    operation,
                    type(exc).__name__,
                )
                raise SandboxAPIError(
                    f"T-Bank sandbox {operation} failed"
                ) from exc
        raise AssertionError("unreachable")
