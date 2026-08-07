"""
Tests for R1 and R2 read asset types.
"""

import pytest
from conftest import GENERAL_FIXTURES_DIR

import stargazer.utils.local_storage as _storage_mod
from stargazer.assets import specialize
from stargazer.assets.reads import R1, R2


@pytest.mark.asyncio
async def test_reads_fetch(fixtures_db):
    """Test query + specialize resolves R1 and R2 paths from TinyDB."""
    [r1_r] = await _storage_mod.default_client.query(
        {"asset": "r1", "sample_id": "NA12829"}
    )
    [r2_r] = await _storage_mod.default_client.query(
        {"asset": "r2", "sample_id": "NA12829"}
    )

    r1 = specialize(r1_r)
    r2 = specialize(r2_r)

    assert r1.path is not None
    assert r1.path.exists()
    assert r2.path is not None
    assert r2.path.exists()


@pytest.mark.asyncio
async def test_reads_get_paths():
    """Test direct access to r1 and r2 paths when set locally."""
    r1_path = GENERAL_FIXTURES_DIR / "NA12829_TP53_R1.fq.gz"
    r2_path = GENERAL_FIXTURES_DIR / "NA12829_TP53_R2.fq.gz"
    assert r1_path.exists()
    assert r2_path.exists()

    r1 = R1(cid="test", path=r1_path, sample_id="NA12829")
    r2 = R2(cid="test", path=r2_path, sample_id="NA12829")

    assert r1.path == r1_path
    assert r1.path.exists()
    assert r2.path == r2_path
    assert r2.path.exists()


@pytest.mark.asyncio
async def test_reads_update_components():
    """Test asset update() uploads files and sets metadata."""
    r1_fixture = GENERAL_FIXTURES_DIR / "NA12829_TP53_R1.fq.gz"
    r2_fixture = GENERAL_FIXTURES_DIR / "NA12829_TP53_R2.fq.gz"
    assert r1_fixture.exists()
    assert r2_fixture.exists()

    r1 = R1()
    await r1.update(r1_fixture, sample_id="NA12829", sequencing_platform="ILLUMINA")

    r2 = R2()
    await r2.update(r2_fixture, sample_id="NA12829", sequencing_platform="ILLUMINA")

    assert r1.sample_id == "NA12829"
    assert r1.sequencing_platform == "ILLUMINA"
    assert r1.cid != ""

    assert r2.sample_id == "NA12829"
    assert r2.sequencing_platform == "ILLUMINA"
    assert r2.cid != ""


@pytest.mark.asyncio
async def test_r1_path_not_cached():
    """Test that path is None when asset not fetched yet."""
    r1 = R1(cid="QmTest")
    assert r1.path is None


@pytest.mark.asyncio
async def test_r2_is_optional():
    """Test that R2 is a standalone optional asset."""
    r1 = R1(sample_id="NA12829")
    assert r1.sample_id == "NA12829"
    assert r1._asset_key == "r1"

    r2 = R2()
    assert r2._asset_key == "r2"
