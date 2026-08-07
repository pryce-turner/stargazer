"""Tests for normalize task."""

import pytest
import scanpy as sc
from conftest import SCRNA_FIXTURES_DIR

from stargazer.assets.scrna import AnnData
from stargazer.tasks.scrna.normalize import normalize

QC_FILTERED_FIXTURE = SCRNA_FIXTURES_DIR / "qc_filtered.h5ad"


@pytest.mark.asyncio
async def test_normalize_layers(fixtures_db):
    """normalize stores raw counts in .layers['counts'] and applies log1p."""
    adata = AnnData(
        path=QC_FILTERED_FIXTURE,
        sample_id="test_sample",
        organism="human",
        stage="qc_filtered",
        n_obs=59,
        n_vars=500,
    )

    fixtures_db()

    result = await normalize(adata=adata)

    assert result.stage == "normalized"
    assert result.path is not None and result.path.exists()

    ad = sc.read_h5ad(result.path)
    assert "counts" in ad.layers
    # log1p values should be >= 0
    assert float(ad.X.min()) >= 0.0
