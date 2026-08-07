"""
### Normalization and log transformation for scRNA-seq data.

spec: [docs/workflows/scrna.md](../workflows/scrna.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets.scrna import AnnData
from stargazer.config import logger, scrna_env


@scrna_env.task
async def normalize(adata: AnnData) -> AnnData:
    """Normalize counts and apply log1p transformation.

    Stores raw counts in .layers["counts"] before normalization so they
    are available for downstream differential expression analysis.

    Args:
        adata: QC-filtered AnnData asset

    Returns:
        Normalized and log-transformed AnnData asset
    """
    import scanpy as sc

    logger.info(adata.to_dict())
    await adata.fetch()
    ad = sc.read_h5ad(adata.path)

    sc.pp.normalize_total(ad)
    ad.layers["counts"] = ad.X.copy()
    sc.pp.log1p(ad)

    out_path = _storage.default_client.local_dir / "normalized.h5ad"
    ad.write_h5ad(out_path)

    result = AnnData(
        sample_id=adata.sample_id,
        organism=adata.organism,
        source_cid=adata.cid,
    )
    await result.update(out_path, n_obs=ad.n_obs, n_vars=ad.n_vars, stage="normalized")
    logger.info(result.to_dict())
    return result
