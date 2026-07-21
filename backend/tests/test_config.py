from app.core.config import get_settings


def test_settings_defaults() -> None:
    settings = get_settings()

    assert settings["environment"] == "dev"
    assert settings["market_data_schema"] == "trading"
    assert settings["app_schema"] == "terminal"
    assert isinstance(settings["app_database_url"], str)
    assert isinstance(settings["market_database_url"], str)
