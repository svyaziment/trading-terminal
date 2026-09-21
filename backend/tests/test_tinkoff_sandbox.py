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
    request_attempts = []
    sandbox = Sandbox()
    monkeypatch.setattr(module, "SDK_RETRYABLE_ERRORS", (TransientError,))
    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=factory_for(sandbox),
        sleep_fn=sleeps.append,
        before_request=lambda: request_attempts.append("token"),
    )

    result = client.execute_order(
        instrument_id="uid",
        quantity=1,
        order_id="idempotent-order-id",
    )

    assert result.order_id == "idempotent-order-id"
    assert sandbox.order_ids == ["idempotent-order-id", "idempotent-order-id"]
    assert sleeps == [0.5]
    assert request_attempts == ["token", "token"]


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


# --- Issue #175: broker stop orders, operations and resting orders ------------


def stop_response(stop_order_id="stop-1"):
    return SimpleNamespace(stop_order_id=stop_order_id, order_request_id="req-1")


def stop_order_item(stop_order_id="stop-1", status="STOP_ORDER_STATUS_ACTIVE", **overrides):
    data = {
        "stop_order_id": stop_order_id,
        "status": SimpleNamespace(name=status),
        "direction": SimpleNamespace(name="STOP_ORDER_DIRECTION_SELL"),
        "order_type": SimpleNamespace(name="STOP_ORDER_TYPE_STOP_LOSS"),
        "lots_requested": 2,
        "stop_price": value(95),
        "price": value(94, 500_000_000),
        "ticker": "SBER",
        "figi": "figi-sber",
        "instrument_uid": "uid-sber",
        "currency": "rub",
        "exchange_order_id": None,
        "create_date": None,
        "activation_date_time": None,
        "expiration_time": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_client(sandbox, **kwargs):
    return TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=factory_for(sandbox),
        **kwargs,
    )


def test_post_stop_order_builds_stop_loss_request():
    class Sandbox:
        def post_sandbox_stop_order(self, **kwargs):
            self.kwargs = kwargs
            return stop_response("stop-42")

    sandbox = Sandbox()
    client = make_client(sandbox)

    result = client.post_stop_order(
        instrument_id="uid-sber",
        quantity=2,
        stop_price="95.00",
        price="94.50",
        order_id="live-stop-41-0-1",
    )

    request = sandbox.kwargs["request"]
    assert result.stop_order_id == "stop-42"
    assert result.order_request_id == "req-1"
    assert request.account_id == "sandbox-account"
    assert request.instrument_id == "uid-sber"
    assert request.quantity == 2
    assert request.stop_price.units == 95
    assert request.stop_price.nano == 0
    assert request.price.units == 94
    assert request.price.nano == 500_000_000
    assert (
        request.direction
        == module.StopOrderDirection.STOP_ORDER_DIRECTION_SELL
    )
    assert (
        request.stop_order_type == module.StopOrderType.STOP_ORDER_TYPE_STOP_LOSS
    )
    assert (
        request.expiration_type
        == module.StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL
    )
    assert request.order_id == "live-stop-41-0-1"


