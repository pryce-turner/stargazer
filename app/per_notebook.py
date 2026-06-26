"""
### Per-notebook image + AppEnvironment factory.

Defines the shared `notebook-app` programmatic `flyte.Image` used by
every per-notebook Knative pod, and `per_notebook_env(...)` — the
AppEnvironment factory the admin app's `/launch` handler invokes.

The image layers, on top of the Flyte debian base:

- `micromamba` plus the bioinformatics tools (gatk4, samtools, bwa,
  bwa-mem2) — system-level so subprocess calls from inside the
  per-notebook sandbox venv can reach them.
- `uv` — needed by `marimo --sandbox` to provision each notebook's
  PEP 723 venv at boot.
- `claude` (Claude Code CLI) — the AI agent, on PATH for the dropdown
  terminal the proxy injects. Pinned standalone binary (auto-update off);
  auth is interactive (`claude` browser login) and ephemeral.
- `marimo` plus the cookie-validating reverse proxy's web deps
  (`fastapi`, `uvicorn`, `itsdangerous`, `httpx`, `websockets`).
- `app/proxy.py` baked at `/usr/local/lib/sg_proxy.py` (top-level module,
  importable via `PYTHONPATH=/usr/local/lib` as `sg_proxy:asgi_app`; not
  under `app/` so it doesn't get shadowed by Flyte's loaded_modules
  code bundle which lands `app/` into the pod's `/home/flyte` cwd).
- `app/terminal_overlay.html` baked at `/usr/local/lib/terminal_overlay.html`,
  the dropdown-terminal markup the proxy reads relative to its own `__file__`
  and injects into marimo's HTML responses.
- `app/launch-notebook.sh` baked at `/usr/local/bin/launch-notebook.sh`,
  invoked by the AppEnvironment `args=[...]`.

Stargazer itself is NOT installed at the system level — every notebook
declares its deps inline via PEP 723 and the sandbox venv resolves them
at boot, including `stargazer` via `[tool.uv.sources]`.

Persistence model: the per-notebook pod owns its workspace as container-
local ephemeral storage. The launch script sparse-clones only the fork's
`src/stargazer` subtree into `/workspace` on startup (cone mode; tests/docs/
app are never materialized) and cd's into `src/stargazer/notebooks/workspace`,
the sole work surface. The proxy's `/__sg__/workspace/sync` stages just that
dir and pushes edits back to the fork; the SIGTERM hook fires the same sync
before Knative idles the pod. Because sparse checkout only filters the working
tree (out-of-cone files stay in the index as SKIP_WORKTREE), those commits
still preserve the rest of the fork on `main`. The fork is the source of
truth; the pod is the working copy. No PVC — Flyte v2 doesn't yet support
`K8sPod` payloads on app environments anyway.

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import os
from typing import Literal

import flyte
import flyte.app
from flyte._initialize import get_client, get_init_config
from flyte.remote import App
from flyteidl2.app import app_payload_pb2
from flyteidl2.common import identifier_pb2, list_pb2

from stargazer.config import PROJECT_ROOT, STARGAZER_ENV_VARS

from app import config
from app.notebook_meta import NotebookResources


# Bake the proxy as a TOP-LEVEL module (not under `app/`) because Flyte's
# loaded_modules code bundle ships an `app/` package into the pod's cwd
# (`/home/flyte`) at every per-notebook deploy. If the baked-in proxy lived at
# `/usr/local/lib/app/proxy.py`, that cwd-shadowed `app` package would mask
# it and `uvicorn app.proxy:asgi_app` would fail to import.
_PROXY_LIB_DIR = "/usr/local/lib"
_PROXY_MODULE = "sg_proxy"
_LAUNCH_BIN = "/usr/local/bin"


# Mutable tag the deployer publishes (`admin_app._build_and_push_notebook_image`)
# and that running admin pods reference when spawning per-notebook apps. A fixed
# tag decouples per-notebook serve calls from whatever content hash the in-pod
# Python state would otherwise compute for `notebook_app_img_recipe`.
#
# `:latest` is load-bearing: it flips K8s's default `imagePullPolicy` to `Always`,
# so per-notebook pods pull on every cold-start and pick up code changes after a
# `python -m app.admin_app` redeploy. Any other tag defaults to `IfNotPresent`,
# and nodes that already cached the previous manifest digest skip the pull —
# meaning new proxy code (e.g. a new `/__sg__/*` route) never reaches the pod.
# In prod with a remote builder, this drops out: each deploy gets a unique URI.
NOTEBOOK_IMAGE_TAG = "latest"
NOTEBOOK_IMAGE_URI = (
    f"{os.environ['STARGAZER_REGISTRY']}/notebook-app:{NOTEBOOK_IMAGE_TAG}"
)


# Layered build recipe. Consumed only by the deployer's build step; the admin
# pod never resolves this to a URI (`Image.from_base` below is what its
# per-notebook serve calls reference).
notebook_app_img_recipe = (
    flyte.Image.from_debian_base(
        name="notebook-app",
        registry=os.environ["STARGAZER_REGISTRY"],
        platform=("linux/amd64", "linux/arm64"),
    )
    .with_apt_packages("ca-certificates", "curl", "git", "bzip2")
    .with_commands(
        [
            # micromamba + bioinformatics tools (same recipe as gatk_env).
            # Reachable by subprocess from inside each notebook's sandbox venv.
            'arch=$(uname -m); case "$arch" in x86_64) marc=linux-64;; '
            "aarch64|arm64) marc=linux-aarch64;; esac; "
            "curl -Ls https://micro.mamba.pm/api/micromamba/${marc}/latest "
            "| tar -xj -C /usr/local/bin --strip-components=1 bin/micromamba",
            "/usr/local/bin/micromamba create -p /opt/conda -y "
            "-c bioconda -c conda-forge gatk4 samtools bwa bwa-mem2 "
            "&& /usr/local/bin/micromamba clean -a -y",
            "ln -s /opt/conda/bin/gatk /usr/local/bin/gatk "
            "&& ln -s /opt/conda/bin/java /usr/local/bin/java "
            "&& ln -s /opt/conda/bin/samtools /usr/local/bin/samtools "
            "&& ln -s /opt/conda/bin/bwa /usr/local/bin/bwa "
            "&& ln -s /opt/conda/bin/bwa-mem2 /usr/local/bin/bwa-mem2",
            # uv — used by `marimo --sandbox` to build per-notebook venvs.
            "curl -LsSf https://astral.sh/uv/install.sh | sh "
            "&& install -m 755 /root/.local/bin/uv /usr/local/bin/uv "
            "&& install -m 755 /root/.local/bin/uvx /usr/local/bin/uvx",
            # Claude Code CLI — the AI agent, on PATH inside the dropdown
            # terminal. The native installer is a standalone arch-detecting
            # binary (no node). Install under a fixed build HOME so its launcher
            # (~/.local/bin/claude) and versioned binaries (~/.local/share/claude)
            # land at stable absolute paths independent of the `flyte` runtime
            # user's HOME, make the tree world-readable, then symlink onto PATH.
            # Pinned to a stable version for repro (auto-update disabled via the
            # image env below); auth is interactive (`claude` browser login) and
            # ephemeral per the pod's storage.
            "mkdir -p /opt/claude-cli "
            "&& curl -fsSL https://claude.ai/install.sh | HOME=/opt/claude-cli bash -s 2.1.153 "
            "&& chmod -R a+rX /opt/claude-cli "
            "&& ln -s /opt/claude-cli/.local/bin/claude /usr/local/bin/claude",
        ]
    )
    .with_pip_packages(
        # Launcher marimo, pinned. Notebooks don't list marimo in their PEP 723
        # headers — `marimo --sandbox` injects *this* version into each kernel
        # venv, so launcher and kernel are the same build by construction.
        "marimo==0.23.6",
        "fastapi>=0.115",
        "uvicorn>=0.34",
        "itsdangerous>=2.1",
        "httpx>=0.27",
        "websockets>=12",
        # The proxy decrypts the Fernet session cookie (app.session._fernet).
        "cryptography>=43",
    )
    # `/usr/local/lib/` already exists in the base image, so the COPY drops
    # `proxy.py` into it without needing a directory-creating trailing-slash.
    # Renamed to `sg_proxy.py` in the next command to avoid clashing with
    # Flyte's `app/` code bundle and to keep the module name unambiguous.
    .with_source_file(PROJECT_ROOT / "app" / "proxy.py", _PROXY_LIB_DIR)
    # Static dropdown-terminal markup the proxy reads at import (relative to its
    # own __file__) and injects into marimo's HTML. Baked into the same dir as
    # the proxy so that relative read resolves in-pod.
    .with_source_file(PROJECT_ROOT / "app" / "terminal_overlay.html", _PROXY_LIB_DIR)
    .with_source_file(PROJECT_ROOT / "app" / "launch-notebook.sh", _LAUNCH_BIN)
    # Bake the stargazer source tree at `/stargazer/` so each notebook's
    # `[tool.uv.sources] stargazer = { path = "/stargazer", editable = true }`
    # resolves inside the marimo --sandbox venv. Image-shipped notebooks live
    # under `/stargazer/src/stargazer/notebooks/{tutorials,workflows}/`.
    .with_source_file(PROJECT_ROOT / "pyproject.toml", "/stargazer/")
    .with_source_file(PROJECT_ROOT / "README.md", "/stargazer/")
    .with_source_folder(PROJECT_ROOT / "src", "/stargazer/src")
    .with_source_folder(PROJECT_ROOT / "app", "/stargazer/app")
    .with_commands(
        [
            f"mv {_PROXY_LIB_DIR}/proxy.py {_PROXY_LIB_DIR}/{_PROXY_MODULE}.py",
            f"chmod +x {_LAUNCH_BIN}/launch-notebook.sh",
            # Pre-create /workspace owned by the flyte runtime user so
            # launch-notebook.sh's `git clone … /workspace` can write into
            # the root-owned filesystem at startup.
            "mkdir -p /workspace && chown -R flyte:flyte /workspace",
        ]
    )
    # PYTHONPATH lets the proxy import as `sg_proxy`. DISABLE_AUTOUPDATER pins
    # Claude Code to the baked version: the install tree is read-only for the
    # flyte user, so a background self-update would otherwise re-download into
    # ephemeral home on every cold-start, defeating the pin and bloating storage.
    .with_env_vars({"PYTHONPATH": _PROXY_LIB_DIR, "DISABLE_AUTOUPDATER": "1"})
)


# Stable-tag reference. `Image.from_base` keeps `_is_cloned=False` so the SDK
# treats the URI as preexisting and skips any build/existence check; the admin
# pod just hands the URI to Flyte at per-notebook serve time.
notebook_app_img = flyte.Image.from_base(NOTEBOOK_IMAGE_URI)


def per_notebook_env(
    *,
    slug: str,
    mode: Literal["edit", "run"],
    notebook_path: str,
    fork_full_name: str,
    pod_capability: str,
    session_secret: str,
    admin_url: str,
    resources: NotebookResources | None = None,
) -> flyte.app.AppEnvironment:
    """Build a per-notebook AppEnvironment for one (slug, mode) launch.

    `notebook_path` is the absolute path inside the spawned pod — for
    image-baked notebooks that's `/stargazer/...`, for workspace notebooks
    it's `/workspace/...` (populated by the launch script's
    clone-on-startup against the user's fork).

    `fork_full_name` (`owner/repo` of the verified fork) tells the launch
    script which fork to clone — the full name handles the collision case
    where GitHub named the fork `…-1` — and `FORK_OWNER` is derived for the
    git commit identity. No GitHub credential is injected: instead
    `pod_capability` (a `SESSION_SECRET`-signed token carrying only the fork
    name, **not** a GitHub credential) goes in as `SG_POD_TOKEN`. At clone /
    push time the pod presents it to the admin's `/workspace/pod-token`
    endpoint, which mints a fresh, fork-scoped, ~1h installation token — so the
    broad token never reaches code the user controls and nothing durable lands
    in `.git/config`. `session_secret` keys the proxy's cookie validation so
    authenticated browser sessions are accepted while drive-by requests get
    401s. `admin_url` is the admin app's public base URL; the proxy's
    `/__sg__/dashboard` route 302s here, and the pod calls it back for tokens.

    `resources` is the notebook's declared `[tool.stargazer]` spec, honored
    as-authored (no ceiling). When None — image-baked tutorials and workflows
    notebooks — the env falls back to the legacy `("2Gi", "6Gi")`
    request/limit, which the memory-heavy scRNA notebook depends on.
    """
    flyte_resources = (
        flyte.Resources(cpu=resources.cpu, memory=resources.memory)
        if resources is not None
        else flyte.Resources(memory=("2Gi", "6Gi"))
    )
    return flyte.app.AppEnvironment(
        name=f"nb-{slug}-{mode}",
        description=f"Per-notebook app: {slug} ({mode})",
        image=notebook_app_img,
        # `exec` is load-bearing, not cosmetic. Flyte's `fserve` (PID 1) runs
        # these args via `Popen(" ".join(args), shell=True)` and, on the Knative
        # SIGTERM at scale-to-zero, forwards the signal to that single direct
        # child only. Without `exec`, Debian's `/bin/sh -c "launch-notebook.sh …"`
        # can linger as an intermediate shell that swallows SIGTERM, so the
        # launch script's final `exec uvicorn` never receives it and the proxy's
        # shutdown flush (commit+push of workspace edits) is silently skipped.
        # Prepending `exec` forces the `sh -c` wrapper to replace itself with the
        # script, which then `exec`s uvicorn into that same direct-child slot —
        # so the proxy is the process `fserve` signals. See launch-notebook.sh.
        args=[
            "exec",
            f"{_LAUNCH_BIN}/launch-notebook.sh",
            mode,
            notebook_path,
        ],
        port=8080,
        requires_auth=False,
        resources=flyte_resources,
        env_vars={
            **STARGAZER_ENV_VARS,
            "FLYTE_DOMAIN": "development",
            "FORK_FULL_NAME": fork_full_name,
            "FORK_OWNER": fork_full_name.split("/", 1)[0],
            "SG_POD_TOKEN": pod_capability,
            "SESSION_SECRET": session_secret,
            "STARGAZER_ADMIN_URL": admin_url,
            # Propagate the cookie-Secure policy so the proxy sets the session
            # cookie identically to the admin (off on devbox/http, on under TLS).
            "STARGAZER_SECURE_COOKIES": "1" if config.SECURE_COOKIES else "",
        },
    )


async def list_project_apps(
    project: str, domain: str = "development", limit: int = 500
) -> list[App]:
    """List every App deployment in `project`, regardless of name or state.

    `flyte.remote.App.listall` only honors the ambient init-config project (the
    admin's), but per-notebook apps live in each user's own project, so we issue
    the project-scoped list against the same client the SDK uses — there's no
    public list API that takes a project. Callers re-`App.get` by name for
    authoritative status, since a list payload may not carry full conditions.

    Used by `/workspace/cleanup` to find stopped apps for notebooks no longer on
    the dashboard (e.g. deleted ones), which a name-bounded probe would miss.
    """
    cfg = get_init_config()
    project_id = identifier_pb2.ProjectIdentifier(
        organization=cfg.org, name=project, domain=domain
    )
    apps: list[App] = []
    token = None
    while len(apps) < limit:
        resp = await get_client().app_service.list(
            request=app_payload_pb2.ListRequest(
                request=list_pb2.ListRequest(limit=100, token=token),
                org=cfg.org,
                project=project_id,
            )
        )
        apps.extend(App(a) for a in resp.apps)
        token = resp.token
        if not token:
            break
    return apps
