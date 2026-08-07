# Per-Notebook Apps + Marimo Sandbox Inline Deps

Layer 2 of the **two-app** architecture. Layer 1 (admin login + dashboard) is in [`16_admin_app_with_dashboard.md`](./16_admin_app_with_dashboard.md). This plan assumes that plan has shipped — the admin app's `POST /launch?slug=…&mode=…` handler is the entry point here.

## Goal

When the user clicks **Edit** or **Run** on any tile in the admin app's dashboard, the admin pod:

1. Resolves `(slug, mode, section)` to a notebook source.
2. Spawns a **per-notebook app** in the user's Flyte project (a `flyte.app.AppEnvironment` deployed via `serve.aio`), idempotent per `(user_project, slug, mode)`.
3. Redirects the user to that app's public URL.

Each per-notebook app uses a **shared static image** with `uv`, `marimo`, and system bioinformatics tools — but **no Python project deps**. The image is built programmatically by `app.per_notebook.notebook_app_img` (a `flyte.Image` named `notebook-app`); `flyte.build(notebook_app_img)` is called once at admin-app deploy time so it's pushed to `STARGAZER_REGISTRY` before any user clicks Edit/Run. Python deps are declared in the notebook's PEP 723 inline metadata and provisioned at boot by `marimo {edit|run} --sandbox <path>`. Per [`reference/marimo/inlining_dependencies.md`](../reference/marimo/inlining_dependencies.md).

## Architecture

```
                    Admin POST /launch?slug=…&mode=…
                                   │
                                   ▼
              ┌──────────────────────────────────┐
              │ Admin /launch handler            │
              │  - resolve (slug, mode, section) │
              │  - build per-notebook env spec   │
              │  - flyte.with_servecontext(      │
              │       project=sg-<user>          │
              │    ).serve.aio(env)              │
              │  - redirect → app.endpoint       │
              └────────────────┬─────────────────┘
                               │  one per (user × slug × mode)
                               ▼
       ┌─────────────────────────────────────────────────┐
       │ Per-notebook app                                │
       │  - shared base image: uv + marimo + sys tools   │
       │  - launch script: clone fork → marimo + proxy   │
       │  - sandbox venv built from notebook PEP 723     │
       │  - SIGTERM hook → push edits back to fork       │
       └─────────────────────────────────────────────────┘
```

### Shared image (programmatic `flyte.Image`)

Defined in `app/per_notebook.py` as `notebook_app_img` (image name `notebook-app`). Built and pushed to `STARGAZER_REGISTRY` by `flyte.build(notebook_app_img)` from the admin-app deploy entrypoint. Contents (layered onto `flyte.Image.from_debian_base`):
- `ca-certificates`, `curl`, `git`, `bzip2`.
- `micromamba` + bioconda tools: BWA, BWA-MEM2, samtools, GATK4 — system-level so subprocess calls from inside each notebook's sandbox venv can reach them.
- `uv` and `uvx` (marimo `--sandbox` needs uv to provision per-notebook venvs).
- `marimo` installed at the system level (so the entrypoint can launch it; the `--sandbox` venv it spawns is separate).
- Web deps for the proxy + uvicorn (`fastapi`, `uvicorn`, `itsdangerous`, `httpx`, `websockets`).
- `app/proxy.py` baked at `/usr/local/lib/app/proxy.py`; `app/launch-notebook.sh` baked at `/usr/local/bin/launch-notebook.sh`.
- **No `with_uv_project()`** — the stargazer package is NOT installed into the system Python. Each notebook's PEP 723 metadata declares stargazer as a dep.

The local Dockerfile `note` target is now a thin marimo-only image for `docker run` exploration; it does NOT back the hosted apps.

The hosted image is a one-time build per upstream change (system tools rarely move); Python dep churn happens inside per-notebook sandbox venvs, no image rebuild needed.

### App spec factory

`app/per_notebook.py`:

```python
def per_notebook_env(
    *,
    slug: str,
    mode: Literal["edit", "run"],
    notebook_path: str,
    fork_owner: str,
    github_token: str,
    session_secret: str,
) -> flyte.app.AppEnvironment:
    """Build the AppEnvironment for one (slug, mode) launch."""
    return flyte.app.AppEnvironment(
        name=f"nb-{slug}-{mode}",  # deterministic → Knative dedupes
        image=_NOTEBOOK_IMAGE,
        args=["/usr/local/bin/launch-notebook.sh", mode, notebook_path],
        port=8080,
        requires_auth=False,  # gate sits in front via the cookie-validating proxy
        resources=flyte.Resources(memory=("2Gi", "6Gi")),
        env_vars={
            **STARGAZER_ENV_VARS,
            "FLYTE_DOMAIN": "development",
            "FORK_OWNER": fork_owner,
            "GITHUB_TOKEN": github_token,
            "SESSION_SECRET": session_secret,
        },
    )
```

