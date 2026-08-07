"""
Tests for combine_gvcfs task.
"""

import shutil

import pytest
from conftest import GATK_FIXTURES_DIR, GENERAL_FIXTURES_DIR

from stargazer.assets import Reference, Variants
from stargazer.tasks.gatk.combine_gvcfs import combine_gvcfs

SAMPLE_GVCFS = {
    "NA12829": "NA12829_TP53.g.vcf",
    "NA12891": "NA12891_TP53.g.vcf",
    "NA12892": "NA12892_TP53.g.vcf",
}


def make_ref() -> Reference:
    """Build a Reference asset for the TP53 region fixture."""
    return Reference(
        path=GENERAL_FIXTURES_DIR / "GRCh38_TP53.fa",
        build="GRCh38",
    )


def make_gvcf(sample_id: str) -> Variants:
    """Build a Variants GVCF asset for the given sample."""
    return Variants(
        path=GATK_FIXTURES_DIR / SAMPLE_GVCFS[sample_id],
        sample_id=sample_id,
        caller="haplotypecaller",
        variant_type="gvcf",
        build="GRCh38",
    )


@pytest.mark.asyncio
async def test_combine_gvcfs_merges_samples(fixtures_db):
    """Test that combine_gvcfs merges multiple GVCFs."""
    if shutil.which("gatk") is None:
        pytest.skip("gatk not available in environment")

    sample_ids = ["NA12829", "NA12891", "NA12892"]
    gvcfs = [make_gvcf(sid) for sid in sample_ids]
    ref = make_ref()

    fixtures_db()  # checkout: switch to isolated work dir

    result = await combine_gvcfs(gvcfs=gvcfs, ref=ref, cohort_id="test_family")

    assert isinstance(result, Variants)
    assert result.sample_id == "test_family"
    assert result.variant_type == "gvcf"
    assert result.caller == "combine_gvcfs"
    assert result.sample_count == 3
    assert set(result.source_samples) == set(sample_ids)
    assert result.path is not None
    assert result.path.exists()


@pytest.mark.asyncio
async def test_combine_gvcfs_rejects_empty_list():
    """Test that combine_gvcfs raises error for empty list."""
    with pytest.raises(ValueError, match="cannot be empty"):
        await combine_gvcfs(gvcfs=[], ref=Reference(build="test"))


@pytest.mark.asyncio
async def test_combine_gvcfs_rejects_vcf_input():
    """Test that combine_gvcfs raises error when any input is VCF (not GVCF)."""
    vcf = Variants(
        sample_id="NA12878_vcf",
        variant_type="vcf",
        caller="haplotypecaller",
    )
    with pytest.raises(ValueError, match="requires GVCF files"):
        await combine_gvcfs(
            gvcfs=[vcf],
            ref=Reference(build="test"),
        )


@pytest.mark.asyncio
async def test_combine_gvcfs_single_sample(fixtures_db):
    """Test that combine_gvcfs works with a single sample (edge case)."""
    if shutil.which("gatk") is None:
        pytest.skip("gatk not available in environment")

    sample_id = "NA12829"
    gvcf = make_gvcf(sample_id)
    ref = make_ref()

    fixtures_db()  # checkout

    result = await combine_gvcfs(
        gvcfs=[gvcf], ref=ref, cohort_id="single_sample_cohort"
    )

    assert isinstance(result, Variants)
    assert result.variant_type == "gvcf"
    assert result.sample_count == 1
    assert result.source_samples == [sample_id]


@pytest.mark.asyncio
async def test_combine_gvcfs_task_is_callable():
    """Test that combine_gvcfs is a callable task."""
    assert callable(combine_gvcfs)
    assert "combine_gvcfs" in str(combine_gvcfs)


class TestCombineGvcfsExports:
    """Test that combine_gvcfs is properly exported."""

    def test_combine_gvcfs_exported_from_package(self):
        """Test that combine_gvcfs is accessible from stargazer.tasks."""
        from stargazer.tasks import combine_gvcfs

        assert callable(combine_gvcfs)
