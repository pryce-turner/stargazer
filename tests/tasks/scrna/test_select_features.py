"""Tests for select_features task."""

import pytest
import scanpy as sc
from conftest import SCRNA_FIXTURES_DIR

from stargazer.assets.scrna import AnnData
from stargazer.tasks.scrna.select_features import select_features

NORMALIZED_FIXTURE = SCRNA_FIXTURES_DIR / "normalized.h5ad"


@pytest.mark.asyncio
async def test_select_features_hvg(fixtures_db):
    """select_features annotates .var with highly_variable column."""
    adata = AnnData(
        path=NORMALIZED_FIXTURE,
        sample_id="test_sample",
        organism="human",
        stage="normalized",
        n_obs=59,
        n_vars=500,
    )

    fixtures_db()

    result = await select_features(adata=adata, n_top_genes=100)

    assert result.stage == "featured"
    ad = sc.read_h5ad(result.path)
    assert "highly_variable" in ad.var.columns
    assert ad.var["highly_variable"].sum() <= 100
