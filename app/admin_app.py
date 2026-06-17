"""
### Stargazer admin app — OAuth, provisioning, and the dashboard itself.

Shared, single deployment. Handles three roles in one FastAPI service:

1. **Unauthenticated landing + GitHub OAuth.** Users sign in with GitHub;
   the callback ensures their Flyte project exists, then drops them onto
   the dashboard. Forking the upstream repo is deferred and opt-in — it
   happens only when the user enables Workspace saving via
   `POST /workspace/enable`.

2. **Per-user dashboard.** Renders three sections of notebook tiles —
   Tutorials, Community (both shipped in the per-notebook image),
   Workspace (lives in the user's GitHub fork and is listed either via
   a running per-notebook pod's local clone or, as a cold-case fallback,
   via the GitHub Contents API).

3. **Launch broker.** `POST /launch?slug=…&mode=…` builds a per-notebook
   AppEnvironment via `app.per_notebook.per_notebook_env(...)`, serves
   it into the user's Flyte project, and redirects the browser to the
   resulting URL.

`app_env` (this app's own AppEnvironment) and `main()` (the deploy
entrypoint) are also defined here.

Local development:
    uvicorn app.admin_app:asgi_app --reload --port 8080

Deploy hosted to Flyte:
    python -m app.admin_app           # or: stargazer-app

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import asyncio
import atexit
import os
import re
import secrets
import socket
import subprocess
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import flyte
import flyte.app
import httpx
from flyte.remote import App
from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

from stargazer.config import (
    PROJECT_ROOT,
    STARGAZER_ENV_VARS,
    logger,
)

from app import config, installation_tokens
from app.github import (
    canonical_fork_name,
    create_snapshot_notebook,
    create_workspace_notebook,
    delete_snapshot_notebook,
    delete_workspace_notebook,
    find_existing_fork,
    fork_upstream,
    get_snapshot_notebook,
    get_workspace_notebook,
    is_genuine_fork,
    list_snapshots as gh_list_snapshots,
    list_workspace as gh_list_workspace,
    update_workspace_notebook,
)
from app.notebook_meta import (
    DEFAULT_RESOURCES,
    NotebookResources,
    memory_to_gib,
    parse_notebook_description,
    parse_notebook_resources,
    resources_from_inputs,
    with_stargazer_resources,
)
from app.init import init
from app.notebooks import (
    NOTEBOOKS,
    SEED_SLUGS,
    SNAPSHOT_NOTEBOOK_DIR,
    WORKSPACE_NOTEBOOK_DIR,
    Notebook,
    by_section,
    by_slug,
)
from app.assets import router as assets_router
from app.oauth import exchange_code, get_github_user, github_auth_url
from app.per_notebook import (
    NOTEBOOK_IMAGE_URI,
    list_project_apps,
    notebook_app_img_recipe,
    per_notebook_env,
)
from app.provision import provision_user, sanitize_project_id
from app.session import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    SessionData,
    create_session_cookie,
    read_pod_capability,
    session_from_request,
    sign_pod_capability,
)
from app.templates import templates


# ---------------------------------------------------------------------------
# Flyte AppEnvironment for the admin app itself.
# ---------------------------------------------------------------------------

# AppEnvironment `secrets=[...]` is silently dropped on App pods in this Flyte
# build: flyte-binary stamps neither the secret annotations nor the
# `inject-flyte-secrets` label onto the Knative pod, so the injection webhook
# never fires (verified empirically — the rendered ksvc pod template carries
# only autoscaling annotations). Until Flyte wires up App secrets, bake the
# OAuth secrets from the deployer's shell into `env_vars`; the trade-off is the
# values then live in the App spec. Export them before `python -m app.admin_app`.
# See .opencode/reference/devbox_workarounds.md
_SECRET_NAMES = ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "SESSION_SECRET")
# GitHub App credentials + config (trust anchor for fork-scoped installation
# tokens — see app.installation_tokens / plan 18). `GITHUB_APP_SLUG` (non-secret)
# drives the install-redirect URL. All optional: baked into the App env only
# when set, so deploys before the GitHub App is registered keep working.
_GITHUB_APP_NAMES = ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_SLUG")
# Pinata credential for the /assets routes (list/sign/download). Optional:
# without it the asset manager renders a "Pinata not configured" state.
_STORAGE_NAMES = ("PINATA_JWT",)
_RUNTIME_SECRETS = {
    name: os.environ[name]
    for name in (*_SECRET_NAMES, *_GITHUB_APP_NAMES, *_STORAGE_NAMES)
    if os.environ.get(name)
}

# Admin pod needs a default Flyte project for code-bundle uploads during
# per-user `serve.aio(per_notebook_env)` calls. `with_servecontext(project=...)`
# alone is not enough — the upload uses the client's init-time project.
_FLYTE_CONTEXT = {
    "FLYTE_PROJECT": config.FLYTE_PROJECT,
    "FLYTE_DOMAIN": config.FLYTE_DOMAIN,
}

# Non-secret config that request handlers read at runtime, baked into the pod
# env so the deployed admin (and, propagated onward, the notebook proxy) sees
# it. `STARGAZER_SECURE_COOKIES` is re-serialized from the parsed flag so a
# single source of truth (`config.SECURE_COOKIES`) drives every cookie writer.
_PUBLIC_CONFIG = {
    "STARGAZER_SECURE_COOKIES": "1" if config.SECURE_COOKIES else "",
}


app_env = flyte.app.AppEnvironment(
    name="admin-app",
    description="Stargazer landing, OAuth, dashboard, and notebook launcher",
    image=(
        flyte.Image.from_debian_base(
            name="admin-app",
            registry=os.environ["STARGAZER_REGISTRY"],
            platform=("linux/amd64", "linux/arm64"),
        )
        .with_apt_packages("ca-certificates", "git")
        .with_uv_project(
            PROJECT_ROOT / "pyproject.toml",
            project_install_mode="install_project",
            extra_args="--extra landing",
        )
        .with_commands(["flyte create config --local-persistence"])
    ),
    args=[
        "uvicorn",
        "app.admin_app:asgi_app",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
    ],
    port=8080,
    requires_auth=False,
    resources=flyte.Resources(memory=("512Mi", "1Gi")),
    env_vars={
        **STARGAZER_ENV_VARS,
        **_RUNTIME_SECRETS,
        **_FLYTE_CONTEXT,
        **_PUBLIC_CONFIG,
    },
    # Flyte's loaded_modules bundler ships only .py files; HTML and the
    # landing-page logo must be enumerated.
    include=("templates/", "static/"),
)


# ---------------------------------------------------------------------------
# Per-user launched-pod registry (in-memory; volatile across admin restarts)
# ---------------------------------------------------------------------------


# `_launched[github_username][(slug, mode)]` is the public endpoint URL of
# every per-notebook pod the user has spawned at least once. Used by
# `/__sg__/workspace/list` queries when rendering the dashboard. (Live run
# state for the spinner comes from the cluster via `/launch/status`'s
# `App.get` calls, not from this registry.)
# In-memory only — admin restarts clear it; users would then re-click Edit/Run
# to repopulate.
_launched: dict[str, dict[tuple[str, str], str]] = defaultdict(dict)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def _env(key: str) -> str:
    """Read a required environment variable."""
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing required env var: {key}")
    return value


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize Flyte client so /launch can call flyte.serve.aio()."""
    init()
    yield


