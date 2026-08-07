"""
Tests for base_recalibrator task.
"""

import shutil

import pytest
from conftest import GATK_FIXTURES_DIR, GENERAL_FIXTURES_DIR

from stargazer.assets import Alignment, BQSRReport, KnownSites, Reference
from stargazer.tasks.gatk.base_recalibrator import base_recalibrator

KNOWN_SITES_VCF = "Mills_and_1000G_gold_standard.indels.TP53.hg38.vcf"


@pytest.mark.asyncio
async def test_base_recalibrator_creates_report(fixtures_db):
    """Test that base_recalibrator returns a BQSRReport."""
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

    known_sites_file = KnownSites(
        path=GATK_FIXTURES_DIR / KNOWN_SITES_VCF,
        build="GRCh38",
    )

    fixtures_db()  # checkout: switch to isolated work dir

    result = await base_recalibrator(
        alignment=alignment,
        ref=ref,
        known_sites=[known_sites_file],
    )

    assert isinstance(result, BQSRReport)
    assert result.sample_id == sample_id
    assert result.tool == "gatk_base_recalibrator"
    assert result.path is not None
    assert result.path.exists()


@pytest.mark.asyncio
async def test_base_recalibrator_rejects_empty_known_sites():
    """Test that base_recalibrator raises error for empty known_sites."""
    with pytest.raises(ValueError, match="known_sites list cannot be empty"):
        await base_recalibrator(
            alignment=Alignment(sample_id="test"),
            ref=Reference(build="test"),
            known_sites=[],
        )


@pytest.mark.asyncio
async def test_base_recalibrator_task_is_callable():
    """Test that base_recalibrator is a callable task."""
    assert callable(base_recalibrator)
    assert "base_recalibrator" in str(base_recalibrator)


class TestBQSRExports:
    """Test that BQSR tasks are properly exported."""

    def test_base_recalibrator_exported_from_package(self):
        """Test that base_recalibrator is accessible from stargazer.tasks."""
        from stargazer.tasks import base_recalibrator

        assert callable(base_recalibrator)
