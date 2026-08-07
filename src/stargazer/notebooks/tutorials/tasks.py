# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "stargazer",
# ]
#
# [tool.uv.sources]
# stargazer = { path = "/stargazer", editable = true }
# ///
"""
### Stargazer tasks tutorial.

Imports the `SampleSheet` asset from the assets tutorial and walks
through defining a single Flyte task that consumes it and where task
environments live. Running the task — locally and remotely — is saved
for the execution tutorial; composing it into a workflow is the one
before that.

`CohortSummary`, `tutorial_env`, and `summarize_cohort` are defined here
as marimo *reusable* top-level symbols, so the later tutorials import
these exact objects rather than redefining them — no drift.

spec: [docs/architecture/tasks.md](../../docs/architecture/tasks.md)
"""

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")

with app.setup:
    import csv
    import json
    from dataclasses import dataclass
    from pathlib import Path
    from typing import ClassVar

    import flyte
    import marimo as mo

    from stargazer.assets.asset import Asset
    from stargazer.notebooks.tutorials.assets import SampleSheet

    # Declared here (not in a cell) so the reusable task/asset below can
    # reference it and stay importable. No Flyte init happens in this
    # notebook — defining a task needs none, and running it is the
    # execution tutorial's job.
    tutorial_env = flyte.TaskEnvironment(
        name="tutorial",
        description="Lightweight env for the tutorial notebooks.",
        resources=flyte.Resources(cpu=1, memory="512Mi"),
    )


@app.cell
def _():
    """Intro."""
    mo.md(
        """
        # Stargazer Tasks

        We have a typed `SampleSheet` asset from the previous tutorial —
        **imported, not redefined**. Now we'll wrap a real piece of work
        around it.

        The plan:

        1. Import `SampleSheet`, add a `CohortSummary` output type.
        2. Define a Flyte task that consumes a `SampleSheet` and emits
           a `CohortSummary`.
        3. See where task environments live.

        This notebook only *defines* the task. Composing it into a
        workflow is the next tutorial (`workflows.py`); running it,
        locally and on a remote cluster, is the one after
        (`execution.py`).
        """
    )


@app.cell
def _():
    """Section 1 — recap the SampleSheet asset."""
    mo.md(
        """
        ## 1. Two assets: `SampleSheet` (input) and `CohortSummary` (output)

        `SampleSheet` is imported straight from the assets tutorial — the
        same registered class, no copy. Here we add a tiny
        `CohortSummary` output type. Notice `sample_sheet_cid` on the
        summary — that's the **companion pattern** from the asset
        tutorial in action: any `CohortSummary` knows which `SampleSheet`
        produced it, so `sheet.fetch()` will pull the summary alongside.
        """
    )


@app.class_definition
@dataclass
class CohortSummary(Asset):
    """JSON summary of a cohort: per-organism counts and totals."""

    _asset_key: ClassVar[str] = "cohort_summary"
    cohort_id: str = ""
    n_samples: int = 0
    n_organisms: int = 0
    sample_sheet_cid: str = ""


@app.cell
def _():
    """Section 2 — what is a task."""
    mo.md(
        """
        ## 2. What's a Flyte task?

        A Flyte task is the unit of work the orchestrator schedules.
        In Flyte v2 it's just a Python function — sync or async —
        attached to a `TaskEnvironment` via `@env.task`:

        ```python
        env = flyte.TaskEnvironment("tutorial")

        @env.task
        async def summarize_cohort(sheet: SampleSheet) -> CohortSummary:
            ...
        ```

        The things worth knowing for Stargazer:

        - **The environment defines the container.** Image, CPU,
          memory, env vars, secrets — all pinned by the
          `TaskEnvironment`. Every task attached to `scrna_env` runs
          in the same scanpy image; every task attached to `gatk_env`
          runs in the GATK image. New workload, new env.
        - **Typed signatures cross the wire.** Inputs and outputs are
          serialized through their declared types — for us that's
          `Asset` subclasses via `to_keyvalues()` / `from_keyvalues()`.
          A task called remotely receives the same typed object it
          would have received locally.
        - **Same function, two execution shapes.** `await fn(...)` runs
          it in-process; `flyte.with_runcontext(mode="local").run(...)`
          goes through the full Flyte machinery (caching, retries, typed
          I/O); `flyte.run(...)` ships it to a cluster. The execution
          tutorial demonstrates all three.
        - **Tasks can call tasks.** A "workflow" in v2 is just a task
          whose body awaits other tasks — that's the next tutorial.

        Full reference:
        [Flyte v2 — Core concepts: Tasks](https://www.union.ai/docs/v2/flyte/user-guide/core-concepts/tasks/).

        We declare a minimal `tutorial_env` in this notebook's setup
        block so it's importable — `summarize_cohort` is decorated with
        it, and the later tutorials import the same env.
        """
    )


