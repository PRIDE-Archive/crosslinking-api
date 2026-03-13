"""Unit tests for pure helper functions. No database required."""
import pytest

from app.routes.shared import build_xiview_cache_key


@pytest.mark.unit
class TestBuildXiviewCacheKey:
    def test_with_file(self):
        key = build_xiview_cache_key("matches", "PXD001", "file.mzid")
        assert key == "xiview:matches:PXD001:file.mzid"

    def test_without_file(self):
        key = build_xiview_cache_key("matches", "PXD001")
        assert key == "xiview:matches:PXD001"

    def test_peptides_endpoint(self):
        assert build_xiview_cache_key("peptides", "PXD123") == "xiview:peptides:PXD123"

    def test_proteins_endpoint_with_file(self):
        key = build_xiview_cache_key("proteins", "PXD999", "myfile.mzid")
        assert key == "xiview:proteins:PXD999:myfile.mzid"

    def test_none_file_omits_file_segment(self):
        key = build_xiview_cache_key("matches", "PXD001", None)
        assert key == "xiview:matches:PXD001"
