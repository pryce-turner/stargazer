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
### Workspace template — barebones notebook skeleton.

A choose-your-own-adventure scaffold for authoring your own notebook from
scratch: ingest a file, define an asset for it, process it with a task,
and fan that task out in a workflow. Each section is a TODO-style
template — pair with `assets.py` and `tasks.py` for the
why and the deeper patterns.

spec: [docs/architecture/notebook.md](../../docs/architecture/notebook.md)
"""

import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    """Imports and Flyte init."""
    import asyncio
    import tempfile
    from dataclasses import dataclass
    from pathlib import Path
    from typing import ClassVar

    import flyte
    import marimo as mo

    flyte.init_from_config()

    mo.md(
        """
        # Bring Your Own Data — Skeleton

        Five fillable sections: **asset → upload → task → workflow → run**.
        Defaults work as-is (your file round-trips through a no-op
        pipeline). Replace the TODOs to make it do real work.

        Reference: [`assets.py`](../tutorials/assets.py) for asset
        internals, [`tasks.py`](../tutorials/tasks.py) for tasks, and
        [`workflows.py`](../tutorials/workflows.py) for composition.
        """
    )
    return ClassVar, Path, asyncio, dataclass, flyte, mo, tempfile


@app.cell
def _(mo):
    """Section 1 — asset skeleton."""
    mo.md(
        """
        ## 1. Asset

        Describe one of *your* file types. Pick a unique `_asset_key`
        and add typed fields with defaults.
        """
    )


@app.cell
def _(ClassVar, dataclass):
    """Define your asset type."""
    from stargazer.assets.asset import Asset

    @dataclass
    class MyAsset(Asset):
        """TODO: describe your file type."""

        _asset_key: ClassVar[str] = "my_asset"  # TODO: rename
        # TODO: add typed metadata fields with defaults, e.g.:
        # sample_id: str = ""
        # n_records: int = 0
        # source: str = ""

    return Asset, MyAsset


@app.cell
def _(mo):
    """Section 2 — upload widget."""
    mo.md(
        """
        ## 2. Upload

        Drop a file below. See
        [`mo.ui.file`](https://docs.marimo.io/api/inputs/file/) for
        filetype filters, multi-file mode, and styling options.
        """
    )


@app.cell
def _(mo):
    """File upload widget."""
    file_input = mo.ui.file(
        # TODO: filetypes=[".csv", ".h5ad"], multiple=False, kind="area"
        label="Drop a file",
    )
    file_input
    return (file_input,)


@app.cell
async def _(MyAsset, Path, file_input, mo, tempfile):
    """Persist the upload as a MyAsset."""
    if not file_input.value:
        mo.stop(True, mo.md("⬆ Upload a file above to continue."))

    _f = file_input.value[0]
    _tmpdir = Path(tempfile.mkdtemp(prefix="stargazer_template_"))
    _path = _tmpdir / _f.name
    _path.write_bytes(_f.contents)

    my_asset = MyAsset()
    await my_asset.update(
        path=_path,
        # TODO: pass values for the fields you declared in §1, e.g.:
        # sample_id="example",
    )

    mo.md(
        f"""
        Uploaded **`{_f.name}`** as a `MyAsset`:

        - `cid` → `{my_asset.cid}`
        - `path` → `{my_asset.path}`
        """
    )
    return (my_asset,)


@app.cell
def _(mo):
    """Section 3 — task skeleton."""
    mo.md(
        """
        ## 3. Task

        One Flyte task that does one thing with your asset. The
        environment pins the container — swap image / resources for
        real workloads.
        """
    )


@app.cell
def _(flyte):
    """Task environment for your work."""
    my_env = flyte.TaskEnvironment(
        name="template",
        # TODO: image=flyte.Image.from_debian_base().with_pip_packages(...),
        resources=flyte.Resources(cpu=1, memory="512Mi"),
    )
    return (my_env,)


@app.cell
def _(MyAsset, my_env):
    """Define your task."""

    @my_env.task
    async def my_task(asset: MyAsset) -> MyAsset:
        """TODO: do work on `asset.path` and return a (new) asset."""
        await asset.fetch()
        # TODO: read asset.path, write a new file, then either:
        #   1. mutate + re-upload this asset, OR
        #   2. construct a NEW asset and `await new.update(path=..., **kw)`
        return asset

    return (my_task,)


@app.cell
def _(mo):
    """Section 4 — workflow skeleton."""
    mo.md(
        """
        ## 4. Workflow

        A workflow is just a task that calls other tasks. Fan out with
        `asyncio.gather`; chain tasks by awaiting them in sequence.
        """
    )


@app.cell
def _(MyAsset, asyncio, my_env, my_task):
    """Define your workflow."""

    @my_env.task
    async def my_workflow(assets: list[MyAsset]) -> list[MyAsset]:
        """TODO: orchestrate. Default = fan `my_task` out across inputs."""
        return list(await asyncio.gather(*[my_task(a) for a in assets]))

    return (my_workflow,)


@app.cell
def _(mo):
    """Section 5 — run it."""
    mo.md(
        """
        ## 5. Run

        `await` runs in-process. `flyte.run(my_workflow, assets=[...])`
        goes through the full Flyte runtime — and against a remote
        endpoint once `.flyte/config.yaml` points at one.
        """
    )


@app.cell
async def _(mo, my_asset, my_workflow):
    """Run the workflow on your uploaded asset."""
    results = await my_workflow([my_asset])
    mo.ui.table(
        [{"cid": _r.cid, "path": str(_r.path)} for _r in results],
        selection=None,
    )


if __name__ == "__main__":
    app.run()
