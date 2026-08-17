from app.core.config_manager import load_settings


def test_load_settings_returns_settings_object() -> None:
    settings = load_settings()

    assert isinstance(settings.db.host, str)
    assert isinstance(settings.db.database, str)
    assert isinstance(settings.db.user, str)
    assert isinstance(settings.db.password, str)
    assert isinstance(settings.db.port, int)
    assert isinstance(settings.api.sandbox_token, str)
    assert isinstance(settings.api.sandbox_account_id, str)
    assert isinstance(settings.telegram.token, str)
    assert isinstance(settings.telegram.chat_id, str)


def test_terminal_and_risk_defaults_are_loaded() -> None:
    settings = load_settings()

    assert settings.terminal.theme in {"dark", "light"}
    assert isinstance(settings.terminal.refresh_rate_sec, int)
    assert isinstance(settings.risk.max_daily_loss_pct, float)
    assert isinstance(settings.risk.max_position_size, int)


def test_sandbox_token_uses_dedicated_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("TINVEST_TOKEN", "market-data-token")
    monkeypatch.setenv("TINVEST_SANDBOX", "sandbox-only-token")
    monkeypatch.setenv("TINVEST_ACC", "market-data-account")
    monkeypatch.setenv("TINVEST_SANDBOX_ACC", "sandbox-only-account")

    settings = load_settings()

    assert settings.api.token == "market-data-token"
    assert settings.api.sandbox_token == "sandbox-only-token"
    assert settings.api.account_id == "market-data-account"
    assert settings.api.sandbox_account_id == "sandbox-only-account"


def test_telegram_uses_tgm_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("TGM_TOKEN", "bot-token")
    monkeypatch.setenv("TGM_CHAT_ID", "-100123")
    monkeypatch.setenv("TGM_APP_ID", "12345")
    monkeypatch.setenv("TGM_APP_HASH", "app-hash")

    settings = load_settings()

    assert settings.telegram.token == "bot-token"
    assert settings.telegram.chat_id == "-100123"
    assert settings.telegram.app_id == "12345"
    assert settings.telegram.app_hash == "app-hash"
