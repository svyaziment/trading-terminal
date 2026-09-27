"""Tests of the real T-Bank contour client and the contour factory (#178).

The fakes mirror ``tests/test_tinkoff_sandbox.py``: no gRPC, no SDK calls - only
the request objects the SDK defines, so the assertions are about *which* service
and *which* arguments the client uses.
"""

from decimal import Decimal
from types import SimpleNamespace

import logging

import pytest

from app.analytics import trading_config
from app.analytics.trading_config import get_sandbox_trading_config
from app.broker import client_factory
from app.broker import tinkoff_live as module
from app.broker.tinkoff_live import (
    LiveAPIError,
    LiveConfigurationError,
    TinkoffLiveClient,
)
from app.broker.tinkoff_sandbox import (
    SandboxAPIError,
    SandboxConfigurationError,
    TinkoffSandboxClient,
)


def value(units=0, nano=0, currency="rub"):
    return SimpleNamespace(units=units, nano=nano, currency=currency)


def policy(**overrides):
    """A complete execution policy copy with test overrides applied."""
    base = {
        "enabled": True,
        "allow_real_trading": True,
        "initial_capital_rub": 50_000,
        "default_currency": "rub",
        "retry_attempts": 3,
        "retry_base_delay_seconds": 0.5,
        "discover_account_when_missing": True,
    }
    base.update(overrides)
    return base


class FakeClientContext:
    """Stand-in for ``Client(...)`` used as a context manager."""

    def __init__(self, **services):
        self.services = SimpleNamespace(**services)

    def __enter__(self):
        return self.services

    def __exit__(self, *_args):
        return False


def services_factory(captured=None, **services):
    """Build a client factory serving the given sub-services."""

    def factory(*args, **kwargs):
        if captured is not None:
            captured["args"] = args
            captured["kwargs"] = kwargs
        return FakeClientContext(**services)

    return factory


def make_client(**kwargs):
    """A live client that is allowed to exist, with injectable services."""
    kwargs.setdefault("token", "live-token")
    kwargs.setdefault("account_id", "real-account")
    kwargs.setdefault("allow_real_trading", True)
    kwargs.setdefault("client_factory", services_factory())
    return TinkoffLiveClient(**kwargs)


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


def api_settings(token="market-data-token", live_token="live-token",
                 live_account_id="real-account"):
    """``load_settings()`` stand-in exposing the three credential pairs."""
    return SimpleNamespace(
        api=SimpleNamespace(
            token=token,
            sandbox_token="sandbox-token",
            account_id="market-account",
            sandbox_account_id="sandbox-account",
            live_token=live_token,
            live_account_id=live_account_id,
        )
    )


# --- the allow_real_trading gate ---------------------------------------------


def test_client_refuses_to_exist_while_the_gate_is_closed(monkeypatch):
    monkeypatch.setattr(
        module, "get_sandbox_trading_config", lambda: policy(allow_real_trading=False)
    )

    with pytest.raises(LiveConfigurationError, match="allow_real_trading"):
        TinkoffLiveClient(
            token="live-token",
            account_id="real-account",
            client_factory=services_factory(),
        )


def test_gate_defaults_to_false_in_code(monkeypatch):
    monkeypatch.delenv("ALLOW_REAL_TRADING", raising=False)

    assert trading_config.SANDBOX_TRADING["allow_real_trading"] is False
    assert get_sandbox_trading_config()["allow_real_trading"] is False


def test_env_override_opens_the_gate(monkeypatch):
    monkeypatch.setenv("ALLOW_REAL_TRADING", "true")

    assert get_sandbox_trading_config()["allow_real_trading"] is True


@pytest.mark.parametrize("word", ["0", "false", "no", "off", "FALSE"])
def test_env_override_accepts_false_words(monkeypatch, word):
    monkeypatch.setenv("ALLOW_REAL_TRADING", word)

    assert get_sandbox_trading_config()["allow_real_trading"] is False


