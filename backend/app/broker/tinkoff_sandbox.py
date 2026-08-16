"""Safe synchronous client for order execution in the T-Bank sandbox."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, List, Optional
from uuid import uuid4

from app.analytics.trading_config import get_sandbox_trading_config
from app.core.config_manager import load_settings

try:
    from t_tech.invest import (
        AccountStatus,
        Client,
        OrderDirection,
        OrderType,
        Quotation,
    )
    from t_tech.invest.constants import INVEST_GRPC_API
    from t_tech.invest.exceptions import RequestError

    IS_SDK_AVAILABLE = True
    SDK_RETRYABLE_ERRORS = (RequestError,)
except ImportError:  # pragma: no cover - exercised only in a broken deployment
    AccountStatus = Client = OrderDirection = OrderType = Quotation = None
    INVEST_GRPC_API = None
    IS_SDK_AVAILABLE = False
    SDK_RETRYABLE_ERRORS = ()


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
    """Execute orders and inspect funds exclusively through ``client.sandbox``."""

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        account_id: Optional[str] = None,
        client_factory: Optional[Callable[..., Any]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not IS_SDK_AVAILABLE:
            raise SandboxConfigurationError("t-tech-investments SDK is not available")

        settings = load_settings()
        policy = get_sandbox_trading_config()
        self.token = (token if token is not None else settings.api.token).strip()
        self.account_id = (
            account_id if account_id is not None else settings.api.account_id
        ).strip()
        self.retry_attempts = max(1, int(policy["retry_attempts"]))
        self.retry_base_delay = max(
            0.0, float(policy["retry_base_delay_seconds"])
        )
        self.discover_account = bool(policy["discover_account_when_missing"])
        self._client_factory = client_factory or Client
        self._sleep = sleep_fn

        if not policy.get("enabled"):
            raise SandboxConfigurationError("Sandbox trading is disabled")
        if policy.get("allow_real_trading"):
            raise SandboxConfigurationError(
                "Unsafe configuration: allow_real_trading must remain false"
            )
        if not self.token:
            raise SandboxConfigurationError("TINVEST_TOKEN is empty")
        if not self.account_id and not self.discover_account:
            raise SandboxConfigurationError(
                "TINVEST_ACC is empty and account discovery is disabled"
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

    def _resolve_account_id(self, sandbox: Any) -> str:
        if self.account_id:
            return self.account_id
        if not self.discover_account:
            raise SandboxConfigurationError("Sandbox account id is not configured")

        response = sandbox.get_sandbox_accounts()
        open_status = AccountStatus.ACCOUNT_STATUS_OPEN
        accounts = [
            account
            for account in (getattr(response, "accounts", None) or [])
            if getattr(account, "status", None) == open_status
        ]
        if not accounts:
            raise SandboxConfigurationError(
                "No open sandbox account found; set TINVEST_ACC or open an account"
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
                with self._client_factory(
                    self.token,
                    target=INVEST_GRPC_API,
                    sandbox_token=self.token,
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
