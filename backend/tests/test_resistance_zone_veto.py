"""Issue #97: levels_reversal must not enter inside an active resistance zone.

ALRS paper_positions.id=711 (2026-08-20) reconstructed from Vulpec analysis:
fill 19.80 sat in impulse resistance 19.67 [19.40, 19.94] while
nearest_level_at(..., 'support') returned the older impulse support 19.61.
"""
from __future__ import annotations

import pandas as pd

from app.analytics.levels_engine import (
    nearest_level_at,
    overlapping_resistance_zone_at,
)
from app.analytics.strategies.context import MarketContext
from app.analytics.strategies.registry import get_registry, register_default_strategies
from app.analytics.strategy_engine import StrategyEvaluator


ENTRY_TS = pd.Timestamp("2026-08-20 11:50:24")
ACTIVE_4H = pd.Timestamp("2026-08-20 08:00:00")
ENTRY_PRICE = 19.80
STOP_PRICE = 19.61
TAKE_PRICE = 20.90
ATR_4H = 0.5714285714285714

SUPPORT_19_61 = {
    "available_from_ts": pd.Timestamp("2026-07-27 08:00:00"),
    "defined_ts": pd.Timestamp("2026-07-27 08:00:00"),
    "level_price": 19.61,
    "type": "support",
    "method": "impulse",
    "atr": 0.3164285714285714,
    "zone_lower": 19.451785714285712,
    "zone_upper": 19.768214285714286,
}
RESISTANCE_19_67 = {
    "available_from_ts": pd.Timestamp("2026-08-14 12:00:00"),
    "defined_ts": pd.Timestamp("2026-08-14 12:00:00"),
    "level_price": 19.67,
    "type": "resistance",
    "method": "impulse",
    "atr": 0.5407142857142855,
    "zone_lower": 19.399642857142858,
    "zone_upper": 19.940357142857145,
}
TAKE_20_90 = {
    "available_from_ts": pd.Timestamp("2026-07-08 16:00:00"),
    "defined_ts": pd.Timestamp("2026-07-06 08:00:00"),
    "level_price": 20.90,
    "type": "resistance",
    "method": "swing",
    "atr": 0.2857142857142855,
    "zone_lower": 20.757142857142856,
    "zone_upper": 21.04285714285714,
}

LOCKED_LIKE_CONFIG = {
    "patterns": ["levels_reversal", "signal_4h_buy"],
    "confirm_windows": [10],
    "commission_pct": 0.06,
    "slippage_pct": 0.0,
    "risk_reward": {"risk": 1.0, "reward": 2.0},
    "entry_window": (7, 19),
}