asgi_app = FastAPI(title="Stargazer", docs_url=None, redoc_url=None, lifespan=lifespan)
asgi_app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
asgi_app.include_router(assets_router)


def _redirect_uri(request: Request) -> str:
    """Build the OAuth callback URI."""
    if config.LANDING_BASE_URL:
        return f"{config.LANDING_BASE_URL.rstrip('/')}/auth/callback"
    return str(request.url_for("auth_callback"))


def _get_session(request: Request) -> SessionData | None:
    """Extract a valid session from the request cookie, if present."""
    return session_from_request(request, _env("SESSION_SECRET"))


def _session_redirect(
    location: str, session: SessionData, status_code: int = 302
) -> RedirectResponse:
    """Redirect to `location` with the (re-)signed session cookie attached.

    The single place the host-only, 30-day session cookie is written — used by
    every route that mints or updates a session (login callback, enable, install
    callback). `Secure` comes from `config.SECURE_COOKIES` (off on devbox/http,
    on under production TLS). Use 303 from POST handlers to force a GET on the
    redirect, 302 from GET handlers.
    """
    response = RedirectResponse(location, status_code=status_code)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_cookie(session, _env("SESSION_SECRET")),
        httponly=True,
        secure=config.SECURE_COOKIES,
        max_age=SESSION_MAX_AGE,
        samesite="lax",
    )
    return response


# ---------------------------------------------------------------------------
# Workspace listing helper
# ---------------------------------------------------------------------------


async def _list_workspace_from_pods(
    session_cookie: str, endpoints: dict[tuple[str, str], str]
) -> list[str] | None:
    """Try each previously-launched per-notebook pod for a workspace listing.

    Returns the first successful response's file list, or None if no pod
    answers within the short per-attempt timeout. Uses each pod's public
    endpoint URL (cached at /launch time) so the request follows the same
    path the browser uses — no dependence on in-cluster DNS.
    """
    for endpoint in endpoints.values():
        url = f"{endpoint.rstrip('/')}/__sg__/workspace/list"
        try:
            async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
                resp = await client.get(url, cookies={SESSION_COOKIE: session_cookie})
            if resp.status_code == 200:
                return resp.json().get("files", [])
        except Exception:
            continue
    return None


async def _resolve_workspace_files(
    session: SessionData, cookie_value: str
) -> list[str]:
    """Return the user's workspace files, preferring a live pod over GitHub."""
    known = _launched.get(session.github_username, {})
    from_pod = await _list_workspace_from_pods(cookie_value, known)
    if from_pod is not None:
        return from_pod
    if not session.workspace_enabled:
        return []
    try:
        token = await installation_tokens.fork_token(session.fork_full_name)
        return await gh_list_workspace(session.fork_full_name, token)
    except Exception as exc:
        logger.warning(f"GitHub workspace listing failed: {exc}")
        return []


async def _resolve_snapshot_files(session: SessionData) -> list[str]:
    """List the fork's frozen snapshots from `notebooks/snapshots/` (GitHub).

    Snapshots flow through the fork like the rest of the repo: a fresh fork
    inherits every **public, merged** snapshot from upstream, and the user's own
    📸 freezes land in the same dir (refreshed by syncing the fork with `main`).
    So this single fork listing covers both — there's no separate upstream
    source. The listing always reads GitHub (no live-pod path, unlike the
    workspace listing) even though run-mode launches *do* serve a snapshot from
    the fork clone. Returns [] when saving is off or the listing fails (the
    section just shows its empty-state hint).
    """
    if not session.workspace_enabled:
        return []
    try:
        token = await installation_tokens.fork_token(session.fork_full_name)
        return await gh_list_snapshots(session.fork_full_name, token)
    except Exception as exc:
        logger.warning(f"GitHub snapshot listing failed: {exc}")
        return []


async def _candidate_slugs(session: SessionData, cookie_value: str) -> list[str]:
    """All notebook slugs whose per-notebook apps a user might have.

    Registry Tutorials/Community plus the user's Workspace notebooks (seeds
    excluded) and frozen Snapshots. Shared by `/launch/status` and
    `/workspace/cleanup` to bound the set of `nb-{slug}-{mode}` apps they query.
    """
    slugs = [n.slug for n in NOTEBOOKS]
    if session.workspace_enabled:
        files = await _resolve_workspace_files(session, cookie_value)
        slugs += [
            slug for f in files if (slug := f.removesuffix(".py")) not in SEED_SLUGS
        ]
        snapshots = await _resolve_snapshot_files(session)
        slugs += [f.removesuffix(".py") for f in snapshots]
    return slugs


# ---------------------------------------------------------------------------
# Routes — login / dashboard / launch
# ---------------------------------------------------------------------------


def _tile_dict(
    slug: str,
    title: str,
    description: str,
    section: str,
    *,
    cpu: int | None = None,
    memory: int | None = None,
) -> dict:
    """Build the context dict consumed by `_tile.html`.

    `cpu`/`memory` (whole GiB) are carried only for workspace tiles — they
    seed the settings modal's fields via the gear button's data attributes.
    """
    return {
        "slug": slug,
        "title": title,
        "description": description,
        "section": section,
        "cpu": cpu,
        "memory": memory,
    }