@pytest.mark.parametrize("word", ["1", "yes", "on", "TRUE"])
def test_env_override_accepts_truth_words(monkeypatch, word):
    monkeypatch.setenv("ALLOW_REAL_TRADING", word)

    assert get_sandbox_trading_config()["allow_real_trading"] is True


def test_env_override_rejects_an_ambiguous_value(monkeypatch):
    monkeypatch.setenv("ALLOW_REAL_TRADING", "ture")

    with pytest.raises(ValueError, match="ALLOW_REAL_TRADING"):
        get_sandbox_trading_config()


def test_live_errors_stay_catchable_by_the_sandbox_handlers():
    """The executor only knows the sandbox error types - the mirror must fit."""
    assert issubclass(LiveAPIError, SandboxAPIError)
    assert issubclass(LiveConfigurationError, SandboxConfigurationError)


def test_missing_live_token_fails_fast(monkeypatch):
    monkeypatch.setattr(
        module, "load_settings", lambda: api_settings(live_token="")
    )
    monkeypatch.setattr(module, "get_sandbox_trading_config", lambda: policy())

    with pytest.raises(LiveConfigurationError, match="TINVEST_LIVE_TOKEN"):
        TinkoffLiveClient(account_id="real-account", client_factory=services_factory())


def test_live_token_never_falls_back_to_another_contour(monkeypatch):
    """A real token comes from TINVEST_LIVE_TOKEN only (Epic #172 red line)."""
    monkeypatch.setattr(module, "load_settings", lambda: api_settings())
    monkeypatch.setattr(module, "get_sandbox_trading_config", lambda: policy())

    client = TinkoffLiveClient(client_factory=services_factory())

    assert client.live_token == "live-token"
    assert client.account_id == "real-account"


def test_reusing_the_market_data_token_is_refused(monkeypatch):
    monkeypatch.setattr(
        module,
        "load_settings",
        lambda: api_settings(token="same-token", live_token="same-token"),
    )
    monkeypatch.setattr(module, "get_sandbox_trading_config", lambda: policy())

    with pytest.raises(LiveConfigurationError, match="TINVEST_TOKEN"):
        TinkoffLiveClient(client_factory=services_factory())


def test_missing_account_without_discovery_fails_fast(monkeypatch):
    monkeypatch.setattr(
        module,
        "get_sandbox_trading_config",
        lambda: policy(discover_account_when_missing=False),
    )

    with pytest.raises(LiveConfigurationError, match="TINVEST_LIVE_ACC"):
        TinkoffLiveClient(
            token="live-token",
            account_id="",
            allow_real_trading=True,
            client_factory=services_factory(),
        )


def test_disabled_trading_policy_is_refused(monkeypatch):
    monkeypatch.setattr(
        module, "get_sandbox_trading_config", lambda: policy(enabled=False)
    )

    with pytest.raises(LiveConfigurationError, match="disabled"):
        TinkoffLiveClient(
            token="live-token",
            account_id="real-account",
            allow_real_trading=True,
            client_factory=services_factory(),
        )


# --- the mirrored contract ---------------------------------------------------


def test_execute_order_uses_the_orders_service_and_idempotence_id():
    class Orders:
        def post_order(self, **kwargs):
            self.kwargs = kwargs
            return order_response("broker-order-1")

    orders = Orders()
    captured = {}
    client = make_client(client_factory=services_factory(captured, orders=orders))

    result = client.execute_order(
        instrument_id="instrument-uid",
        quantity=2,
        direction="buy",
        order_type="limit",
        price="101.25",
        order_id="stable-request-id",
    )

    assert result.order_id == "broker-order-1"
    assert result.lots_executed == 2
    assert result.executed_order_price == Decimal("101.3")
    assert orders.kwargs["account_id"] == "real-account"
    assert orders.kwargs["instrument_id"] == "instrument-uid"
    assert orders.kwargs["price"].units == 101
    assert orders.kwargs["price"].nano == 250_000_000
    assert orders.kwargs["order_type"] == module.ORDER_TYPE_MAP["limit"]
    assert orders.kwargs["direction"] == module.ORDER_DIRECTION_MAP["buy"]
    # The real contour keys idempotency on `idempotence_id`, not `order_id`.
    assert orders.kwargs["idempotence_id"] == "stable-request-id"
    assert "order_id" not in orders.kwargs
    # ... and it talks to the production endpoint, never to the sandbox one.
    assert captured["kwargs"]["target"] == module.INVEST_GRPC_API
    assert captured["args"] == ("live-token",)


