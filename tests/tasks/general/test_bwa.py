"""
Tests for BWA tasks.
"""

import shutil
from pathlib import Path

import pytest
from conftest import GENERAL_FIXTURES_DIR

from stargazer.assets import AlignerIndex, Reference
from stargazer.tasks.general.bwa import bwa_index


@pytest.mark.asyncio
async def test_bwa_index(fixtures_db):
    """Test bwa index creates all index files (.amb, .ann, .bwt, .pac, .sa)."""
    if shutil.which("bwa") is None:
        pytest.skip("bwa not available in environment")

    ref = Reference(path=GENERAL_FIXTURES_DIR / "GRCh38_TP53.fa", build="GRCh38")

    fixtures_db()  # checkout: switch to isolated work dir

    result = await bwa_index(ref)

    assert isinstance(result, list)
    assert len(result) == 5, "Should have exactly 5 BWA index files"

    index_extensions = [".amb", ".ann", ".bwt", ".pac", ".sa"]
    for ext in index_extensions:
        matching = [idx for idx in result if idx.path and idx.path.name.endswith(ext)]
        assert len(matching) == 1, f"Should have exactly one {ext} file"

        idx = matching[0]
        assert isinstance(idx, AlignerIndex)
        assert idx.aligner == "bwa"
        assert idx.build == "GRCh38"
        assert idx.path is not None
        assert idx.path.exists()


@pytest.mark.asyncio
async def test_bwa_index_missing_file():
    """Test that bwa_index raises error when reference file is missing."""
    if shutil.which("bwa") is None:
        pytest.skip("bwa not available in environment")

    ref = Reference(path=Path("/nonexistent/path/ref.fasta"), build="GRCh38")

    with pytest.raises((FileNotFoundError, RuntimeError)):
        await bwa_index(ref)


@pytest.mark.asyncio
async def test_bwa_index_task_is_callable():
    """Test that bwa_index is a callable task."""
    assert callable(bwa_index)
    assert "bwa_index" in str(bwa_index)


class TestBwaExports:
    """Test that bwa tasks are properly exported."""

    def test_bwa_index_exported_from_package(self):
        """Test that bwa_index is accessible from stargazer.tasks."""
        from stargazer.tasks import bwa_index

        assert callable(bwa_index)
