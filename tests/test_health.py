"""Integration tests for the /health endpoint."""
import pytest

from tests.conftest import PREFIX


@pytest.mark.integration
def test_health_returns_200(client):
    resp = client.get(f"{PREFIX}/health")
    assert resp.status_code == 200


@pytest.mark.integration
def test_health_has_required_keys(client):
    data = client.get(f"{PREFIX}/health").json()
    assert "status" in data
    assert "db_status" in data


@pytest.mark.integration
def test_health_status_ok(client):
    data = client.get(f"{PREFIX}/health").json()
    assert data["status"] == "OK"


@pytest.mark.integration
def test_health_db_status_ok(client):
    data = client.get(f"{PREFIX}/health").json()
    assert data["db_status"] == "OK"
