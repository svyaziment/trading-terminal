from app.core.config_manager import load_settings


def test_load_settings_returns_settings_object() -> None:
    settings = load_settings()

    assert isinstance(settings.db.host, str)
    assert isinstance(settings.db.database, str)
    assert isinstance(settings.db.user, str)
    assert isinstance(settings.db.password, str)
    assert isinstance(settings.db.port, int)


def test_terminal_and_risk_defaults_are_loaded() -> None:
    settings = load_settings()

    assert settings.terminal.theme in {"dark", "light"}
    assert isinstance(settings.terminal.refresh_rate_sec, int)
    assert isinstance(settings.risk.max_daily_loss_pct, float)
    assert isinstance(settings.risk.max_position_size, int)
