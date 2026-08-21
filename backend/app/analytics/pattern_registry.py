"""Pattern registry and config normalization for issue #12.

Единая точка правды по паттернам и их параметрам.

Задача:
- реестр паттернов со схемой параметров;
- GET /api/patterns;
- normalize_patterns() для старого формата patterns: list и нового patterns: dict;
- обратная совместимость: confirm_windows дублируется на верхний уровень config,
  потому что StrategyEvaluator пока читает confirm_windows сверху.

Issue #79: timeframe contract for SignalEngine AND-filters is stable here.
Issue #80: full schemas for the ten SignalEngine ids use SIGNAL_PATTERN_TIMEFRAME_PARAM
plus 4h defaults from the current BasePattern implementations.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple


PATTERN_REGISTRY: Dict[str, Dict[str, Any]] = {
    "levels_reversal": {
        "label": "Levels Reversal",
        "hint": "цена в зоне уровня + подтверждение разворота",
        "category": "levels",
        "params": [
            {
                "key": "level_timeframe",
                "label": "Таймфрейм уровней",
                "type": "select",
                "options": ["1h", "4h", "1d", "1w", "1M"],
                "default": "4h",
            },
            {
                "key": "level_method",
                "label": "Метод определения уровня",
                "type": "multiselect",
                "options": ["swing", "impulse"],
                "default": ["swing", "impulse"],
            },
            {
                "key": "swing_window",
                "label": "Окно swing (баров)",
                "type": "number",
                "min": 2,
                "max": 50,
                "step": 1,
                "default": 10,
            },
            {
                "key": "impulse_body_ratio",
                "label": "Доля тела импульсной свечи",
                "type": "number",
                "min": 0.1,
                "max": 1.0,
                "step": 0.05,
                "default": 0.7,
            },
            {
                "key": "impulse_atr_mult",
                "label": "Размер импульса (×ATR)",
                "type": "number",
                "min": 0.5,
                "max": 5.0,
                "step": 0.1,
                "default": 1.5,
            },
            {
                "key": "zone_atr_mult",
                "label": "Ширина зоны (×ATR)",
                "type": "number",
                "min": 0.1,
                "max": 2.0,
                "step": 0.05,
                "default": 0.5,
            },
            {
                "key": "confirm_windows",
                "label": "Окна подтверждения (мин)",
                "type": "multiselect",
                "options": [1, 5, 10, 15, 20, 25, 30, 60, 90, 120],
                "default": [10],
            },
        ],
    },
    "signal_4h_buy": {
        "label": "4H Buy",
        "hint": "активный 4h BUY-сигнал",
        "category": "signal",
        "params": [],
    },
    "rsi_oversold": {
        "label": "RSI Oversold",
        "hint": "RSI ниже порога",
        "category": "mean_reversion",
        "params": [
            {
                "key": "threshold",
                "label": "Порог RSI",
                "type": "number",
                "min": 1,
                "max": 50,
                "step": 1,
                "default": 30,
            }
        ],
    },
    "macd_bullish": {
        "label": "MACD Bullish",
        "hint": "бычий MACD",
        "category": "trend",
        "params": [],
    },
    "bb_lower": {
        "label": "Bollinger Lower",
        "hint": "цена у нижней границы Bollinger",
        "category": "mean_reversion",
        "params": [],
    },
    # Issue #107: Lab AND-filter, not a SignalEngine inline-evaluate id.
    # Issue #109: label_en / hint_en / icon / param hints are optional API fields
    # for Strategy Lab; evaluator ignores them.
    "level_breakout_retest": {
        "label": "Пробой уровня с ретестом",
        "label_en": "Level Breakout Retest",
        "hint": "После подтверждённого пробоя сопротивления цена возвращается к уровню как к новой поддержке (смена роли); вход — по бычьему триггеру.",
        "hint_en": "After a confirmed resistance break, price retests the level as new support (role reversal); entry waits for a bullish trigger.",
        "icon": "breakout_up",
        "category": "breakout",
        "params": [
            {
                "key": "level_timeframe",
                "label": "Таймфрейм уровней",
                "label_en": "Level timeframe",
                "hint": "ТФ, на котором строятся уровни и считается окно ретеста.",
                "hint_en": "Timeframe used to build levels and count the retest window.",
                "type": "select",
                "options": ["1h", "4h", "1d"],
                "default": "4h",
            },
            {
                "key": "retest_window_bars",
                "label": "Окно ретеста (баров ТФ)",
                "label_en": "Retest window (TF bars)",
                "hint": "Максимум баров ТФ после подтверждённого пробоя, пока ретест ещё валиден.",
                "hint_en": "Max HTF bars after a confirmed break during which a retest is valid.",
                "type": "number",
                "min": 1,
                "max": 100,
                "step": 1,
                "default": 20,
            },
            {
                "key": "retest_zone_atr",
                "label": "Зона ретеста (×ATR)",
                "label_en": "Retest zone (×ATR)",
                "hint": "Ширина зоны вокруг пробитого уровня в долях ATR.",
                "hint_en": "Width of the retest band around the broken level, in ATR units.",
                "type": "number",
                "min": 0.1,
                "max": 2.0,
                "step": 0.05,
                "default": 0.5,
            },
            {
                "key": "entry_trigger_bullish",
                "label": "Триггер: бычья свеча / пробой high",
                "label_en": "Bullish trigger (body / break of high)",
                "hint": "Требовать бычье тело или close выше предыдущего high.",
                "hint_en": "Require a bullish body or a close above the previous high.",
                "type": "boolean",
                "default": True,
            },
            {
                "key": "stop_atr",
                "label": "Стоп (×ATR)",
                "label_en": "Stop (×ATR)",
                "hint": "Расстояние стопа ниже цены входа в долях ATR.",
                "hint_en": "Stop distance below entry, in ATR units.",
                "type": "number",
                "min": 0.5,
                "max": 3.0,
                "step": 0.1,
                "default": 1.0,
            },
            {
                "key": "risk_reward",
                "label": "Take / risk",
                "label_en": "Take / risk",
                "hint": "Отношение тейка к стопу; меньше 1 сделает take ближе стопа.",
                "hint_en": "Take distance as a multiple of stop; values below 1 put take closer than stop.",
                "type": "number",
                "min": 1.0,
                "max": 5.0,
                "step": 0.1,
                "default": 2.0,
            },
        ],
    },
}

# Issue #79/#80: SignalEngine AND-filter ids + timeframe contract.
# Do not treat rsi_oversold as MR_RSI_Reversal. Do not fold signal_4h_buy
# into this set (it stays a 4h trading.signals lookup).
SIGNAL_ENGINE_PATTERN_IDS: Tuple[str, ...] = (
    "Trend_SMA_Alignment",
    "PA_Engulfing",
    "PA_HangingMan",
    "PA_Hammer",
    "PA_ThreeBlackCrows",
    "PA_ThreeWhiteSoldiers",
    "VOL_Spike",
    "VOL_Low_Pullback",
    "MR_RSI_Reversal",
    "BO_BB_Squeeze",
)

SIGNAL_ENGINE_TIMEFRAMES: Tuple[str, ...] = (
    "30min",
    "1h",
    "2h",
    "4h",
    "1d",
    "1w",
)

DEFAULT_SIGNAL_TIMEFRAME = "4h"

SIGNAL_PATTERN_TIMEFRAME_PARAM: Dict[str, Any] = {
    "key": "timeframe",
    "label": "Таймфрейм",
    "type": "select",
    "options": list(SIGNAL_ENGINE_TIMEFRAMES),
    "default": DEFAULT_SIGNAL_TIMEFRAME,
}

_SMA_COLUMNS = ["sma_10", "sma_20", "sma_50", "sma_200"]


def _signal_params(*extra: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Timeframe first, then pattern-specific params. Copies so schemas stay isolated."""
    params = [copy.deepcopy(SIGNAL_PATTERN_TIMEFRAME_PARAM)]
    params.extend(copy.deepcopy(item) for item in extra)
    return params


