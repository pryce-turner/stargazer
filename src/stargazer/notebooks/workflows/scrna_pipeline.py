# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "matplotlib",
#   "numpy",
#   "scanpy>=1.12",
#   "anndata",
#   "stargazer",
# ]
#
# [tool.uv.sources]
# stargazer = { path = "/stargazer", editable = true }
# ///
"""
### Stargazer scRNA-seq.

Multi-sample fan-out preprocessing plus interactive clustering, side-by-side
UMAPs, and marker-gene tables. The workflow-tier showcase: the same
primitives the tutorials teach, applied as a full-fat scRNA-seq pipeline.

spec: [docs/architecture/notebook.md](../../docs/architecture/notebook.md)
"""

import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    """Imports, warning filters, and Flyte init."""
    import warnings

    warnings.filterwarnings("ignore", message="Variable names are not unique")
    warnings.filterwarnings("ignore", category=RuntimeWarning, module="scanpy")

    import asyncio

    import flyte
    import marimo as mo
    import matplotlib.pyplot as plt
    import numpy as np

    flyte.init_from_config()

    mo.md(
        """
        # Stargazer — scRNA-seq

        A guided tour of how Stargazer wraps scanpy in a Flyte v2
        orchestrator. Pick samples from the `scrna_demo` bundle, then
        watch two inline `@scrna_env.task` workflows process them in
        parallel:

        - **`preprocess`** (qc_filter → normalize → select_features →
          reduce_dimensions) runs once per sample, fanned out with
          `asyncio.gather`.
        - **`cluster_and_annotate`** (cluster → find_markers) re-runs
          across all samples whenever you nudge the resolution slider —
          preprocessing outputs are reused, only the cheap stage repeats.

        Side-by-side UMAPs and a comparison table at the bottom let you
        eyeball the effect of each change across samples at once.
        """
    )
    return asyncio, mo, np, plt


@app.cell
def _(mo):
    """Task and workflow catalog."""
    from stargazer.registry import TaskRegistry

    _registry = TaskRegistry()
    _task_rows = [
        {
            "name": t["name"],
            "description": t["description"],
            "params": ", ".join(f"{p['name']}: {p['type']}" for p in t["params"]),
        }
        for t in _registry.to_catalog()
    ]
    mo.vstack(
        [
            mo.md(
                "## Task Catalog\n\n"
                "Every Stargazer task and workflow registered with the MCP server. "
                "The two workflows defined later in this notebook will appear here as "
                "well once the cell runs."
            ),
            mo.ui.table(
                _task_rows,
                label="All registered tasks and workflows",
                selection=None,
            ),
        ]
    )


@app.cell
def _(mo):
    """Section 1 — sample selection."""
    sample_picker = mo.ui.multiselect(
        options=["s1d1", "s1d3"], value=["s1d1", "s1d3"], label="Samples"
    )
    mo.vstack(
        [
            mo.md(
                """
                ## 1. Pick Samples

                Choose one or more samples from the `scrna_demo` bundle.
                The selection drives every downstream cell — the raw QC
                peek, both workflow stages, and the side-by-side UMAPs.
                If nothing is cached locally, the bundle is fetched
                automatically on the next cell.
                """
            ),
            sample_picker,
        ]
    )
    return (sample_picker,)


@app.cell
async def _(mo, sample_picker):
    """Assemble raw AnnData for every selected sample (auto-fetch on miss)."""
    import scanpy as sc

    from stargazer.assets.asset import assemble
    from stargazer.bundles import fetch_bundle

    if not sample_picker.value:
        mo.stop(True, mo.md("Pick at least one sample."))

    async def _resolve(sid):
        """Look up a single raw AnnData asset by sample_id, or None if missing."""
        hits = await assemble(sample_id=sid, asset="anndata", stage="raw")
        return hits[0] if hits else None

    raw_assets = [await _resolve(_sid) for _sid in sample_picker.value]
    if any(_a is None for _a in raw_assets):
        with mo.status.spinner(title="Fetching scrna_demo bundle..."):
            await fetch_bundle("scrna_demo")
        raw_assets = [await _resolve(_sid) for _sid in sample_picker.value]

    _missing = [_sid for _sid, _a in zip(sample_picker.value, raw_assets) if _a is None]
    if _missing:
        mo.stop(
            True,
            mo.md(
                f"Samples not in `scrna_demo`: **{', '.join(_missing)}**. "
                "Try `s1d1` or `s1d3`."
            ),
        )

    raw_ads = []
    for _a in raw_assets:
        await _a.fetch()
        _ad = sc.read_h5ad(_a.path)
        _ad.var_names_make_unique()
        raw_ads.append(_ad)

    mo.md(
        "Loaded: "
        + ", ".join(
            f"**{_a.sample_id}** ({_ad.n_obs:,} cells)"
            for _a, _ad in zip(raw_assets, raw_ads)
        )
    )
    return raw_ads, raw_assets, sc


