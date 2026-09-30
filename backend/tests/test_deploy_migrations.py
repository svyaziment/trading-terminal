"""Issue #178 step 5: the deploy path of the schema migrations.

These are the checks that used to be impossible to run automatically - "does
``alembic upgrade head`` work on a clean database, inside the image, against the
database the backend actually uses?" - expressed as assertions on the artifacts
the deploy is made of:

* the alembic script directory has exactly ONE head and a connected chain, which
  is precisely what a fresh database needs to reach the kill-switch seed;
* the image carries ``alembic.ini`` and ``alembic/``;
* compose runs the migration as a one-shot job the API waits for, with the same
  environment block, and the real-money gate closed by default;
* ``get_app_database_url()`` resolves the password the deployment actually sets
  (``PSTGRS_PWD``), so alembic and the application cannot pick two databases.
"""

from pathlib import Path
from urllib.parse import unquote_plus

import pytest
import yaml

from app.core.config import get_app_database_url


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = BACKEND_DIR / "Dockerfile"

EXPECTED_HEAD = "20260928_001"


def script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config)


def compose_config():
    with open(COMPOSE_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# --- a clean database can reach head -----------------------------------------


def test_the_migration_chain_has_a_single_head():
    """Two heads mean `alembic upgrade head` fails on a clean database."""
    assert script_directory().get_heads() == [EXPECTED_HEAD]


def test_the_chain_is_connected_down_to_the_base():
    script = script_directory()
    revisions = list(script.walk_revisions())
    seen = {revision.revision for revision in revisions}

    assert revisions[-1].down_revision is None  # exactly one base
    for revision in revisions:
        if revision.down_revision is not None:
            assert revision.down_revision in seen, revision.revision
    # Every file in versions/ is part of the chain - no orphan migration.
    files = {
        path.stem
        for path in (BACKEND_DIR / "alembic" / "versions").glob("*.py")
    }
    assert len(seen) == len(files)


def test_the_kill_switch_migration_is_part_of_the_chain():
    script = script_directory()
    revision = script.get_revision(EXPECTED_HEAD)

    assert revision is not None
    assert revision.down_revision == "20260927_001"
    assert "kill switch" in (revision.doc or "").lower()


# --- the image carries the migrations ----------------------------------------


def test_the_image_copies_the_alembic_scripts():
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "COPY alembic.ini ./" in dockerfile
    assert "COPY alembic ./alembic" in dockerfile
    # The copies must precede the runtime command, or the job has nothing to run.
    assert dockerfile.index("COPY alembic ./alembic") < dockerfile.index("CMD [")


def test_alembic_ini_points_at_the_copied_script_location():
    ini = (BACKEND_DIR / "alembic.ini").read_text(encoding="utf-8")

    assert "script_location = alembic" in ini


# --- compose runs migrations as a deploy step --------------------------------


def test_compose_defines_a_one_shot_migration_job():
    services = compose_config()["services"]

    assert "migrate" in services
    migrate = services["migrate"]
    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert migrate["restart"] == "no"
    assert migrate["working_dir"] == "/app"


def test_the_backend_waits_for_a_successful_migration():
    services = compose_config()["services"]

    assert services["backend"]["depends_on"] == {
        "migrate": {"condition": "service_completed_successfully"}
    }


def test_migrate_and_backend_share_one_environment():
    """One DSN for the migration job and for the API - no silent split-brain."""
    services = compose_config()["services"]

    assert services["migrate"]["environment"] == services["backend"]["environment"]
    environment = services["backend"]["environment"]
    for key in (
        "PSTGRS_PWD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_DB",
    ):
        assert key in environment


def test_the_real_money_gate_is_closed_by_default():
    environment = compose_config()["services"]["backend"]["environment"]

    assert environment["ALLOW_REAL_TRADING"] == "${ALLOW_REAL_TRADING:-false}"
    assert environment["TINVEST_LIVE_TOKEN"] == "${TINVEST_LIVE_TOKEN:-}"
    assert environment["TINVEST_LIVE_ACC"] == "${TINVEST_LIVE_ACC:-}"
    # Issue #192: reusing one physical token for market data and the real contour
    # is opt-in and must stay closed unless .env opens it explicitly.
    assert environment["ALLOW_LIVE_TOKEN_REUSE"] == "${ALLOW_LIVE_TOKEN_REUSE:-false}"


def test_the_live_risk_and_alerting_overrides_reach_the_container():
    """#176/#177 documented env overrides; compose is what delivers them."""
    environment = compose_config()["services"]["backend"]["environment"]

    for key in (
        "MAX_DAILY_LOSS_PCT",
        "MAX_POSITION_SIZE",
        "MAX_OPEN_POSITIONS",
        "LIVE_EQUITY_SNAPSHOT",
        "LIVE_TELEGRAM_ALERTS",
        "LIVE_HEARTBEAT_INTERVAL_SECONDS",
        "LIVE_HEARTBEAT_STALE_SECONDS",
        "LIVE_ALERT_DEBOUNCE_SECONDS",
        "LIVE_SLIPPAGE_ALERT_BP",
        "LIVE_MAX_CONSECUTIVE_ERRORS",
        "LIVE_METRICS_FLUSH_SECONDS",
    ):
        assert key in environment, key


# --- alembic and the application resolve the same DSN -------------------------


@pytest.fixture(autouse=True)
def _clean_db_env(monkeypatch):
    for name in (
        "APP_DATABASE_URL",
        "POSTGRES_PASSWORD",
        "PSTGRS_PWD",
        "POSTGRES_USER",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_historical_password_variable_is_honoured(monkeypatch):
    """docker-compose passes PSTGRS_PWD; alembic used to ignore it (#178)."""
    monkeypatch.setenv("PSTGRS_PWD", "s3cret")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_USER", "terminal")
    monkeypatch.setenv("POSTGRES_DB", "trading")

    assert get_app_database_url() == "postgresql://terminal:s3cret@db:5432/trading"


def test_the_standard_password_variable_wins(monkeypatch):
    monkeypatch.setenv("PSTGRS_PWD", "historical")
    monkeypatch.setenv("POSTGRES_PASSWORD", "standard")

    url = get_app_database_url()

    assert "standard" in url
    assert "historical" not in url


def test_an_explicit_url_still_wins_over_everything(monkeypatch):
    monkeypatch.setenv("PSTGRS_PWD", "ignored")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://u:p@h:5432/d")

    assert get_app_database_url() == "postgresql://u:p@h:5432/d"


def test_a_password_with_special_characters_is_url_encoded(monkeypatch):
    """alembic escapes '%' itself; the URL must survive '@', '!' and spaces."""
    monkeypatch.setenv("PSTGRS_PWD", "p@ss w!rd")

    url = get_app_database_url()

    assert unquote_plus(url.split(":")[2].split("@")[0]) == "p@ss w!rd"
    assert url.startswith("postgresql://app:")


def test_the_defaults_stay_usable_for_a_local_compose_postgres():
    assert (
        get_app_database_url()
        == "postgresql://app:app@postgres:5432/trading_terminal"
    )