# Schema defaults are the 4h SignalEngine thresholds (DEFAULT_SIGNAL_TIMEFRAME).
# PA ratios are the literals from evaluate(); other ids use get_thresholds("4h").
SIGNAL_ENGINE_PATTERN_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "Trend_SMA_Alignment": {
        "label": "SMA Alignment",
        "hint": "цена > быстрая SMA > медленная SMA (BUY)",
        "category": "trend",
        "params": _signal_params(
            {
                "key": "fast_sma",
                "label": "Быстрая SMA",
                "type": "select",
                "options": list(_SMA_COLUMNS),
                "default": "sma_20",
            },
            {
                "key": "slow_sma",
                "label": "Медленная SMA",
                "type": "select",
                "options": list(_SMA_COLUMNS),
                "default": "sma_50",
            },
        ),
    },
    "PA_Engulfing": {
        "label": "Engulfing",
        "hint": "бычье/медвежье поглощение предыдущей свечи",
        "category": "price_action",
        "params": _signal_params(),
    },
    "PA_HangingMan": {
        "label": "Hanging Man",
        "hint": "медвежий молот: длинная нижняя тень, закрытие внизу",
        "category": "price_action",
        "params": _signal_params(
            {
                "key": "lower_shadow_mult",
                "label": "Мин. нижняя тень (×тело)",
                "type": "number",
                "min": 1.0,
                "max": 5.0,
                "step": 0.1,
                "default": 2.0,
            },
            {
                "key": "upper_shadow_mult",
                "label": "Макс. верхняя тень (×тело)",
                "type": "number",
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "default": 0.5,
            },
        ),
    },
    "PA_Hammer": {
        "label": "Hammer",
        "hint": "бычий молот: длинная нижняя тень, закрытие вверху",
        "category": "price_action",
        "params": _signal_params(
            {
                "key": "lower_shadow_mult",
                "label": "Мин. нижняя тень (×тело)",
                "type": "number",
                "min": 1.0,
                "max": 5.0,
                "step": 0.1,
                "default": 2.0,
            },
            {
                "key": "upper_shadow_mult",
                "label": "Макс. верхняя тень (×тело)",
                "type": "number",
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "default": 0.5,
            },
        ),
    },
    "PA_ThreeBlackCrows": {
        "label": "Three Black Crows",
        "hint": "три последовательные медвежьи свечи с падающим закрытием",
        "category": "price_action",
        "params": _signal_params(
            {
                "key": "min_body_range_ratio",
                "label": "Мин. тело / диапазон",
                "type": "number",
                "min": 0.3,
                "max": 1.0,
                "step": 0.05,
                "default": 0.7,
            },
        ),
    },
    "PA_ThreeWhiteSoldiers": {
        "label": "Three White Soldiers",
        "hint": "три последовательные бычьи свечи с растущим закрытием",
        "category": "price_action",
        "params": _signal_params(
            {
                "key": "min_body_range_ratio",
                "label": "Мин. тело / диапазон",
                "type": "number",
                "min": 0.3,
                "max": 1.0,
                "step": 0.05,
                "default": 0.7,
            },
        ),
    },
    "VOL_Spike": {
        "label": "Volume Spike",
        "hint": "аномальный объём и закрытие у края диапазона",
        "category": "volume",
        "params": _signal_params(
            {
                "key": "min_volume_ratio",
                "label": "Мин. volume_ratio",
                "type": "number",
                "min": 0.5,
                "max": 10.0,
                "step": 0.1,
                "default": 1.8,
            },
            {
                "key": "spike_ratio",
                "label": "Spike volume_ratio",
                "type": "number",
                "min": 1.0,
                "max": 10.0,
                "step": 0.1,
                "default": 2.0,
            },
        ),
    },
    "VOL_Low_Pullback": {
        "label": "Low Volume Pullback",
        "hint": "откат против тренда на низком объёме",
        "category": "volume",
        "params": _signal_params(
            {
                "key": "low_volume_ratio",
                "label": "Порог низкого объёма",
                "type": "number",
                "min": 0.1,
                "max": 1.0,
                "step": 0.05,
                "default": 0.7,
            },
            {
                "key": "min_trend_strength",
                "label": "Мин. сила тренда",
                "type": "number",
                "min": 0.001,
                "max": 0.2,
                "step": 0.001,
                "default": 0.03,
            },
        ),
    },
    "MR_RSI_Reversal": {
        "label": "RSI Reversal",
        "hint": "пересечение RSI порога перепроданности/перекупленности",
        "category": "mean_reversion",
        "params": _signal_params(
            {
                "key": "oversold",
                "label": "RSI перепроданность",
                "type": "number",
                "min": 1,
                "max": 50,
                "step": 1,
                "default": 30,
            },
            {
                "key": "overbought",
                "label": "RSI перекупленность",
                "type": "number",
                "min": 50,
                "max": 99,
                "step": 1,
                "default": 70,
            },
        ),
    },
    "BO_BB_Squeeze": {
        "label": "BB Squeeze",
        "hint": "сжатие bb_width и пробой полосы Боллинджера",
        "category": "breakout",
        "params": _signal_params(
            {
                "key": "squeeze_percentile",
                "label": "Перцентиль сжатия",
                "type": "number",
                "min": 1,
                "max": 50,
                "step": 1,
                "default": 20,
            },
            {
                "key": "lookback",
                "label": "Окно lookback (баров)",
                "type": "number",
                "min": 20,
                "max": 200,
                "step": 1,
                "default": 50,
            },
        ),
    },
}