@app.cell
def _(mo, plt, raw_ads, raw_assets, sc):
    """Section 2 — per-sample raw QC histograms (one row per sample)."""
    _n = len(raw_ads)
    _fig, _axes = plt.subplots(_n, 4, figsize=(16, 3.5 * _n), squeeze=False)

    for _row, (_asset, _ad) in enumerate(zip(raw_assets, raw_ads)):
        _ad = _ad.copy()
        _ad.var["mt"] = _ad.var_names.str.startswith(
            "MT-"
        ) | _ad.var_names.str.startswith("mt-")
        _ad.var["ribo"] = _ad.var_names.str.startswith(("RPS", "RPL", "Rps", "Rpl"))
        sc.pp.calculate_qc_metrics(
            _ad, qc_vars=["mt", "ribo"], inplace=True, log1p=False
        )

        for _ax, key, color, title in zip(
            _axes[_row],
            ["n_genes_by_counts", "total_counts", "pct_counts_mt", "pct_counts_ribo"],
            ["#2196F3", "#4CAF50", "#FF5722", "#9C27B0"],
            ["Genes per cell", "Total counts", "% Mitochondrial", "% Ribosomal"],
        ):
            _ax.hist(_ad.obs[key], bins=50, color=color)
            _ax.set_title(f"{_asset.sample_id} — {title}")
            _ax.set_xlabel(key)

    _fig.suptitle("Raw QC Metrics", fontsize=14, fontweight="bold")
    _fig.tight_layout()

    mo.vstack(
        [
            mo.md(
                """
                ## 2. Raw Quality Control

                A quick look at each sample before any filtering. Genes per
                cell and total counts highlight low-quality droplets;
                mitochondrial and ribosomal fractions surface stressed or
                lysed cells. The `qc_filter` task in the workflow below
                trims cells using the **Max % mitochondrial** slider.
                """
            ),
            _fig,
        ]
    )


@app.cell
def _(mo):
    """Section 3 — narrative for the two-workflow design."""
    mo.md(
        """
        ## 3. Compose Two Flyte Workflows

        Pure scanpy is a sequence of function calls. Stargazer tasks are
        Flyte tasks — they have caching, asset-typed I/O, and they fan
        out. The next cell defines two `@scrna_env.task` workflows that
        compose existing Stargazer tasks:

        - **`preprocess`** — qc_filter → normalize → select_features →
          reduce_dimensions. Expensive (PCA, neighbors, UMAP) so we only
          want to run it once per sample.
        - **`cluster_and_annotate`** — cluster → find_markers. Cheap,
          parameter-sensitive, and the only thing that needs to re-run
          when you tweak the Leiden resolution.

        Splitting the pipeline this way lets the cheap stage be
        interactive while reusing the expensive stage's outputs. Both
        workflows fan out across the picked samples with
        `asyncio.gather`, so multiple samples run concurrently.
        """
    )


@app.cell
def _():
    """Define the preprocess and cluster_and_annotate workflows."""
    from stargazer.assets.scrna import AnnData
    from stargazer.config import scrna_env
    from stargazer.tasks.scrna import (
        cluster,
        find_markers,
        normalize,
        qc_filter,
        reduce_dimensions,
        select_features,
    )

    @scrna_env.task(cache="disable")
    async def preprocess(raw: AnnData, max_pct_mt: float, n_top_genes: int) -> AnnData:
        """QC, normalize, select HVGs, and reduce dimensions for one sample."""
        filtered = await qc_filter(adata=raw, max_pct_mt=max_pct_mt)
        normalized = await normalize(adata=filtered)
        featured = await select_features(adata=normalized, n_top_genes=n_top_genes)
        return await reduce_dimensions(adata=featured)

    @scrna_env.task(cache="disable")
    async def cluster_and_annotate(reduced: AnnData, resolution: float) -> AnnData:
        """Leiden clustering followed by marker gene identification."""
        clustered = await cluster(adata=reduced, resolution=resolution)
        return await find_markers(adata=clustered)

    return cluster_and_annotate, preprocess


@app.cell
def _(mo):
    """Section 4 — preprocess parameters."""
    max_mt_slider = mo.ui.slider(
        start=5.0, stop=50.0, step=1.0, value=20.0, label="Max % mitochondrial"
    )
    n_genes_slider = mo.ui.slider(
        start=500, stop=5000, step=250, value=2000, label="Top variable genes"
    )
    mo.vstack(
        [
            mo.md(
                """
                ## 4. Run `preprocess` in Parallel

                These two parameters feed the expensive stage. Adjusting
                either re-runs `preprocess` for every selected sample;
                changing only the resolution further down does not.
                """
            ),
            mo.hstack([max_mt_slider, n_genes_slider], justify="start"),
        ]
    )
    return max_mt_slider, n_genes_slider