def test_market_order_rejects_a_price():
    client = make_client(client_factory=services_factory(orders=object()))

    with pytest.raises(ValueError, match="price must be omitted"):
        client.execute_order(
            instrument_id="uid", quantity=1, order_type="market", price="10"
        )


def test_check_balance_sums_only_the_requested_currency():
    class Operations:
        def get_positions(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                money=[value(50_000, 500_000_000, "rub"), value(10, 0, "usd")]
            )

    operations = Operations()
    client = make_client(client_factory=services_factory(operations=operations))

    assert client.check_balance() == Decimal("50000.5")
    assert client.check_balance("usd") == Decimal("10")
    assert operations.kwargs["account_id"] == "real-account"


def test_get_positions_reads_the_portfolio_and_skips_flat_lines():
    class Operations:
        def get_portfolio(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                positions=[
                    SimpleNamespace(
                        figi="FIGI1",
                        ticker="SBER",
                        instrument_uid="uid-1",
                        instrument_type="share",
                        quantity=value(10),
                        quantity_lots=value(1),
                        blocked_lots=value(0),
                        average_position_price=value(100),
                        current_price=value(105, 500_000_000),
                        expected_yield=value(5),
                    ),
                    SimpleNamespace(
                        figi="FIGI2",
                        ticker="GAZP",
                        instrument_uid="uid-2",
                        instrument_type="share",
                        quantity=value(0),
                        quantity_lots=value(0),
                        blocked_lots=value(0),
                        average_position_price=None,
                        current_price=None,
                        expected_yield=None,
                    ),
                ]
            )

    operations = Operations()
    client = make_client(client_factory=services_factory(operations=operations))

    positions = client.get_positions()

    assert [item.ticker for item in positions] == ["SBER"]
    assert positions[0].quantity == Decimal("10")
    assert positions[0].current_price == Decimal("105.5")
    assert operations.kwargs["account_id"] == "real-account"


def stop_item(stop_order_id="stop-1", uid="uid-1", ticker="SBER", figi="FIGI1"):
    return SimpleNamespace(
        stop_order_id=stop_order_id,
        status=SimpleNamespace(name="STOP_ORDER_STATUS_ACTIVE"),
        direction=SimpleNamespace(name="STOP_ORDER_DIRECTION_SELL"),
        order_type=SimpleNamespace(name="STOP_ORDER_TYPE_STOP_LOSS"),
        lots_requested=2,
        stop_price=value(99),
        price=value(98),
        ticker=ticker,
        figi=figi,
        instrument_uid=uid,
        currency="rub",
        exchange_order_id="",
        create_date=None,
        activation_date_time=None,
        expiration_time=None,
    )


def test_post_stop_order_builds_the_protective_request():
    class StopOrders:
        def post_stop_order(self, request):
            self.request = request
            return SimpleNamespace(stop_order_id="stop-1", order_request_id="req-1")

    stop_orders = StopOrders()
    client = make_client(client_factory=services_factory(stop_orders=stop_orders))

    result = client.post_stop_order(
        instrument_id="uid-1",
        quantity=3,
        stop_price="99.5",
        direction="sell",
        stop_order_type="stop_loss",
        price="99.0",
        order_id="protection-key",
    )

    payload = stop_orders.request
    assert result.stop_order_id == "stop-1"
    assert result.order_request_id == "req-1"
    assert payload.account_id == "real-account"
    assert payload.instrument_id == "uid-1"
    assert payload.quantity == 3
    assert payload.stop_price.units == 99
    assert payload.stop_price.nano == 500_000_000
    assert payload.price.units == 99
    assert payload.order_id == "protection-key"
    assert payload.direction == module.STOP_ORDER_DIRECTION_MAP["sell"]
    assert payload.stop_order_type == module.STOP_ORDER_TYPE_MAP["stop_loss"]
    assert (
        payload.expiration_type == module.STOP_ORDER_EXPIRATION_MAP["good_till_cancel"]
    )


