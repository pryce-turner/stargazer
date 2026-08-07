"""
Tests for sort_sam task.
"""

import shutil

import pytest
from conftest import GATK_FIXTURES_DIR

from stargazer.assets import Alignment
from stargazer.tasks.gatk.sort_sam import sort_sam


@pytest.mark.asyncio
async def test_sort_sam_sorts_bam(fixtures_db):
    """Test that sort_sam creates a sorted BAM."""
    if shutil.which("gatk") is None:
        pytest.skip("gatk not available in environment")

    sample_id = "NA12829_TP53_merged"

    alignment = Alignment(
        path=GATK_FIXTURES_DIR / "NA12829_TP53_merged.bam",
        sample_id=sample_id,
        format="bam",
        tool="bwa_mem",
    )

    fixtures_db()  # checkout: switch to isolated work dir

    sorted_bam = await sort_sam(alignment=alignment, sort_order="coordinate")

    assert isinstance(sorted_bam, Alignment)
    assert sorted_bam.sample_id == sample_id
    assert sorted_bam.sorted == "coordinate"
    assert sorted_bam.tool == "gatk_sort_sam"
    assert sorted_bam.path is not None
    assert sorted_bam.path.exists()


@pytest.mark.asyncio
async def test_sort_sam_validates_sort_order():
    """Test that sort_sam rejects invalid sort orders."""
    alignment = Alignment(sample_id="test")

    with pytest.raises(ValueError, match="Invalid sort_order"):
        await sort_sam(alignment=alignment, sort_order="invalid_order")


@pytest.mark.asyncio
async def test_sort_sam_task_is_callable():
    """Test that sort_sam is a callable task."""
    assert callable(sort_sam)
    assert "sort_sam" in str(sort_sam)


class TestSortSamExports:
    """Test that sort_sam task is properly exported."""

    def test_sort_sam_exported_from_package(self):
        """Test that sort_sam is accessible from stargazer.tasks."""
        from stargazer.tasks import sort_sam

        assert callable(sort_sam)
