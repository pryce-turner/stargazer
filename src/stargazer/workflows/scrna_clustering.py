"""
### scRNA-seq clustering pipeline: QC → normalization → clustering → marker genes.

Implements the scanpy clustering tutorial workflow as Flyte v2 tasks.
Assembles a raw AnnData from storage by sample_id, then runs the full
preprocessing and clustering stack.

Prerequisites:
    A raw .h5ad file must be uploaded to storage with asset="anndata" and stage="raw".

Reference:
    https://scanpy.readthedocs.io/en/stable/tutorials/basics/clustering.html

spec: [docs/workflows/scrna.md](../workflows/scrna.md)
"""

from stargazer.assets.asset import assemble
from stargazer.assets.scrna import AnnData
from stargazer.config import log_execution, scrna_env
from stargazer.tasks.scrna import (
    cluster,
    find_markers,
    normalize,
    qc_filter,
    reduce_dimensions,
    select_features,
)


@scrna_env.task(cache="disable")
async def scrna_clustering_pipeline(
    sample_id: str,
    organism: str = "human",
    n_top_genes: int = 2000,
    resolution: float = 0.5,
    max_pct_mt: float = 20.0,
) -> AnnData:
    """End-to-end scRNA-seq clustering pipeline.

    Runs QC filtering, normalization, feature selection, dimensionality
    reduction, Leiden clustering, and marker gene identification in sequence.

    Args:
        sample_id: Sample identifier used to look up the raw AnnData in storage
        organism: Organism name (e.g. "human", "mouse")
        n_top_genes: Number of highly variable genes to select
        resolution: Leiden clustering resolution (higher = more clusters)
        max_pct_mt: Maximum mitochondrial gene percentage per cell

    Returns:
        Annotated AnnData asset with cluster labels and ranked marker genes
    """
    log_execution()

    assets = await assemble(sample_id=sample_id, asset="anndata", stage="raw")
    if not assets:
        raise ValueError(
            f"No raw AnnData found for sample_id={sample_id!r}. "
            "Upload a .h5ad file with asset='anndata' and stage='raw' first."
        )
    raw = assets[0]

    filtered = await qc_filter(adata=raw, max_pct_mt=max_pct_mt, organism=organism)
    normalized = await normalize(adata=filtered)
    featured = await select_features(adata=normalized, n_top_genes=n_top_genes)
    reduced = await reduce_dimensions(adata=featured)
    clustered = await cluster(adata=reduced, resolution=resolution)
    annotated = await find_markers(adata=clustered)

    return annotated