@app.cell
async def _(asyncio, max_mt_slider, mo, n_genes_slider, preprocess, raw_assets):
    """Fan out preprocess across selected samples with asyncio.gather."""
    with mo.status.spinner(
        title=f"Preprocessing {len(raw_assets)} sample(s) in parallel..."
    ):
        reduced_assets = list(
            await asyncio.gather(
                *[
                    preprocess(
                        raw=_r,
                        max_pct_mt=max_mt_slider.value,
                        n_top_genes=n_genes_slider.value,
                    )
                    for _r in raw_assets
                ]
            )
        )

    mo.md(
        "Preprocessed: "
        + ", ".join(f"**{_a.sample_id}** ({_a.n_obs:,} cells)" for _a in reduced_assets)
    )
    return (reduced_assets,)


@app.cell
def _(mo):
    """Section 5 — clustering parameter (drives the cheap stage)."""
    resolution_slider = mo.ui.slider(
        start=0.1, stop=2.0, step=0.1, value=0.5, label="Leiden resolution"
    )
    mo.vstack(
        [
            mo.md(
                """
                ## 5. Tune Clustering Across Samples

                Move the slider — only `cluster_and_annotate` re-runs, in
                parallel across all selected samples. The expensive
                `preprocess` outputs above are reused as inputs.
                """
            ),
            resolution_slider,
        ]
    )
    return (resolution_slider,)


@app.cell
async def _(
    asyncio,
    cluster_and_annotate,
    mo,
    reduced_assets,
    resolution_slider,
    sc,
):
    """Fan out cluster + find_markers across samples."""
    with mo.status.spinner(
        title=f"Clustering {len(reduced_assets)} sample(s) in parallel..."
    ):
        annotated_assets = list(
            await asyncio.gather(
                *[
                    cluster_and_annotate(reduced=_r, resolution=resolution_slider.value)
                    for _r in reduced_assets
                ]
            )
        )

    annotated_ads = []
    for _a in annotated_assets:
        await _a.fetch()
        annotated_ads.append(sc.read_h5ad(_a.path))
    return annotated_ads, annotated_assets


@app.cell
def _(annotated_ads, annotated_assets, mo, np, plt):
    """Side-by-side UMAPs across samples."""
    _n = len(annotated_assets)
    _fig, _axes = plt.subplots(1, _n, figsize=(7 * _n, 6), squeeze=False)

    for _ax, _asset, _ad in zip(_axes[0], annotated_assets, annotated_ads):
        _umap = _ad.obsm["X_umap"]
        _labels = _ad.obs["leiden"].astype(int)
        _n_clusters = _labels.nunique()
        _cmap = plt.colormaps["tab20"].resampled(max(_n_clusters, 1))
        _ax.scatter(_umap[:, 0], _umap[:, 1], c=_labels, cmap=_cmap, s=2, alpha=0.6)

        for _cl in range(_n_clusters):
            _mask = _labels == _cl
            _cx, _cy = np.median(_umap[_mask, 0]), np.median(_umap[_mask, 1])
            _ax.annotate(
                str(_cl),
                (_cx, _cy),
                fontsize=10,
                fontweight="bold",
                ha="center",
                va="center",
                bbox={"boxstyle": "round,pad=0.2", "fc": "white", "alpha": 0.8},
            )

        _ax.set_title(f"{_asset.sample_id} — {_n_clusters} clusters")
        _ax.set_xlabel("UMAP1")
        _ax.set_ylabel("UMAP2")
        _ax.set_aspect("equal")

    _fig.tight_layout()
    mo.vstack([mo.md("### Clusters per sample"), _fig])


@app.cell
def _(annotated_ads, annotated_assets, mo):
    """Comparison summary across samples."""
    _rows = [
        {
            "sample": _a.sample_id,
            "organism": _a.organism,
            "cells": f"{_ad.n_obs:,}",
            "genes": f"{_ad.n_vars:,}",
            "clusters": _ad.obs["leiden"].nunique(),
            "cid": _a.cid,
        }
        for _a, _ad in zip(annotated_assets, annotated_ads)
    ]
    mo.vstack(
        [
            mo.md(
                "## Summary\n\n"
                "Final per-sample stats. Each `cid` is a content-addressed "
                "handle to the annotated AnnData stored locally — the same "
                "asset can be loaded from any other Stargazer task or notebook."
            ),
            mo.ui.table(_rows, selection=None),
        ]
    )


@app.cell
def _(mo):
    """Links to the tutorial sequence."""
    mo.md(
        """
        ## Next Steps

        The tutorials build up the primitives behind this pipeline:

        - [Assets](/tutorials/assets) — typed, content-addressed I/O
        - [Tasks](/tutorials/tasks) — single units of work
        - [Workflows](/tutorials/workflows) — composing tasks
        - [Execution](/tutorials/execution) — local vs remote
        """
    )


if __name__ == "__main__":
    app.run()