No `pod_template=` — Flyte v2's server rejects pod templates on AppEnvironments (`"K8sPod app payload is not yet supported"`). The launch script clones the user's fork into the container-local `/workspace` directory on startup; the proxy serves `/__sg__/workspace/{list,sync}` against that local directory.

For `tutorials/` and `community/` notebooks, `notebook_path` is the in-image path (e.g. `/stargazer/src/stargazer/notebooks/tutorials/preprocessing_tutorial.py`). For `workspace/` notebooks, it's `/workspace/src/stargazer/notebooks/workspace/<file>.py` — populated by the launch script's clone.

The admin app's `/launch` handler calls `flyte.with_servecontext(project=user_project, domain=...).serve.aio(env)`, mutating `env.env_vars["FLYTE_PROJECT"] = user_project` first.

### Knative naming + idempotency

`name=f"nb-{slug}-{mode}"` is intentionally NOT user-prefixed — isolation comes from the user's Flyte project / k8s namespace. Two users hitting Edit on `preprocessing` each get an `nb-preprocessing-edit` app, but in their own `sg-<user>` projects, so the Knative resources are separate. Re-clicking from the same user is idempotent: Knative returns the existing revision.

### Workspace via per-pod clone-on-startup (no PVC)

Flyte v2's app deployment rejects pod templates today (`"K8sPod app payload is not yet supported"` from the server), so we can't mount a shared PVC across the dashboard and per-notebook pods. Workspace state lives instead in each per-notebook pod's ephemeral container storage; the user's GitHub fork is the source of truth.

The launch script clones on startup:

```bash
if [ ! -d /workspace/.git ]; then
  git clone --depth 1 \
    "https://x-access-token:$GITHUB_TOKEN@github.com/$FORK_OWNER/stargazer.git" \
    /workspace
fi
```

`FORK_OWNER` + `GITHUB_TOKEN` are baked into the per-notebook AppEnvironment's `env_vars` by the factory in `app/per_notebook.py`. A SIGTERM handler in the launch script issues `POST 127.0.0.1:8080/__sg__/workspace/sync` (the proxy's own route) before exit, so workspace edits flush to the fork when Knative idles the pod.

**Tradeoff:** two simultaneous per-notebook pods for the same user have independent clones and can diverge until one pushes and the other re-clones on a fresh wake. Single-pod sessions are fine. Worth revisiting once Flyte v2 ships pod-template support on apps — then we can go back to the shared-PVC design.

### Auth gate + workspace endpoints

The admin app issues a signed session cookie keyed by `SESSION_SECRET`. Per-notebook apps host marimo directly — no native FastAPI hook for cookie validation. Resolution: a small reverse-proxy entrypoint (`app/proxy.py`, ~120 lines) baked into the per-notebook image on port 8080. It does three things:

1. Validates the signed session cookie against the same `SESSION_SECRET` the admin app uses (baked into env_vars per pod). Drive-by requests get a 401.
2. Forwards everything else — HTTP and websockets — to marimo on `127.0.0.1:8081`.
3. Reserves two paths it handles itself rather than forwarding:
   - `GET /__sg__/workspace/list` — directory listing off the container-local `/workspace` checkout.
   - `POST /__sg__/workspace/sync` — `git add` + commit + push to the user's fork. The route bypasses the cookie check when called with `X-Sg-Reason: notebook-shutdown` (i.e. by the SIGTERM hook on loopback).

Same `SESSION_SECRET` baked into `env_vars` as the admin app, so the same cookie works across all the user's per-notebook pods.

## Inline-deps conversion (notebooks)

Every notebook that's eligible for Edit/Run must carry PEP 723 inline metadata. The current notebooks don't — converting them is part of this plan.

Example header for the scRNA tutorials:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "marimo",
#   "scanpy>=1.12",
#   "anndata",
#   "matplotlib",
#   "stargazer",
# ]
#
# [tool.uv.sources]
# stargazer = { path = "/stargazer", editable = true }
# ///
"""..."""

import marimo

