"""Tests for assemble() — storage query to specialized asset list."""

from unittest.mock import AsyncMock, patch

import pytest

from stargazer.assets.asset import assemble
from stargazer.assets.reads import R1, R2
from stargazer.assets.reference import AlignerIndex, Reference, ReferenceIndex


def raw(cid, **kv):
    return {"cid": cid, "keyvalues": kv}


@pytest.mark.asyncio
async def test_assemble_returns_specialized_list():
    """assemble() returns a flat list of specialized assets."""
    mock_client = AsyncMock()
    mock_client.query.return_value = [
        raw("Qmr", asset="reference", build="GRCh38"),
        raw("Qmi", asset="reference_index", build="GRCh38"),
    ]

    with patch("stargazer.utils.local_storage.default_client", mock_client):
        assets = await assemble(build="GRCh38")

    assert len(assets) == 2
    assert len([a for a in assets if isinstance(a, Reference)]) == 1
    assert len([a for a in assets if isinstance(a, ReferenceIndex)]) == 1


@pytest.mark.asyncio
async def test_assemble_list_filter_issues_multiple_queries():
    """asset=["r1", "r2"] generates two queries via cartesian product."""
    mock_client = AsyncMock()
    mock_client.query.side_effect = [
        [raw("Qmr1", asset="r1", sample_id="S1")],
        [raw("Qmr2", asset="r2", sample_id="S1")],
    ]

    with patch("stargazer.utils.local_storage.default_client", mock_client):
        assets = await assemble(sample_id="S1", asset=["r1", "r2"])

    assert mock_client.query.call_count == 2
    assert len([a for a in assets if isinstance(a, R1)]) == 1
    assert len([a for a in assets if isinstance(a, R2)]) == 1


@pytest.mark.asyncio
async def test_assemble_deduplicates_by_cid():
    """Duplicate CIDs across multiple queries are deduplicated."""
    mock_client = AsyncMock()
    dup = raw("Qmdup", asset="reference", build="GRCh38")
    mock_client.query.side_effect = [[dup], [dup]]

    with patch("stargazer.utils.local_storage.default_client", mock_client):
        assets = await assemble(build="GRCh38", asset=["reference", "reference"])

    assert len([a for a in assets if isinstance(a, Reference)]) == 1


@pytest.mark.asyncio
async def test_assemble_empty_result():
    """assemble() with no matches returns empty list."""
    mock_client = AsyncMock()
    mock_client.query.return_value = []

    with patch("stargazer.utils.local_storage.default_client", mock_client):
        assets = await assemble(build="nonexistent")

    assert assets == []


@pytest.mark.asyncio
async def test_assemble_multiple_same_key():
    """Multiple results with same _asset_key all appear in the list."""
    mock_client = AsyncMock()
    mock_client.query.return_value = [
        raw("Qm1", asset="aligner_index", aligner="bwa"),
        raw("Qm2", asset="aligner_index", aligner="bwa"),
    ]

    with patch("stargazer.utils.local_storage.default_client", mock_client):
        assets = await assemble(build="GRCh38")

    assert len([a for a in assets if isinstance(a, AlignerIndex)]) == 2
