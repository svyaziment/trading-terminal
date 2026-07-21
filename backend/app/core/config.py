import os


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if value == "":
        return default
    return value


def get_app_database_url() -> str:
    url = _env("APP_DATABASE_URL")
    if url:
        return url

    user = _env("POSTGRES_USER", "app")
    password = _env("POSTGRES_PASSWORD", "app")
    host = _env("POSTGRES_HOST", "postgres")
    port = _env("POSTGRES_PORT", "5432")
    db = _env("POSTGRES_DB", "trading_terminal")

    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def get_market_database_url() -> str:
    url = _env("MARKET_DATA_DATABASE_URL")
    if url:
        return url

    return get_app_database_url()


def get_settings() -> dict:
    return {
        "environment": _env("ENVIRONMENT", "dev"),
        "app_database_url": get_app_database_url(),
        "market_database_url": get_market_database_url(),
        "market_data_schema": _env("MARKET_DATA_SCHEMA", "trading"),
        "app_schema": _env("APP_SCHEMA", "terminal"),
        "postgres_host": _env("POSTGRES_HOST", "postgres"),
        "postgres_port": _env("POSTGRES_PORT", "5432"),
        "postgres_db": _env("POSTGRES_DB", "trading_terminal"),
    }