def test_post_stop_order_retries_with_the_same_idempotency_key(monkeypatch):
    class TransientError(Exception):
        code = SimpleNamespace(name="UNAVAILABLE")

    class Sandbox:
        def __init__(self):
            self.order_ids = []

        def post_sandbox_stop_order(self, **kwargs):
            self.order_ids.append(kwargs["request"].order_id)
            if len(self.order_ids) == 1:
                raise TransientError()
            return stop_response("stop-1")

    sleeps = []
    sandbox = Sandbox()
    monkeypatch.setattr(module, "SDK_RETRYABLE_ERRORS", (TransientError,))
    client = make_client(sandbox, sleep_fn=sleeps.append)

    result = client.post_stop_order(
        instrument_id="uid-sber",
        quantity=1,
        stop_price=95,
        order_id="stable-stop-id",
    )

    assert result.stop_order_id == "stop-1"
    assert sandbox.order_ids == ["stable-stop-id", "stable-stop-id"]
    assert sleeps == [0.5]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"instrument_id": "", "quantity": 1, "stop_price": 95}, "instrument_id"),
        ({"instrument_id": "uid", "quantity": 0, "stop_price": 95}, "quantity"),
        ({"instrument_id": "uid", "quantity": 1, "stop_price": 0}, "stop_price"),
        (
            {"instrument_id": "uid", "quantity": 1, "stop_price": 95, "price": 0},
            "price",
        ),
        (
            {
                "instrument_id": "uid",
                "quantity": 1,
                "stop_price": 95,
                "direction": "hold",
            },
            "direction",
        ),
        (
            {
                "instrument_id": "uid",
                "quantity": 1,
                "stop_price": 95,
                "stop_order_type": "stop_limit",
            },
            "stop_order_type",
        ),
        (
            {
                "instrument_id": "uid",
                "quantity": 1,
                "stop_price": 95,
                "expiration_type": "good_till_date",
            },
            "expire_date",
        ),
    ],
)
def test_post_stop_order_validates_request(kwargs, message):
    client = TinkoffSandboxClient(
        token="token",
        account_id="sandbox-account",
        client_factory=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(ValueError, match=message):
        client.post_stop_order(**kwargs)


def test_get_stop_orders_maps_status_and_filters_by_instrument():
    class Sandbox:
        def get_sandbox_stop_orders(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                stop_orders=[
                    stop_order_item("stop-1"),
                    stop_order_item(
                        "stop-2",
                        status="STOP_ORDER_STATUS_EXECUTED",
                        ticker="GAZP",
                        figi="figi-gazp",
                        instrument_uid="uid-gazp",
                        exchange_order_id="exchange-2",
                    ),
                ]
            )

    sandbox = Sandbox()
    client = make_client(sandbox)

    stops = client.get_stop_orders(instrument_id="uid-sber")

    request = sandbox.kwargs["request"]
    assert request.account_id == "sandbox-account"
    assert request.status == module.StopOrderStatusOption.STOP_ORDER_STATUS_ACTIVE
    assert [stop.stop_order_id for stop in stops] == ["stop-1"]
    assert stops[0].status == "STOP_ORDER_STATUS_ACTIVE"
    assert stops[0].direction == "STOP_ORDER_DIRECTION_SELL"
    assert stops[0].order_type == "STOP_ORDER_TYPE_STOP_LOSS"
    assert stops[0].lots_requested == 2
    assert stops[0].stop_price == Decimal("95")
    assert stops[0].price == Decimal("94.5")

    all_stops = client.get_stop_orders(status="all")

    assert [stop.stop_order_id for stop in all_stops] == ["stop-1", "stop-2"]
    assert all_stops[1].status == "STOP_ORDER_STATUS_EXECUTED"
    assert all_stops[1].exchange_order_id == "exchange-2"


def test_get_stop_orders_rejects_unknown_status():
    client = make_client(object())

    with pytest.raises(ValueError, match="status"):
        client.get_stop_orders(status="resting")


def test_cancel_stop_order_calls_sandbox_service():
    cancelled_at = object()

    class Sandbox:
        def cancel_sandbox_stop_order(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(time=cancelled_at)

    sandbox = Sandbox()
    client = make_client(sandbox)

    result = client.cancel_stop_order("stop-7")

    assert sandbox.kwargs["request"].account_id == "sandbox-account"
    assert sandbox.kwargs["request"].stop_order_id == "stop-7"
    assert result.stop_order_id == "stop-7"
    assert result.cancelled_at is cancelled_at


def test_get_operations_maps_fills_and_trades():
    class Sandbox:
        def get_sandbox_operations(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                operations=[
                    SimpleNamespace(
                        id="op-1",
                        parent_operation_id="parent-1",
                        currency="rub",
                        payment=value(-19_100),
                        price=value(95, 500_000_000),
                        state=SimpleNamespace(name="OPERATION_STATE_EXECUTED"),
                        quantity=20,
                        quantity_rest=0,
                        figi="figi-sber",
                        instrument_type="share",
                        instrument_uid="uid-sber",
                        date=None,
                        type="Sell",
                        operation_type=SimpleNamespace(name="OPERATION_TYPE_SELL"),
                        trades=[
                            SimpleNamespace(
                                trade_id="t1",
                                quantity=12,
                                price=value(95, 500_000_000),
                                date_time=None,
                            ),
                            SimpleNamespace(
                                trade_id="t2",
                                quantity=8,
                                price=value(95),
                                date_time=None,
                            ),
                        ],
                    )
                ]
            )

    sandbox = Sandbox()
    client = make_client(sandbox)

    operations = client.get_operations(state="executed", figi="figi-sber")

    assert sandbox.kwargs["account_id"] == "sandbox-account"
    assert sandbox.kwargs["figi"] == "figi-sber"
    assert sandbox.kwargs["state"] == module.OperationState.OPERATION_STATE_EXECUTED
    assert len(operations) == 1
    assert operations[0].id == "op-1"
    assert operations[0].operation_type == "OPERATION_TYPE_SELL"
    assert operations[0].state == "OPERATION_STATE_EXECUTED"
    assert operations[0].quantity == 20
    assert operations[0].price == Decimal("95.5")
    assert operations[0].payment == Decimal("-19100")
    assert [trade.trade_id for trade in operations[0].trades] == ["t1", "t2"]
    assert operations[0].trades[1].price == Decimal("95")


def test_get_operations_rejects_unknown_state():
    client = make_client(object())

    with pytest.raises(ValueError, match="state"):
        client.get_operations(state="filled")


def test_get_orders_returns_resting_orders():
    class Sandbox:
        def get_sandbox_orders(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                orders=[
                    SimpleNamespace(
                        order_id="take-1",
                        execution_report_status=SimpleNamespace(
                            name="EXECUTION_REPORT_STATUS_NEW"
                        ),
                        lots_requested=2,
                        lots_executed=0,
                        initial_order_price=value(220),
                        executed_order_price=value(0),
                        figi="figi-sber",
                        instrument_uid="uid-sber",
                        direction=SimpleNamespace(name="ORDER_DIRECTION_SELL"),
                        order_type=SimpleNamespace(name="ORDER_TYPE_LIMIT"),
                        currency="rub",
                        order_date=None,
                    )
                ]
            )

    sandbox = Sandbox()
    client = make_client(sandbox)

    orders = client.get_orders()

    assert sandbox.kwargs == {"account_id": "sandbox-account"}
    assert orders[0].order_id == "take-1"
    assert orders[0].status == "EXECUTION_REPORT_STATUS_NEW"
    assert orders[0].direction == "ORDER_DIRECTION_SELL"
    assert orders[0].order_type == "ORDER_TYPE_LIMIT"
    assert orders[0].lots_requested == 2
    assert orders[0].initial_order_price == Decimal("220")

