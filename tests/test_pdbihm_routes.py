"""Integration tests for the PDB-IHM routes."""
import math
import pytest

from tests.conftest import PREFIX

PDBIHM_PREFIX = f"{PREFIX}/pdbihm"

# A protein accession known to be in the dbsequence table of the ECOLI_MZML fixture.
# Periplasmic serine endoprotease DegP from E. coli K-12.
KNOWN_PROTEIN_ACCESSION = "P0C0V0"


@pytest.mark.integration
class TestGetProjectsByProtein:
    def test_known_accession_returns_200(self, client):
        resp = client.get(f"{PDBIHM_PREFIX}/projects/{KNOWN_PROTEIN_ACCESSION}")
        assert resp.status_code == 200

    def test_exact_projects_for_known_accession(self, client):
        data = client.get(f"{PDBIHM_PREFIX}/projects/{KNOWN_PROTEIN_ACCESSION}").json()
        # P0C0V0 appears in dbsequence for upload 1 (ECOLI_MZML) and 2 (ECOLI_MGF) only
        assert set(data) == {"ECOLI_MZML", "ECOLI_MGF"}

    def test_unknown_accession_returns_empty_list(self, client):
        data = client.get(f"{PDBIHM_PREFIX}/projects/UNKNOWNACCXYZ").json()
        assert data == []


@pytest.mark.integration
class TestResiduePairs:
    # Fixture has 10 unique residue pairs for ECOLI_MZML (same count for passing and all).
    TOTAL_ELEMENTS = 10

    def test_passing_threshold_returns_200(self, client):
        resp = client.get(
            f"{PDBIHM_PREFIX}/projects/ECOLI_MZML"
            f"/residue-pairs/based-on-reported-psm-level/passing"
        )
        assert resp.status_code == 200

    def test_all_threshold_returns_200(self, client):
        resp = client.get(
            f"{PDBIHM_PREFIX}/projects/ECOLI_MZML"
            f"/residue-pairs/based-on-reported-psm-level/all"
        )
        assert resp.status_code == 200

    def test_invalid_threshold_returns_422(self, client):
        resp = client.get(
            f"{PDBIHM_PREFIX}/projects/ECOLI_MZML"
            f"/residue-pairs/based-on-reported-psm-level/invalid_value"
        )
        assert resp.status_code == 422

    def test_passing_exact_pagination(self, client):
        data = client.get(
            f"{PDBIHM_PREFIX}/projects/ECOLI_MZML"
            f"/residue-pairs/based-on-reported-psm-level/passing"
        ).json()
        page = data["page"]
        assert page["page_no"] == 1
        assert page["page_size"] == 10
        assert page["total_elements"] == self.TOTAL_ELEMENTS
        assert page["total_pages"] == math.ceil(self.TOTAL_ELEMENTS / 10)

    def test_all_exact_total_elements(self, client):
        data = client.get(
            f"{PDBIHM_PREFIX}/projects/ECOLI_MZML"
            f"/residue-pairs/based-on-reported-psm-level/all"
        ).json()
        assert data["page"]["total_elements"] == self.TOTAL_ELEMENTS

    def test_page_size_too_small_returns_422(self, client):
        # page_size has gt=5 constraint
        resp = client.get(
            f"{PDBIHM_PREFIX}/projects/ECOLI_MZML"
            f"/residue-pairs/based-on-reported-psm-level/passing"
            f"?page_size=5"
        )
        assert resp.status_code == 422

    def test_pagination_page_size(self, client):
        data = client.get(
            f"{PDBIHM_PREFIX}/projects/ECOLI_MZML"
            f"/residue-pairs/based-on-reported-psm-level/all"
            f"?page=1&page_size=10"
        ).json()
        assert len(data.get("data", [])) <= 10
