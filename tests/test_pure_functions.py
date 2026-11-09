"""Unit tests for pure helper functions. No database required."""
import pytest

from fastapi import HTTPException

from app.routes.shared import build_xiview_cache_key
from app.routes.xiview_xi2 import _parse_resultset_ids, _normalise_resultset_ids


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


A = "ace83ae4-3a4a-4d09-bd61-df9326345a88"
B = "37a0bea4-2a40-4ebe-b2b8-3a773e46c12a"
C = "9a50ca00-db52-4163-9d57-ae793b212082"


@pytest.mark.unit
class TestParseResultsetIds:
    def test_single_bare_uuid(self):
        assert _parse_resultset_ids([A]) == [A]

    def test_aggregated_with_groups(self):
        assert _parse_resultset_ids([f"{A}~B3.{B}~B2.{C}~B1"]) == [A, B, C]

    def test_aggregated_without_groups(self):
        assert _parse_resultset_ids([f"{A}.{B}"]) == [A, B]

    def test_repeated_params(self):
        assert _parse_resultset_ids([A, f"{B}~g"]) == [A, B]

    def test_duplicates_removed_keeping_order(self):
        assert _parse_resultset_ids([f"{B}~x.{A}.{B}~y"]) == [B, A]

    def test_invalid_uuid(self):
        with pytest.raises(HTTPException) as e:
            _parse_resultset_ids([f"{A}~B1.not-a-uuid"])
        assert e.value.status_code == 400

    def test_normalise_ignores_order_and_groups(self):
        assert _normalise_resultset_ids([f"{C}~B1.{A}~B3"]) == sorted([A, C])
