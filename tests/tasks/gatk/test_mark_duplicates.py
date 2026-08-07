"""
Tests for mark_duplicates task.
"""

import shutil

import pytest
from conftest import GATK_FIXTURES_DIR

from stargazer.assets import Alignment
from stargazer.tasks.gatk.mark_duplicates import mark_duplicates


@pytest.mark.asyncio
async def test_mark_duplicates_marks_duplicates(fixtures_db):
    """Test that mark_duplicates creates a marked BAM."""
    if shutil.which("gatk") is None:
        pytest.skip("gatk not available in environment")

    sample_id = "NA12829_TP53_merged"

    alignment = Alignment(
        path=GATK_FIXTURES_DIR / "NA12829_TP53_merged.bam",
        sample_id=sample_id,
        format="bam",
        sorted="coordinate",
        tool="gatk_merge_bam_alignment",
    )

    fixtures_db()  # checkout: switch to isolated work dir

    marked = await mark_duplicates(alignment=alignment)

    assert isinstance(marked, Alignment)
    assert marked.sample_id == sample_id
    assert marked.duplicates_marked is True
    assert marked.tool == "gatk_mark_duplicates"
    assert marked.path is not None
    assert marked.path.exists()


@pytest.mark.asyncio
async def test_mark_duplicates_task_is_callable():
    """Test that mark_duplicates is a callable task."""
    assert callable(mark_duplicates)
    assert "mark_duplicates" in str(mark_duplicates)


class TestMarkDuplicatesExports:
    """Test that mark_duplicates task is properly exported."""

    def test_mark_duplicates_exported_from_package(self):
        """Test that mark_duplicates is accessible from stargazer.tasks."""
        from stargazer.tasks import mark_duplicates

        assert callable(mark_duplicates)
