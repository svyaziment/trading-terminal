"""Broker client selection for the live-executor contour (Issue #178).

One place decides whether the executor trades on the T-Bank sandbox or on a real
account: :data:`SANDBOX_TRADING.allow_real_trading`, overridable per deployment
through ``ALLOW_REAL_TRADING``. Both clients expose the same duck-typed contract
(``execute_order`` / ``post_stop_order`` / ``get_stop_orders`` /
``cancel_stop_order`` / ``get_orders`` / ``get_operations`` / ``get_positions`` /
``check_balance`` / ``cancel_order``), so nothing downstream of this factory has
to branch on the contour - and nothing downstream *can* accidentally trade real
money while believing it is on the sandbox.

The choice is logged at WARNING for the real contour on purpose: it must be
visible in ``reports/live-executor/executor.log`` and in the container log of
every start, so "which account did this process trade?" is answerable after the
fact.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from app.analytics.trading_config import get_sandbox_trading_config
from app.broker.tinkoff_sandbox import TinkoffSandboxClient


logger = logging.getLogger(__name__)

#: Contour names published in the executor metrics snapshot.
SANDBOX_CONTOUR = "sandbox"
REAL_CONTOUR = "real"


def real_trading_allowed() -> bool:
    """True when the deployment explicitly enabled the real-money contour."""
    return bool(get_sandbox_trading_config().get("allow_real_trading"))


def contour_name(*, allow_real_trading: Optional[bool] = None) -> str:
    """Return ``real`` or ``sandbox`` for the given (or configured) gate."""
    allowed = real_trading_allowed() if allow_real_trading is None else bool(
        allow_real_trading
    )
    return REAL_CONTOUR if allowed else SANDBOX_CONTOUR


def create_execution_client(
    *,
    before_request: Optional[Callable[[], None]] = None,
    allow_real_trading: Optional[bool] = None,
) -> Any:
    """Build the broker client the live executor will trade through.

    Args:
        before_request: Rate-limit hook of the embedded client. ``LiveExecutor``
            passes its token-bucket acquire so every attempt - including the
            client's internal retries - is throttled by the executor's policy.
        allow_real_trading: Test/diagnostics override of the configured gate.
            ``None`` (the production path) reads
            ``SANDBOX_TRADING.allow_real_trading`` / ``ALLOW_REAL_TRADING``.

    Returns:
        :class:`TinkoffLiveClient` when the real contour is enabled, otherwise
        :class:`TinkoffSandboxClient`.

    Raises:
        SandboxConfigurationError: the sandbox contour is misconfigured.
        LiveConfigurationError: the real contour is misconfigured (missing
            ``TINVEST_LIVE_TOKEN``/``TINVEST_LIVE_ACC``, or trading disabled).
            It is a subclass of ``SandboxConfigurationError``, so a caller that
            only knows the sandbox type still catches it.
    """
    allowed = real_trading_allowed() if allow_real_trading is None else bool(
        allow_real_trading
    )
    if allowed:
        # Imported lazily: a sandbox deployment never loads the real-money
        # module, so its guard cannot be reached by accident.
        from app.broker.tinkoff_live import TinkoffLiveClient

        logger.warning(
            "Using TinkoffLiveClient: REAL T-Bank account contour "
            "(allow_real_trading=true). Orders are placed with real money."
        )
        return TinkoffLiveClient(
            before_request=before_request,
            allow_real_trading=True,
        )

    logger.info(
        "Using TinkoffSandboxClient: T-Bank sandbox contour "
        "(allow_real_trading=false)"
    )
    return TinkoffSandboxClient(before_request=before_request)


def describe_execution_client(client: Any) -> str:
    """Contour label of an already built client (metrics and alerts)."""
    if isinstance(client, TinkoffSandboxClient):
        return SANDBOX_CONTOUR
    from app.broker.tinkoff_live import TinkoffLiveClient

    if isinstance(client, TinkoffLiveClient):
        return REAL_CONTOUR
    # An injected fake in the tests: report the class name instead of guessing.
    return type(client).__name__