def test_post_stop_order_validates_its_arguments():
    client = make_client(client_factory=services_factory(stop_orders=object()))

    with pytest.raises(ValueError, match="stop_price must be positive"):
        client.post_stop_order(instrument_id="uid", quantity=1, stop_price="0")
    with pytest.raises(ValueError, match="stop_order_type"):
        client.post_stop_order(
            instrument_id="uid", quantity=1, stop_price="1", stop_order_type="trail"
        )
    with pytest.raises(ValueError, match="expire_date is required"):
        client.post_stop_order(
            instrument_id="uid",
            quantity=1,
            stop_price="1",
            expiration_type="good_till_date",
        )


def test_get_stop_orders_filters_client_side_and_ignores_dates():
    class StopOrders:
        def get_stop_orders(self, request):
            self.request = request
            return SimpleNamespace(
                stop_orders=[
                    stop_item("stop-1", uid="uid-1"),
                    stop_item("stop-2", uid="uid-2", ticker="GAZP", figi="FIGI2"),
                ]
            )

    stop_orders = StopOrders()
    client = make_client(client_factory=services_factory(stop_orders=stop_orders))

    stops = client.get_stop_orders(
        status="active",
        instrument_id="uid-1",
        from_date="ignored",
        to_date="ignored",
    )

    assert [item.stop_order_id for item in stops] == ["stop-1"]
    assert stops[0].status == "STOP_ORDER_STATUS_ACTIVE"
    assert stops[0].stop_price == Decimal("99")
    assert stops[0].price == Decimal("98")
    # GetStopOrdersRequest carries account_id and status only - the date
    # arguments of the sandbox signature have no real counterpart.
    assert stop_orders.request.account_id == "real-account"
    assert stop_orders.request.status == module.STOP_ORDER_STATUS_MAP["active"]


def test_get_stop_orders_rejects_an_unknown_status():
    client = make_client(client_factory=services_factory(stop_orders=object()))

    with pytest.raises(ValueError, match="status must be one of"):
        client.get_stop_orders(status="whatever")


