"""
Tests for Variants asset types.
"""

import pytest
from conftest import GATK_FIXTURES_DIR

import stargazer.utils.local_storage as _storage_mod
from stargazer.assets import specialize
from stargazer.assets.variants import KnownSites, Variants, VariantsIndex


@pytest.mark.asyncio
async def test_variants_fetch(fixtures_db):
    """Test query + specialize resolves VCF and index paths from TinyDB."""
    [vcf_r] = await _storage_mod.default_client.query(
        {"asset": "variants", "sample_id": "NA12829"}
    )
    [idx_r] = await _storage_mod.default_client.query(
        {"asset": "variants_index", "sample_id": "NA12829"}
    )

    vcf = specialize(vcf_r)
    idx = specialize(idx_r)

    assert vcf.path is not None
    assert vcf.path.exists()
    assert idx.path is not None
    assert idx.path.exists()


@pytest.mark.asyncio
async def test_variants_get_vcf_path():
    """Test direct access to variants asset returns correct path."""
    vcf_path = GATK_FIXTURES_DIR / "NA12829_TP53.g.vcf"
    assert vcf_path.exists()

    vcf = Variants(cid="test", path=vcf_path, sample_id="NA12829")

    assert vcf.path == vcf_path
    assert vcf.path.exists()


@pytest.mark.asyncio
async def test_variants_update_components():
    """Test asset update() uploads files and sets metadata."""
    vcf_fixture = GATK_FIXTURES_DIR / "NA12829_TP53.g.vcf"
    idx_fixture = GATK_FIXTURES_DIR / "NA12829_TP53.g.vcf.idx"
    assert vcf_fixture.exists()
    assert idx_fixture.exists()

    vcf = Variants()
    await vcf.update(
        vcf_fixture,
        sample_id="NA12829",
        caller="haplotypecaller",
        variant_type="gvcf",
        build="GRCh38",
    )

    tbi = VariantsIndex()
    await tbi.update(idx_fixture, sample_id="NA12829")

    assert vcf.sample_id == "NA12829"
    assert vcf.caller == "haplotypecaller"
    assert vcf.variant_type == "gvcf"
    assert vcf.build == "GRCh38"
    assert vcf.cid != ""

    assert tbi.sample_id == "NA12829"
    assert tbi.cid != ""


@pytest.mark.asyncio
async def test_variants_path_not_cached():
    """Test that path is None when asset not fetched yet."""
    vcf = Variants(cid="QmTest")
    assert vcf.path is None


@pytest.mark.asyncio
async def test_variants_properties():
    """Test Variants properties read from keyvalues."""
    vcf = Variants(caller="deepvariant", variant_type="gvcf")
    assert vcf.caller == "deepvariant"
    assert vcf.variant_type == "gvcf"

    vcf2 = Variants(caller="haplotypecaller", variant_type="vcf")
    assert vcf2.caller == "haplotypecaller"
    assert vcf2.variant_type == "vcf"

    vcf3 = Variants()
    assert vcf3.caller == ""
    assert vcf3.variant_type == ""


@pytest.mark.asyncio
async def test_variants_source_samples():
    """Test source_samples and sample_count properties on Variants asset."""
    vcf = Variants(sample_count=3, source_samples=["NA12829", "NA12830", "NA12831"])
    assert vcf.sample_count == 3
    assert vcf.source_samples == ["NA12829", "NA12830", "NA12831"]

    vcf2 = Variants(sample_count=1)
    assert vcf2.sample_count == 1
    assert vcf2.source_samples is None


@pytest.mark.asyncio
async def test_variants_source_samples_default():
    """Test source_samples is None when metadata not set."""
    vcf = Variants()
    assert vcf.source_samples is None


@pytest.mark.asyncio
async def test_known_sites_standalone(fixtures_db):
    """Test KnownSites is a standalone asset scoped by build."""
    results = await _storage_mod.default_client.query(
        {"asset": "known_sites", "build": "GRCh38"}
    )
    assert len(results) > 0
    for r in results:
        assert r["keyvalues"].get("asset") == "known_sites"
        assert r["keyvalues"].get("build") == "GRCh38"
        assert "sample_id" not in r["keyvalues"]


@pytest.mark.asyncio
async def test_known_sites_asset():
    """Test KnownSites asset fields."""
    ks = KnownSites()
    ks.build = "GRCh38"
    assert ks._asset_key == "known_sites"
    assert ks.build == "GRCh38"
