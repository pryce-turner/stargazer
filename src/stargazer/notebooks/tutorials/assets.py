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
### Stargazer asset and types system tutorial.

A guided walkthrough of why typed `Asset` subclasses exist, the three
layers each Asset has (cid, path, fields), defining a new asset class,
and the round-trip from Python object to storage and back.

`SampleSheet` is defined here as a marimo *reusable* top-level symbol
(`with app.setup:` + `@app.class_definition`) so the Tasks tutorial
imports this exact class rather than redefining it — one definition, no
drift across the tutorial sequence.

spec: [docs/architecture/types.md](../../docs/architecture/types.md)
"""

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")

with app.setup:
    from dataclasses import dataclass, fields
    from typing import ClassVar

    import marimo as mo

    from stargazer.assets.asset import Asset


@app.cell
def _():
    """Intro."""
    mo.md(
        """
        # Stargazer Asset System

        A walkthrough of *why* Stargazer wraps every file in a typed
        `Asset`, what an Asset actually is, and how to add a new one to
        the system. By the end of this notebook you'll have defined a
        `SampleSheet` asset, uploaded a real CSV through it, and
        rediscovered it via a metadata query.

        The `SampleSheet` you define here is importable — the next three
        tutorials reuse this very class.
        """
    )


@app.cell
def _():
    """Why typed assets exist."""
    mo.md(
        """
        ## Why typed assets?

        Classic bioinformatics pipelines pass file *paths* between
        functions:

        ```python
        bam = align(fastq_path)
        sorted_bam = sort(bam)
        vcf = call(sorted_bam, ref_path)
        ```

        Fine for one-off scripts; brittle the moment things grow:

        | Pain | Why a path can't help |
        |------|------------------------|
        | "Where did this come from?" | The path tells you *where it lives*, not *what produced it*. |
        | "Show me all alignments for sample X" | Folder conventions only — no querying. |
        | "Did I run this with duplicates marked?" | Lives in someone's notes, not the file. |
        | "Where are the companion files?" | A BAM and its `.bai` are conceptually one thing but two paths. |

        Stargazer's answer: every file is an `Asset` — a small
        dataclass that carries (a) the content's identity, (b) the
        local path once fetched, and (c) typed metadata fields. Tasks
        accept and return Assets, not strings. Metadata travels with
        the file in storage and the storage layer is queryable.
        """
    )


@app.cell
def _():
    """The three layers of an Asset."""
    mo.md(
        """
        ## 1. Anatomy of an Asset

        Every Asset has three layers:

        - **`cid`** — a content identifier. Same bytes anywhere → same
          CID. This is the asset's *identity*, independent of where it
          physically lives. Local CIDs look like `local_<md5>`; remote
          (Pinata/IPFS) ones are `bafy...` hashes.
        - **`path`** — the local filesystem path, set after `fetch()` or
          `update()`. Decoupled from `cid` so a workflow can refer to
          data before it has been downloaded.
        - **Typed dataclass fields** — `sample_id`, `organism`,
          `stage`, … whatever the asset type needs. These get
          serialized to a flat `dict[str, str]` for storage via
          `to_keyvalues()` and reconstituted via `from_keyvalues()`.

        Together these let workflows refer to data by *what it is and
        where it came from*, not by where it happens to sit on disk.
        """
    )


@app.cell
def _():
    """Aside — content addressing and IPFS."""
    mo.md(
        """
        ### Aside: content addressing and IPFS

        A CID is a hash of the bytes. Same bytes anywhere → same CID;
        different bytes → different CID. Always. Stargazer issues
        `local_<md5>` CIDs when running locally (indexed in TinyDB on
        your machine) and real **IPFS** hashes (`bafy…`) when
        `PINATA_JWT` is set — so an asset uploaded by you and by a
        colleague resolves to the same identity, fetchable from any IPFS
        node that holds the bytes.

        That tiny shift unlocks four things at once:

        - **Immutability** — a CID never points at "the latest" of
          anything. If the bytes change, the CID changes. No version
          drift hides under a stable name.
        - **Dedup** — re-uploading identical bytes is a no-op. Two
          workflows that produce the same intermediate share storage,
          and downstream tasks get a cache hit.
        - **Reproducibility** — record the input and output CIDs of a
          run once, and you can replay the pipeline on the *exact*
          same bytes months later regardless of where they're hosted.
        - **Location-independence** — a `Reference(cid="bafy…")`
          handle doesn't care whether the bytes live on your laptop,
          in Pinata, or on a stranger's IPFS node. `fetch()` resolves
          it the same way every time.

        This is why Stargazer assets carry `cid` separately from
        `path`: identity travels with the asset; the location is
        materialized lazily.
        """
    )


@app.cell
def _():
    """Inspect an existing Asset class to ground the abstraction."""
    from stargazer.assets.scrna import AnnData

    _rows = [
        {
            "field": _f.name,
            "type": getattr(_f.type, "__name__", str(_f.type)),
            "default": repr(_f.default),
        }
        for _f in fields(AnnData)
    ]
    mo.vstack(
        [
            mo.md(
                """
                ### A real example: `AnnData`

                Here is the scRNA-seq asset declaration as it lives in
                `src/stargazer/assets/scrna.py`:

                ```python
                @dataclass
                class AnnData(Asset):
                    _asset_key: ClassVar[str] = "anndata"
                    sample_id: str = ""
                    n_obs: int = 0
                    n_vars: int = 0
                    stage: str = ""
                    organism: str = ""
                    source_cid: str = ""
                ```

                And here are its fields as Python's `dataclasses` sees
                them — note that `cid` and `path` come from the `Asset`
                base class, while everything else is `AnnData`-specific:
                """
            ),
            mo.ui.table(_rows, selection=None),
        ]
    )


@app.cell
def _():
    """Section 2 — define a new asset."""
    mo.md(
        """
        ## 2. Define a new asset

        Time to add one. We'll create `SampleSheet` — a CSV mapping
        samples to per-sample metadata for a cohort. In real code this
        would live in `src/stargazer/assets/sample_sheet.py`; here we
        define it as a top-level class right in the notebook (the
        `@app.class_definition` decorator below) so the later tutorials
        can `import` it.

        The template is always the same:

        1. `@dataclass` subclass of `Asset`
        2. `_asset_key: ClassVar[str] = "<unique key>"`
        3. Typed fields with defaults

        Defaults are required because every field becomes optional in
        `from_keyvalues()` — older records may not have newer fields.
        """
    )


@app.class_definition
@dataclass
class SampleSheet(Asset):
    """CSV of per-sample metadata for a cohort."""

    _asset_key: ClassVar[str] = "sample_sheet"
    cohort_id: str = ""
    n_samples: int = 0
    organism: str = ""


@app.cell
def _():
    """Confirm the new asset registered itself."""
    from stargazer.assets import ASSET_REGISTRY

    _registered = SampleSheet._asset_key in ASSET_REGISTRY
    _resolved = ASSET_REGISTRY.get(SampleSheet._asset_key)
    mo.md(
        f"""
        Just by defining the class, `__init_subclass__` ran and
        registered it:

        - `SampleSheet._asset_key` → `"{SampleSheet._asset_key}"`
        - `"{SampleSheet._asset_key}" in ASSET_REGISTRY` → **{_registered}**
        - `ASSET_REGISTRY["{SampleSheet._asset_key}"]` → `{_resolved.__name__ if _resolved else None}`

        That registration is what lets `assemble()` rebuild the right
        Python type from a flat storage record later.
        """
    )


@app.cell
def _():
    """Section 3 — round-trip metadata."""
    mo.md(
        """
        ## 3. Round-trip the metadata

        Storage backends (TinyDB locally, Pinata remotely) only deal in
        flat `dict[str, str]`. `to_keyvalues()` is the boundary
        function: `str` fields pass through; everything else gets
        `json.dumps`'d. `from_keyvalues()` does the inverse.
        """
    )


@app.cell
def _():
    """Show to_keyvalues() output."""
    sheet = SampleSheet(cohort_id="demo_cohort", n_samples=12, organism="human")
    _kv = sheet.to_keyvalues()

    mo.vstack(
        [
            mo.md(
                """
                Construct an instance with normal Python types:

                ```python
                sheet = SampleSheet(
                    cohort_id="demo_cohort", n_samples=12, organism="human"
                )
                ```

                `sheet.to_keyvalues()` flattens it for storage. Notice
                `n_samples` (an `int`) becomes the JSON-encoded string
                `"12"`, while the strings pass through untouched:
                """
            ),
            mo.ui.table(
                [{"key": _k, "value": _v} for _k, _v in _kv.items()],
                selection=None,
            ),
        ]
    )
    return (sheet,)


@app.cell
def _(sheet):
    """Round-trip via from_keyvalues()."""
    _kv = sheet.to_keyvalues()
    rehydrated = SampleSheet.from_keyvalues(_kv)
    return (rehydrated,)


@app.cell
def _(rehydrated, sheet):
    """Confirm the round-trip preserves typed values."""
    mo.md(
        f"""
        Going the other direction:

        ```python
        rehydrated = SampleSheet.from_keyvalues(sheet.to_keyvalues())
        ```

        - `rehydrated.cohort_id` → `{rehydrated.cohort_id!r}` (str pass-through)
        - `rehydrated.n_samples` → `{rehydrated.n_samples!r}` ({type(rehydrated.n_samples).__name__}, decoded from JSON)
        - `rehydrated.organism` → `{rehydrated.organism!r}`
        - `rehydrated == sheet` → **{rehydrated == sheet}**

        The coercion happens at the storage boundary so callers always
        see real Python types.
        """
    )


@app.cell
def _():
    """Section 4 — persist to storage."""
    mo.md(
        """
        ## 4. Persist via `update()`

        `Asset.update(path, **kwargs)` is the canonical "write this to
        storage" call:

        1. Sets any field kwargs on the asset
        2. Sets `self.path` to the file you're publishing
        3. Hashes the file contents and uploads to the configured
           backend (local TinyDB by default, Pinata if `PINATA_JWT` is
           set)
        4. Sets `self.cid` to the resulting content ID

        We'll write a tiny CSV to a temp file and push it through.
        """
    )


@app.cell
async def _():
    """Write a CSV and upload it as a SampleSheet."""
    import csv
    import tempfile
    from pathlib import Path

    _tmpdir = Path(tempfile.mkdtemp(prefix="stargazer_assets_"))
    csv_path = _tmpdir / "demo_cohort.csv"
    with csv_path.open("w", newline="") as _fh:
        _w = csv.writer(_fh)
        _w.writerow(["sample_id", "organism", "tissue"])
        _w.writerow(["s1d1", "human", "PBMC"])
        _w.writerow(["s1d3", "human", "PBMC"])

    uploaded_sheet = SampleSheet()
    await uploaded_sheet.update(
        path=csv_path,
        cohort_id="assets_demo",
        n_samples=2,
        organism="human",
    )

    mo.md(
        f"""
        Wrote `{csv_path.name}` to a temp dir and called
        `update()`. The asset now has both an identity and a location:

        - `uploaded_sheet.cid` → `{uploaded_sheet.cid}`
        - `uploaded_sheet.path` → `{uploaded_sheet.path}`
        - `uploaded_sheet.cohort_id` → `{uploaded_sheet.cohort_id!r}`
        - `uploaded_sheet.n_samples` → `{uploaded_sheet.n_samples}`

        The CID prefix `local_` tells you this lives in TinyDB on this
        machine. With `PINATA_JWT` set, the same call would push to
        Pinata and return an IPFS-style hash instead.
        """
    )
    return (uploaded_sheet,)


@app.cell
def _():
    """Section 5 — discover via assemble()."""
    mo.md(
        """
        ## 5. Discover with `assemble()`

        `assemble(**filters)` queries storage by keyvalue filters,
        deduplicates by CID, and returns specialized subclass instances
        — the registry from earlier earns its keep here. No file path
        conventions, no folder scanning.
        """
    )


@app.cell
async def _(uploaded_sheet):
    """Query for our newly uploaded sheet."""
    from stargazer.assets.asset import assemble

    found = await assemble(asset="sample_sheet", cohort_id="assets_demo")
    _hit = next((_a for _a in found if _a.cid == uploaded_sheet.cid), None)

    mo.md(
        f"""
        ```python
        found = await assemble(
            asset="sample_sheet", cohort_id="assets_demo"
        )
        ```

        - Returned `{len(found)}` asset(s)
        - Each is a `{type(_hit).__name__ if _hit else "—"}` (note: a
          real subclass, not a generic `Asset`) — that's `specialize()`
          using the registry to pick the right Python class.
        - `found[0].cid == uploaded_sheet.cid` → **{_hit is not None}**
        - `isinstance(found[0], SampleSheet)` → **{isinstance(_hit, SampleSheet) if _hit else False}**

        From this point a downstream task can take the `SampleSheet`
        as a typed argument, call `await sheet.fetch()` to pull the
        bytes, and read `sheet.path`.
        """
    )


@app.cell
def _():
    """Section 6 — companion pattern."""
    mo.md(
        """
        ## 6. The companion pattern

        Many bioinformatics files come in pairs: BAM + BAI, FASTA +
        FAI, BED + index. Stargazer handles this with a convention:

        > A companion asset stores `<parent_asset_key>_cid` pointing at
        > the parent's CID. `parent.fetch()` queries for assets where
        > that key matches and downloads them too.

        Real example from `src/stargazer/assets/reference.py`:

        ```python
        @dataclass
        class Reference(Asset):
            _asset_key: ClassVar[str] = "reference"
            build: str = ""

        @dataclass
        class ReferenceIndex(Asset):
            _asset_key: ClassVar[str] = "reference_index"
            build: str = ""
            tool: str = ""
            reference_cid: str = ""   # ← link back to the Reference
        ```

        When you call `await ref.fetch()`, the base implementation
        does:

        1. Download the FASTA itself
        2. `assemble(reference_cid=ref.cid)` to find any companions
        3. Download every match alongside

        So the BAI lands next to the BAM, the FAI next to the FASTA,
        without each task having to know about the pairing.

        For our `SampleSheet` we don't need a companion (it's a single
        file), but the `CohortSummary` in the next tutorial carries
        `sample_sheet_cid` and rides along automatically.
        """
    )


@app.cell
def _():
    """Section 7 — why the indirection is worth it."""
    mo.md(
        """
        ## 7. "Isn't this overkill for a local filesystem?"

        Honest question. On a single laptop with a fixed directory, you
        could glob the right folder and read the file. The machinery
        earns its keep the moment compute stops being local and
        persistent — which is exactly what Flyte does to every task.

        Each task runs in its own Docker container, and those containers
        are scattered **geographically** (no shared filesystem with the
        task that produced their inputs) and **temporally** (a retry, a
        re-run a year later, or a parameter sweep all land on fresh
        containers with empty disks). There *is* no shared path. The
        only way a container finds its inputs is to ask the storage
        layer "give me the asset with these properties" — `assemble()` —
        and the only way the BAI lands next to the BAM everywhere is the
        companion convention.

        The local TinyDB mode you're using now is the same API with a
        one-machine backend. It looks like ceremony when you're alone on
        your laptop, but the **Execution** tutorial shows it pay off:
        the identical code running on a remote cluster, inputs resolved
        by CID with no path coordination at all.
        """
    )


@app.cell
def _():
    """Section 8 — recap."""
    mo.md(
        """
        ## Recap

        | Concept | What it gives you |
        |---------|-------------------|
        | `Asset` base + `_asset_key` | A typed, registered, queryable file |
        | `cid` vs `path` | Identity vs location, decoupled |
        | `to_keyvalues()` / `from_keyvalues()` | Round-trip across a flat-string storage boundary |
        | `update(path, **kwargs)` | Single call: set fields, upload, get a CID |
        | `assemble(**filters)` | Discovery by metadata, not folders |
        | Companion pattern via `<key>_cid` | Index/data pairs travel together |

        That's the whole system.

        ### → Next: `tasks.py`

        The follow-on notebook **imports the `SampleSheet` you defined
        here** and wraps a Flyte task around it. From there the arc is:

        1. **Tasks** — one task, run locally two ways.
        2. **Workflows** — compose tasks into a fan-out workflow.
        3. **Execution** — the same code on a remote cluster, no changes.

        Each tutorial imports the previous one's objects, so the whole
        sequence runs on a single shared `SampleSheet`. Open `tasks.py`
        next.
        """
    )


if __name__ == "__main__":
    app.run()
