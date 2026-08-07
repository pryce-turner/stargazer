"""Tests for cluster task."""

import pytest
import scanpy as sc
from conftest import SCRNA_FIXTURES_DIR

from stargazer.assets.scrna import AnnData
from stargazer.tasks.scrna.cluster import cluster

REDUCED_FIXTURE = SCRNA_FIXTURES_DIR / "reduced.h5ad"


@pytest.mark.asyncio
async def test_cluster_leiden_labels(fixtures_db):
    """cluster adds leiden cluster labels to .obs."""
    adata = AnnData(
        path=REDUCED_FIXTURE,
        sample_id="test_sample",
        organism="human",
        stage="reduced",
        n_obs=59,
        n_vars=500,
    )

    fixtures_db()

    result = await cluster(adata=adata, resolution=0.5)

    assert result.stage == "clustered"
    ad = sc.read_h5ad(result.path)
    assert "leiden" in ad.obs.columns
    # At least 1 cluster
    assert ad.obs["leiden"].nunique() >= 1
