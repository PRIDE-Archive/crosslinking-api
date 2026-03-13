"""Integration tests for the PRIDE core routes."""
import pytest

from tests.conftest import PREFIX


@pytest.mark.integration
class TestStatisticsCount:
    def test_returns_200(self, client):
        resp = client.get(f"{PREFIX}/statistics-count")
        assert resp.status_code == 200

    def test_exact_values(self, client):
        data = client.get(f"{PREFIX}/statistics-count").json()
        assert data["Number of Projects"] == 7
        assert data["Number of species"] == 1
        assert data["Number of proteins"] is None
        assert data["Number of peptides"] is None
        assert data["Number of spectra"] is None


@pytest.mark.integration
class TestProjectsPerSpecies:
    def test_returns_200(self, client):
        resp = client.get(f"{PREFIX}/projects-per-species")
        assert resp.status_code == 200

    def test_exact_species_list(self, client):
        data = client.get(f"{PREFIX}/projects-per-species").json()
        assert data == [{"organism": "Escherichia coli", "count": 7}]


@pytest.mark.integration
class TestProjectDetail:
    def test_known_project_returns_200(self, client):
        resp = client.get(f"{PREFIX}/projects/ECOLI_MZML")
        assert resp.status_code == 200

    def test_known_project_fields(self, client):
        data = client.get(f"{PREFIX}/projects/ECOLI_MZML").json()
        assert isinstance(data, list)
        assert data[0]["project_id"] == "ECOLI_MZML"
        assert data[0]["title"] == "ECOLI_MZML test project"
        assert data[0]["description"] == "Fixture data for crosslinking-api tests"
        assert data[0]["organism"] == "Escherichia coli"

    def test_known_project_sub_detail(self, client):
        data = client.get(f"{PREFIX}/projects/ECOLI_MZML").json()
        sub = data[0]["project_sub_details"][0]
        assert sub["protein_accession"] == "P0A9Q7"
        assert sub["gene_name"] == "dnaK"
        assert sub["number_of_peptides"] == 5
        assert sub["number_of_cross_links"] == 3

    def test_unknown_project_returns_404(self, client):
        resp = client.get(f"{PREFIX}/projects/DOESNOTEXIST999")
        assert resp.status_code == 404


@pytest.mark.integration
class TestProjectSearch:
    def test_page_size_too_small_returns_422(self, client):
        # page_size has gt=5 constraint, so 5 is invalid
        resp = client.get(f"{PREFIX}/projects?page_size=5")
        assert resp.status_code == 422

    def test_page_size_6_is_valid(self, client):
        resp = client.get(f"{PREFIX}/projects?page_size=6")
        assert resp.status_code in (200, 404)

    def test_query_matching_project_returns_result(self, client):
        # ECOLI_MZML has exactly one projectsubdetail row
        resp = client.get(f"{PREFIX}/projects?query=ECOLI_MZML")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"]["total_elements"] == 1
        assert data["page"]["page_no"] == 1
        assert data["page"]["page_size"] == 10

    def test_query_nonexistent_project_returns_404(self, client):
        resp = client.get(f"{PREFIX}/projects?query=NOSUCHPROJECT")
        assert resp.status_code == 404
