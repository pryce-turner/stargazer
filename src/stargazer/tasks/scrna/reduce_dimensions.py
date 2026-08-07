"""
### PCA, neighbor graph, and UMAP dimensionality reduction for scRNA-seq data.

spec: [docs/workflows/scrna.md](../workflows/scrna.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets.scrna import AnnData
from stargazer.config import logger, scrna_env


@scrna_env.task
async def reduce_dimensions(
    adata: AnnData,
    n_pcs: int = 50,
    n_neighbors: int = 15,
) -> AnnData:
    """Compute PCA, k-nearest neighbor graph, and UMAP embedding.

    Operates on the highly variable gene subset to reduce noise. Embeddings
    are stored in .obsm for downstream clustering and visualization.

    Args:
        adata: AnnData asset with HVG annotations in .var
        n_pcs: Number of principal components to compute
        n_neighbors: Number of neighbors for the kNN graph

    Returns:
        AnnData asset with PCA (.obsm["X_pca"]), neighbors, and UMAP (.obsm["X_umap"])
    """
    import scanpy as sc

    logger.info(adata.to_dict())
    await adata.fetch()
    ad = sc.read_h5ad(adata.path)

    sc.tl.pca(ad, n_comps=n_pcs)
    sc.pp.neighbors(ad, n_neighbors=n_neighbors, n_pcs=n_pcs)
    sc.tl.umap(ad)

    out_path = _storage.default_client.local_dir / "reduced.h5ad"
    ad.write_h5ad(out_path)

    result = AnnData(
        sample_id=adata.sample_id,
        organism=adata.organism,
        source_cid=adata.cid,
    )
    await result.update(out_path, n_obs=ad.n_obs, n_vars=ad.n_vars, stage="reduced")
    logger.info(result.to_dict())
    return result
