"""Pattern registry and config normalization for issue #12.

Единая точка правды по паттернам и их параметрам.

Задача:
- реестр паттернов со схемой параметров;
- GET /api/patterns;
- normalize_patterns() для старого формата patterns: list и нового patterns: dict;
- обратная совместимость: confirm_windows дублируется на верхний уровень config,
  потому что StrategyEvaluator пока читает confirm_windows сверху.

Issue #79: timeframe contract for SignalEngine AND-filters is stable here.
Full ten-pattern schemas are added in #80 using SIGNAL_PATTERN_TIMEFRAME_PARAM.
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
}

# Issue #79: stable SignalEngine filter id + timeframe contract for registry #80.
# These ten ids are AND-filters in StrategyEvaluator; they are not listed in
# PATTERN_REGISTRY until #80 adds full schemas. Do not treat rsi_oversold as
# MR_RSI_Reversal. Do not fold signal_4h_buy into this set (it stays a 4h
# trading.signals lookup).
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
    """Ensure SignalEngine patterns have a resolved ``timeframe`` param."""
    out = copy.deepcopy(params) if isinstance(params, dict) else {}
    if not is_signal_engine_pattern(pattern_id):
        return out
    out["timeframe"] = resolve_signal_timeframe(out.get("timeframe"))
    return out


def list_patterns() -> List[Dict[str, Any]]:
    """Вернуть весь реестр в формате, ожидаемом GET /api/patterns."""
    result: List[Dict[str, Any]] = []

    for pattern_id, record in PATTERN_REGISTRY.items():
        result.append(
            {
                "id": pattern_id,
                "label": record.get("label", pattern_id),
                "hint": record.get("hint", ""),
                "category": record.get("category", "other"),
                "params": copy.deepcopy(record.get("params", [])),
            }
        )

    return result


def get_pattern(pattern_id: str) -> Optional[Dict[str, Any]]:
    """Вернуть запись паттерна или None."""
    record = PATTERN_REGISTRY.get(pattern_id)
    if record is None:
        return None

    return {
        "id": pattern_id,
        "label": record.get("label", pattern_id),
        "hint": record.get("hint", ""),
        "category": record.get("category", "other"),
        "params": copy.deepcopy(record.get("params", [])),
    }


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
