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
### Stargazer workflows tutorial.

Imports the `summarize_cohort` task from the tasks tutorial and composes
it into a fan-out workflow. In Flyte v2 a workflow is just a task that
calls other tasks, so fan-out is plain `asyncio.gather` over task calls.

`audit_cohorts` is defined here as a marimo *reusable* top-level symbol
(`with app.setup:` + `@app.function`), so the Execution tutorial runs
this exact workflow object — no copy, no drift.

spec: [docs/architecture/workflows.md](../../docs/architecture/workflows.md)
"""

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")

with app.setup:
    import asyncio
    import csv
    import tempfile
    from pathlib import Path

    import marimo as mo

    from stargazer.notebooks.tutorials.assets import SampleSheet
    from stargazer.notebooks.tutorials.tasks import (
        CohortSummary,
        summarize_cohort,
        tutorial_env,
    )


@app.cell
def _():
    """Intro."""
    mo.md(
        """
        # Stargazer Workflows

        This notebook builds on `tasks.py`. There we wrote a single
        task, `summarize_cohort`, that turns one `SampleSheet` into one
        `CohortSummary`. Here we **import** it and compose it into a
        **workflow** that processes many cohorts at once.

        The headline: in Flyte v2 there is **no `@workflow` decorator**.
        A workflow is just a task whose body awaits other tasks.

        `summarize_cohort` and the two asset types come straight from the
        earlier tutorials. Here we define the workflow, `audit_cohorts`,
        plus a small reusable input builder, `make_demo_sheets`. The
        Execution tutorial imports both and runs them locally and
        remotely.
        """
    )


@app.cell
def _():
    """Section 1 — recap what we import."""
    mo.md(
        """
        ## 1. What we import

        These come from the previous tutorials, not redefined:

        - `SampleSheet` (input) and `CohortSummary` (output) — the asset
          types from the assets tutorial.
        - `summarize_cohort` — the task from the tasks tutorial.

        Below we add the workflow that fans `summarize_cohort` out, plus
        `make_demo_sheets` — a small builder that uploads a few cohort
        `SampleSheet` assets to run it against.
        """
    )


@app.cell
def _():
    """Section 2 — define the workflow."""
    mo.md(
        """
        ## 2. Compose into a workflow

        A workflow is a task whose body orchestrates other tasks. Same
        `@tutorial_env.task` decorator — the difference is what's inside:
        instead of doing the work, it awaits `summarize_cohort` once per
        cohort and gathers the results.

        ```python
        @app.function
        @tutorial_env.task
        async def audit_cohorts(
            sheets: list[SampleSheet],
        ) -> list[CohortSummary]:
            return list(
                await asyncio.gather(*[summarize_cohort(s) for s in sheets])
            )
        ```

        `asyncio.gather` is the whole fan-out story. Run locally it's
        concurrent in-process; run on a cluster (the Execution tutorial)
        each `summarize_cohort` call becomes its own container,
        scheduled in parallel — **the same code, no changes.**
        """
    )


@app.function
@tutorial_env.task
async def audit_cohorts(sheets: list[SampleSheet]) -> list[CohortSummary]:
    """Summarize many cohorts in parallel and return all results."""
    return list(await asyncio.gather(*[summarize_cohort(s) for s in sheets]))


@app.function
async def make_demo_sheets() -> list[SampleSheet]:
    """Build and upload three small cohort `SampleSheet` assets.

    Reusable so the Execution tutorial imports the exact same inputs
    instead of rebuilding them.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="stargazer_demo_"))
    cohorts = [
        ("alpha", [("a1", "human"), ("a2", "human"), ("a3", "human")]),
        ("beta", [("b1", "human"), ("b2", "mouse"), ("b3", "mouse")]),
        ("gamma", [("g1", "human"), ("g2", "human")]),
    ]

    sheets = []
    for cohort_id, samples in cohorts:
        csv_path = tmpdir / f"{cohort_id}.csv"
        with csv_path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["sample_id", "organism"])
            writer.writerows(samples)
        sheet = SampleSheet()
        await sheet.update(
            path=csv_path,
            cohort_id=f"demo_{cohort_id}",
            n_samples=len(samples),
            organism="mixed",
        )
        sheets.append(sheet)
    return sheets


@app.cell
def _():
    """Recap and next."""
    mo.md(
        """
        ## Recap

        | What you did | What you got |
        |--------------|--------------|
        | Workflow = `@task` that awaits other tasks | Composition with no special syntax |
        | `asyncio.gather` over task calls | Parallel fan-out |
        | Imported task + assets, defined the workflow | One shared definition, no drift |

        This notebook only *defines* the workflow and its inputs —
        running them is the execution tutorial's job.

        ### → Next: `execution.py` (Execution)

        The Execution tutorial imports `audit_cohorts` and
        `make_demo_sheets` and runs the workflow **locally and then on a
        remote cluster with no code changes**, watching it execute
        through a live console URL.
        """
    )


if __name__ == "__main__":
    app.run()