def _display_name(filename: str) -> str:
    """Human-friendly tile title for a workspace notebook file.

    Drops the `.py` extension and capitalizes the first letter, so
    `my-notebook.py` tiles as `My-notebook`.
    """
    stem = filename.removesuffix(".py")
    return stem[:1].upper() + stem[1:]


_GENERIC_WS_DESC = "Personal workspace notebook."


def _workspace_tile_dict(slug: str, cpu: int, memory: int, description: str) -> dict:
    """A workspace tile carrying its editable resources + blurb for the modal."""
    return _tile_dict(
        slug,
        _display_name(f"{slug}.py"),
        description or _GENERIC_WS_DESC,
        "workspace",
        cpu=cpu,
        memory=memory,
    )


def _snapshot_tile_dict(slug: str) -> dict:
    """A read-only Snapshots tile — a frozen record, runnable only in run mode."""
    return _tile_dict(slug, _display_name(f"{slug}.py"), "", "snapshots")


def _snapshot_tiles(snapshot_files: list[str]) -> list[dict]:
    """Build read-only Snapshots tiles from the fork's `snapshots/` listing."""
    return [
        _snapshot_tile_dict(slug)
        for f in snapshot_files
        if (slug := f.removesuffix(".py"))
    ]


async def _workspace_tiles(
    session: SessionData, workspace_files: list[str]
) -> list[dict]:
    """Build Workspace tiles from the user's own notebooks.

    The shipped seed notebooks (`SEED_SLUGS`) are filtered out — they're create
    sources, not user notebooks. Each remaining notebook's `[tool.stargazer]`
    header is fetched from the fork (in parallel, best-effort) so the tile shows
    its edited description and the gear seeds the settings modal with current
    resources. A fetch/parse failure falls back to the generic blurb + default
    resources rather than dropping the tile.
    """
    slugs = [
        slug
        for f in workspace_files
        if (slug := f.removesuffix(".py")) not in SEED_SLUGS
    ]
    if not slugs:
        return []
    try:
        token = await installation_tokens.fork_token(session.fork_full_name)
    except Exception as exc:
        logger.warning(f"Workspace metadata token failed: {exc}")
        token = None

    default_gib = memory_to_gib(DEFAULT_RESOURCES.memory)

    async def _tile(slug: str) -> dict:
        """Build one workspace tile, parsing its header (best-effort)."""
        cpu, memory, desc = DEFAULT_RESOURCES.cpu, default_gib, ""
        if token is not None:
            try:
                src = await get_workspace_notebook(
                    session.fork_full_name, token, f"{slug}.py"
                )
                if src:
                    res = parse_notebook_resources(src)
                    cpu, memory = res.cpu, memory_to_gib(res.memory)
                    desc = parse_notebook_description(src)
            except Exception as exc:
                logger.warning(f"Workspace metadata fetch failed for {slug!r}: {exc}")
        return _workspace_tile_dict(slug, cpu, memory, desc)

    return list(await asyncio.gather(*[_tile(s) for s in slugs]))


async def _dashboard_context(
    session: SessionData,
    workspace_files: list[str],
    snapshot_files: list[str] | None = None,
    fork_error: bool = False,
) -> dict:
    """Assemble the tile lists Jinja's `dashboard.html` iterates over.

    When workspace saving is off the user hasn't opted in to forking, so no
    Workspace tiles are built — the template renders the disclaimer + Enable
    button instead. Snapshots are read-only frozen records from the fork's
    `notebooks/snapshots/` (see `_resolve_snapshot_files`). `fork_error` (set
    after a failed `/workspace/enable`) tells the disclaimer to explain the
    fork couldn't be created.
    """
    workspace = (
        await _workspace_tiles(session, workspace_files)
        if session.workspace_enabled
        else []
    )
    return {
        "title": "Dashboard",
        "github_username": session.github_username,
        "workspace_enabled": session.workspace_enabled,
        "fork_error": fork_error,
        "tutorials": [
            _tile_dict(n.slug, n.title, n.description, "tutorials")
            for n in by_section("tutorials")
        ],
        "workflows": [
            _tile_dict(n.slug, n.title, n.description, "workflows")
            for n in by_section("workflows")
        ],
        "snapshots": _snapshot_tiles(snapshot_files or []),
        "workspace": workspace,
    }


@asgi_app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    """Render the login page or the dashboard depending on session state."""
    session = _get_session(request)
    if not session:
        return templates.TemplateResponse(request, "login.html", {"title": "Sign In"})
    files: list[str] = []
    snapshots: list[str] = []
    if session.workspace_enabled:
        cookie_value = request.cookies.get(SESSION_COOKIE, "")
        files = await _resolve_workspace_files(session, cookie_value)
        snapshots = await _resolve_snapshot_files(session)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        await _dashboard_context(
            session,
            files,
            snapshots,
            fork_error=request.query_params.get("ws_error") == "fork",
        ),
    )


@asgi_app.get("/auth/login")
async def auth_login(request: Request):
    """Redirect to GitHub for OAuth authorization."""
    state = secrets.token_urlsafe(32)
    url = github_auth_url(
        client_id=_env("GITHUB_CLIENT_ID"),
        redirect_uri=_redirect_uri(request),
        state=state,
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        "oauth_state",
        state,
        httponly=True,
        secure=config.SECURE_COOKIES,
        max_age=600,
        samesite="lax",
    )
    return response


