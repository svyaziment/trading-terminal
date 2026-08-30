"""Support-only entry engine with tracker-aware veto (Issue #127 / Epic #126).

Same support geometry as ``levels_reversal`` (zone, confirm, levels stop/take,
top-level RR) plus the Issue #97 veto of *active* resistance with
``LevelsTracker`` (a broken resistance does not cut a valid support entry).

This is an isolated single-path entry engine, not an AND-filter and not a
SignalEngine ``BasePattern``. It does **not** call ``check_breakout_retest``.
Do not put the file under ``patterns/breakout/``.

When both ``levels_sr_support`` and ``levels_sr_breakout`` are in
``config.patterns``, the composite wins. When both ``levels_sr_support`` and
``levels_reversal`` are present, this id wins (one support path, no doubling).
Do not silently enable the tracker on locked ``test_20260731``.
"""
from __future__ import annotations

from typing import Any, Iterable

PATTERN_ID = "levels_sr_support"
SOURCE = "levels_sr_support"


def _pattern_ids(patterns: Any) -> Iterable[str]:
    if isinstance(patterns, dict):
        return (pid for pid in patterns if isinstance(pid, str))
    if isinstance(patterns, list):
        return (pid for pid in patterns if isinstance(pid, str))
    return ()


def has_sr_support(patterns: Any) -> bool:
    return PATTERN_ID in set(_pattern_ids(patterns))
