"""
Configuration manager for Trading Terminal backend.

Priority:
1. Environment variables
2. YAML config file
3. Default values

Database password is loaded only from environment variables.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "settings.yaml"


class BaseConfig(BaseModel):
    model_config = {"extra": "ignore"}


class ApiConfig(BaseConfig):
    token: str = ""
    sandbox_token: str = ""
    account_id: str = ""
    sandbox_account_id: str = ""


class TerminalConfig(BaseConfig):
    theme: str = "dark"
    refresh_rate_sec: int = 5


class RiskConfig(BaseConfig):
    max_daily_loss_pct: float = 2.0
    max_position_size: int = 100000


class TelegramConfig(BaseConfig):
    token: str = ""
    chat_id: str = ""
    app_id: str = ""
    app_hash: str = ""


class DbConfig(BaseConfig):
    host: str = "localhost"
    database: str = "postgres"
    user: str = "postgres"
    password: str = ""
    port: int = 5432


class Settings(BaseConfig):
    api: ApiConfig = Field(default_factory=ApiConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    db: DbConfig = Field(default_factory=DbConfig)


def _load_yaml_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except UnicodeDecodeError:
        data = {}

    if not isinstance(data, dict):
        return {}

    return data


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default

    value = value.strip()
    if value == "":
        return default

    return value


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default

    try:
        return int(value.strip())
    except ValueError:
        return default


def load_settings() -> Settings:
    yaml_data = _load_yaml_config()

    api_yaml = yaml_data.get("api", {}) or {}
    terminal_yaml = yaml_data.get("terminal", {}) or {}
    risk_yaml = yaml_data.get("risk", {}) or {}
    telegram_yaml = yaml_data.get("telegram", {}) or {}
    db_yaml = yaml_data.get("db", {}) or {}

    api_config = ApiConfig(
        token=_env_str("TINVEST_TOKEN", str(api_yaml.get("token", ""))),
        sandbox_token=_env_str(
            "TINVEST_SANDBOX", str(api_yaml.get("sandbox_token", ""))
        ),
        account_id=_env_str("TINVEST_ACC", str(api_yaml.get("account_id", ""))),
        sandbox_account_id=_env_str(
            "TINVEST_SANDBOX_ACC",
            str(api_yaml.get("sandbox_account_id", "")),
        ),
    )

    terminal_config = TerminalConfig(**terminal_yaml)
    risk_config = RiskConfig(**risk_yaml)
    telegram_config = TelegramConfig(
        token=_env_str("TGM_TOKEN", str(telegram_yaml.get("token", ""))),
        chat_id=_env_str(
            "TGM_CHAT",
            _env_str("TGM_CHAT_ID", str(telegram_yaml.get("chat_id", ""))),
        ),
        app_id=_env_str("TGM_APP_ID", str(telegram_yaml.get("app_id", ""))),
        app_hash=_env_str("TGM_APP_HASH", str(telegram_yaml.get("app_hash", ""))),
    )

    db_password = _env_str("PSTGRS_PWD", "")
    if db_password == "":
        db_password = _env_str("POSTGRES_PASSWORD", "")

    db_config = DbConfig(
        host=_env_str("POSTGRES_HOST", str(db_yaml.get("host", "localhost"))),
        database=_env_str("POSTGRES_DB", str(db_yaml.get("database", "postgres"))),
        user=_env_str("POSTGRES_USER", str(db_yaml.get("user", "postgres"))),
        password=db_password,
        port=_env_int("POSTGRES_PORT", int(db_yaml.get("port", 5432))),
    )

    return Settings(
        api=api_config,
        terminal=terminal_config,
        risk=risk_config,
        telegram=telegram_config,
        db=db_config,
    )


def get_db_password() -> str:
    password = _env_str("PSTGRS_PWD", "")
    if password == "":
        password = _env_str("POSTGRES_PASSWORD", "")
    return password


def setup_logger(name: str = "TradingTerminal") -> logging.Logger:
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.INFO)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        try:
            log_dir = ROOT_DIR / "logs_internal"
            log_dir.mkdir(exist_ok=True)

            file_handler = logging.FileHandler(
                log_dir / "app.log",
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            pass

    return logger
