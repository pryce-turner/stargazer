# scRNA Preprocessing Tutorial Notebook Rebuild

Replace `src/stargazer/notebooks/tutorials/scrna_tutorial.py` with a new file `src/stargazer/notebooks/tutorials/preprocessing_tutorial.py` — a progressive walkthrough that introduces Stargazer's primitives in the order a new user encounters them: **Asset → Task → Workflow → Local execution → Remote execution**. Scope is narrowed to **preprocessing only** (qc_filter → normalize → select_features → reduce_dimensions) so the local-vs-remote contrast is the headline lesson.

The old `scrna_tutorial.py` is deleted in the same change; the follow-up "clustering tutorial" gets its own new file when written.

## Out of scope (deferred to a follow-up tutorial)

- `cluster` / `find_markers` and the resolution slider
- The two-workflow ("expensive vs cheap stage") caching argument
- Multi-sample fan-out with `asyncio.gather`
- Side-by-side UMAP comparison across samples
- `TaskRegistry` catalog cell

These are real selling points but each one is a separate concept; bundling them with the local→remote story has been confusing readers. They get their own tutorial later.

## Target narrative — cell-by-cell

### Cell 1 — Imports + Flyte init

- Imports: `marimo`, `flyte`, `matplotlib`, `numpy`, `scanpy`.
- `flyte.init_in_cluster(project=os.environ["FLYTE_PROJECT"], domain=os.environ["FLYTE_DOMAIN"])` (not `init_from_config()` — the notebook is running in a pod; see [[flyte_docs_project_create_wrong]] for why this matters).
- Title + one-paragraph orientation: "this notebook builds a 4-stage scRNA-seq pipeline out of Stargazer primitives, then runs it both ways."

### Cell 2 — What is an Asset?

- Pure-markdown explainer:
  - Every input/output in Stargazer is an `Asset` subclass (`cid` + `path` + typed metadata).
  - `cid` is the content-addressed identifier; `path` is the local materialization.
  - `assemble(...)` looks up assets by metadata; `await asset.fetch()` materializes the file.
- Show `AnnData` definition (literal code snippet pulled from `src/stargazer/assets/scrna.py`) — `sample_id`, `n_obs`, `n_vars`, `stage`, `organism`, `source_cid`.

### Cell 3 — Fetch a single raw AnnData

- Hardcode `sample_id = "s1d1"`.
- `assemble(sample_id=sample_id, asset="anndata", stage="raw")`. On miss, call `fetch_bundle("scrna_demo")` and re-resolve.
- `await raw_asset.fetch()`, then `sc.read_h5ad(raw_asset.path)` for inspection.
- Returns: `raw_asset` (the `AnnData` instance), `raw_ad` (the in-memory scanpy object).

### Cell 4 — Raw QC histograms

- Keep the existing four-panel histogram cell, simplified to one sample.
- Pedagogical role: a visual "before" picture so the post-preprocess UMAP later is satisfying.

### Cell 5 — What is a Task?

- Markdown: a Stargazer task is an `async def` decorated with `@scrna_env.task`. Takes typed assets in, returns a typed asset out. The decorator handles caching, retries, resource scheduling, and serialization.
- Show `qc_filter` source inline (literal include from `src/stargazer/tasks/scrna/qc_filter.py`), then a one-line summary of the other three: `normalize`, `select_features`, `reduce_dimensions`.

### Cell 6 — Compose tasks into a workflow

- Define `preprocess` as a `@scrna_env.task` that awaits the four sub-tasks in sequence. Single-sample, no fan-out:

  ```python
  @scrna_env.task
  async def preprocess(raw: AnnData, max_pct_mt: float, n_top_genes: int) -> AnnData:
      filtered = await qc_filter(adata=raw, max_pct_mt=max_pct_mt)
      normalized = await normalize(adata=filtered)
      featured = await select_features(adata=normalized, n_top_genes=n_top_genes)
      return await reduce_dimensions(adata=featured)
  ```

- Markdown note: in v2, "workflows" are just tasks that call other tasks. No separate `@workflow` decorator. Caching is left at the default (drop `cache="disable"`) so the next cell shows a real second-call speedup.

