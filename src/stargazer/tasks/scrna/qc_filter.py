"""
### Quality control and cell/gene filtering for scRNA-seq data.

spec: [docs/workflows/scrna.md](../workflows/scrna.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets.scrna import AnnData
from stargazer.config import logger, scrna_env


@scrna_env.task
async def qc_filter(
    adata: AnnData,
    min_genes: int = 100,
    min_cells: int = 3,
    max_pct_mt: float = 20.0,
    batch_key: str = "",
    organism: str = "human",
) -> AnnData:
    """Filter low-quality cells and genes from raw scRNA-seq data.

    Applies standard QC filters: minimum gene/cell thresholds, mitochondrial
    gene percentage, and scrublet doublet detection.

    Args:
        adata: Raw AnnData asset (.h5ad)
        min_genes: Minimum number of genes expressed per cell
        min_cells: Minimum number of cells a gene must be expressed in
        max_pct_mt: Maximum mitochondrial gene percentage allowed per cell
        batch_key: Column in .obs to use as batch for scrublet (empty = no batch)
        organism: Organism name ("human" or "mouse") for gene prefix selection

    Returns:
        Filtered AnnData asset with QC metrics in .obs
    """
    import scanpy as sc

    logger.info(adata.to_dict())
    await adata.fetch()
    ad = sc.read_h5ad(adata.path)

    sc.pp.filter_cells(ad, min_genes=min_genes)
    sc.pp.filter_genes(ad, min_cells=min_cells)

    # Annotate mitochondrial, ribosomal, and hemoglobin genes (organism-aware)
    if organism.lower() == "mouse":
        ad.var["mt"] = ad.var_names.str.startswith("mt-")
        ad.var["ribo"] = ad.var_names.str.startswith(("Rps", "Rpl"))
        ad.var["hb"] = ad.var_names.str.contains("^Hb[^(p)]")
    else:
        ad.var["mt"] = ad.var_names.str.startswith("MT-")
        ad.var["ribo"] = ad.var_names.str.startswith(("RPS", "RPL"))
        ad.var["hb"] = ad.var_names.str.contains("^HB[^(P)]")

    sc.pp.calculate_qc_metrics(
        ad, qc_vars=["mt", "ribo", "hb"], inplace=True, log1p=True
    )

    # Doublet detection
    scrublet_kwargs = {}
    if batch_key:
        scrublet_kwargs["batch_key"] = batch_key
    sc.pp.scrublet(ad, **scrublet_kwargs)

    # Filter by mitochondrial percentage and predicted doublets
    ad = ad[ad.obs["pct_counts_mt"] < max_pct_mt]
    ad = ad[~ad.obs["predicted_doublet"]]

    out_path = _storage.default_client.local_dir / "qc_filtered.h5ad"
    ad.write_h5ad(out_path)

    result = AnnData(
        sample_id=adata.sample_id,
        organism=adata.organism,
        source_cid=adata.cid,
    )
    await result.update(out_path, n_obs=ad.n_obs, n_vars=ad.n_vars, stage="qc_filtered")
    logger.info(result.to_dict())
    return result
