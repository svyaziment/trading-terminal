import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from app.analytics.signal_generator import SignalGenerator

@pytest.fixture
def mock_db():
    return MagicMock()

def test_signal_generator_initialization(mock_db):
    with patch('app.analytics.signal_generator.DBManager') as mock_db_cls:
        mock_db_cls.return_value = mock_db
        gen = SignalGenerator()
        assert gen is not None

def test_get_top_tickers_empty():
    gen = SignalGenerator()
    with patch.object(gen.db, 'select') as mock_select:
        mock_result = MagicMock()
        mock_result.to_dataframe.return_value = pd.DataFrame()
        mock_select.return_value = mock_result
        tickers = gen.get_top_tickers()
        assert tickers == []

# TODO: добавить больше тестов после реализации реальных паттернов
