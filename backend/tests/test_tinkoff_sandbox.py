from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.broker import tinkoff_sandbox as module
from app.broker.tinkoff_sandbox import (
    SandboxAPIError,
    SandboxConfigurationError,
    TinkoffSandboxClient,
)


def value(units=0, nano=0, currency="rub"):
    return SimpleNamespace(units=units, nano=nano, currency=currency)


class FakeClientContext:
    def __init__(self, sandbox):
        self.services = SimpleNamespace(sandbox=sandbox)

    def __enter__(self):
        return self.services

    def __exit__(self, *_args):
        return False


def factory_for(sandbox):
    return lambda *_args, **_kwargs: FakeClientContext(sandbox)


def order_response(order_id="order-1"):
    return SimpleNamespace(
        order_id=order_id,
        execution_report_status=SimpleNamespace(name="EXECUTION_REPORT_STATUS_FILL"),
        lots_requested=2,
        lots_executed=2,
        initial_order_price=value(101, 250_000_000),
        executed_order_price=value(101, 300_000_000),
        total_order_amount=value(202, 600_000_000),
        executed_commission=value(0, 60_000_000),
        message="",
    )


def test_execute_limit_order_uses_sandbox_service_and_converts_price():
    class Sandbox:
        def post_sandbox_order(self, **kwargs):
            self.kwargs = kwargs
            return order_response(kwargs["order_id"])

    sandbox = Sandbox()
    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=factory_for(sandbox),
    )

    result = client.execute_order(
        instrument_id="instrument-uid",
        quantity=2,
        direction="buy",
        order_type="limit",
        price="101.25",
        order_id="stable-request-id",
    )

    assert result.order_id == "stable-request-id"
    assert result.executed_order_price == Decimal("101.3")
    assert sandbox.kwargs["account_id"] == "sandbox-account"
    assert sandbox.kwargs["instrument_id"] == "instrument-uid"
    assert sandbox.kwargs["price"].units == 101
    assert sandbox.kwargs["price"].nano == 250_000_000
    assert (
        sandbox.kwargs["order_type"]
        == module.OrderType.ORDER_TYPE_LIMIT
    )


def test_check_balance_returns_only_requested_free_currency():
    class Sandbox:
        def get_sandbox_positions(self, **kwargs):
            assert kwargs["account_id"] == "sandbox-account"
            return SimpleNamespace(
                money=[
                    value(50_000, 500_000_000, "rub"),
                    value(10, 0, "usd"),
                ]
            )

    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=factory_for(Sandbox()),
    )

    assert client.check_balance() == Decimal("50000.5")
    assert client.check_balance("usd") == Decimal("10")


def test_client_uses_dedicated_sandbox_endpoint():
    captured = {}

    class Sandbox:
        def get_sandbox_positions(self, **_kwargs):
            return SimpleNamespace(money=[])

    def client_factory(*_args, **kwargs):
        captured.update(kwargs)
        return FakeClientContext(Sandbox())

    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=client_factory,
    )

    client.check_balance()

    assert captured["target"] == module.INVEST_GRPC_API_SANDBOX


def test_client_never_falls_back_to_market_data_token(monkeypatch):
    monkeypatch.setattr(
        module,
        "load_settings",
        lambda: SimpleNamespace(
            api=SimpleNamespace(
                token="market-data-token",
                sandbox_token="",
                account_id="",
                sandbox_account_id="",
            )
        ),
    )

    with pytest.raises(SandboxConfigurationError, match="TINVEST_SANDBOX"):
        TinkoffSandboxClient()


def test_get_positions_discovers_open_account_and_skips_zero_positions():
    class Sandbox:
        def get_sandbox_accounts(self):
            return SimpleNamespace(
                accounts=[
                    SimpleNamespace(
                        id="discovered-account",
                        status=module.AccountStatus.ACCOUNT_STATUS_OPEN,
                    )
                ]
            )

        def get_sandbox_portfolio(self, **kwargs):
            assert kwargs["account_id"] == "discovered-account"
            return SimpleNamespace(
                positions=[
                    SimpleNamespace(
                        figi="figi-1",
                        ticker="SBER",
                        instrument_uid="uid-1",
                        instrument_type="share",
                        quantity=value(20),
                        quantity_lots=value(2),
                        blocked_lots=value(1),
                        average_position_price=value(300),
                        current_price=value(310),
                        expected_yield=value(200),
                    ),
                    SimpleNamespace(
                        quantity=value(0),
                        quantity_lots=value(0),
                    ),
                ]
            )

    client = TinkoffSandboxClient(
        token="token",
        account_id="",
        client_factory=factory_for(Sandbox()),
    )

    positions = client.get_positions()

    assert client.account_id == "discovered-account"
    assert len(positions) == 1
    assert positions[0].ticker == "SBER"
    assert positions[0].quantity_lots == Decimal("2")
    assert positions[0].blocked_lots == Decimal("1")


