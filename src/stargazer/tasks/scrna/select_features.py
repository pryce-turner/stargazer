"""
### Highly variable gene selection for scRNA-seq data.

spec: [docs/workflows/scrna.md](../workflows/scrna.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets.scrna import AnnData
from stargazer.config import logger, scrna_env


@scrna_env.task
async def select_features(
    adata: AnnData,
    n_top_genes: int = 2000,
    batch_key: str = "",
) -> AnnData:
    """Select highly variable genes for dimensionality reduction.

    Annotates .var with highly_variable flags. Downstream tasks use only
    the highly variable subset.

    Args:
        adata: Normalized AnnData asset
        n_top_genes: Number of top highly variable genes to select
        batch_key: Column in .obs to use as batch (empty = no batch correction)

    Returns:
        AnnData asset with HVG annotations in .var
    """
    import scanpy as sc

    logger.info(adata.to_dict())
    await adata.fetch()
    ad = sc.read_h5ad(adata.path)

    hvg_kwargs = {"n_top_genes": n_top_genes}
    if batch_key:
        hvg_kwargs["batch_key"] = batch_key
    sc.pp.highly_variable_genes(ad, **hvg_kwargs)

    out_path = _storage.default_client.local_dir / "features_selected.h5ad"
    ad.write_h5ad(out_path)

    result = AnnData(
        sample_id=adata.sample_id,
        organism=adata.organism,
        source_cid=adata.cid,
    )
    await result.update(out_path, n_obs=ad.n_obs, n_vars=ad.n_vars, stage="featured")
    logger.info(result.to_dict())
    return result
