"""
Tests for create_sequence_dictionary task.
"""

import shutil

import pytest
from conftest import GENERAL_FIXTURES_DIR

from stargazer.assets import Reference, SequenceDict
from stargazer.tasks.gatk.create_sequence_dictionary import create_sequence_dictionary


@pytest.mark.asyncio
async def test_create_sequence_dictionary_creates_dict(fixtures_db):
    """Test create_sequence_dictionary creates .dict file."""
    if shutil.which("gatk") is None:
        pytest.skip("gatk not available in environment")

    ref = Reference(
        path=GENERAL_FIXTURES_DIR / "GRCh38_TP53.fa",
        build="GRCh38",
    )

    fixtures_db()  # checkout: switch to isolated work dir

    result = await create_sequence_dictionary(ref)

    assert isinstance(result, SequenceDict)
    assert result.build == "GRCh38"
    assert result.tool == "gatk_CreateSequenceDictionary"
    assert result.path is not None
    assert result.path.exists()
    assert result.path.name.endswith(".dict")


@pytest.mark.asyncio
async def test_create_sequence_dictionary_is_callable():
    """Test that create_sequence_dictionary is a callable task."""
    assert callable(create_sequence_dictionary)
    assert "create_sequence_dictionary" in str(create_sequence_dictionary)


class TestExports:
    """Test that create_sequence_dictionary is properly exported."""

    def test_exported_from_package(self):
        """Test that create_sequence_dictionary is accessible from stargazer.tasks."""
        from stargazer.tasks import create_sequence_dictionary

        assert callable(create_sequence_dictionary)
