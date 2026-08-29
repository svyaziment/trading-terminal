"""Composite S/R entry engine for Strategy Lab (Issue #117 / Epic #115).

Path A — support: same geometry as ``levels_reversal`` plus the Issue #97 veto
on *active* resistance.
Path B — resistance: confirmed break + retest via ``check_breakout_retest``
(no native support zone required).

This is an isolated entry engine (OR of two paths), not an AND-filter and not
a SignalEngine ``BasePattern``. Do not put the file under ``patterns/breakout/``
— that path would shadow ``breakout.py``.

When both ``levels_reversal`` and ``levels_sr_breakout`` are in ``config.patterns``,
the composite wins (one support path, no doubling). Do not AND this id with
``level_breakout_retest`` as a substitute for the composite.
"""
from __future__ import annotations

from typing import Any, Iterable

PATTERN_ID = "levels_sr_breakout"
SOURCE_SUPPORT = "levels_sr_breakout_support"
SOURCE_RESISTANCE = "levels_sr_breakout_resistance"

_ENTRY_ENGINE_IDS = (PATTERN_ID, "levels_reversal")


def _pattern_ids(patterns: Any) -> Iterable[str]:
    if isinstance(patterns, dict):
        return (pid for pid in patterns if isinstance(pid, str))
    if isinstance(patterns, list):
        return (pid for pid in patterns if isinstance(pid, str))
    return ()


def has_sr_breakout(patterns: Any) -> bool:
    return PATTERN_ID in set(_pattern_ids(patterns))


def has_levels_entry_engine(patterns: Any) -> bool:
    """True if config has a levels entry engine that defines stop/take."""
    enabled = set(_pattern_ids(patterns))
    return any(pid in enabled for pid in _ENTRY_ENGINE_IDS)