app = marimo.App(width="medium")
# ...
```

For `tutorials/` and `community/` notebooks shipping inside the image, `[tool.uv.sources] stargazer = { path = "/stargazer", editable = true }` resolves to the image's WORKDIR (the package source baked at image build time).

For workspace notebooks, the container-local `/workspace` directory IS the user's fork checkout (populated by the launch script's clone-on-startup), so `path = "/workspace"` instead.

## Open questions (resolved this session)

1. **Stargazer package in sandbox venvs.** ✅ Editable path source for both image-baked (`path = "/stargazer"`) and workspace (`path = "/workspace"`) notebooks. Roadmap item tracks publishing stargazer to PyPI so notebooks can eventually pin a version instead.
2. **Heavy deps that aren't `pip`-installable.** ✅ System tools (BWA, samtools, GATK) stay baked into the `note` image; the sandbox venv accesses them via `subprocess` — venv isolation doesn't block subprocess.
3. **Sandbox venv cold-start latency.** ✅ Accept for the MVP. First click pays the uv install cost; Knative wake-ups thereafter pay it again. Revisit only if it becomes painful in practice.
4. **Auth on per-notebook apps.** ✅ Cookie-validating reverse proxy in the base image as the primary plan; URL-secrecy as a fallback. Same `SESSION_SECRET` as the dashboard.
5. **Workspace push frequency.** ✅ Manual Commit+Push from the dashboard + SIGTERM-hook flush on per-notebook app. No background autosave loop.

## Open questions (still unresolved)

1. **Token lifetime for long-lived workspace pods.** GitHub OAuth tokens granted via the standard flow don't expire by default, so this is largely a non-issue for `public_repo` scope — but worth confirming once we land plan 16's `provision_user()` token-bake-on-login pattern. If we ever switch to a refresh-token flow this becomes load-bearing.
2. **Proxy + marimo websocket compatibility.** The cookie-validating reverse proxy needs to forward both HTTP and websocket traffic. Test against marimo's actual transport before committing to the proxy approach.

## Status — implemented

All of the below has been built; this section is a record:

- `app/per_notebook.py` defines `notebook_app_img` (image name `notebook-app`) — a `flyte.Image` that layers `micromamba` + bioinformatics tools, `uv`/`uvx`, `marimo`, the proxy's web deps (`fastapi`, `uvicorn`, `itsdangerous`, `httpx`, `websockets`), and copies `proxy.py` + `launch-notebook.sh` in via `with_source_file`. Admin-app deploy calls `flyte.build(notebook_app_img)` to land it in `STARGAZER_REGISTRY`. No `with_uv_project` — stargazer comes via each notebook's PEP 723 sandbox. The local Dockerfile `note` target is now a simple marimo image for `docker run` only.
- `app/proxy.py` validates the session cookie, serves the two `/__sg__/workspace/*` routes locally, and forwards HTTP + websockets to marimo on `127.0.0.1:8081`.
- `app/launch-notebook.sh` clones the user's fork into `/workspace` if absent, starts marimo on 8081 (background), then `exec`s uvicorn on 8080. SIGTERM trap fires the local `/__sg__/workspace/sync` flush.
- `app/per_notebook.py` factory builds the AppEnvironment per `(slug, mode)` and bakes `FORK_OWNER` / `GITHUB_TOKEN` / `SESSION_SECRET` into env_vars. No `pod_template=` — Flyte v2 rejects K8sPod payloads on AppEnvironments.
- `app/admin_app.py` `/launch` handler calls the factory + `serve.aio()` + redirect.
- All five existing notebooks (`preprocessing`, `assets`, `tasks`, `byod`, `scrna_pipeline`) carry PEP 723 inline metadata with `[tool.uv.sources] stargazer = { path = "/stargazer", editable = true }`.
- `src/stargazer/notebooks/**/*.py` — add PEP 723 headers to every executable notebook.
- `app/notebook_app.py` — delete (its job is now plan 16's dashboard + this plan's spawn factory).

## Out of scope

- Pinned image digests for community notebooks (deferred — fork PR flow handles this naturally once promotion lands).
- Per-notebook resources tuning beyond a generous default (revisit when something OOMs).
- MCP / in-notebook LLM.
- Local-vs-remote dispatch toggle inside cells (separate roadmap item; the per-cell `flyte.with_runcontext(mode="local").run` vs `flyte.run` story is independent of this app split).
- Publishing stargazer to PyPI (separate roadmap item that, once shipped, lets notebook PEP 723 headers pin a version instead of pointing at the image path).