def test_cancel_order_calls_sandbox_service():
    cancelled_at = object()

    class Sandbox:
        def cancel_sandbox_order(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(time=cancelled_at)

    sandbox = Sandbox()
    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=factory_for(sandbox),
    )

    result = client.cancel_order("order-1")

    assert sandbox.kwargs == {
        "account_id": "sandbox-account",
        "order_id": "order-1",
    }
    assert result.order_id == "order-1"
    assert result.cancelled_at is cancelled_at


def test_transient_api_error_retries_with_same_order_id(monkeypatch):
    class TransientError(Exception):
        code = SimpleNamespace(name="UNAVAILABLE")

    class Sandbox:
        def __init__(self):
            self.order_ids = []

        def post_sandbox_order(self, **kwargs):
            self.order_ids.append(kwargs["order_id"])
            if len(self.order_ids) == 1:
                raise TransientError()
            return order_response(kwargs["order_id"])

    sleeps = []
    sandbox = Sandbox()
    monkeypatch.setattr(module, "SDK_RETRYABLE_ERRORS", (TransientError,))
    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=factory_for(sandbox),
        sleep_fn=sleeps.append,
    )

    result = client.execute_order(
        instrument_id="uid",
        quantity=1,
        order_id="idempotent-order-id",
    )

    assert result.order_id == "idempotent-order-id"
    assert sandbox.order_ids == ["idempotent-order-id", "idempotent-order-id"]
    assert sleeps == [0.5]


def test_non_retryable_api_error_is_wrapped(monkeypatch):
    class PermissionErrorFromAPI(Exception):
        code = SimpleNamespace(name="PERMISSION_DENIED")

    class Sandbox:
        def get_sandbox_positions(self, **_kwargs):
            raise PermissionErrorFromAPI()

    monkeypatch.setattr(
        module, "SDK_RETRYABLE_ERRORS", (PermissionErrorFromAPI,)
    )
    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=factory_for(Sandbox()),
    )

    with pytest.raises(SandboxAPIError, match="PERMISSION_DENIED"):
        client.check_balance()


def test_invalid_configured_account_falls_back_to_discovered_sandbox(monkeypatch):
    class AccountNotFoundError(Exception):
        code = SimpleNamespace(name="NOT_FOUND")
        details = "50004"

    class Sandbox:
        def __init__(self):
            self.requested_accounts = []

        def get_sandbox_accounts(self):
            return SimpleNamespace(
                accounts=[
                    SimpleNamespace(
                        id="open-sandbox",
                        status=module.AccountStatus.ACCOUNT_STATUS_OPEN,
                    )
                ]
            )

        def get_sandbox_positions(self, **kwargs):
            self.requested_accounts.append(kwargs["account_id"])
            if kwargs["account_id"] == "production-account":
                raise AccountNotFoundError()
            return SimpleNamespace(money=[value(50_000)])

    sandbox = Sandbox()
    monkeypatch.setattr(module, "SDK_RETRYABLE_ERRORS", (AccountNotFoundError,))
    client = TinkoffSandboxClient(
        token="token",
        account_id="production-account",
        client_factory=factory_for(sandbox),
    )

    assert client.check_balance() == Decimal("50000")
    assert client.account_id == "open-sandbox"
    assert sandbox.requested_accounts == ["production-account", "open-sandbox"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"instrument_id": "", "quantity": 1}, "instrument_id"),
        ({"instrument_id": "uid", "quantity": 0}, "quantity"),
        (
            {
                "instrument_id": "uid",
                "quantity": 1,
                "order_type": "limit",
            },
            "price",
        ),
        (
            {
                "instrument_id": "uid",
                "quantity": 1,
                "order_type": "market",
                "price": 10,
            },
            "price",
        ),
    ],
)
def test_execute_order_validates_request(kwargs, message):
    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(ValueError, match=message):
        client.execute_order(**kwargs)