@asgi_app.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request, code: str, state: str):
    """Handle GitHub OAuth callback, then provision the user."""
    expected_state = request.cookies.get("oauth_state")
    if not expected_state or not secrets.compare_digest(state, expected_state):
        return RedirectResponse("/", status_code=302)

    access_token = await exchange_code(
        client_id=_env("GITHUB_CLIENT_ID"),
        client_secret=_env("GITHUB_CLIENT_SECRET"),
        code=code,
        redirect_uri=_redirect_uri(request),
    )
    github_user = await get_github_user(access_token)
    username = github_user["login"]

    try:
        await provision_user(github_username=username)
    except Exception as exc:
        logger.error(f"Provisioning failed for {username!r}: {exc}")

    # GitHub forking is opt-in: no fork is *created* here. But enabling
    # Workspace saving forks the upstream repo, and that fork persists on
    # GitHub across sessions while the session cookie is minted fresh each
    # login. So detect an already-existing genuine fork and restore
    # `fork_full_name` — otherwise a returning user who enabled saving in a
    # past session would find it silently off. Users who never enabled have no
    # fork, so this stays empty == saving off, and they run Tutorials/Community
    # notebooks (ephemeral, image-baked) with no write to their GitHub account.
    fork_full_name = ""
    try:
        existing_fork = await find_existing_fork(access_token, username)
        if existing_fork is not None:
            fork_full_name = existing_fork["full_name"]
    except Exception as exc:
        logger.warning(f"Fork lookup failed for {username!r}: {exc}")

    # A returning user's fork persists, but the cookie is minted fresh each
    # login — so re-confirm the GitHub App is still installed on it (the second
    # half of opt-in). A live `get_installation_id` is authoritative: if they
    # uninstalled, `app_installed` goes False and they're prompted to re-enable.
    app_installed = False
    if fork_full_name:
        try:
            await installation_tokens.get_installation_id(username)
            app_installed = True
        except Exception as exc:
            logger.warning(f"App-install check failed for {username!r}: {exc}")

    # The OAuth token is kept only to fork at a future `/workspace/enable`. An
    # already-enabled returning user (fork + install confirmed) has nothing left
    # to fork, so we don't store it — their session stays token-free. Everyone
    # else keeps it until the install callback clears it.
    fully_enabled = bool(fork_full_name and app_installed)
    session = SessionData(
        github_username=username,
        github_id=github_user["id"],
        access_token="" if fully_enabled else access_token,
        fork_full_name=fork_full_name,
        app_installed=app_installed,
    )
    response = _session_redirect("/", session)
    response.delete_cookie("oauth_state")
    return response


@asgi_app.get("/auth/logout")
async def auth_logout():
    """Clear session and redirect to landing page."""
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


@asgi_app.post("/workspace/enable")
async def workspace_enable(request: Request):
    """Opt in to Workspace saving by forking the upstream repo for this user.

    The first of opt-in's two consent-gated steps: it creates the user's fork
    and records its verified `fork_full_name`, then sends them to install the
    GitHub App (the second step). Saving turns on only once **both** are done —
    `workspace_enabled` gates on the install too. Idempotent — `fork_upstream`
    returns the existing fork if one already exists.

    Two checks before recording: `is_genuine_fork` confirms GitHub handed back
    a fork the user owns — NOT the upstream source (the transfer-redirect /
    self-fork case, where writing would clobber the shared repo) — and the
    result must sit at the canonical `{username}/{upstream_name}` location. If
    that name is already taken, GitHub renames the fork (`stargazer-1`); we
    refuse rather than track the alias, so detection at login (which only looks
    at the canonical name) can never disagree with what enable recorded. On any
    failure the session is left untouched (saving stays off) and we redirect
    with `?ws_error=fork` so the user sees why.

    After the fork is recorded we send the user to install the GitHub App on it
    (`_app_install_url`), so post-fork ops can mint fork-scoped tokens. The
    install's setup callback (`/auth/app-install-callback`) finishes the opt-in
    and drops the now-spent OAuth token. If no App is configured
    (`GITHUB_APP_SLUG` unset) we skip straight to the dashboard.
    """
    session = _get_session(request)
    if session is None:
        return RedirectResponse("/", status_code=302)
    try:
        fork = await fork_upstream(session.access_token)
    except Exception as exc:
        logger.error(f"Fork creation failed for {session.github_username!r}: {exc}")
        return RedirectResponse("/?ws_error=fork", status_code=303)

    canonical = canonical_fork_name(session.github_username)
    if not is_genuine_fork(fork) or fork.get("full_name") != canonical:
        logger.error(
            f"Refusing workspace enable for {session.github_username!r}: not a "
            f"genuine fork at the canonical name {canonical!r} "
            f"(full_name={fork.get('full_name')!r}, fork={fork.get('fork')!r})"
        )
        return RedirectResponse("/?ws_error=fork", status_code=303)

    session.fork_full_name = fork["full_name"]
    return _session_redirect(_app_install_url() or "/", session, status_code=303)


def _app_install_url() -> str | None:
    """The GitHub App's installation URL, or None when no App is configured.

    Built from `config.GITHUB_APP_SLUG` (the App's public URL handle, e.g.
    `stargazer-workspaces`). On the App's "Only select repositories" install
    page the user picks their fork. Unset means installs aren't wired up yet, so
    `/workspace/enable` just lands on the dashboard (clone/push will no-op until
    the App is installed).
    """
    slug = config.GITHUB_APP_SLUG
    return f"https://github.com/apps/{slug}/installations/new" if slug else None


@asgi_app.get("/auth/app-install-callback")
async def app_install_callback(request: Request):
    """Finish opt-in after the user installs the GitHub App on their fork.

    Configured as the App's Setup URL; GitHub redirects here post-install with
    `installation_id` / `setup_action` (we don't need either — `fork_token`
    resolves the install by owner on demand). Reaching this route *is* the
    install confirmation (GitHub only sends users here after a successful
    install, so we trust it over a racy API re-check): we set `app_installed`,
    which flips `workspace_enabled` on, and **drop the OAuth token** — the fork
    exists, the App is installed, and every post-fork op uses fork-scoped
    installation tokens from here on. Re-sign the cookie and land on the
    dashboard.
    """
    session = _get_session(request)
    if session is None:
        return RedirectResponse("/", status_code=302)
    session.app_installed = True
    session.access_token = ""
    return _session_redirect("/", session)


_NOTEBOOK_NAME_RE = re.compile(r"[^a-z0-9]+")


def _notebook_slug(name: str) -> str | None:
    """Sanitize a user-supplied notebook name into a filesystem-safe slug.

    Lowercases, collapses runs of non-alphanumerics to single hyphens, and
    trims leading/trailing hyphens. Returns None when the result is empty or
    collides with a reserved seed slug (`SEED_SLUGS`).
    """
    slug = _NOTEBOOK_NAME_RE.sub("-", name.strip().lower()).strip("-")
    if not slug or slug in SEED_SLUGS:
        return None
    return slug