PATTERN_REGISTRY.update(SIGNAL_ENGINE_PATTERN_SCHEMAS)

# Issue #107 AC: expose the Lab schema on SIGNAL_ENGINE_PATTERN_SCHEMAS so
# GET /api/patterns merge path is covered. Do NOT add the id to
# SIGNAL_ENGINE_PATTERN_IDS — there is no BasePattern.evaluate on indicators.
SIGNAL_ENGINE_PATTERN_SCHEMAS["level_breakout_retest"] = PATTERN_REGISTRY[
    "level_breakout_retest"
]


def is_signal_engine_pattern(pattern_id: str) -> bool:
    return pattern_id in SIGNAL_ENGINE_PATTERN_IDS


def resolve_signal_timeframe(value: Any) -> str:
    """Return a supported SignalEngine timeframe, else the default (4h)."""
    if isinstance(value, str) and value in SIGNAL_ENGINE_TIMEFRAMES:
        return value
    return DEFAULT_SIGNAL_TIMEFRAME


def apply_signal_pattern_defaults(
    pattern_id: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Fill SignalEngine registry defaults and resolve ``timeframe``."""
    provided = copy.deepcopy(params) if isinstance(params, dict) else {}
    if not is_signal_engine_pattern(pattern_id):
        return provided
    out = get_pattern_defaults(pattern_id)
    out.update(provided)
    out["timeframe"] = resolve_signal_timeframe(out.get("timeframe"))
    return out


_OPTIONAL_PATTERN_FIELDS = ("label_en", "hint_en", "icon")


def _pattern_api_record(pattern_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Public GET /api/patterns shape. Optional i18n/icon keys are omitted when empty."""
    out: Dict[str, Any] = {
        "id": pattern_id,
        "label": record.get("label", pattern_id),
        "hint": record.get("hint", ""),
        "category": record.get("category", "other"),
        "params": copy.deepcopy(record.get("params", [])),
    }
    for key in _OPTIONAL_PATTERN_FIELDS:
        value = record.get(key)
        if value:
            out[key] = value
    return out


def list_patterns() -> List[Dict[str, Any]]:
    """Вернуть весь реестр в формате, ожидаемом GET /api/patterns."""
    return [
        _pattern_api_record(pattern_id, record)
        for pattern_id, record in PATTERN_REGISTRY.items()
    ]


def get_pattern(pattern_id: str) -> Optional[Dict[str, Any]]:
    """Вернуть запись паттерна или None."""
    record = PATTERN_REGISTRY.get(pattern_id)
    if record is None:
        return None
    return _pattern_api_record(pattern_id, record)


def get_pattern_defaults(pattern_id: str) -> Dict[str, Any]:
    """Вернуть defaults паттерна в виде {param_key: default_value}."""
    record = PATTERN_REGISTRY.get(pattern_id)
    if not record:
        return {}

    defaults: Dict[str, Any] = {}
    for param in record.get("params", []):
        if "key" not in param:
            continue
        if "default" in param:
            defaults[param["key"]] = copy.deepcopy(param["default"])

    return defaults


def normalize_patterns(config: Dict[str, Any]) -> Dict[str, Any]:
    """Нормализовать config.patterns к новому формату.

    Старый формат:
        {"patterns": ["levels_reversal", "signal_4h_buy"], "confirm_windows": [10]}

    Новый формат:
        {
          "patterns": {
            "levels_reversal": {"level_timeframe": "4h", ..., "confirm_windows": [10]},
            "signal_4h_buy": {}
          }
        }

    Важно:
    - входной config не мутируется;
    - неизвестные паттерны сохраняются как есть;
    - для levels_reversal confirm_windows синхронизируется с верхним уровнем
      config["confirm_windows"], чтобы текущий StrategyEvaluator не сломался.
    """
    if not isinstance(config, dict):
        return {}

    cfg = copy.deepcopy(config)
    raw_patterns = cfg.get("patterns", [])
    top_confirm = cfg.get("confirm_windows", None)

    normalized: Dict[str, Dict[str, Any]] = {}

    if isinstance(raw_patterns, list):
        for pattern_id in raw_patterns:
            if not isinstance(pattern_id, str):
                continue

            defaults = get_pattern_defaults(pattern_id)

            if pattern_id == "levels_reversal" and top_confirm is not None:
                defaults["confirm_windows"] = copy.deepcopy(top_confirm)

            normalized[pattern_id] = apply_signal_pattern_defaults(
                pattern_id, defaults
            )

    elif isinstance(raw_patterns, dict):
        for pattern_id, params in raw_patterns.items():
            if not isinstance(pattern_id, str):
                continue

            defaults = get_pattern_defaults(pattern_id)
            provided = params if isinstance(params, dict) else {}
            defaults.update(copy.deepcopy(provided))

            if (
                pattern_id == "levels_reversal"
                and top_confirm is not None
                and "confirm_windows" not in provided
            ):
                defaults["confirm_windows"] = copy.deepcopy(top_confirm)

            normalized[pattern_id] = apply_signal_pattern_defaults(
                pattern_id, defaults
            )
    else:
        normalized = {}

    cfg["patterns"] = normalized

    if "levels_reversal" in normalized:
        levels_params = normalized["levels_reversal"]
        confirm_windows = levels_params.get("confirm_windows")

        if not isinstance(confirm_windows, list):
            confirm_windows = get_pattern_defaults("levels_reversal").get(
                "confirm_windows", [10]
            )
            levels_params["confirm_windows"] = copy.deepcopy(confirm_windows)

        cfg["confirm_windows"] = copy.deepcopy(confirm_windows)
    elif top_confirm is not None:
        cfg["confirm_windows"] = copy.deepcopy(top_confirm)

    return cfg
