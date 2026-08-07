"""
Tests for merge_bam_alignment task.
"""

import shutil

import pytest
from conftest import GATK_FIXTURES_DIR, GENERAL_FIXTURES_DIR

from stargazer.assets import Alignment, Reference
from stargazer.tasks.gatk.merge_bam_alignment import merge_bam_alignment


@pytest.mark.asyncio
async def test_merge_bam_alignment_merges_bams(fixtures_db):
    """Test that merge_bam_alignment creates a merged BAM."""
    if shutil.which("gatk") is None:
        pytest.skip("gatk not available in environment")

    sample_id = "NA12829_TP53_merge"

    aligned_bam = Alignment(
        path=GATK_FIXTURES_DIR / "NA12829_TP53_bwa_aligned.bam",
        sample_id=sample_id,
        format="bam",
        tool="bwa_mem",
    )

    unmapped_bam = Alignment(
        path=GATK_FIXTURES_DIR / "NA12829_TP53_unmapped.bam",
        sample_id=sample_id,
        format="bam",
        sorted="queryname",
        tool="picard",
    )

    ref = Reference(
        path=GENERAL_FIXTURES_DIR / "GRCh38_TP53.fa",
        build="GRCh38",
    )

    fixtures_db()  # checkout: switch to isolated work dir

    merged = await merge_bam_alignment(
        aligned_bam=aligned_bam,
        unmapped_bam=unmapped_bam,
        ref=ref,
    )

    assert isinstance(merged, Alignment)
    assert merged.sample_id == sample_id
    assert merged.sorted == "coordinate"
    assert merged.tool == "gatk_merge_bam_alignment"
    assert merged.path is not None
    assert merged.path.exists()


@pytest.mark.asyncio
async def test_merge_bam_alignment_task_is_callable():
    """Test that merge_bam_alignment is a callable task."""
    assert callable(merge_bam_alignment)
    assert "merge_bam_alignment" in str(merge_bam_alignment)


class TestMergeBamAlignmentExports:
    """Test that merge_bam_alignment task is properly exported."""

    def test_merge_bam_alignment_exported_from_package(self):
        """Test that merge_bam_alignment is accessible from stargazer.tasks."""
        from stargazer.tasks import merge_bam_alignment

        assert callable(merge_bam_alignment)
