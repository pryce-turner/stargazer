"""Tests for BQSRReport type (declared in alignment.py)."""

from pathlib import Path

from stargazer.assets import ASSET_REGISTRY
from stargazer.assets.alignment import BQSRReport
from stargazer.assets.asset import Asset


class TestBQSRReport:
    def test_asset_keyvalue(self):
        assert BQSRReport()._asset_key == "bqsr_report"

    def test_is_asset(self):
        assert isinstance(BQSRReport(), Asset)

    def test_sample_id_via_attr(self):
        r = BQSRReport()
        r.sample_id = "NA12829"
        assert r.sample_id == "NA12829"

    def test_sample_id_default(self):
        r = BQSRReport()
        assert r.sample_id == ""

    def test_registered_in_asset_registry(self):
        assert "bqsr_report" in ASSET_REGISTRY
        assert ASSET_REGISTRY["bqsr_report"] is BQSRReport

    def test_roundtrip(self):
        r = BQSRReport(
            cid="Qm" + "a" * 44,
            path=Path("/tmp/sample_bqsr.table"),
            sample_id="NA12829",
        )
        restored = BQSRReport.from_dict(r.to_dict())
        assert restored.cid == r.cid
        assert restored.path == r.path
        assert restored.sample_id == r.sample_id
