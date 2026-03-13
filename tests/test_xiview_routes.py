"""Integration tests for the xiVIEW data routes."""
import struct
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import PREFIX


DATA_PREFIX = f"{PREFIX}/data"


@pytest.mark.integration
class TestGetDatasets:
    def test_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_datasets")
        assert resp.status_code == 200

    def test_fixture_entry_present(self, client):
        data = client.get(f"{DATA_PREFIX}/get_datasets").json()
        assert {"project_id": "ECOLI_MZML", "identification_file_name": "mzml_ecoli_dsso.mzid"} in data


@pytest.mark.integration
class TestVisualisations:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/visualisations/ECOLI_MZML")
        assert resp.status_code == 200

    def test_exact_visualisation(self, client):
        data = client.get(f"{DATA_PREFIX}/visualisations/ECOLI_MZML").json()
        assert len(data) == 1
        assert data[0]["filename"] == "mzml-ecoli-dsso-mzid"
        assert data[0]["visualisation"] == "cross-linking"

    def test_unknown_project_returns_empty_list(self, client):
        data = client.get(f"{DATA_PREFIX}/visualisations/DOESNOTEXIST").json()
        assert data == []


@pytest.mark.integration
class TestGetXiviewMatches:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML")
        assert resp.status_code == 200

    def test_exact_count_and_columns(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML").json()
        assert len(data) == 22
        expected_keys = {"id", "pi1", "pi2", "sc", "ui", "c_mz", "pc_c", "pc_mz", "sp", "sd", "p", "r", "sip", "msi_id", "msi_pc"}
        assert expected_keys == set(data[0].keys())

    def test_all_matches_belong_to_upload_1(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML").json()
        assert all(m["ui"] == 1 for m in data)

    def test_all_matches_pass_threshold(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML").json()
        assert all(m["p"] is True for m in data)

    def test_unknown_project_returns_empty_data(self, client):
        # No 404 is raised when no uploads found (file param not provided)
        resp = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=DOESNOTEXIST")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_limit_zero_returns_422(self, client):
        # limit has ge=1 constraint
        resp = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML&limit=0")
        assert resp.status_code == 422

    def test_limit_works(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_matches?project=ECOLI_MZML&limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


@pytest.mark.integration
class TestGetXiviewPeptides:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_peptides?project=ECOLI_MZML")
        assert resp.status_code == 200

    def test_exact_count_and_columns(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_peptides?project=ECOLI_MZML").json()
        assert len(data) == 38
        expected_keys = {"id", "u_id", "seq", "prt", "pos", "dec", "ls1", "ls2", "m_as", "m_ps", "m_ms", "cl_m"}
        assert expected_keys == set(data[0].keys())

    def test_all_peptides_belong_to_upload_1(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_peptides?project=ECOLI_MZML").json()
        assert all(p["u_id"] == 1 for p in data)

    def test_unknown_project_returns_empty_data(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_peptides?project=DOESNOTEXIST")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.integration
class TestGetXiviewProteins:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=ECOLI_MZML")
        assert resp.status_code == 200

    def test_exact_count_and_columns(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=ECOLI_MZML").json()
        assert len(data) == 12
        expected_keys = {"id", "name", "accession", "sequence", "search_id", "description"}
        assert expected_keys == set(data[0].keys())

    def test_known_protein_accession_present(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=ECOLI_MZML").json()
        assert "P0C0V0" in {p["accession"] for p in data}

    def test_all_proteins_belong_to_upload_1(self, client):
        data = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=ECOLI_MZML").json()
        assert all(p["search_id"] == "1" for p in data)

    def test_unknown_project_returns_empty_data(self, client):
        resp = client.get(f"{DATA_PREFIX}/get_xiview_proteins?project=DOESNOTEXIST")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.integration
class TestGetAnnotatedPeaklist:
    ENDPOINT = f"{DATA_PREFIX}/get_annotated_peaklist"

    # Minimal xi2-format crosslink annotation request — peaks are injected by the endpoint
    ANNOTATION_BODY = {
        "LinkSite": [
            {"id": 0, "peptideId": 0, "linkSite": 1},
            {"id": 0, "peptideId": 1, "linkSite": 0},
        ],
        "Peptides": [
            {"base_sequence": "AKT", "modification_ids": [], "modification_positions": []},
            {"base_sequence": "KMR", "modification_ids": [], "modification_positions": []},
        ],
        "annotation": {
            "returnModSyntax": "Xmod",
            "precursorMZ": 297.509118,
            "precursorCharge": 3,
            "config": {
                "ms2_tol": "10 ppm",
                "crosslinker": ["BS3"],
                "fragmentation": {
                    "nterm_ions": ["b"],
                    "cterm_ions": ["y"],
                    "add_precursor": True,
                },
            },
        },
    }

    # Theoretical fragment m/z values for the AKT(K1)-BS3-KMR(K0) crosslink at z=3
    PEAK_MZS = [
        771.454556526179,
        819.4756858935789,
        386.23091649652895,
        410.24148118022896,
        257.82303648664566,
        273.8300796091123,
    ]
    PEAK_INTENSITIES = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]

    def _make_mock_row(self):
        mz_bytes = struct.pack(f"{len(self.PEAK_MZS)}d", *self.PEAK_MZS)
        int_bytes = struct.pack(f"{len(self.PEAK_INTENSITIES)}d", *self.PEAK_INTENSITIES)
        return {"mz": mz_bytes, "intensity": int_bytes}

    def _post(self, client, body=None):
        mock_row = self._make_mock_row()
        with patch("app.routes.xiview.execute_query", new_callable=AsyncMock, return_value=mock_row):
            return client.post(
                f"{self.ENDPOINT}?id=scan1&sd_ref=1&upload_id=1",
                json=body if body is not None else self.ANNOTATION_BODY,
            )

    def test_returns_200(self, client):
        assert self._post(client).status_code == 200

    def test_response_has_expected_keys(self, client):
        data = self._post(client).json()
        for key in ("peaks", "fragments", "clusters", "annotation"):
            assert key in data

    def test_peaks_count_matches_db_data(self, client):
        data = self._post(client).json()
        assert len(data["peaks"]) == len(self.PEAK_MZS)

    def test_peaks_come_from_db_not_request_body(self, client):
        """Any peaks in the request body are overwritten with DB data."""
        body_with_stale_peaks = {
            **self.ANNOTATION_BODY,
            "peaks": [{"mz": 9999.0, "intensity": 1.0}],
        }
        data = self._post(client, body=body_with_stale_peaks).json()
        assert not any(abs(p["mz"] - 9999.0) < 0.001 for p in data["peaks"])
        assert len(data["peaks"]) == len(self.PEAK_MZS)

    def test_annotation_has_calculated_mz(self, client):
        data = self._post(client).json()
        assert "calculatedMZ" in data["annotation"]

    def test_fragments_are_returned(self, client):
        """Known theoretical peaks should produce at least one annotated fragment."""
        data = self._post(client).json()
        assert len(data["fragments"]) > 0

    def test_missing_params_returns_422(self, client):
        resp = client.post(self.ENDPOINT, json=self.ANNOTATION_BODY)
        assert resp.status_code == 422
