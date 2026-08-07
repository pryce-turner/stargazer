"""
Tests for apply_bqsr task.
"""

import shutil

import pytest
from conftest import GATK_FIXTURES_DIR, GENERAL_FIXTURES_DIR

from stargazer.assets import Alignment, BQSRReport, Reference
from stargazer.tasks.gatk.apply_bqsr import apply_bqsr


@pytest.mark.asyncio
async def test_apply_bqsr_recalibrates_bam(fixtures_db):
    """Test that apply_bqsr creates a recalibrated BAM."""
    if shutil.which("gatk") is None:
        pytest.skip("gatk not available in environment")

    sample_id = "NA12829_TP53_markdup"

    alignment = Alignment(
        path=GATK_FIXTURES_DIR / "NA12829_TP53_markdup.bam",
        sample_id=sample_id,
        format="bam",
        sorted="coordinate",
        duplicates_marked=True,
        tool="gatk_mark_duplicates",
    )

    ref = Reference(
        path=GENERAL_FIXTURES_DIR / "GRCh38_TP53.fa",
        build="GRCh38",
    )

    bqsr_report = BQSRReport(
        path=GATK_FIXTURES_DIR / "NA12829_TP53_bqsr.table",
        sample_id=sample_id,
        tool="gatk_base_recalibrator",
    )

    fixtures_db()  # checkout: switch to isolated work dir

    recalibrated = await apply_bqsr(
        alignment=alignment,
        ref=ref,
        bqsr_report=bqsr_report,
    )

    assert isinstance(recalibrated, Alignment)
    assert recalibrated.sample_id == sample_id
    assert recalibrated.bqsr_applied is True
    assert recalibrated.tool == "gatk_apply_bqsr"
    assert recalibrated.path is not None
    assert recalibrated.path.exists()


@pytest.mark.asyncio
async def test_apply_bqsr_task_is_callable():
    """Test that apply_bqsr is a callable task."""
    assert callable(apply_bqsr)
    assert "apply_bqsr" in str(apply_bqsr)


class TestBQSRExports:
    """Test that BQSR tasks are properly exported."""

    def test_apply_bqsr_exported_from_package(self):
        """Test that apply_bqsr is accessible from stargazer.tasks."""
        from stargazer.tasks import apply_bqsr

        assert callable(apply_bqsr)