def _levels(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _entry_row(price: float = ENTRY_PRICE) -> pd.Series:
    return pd.Series(
        {
            "timestamp": ENTRY_TS,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
        }
    )


def _context(levels: pd.DataFrame) -> dict:
    confirm_ts = [pd.Timestamp("2026-08-20 11:40:00")]
    return {
        "levels": levels,
        "ts_4h": [ACTIVE_4H],
        "atr_by_ts": {ACTIVE_4H: ATR_4H},
        "buy_ts": [ACTIVE_4H],
        "confirm_series": [(confirm_ts, [19.85])],
    }


def _load(ev: StrategyEvaluator, ctx: dict) -> None:
    ev.load_context(
        ctx["levels"],
        ctx["ts_4h"],
        ctx["atr_by_ts"],
        ctx["buy_ts"],
        ctx["confirm_series"],
    )


def _plugin_entry(levels: pd.DataFrame, row: pd.Series):
    register_default_strategies()
    plugin = get_registry().get_plugin("levels_reversal", LOCKED_LIKE_CONFIG)
    ctx_data = _context(levels)
    market = MarketContext(
        timestamp=pd.Timestamp(row["timestamp"]),
        candles_1min=pd.DataFrame([row]),
        levels=levels,
        ts_4h=ctx_data["ts_4h"],
        atr_by_ts=ctx_data["atr_by_ts"],
        buy_ts=ctx_data["buy_ts"],
        confirm_series=ctx_data["confirm_series"],
    )
    plugin.load_market_context(market)
    return plugin.check_entry(market)


def test_alrs_711_geometry_matches_vulpec_reconstruction():
    """Support 19.61 is a valid impulse support; fill is outside its zone
    and only reaches via the 0.5×ATR extension. Resistance 19.67 owns the fill."""
    fill = ENTRY_PRICE
    zl, zu = SUPPORT_19_61["zone_lower"], SUPPORT_19_61["zone_upper"]
    just_above = zu + 0.5 * ATR_4H
    assert not (zl <= fill <= zu)
    assert zu < fill <= just_above
    assert abs(just_above - 20.05392857142857) < 1e-9

    rz_l, rz_u = RESISTANCE_19_67["zone_lower"], RESISTANCE_19_67["zone_upper"]
    assert rz_l <= fill <= rz_u
    dist_res = abs(fill - RESISTANCE_19_67["level_price"])
    dist_sup = abs(fill - SUPPORT_19_61["level_price"])
    assert dist_res < dist_sup
    assert abs(dist_res - 0.13) < 1e-9
    assert abs(dist_sup - 0.19) < 1e-9


def test_nearest_level_at_ignores_resistance_below_market():
    """Root cause: one-sided lookup never sees resistance 19.67 as the take."""
    levels = _levels(SUPPORT_19_61, RESISTANCE_19_67, TAKE_20_90)
    sup = nearest_level_at(levels, ACTIVE_4H, ENTRY_PRICE, "support")
    res = nearest_level_at(levels, ACTIVE_4H, ENTRY_PRICE, "resistance")
    assert sup is not None
    assert sup["level_price"] == STOP_PRICE
    assert res is not None
    assert res["level_price"] == TAKE_PRICE
    assert res["level_price"] != RESISTANCE_19_67["level_price"]


def test_overlapping_resistance_zone_detects_alrs_711():
    levels = _levels(SUPPORT_19_61, RESISTANCE_19_67, TAKE_20_90)
    hit = overlapping_resistance_zone_at(levels, ACTIVE_4H, ENTRY_PRICE)
    assert hit is not None
    assert hit["level_price"] == RESISTANCE_19_67["level_price"]
    assert hit["method"] == "impulse"

    assert overlapping_resistance_zone_at(levels, ACTIVE_4H, 19.30) is None
    assert overlapping_resistance_zone_at(
        _levels(SUPPORT_19_61, TAKE_20_90), ACTIVE_4H, ENTRY_PRICE
    ) is None


def test_check_entry_rejects_alrs_711_collision():
    ev = StrategyEvaluator(LOCKED_LIKE_CONFIG)
    _load(ev, _context(_levels(SUPPORT_19_61, RESISTANCE_19_67, TAKE_20_90)))
    assert ev.check_entry(_entry_row()) is None


def test_check_entry_allows_same_geometry_without_opposing_zone():
    ev = StrategyEvaluator(LOCKED_LIKE_CONFIG)
    _load(ev, _context(_levels(SUPPORT_19_61, TAKE_20_90)))
    decision = ev.check_entry(_entry_row())
    assert decision is not None
    assert decision["action"] == "enter"
    assert decision["entry_price"] == ENTRY_PRICE
    assert decision["stop"] == STOP_PRICE
    assert decision["take"] == TAKE_PRICE


def test_plugin_and_evaluator_match_on_alrs_711() -> None:
    """SOP: StrategyEvaluator change requires bit-for-bit plugin parity."""
    colliding = _levels(SUPPORT_19_61, RESISTANCE_19_67, TAKE_20_90)
    clean = _levels(SUPPORT_19_61, TAKE_20_90)
    row = _entry_row()

    ev_colliding = StrategyEvaluator(LOCKED_LIKE_CONFIG)
    _load(ev_colliding, _context(colliding))
    ev_clean = StrategyEvaluator(LOCKED_LIKE_CONFIG)
    _load(ev_clean, _context(clean))

    plugin_colliding = _plugin_entry(colliding, row)
    plugin_clean = _plugin_entry(clean, row)
    eval_colliding = ev_colliding.check_entry(row)
    eval_clean = ev_clean.check_entry(row)

    match_colliding = plugin_colliding is None and eval_colliding is None
    match_clean = (
        plugin_clean is not None
        and eval_clean is not None
        and plugin_clean.entry_price == eval_clean["entry_price"]
        and plugin_clean.stop == eval_clean["stop"]
        and plugin_clean.take == eval_clean["take"]
    )
    result = {
        "regression_match": match_colliding and match_clean,
        "colliding": {"evaluator": eval_colliding, "plugin": plugin_colliding},
        "clean": {
            "evaluator": eval_clean,
            "plugin": None
            if plugin_clean is None
            else {
                "entry_price": plugin_clean.entry_price,
                "stop": plugin_clean.stop,
                "take": plugin_clean.take,
            },
        },
    }
    assert result["regression_match"] is True
    assert eval_clean["stop"] == STOP_PRICE
    assert eval_clean["take"] == TAKE_PRICE
