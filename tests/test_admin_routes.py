"""Integration tests for admin/parser routes (auth + write endpoints)."""
import pytest

from tests.conftest import PREFIX

PARSE_PREFIX = f"{PREFIX}/parse"


@pytest.mark.integration
class TestAuthRequired:
    """All parse/admin endpoints require a valid X-API-Key header."""

    def test_parse_endpoint_requires_auth(self, client):
        resp = client.post(f"{PREFIX}/parse?px_accession=PXD001")
        assert resp.status_code == 401

    def test_delete_requires_auth(self, client):
        resp = client.delete(f"{PREFIX}/delete/ECOLI_MZML")
        assert resp.status_code == 401

    def test_write_new_upload_requires_auth(self, client):
        resp = client.post(f"{PARSE_PREFIX}/write_new_upload", json={
            "identification_file_name": "test.mzid",
            "identification_file_name_clean": "test-mzid",
            "project_id": "TEST001",
        })
        assert resp.status_code == 401

    def test_log_level_requires_auth(self, client):
        resp = client.put(f"{PREFIX}/log/debug")
        assert resp.status_code == 401

    def test_write_data_requires_auth(self, client):
        resp = client.post(f"{PARSE_PREFIX}/write_data", json={
            "table": "upload",
            "data": [],
        })
        assert resp.status_code == 401


@pytest.mark.integration
class TestWriteNewUpload:
    def test_returns_upload_id(self, client, auth_headers):
        resp = client.post(
            f"{PARSE_PREFIX}/write_new_upload",
            json={
                "identification_file_name": "test_admin.mzid",
                "identification_file_name_clean": "test-admin-mzid",
                "project_id": "TEST_ADMIN",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        upload_id = resp.json()
        assert isinstance(upload_id, int)
        assert upload_id > 0

    def test_wrong_api_key_returns_401(self, client):
        resp = client.post(
            f"{PARSE_PREFIX}/write_new_upload",
            json={
                "identification_file_name": "test.mzid",
                "identification_file_name_clean": "test-mzid",
                "project_id": "TEST001",
            },
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401


@pytest.mark.integration
class TestLogLevel:
    def test_change_log_level_returns_200(self, client, auth_headers):
        resp = client.put(f"{PREFIX}/log/info", headers=auth_headers)
        assert resp.status_code == 200