@app.cell
def _():
    """Where task environments really live."""
    mo.md(
        """
        ### Where environments really live

        `tutorial_env` is a throwaway for the tutorials, but production
        environments live in `src/stargazer/config.py`. That's where
        `scrna_env` (the scanpy image) and `gatk_env` (the GATK image)
        are defined, each pinning its own `flyte.Image`, resources, and
        secrets. A real task is decorated with one of those —
        `@scrna_env.task`, `@gatk_env.task` — so it lands in the right
        container. New kind of workload → new env in `config.py`.
        """
    )


@app.cell
def _():
    """Section 3 — define a task."""
    mo.md(
        """
        ## 3. Define `summarize_cohort`

        The task does one job: read the CSV the `SampleSheet` points
        at, count rows and unique organisms, write a JSON file, and
        publish a `CohortSummary` asset that links back to its input.

        Notice the I/O shape:

        - **Input is typed**: `sheet: SampleSheet`. The task can't be
          called with the wrong kind of asset.
        - **Output is typed**: `-> CohortSummary`. Downstream tasks
          consuming the result get the same guarantee.
        - **Storage is automatic**: `await sheet.fetch()` materializes
          the CSV; `await summary.update(path=..., **fields)` uploads
          the new JSON and assigns its CID. No manual path juggling.

        Defining the task is all this notebook does — the next two
        tutorials feed it inputs and run it.
        """
    )


@app.function
@tutorial_env.task
async def summarize_cohort(sheet: SampleSheet) -> CohortSummary:
    """Count samples and unique organisms in a cohort sample sheet."""
    await sheet.fetch()

    with sheet.path.open() as fh:
        rows = list(csv.DictReader(fh))
    organisms = sorted({r["organism"] for r in rows if r.get("organism")})

    out_path = Path(sheet.path).parent / f"{sheet.cohort_id}_summary.json"
    out_path.write_text(
        json.dumps(
            {
                "cohort_id": sheet.cohort_id,
                "n_samples": len(rows),
                "organisms": organisms,
            },
            indent=2,
        )
    )

    summary = CohortSummary()
    await summary.update(
        path=out_path,
        cohort_id=sheet.cohort_id,
        n_samples=len(rows),
        n_organisms=len(organisms),
        sample_sheet_cid=sheet.cid,
    )
    return summary


@app.cell
def _():
    """Recap."""
    mo.md(
        """
        ## Recap

        | What you did | What you got |
        |--------------|--------------|
        | `@tutorial_env.task` on a function | A unit of work Flyte can run anywhere |
        | Typed `SampleSheet` in, `CohortSummary` out | Self-describing I/O, automatic storage |
        | `await summary.update(...)` inside the task | Upload + CID assignment in one call |

        ### → Next: `workflows.py`

        One task is the primitive. The next notebook **imports
        `summarize_cohort`** and composes it into a **workflow** — a task
        that awaits other tasks — fanning it out across many cohorts with
        `asyncio.gather`. After that, the Execution tutorial runs the
        whole thing locally and on a remote cluster.
        """
    )


if __name__ == "__main__":
    app.run()