@asgi_app.post("/workspace/create")
async def workspace_create(
    request: Request,
    name: str = Form(...),
    source: str = Form("blank"),
    cpu: str = Form("2"),
    memory: str = Form("4"),
):
    """Create a new workspace notebook (blank or from template) on the fork.

    Writes `<slug>.py` to the fork's `main` (under `notebooks/workspace/`) with
    default resources baked into its `[tool.stargazer]` header, then returns the
    rendered tile. Resources (and the tile blurb) are edited afterward via the
    tile's gear → settings modal (`/workspace/settings`), so creation only asks
    for a name + seed. The dashboard JS chains into the existing `/launch` flow
    (edit mode) to open it, so `/launch` stays the only serve path.
    """
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not session.workspace_enabled:
        return JSONResponse({"error": "enable workspace saving first"}, status_code=403)
    if source not in ("blank", "template"):
        return JSONResponse({"error": f"invalid source: {source}"}, status_code=400)

    slug = _notebook_slug(name)
    if slug is None:
        return JSONResponse(
            {"error": f"invalid or reserved notebook name: {name!r}"}, status_code=400
        )

    filename = f"{slug}.py"
    resources = resources_from_inputs(cpu, memory)
    repo = session.fork_full_name

    try:
        token = await installation_tokens.fork_token(session.fork_full_name)
        if await get_workspace_notebook(repo, token, filename) is not None:
            return JSONResponse(
                {"error": f"notebook already exists: {filename}"}, status_code=409
            )

        # Both sources are shipped seed notebooks (`{source}.py`); copy the
        # chosen one and inject the requested resources into its header.
        seed_src = await get_workspace_notebook(repo, token, f"{source}.py")
        if seed_src is None:
            return JSONResponse(
                {"error": f"{source} seed not found in fork"}, status_code=502
            )
        content = with_stargazer_resources(seed_src, resources)

        await create_workspace_notebook(repo, token, filename, content)
    except Exception as exc:
        logger.error(f"Notebook create failed for {filename!r}: {exc}")
        return JSONResponse({"error": f"create failed: {exc}"}, status_code=502)

    # Return the rendered tile so the dashboard can drop it straight into the
    # Workspace grid — from there it behaves like any other tile (Edit/Run,
    # then the standard launch + status flow). Create stays a pure "add a
    # notebook" action: no launching, no navigation.
    tile = _workspace_tile_dict(
        slug, resources.cpu, memory_to_gib(resources.memory), ""
    )
    tile_html = templates.env.get_template("_tile.html").render(tile=tile)
    return JSONResponse({"slug": slug, "tile_html": tile_html})


@asgi_app.post("/workspace/settings")
async def workspace_settings(
    request: Request,
    slug: str = Form(...),
    cpu: str = Form(...),
    memory: str = Form(...),
    description: str = Form(""),
):
    """Persist edited resources + description for a workspace notebook.

    Rewrites the notebook's `[tool.stargazer]` header on the fork (cpu/memory +
    the tile blurb) and commits it via the Contents API, like create and delete.
    Resource changes take effect at the **next** `/launch` (resources bind at
    pod-spawn); the description is reflected on the tile immediately. Returns the
    normalized values so the browser can refresh the tile + gear data in place.
    """
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not session.workspace_enabled:
        return JSONResponse({"error": "enable workspace saving first"}, status_code=403)
    if _notebook_slug(slug) != slug:
        return JSONResponse(
            {"error": f"invalid or reserved notebook: {slug!r}"}, status_code=400
        )

    repo = session.fork_full_name
    filename = f"{slug}.py"
    resources = resources_from_inputs(cpu, memory)
    desc = " ".join(description.split())[:200]
    try:
        token = await installation_tokens.fork_token(repo)
        source = await get_workspace_notebook(repo, token, filename)
        if source is None:
            return JSONResponse(
                {"error": f"notebook not found: {filename}"}, status_code=404
            )
        content = with_stargazer_resources(source, resources, description=desc or None)
        await update_workspace_notebook(
            repo, token, filename, content, message=f"workspace: settings {filename}"
        )
    except Exception as exc:
        logger.error(f"Notebook settings update failed for {filename!r}: {exc}")
        return JSONResponse({"error": f"update failed: {exc}"}, status_code=502)

    return JSONResponse(
        {
            "slug": slug,
            "cpu": resources.cpu,
            "memory": memory_to_gib(resources.memory),
            "description": desc or _GENERIC_WS_DESC,
        }
    )


async def _teardown_notebook_pods(session: SessionData, slug: str) -> None:
    """Completely tear down each edit/run pod for a notebook slug.

    Deactivate first (a clean stop), then delete the deployment record, so the
    notebook leaves nothing behind — no tile to Stop it, no orphan for
    `/workspace/cleanup` to reap. Shared by `/workspace/delete` and
    `/workspace/snapshot` (both remove a notebook from the Workspace surface).
    Every step is best-effort and never raises.
    """
    project = sanitize_project_id(session.github_username)
    for mode in ("edit", "run"):
        name = f"nb-{slug}-{mode}"
        try:
            app = await App.get.aio(name=name, project=project, domain="development")
            await app.deactivate.aio()
        except Exception:
            pass
        try:
            await App.delete.aio(name=name, project=project, domain="development")
        except Exception:
            pass
        _launched.get(session.github_username, {}).pop((slug, mode), None)


@asgi_app.post("/workspace/delete")
async def workspace_delete(request: Request, slug: str = Form(...)):
    """Delete a workspace notebook from the user's fork.

    Removes `<slug>.py` from the fork's `main` (idempotent — a file that's
    already gone still returns 200) and tears down each edit/run pod for that
    slug completely, so a deleted notebook leaves nothing behind — no tile to
    Stop it, no orphan for `/workspace/cleanup` to reap. Teardown is best-effort
    and never fails the delete.
    """
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not session.workspace_enabled:
        return JSONResponse({"error": "enable workspace saving first"}, status_code=403)
    if _notebook_slug(slug) != slug:
        return JSONResponse(
            {"error": f"invalid or reserved notebook: {slug!r}"}, status_code=400
        )

    await _teardown_notebook_pods(session, slug)

    try:
        token = await installation_tokens.fork_token(session.fork_full_name)
        await delete_workspace_notebook(session.fork_full_name, token, f"{slug}.py")
    except Exception as exc:
        logger.error(f"Notebook delete failed for {slug!r}: {exc}")
        return JSONResponse({"error": f"delete failed: {exc}"}, status_code=502)

    return JSONResponse({"status": "deleted", "slug": slug})


