# 23 — Notebook-Declared Pod Image (`main_img`)

Let a notebook declare the image its own pod runs on, as a `flyte.Image`
expression in the setup block:

```python
with app.setup:
    from stargazer.notebooks import notebook_base

    main_img = (
        notebook_base
        .with_apt_packages("bcftools")
        .with_commands(["micromamba install -y -c bioconda vcftools"])
    )
```

`/launch` reads it out of the notebook source, replays it onto the base
image, builds it, and spawns the per-notebook pod on the result instead of
the static `notebook-app:latest`.

**Why.** Today a notebook's environment is configured through the
`[tool.stargazer]` TOML table plus the dashboard settings modal. Every new
knob costs a form field, a parser function, a writer branch in
`with_stargazer_resources`, and a template row. `main_img` replaces that
growth curve with one Python object that already has the whole `flyte.Image`
API behind it — apt packages, conda tools, CLI binaries, env vars — with no
UI work per knob.

**Prerequisite: a remote image builder.** The admin pod cannot build images
with the local (Docker) builder — see the docstring on
`_build_and_push_notebook_image` in `app/admin_app.py`. This plan assumes
`image.builder: remote` is configured and `flyte.build()` works from inside
the admin pod. Verify that before starting (step 0); without it, none of
Piece 2 onward can land.

## Scope

**In:** workspace notebooks. Their source is already fetched into the admin
at launch time for resource parsing (`app/admin_app.py`, the
`section in ("workspace", "snapshots")` branch), so the read costs no new
I/O.

**Deferred:** image-baked tutorials and workflows notebooks. `/launch` uses
their `path_in_image` directly and never reads their source, so letting them
declare `main_img` needs a new read — and their content is fixed at image
build time anyway, so a deploy-time pre-parse is the better shape when it's
wanted. Snapshots follow workspace for free (same fetch path) but are
frozen records; decide in Piece 3 whether a snapshot rebuilds its image or
pins the URI it was snapshotted with.

**Explicitly out:** replacing `[tool.stargazer]` cpu/memory. Those are pod
spec, not image, and Flyte wants them as `flyte.Resources` on the
AppEnvironment. The `main_resources` companion is Piece 5, optional.

## Piece 0 — Verify the builder

- [ ] Confirm `image.builder` is `remote` in the cluster config the admin
      pod initializes against.
- [ ] From an admin pod shell (or a task pod), run `flyte.build()` on a
      trivial derived image and confirm it returns a URI with no local
      Docker.
- [ ] Confirm the registry credentials available to the admin pod allow
      push, and that the cluster can pull what the builder produces.
- [ ] Check whether `flyte.build(image, force=False)` short-circuits when
      the content-hashed tag already exists in the registry. If it does,
      Piece 3's caching is nearly free; if it doesn't, Piece 3 adds an
      explicit manifest probe.

Record findings in `.opencode/reference/devbox_workarounds.md` if any of
this is devbox-specific.

## Piece 1 — Static parse of `main_img`

New `app/notebook_image.py`, deliberately a sibling of
`app/notebook_meta.py` and holding the same contract: **pure, framework-free,
never executes notebook code, never raises.** `/launch` runs inside the
admin process, which holds `SESSION_SECRET` and the GitHub App private key —
`exec`ing user notebook source there is off the table permanently, not just
for now.

Marimo files are valid Python, so `ast` is enough.

```python
@dataclass(frozen=True)
class ImageStep:
    """One whitelisted `flyte.Image` method call, with literal arguments."""
    method: str
    args: tuple
    kwargs: dict


_ALLOWED_METHODS = frozenset({
    "with_apt_packages",
    "with_pip_packages",
    "with_commands",
    "with_env_vars",
    "with_workdir",
})

_BASE_NAME = "notebook_base"


def parse_main_img(source: str) -> list[ImageStep] | None:
    """Return `main_img`'s method chain as replayable steps, or None."""
```

Rules:

- Find a module-level `main_img = <expr>` assignment. Marimo puts the setup
  block at module level in the generated file, so a plain `ast.walk` over
  top-level nodes finds it; cells are function bodies and are ignored by
  construction.
- The expression must be a chain of `Call` nodes bottoming out at the bare
  `Name` `notebook_base`. Anything else → `None`.
- Every method in the chain must be in `_ALLOWED_METHODS` → else `None`.
- Every argument must be `ast.literal_eval`-able → else `None`.
- Any failure returns `None` and the caller falls back to the base image,
  matching `notebook_meta`'s never-raise-at-launch behavior.

**Rooting the chain at `notebook_base` is load-bearing for correctness**, not
security — it's the user's own pod. The pod must still carry `sg_proxy`,
`terminal_overlay.html`, `launch-notebook.sh`, marimo, uv, and `/stargazer`.
A hand-rolled `Image.from_debian_base()` produces a pod that cannot boot.

`with_source_file`, `with_source_folder`, and `with_uv_project` stay off the
whitelist: they reference deployer-host paths that do not exist in the admin
pod.

Returning steps rather than a built `Image` keeps this module pure exactly
like `NotebookResources`, and leaves the replay to `app/per_notebook.py` —
same split the resources path already uses.

### Tests first

`tests/unit/test_notebook_image.py`, all pure string-in / value-out:

- [ ] Absent `main_img` → `None`.
- [ ] Bare `main_img = notebook_base` → `[]` (valid, no steps).
- [ ] Single and multi-call chains parse to the right steps in order.
- [ ] kwargs parse (`with_env_vars({"FOO": "bar"})`).
- [ ] Chain rooted at something else (`flyte.Image.from_debian_base()`) →
      `None`.
