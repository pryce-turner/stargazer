"""
### Leiden community detection clustering for scRNA-seq data.

spec: [docs/workflows/scrna.md](../workflows/scrna.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets.scrna import AnnData
from stargazer.config import logger, scrna_env


@scrna_env.task
async def cluster(
    adata: AnnData,
    resolution: float = 0.5,
    key_added: str = "leiden",
) -> AnnData:
    """Assign cells to clusters using the Leiden algorithm.

    Requires a precomputed neighbor graph (.uns["neighbors"]).
    Cluster labels are stored in .obs[key_added].

    Args:
        adata: AnnData asset with neighbor graph
        resolution: Leiden resolution parameter (higher = more clusters)
        key_added: .obs column name to store cluster labels

    Returns:
        AnnData asset with cluster labels in .obs
    """
    import scanpy as sc

    logger.info(adata.to_dict())
    await adata.fetch()
    ad = sc.read_h5ad(adata.path)

    sc.tl.leiden(
        ad, flavor="igraph", n_iterations=2, resolution=resolution, key_added=key_added
    )

    out_path = _storage.default_client.local_dir / "clustered.h5ad"
    ad.write_h5ad(out_path)

    result = AnnData(
        sample_id=adata.sample_id,
        organism=adata.organism,
        source_cid=adata.cid,
    )
    await result.update(out_path, n_obs=ad.n_obs, n_vars=ad.n_vars, stage="clustered")
    logger.info(result.to_dict())
    return result
