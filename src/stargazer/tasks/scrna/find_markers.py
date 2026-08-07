"""
### Marker gene identification via differential expression for scRNA-seq data.

spec: [docs/workflows/scrna.md](../workflows/scrna.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets.scrna import AnnData
from stargazer.config import logger, scrna_env


@scrna_env.task
async def find_markers(
    adata: AnnData,
    groupby: str = "leiden",
    method: str = "wilcoxon",
) -> AnnData:
    """Identify marker genes for each cluster using differential expression.

    Uses raw count data from .layers["counts"] for statistical testing.
    Results are stored in .uns["rank_genes_groups"].

    Args:
        adata: Clustered AnnData asset with .layers["counts"]
        groupby: .obs column to group cells by (cluster labels)
        method: Statistical test method ("wilcoxon", "t-test", etc.)

    Returns:
        AnnData asset with ranked marker genes in .uns["rank_genes_groups"]
    """
    import scanpy as sc

    logger.info(adata.to_dict())
    await adata.fetch()
    ad = sc.read_h5ad(adata.path)

    sc.tl.rank_genes_groups(ad, groupby=groupby, method=method, layer="counts")

    out_path = _storage.default_client.local_dir / "markers.h5ad"
    ad.write_h5ad(out_path)

    result = AnnData(
        sample_id=adata.sample_id,
        organism=adata.organism,
        source_cid=adata.cid,
    )
    await result.update(out_path, n_obs=ad.n_obs, n_vars=ad.n_vars, stage="annotated")
    logger.info(result.to_dict())
    return result
