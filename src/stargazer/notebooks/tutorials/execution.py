# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "matplotlib",
#   "stargazer",
# ]
#
# [tool.uv.sources]
# stargazer = { path = "/stargazer", editable = true }
# ///
"""
### Stargazer execution tutorial.

Assumes the asset/task/workflow primitives from the earlier tutorials and
spends them on the headline lesson: running a workflow first locally,
then on a remote cluster with no code changes. It imports the very same
`audit_cohorts` workflow composed in `workflows.py` and runs it both ways,
charting the result.

spec: [docs/architecture/workflows.md](../../docs/architecture/workflows.md)
"""

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    """Imports and Flyte init."""
    import time

    import flyte
    import marimo as mo
    import matplotlib.pyplot as plt

    flyte.init_from_config()

    mo.md(
        """
        # Stargazer — Execution

        By now you know the primitives: **assets** (typed, content-
        addressed I/O), **tasks** (single units of work), and
        **workflows** (tasks that call other tasks).

        We take the `audit_cohorts` workflow you composed in the
        previous tutorial and run the *same workflow* two ways:

        1. **Locally**, in this notebook's own process (blocking).
        2. **Remotely**, submitted to the Flyte cluster, with a URL to
           watch the action as it executes.

        No code changes between the two — only the call site. That's the
        superpower.
        """
    )
    return flyte, mo, plt, time


@app.cell
def _(mo):
    """Section 1 — import the workflow."""
    mo.md(
        """
        ## 1. Import the Workflow

        Composing a workflow was the previous tutorial, so here we just
        **import** it. `audit_cohorts` (and the `make_demo_sheets` input
        builder) come straight from `workflows.py` — the exact objects
        you composed, not a copy. That's what keeps the lesson and the
        runnable code from drifting apart.
        """
    )


@app.cell
def _():
    """Import the reusable workflow and input builder from the workflows tutorial."""
    from stargazer.notebooks.tutorials.workflows import (
        audit_cohorts,
        make_demo_sheets,
    )

    return audit_cohorts, make_demo_sheets


@app.cell
async def _(make_demo_sheets, mo):
    """Section 2 — build the inputs."""
    sheets = await make_demo_sheets()

    mo.vstack(
        [
            mo.md(
                f"""
                ## 2. Build the Inputs

                `make_demo_sheets()` uploads **{len(sheets)}** small cohort
                `SampleSheet` assets — the workflow's inputs.
                """
            ),
            mo.ui.table(
                [
                    {"cohort_id": s.cohort_id, "n_samples": s.n_samples, "cid": s.cid}
                    for s in sheets
                ],
                selection=None,
            ),
        ]
    )
    return (sheets,)


@app.cell
def _(audit_cohorts, flyte, mo, sheets, time):
    """Section 3 — run audit_cohorts locally (blocking)."""
    with mo.status.spinner(title="Running audit_cohorts locally..."):
        _t0 = time.perf_counter()
        local_run = flyte.with_runcontext(mode="local").run(
            audit_cohorts, sheets=sheets
        )
        summaries_local = local_run.outputs()
        _elapsed_local = time.perf_counter() - _t0

    mo.md(
        f"""
        ## 3. Run Locally (Blocking)

        Awaiting `audit_cohorts(...)` directly would just execute it in
        this process with no Flyte run record.
        `flyte.with_runcontext(mode="local").run(...)` exercises the
        same code path as cluster execution — task serialization, cache
        lookups, typed I/O — but stays in-process. The call **blocks**
        until the whole DAG finishes.

        Got back **{len(summaries_local)}** `CohortSummary` results in
        `{_elapsed_local:.2f}s`.
        """
    )
    return (summaries_local,)


@app.cell
def _(audit_cohorts, flyte, mo, sheets, summaries_local, time):
    """Section 4 — submit audit_cohorts remotely; render URL before waiting."""
    _t0 = time.perf_counter()
    remote_run = flyte.run(audit_cohorts, sheets=sheets)

    # Flush the console URL to the cell output *before* blocking on wait()
    # — otherwise the URL would appear only after the run completes.
    mo.output.append(
        mo.md(
            f"""
            ## 4. Run Remotely (URL First, Then Wait)

            `flyte.run(audit_cohorts, ...)` submits the workflow to the
            cluster and returns a `Run` handle **immediately**. The
            `.url` deep-links into the Flyte console so you can watch
            each `summarize_cohort` fan out across containers.

            **Watch on console:** [{remote_run.url}]({remote_run.url})

            Waiting for completion below...
            """
        )
    )

    with mo.status.spinner(title="Waiting for remote run..."):
        remote_run.wait()
        summaries_remote = remote_run.outputs()
        _elapsed_remote = time.perf_counter() - _t0

    _local_cids = sorted(s.cid for s in summaries_local)
    _remote_cids = sorted(s.cid for s in summaries_remote)

    mo.output.append(
        mo.md(
            f"""
            **Finished in `{_elapsed_remote:.2f}s`.** Because outputs are
            content-addressed, the remote results carry the *same CIDs*
            as the local run:

            `local CIDs == remote CIDs` → **{_local_cids == _remote_cids}**
            """
        )
    )
    return (summaries_remote,)


@app.cell
def _(mo, plt, summaries_remote):
    """Section 5 — chart the per-cohort counts."""
    _summaries = sorted(summaries_remote, key=lambda s: s.cohort_id)
    _labels = [s.cohort_id.removeprefix("workflows_") for s in _summaries]
    _x = range(len(_summaries))

    _fig, _ax = plt.subplots(figsize=(7, 4))
    _ax.bar(
        [i - 0.2 for i in _x],
        [s.n_samples for s in _summaries],
        width=0.4,
        label="samples",
        color="#2196F3",
    )
    _ax.bar(
        [i + 0.2 for i in _x],
        [s.n_organisms for s in _summaries],
        width=0.4,
        label="organisms",
        color="#4CAF50",
    )
    _ax.set_xticks(list(_x))
    _ax.set_xticklabels(_labels)
    _ax.set_ylabel("count")
    _ax.set_title("Per-cohort summary (from the remote run)")
    _ax.legend()
    _fig.tight_layout()

    mo.vstack(
        [
            mo.md(
                """
                ## 5. Visualize the Result

                Same `CohortSummary` objects whether they came from the
                local or the remote run — here's the per-cohort sample
                and organism count the workflow produced.
                """
            ),
            _fig,
        ]
    )


if __name__ == "__main__":
    app.run()
