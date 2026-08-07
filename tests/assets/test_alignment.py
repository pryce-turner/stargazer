"""
Tests for Alignment asset types.
"""

import pytest
from conftest import GATK_FIXTURES_DIR

import stargazer.utils.local_storage as _storage_mod
from stargazer.assets import specialize
from stargazer.assets.alignment import (
    Alignment,
    AlignmentIndex,
    BQSRReport,
    DuplicateMetrics,
)


@pytest.mark.asyncio
async def test_alignment_fetch(fixtures_db):
    """Test query + specialize resolves BAM and BAI paths from TinyDB."""
    [bam_r] = await _storage_mod.default_client.query(
        {"asset": "alignment", "sample_id": "NA12829", "bqsr_applied": "true"}
    )
    bam = specialize(bam_r)

    [idx_r] = await _storage_mod.default_client.query(
        {"asset": "alignment_index", "alignment_cid": bam.cid}
    )
    idx = specialize(idx_r)

    assert bam.path is not None
    assert bam.path.exists()
    assert idx.path is not None
    assert idx.path.exists()


@pytest.mark.asyncio
async def test_alignment_get_bam_path():
    """Test direct access to alignment asset returns correct path."""
    bam_path = GATK_FIXTURES_DIR / "NA12829_TP53_paired.bam"
    assert bam_path.exists()

    bam = Alignment(cid="test", path=bam_path, sample_id="NA12829")

    assert bam.path == bam_path
    assert bam.path.exists()


@pytest.mark.asyncio
async def test_alignment_get_bai_path():
    """Test direct access to alignment index asset returns correct path."""
    bai_path = GATK_FIXTURES_DIR / "NA12829_TP53_paired.bam.bai"
    assert bai_path.exists()

    idx = AlignmentIndex(cid="test", path=bai_path, sample_id="NA12829")

    assert idx.path == bai_path
    assert idx.path.exists()


@pytest.mark.asyncio
async def test_alignment_update_components():
    """Test asset update() uploads files and sets metadata."""
    bam_fixture = GATK_FIXTURES_DIR / "NA12829_TP53_paired.bam"
    bai_fixture = GATK_FIXTURES_DIR / "NA12829_TP53_paired.bam.bai"
    assert bam_fixture.exists()
    assert bai_fixture.exists()

    bam = Alignment()
    await bam.update(
        bam_fixture,
        sample_id="NA12829",
        format="bam",
        sorted="coordinate",
        duplicates_marked=True,
    )

    idx = AlignmentIndex()
    await idx.update(bai_fixture, sample_id="NA12829")

    assert bam.sample_id == "NA12829"
    assert bam.sorted == "coordinate"
    assert bam.duplicates_marked is True
    assert bam.cid != ""

    assert idx.sample_id == "NA12829"
    assert idx.cid != ""


@pytest.mark.asyncio
async def test_alignment_path_not_cached():
    """Test that path is None when asset not fetched yet."""
    bam = Alignment(cid="QmTest")
    assert bam.path is None


@pytest.mark.asyncio
async def test_alignment_metadata_properties():
    """Test Alignment properties read from keyvalues."""
    bam = Alignment(duplicates_marked=True, sorted="coordinate")

    assert bam.duplicates_marked is True
    assert bam.sorted == "coordinate"

    bam2 = Alignment()
    assert bam2.duplicates_marked is False
    assert bam2.sorted == ""


@pytest.mark.asyncio
async def test_alignment_bqsr_applied():
    """Test bqsr_applied property reads from keyvalues."""
    bam = Alignment(bqsr_applied=True)
    assert bam.bqsr_applied is True

    bam2 = Alignment()
    assert bam2.bqsr_applied is False


@pytest.mark.asyncio
async def test_bqsr_report_asset():
    """Test BQSRReport asset fields."""
    report = BQSRReport()
    report.sample_id = "NA12829"
    assert report._asset_key == "bqsr_report"
    assert report.sample_id == "NA12829"


@pytest.mark.asyncio
async def test_duplicate_metrics_asset():
    """Test DuplicateMetrics asset fields."""
    metrics = DuplicateMetrics()
    metrics.sample_id = "NA12829"
    assert metrics._asset_key == "duplicate_metrics"
