"""
Tests for samtools tasks.
"""

import shutil
from pathlib import Path

import pytest
from conftest import GENERAL_FIXTURES_DIR

from stargazer.assets import Reference, ReferenceIndex
from stargazer.tasks.general.samtools import samtools_faidx


@pytest.mark.asyncio
async def test_samtools_faidx(fixtures_db):
    """Test samtools faidx creates .fai index file."""
    if shutil.which("samtools") is None:
        pytest.skip("samtools not available in environment")

    ref = Reference(path=GENERAL_FIXTURES_DIR / "GRCh38_TP53.fa", build="GRCh38")

    fixtures_db()  # checkout: switch to isolated work dir

    result = await samtools_faidx(ref)

    assert isinstance(result, ReferenceIndex)
    assert result.tool == "samtools_faidx"
    assert result.build == "GRCh38"
    assert result.path is not None
    assert result.path.exists()
    assert result.path.name.endswith(".fai")


@pytest.mark.asyncio
async def test_samtools_faidx_idempotent(fixtures_db):
    """Test that samtools_faidx is idempotent (doesn't fail if .fai already exists)."""
    if shutil.which("samtools") is None:
        pytest.skip("samtools not available in environment")

    ref = Reference(path=GENERAL_FIXTURES_DIR / "GRCh38_TP53.fa", build="GRCh38")

    fixtures_db()  # checkout

    result = await samtools_faidx(ref)

    assert isinstance(result, ReferenceIndex)
    assert result.path is not None


@pytest.mark.asyncio
async def test_samtools_faidx_missing_file():
    """Test that samtools_faidx raises error when reference file is missing."""
    if shutil.which("samtools") is None:
        pytest.skip("samtools not available in environment")

    ref = Reference(path=Path("/nonexistent/path/ref.fasta"), build="GRCh38")

    with pytest.raises((FileNotFoundError, RuntimeError)):
        await samtools_faidx(ref)


@pytest.mark.asyncio
async def test_samtools_faidx_task_is_callable():
    """Test that samtools_faidx is a callable task."""
    assert callable(samtools_faidx)
    assert "samtools_faidx" in str(samtools_faidx)


class TestSamtoolsExports:
    """Test that samtools tasks are properly exported."""

    def test_samtools_faidx_exported_from_package(self):
        """Test that samtools_faidx is accessible from stargazer.tasks."""
        from stargazer.tasks import samtools_faidx

        assert callable(samtools_faidx)
