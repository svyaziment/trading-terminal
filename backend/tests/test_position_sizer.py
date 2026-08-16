import pytest

from app.analytics import trading_config
from app.analytics.position_sizer import calculate_position_size


def test_risk_limit_and_whole_lot_rounding():
    result = calculate_position_size(
        capital_rub=100_000,
        stop_distance_pct=6,
        price=310,
        lot_size=10,
    )

    assert result == {
        "size_lots": 5,
        "size_rub": pytest.approx(100_000 / 6),
        "risk_rub": 1_000,
        "reason": "risk",
    }


def test_concentration_limit():
    result = calculate_position_size(
        capital_rub=100_000,
        stop_distance_pct=2,
        price=100,
        lot_size=10,
    )

    assert result == {
        "size_lots": 20,
        "size_rub": 20_000,
        "risk_rub": 1_000,
        "reason": "concentration",
    }


def test_minimum_one_lot_when_capital_can_cover_it():
    result = calculate_position_size(
        capital_rub=10_000,
        stop_distance_pct=10,
        price=900,
        lot_size=10,
    )

    assert result["size_lots"] == 1
    assert result["size_rub"] == 1_000
    assert result["reason"] == "min_lot"


@pytest.mark.parametrize("stop_distance_pct", [0, -1])
def test_invalid_stop_is_rejected(stop_distance_pct):
    result = calculate_position_size(
        capital_rub=100_000,
        stop_distance_pct=stop_distance_pct,
        price=100,
        lot_size=10,
    )

    assert result["size_lots"] == 0
    assert result["reason"] == "invalid_stop"


def test_insufficient_capital_is_rejected():
    result = calculate_position_size(
        capital_rub=999,
        stop_distance_pct=5,
        price=100,
        lot_size=10,
    )

    assert result["size_lots"] == 0
    assert result["reason"] == "insufficient_capital"


def test_defaults_are_read_from_trading_config(monkeypatch):
    monkeypatch.setitem(trading_config.POSITION_SIZING, "risk_per_trade_pct", 2.0)
    monkeypatch.setitem(trading_config.POSITION_SIZING, "max_position_pct", 30.0)

    result = calculate_position_size(
        capital_rub=100_000,
        stop_distance_pct=10,
        price=100,
        lot_size=10,
    )

    assert result["risk_rub"] == 2_000
    assert result["size_rub"] == 20_000
    assert result["reason"] == "risk"


def test_explicit_limits_override_trading_config():
    result = calculate_position_size(
        capital_rub=100_000,
        stop_distance_pct=10,
        price=100,
        lot_size=10,
        risk_per_trade_pct=3,
        max_position_pct=15,
    )

    assert result["risk_rub"] == 3_000
    assert result["size_rub"] == 15_000
    assert result["reason"] == "concentration"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", 0),
        ("lot_size", 0),
        ("lot_size", 1.5),
        ("risk_per_trade_pct", -1),
        ("max_position_pct", -1),
    ],
)
def test_invalid_numeric_inputs_fail_fast(field, value):
    kwargs = {
        "capital_rub": 100_000,
        "stop_distance_pct": 5,
        "price": 100,
        "lot_size": 10,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=field):
        calculate_position_size(**kwargs)
