"""Synchronous client for order execution against the REAL T-Bank account.

Issue #178. This module is the deliberate mirror of
:mod:`app.broker.tinkoff_sandbox`: same method names, same keyword arguments,
same return dataclasses, same retry policy - but pointed at
``INVEST_GRPC_API`` and at the real ``orders`` / ``stop_orders`` /
``operations`` / ``users`` services instead of ``sandbox``.

Because the contract is duck-typed and identical, :class:`LiveExecutor` needs no
branching: it calls ``execute_order`` / ``post_stop_order`` / ``get_stop_orders``
/ ``get_operations`` and receives the same shapes. That is why the errors below
inherit from the sandbox error types - every existing ``except SandboxAPIError``
handler in the executor keeps working when the broker is a real account.

Safety model (Epic #172 red lines):

* instantiating :class:`TinkoffLiveClient` is **refused** unless
  ``SANDBOX_TRADING.allow_real_trading`` is true (``ALLOW_REAL_TRADING`` env on
  the deployment, decision of the Product Owner). There is no sandbox
  fallback and no silent degradation: a real-money client is either explicitly
  allowed or does not exist;
* the real token is read from ``TINVEST_LIVE_TOKEN`` only. ``TINVEST_TOKEN``
  (market data) and ``TINVEST_SANDBOX`` (sandbox) are never consulted, so a
  real token cannot leak into the sandbox contour and vice versa. Filling both
  variables with the *same* secret is refused as a misconfiguration unless the
  deployment opts in explicitly through ``ALLOW_LIVE_TOKEN_REUSE`` (Issue #192) -
  and even then the two variables stay independent: no fallback is introduced;
* the token is never logged; the account id is logged masked.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from app.analytics.trading_config import get_sandbox_trading_config
from app.broker.tinkoff_sandbox import (
    OPERATION_STATE_MAP,
    RETRYABLE_GRPC_CODES,
    STOP_ORDER_DIRECTION_MAP,
    STOP_ORDER_EXPIRATION_MAP,
    STOP_ORDER_STATUS_MAP,
    STOP_ORDER_TYPE_MAP,
    CancelledSandboxOrder,
    CancelledSandboxStopOrder,
    SandboxAPIError,
    SandboxClientError,
    SandboxConfigurationError,
    SandboxOperation,
    SandboxOperationTrade,
    SandboxOrder,
    SandboxOrderState,
    SandboxPosition,
    SandboxStopOrder,
    SandboxStopOrderState,
    _decimal_value,
    _enum_name,
    _masked_account,
    _quotation,
)
from app.core.config_manager import load_settings

try:
    from t_tech.invest import (
        AccountStatus,
        Client,
        OrderDirection,
        OrderType,
    )
    from t_tech.invest.constants import INVEST_GRPC_API
    from t_tech.invest.exceptions import RequestError

    IS_LIVE_SDK_AVAILABLE = True
    LIVE_SDK_RETRYABLE_ERRORS: tuple = (RequestError,)
    ORDER_DIRECTION_MAP: Dict[str, Any] = {
        "buy": OrderDirection.ORDER_DIRECTION_BUY,
        "sell": OrderDirection.ORDER_DIRECTION_SELL,
    }
    ORDER_TYPE_MAP: Dict[str, Any] = {
        "market": OrderType.ORDER_TYPE_MARKET,
        "limit": OrderType.ORDER_TYPE_LIMIT,
    }
except ImportError:  # pragma: no cover - exercised only in a broken deployment
    AccountStatus = Client = None
    OrderDirection = OrderType = None
    INVEST_GRPC_API = None
    IS_LIVE_SDK_AVAILABLE = False
    LIVE_SDK_RETRYABLE_ERRORS = ()
    ORDER_DIRECTION_MAP = {}
    ORDER_TYPE_MAP = {}


logger = logging.getLogger(__name__)

#: Duck-typed aliases: the live contour returns exactly the structures the
#: executor already consumes, so no call site has to know which contour it is on.
LiveOrder = SandboxOrder
LivePosition = SandboxPosition
CancelledLiveOrder = CancelledSandboxOrder
LiveStopOrder = SandboxStopOrder
LiveStopOrderState = SandboxStopOrderState
CancelledLiveStopOrder = CancelledSandboxStopOrder
LiveOrderState = SandboxOrderState
LiveOperation = SandboxOperation
LiveOperationTrade = SandboxOperationTrade


class LiveClientError(SandboxClientError):
    """Base error of the real-money client.

    Inherits :class:`SandboxClientError` on purpose: the executor catches the
    sandbox types around every broker call and must not need a second handler
    for the real contour.
    """


class LiveConfigurationError(SandboxConfigurationError, LiveClientError):
    """The real contour cannot start: configuration is missing or unsafe."""


class LiveAPIError(SandboxAPIError, LiveClientError):
    """A real T-Bank Invest API request failed."""
class TinkoffLiveClient:
    """Execute orders, stop orders and inspect funds on a REAL T-Bank account.

    The public surface is the sandbox client's surface, method for method:

    ==========================  =====================================
    Method                      Real API call
    ==========================  =====================================
    :meth:`execute_order`       ``orders.post_order``
    :meth:`cancel_order`        ``orders.cancel_order``
    :meth:`get_orders`          ``orders.get_orders``
    :meth:`post_stop_order`     ``stop_orders.post_stop_order``
    :meth:`get_stop_orders`     ``stop_orders.get_stop_orders``
    :meth:`cancel_stop_order`   ``stop_orders.cancel_stop_order``
    :meth:`get_operations`      ``operations.get_operations``
    :meth:`get_positions`       ``operations.get_portfolio``
    :meth:`check_balance`       ``operations.get_positions``
    account discovery           ``users.get_accounts``
    ==========================  =====================================

    Two contract details differ from the sandbox and are absorbed here so no
    caller has to know which contour it runs against:

    * every real service method is keyword-only in t-tech-investments 1.51.0 and
      takes its arguments directly - unlike ``SandboxService``, none of them
      accepts a ``request=`` payload object (Issue #192: passing one raised
      ``TypeError`` before the request left the process). ``orders.post_order``
      takes the client idempotency key as ``order_id``, like the sandbox one;
    * ``GetStopOrders`` does support ``from_`` / ``to``, but
      :meth:`get_stop_orders` keeps ignoring ``from_date`` / ``to_date`` and
      filtering client-side, so the executor sees one behaviour on both contours.
    """

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        account_id: Optional[str] = None,
        client_factory: Optional[Callable[..., Any]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        before_request: Optional[Callable[[], None]] = None,
        allow_real_trading: Optional[bool] = None,
    ) -> None:
        """Build the real-money client, refusing unless it is explicitly allowed.

        Args:
            token: Real contour token. Defaults to ``TINVEST_LIVE_TOKEN``
                (``settings.api.live_token``); the market-data and sandbox
                tokens are never used as a fallback.
            account_id: Real account id. Defaults to ``TINVEST_LIVE_ACC``; when
                empty and discovery is on, the first open account is used (D6).
            client_factory: Injectable ``Client`` for the tests.
            sleep_fn: Injectable sleep for the retry backoff.
            before_request: Rate-limit hook, identical to the sandbox client's -
                ``LiveExecutor`` throttles every attempt through its bucket.
            allow_real_trading: Test/diagnostics override of the
                ``SANDBOX_TRADING.allow_real_trading`` gate. ``None`` (the
                production path) reads the policy.

        Raises:
            LiveConfigurationError: SDK missing, gate closed, trading disabled
                or credentials absent.
        """
        if not IS_LIVE_SDK_AVAILABLE:
            raise LiveConfigurationError("t-tech-investments SDK is not available")

        settings = load_settings()
        policy = get_sandbox_trading_config()
        allowed = (
            bool(policy.get("allow_real_trading"))
            if allow_real_trading is None
            else bool(allow_real_trading)
        )
        # Fail fast: the real contour is opt-in per deployment, never per call.
        if not allowed:
            raise LiveConfigurationError(
                "Refusing to build a real-money client: allow_real_trading is "
                "false (set ALLOW_REAL_TRADING=true on the deployment only)"
            )
        if not policy.get("enabled"):
            raise LiveConfigurationError("Live trading is disabled")

        self.live_token = (
            token if token is not None else settings.api.live_token
        ).strip()
        self.account_id = (
            account_id if account_id is not None else settings.api.live_account_id
        ).strip()
        self.retry_attempts = max(1, int(policy["retry_attempts"]))
        self.retry_base_delay = max(0.0, float(policy["retry_base_delay_seconds"]))
        self.discover_account = bool(policy["discover_account_when_missing"])
        # Issue #192: explicit opt-in for one physical token serving both the
        # market-data and the real contour. ``False`` in code (trading_config),
        # overridable per deployment through ``ALLOW_LIVE_TOKEN_REUSE``. It never
        # enables a fallback between contours - it only stops the duplicate-value
        # check below from refusing a deliberate configuration.
        self.allow_token_reuse = bool(policy.get("allow_live_token_reuse", False))
        self._client_factory = client_factory or Client
        self._sleep = sleep_fn
        self._before_request = before_request or (lambda: None)

        if not self.live_token:
            raise LiveConfigurationError("TINVEST_LIVE_TOKEN is empty")
        market_token = str(settings.api.token or "").strip()
        if market_token and self.live_token == market_token:
            # Same secret in both variables usually means the operator filled the
            # wrong one; refusing is cheaper than trading on an ambiguous
            # credential, so this stays the default. A deployment that
            # deliberately runs one physical token for market data AND for the
            # real contour opts in explicitly (``ALLOW_LIVE_TOKEN_REUSE``, Issue
            # #192, PO decision of 2026-09-30). Even then there is no fallback:
            # the real client reads ``TINVEST_LIVE_TOKEN`` only.
            if not self.allow_token_reuse:
                raise LiveConfigurationError(
                    "TINVEST_LIVE_TOKEN must not reuse the market-data "
                    "TINVEST_TOKEN (set ALLOW_LIVE_TOKEN_REUSE=true only when one "
                    "physical token is intended for both contours)"
                )
            logger.warning(
                "TINVEST_LIVE_TOKEN reuses the market-data TINVEST_TOKEN "
                "(ALLOW_LIVE_TOKEN_REUSE=true): one physical token serves both "
                "contours; rotating it changes market data and trading together"
            )
        if not self.account_id and not self.discover_account:
            raise LiveConfigurationError(
                "TINVEST_LIVE_ACC is empty and account discovery is disabled"
            )

        logger.info(
            "TinkoffLiveClient ready for the REAL contour: account=%s retries=%s "
            "discovery=%s",
            _masked_account(self.account_id),
            self.retry_attempts,
            self.discover_account,
        )


    # --- transport ----------------------------------------------------------

    def _call(
        self,
        operation: str,
        service_name: str,
        request: Callable[[Any], Any],
    ) -> Any:
        """Run one real API call with the sandbox retry policy.

        ``service_name`` selects the sub-service of ``Services`` the closure
        receives (``orders`` / ``stop_orders`` / ``operations`` / ``users``) -
        the real contour, unlike the sandbox, spreads its methods over several
        services.

        Retries follow ``SANDBOX_TRADING`` (attempts, exponential backoff) and
        only for the gRPC codes worth retrying; anything else becomes
        :class:`LiveAPIError` immediately so the executor's protection logic can
        react instead of waiting out a backoff.
        """
        for attempt in range(1, self.retry_attempts + 1):
            try:
                self._before_request()
                with self._client_factory(
                    self.live_token,
                    target=INVEST_GRPC_API,
                ) as services:
                    return request(getattr(services, service_name))
            except SandboxClientError:
                raise
            except LIVE_SDK_RETRYABLE_ERRORS as exc:
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
                        "Configured account %s was rejected by the real contour; "
                        "falling back to account discovery",
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
                        "T-Bank LIVE %s failed: grpc_code=%s attempts=%s",
                        operation,
                        code_name,
                        attempt,
                    )
                    raise LiveAPIError(
                        f"T-Bank live {operation} failed ({code_name})"
                    ) from exc
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Retrying T-Bank LIVE %s: grpc_code=%s attempt=%s/%s delay=%.2fs",
                    operation,
                    code_name,
                    attempt + 1,
                    self.retry_attempts,
                    delay,
                )
                self._sleep(delay)
            except Exception as exc:
                logger.error(
                    "T-Bank LIVE %s failed with %s",
                    operation,
                    type(exc).__name__,
                )
                raise LiveAPIError(f"T-Bank live {operation} failed") from exc
        raise AssertionError("unreachable")

    def _resolve_account_id(self) -> str:
        """Return the real account id, discovering it once when not configured.

        Called from inside the request closures (like the sandbox client) so that
        a retry after a rejected account id re-discovers instead of reusing the
        stale value. Discovery opens its own short-lived client context and is
        cached on ``self.account_id``, so it costs one extra ``GetAccounts`` per
        process at most.
        """
        if self.account_id:
            return self.account_id
        if not self.discover_account:
            raise LiveConfigurationError("Live account id is not configured")

        def request(users: Any) -> Any:
            return users.get_accounts()

        response = self._call("get_accounts", "users", request)
        open_status = AccountStatus.ACCOUNT_STATUS_OPEN
        accounts = [
            account
            for account in (getattr(response, "accounts", None) or [])
            if getattr(account, "status", None) == open_status
        ]
        if not accounts:
            raise LiveConfigurationError(
                "No open real account found; set TINVEST_LIVE_ACC explicitly"
            )
        if len(accounts) > 1:
            logger.warning(
                "Discovered %d open real accounts; using %s. "
                "Pin TINVEST_LIVE_ACC to make the choice explicit.",
                len(accounts),
                _masked_account(str(accounts[0].id)),
            )
        self.account_id = str(accounts[0].id)
        logger.info(
            "Using discovered real account %s",
            _masked_account(self.account_id),
        )
        return self.account_id

    # --- market / limit orders ---------------------------------------------

    def execute_order(
        self,
        *,
        instrument_id: str,
        quantity: int,
        direction: str = "buy",
        order_type: str = "market",
        price: Optional[Decimal | float | str] = None,
        order_id: Optional[str] = None,
    ) -> LiveOrder:
        """Place a market or limit order on the real account.

        Quantity is expressed in **lots**, exactly like the sandbox client. The
        idempotency key is generated before the first attempt and reused by every
        retry, so a retried ``post_order`` cannot fill twice - on a real account
        that is the difference between one position and two.
        """
        instrument_id = instrument_id.strip()
        direction_key = direction.strip().lower()
        order_type_key = order_type.strip().lower()
        if not instrument_id:
            raise ValueError("instrument_id must not be empty")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer number of lots")
        if direction_key not in ORDER_DIRECTION_MAP:
            raise ValueError("direction must be 'buy' or 'sell'")
        if order_type_key not in ORDER_TYPE_MAP:
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

        def request(orders: Any) -> Any:
            # Resolved inside the closure (like the sandbox client) so a retry
            # after a rejected account id re-discovers instead of reusing it.
            account_id = self._resolve_account_id()
            return orders.post_order(
                instrument_id=instrument_id,
                quantity=quantity,
                price=_quotation(decimal_price) if decimal_price is not None else None,
                direction=ORDER_DIRECTION_MAP[direction_key],
                account_id=account_id,
                order_type=ORDER_TYPE_MAP[order_type_key],
                # Issue #192: the real contour keys idempotency on ``order_id``
                # (``PostOrderRequest.order_id``). ``idempotence_id`` does not
                # exist in t-tech-investments 1.51.0 and raised TypeError before
                # the request ever left the process.
                order_id=request_id,
            )

        response = self._call("execute_order", "orders", request)
        result = LiveOrder(
            order_id=str(getattr(response, "order_id", "") or request_id),
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
            "LIVE order %s submitted: instrument=%s type=%s direction=%s lots=%s",
            result.order_id,
            instrument_id,
            order_type_key,
            direction_key,
            quantity,
        )
        return result

    def cancel_order(self, order_id: str) -> CancelledLiveOrder:
        """Cancel an active order on the real account."""
        order_id = order_id.strip()
        if not order_id:
            raise ValueError("order_id must not be empty")
        def request(orders: Any) -> Any:
            return orders.cancel_order(
                account_id=self._resolve_account_id(),
                order_id=order_id,
            )

        response = self._call("cancel_order", "orders", request)
        logger.info("LIVE order %s cancelled", order_id)
        return CancelledLiveOrder(
            order_id=order_id,
            cancelled_at=getattr(response, "time", None),
        )

    def get_orders(self) -> List[LiveOrderState]:
        """Return resting (not yet executed) orders of the real account.

        OCO monitoring (#175) uses this to detect a take-profit limit that
        outlived its position.
        """
        def request(orders: Any) -> Any:
            return orders.get_orders(account_id=self._resolve_account_id())

        response = self._call("get_orders", "orders", request)
        resting: List[LiveOrderState] = []
        for item in getattr(response, "orders", None) or []:
            resting.append(
                LiveOrderState(
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
        return resting

    # --- broker stop orders (protection) ------------------------------------

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
    ) -> LiveStopOrder:
        """Place a broker stop order (``PostStopOrder``) on the real account.

        This is the protection the executor arms as soon as an entry fills and
        amends by duplication on every trailing step (#175). Semantics, argument
        names and the returned structure are the sandbox client's, so the amend /
        verify / cancel-old sequence needs no contour-specific branch.

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
            :class:`LiveStopOrder` with the broker ``stop_order_id``.
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
        def request(stop_orders: Any) -> Any:
            # Issue #192: ``StopOrdersService.post_stop_order`` is keyword-only in
            # t-tech-investments 1.51.0 and does NOT accept ``request=``; passing
            # the dataclass raised TypeError before the request left the process.
            kwargs: Dict[str, Any] = {
                "account_id": self._resolve_account_id(),
                "instrument_id": instrument_id,
                "quantity": quantity,
                "stop_price": _quotation(decimal_stop),
                "direction": STOP_ORDER_DIRECTION_MAP[direction_key],
                "stop_order_type": STOP_ORDER_TYPE_MAP[type_key],
                "expiration_type": STOP_ORDER_EXPIRATION_MAP[expiration_key],
                "order_id": request_id,
            }
            if decimal_price is not None:
                kwargs["price"] = _quotation(decimal_price)
            if expire_date is not None:
                kwargs["expire_date"] = expire_date
            return stop_orders.post_stop_order(**kwargs)

        response = self._call("post_stop_order", "stop_orders", request)
        result = LiveStopOrder(
            stop_order_id=str(getattr(response, "stop_order_id", "") or request_id),
            order_request_id=str(getattr(response, "order_request_id", "") or ""),
        )
        logger.info(
            "LIVE stop order %s submitted: instrument=%s type=%s direction=%s "
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
    ) -> List[LiveStopOrderState]:
        """Return broker stop orders (``GetStopOrders``) of the real account.

        Used to verify that protection is still armed, to detect orphaned stops
        after a close (OCO monitoring) and to confirm the new stop of an
        amend-trailing step before cancelling the older one.

        ``from_date`` / ``to_date`` are accepted for call-signature parity with
        the sandbox client and **ignored**: the real ``GetStopOrders`` does take
        ``from_`` / ``to``, but filtering stays client-side so both contours
        behave identically. ``instrument_id`` narrows the result client-side by
        uid / FIGI / ticker, exactly like the sandbox client.
        """
        status_key = status.strip().lower()
        if status_key not in STOP_ORDER_STATUS_MAP:
            raise ValueError(
                "status must be one of %s" % ", ".join(sorted(STOP_ORDER_STATUS_MAP))
            )
        if from_date is not None or to_date is not None:
            logger.debug(
                "GetStopOrders has no date filter on the real contour; "
                "from_date/to_date are ignored"
            )
        needle = (instrument_id or "").strip()

        def request(stop_orders: Any) -> Any:
            # Issue #192: keyword-only in t-tech-investments 1.51.0 - ``request=``
            # raised TypeError. ``from_`` / ``to`` exist in the SDK but stay
            # unused on purpose: date filtering remains client-side so the
            # executor keeps the behaviour it has on the sandbox contour.
            return stop_orders.get_stop_orders(
                account_id=self._resolve_account_id(),
                status=STOP_ORDER_STATUS_MAP[status_key],
            )

        response = self._call("get_stop_orders", "stop_orders", request)
        stops: List[LiveStopOrderState] = []
        for item in getattr(response, "stop_orders", None) or []:
            exchange_order_id = getattr(item, "exchange_order_id", None)
            state = LiveStopOrderState(
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

    def cancel_stop_order(self, stop_order_id: str) -> CancelledLiveStopOrder:
        """Cancel a broker stop order (``CancelStopOrder``) on the real account.

        Used by amend-trailing (cancel the older, lower stop once the new one is
        confirmed) and by OCO monitoring of orphaned protections. Cancelling a
        stop while a position is open leaves it naked, which is why the executor
        only calls this after the replacement stop is verified.
        """
        stop_order_id = stop_order_id.strip()
        if not stop_order_id:
            raise ValueError("stop_order_id must not be empty")
        def request(stop_orders: Any) -> Any:
            # Issue #192: keyword-only in t-tech-investments 1.51.0 (``request=``
            # raised TypeError, exactly like the two other stop-order methods).
            return stop_orders.cancel_stop_order(
                account_id=self._resolve_account_id(),
                stop_order_id=stop_order_id,
            )

        response = self._call("cancel_stop_order", "stop_orders", request)
        logger.info("LIVE stop order %s cancelled", stop_order_id)
        return CancelledLiveStopOrder(
            stop_order_id=stop_order_id,
            cancelled_at=getattr(response, "time", None),
        )

    # --- fills, portfolio and cash ------------------------------------------

    def get_operations(
        self,
        *,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        state: str = "all",
        figi: str = "",
    ) -> List[LiveOperation]:
        """Return broker operations/fills (``GetOperations``).

        The executor reconciles real exits with this call instead of trusting
        model prices: ``exit_price_actual``, ``lots_executed`` and the derived
        slippage come from executed operations (#175).

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
        def request(operations: Any) -> Any:
            return operations.get_operations(
                account_id=self._resolve_account_id(),
                from_=from_date,
                to=to_date,
                state=OPERATION_STATE_MAP[state_key],
                figi=(figi or "").strip(),
            )

        response = self._call("get_operations", "operations", request)
        operations_list: List[LiveOperation] = []
        for item in getattr(response, "operations", None) or []:
            trades: List[LiveOperationTrade] = []
            for trade in getattr(item, "trades", None) or []:
                trades.append(
                    LiveOperationTrade(
                        trade_id=str(getattr(trade, "trade_id", "") or ""),
                        quantity=int(getattr(trade, "quantity", 0) or 0),
                        price=_decimal_value(getattr(trade, "price", None)),
                        date_time=getattr(trade, "date_time", None),
                    )
                )
            operations_list.append(
                LiveOperation(
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
        return operations_list

    def get_positions(self) -> List[LivePosition]:
        """Return open non-zero instrument positions of the real portfolio.

        ``operations.get_portfolio`` is the real counterpart of
        ``get_sandbox_portfolio`` and returns the same ``PortfolioPosition``
        shape, so the executor's reconciliation of DB positions against broker
        positions works unchanged.
        """
        def request(operations: Any) -> Any:
            return operations.get_portfolio(account_id=self._resolve_account_id())

        response = self._call("get_positions", "operations", request)
        positions: List[LivePosition] = []
        for item in getattr(response, "positions", None) or []:
            quantity = _decimal_value(getattr(item, "quantity", None)) or Decimal("0")
            quantity_lots = _decimal_value(
                getattr(item, "quantity_lots", None)
            ) or Decimal("0")
            if quantity == 0 and quantity_lots == 0:
                continue
            positions.append(
                LivePosition(
                    figi=str(getattr(item, "figi", "")),
                    ticker=str(getattr(item, "ticker", "")),
                    instrument_uid=str(getattr(item, "instrument_uid", "")),
                    instrument_type=str(getattr(item, "instrument_type", "")),
                    quantity=quantity,
                    quantity_lots=quantity_lots,
                    blocked_lots=_decimal_value(getattr(item, "blocked_lots", None))
                    or Decimal("0"),
                    average_price=_decimal_value(
                        getattr(item, "average_position_price", None)
                    ),
                    current_price=_decimal_value(getattr(item, "current_price", None)),
                    expected_yield=_decimal_value(
                        getattr(item, "expected_yield", None)
                    ),
                )
            )
        return positions

    def check_balance(self, currency: Optional[str] = None) -> Decimal:
        """Return free cash balance of the real account (RUB by default).

        ``operations.get_positions`` is the real counterpart of
        ``get_sandbox_positions``: it returns the money balances and the limits
        of the account, from which the entry sizer takes the free cash.
        """
        selected_currency = (
            currency or get_sandbox_trading_config()["default_currency"]
        ).strip().lower()
        if not selected_currency:
            raise ValueError("currency must not be empty")
        def request(operations: Any) -> Any:
            return operations.get_positions(account_id=self._resolve_account_id())

        response = self._call("check_balance", "operations", request)
        return sum(
            (
                _decimal_value(item) or Decimal("0")
                for item in (getattr(response, "money", None) or [])
                if str(getattr(item, "currency", "")).lower() == selected_currency
            ),
            Decimal("0"),
        )

