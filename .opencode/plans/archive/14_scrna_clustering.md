# scRNA-seq Clustering Pipeline

Implement a scanpy-based scRNA-seq clustering workflow as Flyte v2 tasks in Stargazer, following the [scanpy clustering tutorial](https://scanpy.readthedocs.io/en/stable/tutorials/basics/clustering.html).

**Key difference from existing tasks:** Scanpy tasks call Python functions directly on in-memory AnnData objects rather than wrapping CLI tools via subprocess.

---

## Step 0: Infrastructure Setup

### 0a. Add dependencies to `pyproject.toml`
- `uv add scanpy anndata`

### 0b. Add `scrna_env` TaskEnvironment to `src/stargazer/config.py`
- New `flyte.TaskEnvironment(name="scrna", ...)` with appropriate memory resources (scanpy is memory-hungry)
- No special image needed — scanpy is a pure Python pip dependency

### 0c. Dockerfile
- scanpy/anndata are pip packages handled by `uv sync`, no bioconda addition needed

---

## Step 1: Types — `src/stargazer/assets/scrna.py`

Create Asset subclass for AnnData files:

```python
@dataclass
class AnnData(Asset):
    _asset_key: ClassVar[str] = "anndata"
    sample_id: str = ""
    n_obs: int = 0  # number of cells/observations
    n_vars: int = 0  # number of genes/variables
    stage: str = (
        ""  # pipeline stage: "raw", "qc_filtered", "normalized", "reduced", "clustered"
    )
    organism: str = ""  # e.g. "human", "mouse"
    source_cid: str = ""  # provenance: input anndata CID
```

Register in `src/stargazer/assets/__init__.py` — add import + `__all__` entry.

---

## Step 2: Tasks — `src/stargazer/tasks/scrna/`

Create `src/stargazer/tasks/scrna/__init__.py` and the following task modules.

### Common task pattern

All tasks follow this structure:

```python
@scrna_env.task
async def task_name(adata: AnnData, ...) -> AnnData:
    """Docstring with spec link."""
    import scanpy as sc
    await adata.fetch()
    ad = sc.read_h5ad(adata.path)
    # ... scanpy operations ...
    out_path = _storage.default_client.local_dir / "output.h5ad"
    ad.write_h5ad(out_path)
    result = AnnData(sample_id=adata.sample_id, organism=adata.organism, source_cid=adata.cid)
    await result.update(out_path, n_obs=ad.n_obs, n_vars=ad.n_vars, stage="...")
    return result
```

### 2a. `qc_filter.py` — Quality control and cell/gene filtering
- **Input:** `AnnData` (raw counts .h5ad)
- **Output:** `AnnData` (filtered)
- **Steps:**
  1. `sc.pp.filter_cells(min_genes=100)`
  2. `sc.pp.filter_genes(min_cells=3)`
  3. Annotate mitochondrial/ribosomal/hemoglobin genes
  4. `sc.pp.calculate_qc_metrics(qc_vars=["mt", "ribo", "hb"])`
  5. `sc.pp.scrublet(batch_key=...)` for doublet detection
  6. Filter by user-configurable thresholds (pct_counts_mt, predicted_doublet)
- **Key params:** `min_genes=100`, `min_cells=3`, `max_pct_mt=20.0`, `batch_key=""`

### 2b. `normalize.py` — Normalization and log transformation
- **Input:** `AnnData` (QC-filtered)
- **Output:** `AnnData` (normalized + log-transformed, raw counts in `.layers["counts"]`)
- **Steps:**
  1. `sc.pp.normalize_total()`
  2. Store raw counts: `adata.layers["counts"] = adata.X.copy()`
  3. `sc.pp.log1p()`
- **Key params:** none (standard normalization)

### 2c. `select_features.py` — Highly variable gene selection
- **Input:** `AnnData` (normalized)
- **Output:** `AnnData` (HVG annotated in `.var`)
- **Steps:**
  1. `sc.pp.highly_variable_genes(n_top_genes=n_top_genes, batch_key=...)`
- **Key params:** `n_top_genes=2000`, `batch_key=""`

### 2d. `reduce_dimensions.py` — PCA + neighbor graph + UMAP
- **Input:** `AnnData` (with HVGs)
- **Output:** `AnnData` (with PCA, neighbors, UMAP embeddings)
- **Steps:**
  1. `sc.tl.pca()`
  2. `sc.pp.neighbors()`
  3. `sc.tl.umap()`
- **Key params:** `n_pcs=50`, `n_neighbors=15`

### 2e. `cluster.py` — Leiden clustering
- **Input:** `AnnData` (with neighbor graph)
- **Output:** `AnnData` (with cluster labels in `.obs`)
- **Steps:**
  1. `sc.tl.leiden(flavor="igraph", n_iterations=2, resolution=resolution)`
- **Key params:** `resolution=0.5`, `key_added="leiden"`

### 2f. `find_markers.py` — Differential gene expression
- **Input:** `AnnData` (clustered)
- **Output:** `AnnData` (with ranked genes in `.uns`)
- **Steps:**
  1. `sc.tl.rank_genes_groups(groupby=groupby, method="wilcoxon", layer="counts")`
- **Key params:** `groupby="leiden"`, `method="wilcoxon"`

---

## Step 3: Workflow — `src/stargazer/workflows/scrna_clustering.py`

```python
@scrna_env.task
async def scrna_clustering_pipeline(
    sample_id: str,
    organism: str = "human",
    n_top_genes: int = 2000,
    resolution: float = 0.5,
    max_pct_mt: float = 20.0,
) -> AnnData:
    """End-to-end scRNA-seq clustering pipeline."""
    raw = (await assemble(sample_id=sample_id, asset="anndata", stage="raw"))[0]
    filtered = await qc_filter(adata=raw, max_pct_mt=max_pct_mt, organism=organism)
    normalized = await normalize(adata=filtered)
    featured = await select_features(adata=normalized, n_top_genes=n_top_genes)
    reduced = await reduce_dimensions(adata=featured)
    clustered = await cluster(adata=reduced, resolution=resolution)
    annotated = await find_markers(adata=clustered)
    return annotated
```

---

## Step 4: Tests

### 4a. Create test fixture: `tests/assets/scrna/`
- Generate a small synthetic .h5ad file (e.g. 200 cells x 500 genes) using `AnnData(np.random.poisson(1, (200, 500)))` in conftest or a setup script

### 4b. Unit tests: `tests/unit/test_scrna_tasks.py`
- Test each task independently with the synthetic fixture
- Verify output shape, expected `.obs`/`.var`/`.obsm` keys, stage metadata

### 4c. Integration test: `tests/integration/test_scrna_workflow.py`
- Run full pipeline on synthetic data
- Verify final output has cluster labels and ranked genes

---

## Step 5: Documentation

- Add `docs/architecture/scrna.md` with pipeline overview and type/task descriptions
- Add spec links to all new module docstrings

---

## Implementation Order

1. [ ] Types (`scrna.py` + register in `__init__.py`)
2. [ ] Config (`scrna_env` in `config.py`)
3. [ ] Test fixture (synthetic .h5ad)
4. [ ] Tasks one at a time with tests: qc_filter → normalize → select_features → reduce_dimensions → cluster → find_markers
5. [ ] Workflow + integration test
6. [ ] Docs

## Files to Create/Modify

| Action | Path |
|--------|------|
| Create | `src/stargazer/assets/scrna.py` |
| Modify | `src/stargazer/assets/__init__.py` |
| Modify | `src/stargazer/config.py` |
| Create | `src/stargazer/tasks/scrna/__init__.py` |
| Create | `src/stargazer/tasks/scrna/qc_filter.py` |
| Create | `src/stargazer/tasks/scrna/normalize.py` |
| Create | `src/stargazer/tasks/scrna/select_features.py` |
| Create | `src/stargazer/tasks/scrna/reduce_dimensions.py` |
| Create | `src/stargazer/tasks/scrna/cluster.py` |
| Create | `src/stargazer/tasks/scrna/find_markers.py` |
| Create | `src/stargazer/workflows/scrna_clustering.py` |
| Create | `tests/unit/test_scrna_tasks.py` |
| Create | `tests/integration/test_scrna_workflow.py` |
| Modify | `pyproject.toml` (add scanpy, anndata deps) |
| Create | `docs/architecture/scrna.md` |

## Verification

1. `uv sync` — deps install cleanly
2. `ruff check --fix src/stargazer/tasks/scrna/ src/stargazer/assets/scrna.py src/stargazer/workflows/scrna_clustering.py`
3. `pytest tests/unit/test_scrna_tasks.py -v` — all unit tests pass
4. `pytest tests/integration/test_scrna_workflow.py -v` — end-to-end pipeline completes