### Cell 7 — Run locally (blocking)

- Markdown: "awaiting `preprocess(...)` directly would run it in this pod's process with no Flyte run record. To exercise the same call path as cluster execution but stay in-process, use `flyte.with_runcontext(mode='local').run`. This call **blocks** until the whole DAG finishes; outputs are available immediately."
- Code:

  ```python
  run = flyte.with_runcontext(mode="local").run(
      preprocess,
      raw=raw_asset,
      max_pct_mt=20.0,
      n_top_genes=2000,
  )
  reduced_local = run.outputs()
  ```

- Wrap in a `mo.status.spinner(title="Running locally...")` so the marimo UI shows progress while the call blocks.
- Print elapsed wall-clock and the resulting `AnnData` (n_obs, n_vars, stage).

### Cell 8 — Run remotely (URL first, then wait)

- Markdown: "same call, submitted to the cluster. `flyte.run` returns **immediately** with a `Run` handle whose `.url` deep-links into the Flyte console. We render the URL right away so you can watch the action execute, then call `run.wait()` to block until it finishes."
- Code:

  ```python
  remote_run = flyte.run(
      preprocess,
      raw=raw_asset,
      max_pct_mt=20.0,
      n_top_genes=2000,
  )
  mo.output.append(mo.md(f"[Watch on console]({remote_run.url})"))
  remote_run.wait()
  reduced_remote = remote_run.outputs()
  ```

- The `mo.output.append` is important: marimo cells normally render their final value only, so the URL would otherwise appear after the wait completes (defeating the purpose). Append-mode flushes the URL immediately while `run.wait()` blocks.
- Print elapsed wall-clock for comparison. Note that the result is content-identical to the local run (same `cid` if caching is on and inputs match).

### Cell 9 — Visualize the preprocessed UMAP

- `await reduced_remote.fetch()`, `sc.read_h5ad(...)`, plot the UMAP (`sc.pl.umap` or matplotlib scatter of `.obsm["X_umap"]`).
- One-paragraph wrap-up: "you've defined a typed asset, a workflow built from four tasks, and exercised it both locally and remotely. The next tutorial will add clustering, interactive parameter sweeps, and multi-sample fan-out."

## Open questions to resolve before writing

1. **Marimo + blocking SDK calls.** `flyte.with_runcontext(mode="local").run(...)` and `remote_run.wait()` are both synchronous and may block long enough to freeze the marimo UI thread. Two options: (a) keep the cell sync and trust marimo's worker model, (b) wrap each call in `asyncio.to_thread(...)` from an `async def` cell. Decide by trying option (a) first and falling back if the UI freezes.
2. **`FLYTE_PROJECT` / `FLYTE_DOMAIN` in the notebook pod env.** The admin app sets these on its own pod via `_FLYTE_CONTEXT`; need to verify they're also injected into the per-user `notebook_env` pods (check `app/provision.py` and `notebook_env.env_vars`). If not, cell 1's `init_in_cluster(project=..., domain=...)` will fail and we need to propagate them.

## Verification checklist

- [ ] Run the notebook end-to-end locally via `marimo edit src/stargazer/notebooks/tutorials/scrna_tutorial.py` against a connected devbox.
- [ ] Local cell completes in roughly the same time as the current `await preprocess(...)` cell.
- [ ] Remote cell produces a clickable console URL and returns an `AnnData` with non-zero `n_obs`.
- [ ] UMAP renders.
- [ ] Pre-commit (`ruff`, `docstr-coverage`) passes.
- [ ] `_NOTEBOOK_PATH_IN_IMAGE` in `app/notebook_app.py` updated to `src/stargazer/notebooks/tutorials/preprocessing_tutorial.py`.

## Files touched

- `src/stargazer/notebooks/tutorials/preprocessing_tutorial.py` — **new file**.
- `src/stargazer/notebooks/tutorials/scrna_tutorial.py` — **deleted**.
- `app/notebook_app.py` — update `_NOTEBOOK_PATH_IN_IMAGE` to point at the new file.
- Notebook image rebuild required before the next admin deploy (`_build_and_push_notebook_image`).
