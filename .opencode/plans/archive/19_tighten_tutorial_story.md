# 19 — Tighten the tutorial story

## Problem

The tutorial arc (`Assets → Tasks → Execution → scRNA-seq`) teaches its core
concepts two or three times each:

- **Workflow composition** appears in both `tasks_tutorial.py` §6 (`audit_cohorts`
  fan-out) and `preprocessing_tutorial.py` §5 (`preprocess` chain).
- **Local vs remote** appears in `tasks_tutorial.py` §7 (prose only — never
  actually runs remote) and `preprocessing_tutorial.py` §6–7 (the only place it
  runs for real).
- `preprocessing_tutorial.py` re-teaches "What is an Asset/Task/Workflow"
  (§1/§4/§5) that the first two notebooks already covered.

Goal: one job per notebook, no re-teaching. Not more notebooks — redistribute
what exists.

## Decisions (confirmed with user)

- **TaskEnvironment** stays inline in Tasks (no standalone notebook); add a short
  "where envs really live" pointer to `config.py`'s `scrna_env`/`gatk_env`.
- **Local-vs-remote demo** lives only in Execution, on the real scrna workload.
  Strip the prose-only remote section from Tasks.
- **Full restructure** in one coherent pass.

## Target arc

| # | slug | title | Owns | 
|---|------|-------|------|
| 1 | assets | Assets | typed I/O, cid/path, assemble, companion |
| 2 | tasks | Tasks | one task on SampleSheet; env inline; run locally two ways |
| 3 | workflows | Workflows | compose tasks; asyncio.gather fan-out (NEW file) |
| 4 | preprocessing | Execution | local-vs-remote superpower, for real |
| 5 | scrna-pipeline | scRNA-seq | unchanged showcase |

## Steps

- [x] 1. Create `tutorials/workflows_tutorial.py` — lift `tasks_tutorial.py` §6
  (`audit_cohorts`) into its own notebook. Re-define `SampleSheet`/`CohortSummary`
  + `tutorial_env` + `summarize_cohort` so it stands alone (continues the toy
  example). spec link → `docs/architecture/workflows.md`.
- [x] 2. Trim `tasks_tutorial.py` — drop §6 (workflow) and §7 (remote prose).
  Retitle to single-task focus. Add a short cell pointing at `scrna_env`/`gatk_env`
  in `config.py`. Update recap + "next" pointer to Workflows. Fix docstring.
- [x] 3. Strip `preprocessing_tutorial.py` re-teaching — collapse §1 (Asset), §4
  (Task), §5-narrative (Workflow) to one-liners that assume prior notebooks.
  Keep §6/§7 (local + remote, real run, identical CID) as the centerpiece.
- [x] 4. Trim `assets_tutorial.py` — shorten the IPFS aside and the §7
  "isn't this overkill" essay (ephemeral-compute argument moves toward Execution).
  Update the closing "next" recap to include Workflows in the chain.
- [x] 5. Register Workflows — add `Notebook(slug="workflows", ...)` between tasks
  and preprocessing in `app/notebooks.py::NOTEBOOKS`, and the matching
  `_NavEntry` in `src/stargazer/notebooks/__init__.py::NAV_ORDER`.
- [x] 6. `uv run pytest tests/notebooks/test_notebook_smoke.py` — smoke test
  auto-discovers the new file (import + parse + no multiply-defined vars). Run
  `ruff --fix`.

## Testing

## Follow-up (post-review)

- [x] scRNA-seq is a full workflow, not a tutorial — kept in the `workflows`
  dashboard section, out of the tutorial reading order.
- [x] Deleted the per-notebook nav bar (`nav_bar`/`NAV_ORDER`/helpers) from
  `notebooks/__init__.py` — navigation lives on the dashboard now.
- [x] Dropped the `_tutorial` suffix from all four files (`assets.py`,
  `tasks.py`, `workflows.py`, `preprocessing.py`) — implied by the `tutorials/`
  dir. Updated all cross-references (`app/notebooks.py`, the notebooks' own
  "next" pointers, `workspace/template.py`, `workflows/scrna_pipeline.py`).
- [x] Tutorial order is the reading sequence: assets → tasks → workflows →
  execution. Tile titles numbered ("1. Assets" … "4. Execution").
- [x] Renamed `preprocessing` → `execution` (slug + file) so the tutorial name
  describes the mechanic, not the scRNA application.
- [x] Composition moved out of `execution.py`: promoted the `preprocess` chain
  to the SDK (`stargazer/workflows/scrna_preprocessing.py`, default caching),
  registered it in `workflows/__init__.py::__all__` (+ test_registry), and the
  execution notebook now *imports* it and stays focused on local-vs-remote.
  Regenerated `docs/reference/{catalog,api}.md`.
- [x] Reworked to a single **toy** workflow through T3+T4 (no scanpy): `workflows.py`
  defines `SampleSheet`/`CohortSummary`/`summarize_cohort`/`audit_cohorts`/
  `make_demo_sheets` as marimo *reusable* top-level symbols (`with app.setup:` +
  `@app.function`/`@app.class_definition`), and `execution.py` imports
  `audit_cohorts`+`make_demo_sheets` from it — one definition, taught in T3 and
  run in T4 (local vs remote), charting per-cohort counts. No drift.
- [x] Removed the speculative SDK `scrna_preprocessing.preprocess` entirely — it
  was orphaned once execution imported the toy workflow, and `scrna_clustering_pipeline`
  already covers those stages. Reverted `workflows/__init__.py` + `test_registry`,
  regenerated `docs/reference/{catalog,api}.md`.
- [x] Made the whole sequence import (not redefine) shared objects — one definition
  each, no drift. Converted assets/tasks/workflows to the `with app.setup:` +
  `@app.function`/`@app.class_definition` pattern:
  - `assets.py` defines & exports `SampleSheet`.
  - `tasks.py` imports `SampleSheet`; defines & exports `CohortSummary`,
    `tutorial_env`, `summarize_cohort`, `make_demo_sheets` (shared input builder).
  - `workflows.py` imports those; defines & exports `audit_cohorts`.
  - `execution.py` imports `audit_cohorts` + `make_demo_sheets`.
  Asset ops don't need flyte, so assets has no init; tasks/workflows keep
  `flyte.init_from_config()` in a regular cell (not setup) so transitive imports
  have no side effects. Verified end-to-end run, not just import.
- [x] Removed "toy" language across the tutorials (first point of contact for
  serious software).
- [x] tasks.py and workflows.py are now purely definitional — no Flyte init, no
  run cells (workflows dropped `import flyte` entirely). All task/workflow
  execution lives in the execution tutorial. `make_demo_sheets` lives in workflows
  (defined, not run). Final shape: assets = define + storage demo, tasks = define
  task, workflows = define workflow + inputs, execution = run (local + remote).

## Testing

The smoke test (`tests/notebooks/test_notebook_smoke.py`) covers every notebook
generically via `rglob`: imports cleanly, all cells parse, no multiply-defined
reactive vars. The new Workflows notebook is covered automatically. No bespoke
tests needed.
