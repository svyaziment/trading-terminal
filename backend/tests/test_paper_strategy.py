"""Unit tests for paper_strategy.py (issue #25).

Run inside backend container:
    python tests/test_paper_strategy.py
"""
import sys
import unittest

sys.path.insert(0, "/app")

import pandas as pd

from app.analytics.paper_strategy import (
    PaperStrategyAmbiguousError,
    PaperStrategyNotFoundError,
    _parse_config,
    get_active_paper_strategy,
)
from app.analytics.pattern_registry import normalize_patterns


class FakeSelectResult:
    def __init__(self, rows):
        self._rows = rows

    def to_dataframe(self):
        return pd.DataFrame(self._rows)


class FakeDBManager:
    """Minimal DBManager mock: returns a fixed DataFrame for select()."""

    def __init__(self, rows):
        self.rows = rows

    def select(self, query, params=None):
        return FakeSelectResult(self.rows)


class TestParseConfig(unittest.TestCase):
    def test_parse_config_dict(self):
        config = {"patterns": ["levels_reversal"], "confirm_windows": [10]}
        self.assertEqual(_parse_config(config), config)

    def test_parse_config_json_string(self):
        raw = '{"patterns": ["levels_reversal"], "confirm_windows": [10]}'
        self.assertEqual(
            _parse_config(raw),
            {"patterns": ["levels_reversal"], "confirm_windows": [10]},
        )

    def test_parse_config_python_repr_string(self):
        raw = "{'patterns': ['levels_reversal'], 'confirm_windows': [10]}"
        self.assertEqual(
            _parse_config(raw),
            {"patterns": ["levels_reversal"], "confirm_windows": [10]},
        )

    def test_parse_config_none(self):
        self.assertIsNone(_parse_config(None))


class TestNormalizePatterns(unittest.TestCase):
    def test_old_list_format_converts_to_dict(self):
        config = {
            "patterns": ["levels_reversal", "signal_4h_buy"],
            "confirm_windows": [10],
            "commission_pct": 0.06,
        }

        normalized = normalize_patterns(config)

        self.assertIsInstance(normalized["patterns"], dict)
        self.assertIn("levels_reversal", normalized["patterns"])
        self.assertIn("signal_4h_buy", normalized["patterns"])
        self.assertEqual(normalized["confirm_windows"], [10])
        self.assertEqual(normalized["commission_pct"], 0.06)

    def test_old_format_does_not_mutate_input(self):
        config = {
            "patterns": ["levels_reversal", "signal_4h_buy"],
            "confirm_windows": [10],
        }
        original = dict(config)
        original_patterns = list(config["patterns"])

        normalize_patterns(config)

        self.assertEqual(config["patterns"], original_patterns)
        self.assertEqual(config, original)


class TestGetActivePaperStrategy(unittest.TestCase):
    def test_zero_strategies_raises_not_found(self):
        db = FakeDBManager([])

        with self.assertRaises(PaperStrategyNotFoundError):
            get_active_paper_strategy(db)

    def test_multiple_strategies_raises_ambiguous(self):
        rows = [
            {"id": 1, "name": "s1", "config": {}},
            {"id": 2, "name": "s2", "config": {}},
        ]
        db = FakeDBManager(rows)

        with self.assertRaises(PaperStrategyAmbiguousError):
            get_active_paper_strategy(db)

    def test_single_strategy_with_old_format_config(self):
        config = {
            "patterns": ["levels_reversal", "signal_4h_buy"],
            "confirm_windows": [10],
            "commission_pct": 0.06,
        }
        rows = [
            {"id": 36, "name": "test_20260731", "config": config},
        ]
        db = FakeDBManager(rows)

        result = get_active_paper_strategy(db)

        self.assertEqual(result["id"], 36)
        self.assertEqual(result["name"], "test_20260731")
        self.assertIsInstance(result["config"]["patterns"], dict)
        self.assertIn("levels_reversal", result["config"]["patterns"])
        self.assertIn("signal_4h_buy", result["config"]["patterns"])
        self.assertEqual(result["config"]["confirm_windows"], [10])

    def test_single_strategy_with_python_repr_config(self):
        raw_config = "{'patterns': ['levels_reversal'], 'confirm_windows': [10]}"
        rows = [
            {"id": 36, "name": "test_20260731", "config": raw_config},
        ]
        db = FakeDBManager(rows)

        result = get_active_paper_strategy(db)

        self.assertEqual(result["id"], 36)
        self.assertIsInstance(result["config"]["patterns"], dict)
        self.assertIn("levels_reversal", result["config"]["patterns"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