@asgi_app.post("/workspace/snapshot")
async def workspace_snapshot(request: Request, slug: str = Form(...)):
    """Freeze a workspace notebook by *moving* it into the snapshots dir.

    A snapshot takes an analysis out of the editable Workspace surface and
    pins it as a frozen record: the notebook's current source on the fork's
    `main` (its last *saved* state — Save first to capture live edits) is
    re-created verbatim under `notebooks/snapshots/<slug>.py`, then the
    workspace original is deleted. The snapshots write happens first, so a
    failed move leaves the notebook editable rather than lost. Like delete, it
    tears down any running pod for the slug — once moved, no Workspace tile
    remains to Stop it.
    """
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not session.workspace_enabled:
        return JSONResponse({"error": "enable workspace saving first"}, status_code=403)
    if _notebook_slug(slug) != slug:
        return JSONResponse(
            {"error": f"invalid or reserved notebook: {slug!r}"}, status_code=400
        )

    repo = session.fork_full_name
    filename = f"{slug}.py"
    try:
        token = await installation_tokens.fork_token(repo)
        source = await get_workspace_notebook(repo, token, filename)
        if source is None:
            return JSONResponse(
                {"error": f"notebook not found: {filename}"}, status_code=404
            )
        # Write the frozen copy first; only then remove the workspace original,
        # so a failed write can't leave the notebook lost from both dirs.
        await create_snapshot_notebook(repo, token, filename, source)
        await delete_workspace_notebook(repo, token, filename)
    except Exception as exc:
        logger.error(f"Notebook snapshot failed for {slug!r}: {exc}")
        return JSONResponse({"error": f"snapshot failed: {exc}"}, status_code=502)

    await _teardown_notebook_pods(session, slug)

    # Return the rendered frozen tile so the browser can drop it straight into
    # the Snapshots grid (and remove the workspace tile it moved out of).
    tile = _snapshot_tile_dict(slug)
    tile_html = templates.env.get_template("_tile.html").render(tile=tile)
    return JSONResponse({"status": "snapshotted", "slug": slug, "tile_html": tile_html})


@asgi_app.post("/snapshot/delete")
async def snapshot_delete(request: Request, slug: str = Form(...)):
    """Delete a frozen snapshot notebook from the user's fork.

    Removes `<slug>.py` from the fork's `notebooks/snapshots/` (idempotent — a
    file that's already gone still returns 200) and tears down any run pod for
    that slug, so a deleted snapshot leaves nothing behind. Snapshots are
    fork-backed, so this requires the workspace opt-in. Teardown is best-effort
    and never fails the delete.
    """
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not session.workspace_enabled:
        return JSONResponse({"error": "enable workspace saving first"}, status_code=403)
    if _notebook_slug(slug) != slug:
        return JSONResponse(
            {"error": f"invalid or reserved notebook: {slug!r}"}, status_code=400
        )

    await _teardown_notebook_pods(session, slug)

    try:
        token = await installation_tokens.fork_token(session.fork_full_name)
        await delete_snapshot_notebook(session.fork_full_name, token, f"{slug}.py")
    except Exception as exc:
        logger.error(f"Snapshot delete failed for {slug!r}: {exc}")
        return JSONResponse({"error": f"delete failed: {exc}"}, status_code=502)

    return JSONResponse({"status": "deleted", "slug": slug})


@asgi_app.post("/launch")
async def launch(
    request: Request,
    slug: str = Form(...),
    mode: str = Form(...),
    section: str = Form(...),
):
    """Spawn (or reuse) a per-notebook app for the requested slug+mode."""
    # AJAX callers parse the response as JSON; a 302 to /login would be
    # silently followed by fetch() and decoded as HTML, breaking JSON.parse.
    wants_json = "application/json" in request.headers.get("accept", "")

    def _reject(message: str, status: int):
        """Reject the launch: JSON error for AJAX callers, else redirect home."""
        if wants_json:
            return JSONResponse({"error": message}, status_code=status)
        return RedirectResponse("/", status_code=302)

    session = _get_session(request)
    if session is None:
        return _reject("not authenticated", 401)
    if mode not in ("edit", "run"):
        return _reject(f"invalid mode: {mode}", 400)
    if section not in ("tutorials", "workflows", "workspace", "snapshots"):
        return _reject(f"invalid section: {section}", 400)
    # Fork-backed sections (workspace + snapshots) clone the user's fork, so
    # they require opt-in.
    if section in ("workspace", "snapshots") and not session.workspace_enabled:
        return _reject("enable workspace saving first", 403)
    # Snapshots are frozen records: read-only run view only, never editable.
    if section == "snapshots" and mode != "run":
        return _reject("snapshots open in run mode only", 400)

    # Fork-backed notebooks declare resources in their `[tool.stargazer]`
    # header; fetch the source from the fork (workspace or snapshots dir) and
    # parse it before serving. Image-baked tutorials/workflows keep the env's
    # legacy defaults (`resources=None`). A missing/unfetchable source falls
    # back to the parser's defaults rather than failing the launch.
    resources: NotebookResources | None = None
    if section in ("workspace", "snapshots"):
        if section == "workspace":
            notebook_dir, fetch = WORKSPACE_NOTEBOOK_DIR, get_workspace_notebook
        else:
            notebook_dir, fetch = SNAPSHOT_NOTEBOOK_DIR, get_snapshot_notebook
        notebook_path = f"{notebook_dir}/{slug}.py"
        try:
            token = await installation_tokens.fork_token(session.fork_full_name)
            source = await fetch(session.fork_full_name, token, f"{slug}.py")
        except Exception as exc:
            logger.warning(f"Resource fetch failed for {section} {slug!r}: {exc}")
            source = None
        resources = parse_notebook_resources(source) if source else DEFAULT_RESOURCES
    else:
        nb: Notebook | None = by_slug(slug)
        if nb is None or nb.section != section:
            return _reject(f"unknown notebook: {slug}", 404)
        notebook_path = nb.path_in_image

    project = sanitize_project_id(session.github_username)
    # Prefer the explicit LANDING_BASE_URL (set in hosted deploys); fall back
    # to the request's own base_url so local dev (`uvicorn ... --reload`) works
    # without extra config.
    admin_url = (config.LANDING_BASE_URL or str(request.base_url)).rstrip("/")
    # The pod gets a signed *capability* (fork name only), never a GitHub
    # credential. It exchanges that at `/workspace/pod-token` for a fresh,
    # fork-scoped, short-lived token at clone/push time.
    env = per_notebook_env(
        slug=slug,
        mode=mode,  # type: ignore[arg-type]
        notebook_path=notebook_path,
        fork_full_name=session.fork_full_name,
        pod_capability=sign_pod_capability(
            session.fork_full_name, _env("SESSION_SECRET")
        ),
        session_secret=_env("SESSION_SECRET"),
        admin_url=admin_url,
        resources=resources,
    )
    env.env_vars["FLYTE_PROJECT"] = project
    # Owner attribution: workspace MCP/SDK uploads stamp `_owner` from this
    # (and config.py forwards it onward into task pods at run submission).
    env.env_vars["STARGAZER_OWNER"] = session.github_username

    # `serve.aio()` watches the deployment to readiness and raises if the pod
    # is slow to schedule/boot — common on the memory-tight devbox node, where
    # a large `[tool.stargazer]` memory request can sit Pending briefly. That's
    # not a real failure: the route (and `App.endpoint`) exist as soon as the
    # app is admitted, and the dashboard already polls `/__sg__/ready`. So on a
    # watch error, recover the endpoint and hand off anyway; only error out if
    # the app genuinely isn't there yet.
    try:
        deployment = await flyte.with_servecontext(
            project=project, domain="development"
        ).serve.aio(env)
        endpoint = deployment.endpoint
    except Exception as exc:
        logger.warning(f"serve watch unconfirmed for {slug!r}/{mode!r}: {exc}")
        try:
            app = await App.get.aio(
                name=f"nb-{slug}-{mode}", project=project, domain="development"
            )
            endpoint = app.endpoint
        except Exception:
            endpoint = None
        if not endpoint:
            return JSONResponse(
                {"error": "notebook is still starting; please retry in a moment"},
                status_code=503,
            )
    _launched[session.github_username][(slug, mode)] = endpoint

    # Hand off the signed session as a one-shot query param. The admin and the
    # per-notebook live on sibling subdomains and intentionally use host-only
    # cookies (no shared `Domain=` parent) so a notebook can't read the admin's
    # cookie. The proxy validates `sg_launch` on first hit, sets its own
    # host-only cookie, and 302s back to the clean URL.
    launch_token = request.cookies.get(SESSION_COOKIE, "")
    handoff = f"{endpoint}?sg_launch={launch_token}"

    # AJAX clients get the URL as JSON immediately so the dashboard tile can
    # start polling `/launch/status` for readiness and swap the spinner for
    # an "Open" link once the pod responds. Plain form submissions (no JS)
    # still get the 303 redirect.
    if wants_json:
        return JSONResponse({"url": handoff})
    return RedirectResponse(handoff, status_code=303)