- [ ] Non-whitelisted method in an otherwise valid chain → `None`.
- [ ] Non-literal argument (a name, an f-string with a variable, a call) →
      `None`.
- [ ] Syntactically invalid source → `None`, no raise.
- [ ] An assignment named `main_img` *inside a cell function* is ignored.

Pause here for review before implementing — the whitelist and the
fall-back-vs-fail choice are the decisions worth confirming.

## Piece 2 — Replay, build, serve

### `notebook_base` must import in two places

The notebook kernel needs it (the setup block executes) and the admin needs
the same object to replay onto. The kernel runs inside the `marimo --sandbox`
venv where `stargazer` is installed editable from `/stargazer`, so the symbol
belongs SDK-side — a notebook importing `app.per_notebook` would drag in
`flyte.app` and `flyteidl2` for one URI.

- [ ] Add `stargazer.notebooks.notebook_base`: reads `STARGAZER_NOTEBOOK_IMAGE`
      from the environment and returns `flyte.Image.from_base(...)`.
- [ ] Inject `STARGAZER_NOTEBOOK_IMAGE=NOTEBOOK_IMAGE_URI` into the
      per-notebook `env_vars` in `per_notebook_env`.

This keeps the app tier the authority on the URI (it sets the var) while the
import stays SDK-side. The admin replays onto its own `notebook_app_img`
directly, so the two agree by construction.

### Replay and build

In `app/per_notebook.py`:

- [ ] `replay_image_steps(steps) -> flyte.Image` — fold the steps onto
      `notebook_app_img` via `getattr`.
- [ ] `per_notebook_env` grows an `image_steps: list[ImageStep] | None`
      parameter; `None` (and `[]`) keeps today's static `notebook_app_img`
      with no build call at all, so the common path is unchanged.

In `app/admin_app.py`'s `/launch`:

- [ ] Parse `main_img` from the already-fetched workspace source alongside
      `parse_notebook_resources`.
- [ ] If steps are present and non-empty, resolve the image and ensure it's
      built (Piece 3), then pass the resulting URI through to the
      AppEnvironment.

### Failure policy

Two different failures, two different answers:

- **Unparseable `main_img`** → fall back to the base image, log a warning.
  The notebook still opens; the user sees their extras missing.
- **Build failure** → do *not* silently fall back. Surface the error on the
  tile and refuse the launch. A pod quietly missing the tools the notebook
  declares is worse than a clear error.

## Piece 3 — Build caching and async launch UX

`flyte.Image.uri` is content-hashed by the SDK, so an unchanged setup block
resolves to the same URI and an unchanged notebook never rebuilds. Two users
declaring the same extras share one image — the tag hashes the *recipe*, not
the user.

- [ ] If Piece 0 showed `flyte.build(force=False)` is not already a cheap
      no-op on an existing tag, add a registry manifest probe before
      building.
- [ ] Kick the build with `flyte.build(image, wait=False)` and return a
      `building` state from `/launch` rather than blocking the form POST for
      minutes.
- [ ] Report that state through `/launch/status` — the dashboard already
      polls it, so this is a new status value plus a tile treatment
      ("Building environment…"), not new plumbing.
- [ ] Decide snapshot behavior: pin the image URI recorded at snapshot time
      (correct for a frozen record, and a real step toward roadmap item 16's
      bit-for-bit goal) versus re-resolving from source. Pinning is the
      recommendation.

Note that the `:latest` mutable-tag trick in `per_notebook.py` exists only
because non-`latest` tags default to `imagePullPolicy: IfNotPresent` and
devbox nodes cache aggressively. Content-hashed URIs are unique per recipe,
so a changed recipe is a changed digest and pulls correctly — the comment on
`NOTEBOOK_IMAGE_TAG` already anticipates this dropping out under a remote
builder. Confirm the base image itself still updates as expected.

## Piece 4 — Document the boundary

PEP 723 deps and `main_img` pip packages will overlap, and people will pick
wrong without an explicit rule:

- **PEP 723 header** → the kernel venv. `marimo --sandbox` resolves it at pod
  boot, no image rebuild. This is where Python deps belong and where
  iteration should stay fast.
- **`main_img`** → system level. apt packages, bioconda tools, CLI binaries.
  Changing it triggers a rebuild.

- [ ] State that rule in `docs/architecture/notebook.md`, in product terms.
- [ ] Update `docs/architecture/app.md` (launch path now resolves an image).
- [ ] Update `.opencode/reference/architecture/app_internals.md` with the
      parser/replay/build mechanics.
- [ ] Refresh affected module docstrings — `app/per_notebook.py`'s module
      docstring currently states the image is shared and static across every
      per-notebook pod, which this change falsifies.
- [ ] No new doc files, so `zensical.toml`'s `nav` needs no edit. Confirm.

## Piece 5 — `main_resources` companion (optional)

The natural follow-on that actually retires the settings modal:

```python
with app.setup:
    main_resources = flyte.Resources(cpu=4, memory="8Gi")
```

A single literal call, so the Piece 1 parser handles it with a near-identical
code path. Only worth doing once `main_img` has proven itself in real use —
`[tool.stargazer]` cpu/memory works today and the modal is already built.

## Open risk

`with_commands` is arbitrary shell executed on shared build infrastructure.
That is fine while you are the only tenant, and it is genuinely the point of
the feature. But before builds run from arbitrary user forks it needs a real
answer — whether that's dropping `with_commands` from the whitelist for
non-trusted forks, a build-time sandbox, or gating on fork ownership. Worth
writing down now rather than rediscovering it under pressure.