def test_cancel_stop_order_and_cancel_order_use_their_services():
    class StopOrders:
        def cancel_stop_order(self, request):
            self.request = request
            return SimpleNamespace(time="2026-09-28T10:00:00")

    class Orders:
        def cancel_order(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(time="2026-09-28T10:00:01")

    stop_orders = StopOrders()
    orders = Orders()
    client = make_client(
        client_factory=services_factory(stop_orders=stop_orders, orders=orders)
    )

    cancelled_stop = client.cancel_stop_order(" stop-9 ")
    cancelled_order = client.cancel_order(" order-9 ")

    assert cancelled_stop.stop_order_id == "stop-9"
    assert stop_orders.request.account_id == "real-account"
    assert stop_orders.request.stop_order_id == "stop-9"
    assert cancelled_order.order_id == "order-9"
    assert orders.kwargs == {"account_id": "real-account", "order_id": "order-9"}


def test_get_orders_parses_resting_orders():
    class Orders:
        def get_orders(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                orders=[
                    SimpleNamespace(
                        order_id="order-1",
                        execution_report_status=SimpleNamespace(
                            name="EXECUTION_REPORT_STATUS_NEW"
                        ),
                        direction=SimpleNamespace(name="ORDER_DIRECTION_SELL"),
                        order_type=SimpleNamespace(name="ORDER_TYPE_LIMIT"),
                        lots_requested=2,
                        lots_executed=0,
                        figi="FIGI1",
                        instrument_uid="uid-1",
                        currency="rub",
                        initial_order_price=value(110),
                        executed_order_price=None,
                        order_date=None,
                    )
                ]
            )

    orders = Orders()
    client = make_client(client_factory=services_factory(orders=orders))

    resting = client.get_orders()

    assert resting[0].order_id == "order-1"
    assert resting[0].status == "EXECUTION_REPORT_STATUS_NEW"
    assert resting[0].direction == "ORDER_DIRECTION_SELL"
    assert resting[0].lots_requested == 2
    assert resting[0].initial_order_price == Decimal("110")
    assert orders.kwargs == {"account_id": "real-account"}


def test_get_operations_parses_executed_fills():
    class Operations:
        def get_operations(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                operations=[
                    SimpleNamespace(
                        id="op-1",
                        state=SimpleNamespace(name="OPERATION_STATE_EXECUTED"),
                        operation_type=SimpleNamespace(name="OPERATION_TYPE_SELL"),
                        quantity=10,
                        quantity_rest=0,
                        price=value(105),
                        payment=value(1050),
                        currency="rub",
                        figi="FIGI1",
                        instrument_uid="uid-1",
                        instrument_type="share",
                        parent_operation_id="",
                        date=None,
                        trades=[
                            SimpleNamespace(
                                trade_id="trade-1",
                                quantity=10,
                                price=value(105),
                                date_time=None,
                            )
                        ],
                    )
                ]
            )

    operations = Operations()
    client = make_client(client_factory=services_factory(operations=operations))

    fills = client.get_operations(state="executed", figi="FIGI1")

    assert fills[0].id == "op-1"
    assert fills[0].state == "OPERATION_STATE_EXECUTED"
    assert fills[0].quantity == 10
    assert fills[0].price == Decimal("105")
    assert fills[0].trades[0].trade_id == "trade-1"
    assert operations.kwargs["state"] == module.OPERATION_STATE_MAP["executed"]
    assert operations.kwargs["figi"] == "FIGI1"
    assert operations.kwargs["account_id"] == "real-account"


def test_get_operations_rejects_an_unknown_state():
    client = make_client(client_factory=services_factory(operations=object()))

    with pytest.raises(ValueError, match="state must be one of"):
        client.get_operations(state="whatever")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"instrument_id": "", "quantity": 1}, "instrument_id"),
        ({"instrument_id": "uid", "quantity": 0}, "quantity"),
        ({"instrument_id": "uid", "quantity": True}, "quantity"),
        ({"instrument_id": "uid", "quantity": 1, "direction": "hold"}, "direction"),
        ({"instrument_id": "uid", "quantity": 1, "order_type": "stop"}, "order_type"),
        (
            {"instrument_id": "uid", "quantity": 1, "order_type": "limit"},
            "positive price is required",
        ),
        ({"instrument_id": "uid", "quantity": 1, "order_id": "  "}, "order_id"),
    ],
)
def test_execute_order_validates_its_arguments(kwargs, message):
    client = make_client(client_factory=services_factory(orders=object()))

    with pytest.raises(ValueError, match=message):
        client.execute_order(**kwargs)


def test_cancel_calls_reject_empty_ids():
    client = make_client(client_factory=services_factory())

    with pytest.raises(ValueError, match="order_id"):
        client.cancel_order("   ")
    with pytest.raises(ValueError, match="stop_order_id"):
        client.cancel_stop_order("")


# --- retries, discovery and secrecy ------------------------------------------


