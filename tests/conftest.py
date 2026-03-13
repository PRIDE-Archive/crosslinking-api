"""
Test configuration and fixtures.

IMPORTANT: DB_CONFIG env var must be set before any app import because
app/config/database.py and app/routes/parse.py both call create_engine(get_conn_str())
at module import time.
"""
import configparser
import os
import subprocess
import tempfile
import time

import pytest
from sqlalchemy import create_engine

# ---- Write test ini BEFORE any app import ----
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "crosslinking_api_tests")
TEST_DB_USER = os.environ.get("TEST_DB_USER", "ximzid_unittests")
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "ximzid_unittests")
TEST_DB_HOST = os.environ.get("TEST_DB_HOST", "localhost")
TEST_DB_PORT = os.environ.get("TEST_DB_PORT", "5432")
TEST_API_KEY = "test-api-key"
TEST_API_VERSION = "v2"


def _write_test_ini() -> str:
    cfg = configparser.ConfigParser()
    cfg["postgresql"] = {
        "host": TEST_DB_HOST,
        "database": TEST_DB_NAME,
        "user": TEST_DB_USER,
        "password": TEST_DB_PASSWORD,
        "port": TEST_DB_PORT,
    }
    cfg["security"] = {
        "apikey": TEST_API_KEY,
        "apiversion": TEST_API_VERSION,
        "apiport": "8080",
        "xiviewbaseurl": "https://test.example/",
    }
    cfg["redis"] = {
        "host": "localhost",
        "port": "6379",
        "password": "",
        "peptide_per_protein": "test_ppp",
    }
    path = os.path.join(tempfile.gettempdir(), "crosslinking_test.ini")
    with open(path, "w") as f:
        cfg.write(f)
    return path


os.environ["DB_CONFIG"] = _write_test_ini()

# ---- Now safe to import app ----
from fastapi.testclient import TestClient  # noqa: E402

from app.api import app  # noqa: E402
from parser.database.create_db_schema import create_db, drop_db  # noqa: E402

TEST_DB_URL = (
    f"postgresql://{TEST_DB_USER}:{TEST_DB_PASSWORD}"
    f"@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
)
PREFIX = f"/pride/ws/archive/crosslinking/{TEST_API_VERSION}"

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
TEST_DB_DUMP = os.path.join(FIXTURES_DIR, "test_db.sql")


@pytest.fixture(scope="session")
def test_db_engine():
    """Create test DB, load SQL dump (schema + data), yield engine, drop at teardown."""
    try:
        drop_db(TEST_DB_URL)
    except Exception:
        pass
    create_db(TEST_DB_URL)

    env = os.environ.copy()
    env["PGPASSWORD"] = TEST_DB_PASSWORD
    subprocess.run(
        [
            "psql",
            "-U", TEST_DB_USER,
            "-h", TEST_DB_HOST,
            "-p", TEST_DB_PORT,
            "-d", TEST_DB_NAME,
            "-f", TEST_DB_DUMP,
        ],
        check=True,
        capture_output=True,
        env=env,
    )

    engine = create_engine(TEST_DB_URL)
    yield engine
    engine.dispose()
    time.sleep(0.1)
    drop_db(TEST_DB_URL)


@pytest.fixture(scope="session")
def client(test_db_engine):
    """Session-scoped TestClient. Lifespan runs once: asyncpg pool connects to test DB."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-API-Key": TEST_API_KEY}