@asgi_app.post("/stop")
async def stop(
    request: Request,
    slug: str = Form(...),
    mode: str = Form(...),
):
    """Deactivate the per-notebook app for the requested slug+mode."""
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if mode not in ("edit", "run"):
        return JSONResponse({"error": f"invalid mode: {mode}"}, status_code=400)

    project = sanitize_project_id(session.github_username)
    name = f"nb-{slug}-{mode}"
    try:
        app = await App.get.aio(name=name, project=project, domain="development")
        await app.deactivate.aio()
    except Exception as exc:
        logger.error(f"Stop failed for {name!r} in {project!r}: {exc}")
        return JSONResponse({"error": f"stop failed: {exc}"}, status_code=502)

    _launched.get(session.github_username, {}).pop((slug, mode), None)
    return JSONResponse({"stopped": True})


@asgi_app.get("/launch/status")
async def launch_status(request: Request):
    """Report which of the user's notebooks have an active per-notebook app.

    For each candidate `nb-{slug}-{mode}` (Tutorials/Community from the
    registry, plus the user's Workspace notebooks), query Flyte in the user's
    project and keep the ones that are active with a public endpoint. Queried
    live from the control plane, so it's authoritative and survives admin
    restarts (unlike the in-memory `_launched`). The dashboard calls this on
    load to render running notebooks straight to Open+Stop instead of a fresh
    Edit/Run that would re-spin.
    """
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    project = sanitize_project_id(session.github_username)
    cookie_value = request.cookies.get(SESSION_COOKIE, "")
    slugs = await _candidate_slugs(session, cookie_value)

    async def _probe(slug: str, mode: str) -> dict | None:
        """Return run info for `nb-{slug}-{mode}` if it's active, else None."""
        try:
            app = await App.get.aio(
                name=f"nb-{slug}-{mode}", project=project, domain="development"
            )
        except Exception:
            return None  # not found / not deployed
        if app.is_active() and app.endpoint:
            return {"slug": slug, "mode": mode, "url": app.endpoint}
        return None

    probes = [_probe(slug, mode) for slug in slugs for mode in ("edit", "run")]
    running = [r for r in await asyncio.gather(*probes) if r is not None]
    return JSONResponse({"running": running})


@asgi_app.post("/workspace/save")
async def workspace_save(
    request: Request, slug: str = Form(...), mode: str = Form(...)
):
    """Commit + push a running notebook's workspace to the user's fork.

    Per-notebook (not global) because each pod owns its own `/workspace`
    clone — syncing one pod can't clobber another's edits. The admin resolves
    that pod's `App.endpoint` and calls its `/__sg__/workspace/sync`
    (server-to-server, with the session cookie).
    """
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not session.workspace_enabled:
        return JSONResponse({"error": "enable workspace saving first"}, status_code=403)
    if mode not in ("edit", "run"):
        return JSONResponse({"error": f"invalid mode: {mode}"}, status_code=400)

    project = sanitize_project_id(session.github_username)
    try:
        app = await App.get.aio(
            name=f"nb-{slug}-{mode}", project=project, domain="development"
        )
    except Exception:
        return JSONResponse({"error": "notebook not running"}, status_code=409)
    if not app.is_active() or not app.endpoint:
        return JSONResponse({"error": "notebook not running"}, status_code=409)

    cookie = request.cookies.get(SESSION_COOKIE, "")
    sync_url = f"{app.endpoint.rstrip('/')}/__sg__/workspace/sync"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.post(sync_url, cookies={SESSION_COOKIE: cookie})
    except Exception as exc:
        logger.warning(f"Save sync failed for {slug!r}/{mode!r}: {exc}")
        return JSONResponse(
            {"error": f"could not reach notebook: {exc}"}, status_code=502
        )
    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"sync failed (HTTP {resp.status_code})"}, status_code=502
        )
    try:
        return JSONResponse(resp.json())
    except Exception:
        return JSONResponse({"status": "ok"})


