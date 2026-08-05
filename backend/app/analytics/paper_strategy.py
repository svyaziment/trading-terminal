"""Paper strategy reader: reads the active paper-trading strategy from DB.

Single source of truth for the strategy under paper test.
Paper trading must read the active strategy from trading.strategies,
not from hardcoded trading_config.py.

Usage:
    from app.analytics.paper_strategy import get_active_paper_strategy

    strategy = get_active_paper_strategy(db)
    # strategy = {"id": 36, "name": "...", "config": {...normalized...}}
"""
from __future__ import annotations

import ast
import json
from typing import Any, Dict, Optional

from app.db.db_manager import DBManager
from app.analytics.pattern_registry import normalize_patterns


class PaperStrategyNotFoundError(Exception):
    """No strategy with in_paper_test=true AND locked=true found."""

    pass


class PaperStrategyAmbiguousError(Exception):
    """More than one strategy with in_paper_test=true AND locked=true found."""

    pass


def _parse_config(raw) -> Optional[Dict]:
    """Normalize JSONB config (DBManager may return dict or Python-repr str)."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw

    s = str(raw)

    try:
        return json.loads(s)
    except (ValueError, json.JSONDecodeError):
        pass

    try:
        return ast.literal_eval(s)
    except Exception:
        return None


def get_active_paper_strategy(db: DBManager) -> Dict[str, Any]:
    """Read the active paper-trading strategy from DB.

    Finds the strategy with in_paper_test=true AND locked=true.
    Validates that exactly one such strategy exists.
    Normalizes config via normalize_patterns (pattern_registry).

    Returns:
        {"id": int, "name": str, "config": dict}

    Raises:
        PaperStrategyNotFoundError: if no strategy found.
        PaperStrategyAmbiguousError: if more than one strategy found.
    """
    df = db.select(
        "SELECT id, name, config FROM trading.strategies "
        "WHERE in_paper_test=true AND locked=true ORDER BY id"
    ).to_dataframe()

    if df.empty:
        raise PaperStrategyNotFoundError(
            "No strategy with in_paper_test=true AND locked=true found"
        )

    if len(df) > 1:
        ids = df["id"].tolist()
        raise PaperStrategyAmbiguousError(
            f"Multiple strategies with in_paper_test=true AND locked=true: {ids}"
        )

    row = df.iloc[0]
    config = _parse_config(row["config"]) or {}
    normalized_config = normalize_patterns(config)

    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "config": normalized_config,
    }