def test_transient_error_retries_with_the_same_idempotence_id(monkeypatch):
    class TransientError(Exception):
        code = SimpleNamespace(name="UNAVAILABLE")

    class Orders:
        def __init__(self):
            self.keys = []

        def post_order(self, **kwargs):
            self.keys.append(kwargs["idempotence_id"])
            if len(self.keys) == 1:
                raise TransientError()
            return order_response(kwargs["idempotence_id"])

    sleeps = []
    attempts = []
    orders = Orders()
    monkeypatch.setattr(module, "LIVE_SDK_RETRYABLE_ERRORS", (TransientError,))
    client = make_client(
        client_factory=services_factory(orders=orders),
        sleep_fn=sleeps.append,
        before_request=lambda: attempts.append("tick"),
    )

    result = client.execute_order(
        instrument_id="uid", quantity=1, order_id="idempotent-key"
    )

    assert result.order_id == "idempotent-key"
    assert orders.keys == ["idempotent-key", "idempotent-key"]
    assert sleeps == [0.5]
    assert attempts == ["tick", "tick"]


def test_non_retryable_error_becomes_a_live_api_error(monkeypatch):
    class Denied(Exception):
        code = SimpleNamespace(name="PERMISSION_DENIED")

    class Operations:
        def get_positions(self, **_kwargs):
            raise Denied()

    monkeypatch.setattr(module, "LIVE_SDK_RETRYABLE_ERRORS", (Denied,))
    client = make_client(client_factory=services_factory(operations=Operations()))

    # LiveAPIError must stay catchable by the executor's sandbox handler.
    with pytest.raises(SandboxAPIError, match="PERMISSION_DENIED"):
        client.check_balance()


def test_unexpected_exception_is_wrapped_not_leaked():
    class Operations:
        def get_portfolio(self, **_kwargs):
            raise RuntimeError("boom")

    client = make_client(client_factory=services_factory(operations=Operations()))

    with pytest.raises(LiveAPIError, match="get_positions"):
        client.get_positions()


def test_account_discovery_uses_the_users_service_and_is_cached():
    class Users:
        def __init__(self):
            self.calls = 0

        def get_accounts(self):
            self.calls += 1
            return SimpleNamespace(
                accounts=[
                    SimpleNamespace(
                        id="closed-account",
                        status=SimpleNamespace(name="ACCOUNT_STATUS_CLOSED"),
                    ),
                    SimpleNamespace(
                        id="discovered-real",
                        status=module.AccountStatus.ACCOUNT_STATUS_OPEN,
                    ),
                ]
            )

    class Operations:
        def __init__(self):
            self.accounts = []

        def get_positions(self, **kwargs):
            self.accounts.append(kwargs["account_id"])
            return SimpleNamespace(money=[value(1_000)])

    users = Users()
    operations = Operations()
    client = make_client(
        account_id="",
        client_factory=services_factory(users=users, operations=operations),
    )

    assert client.check_balance() == Decimal("1000")
    assert client.check_balance() == Decimal("1000")

    assert client.account_id == "discovered-real"
    assert users.calls == 1  # cached after the first discovery
    assert operations.accounts == ["discovered-real", "discovered-real"]


def test_rejected_account_falls_back_to_discovery(monkeypatch):
    class AccountNotFound(Exception):
        code = SimpleNamespace(name="NOT_FOUND")
        details = "50004"

    class Users:
        def get_accounts(self):
            return SimpleNamespace(
                accounts=[
                    SimpleNamespace(
                        id="open-real",
                        status=module.AccountStatus.ACCOUNT_STATUS_OPEN,
                    )
                ]
            )

    class Operations:
        def __init__(self):
            self.accounts = []

        def get_positions(self, **kwargs):
            self.accounts.append(kwargs["account_id"])
            if kwargs["account_id"] == "wrong-account":
                raise AccountNotFound()
            return SimpleNamespace(money=[value(2_000)])

    operations = Operations()
    monkeypatch.setattr(module, "LIVE_SDK_RETRYABLE_ERRORS", (AccountNotFound,))
    client = make_client(
        account_id="wrong-account",
        client_factory=services_factory(users=Users(), operations=operations),
    )

    assert client.check_balance() == Decimal("2000")
    assert client.account_id == "open-real"
    assert operations.accounts == ["wrong-account", "open-real"]


