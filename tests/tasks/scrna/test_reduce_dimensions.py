"""Tests for reduce_dimensions task."""

import pytest
import scanpy as sc
from conftest import SCRNA_FIXTURES_DIR

from stargazer.assets.scrna import AnnData
from stargazer.tasks.scrna.reduce_dimensions import reduce_dimensions

FEATURED_FIXTURE = SCRNA_FIXTURES_DIR / "featured.h5ad"


@pytest.mark.asyncio
async def test_reduce_dimensions_embeddings(fixtures_db):
    """reduce_dimensions adds PCA and UMAP embeddings to .obsm."""
    adata = AnnData(
        path=FEATURED_FIXTURE,
        sample_id="test_sample",
        organism="human",
        stage="featured",
        n_obs=59,
        n_vars=500,
    )

    fixtures_db()

    result = await reduce_dimensions(adata=adata, n_pcs=10, n_neighbors=5)

    assert result.stage == "reduced"
    ad = sc.read_h5ad(result.path)
    assert "X_pca" in ad.obsm
    assert "X_umap" in ad.obsm
    assert ad.obsm["X_umap"].shape == (ad.n_obs, 2)
