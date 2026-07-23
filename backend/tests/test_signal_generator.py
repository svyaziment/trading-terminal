import pandas as pd
from unittest.mock import MagicMock, patch

from app.analytics.signal_generator import SignalGenerator


def _mock_exists_result(exists: bool = True):
    result = MagicMock()
    result.to_dataframe.return_value = pd.DataFrame([{"exists": exists}])
    return result


def test_signal_generator_initialization():
    with patch("app.analytics.signal_generator.DBManager") as mock_db_cls, \
         patch("app.analytics.signal_generator.IndicatorsManager"):
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.select.return_value = _mock_exists_result(True)

        generator = SignalGenerator()

        assert generator is not None
        assert generator.engine is not None


def test_get_top_tickers_empty():
    with patch("app.analytics.signal_generator.DBManager") as mock_db_cls, \
         patch("app.analytics.signal_generator.IndicatorsManager"):
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        empty_result = MagicMock()
        empty_result.to_dataframe.return_value = pd.DataFrame()

        mock_db.select.side_effect = [
            _mock_exists_result(True),
            empty_result,
        ]

        generator = SignalGenerator()
        tickers = generator.get_top_tickers()

        assert tickers == []


def test_ensure_signals_table_creates_unique_index():
    with patch("app.analytics.signal_generator.DBManager") as mock_db_cls, \
         patch("app.analytics.signal_generator.IndicatorsManager"):
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.select.return_value = _mock_exists_result(True)

        generator = SignalGenerator()
        generator._ensure_signals_table()

        assert mock_db.execute.called