def test_token_is_never_logged(caplog):
    class Operations:
        def get_positions(self, **_kwargs):
            return SimpleNamespace(money=[value(1_000)])

    caplog.set_level(logging.INFO)
    client = make_client(client_factory=services_factory(operations=Operations()))

    client.check_balance()

    assert "live-token" not in caplog.text
    assert "real-account" not in caplog.text
    assert "***ount" in caplog.text  # the account id is published masked


# --- the contour factory -----------------------------------------------------


def test_factory_selects_the_sandbox_client_by_default(monkeypatch, caplog):
    monkeypatch.delenv("ALLOW_REAL_TRADING", raising=False)
    built = {}

    class SandboxStub:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr(client_factory, "TinkoffSandboxClient", SandboxStub)
    caplog.set_level(logging.INFO)

    client = client_factory.create_execution_client(before_request=lambda: None)

    assert isinstance(client, SandboxStub)
    assert callable(built["before_request"])
    assert client_factory.contour_name() == client_factory.SANDBOX_CONTOUR
    assert "Using TinkoffSandboxClient" in caplog.text


def test_factory_selects_the_live_client_when_the_gate_is_open(monkeypatch, caplog):
    monkeypatch.setenv("ALLOW_REAL_TRADING", "true")
    monkeypatch.setenv("TINVEST_LIVE_TOKEN", "live-token")
    monkeypatch.setenv("TINVEST_LIVE_ACC", "real-account")
    caplog.set_level(logging.INFO)

    client = client_factory.create_execution_client()

    assert isinstance(client, TinkoffLiveClient)
    assert client_factory.contour_name() == client_factory.REAL_CONTOUR
    assert client_factory.describe_execution_client(client) == "real"
    assert "Using TinkoffLiveClient" in caplog.text
    # The real contour is announced at WARNING, not buried in INFO.
    assert any(
        record.levelno == logging.WARNING and "TinkoffLiveClient" in record.getMessage()
        for record in caplog.records
    )


def test_factory_propagates_an_ambiguous_env_override(monkeypatch):
    monkeypatch.setenv("ALLOW_REAL_TRADING", "maybe")

    with pytest.raises(ValueError, match="ALLOW_REAL_TRADING"):
        client_factory.create_execution_client()


def test_describe_execution_client_labels_the_contours():
    class FakeBroker:
        pass

    assert client_factory.describe_execution_client(FakeBroker()) == "FakeBroker"
    assert client_factory.SANDBOX_CONTOUR == "sandbox"
    assert client_factory.REAL_CONTOUR == "real"
    assert TinkoffSandboxClient is not None


# --- the executor picks its contour through the factory ----------------------


def test_executor_builds_its_broker_through_the_factory(monkeypatch):
    from app.analytics import live_executor as executor_module

    stub = object()
    calls = {}

    def fake_factory(**kwargs):
        calls.update(kwargs)
        return stub

    monkeypatch.setattr(executor_module, "create_execution_client", fake_factory)

    executor = executor_module.LiveExecutor(db=object())

    assert executor.broker is stub
    assert callable(calls["before_request"])
    # An injected double is reported by its class name, never as "real".
    assert executor.broker_contour == "object"
    assert executor.contour_label == "песочница"
    assert executor.get_metrics()["broker_contour"] == "object"


def test_executor_reports_the_real_contour(monkeypatch):
    from app.analytics import live_executor as executor_module

    monkeypatch.setenv("ALLOW_REAL_TRADING", "true")
    monkeypatch.setenv("TINVEST_LIVE_TOKEN", "live-token")
    monkeypatch.setenv("TINVEST_LIVE_ACC", "real-account")

    executor = executor_module.LiveExecutor(
        db=object(),
        broker=TinkoffLiveClient(client_factory=services_factory()),
    )

    assert executor.broker_contour == "real"
    assert executor.contour_label == "реальный счёт"
    assert executor.get_metrics()["broker_contour"] == "real"