@asgi_app.post("/workspace/cleanup")
async def workspace_cleanup(request: Request):
    """Delete the user's stopped (deactivated) per-notebook app deployments.

    Stopping a notebook (or deleting one) deactivates its app but leaves the
    deployment record. This lists EVERY `nb-*` app in the user's project — not
    just those still on the dashboard — and removes the deactivated ones, so a
    deleted notebook's leftover stopped app is cleaned up too. The list only
    discovers names; each is re-fetched with `App.get` for authoritative status
    before deletion. Active/idle apps are left alone. Returns the deleted names.
    """
    session = _get_session(request)
    if session is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    project = sanitize_project_id(session.github_username)
    try:
        apps = await list_project_apps(project, domain="development")
    except Exception as exc:
        logger.error(f"cleanup listing failed for {project!r}: {exc}")
        return JSONResponse({"error": f"cleanup failed: {exc}"}, status_code=502)

    names = [app.name for app in apps if app.name.startswith("nb-")]

    async def _cleanup(name: str) -> str | None:
        """Delete `name` if it's still a deactivated deployment."""
        try:
            app = await App.get.aio(name=name, project=project, domain="development")
        except Exception:
            return None  # vanished between list and get
        if not app.is_deactivated():
            return None
        try:
            await App.delete.aio(name=name, project=project, domain="development")
            return name
        except Exception as exc:
            logger.warning(f"cleanup delete failed for {name!r}: {exc}")
            return None

    results = await asyncio.gather(*(_cleanup(name) for name in names))
    deleted = [r for r in results if r is not None]
    return JSONResponse({"deleted": deleted, "count": len(deleted)})


@asgi_app.post("/workspace/pod-token")
async def pod_token(request: Request):
    """Mint a fresh, fork-scoped git token for a notebook pod (callback-fetch).

    The pod authenticates with the `SESSION_SECRET`-signed capability injected
    as `SG_POD_TOKEN` (Authorization: Bearer), *not* a GitHub credential. We
    verify it, then mint a short-lived installation token scoped to that one
    fork and return it as plain text for the pod's `GIT_ASKPASS` clone/push.
    The token is minted at use and never persisted in the pod spec or
    `.git/config`. A bad/expired capability is a 401; an un-installed fork (no
    GitHub App) is a 502.
    """
    auth = request.headers.get("Authorization", "")
    capability = auth[7:] if auth.lower().startswith("bearer ") else ""
    fork_full_name = read_pod_capability(capability, _env("SESSION_SECRET"))
    if not fork_full_name:
        return PlainTextResponse("unauthorized", status_code=401)
    try:
        token = await installation_tokens.fork_token(fork_full_name)
    except Exception as exc:
        logger.warning(f"pod-token mint failed for {fork_full_name!r}: {exc}")
        return PlainTextResponse("could not mint token", status_code=502)
    return PlainTextResponse(token)


@asgi_app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Deploy entrypoint
# ---------------------------------------------------------------------------


def _build_and_push_notebook_image() -> None:
    """Build the per-notebook recipe and publish it under a stable tag.

    The admin pod cannot build images itself, so the deployer's machine
    must publish `notebook-app` to `STARGAZER_REGISTRY` before any
    user's `/launch` click can spawn a per-notebook app — the
    AppEnvironment image reference resolves at pod-pull time, not at
    deploy time.

    `flyte.build` produces a content-hashed URI; we then retag that
    manifest as `NOTEBOOK_IMAGE_URI` (`.../notebook-app:stable`) via
    `docker buildx imagetools create` so the multi-arch manifest is
    preserved without re-pulling. Per-notebook AppEnvironments reference
    the stable tag (`Image.from_base(NOTEBOOK_IMAGE_URI)`) so the admin
    pod never needs to recompute the content hash from in-pod Python
    state.
    """
    logger.info("Building per-notebook flyte.Image (recipe)")
    result = flyte.build(notebook_app_img_recipe)
    if result.uri is None:
        raise RuntimeError("flyte.build did not return an image URI")
    logger.info(f"Retagging {result.uri} as {NOTEBOOK_IMAGE_URI}")
    subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "-t",
            NOTEBOOK_IMAGE_URI,
            result.uri,
        ],
        check=True,
    )
    logger.info(f"Per-notebook image published at {NOTEBOOK_IMAGE_URI}")


def _start_storage_port_forward() -> None:
    """Open `localhost:9000 → svc/rustfs-svc:9000` for devbox deploys.

    `flyte-binary` returns signed upload URLs with host `rustfs-svc.flyte:9000`
    (so admin App pods can reach storage via k8s DNS, matching production
    behaviour). On the deployer's laptop that hostname only works if it
    resolves to `127.0.0.1` (NAS DNS or `/etc/hosts`) AND there's a
    port-forward to the in-cluster service.

    Skip silently if the port is already serving (e.g. user ran their
    own port-forward) or if `kubectl` is missing (production deploy).
    """
    if _port_open("127.0.0.1", 9000):
        logger.info("Storage port 9000 already open; skipping port-forward")
        return
    try:
        proc = subprocess.Popen(
            ["kubectl", "port-forward", "-n", "flyte", "svc/rustfs-svc", "9000:9000"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning("kubectl not found; skipping storage port-forward")
        return
    atexit.register(proc.terminate)
    for _ in range(20):
        if _port_open("127.0.0.1", 9000):
            logger.info("Storage port-forward established on localhost:9000")
            return
        time.sleep(0.25)
    raise RuntimeError("kubectl port-forward did not open localhost:9000 within 5s")


def _port_open(host: str, port: int) -> bool:
    """Return True if a TCP connect to host:port succeeds quickly."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def main():
    """Deploy the admin landing app to Flyte."""
    missing = [name for name in _SECRET_NAMES if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            f"Missing required env vars: {', '.join(missing)}. "
            f"Export them before deploying."
        )
    init(root_dir=PROJECT_ROOT)
    _start_storage_port_forward()
    _build_and_push_notebook_image()
    deployment = flyte.serve(app_env)
    print(f"App URL: {deployment.endpoint}")


if __name__ == "__main__":
    main()
