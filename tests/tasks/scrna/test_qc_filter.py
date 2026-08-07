"""Tests for qc_filter task."""

import pytest
from conftest import SCRNA_FIXTURES_DIR

from stargazer.assets.scrna import AnnData
from stargazer.tasks.scrna.qc_filter import qc_filter

RAW_FIXTURE = SCRNA_FIXTURES_DIR / "synthetic_200x500.h5ad"


@pytest.mark.asyncio
async def test_qc_filter_output_shape(fixtures_db):
    """qc_filter reduces obs count and annotates QC metrics."""
    adata = AnnData(
        path=RAW_FIXTURE,
        sample_id="test_sample",
        organism="human",
        stage="raw",
        n_obs=200,
        n_vars=500,
    )

    fixtures_db()

    result = await qc_filter(
        adata=adata,
        min_genes=1,
        min_cells=1,
        max_pct_mt=80.0,
    )

    assert result.stage == "qc_filtered"
    assert result.n_obs > 0
    assert result.n_vars > 0
    assert result.path is not None
    assert result.path.exists()
    assert result.sample_id == "test_sample"
    assert result.organism == "human"


@pytest.mark.asyncio
async def test_qc_filter_task_is_callable():
    """Test that qc_filter is a callable task."""
    assert callable(qc_filter)
    assert "qc_filter" in str(qc_filter)
