"""Tests for the admin app: AppEnvironment, session model, and routes.

Route tests use FastAPI's `TestClient` instantiated WITHOUT the context
manager so the app's lifespan (`init()` → Flyte client) never runs — the
routes under test return before any control-plane call. A `SESSION_SECRET`
is injected per test and signed session cookies are minted with the real
`app.session` helpers so the auth path exercises production code.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.admin_app import _dashboard_context, _workspace_tiles, asgi_app
from app.session import (
    SESSION_COOKIE,
    SessionData,
    create_session_cookie,
    read_pod_capability,
    read_session_cookie,
    sign_pod_capability,
)


SECRET = "test-session-secret"


@pytest.fixture
def secret_env(monkeypatch):
    """Set SESSION_SECRET so routes can read and sign the session cookie."""
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    return SECRET


@pytest.fixture
def client():
    """A TestClient that does not trigger the app lifespan (no Flyte init)."""
    return TestClient(asgi_app)


def _auth(
    client: TestClient,
    *,
    fork_full_name: str = "",
    access_token: str = "",
    app_installed: bool | None = None,
) -> None:
    """Attach a signed session cookie for `octocat` to the client's jar.

    `app_installed` defaults to True whenever a fork is given, so tests that
    just want an *enabled* session (`fork_full_name=…`) stay enabled without
    spelling it out. Pass it explicitly to model a half-finished opt-in.
    """
    if app_installed is None:
        app_installed = bool(fork_full_name)
    data = SessionData(
        "octocat",
        123,
        fork_full_name=fork_full_name,
        access_token=access_token,
        app_installed=app_installed,
    )
    client.cookies.set(SESSION_COOKIE, create_session_cookie(data, SECRET))


INSTALL_TOKEN = "ghs_installation_token"


def _stub_fork_token(monkeypatch, token: str = INSTALL_TOKEN) -> str:
    """Patch the GitHub-App path so post-fork ops use a minted installation token.

    Post-fork admin-side GitHub reads/writes mint via
    `installation_tokens.fork_token(session.fork_full_name)`, never
    `session.access_token`. Stubbing it here keeps the network out of the test
    and lets callers assert the *minted* token is what reaches the op.
    """

    async def fake(fork_full_name):
        return token

    monkeypatch.setattr("app.installation_tokens.fork_token", fake)
    return token


# ---------------------------------------------------------------------------
# AppEnvironment / entrypoint
# ---------------------------------------------------------------------------


def test_app_env_is_valid_app_environment():
    """app_env is a properly configured AppEnvironment."""
    from flyte.app import AppEnvironment

    from app.admin_app import app_env

    assert isinstance(app_env, AppEnvironment)
    assert app_env.name == "admin-app"
    assert app_env.get_port().port == 8080


def test_main_function_exists():
    """admin_app exposes a main() entry point."""
    from app.admin_app import main

    assert callable(main)


# ---------------------------------------------------------------------------
# SessionData.workspace_enabled
# ---------------------------------------------------------------------------


def test_workspace_enabled_false_by_default():
    """A fresh session (no fork) reports workspace saving as off."""
    assert SessionData("u", 1).workspace_enabled is False


def test_workspace_enabled_requires_fork_and_install():
    """Opt-in needs both a fork and a confirmed App install — and no token."""
    # Fork + install, token already dropped → enabled.
    assert (
        SessionData(
            "u", 1, fork_full_name="u/stargazer", app_installed=True
        ).workspace_enabled
        is True
    )
    # Forked but install abandoned → still off.
    assert SessionData("u", 1, fork_full_name="u/stargazer").workspace_enabled is False
    # A token (pre-fork window) without a fork → off.
    assert SessionData("u", 1, access_token="t").workspace_enabled is False


def test_fork_owner_derived_from_full_name():
    """fork_owner is derived from fork_full_name's owner segment."""
    assert SessionData("u", 1, fork_full_name="alice/stargazer-1").fork_owner == "alice"
    assert SessionData("u", 1).fork_owner == ""


# ---------------------------------------------------------------------------
# Pod capability sign / verify
# ---------------------------------------------------------------------------


def test_pod_capability_roundtrips_fork_name():
    """A signed capability verifies back to its fork name."""
    cap = sign_pod_capability("octocat/stargazer", SECRET)
    assert read_pod_capability(cap, SECRET) == "octocat/stargazer"


def test_pod_capability_rejects_tamper_and_wrong_secret():
    """A bad signature / wrong secret yields None, not the fork name."""
    cap = sign_pod_capability("octocat/stargazer", SECRET)
    assert read_pod_capability(cap, "different-secret") is None
    assert read_pod_capability(cap + "x", SECRET) is None


def test_pod_capability_not_confused_with_session_cookie():
    """A session cookie can't be replayed as a pod capability (distinct salt)."""
    cookie = create_session_cookie(
        SessionData("octocat", 1, fork_full_name="octocat/stargazer"), SECRET
    )
    assert read_pod_capability(cookie, SECRET) is None


# ---------------------------------------------------------------------------
# Workspace tile assembly
# ---------------------------------------------------------------------------


_WS_SRC = (
    "# /// script\n"
    '# dependencies = ["marimo"]\n'
    "#\n"
    "# [tool.stargazer]\n"
    "# cpu = 3\n"
    '# memory = "5Gi"\n'
    '# description = "Tile blurb"\n'
    "# ///\n"
    "import marimo\n"
)


def _ws_session():
    """A session with Workspace saving enabled (fork + token + app installed)."""
    return SessionData(
        "octocat",
        123,
        fork_full_name="octocat/stargazer",
        access_token="oauth_tok",
        app_installed=True,
    )


def _stub_ws_fetch(monkeypatch, source: str = _WS_SRC):
    """Stub the per-notebook source fetch the tile builder parses metadata from."""

    async def fake_fetch(fork_full_name, token, filename):
        return source

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    _stub_fork_token(monkeypatch)


async def test_workspace_tiles_excludes_template(monkeypatch):
    """The shipped template is never rendered as a tile."""
    _stub_ws_fetch(monkeypatch)
    assert await _workspace_tiles(_ws_session(), ["template.py"]) == []


async def test_workspace_tiles_lists_user_notebooks_with_meta(monkeypatch):
    """User notebooks tile (template filtered) and carry parsed resources/blurb."""
    _stub_ws_fetch(monkeypatch)
    tiles = await _workspace_tiles(_ws_session(), ["template.py", "my_analysis.py"])
    assert [t["slug"] for t in tiles] == ["my_analysis"]
    assert (tiles[0]["cpu"], tiles[0]["memory"]) == (3, 5)
    assert tiles[0]["description"] == "Tile blurb"


async def test_dashboard_context_off_has_no_workspace_tiles():
    """When opt-in is off, no Workspace tiles are built."""
    ctx = await _dashboard_context(SessionData("octocat", 123), ["foo.py"])
    assert ctx["workspace_enabled"] is False
    assert ctx["workspace"] == []


async def test_dashboard_context_on_builds_workspace_tiles(monkeypatch):
    """When opt-in is on, only the user's own notebooks become tiles."""
    _stub_ws_fetch(monkeypatch)
    ctx = await _dashboard_context(_ws_session(), ["template.py", "foo.py"])
    assert ctx["workspace_enabled"] is True
    assert [t["slug"] for t in ctx["workspace"]] == ["foo"]


async def test_dashboard_context_no_snapshots_is_empty():
    """With no snapshot files, the Snapshots section is empty."""
    ctx = await _dashboard_context(_ws_session(), [])
    assert ctx["snapshots"] == []


async def test_dashboard_context_lists_snapshot_tiles():
    """Snapshot files become read-only snapshots-section tiles."""
    ctx = await _dashboard_context(
        _ws_session(), [], snapshot_files=["my-analysis.py", "old-run.py"]
    )
    snaps = ctx["snapshots"]
    assert [t["slug"] for t in snaps] == ["my-analysis", "old-run"]
    assert all(t["section"] == "snapshots" for t in snaps)


# ---------------------------------------------------------------------------
# Workspace listing routes through the installation token (post-fork read)
# ---------------------------------------------------------------------------


async def test_resolve_workspace_files_uses_installation_token(monkeypatch):
    """The GitHub listing fallback mints a fork-scoped token, not access_token."""
    from app import admin_app

    used: dict = {}

    async def fake_list(fork_full_name, token):
        used.update(fork=fork_full_name, token=token)
        return ["my_analysis.py"]

    monkeypatch.setattr(admin_app, "gh_list_workspace", fake_list)
    _stub_fork_token(monkeypatch)

    session = SessionData(
        "octocat",
        123,
        fork_full_name="octocat/stargazer",
        access_token="oauth_tok",
        app_installed=True,
    )
    files = await admin_app._resolve_workspace_files(session, cookie_value="")

    assert files == ["my_analysis.py"]
    assert used == {"fork": "octocat/stargazer", "token": INSTALL_TOKEN}


# ---------------------------------------------------------------------------
# /launch gating
# ---------------------------------------------------------------------------


def test_launch_requires_session(secret_env, client):
    """Unauthenticated /launch is rejected with 401."""
    resp = client.post(
        "/launch",
        data={"slug": "assets", "mode": "edit", "section": "tutorials"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 401


def test_launch_workspace_blocked_without_optin(secret_env, client):
    """Workspace launches are gated behind opt-in: 403 when saving is off."""
    _auth(client)  # logged in, but no fork → saving off
    resp = client.post(
        "/launch",
        data={"slug": "template", "mode": "edit", "section": "workspace"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 403
    assert "enable workspace saving" in resp.json()["error"].lower()


def test_launch_invalid_mode_rejected(secret_env, client):
    """An invalid launch mode is rejected with 400."""
    _auth(client)
    resp = client.post(
        "/launch",
        data={"slug": "assets", "mode": "bogus", "section": "tutorials"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /workspace/enable opt-in
# ---------------------------------------------------------------------------


def test_workspace_enable_requires_session(secret_env, client):
    """Enabling without a session redirects home and sets no cookie."""
    resp = client.post("/workspace/enable", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert resp.cookies.get(SESSION_COOKIE) is None


def test_workspace_enable_forks_and_sets_cookie(secret_env, client, monkeypatch):
    """A genuine fork is verified and recorded, but saving stays off until install.

    Enable only completes the *first* half of opt-in (the fork). The session
    records `fork_full_name` but `workspace_enabled` stays False until the user
    finishes the GitHub App install (the `/auth/app-install-callback`); so a
    user who abandons the install isn't shown as enabled.
    """

    async def fake_fork(_token):
        return {
            "fork": True,
            "full_name": "octocat/stargazer",
            "owner": {"login": "octocat"},
        }

    monkeypatch.setattr("app.admin_app.fork_upstream", fake_fork)
    _auth(client, access_token="gho_token")  # logged in, not yet enabled

    resp = client.post("/workspace/enable", follow_redirects=False)
    assert resp.status_code == 303

    new_cookie = resp.cookies.get(SESSION_COOKIE)
    assert new_cookie is not None
    session = read_session_cookie(new_cookie, SECRET)
    assert session.fork_full_name == "octocat/stargazer"
    assert session.app_installed is False
    assert session.workspace_enabled is False  # pending the App install


def test_workspace_enable_refuses_collision_fork(secret_env, client, monkeypatch):
    """A collision fork (`stargazer-1`) is refused — only the canonical name.

    Detection at login only looks at the canonical `{user}/stargazer`, so
    recording an alias would silently break saving on the next login. We refuse
    instead and keep the two paths in lockstep.
    """

    async def fake_fork(_token):
        return {
            "fork": True,
            "full_name": "octocat/stargazer-1",
            "owner": {"login": "octocat"},
        }

    monkeypatch.setattr("app.admin_app.fork_upstream", fake_fork)
    _auth(client, access_token="gho_token")

    resp = client.post("/workspace/enable", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?ws_error=fork"
    assert resp.cookies.get(SESSION_COOKIE) is None  # saving stays off


def test_workspace_enable_refuses_non_fork(secret_env, client, monkeypatch):
    """If forking returns the upstream source (transfer redirect), refuse."""

    async def fake_fork(_token):
        # Mimics POST /forks resolving to the source repo, not a fork.
        return {
            "fork": False,
            "full_name": "StargazerBio/stargazer",
            "owner": {"login": "StargazerBio"},
        }

    monkeypatch.setattr("app.admin_app.fork_upstream", fake_fork)
    _auth(client, access_token="gho_token")

    resp = client.post("/workspace/enable", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?ws_error=fork"
    assert resp.cookies.get(SESSION_COOKIE) is None  # saving stays off


def test_workspace_enable_failure_leaves_saving_off(secret_env, client, monkeypatch):
    """If the fork call errors, the session is untouched (saving stays off)."""

    async def boom(_token):
        raise RuntimeError("github down")

    monkeypatch.setattr("app.admin_app.fork_upstream", boom)
    _auth(client, access_token="gho_token")

    resp = client.post("/workspace/enable", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.cookies.get(SESSION_COOKIE) is None  # no re-signed cookie


def test_workspace_enable_redirects_to_app_install(secret_env, client, monkeypatch):
    """With an App configured, a successful fork redirects to its install page."""

    async def fake_fork(_token):
        return {
            "fork": True,
            "full_name": "octocat/stargazer",
            "owner": {"login": "octocat"},
        }

    monkeypatch.setattr("app.admin_app.fork_upstream", fake_fork)
    monkeypatch.setattr("app.config.GITHUB_APP_SLUG", "stargazer-workspaces")
    _auth(client, access_token="gho_token")

    resp = client.post("/workspace/enable", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == (
        "https://github.com/apps/stargazer-workspaces/installations/new"
    )
    # The fork is already recorded before the user leaves to install.
    session = read_session_cookie(resp.cookies.get(SESSION_COOKIE), SECRET)
    assert session.fork_full_name == "octocat/stargazer"


# ---------------------------------------------------------------------------
# /auth/app-install-callback — finish opt-in, drop the OAuth token
# ---------------------------------------------------------------------------


def test_app_install_callback_confirms_install_and_drops_token(secret_env, client):
    """The callback flips on the install (enabling saving) and clears the token."""
    # Mid-flow: forked, OAuth token kept, install not yet confirmed → off.
    _auth(
        client,
        fork_full_name="octocat/stargazer",
        access_token="gho_token",
        app_installed=False,
    )

    resp = client.get("/auth/app-install-callback", follow_redirects=False)
    assert resp.status_code == 302
    session = read_session_cookie(resp.cookies.get(SESSION_COOKIE), SECRET)
    assert session.app_installed is True
    assert session.workspace_enabled is True  # now both halves of opt-in are done
    assert session.access_token == ""  # spent OAuth token dropped


def test_app_install_callback_without_session_redirects_home(secret_env, client):
    """No session → just bounce home, set no cookie."""
    resp = client.get("/auth/app-install-callback", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.cookies.get(SESSION_COOKIE) is None


# ---------------------------------------------------------------------------
# /auth/callback — restore Workspace saving for returning users
# ---------------------------------------------------------------------------


def _patch_oauth(monkeypatch):
    """Stub the OAuth handshake so /auth/callback reaches the fork lookup."""

    async def fake_exchange(**_kw):
        return "gho_token"

    async def fake_user(_token):
        return {"login": "octocat", "id": 123}

    async def fake_provision(github_username):
        return None

    monkeypatch.setattr("app.admin_app.exchange_code", fake_exchange)
    monkeypatch.setattr("app.admin_app.get_github_user", fake_user)
    monkeypatch.setattr("app.admin_app.provision_user", fake_provision)


def _callback(client):
    """Drive /auth/callback with a matching oauth_state cookie."""
    client.cookies.set("oauth_state", "xyz")
    return client.get("/auth/callback?code=abc&state=xyz", follow_redirects=False)


def test_callback_restores_saving_for_returning_fork(secret_env, client, monkeypatch):
    """A returning user whose fork exists AND App is installed gets saving back."""
    _patch_oauth(monkeypatch)

    async def fake_find(_token, _username):
        return {"full_name": "octocat/stargazer"}

    async def fake_install_id(_owner):
        return 42  # App still installed on the fork

    monkeypatch.setattr("app.admin_app.find_existing_fork", fake_find)
    monkeypatch.setattr("app.installation_tokens.get_installation_id", fake_install_id)

    resp = _callback(client)
    session = read_session_cookie(resp.cookies.get(SESSION_COOKIE), SECRET)
    assert session.fork_full_name == "octocat/stargazer"
    assert session.app_installed is True
    assert session.workspace_enabled is True
    # Returning user is already enabled+installed → no OAuth token kept.
    assert session.access_token == ""


def test_callback_fork_but_uninstalled_app_is_not_enabled(
    secret_env, client, monkeypatch
):
    """A fork whose App install is gone → saving off, OAuth token kept to retry."""
    _patch_oauth(monkeypatch)

    async def fake_find(_token, _username):
        return {"full_name": "octocat/stargazer"}

    async def boom(_owner):
        raise RuntimeError("404 not installed")

    monkeypatch.setattr("app.admin_app.find_existing_fork", fake_find)
    monkeypatch.setattr("app.installation_tokens.get_installation_id", boom)

    resp = _callback(client)
    session = read_session_cookie(resp.cookies.get(SESSION_COOKIE), SECRET)
    assert session.fork_full_name == "octocat/stargazer"
    assert session.app_installed is False
    assert session.workspace_enabled is False
    assert session.access_token == "gho_token"  # kept so they can re-enable


def test_callback_first_time_keeps_token_for_enable(secret_env, client, monkeypatch):
    """A first-time user (no fork) keeps the OAuth token to fork at Enable."""
    _patch_oauth(monkeypatch)

    async def fake_find(_token, _username):
        return None

    monkeypatch.setattr("app.admin_app.find_existing_fork", fake_find)

    resp = _callback(client)
    session = read_session_cookie(resp.cookies.get(SESSION_COOKIE), SECRET)
    assert session.fork_full_name == ""
    assert session.access_token == "gho_token"


def test_callback_saving_off_when_no_fork(secret_env, client, monkeypatch):
    """A first-time user (no fork yet) starts with saving off."""
    _patch_oauth(monkeypatch)

    async def fake_find(_token, _username):
        return None

    monkeypatch.setattr("app.admin_app.find_existing_fork", fake_find)

    resp = _callback(client)
    session = read_session_cookie(resp.cookies.get(SESSION_COOKIE), SECRET)
    assert session.fork_full_name == ""
    assert session.workspace_enabled is False


def test_callback_fork_lookup_failure_does_not_block_login(
    secret_env, client, monkeypatch
):
    """A failing fork lookup must not break login — saving just stays off."""
    _patch_oauth(monkeypatch)

    async def boom(_token, _username):
        raise RuntimeError("github down")

    monkeypatch.setattr("app.admin_app.find_existing_fork", boom)

    resp = _callback(client)
    assert resp.status_code == 302
    session = read_session_cookie(resp.cookies.get(SESSION_COOKIE), SECRET)
    assert session.workspace_enabled is False


# ---------------------------------------------------------------------------
# /stop
# ---------------------------------------------------------------------------


def test_stop_requires_session(secret_env, client):
    """Unauthenticated /stop is rejected with 401."""
    resp = client.post("/stop", data={"slug": "assets", "mode": "edit"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /launch/status
# ---------------------------------------------------------------------------


class _FakeApp:
    """Stand-in for a flyte.remote.App in status tests."""

    def __init__(self, active: bool, endpoint: str):
        self._active, self._endpoint = active, endpoint

    def is_active(self) -> bool:
        """Whether the app is deployed and active."""
        return self._active

    @property
    def endpoint(self) -> str:
        """The app's public endpoint URL."""
        return self._endpoint


def _stub_app_get(monkeypatch, table: dict, deleted: list | None = None):
    """Patch admin_app.App so App.get.aio resolves names from `table`.

    App.delete.aio records deleted names into `deleted` when provided, so
    teardown paths (e.g. /workspace/delete) can be asserted.
    """

    class _Get:
        async def aio(self, name, project, domain):
            if name in table:
                return table[name]
            raise RuntimeError("not found")

    class _Delete:
        async def aio(self, name, project, domain):
            if deleted is not None:
                deleted.append(name)

    monkeypatch.setattr(
        "app.admin_app.App", SimpleNamespace(get=_Get(), delete=_Delete())
    )


def test_launch_status_requires_session(secret_env, client):
    """Unauthenticated /launch/status is rejected with 401."""
    resp = client.get("/launch/status")
    assert resp.status_code == 401


def test_launch_status_reports_only_active_apps(secret_env, client, monkeypatch):
    """Status returns active per-notebook apps with their endpoints."""
    _stub_app_get(monkeypatch, {"nb-assets-edit": _FakeApp(True, "http://nb.example")})

    _auth(client)  # no fork → workspace off → registry notebooks only
    resp = client.get("/launch/status", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    running = resp.json()["running"]
    assert running == [{"slug": "assets", "mode": "edit", "url": "http://nb.example"}]


def test_launch_status_skips_inactive_apps(secret_env, client, monkeypatch):
    """A deployed-but-inactive app is not reported as running."""
    _stub_app_get(monkeypatch, {"nb-assets-edit": _FakeApp(False, "http://nb.example")})

    _auth(client)
    resp = client.get("/launch/status", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    assert resp.json()["running"] == []


# ---------------------------------------------------------------------------
# /workspace/save and /workspace/cleanup
# ---------------------------------------------------------------------------


def test_save_requires_session(secret_env, client):
    """Unauthenticated /workspace/save is rejected with 401."""
    resp = client.post("/workspace/save", data={"slug": "foo", "mode": "edit"})
    assert resp.status_code == 401


def test_save_requires_optin(secret_env, client):
    """Saving without workspace saving enabled is rejected with 403."""
    _auth(client)
    resp = client.post("/workspace/save", data={"slug": "foo", "mode": "edit"})
    assert resp.status_code == 403


def test_save_409_when_not_running(secret_env, client, monkeypatch):
    """Saving a notebook with no active pod is rejected with 409."""
    _stub_app_get(monkeypatch, {})  # App.get raises -> not running
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/save",
        data={"slug": "foo", "mode": "edit"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 409


def test_save_posts_to_app_endpoint(secret_env, client, monkeypatch):
    """Save POSTs the sync request to the notebook app's public endpoint."""
    from app import admin_app

    public = "http://nb-foo-edit-octocat-development.devbox.stargazer.bio"
    _stub_app_get(monkeypatch, {"nb-foo-edit": _FakeApp(True, public)})

    posted: dict = {}

    class _FakeAsyncClient:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, cookies=None):
            posted["url"] = url
            return SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

    monkeypatch.setattr(admin_app.httpx, "AsyncClient", _FakeAsyncClient)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/save",
        data={"slug": "foo", "mode": "edit"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert posted["url"] == f"{public}/__sg__/workspace/sync"


def test_cleanup_requires_session(secret_env, client):
    """Unauthenticated /workspace/cleanup is rejected with 401."""
    resp = client.post("/workspace/cleanup")
    assert resp.status_code == 401


def test_cleanup_deletes_deactivated_apps_regardless_of_dashboard(
    secret_env, client, monkeypatch
):
    """Cleanup lists every nb-* app in the project and deletes the deactivated
    ones — including a deleted notebook's leftover that's on no dashboard list,
    while skipping active apps and non-notebook deployments."""

    class _App:
        def __init__(self, name, deactivated):
            self._name = name
            self._deactivated = deactivated

        @property
        def name(self):
            return self._name

        def is_deactivated(self):
            return self._deactivated

    # A deleted notebook's stopped app (in no registry/workspace listing), an
    # active app, and a non-notebook deployment.
    table = {
        "nb-deleted-edit": _App("nb-deleted-edit", True),
        "nb-running-run": _App("nb-running-run", False),
        "other-service": _App("other-service", True),
    }

    async def fake_list(project, domain="development", limit=500):
        return list(table.values())

    monkeypatch.setattr("app.admin_app.list_project_apps", fake_list)

    deleted: list = []

    class _Get:
        async def aio(self, name, project, domain):
            return table[name]

    class _Delete:
        async def aio(self, name, project, domain):
            deleted.append(name)

    monkeypatch.setattr(
        "app.admin_app.App", SimpleNamespace(get=_Get(), delete=_Delete())
    )

    _auth(client)
    resp = client.post("/workspace/cleanup", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": ["nb-deleted-edit"], "count": 1}
    assert deleted == ["nb-deleted-edit"]


def test_cleanup_listing_failure_returns_502(secret_env, client, monkeypatch):
    """If listing the project's apps fails, cleanup reports an error, not a 500."""

    async def boom(project, domain="development", limit=500):
        raise RuntimeError("control plane down")

    monkeypatch.setattr("app.admin_app.list_project_apps", boom)

    _auth(client)
    resp = client.post("/workspace/cleanup", headers={"Accept": "application/json"})
    assert resp.status_code == 502
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# /workspace/pod-token — callback-fetch git token for notebook pods
# ---------------------------------------------------------------------------


def test_pod_token_rejects_missing_capability(secret_env, client):
    """No/!bearer capability is a 401 — the endpoint mints nothing."""
    resp = client.post("/workspace/pod-token")
    assert resp.status_code == 401


def test_pod_token_rejects_bad_capability(secret_env, client):
    """A capability that doesn't verify is a 401."""
    resp = client.post(
        "/workspace/pod-token",
        headers={"Authorization": "Bearer not-a-real-capability"},
    )
    assert resp.status_code == 401


def test_pod_token_mints_fork_scoped_token(secret_env, client, monkeypatch):
    """A valid capability mints a fork-scoped token, returned as plain text."""
    minted: dict = {}

    async def fake_fork_token(fork_full_name):
        minted.update(fork_full_name=fork_full_name)
        return "ghs_pod_scoped"

    monkeypatch.setattr("app.installation_tokens.fork_token", fake_fork_token)
    cap = sign_pod_capability("octocat/stargazer", SECRET)

    resp = client.post(
        "/workspace/pod-token", headers={"Authorization": f"Bearer {cap}"}
    )
    assert resp.status_code == 200
    assert resp.text == "ghs_pod_scoped"
    # Scoped to exactly the capability's fork, never a session/access token.
    assert minted == {"fork_full_name": "octocat/stargazer"}


def test_pod_token_502_when_app_not_installed(secret_env, client, monkeypatch):
    """If minting fails (fork has no GitHub App install), report 502."""

    async def boom(fork_full_name):
        raise RuntimeError("not installed")

    monkeypatch.setattr("app.installation_tokens.fork_token", boom)
    cap = sign_pod_capability("octocat/stargazer", SECRET)

    resp = client.post(
        "/workspace/pod-token", headers={"Authorization": f"Bearer {cap}"}
    )
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# /launch — workspace resource propagation
# ---------------------------------------------------------------------------


def _stub_serve(monkeypatch, sink: dict):
    """Replace flyte.with_servecontext so /launch never hits a control plane.

    The fake captures the served AppEnvironment in `sink['env']` and returns
    a deployment with a fixed endpoint.
    """

    async def fake_aio(env):
        sink["env"] = env
        return SimpleNamespace(endpoint="http://nb.example")

    ctx = SimpleNamespace(serve=SimpleNamespace(aio=fake_aio))
    monkeypatch.setattr("flyte.with_servecontext", lambda **_: ctx)


def test_launch_workspace_applies_notebook_resources(secret_env, client, monkeypatch):
    """A workspace launch parses [tool.stargazer] and serves those resources."""
    source = (
        "# /// script\n"
        '# dependencies = ["marimo"]\n'
        "#\n"
        "# [tool.stargazer]\n"
        "# cpu = 2\n"
        '# memory = "3Gi"\n'
        "# ///\n"
        "import marimo\n"
    )

    async def fake_fetch(_owner, _token, _filename):
        return source

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    _stub_fork_token(monkeypatch)
    sink: dict = {}
    _stub_serve(monkeypatch, sink)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/launch",
        data={"slug": "analysis", "mode": "edit", "section": "workspace"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("http://nb.example")

    served = sink["env"]
    assert served.resources.cpu == 2
    assert served.resources.memory == "3Gi"


def test_launch_workspace_missing_source_uses_defaults(secret_env, client, monkeypatch):
    """If the notebook source can't be fetched, default resources are used."""

    async def fake_fetch(_owner, _token, _filename):
        return None

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    _stub_fork_token(monkeypatch)
    sink: dict = {}
    _stub_serve(monkeypatch, sink)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/launch",
        data={"slug": "analysis", "mode": "edit", "section": "workspace"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200

    from app.notebook_meta import DEFAULT_RESOURCES

    served = sink["env"]
    assert served.resources.cpu == DEFAULT_RESOURCES.cpu
    assert served.resources.memory == DEFAULT_RESOURCES.memory


def test_launch_snapshot_requires_optin(secret_env, client):
    """Snapshot launches clone the fork, so they require opt-in: 403 when off."""
    _auth(client)  # logged in, no fork → saving off
    resp = client.post(
        "/launch",
        data={"slug": "frozen", "mode": "run", "section": "snapshots"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 403
    assert "enable workspace saving" in resp.json()["error"].lower()


def test_launch_snapshot_rejects_edit_mode(secret_env, client):
    """A frozen snapshot opens read-only: edit mode is rejected with 400."""
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/launch",
        data={"slug": "frozen", "mode": "edit", "section": "snapshots"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 400


def test_launch_snapshot_run_serves_from_snapshots_dir(secret_env, client, monkeypatch):
    """A snapshot run launch serves the frozen file from the snapshots dir.

    The notebook is read from `notebooks/snapshots/`, its `[tool.stargazer]`
    resources are honored, and the pod is told to `marimo run` that path.
    """
    source = (
        '# /// script\n# dependencies = ["marimo"]\n#\n'
        '# [tool.stargazer]\n# cpu = 2\n# memory = "3Gi"\n# ///\nimport marimo\n'
    )

    async def fake_fetch(_owner, _token, _filename):
        return source

    monkeypatch.setattr("app.admin_app.get_snapshot_notebook", fake_fetch)
    _stub_fork_token(monkeypatch)
    sink: dict = {}
    _stub_serve(monkeypatch, sink)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/launch",
        data={"slug": "frozen", "mode": "run", "section": "snapshots"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200

    served = sink["env"]
    # run mode, frozen file resolved under the snapshots dir.
    assert served.args[1] == "run"
    assert served.args[2].endswith("notebooks/snapshots/frozen.py")
    assert served.resources.cpu == 2
    assert served.resources.memory == "3Gi"


async def test_candidate_slugs_includes_snapshots(monkeypatch):
    """Snapshot slugs join workspace + registry slugs so /launch/status (and
    cleanup) probe their run pods too — a running snapshot hydrates to Open/Stop.
    """
    from app import admin_app

    async def fake_ws(_session, _cookie):
        return []

    async def fake_snaps(_session):
        return ["frozen.py"]

    monkeypatch.setattr(admin_app, "_resolve_workspace_files", fake_ws)
    monkeypatch.setattr(admin_app, "_resolve_snapshot_files", fake_snaps)
    slugs = await admin_app._candidate_slugs(_ws_session(), cookie_value="")
    assert "frozen" in slugs


# ---------------------------------------------------------------------------
# /workspace/create
# ---------------------------------------------------------------------------


def _create_form(**overrides):
    """Default form fields for a create request."""
    form = {"name": "My Analysis", "source": "blank", "cpu": "2", "memory": "3Gi"}
    form.update(overrides)
    return form


def test_create_requires_session(secret_env, client):
    """Unauthenticated create is rejected with 401."""
    resp = client.post("/workspace/create", data=_create_form())
    assert resp.status_code == 401


def test_create_requires_optin(secret_env, client):
    """Create without workspace saving enabled is rejected with 403."""
    _auth(client)  # logged in, not opted in
    resp = client.post("/workspace/create", data=_create_form())
    assert resp.status_code == 403


def test_create_rejects_reserved_name(secret_env, client):
    """The reserved 'template' name is rejected with 400."""
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post("/workspace/create", data=_create_form(name="Template"))
    assert resp.status_code == 400


def test_create_conflict_when_notebook_exists(secret_env, client, monkeypatch):
    """A name that already exists on the fork is rejected with 409."""

    async def fake_fetch(_owner, _token, _filename):
        return "# existing notebook\n"

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    _stub_fork_token(monkeypatch)
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post("/workspace/create", data=_create_form(name="taken"))
    assert resp.status_code == 409


def test_create_blank_writes_file_and_returns_slug(secret_env, client, monkeypatch):
    """A blank create copies the blank seed, injects resources, returns slug."""
    blank_seed = (
        '# /// script\n# dependencies = ["marimo", "stargazer"]\n# ///\nimport marimo\n'
    )
    written: dict = {}

    async def fake_fetch(_owner, _token, filename):
        # No collision for the new name; return the blank seed for blank.py.
        return blank_seed if filename == "blank.py" else None

    async def fake_create(owner, token, filename, content, message=None):
        written.update(filename=filename, content=content, token=token)
        return {"content": {"name": filename}}

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    monkeypatch.setattr("app.admin_app.create_workspace_notebook", fake_create)
    token = _stub_fork_token(monkeypatch)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/create",
        data=_create_form(name="My Analysis", cpu="2", memory="3Gi"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "my-analysis"
    # The write used the fork-scoped installation token, never session.access_token.
    assert written["token"] == token
    # Create returns a ready-to-insert tile that behaves like any other.
    assert 'name="slug" value="my-analysis"' in body["tile_html"]
    assert "launch-form" in body["tile_html"]

    assert written["filename"] == "my-analysis.py"
    # The written file is a runnable marimo notebook carrying the resources.
    from app.notebook_meta import NotebookResources, parse_notebook_resources

    assert "import marimo" in written["content"]
    assert parse_notebook_resources(written["content"]) == NotebookResources(
        cpu=2, memory="3Gi"
    )


def test_create_from_template_injects_resources(secret_env, client, monkeypatch):
    """A template create fetches template.py and injects chosen resources."""
    template_src = (
        '# /// script\n# dependencies = ["marimo", "stargazer"]\n# ///\nimport marimo\n'
    )
    written: dict = {}

    async def fake_fetch(_owner, _token, filename):
        # No collision for the new name; return the template for template.py.
        return template_src if filename == "template.py" else None

    async def fake_create(_owner, _token, filename, content, message=None):
        written.update(filename=filename, content=content)
        return {}

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    monkeypatch.setattr("app.admin_app.create_workspace_notebook", fake_create)
    _stub_fork_token(monkeypatch)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/create",
        data=_create_form(name="from tmpl", source="template", cpu="1", memory="2Gi"),
    )
    assert resp.status_code == 200

    from app.notebook_meta import NotebookResources, parse_notebook_resources

    assert written["filename"] == "from-tmpl.py"
    assert parse_notebook_resources(written["content"]) == NotebookResources(
        cpu=1, memory="2Gi"
    )


# ---------------------------------------------------------------------------
# /workspace/settings
# ---------------------------------------------------------------------------


def test_settings_requires_session(secret_env, client):
    """Unauthenticated settings is rejected with 401."""
    resp = client.post(
        "/workspace/settings", data={"slug": "foo", "cpu": "2", "memory": "4"}
    )
    assert resp.status_code == 401


def test_settings_rejects_reserved_slug(secret_env, client):
    """The reserved 'template' slug can't be edited (400)."""
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/settings",
        data={"slug": "template", "cpu": "2", "memory": "4"},
    )
    assert resp.status_code == 400


def test_settings_writes_header_and_returns_normalized(secret_env, client, monkeypatch):
    """Settings rewrites the notebook's header and echoes normalized values."""
    src = (
        '# /// script\n# dependencies = ["marimo"]\n'
        '#\n# [tool.stargazer]\n# cpu = 1\n# memory = "2Gi"\n# ///\nimport marimo\n'
    )
    written: dict = {}

    async def fake_fetch(_owner, _token, filename):
        return src if filename == "foo.py" else None

    async def fake_update(_owner, _token, filename, content, message=None):
        written.update(filename=filename, content=content)
        return {}

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    monkeypatch.setattr("app.admin_app.update_workspace_notebook", fake_update)
    _stub_fork_token(monkeypatch)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/settings",
        data={"slug": "foo", "cpu": "4", "memory": "8", "description": "  My  blurb "},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "slug": "foo",
        "cpu": 4,
        "memory": 8,
        "description": "My blurb",
    }

    from app.notebook_meta import (
        NotebookResources,
        parse_notebook_description,
        parse_notebook_resources,
    )

    assert written["filename"] == "foo.py"
    assert parse_notebook_resources(written["content"]) == NotebookResources(
        cpu=4, memory="8Gi"
    )
    assert parse_notebook_description(written["content"]) == "My blurb"


def test_settings_missing_notebook_is_404(secret_env, client, monkeypatch):
    """Editing a notebook that isn't on the fork returns 404."""

    async def fake_fetch(_owner, _token, _filename):
        return None

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    _stub_fork_token(monkeypatch)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/settings",
        data={"slug": "ghost", "cpu": "2", "memory": "4"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /workspace/delete
# ---------------------------------------------------------------------------


def test_delete_requires_session(secret_env, client):
    """Unauthenticated delete is rejected with 401."""
    resp = client.post("/workspace/delete", data={"slug": "foo"})
    assert resp.status_code == 401


def test_delete_requires_optin(secret_env, client):
    """Delete without workspace saving enabled is rejected with 403."""
    _auth(client)  # logged in, not opted in
    resp = client.post("/workspace/delete", data={"slug": "foo"})
    assert resp.status_code == 403


def test_delete_rejects_seed_slug(secret_env, client):
    """Deleting a shipped seed (blank/template) is rejected with 400."""
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post("/workspace/delete", data={"slug": "template"})
    assert resp.status_code == 400


def test_delete_removes_notebook(secret_env, client, monkeypatch):
    """Delete removes the file from the fork and reports the slug."""
    deleted: dict = {}

    async def fake_delete(repo, token, filename, message=None):
        deleted.update(repo=repo, filename=filename, token=token)
        return True

    monkeypatch.setattr(
        "app.admin_app.delete_workspace_notebook", fake_delete, raising=False
    )
    token = _stub_fork_token(monkeypatch)
    _stub_app_get(monkeypatch, {})  # no running pod to deactivate
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/delete",
        data={"slug": "my-analysis"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["slug"] == "my-analysis"
    # The delete used the fork-scoped installation token, never session.access_token.
    assert deleted == {
        "repo": "octocat/stargazer",
        "filename": "my-analysis.py",
        "token": token,
    }


def test_delete_tears_down_pod_deployment(secret_env, client, monkeypatch):
    """Delete deactivates AND deletes the deployment for each running mode."""
    deactivated: list = []
    deleted_apps: list = []

    class _RunningApp:
        def __init__(self, name):
            self.name = name
            self.deactivate = SimpleNamespace(aio=self._deactivate)

        async def _deactivate(self):
            deactivated.append(self.name)

    table = {
        "nb-my-analysis-edit": _RunningApp("nb-my-analysis-edit"),
        "nb-my-analysis-run": _RunningApp("nb-my-analysis-run"),
    }

    async def fake_delete(repo, token, filename, message=None):
        return True

    monkeypatch.setattr(
        "app.admin_app.delete_workspace_notebook", fake_delete, raising=False
    )
    _stub_fork_token(monkeypatch)
    _stub_app_get(monkeypatch, table, deleted=deleted_apps)
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/delete",
        data={"slug": "my-analysis"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert sorted(deactivated) == ["nb-my-analysis-edit", "nb-my-analysis-run"]
    assert sorted(deleted_apps) == ["nb-my-analysis-edit", "nb-my-analysis-run"]


def test_delete_idempotent_when_missing(secret_env, client, monkeypatch):
    """Deleting a notebook absent from the fork still succeeds (idempotent)."""

    async def fake_delete(repo, token, filename, message=None):
        return False  # file not found on the fork

    monkeypatch.setattr(
        "app.admin_app.delete_workspace_notebook", fake_delete, raising=False
    )
    _stub_fork_token(monkeypatch)
    _stub_app_get(monkeypatch, {})
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/delete",
        data={"slug": "ghost"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["slug"] == "ghost"


# ---------------------------------------------------------------------------
# /workspace/snapshot — move a workspace notebook into the snapshots dir
# ---------------------------------------------------------------------------


def test_snapshot_requires_session(secret_env, client):
    """Unauthenticated snapshot is rejected with 401."""
    resp = client.post("/workspace/snapshot", data={"slug": "foo"})
    assert resp.status_code == 401


def test_snapshot_requires_optin(secret_env, client):
    """Snapshot without workspace saving enabled is rejected with 403."""
    _auth(client)  # logged in, not opted in
    resp = client.post("/workspace/snapshot", data={"slug": "foo"})
    assert resp.status_code == 403


def test_snapshot_rejects_seed_slug(secret_env, client):
    """Snapshotting a shipped seed (blank/template) is rejected with 400."""
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post("/workspace/snapshot", data={"slug": "template"})
    assert resp.status_code == 400


def test_snapshot_missing_notebook_is_404(secret_env, client, monkeypatch):
    """Snapshotting a notebook absent from the fork's main returns 404."""

    async def fake_fetch(_owner, _token, _filename):
        return None

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    _stub_fork_token(monkeypatch)
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/snapshot",
        data={"slug": "ghost"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 404


def test_snapshot_moves_notebook_into_snapshots_dir(secret_env, client, monkeypatch):
    """Snapshot MOVES the notebook from workspace/ into snapshots/, frozen.

    It writes the source verbatim under the same `<slug>.py` name in the
    snapshots dir, then deletes the workspace original so the notebook leaves
    the editable surface entirely — a snapshot is no longer a workspace tile.
    Both writes use the fork-scoped installation token, never the OAuth token.
    """
    src = (
        '# /// script\n# dependencies = ["marimo"]\n'
        '#\n# [tool.stargazer]\n# cpu = 2\n# memory = "4Gi"\n# ///\nimport marimo\n'
    )
    written: dict = {}
    deleted: dict = {}

    async def fake_fetch(_owner, _token, filename):
        return src if filename == "my-analysis.py" else None

    async def fake_snapshot(repo, token, filename, content, message=None):
        written.update(repo=repo, token=token, filename=filename, content=content)
        return {}

    async def fake_delete(repo, token, filename, message=None):
        deleted.update(repo=repo, token=token, filename=filename)
        return True

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    monkeypatch.setattr(
        "app.admin_app.create_snapshot_notebook", fake_snapshot, raising=False
    )
    monkeypatch.setattr(
        "app.admin_app.delete_workspace_notebook", fake_delete, raising=False
    )
    token = _stub_fork_token(monkeypatch)
    _stub_app_get(monkeypatch, {})  # no running pod to tear down

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/snapshot",
        data={"slug": "my-analysis"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200

    # Frozen verbatim under the same name, in the snapshots dir.
    assert written["filename"] == "my-analysis.py"
    assert written["content"] == src
    assert written["token"] == token
    # The workspace original is removed — it's a move, not a copy.
    assert deleted["filename"] == "my-analysis.py"
    assert deleted["token"] == token
    # The response reports the snapshotted slug.
    assert resp.json()["slug"] == "my-analysis"


def test_snapshot_returns_rendered_snapshot_tile(secret_env, client, monkeypatch):
    """Snapshot returns a ready-to-insert, read-only snapshots tile.

    The browser drops `tile_html` straight into the Snapshots grid. A frozen
    tile is display-only: it shows the file but carries no launch form (a
    snapshot can't be edited or run from the dashboard).
    """
    src = '# /// script\n# dependencies = ["marimo"]\n# ///\nimport marimo\n'

    async def fake_fetch(_owner, _token, filename):
        return src if filename == "my-analysis.py" else None

    async def fake_snapshot(*_a, **_k):
        return {}

    async def fake_delete(*_a, **_k):
        return True

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    monkeypatch.setattr(
        "app.admin_app.create_snapshot_notebook", fake_snapshot, raising=False
    )
    monkeypatch.setattr(
        "app.admin_app.delete_workspace_notebook", fake_delete, raising=False
    )
    _stub_fork_token(monkeypatch)
    _stub_app_get(monkeypatch, {})

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/snapshot",
        data={"slug": "my-analysis"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    html = resp.json()["tile_html"]
    assert "my-analysis.py" in html
    # Frozen: a read-only Run launch only — no Edit.
    assert 'name="mode" value="run"' in html
    assert 'name="mode" value="edit"' not in html
    assert 'name="section" value="snapshots"' in html


def test_snapshot_tears_down_pod_deployment(secret_env, client, monkeypatch):
    """Snapshot deactivates AND deletes any running pod for the moved notebook.

    Once moved, there's no workspace tile left to Stop the pod, so — exactly
    like delete — snapshot tears down both modes to avoid orphaning a pod.
    """
    src = '# /// script\n# dependencies = ["marimo"]\n# ///\nimport marimo\n'
    deactivated: list = []
    deleted_apps: list = []

    async def fake_fetch(_owner, _token, filename):
        return src if filename == "my-analysis.py" else None

    async def fake_snapshot(repo, token, filename, content, message=None):
        return {}

    async def fake_delete(repo, token, filename, message=None):
        return True

    class _RunningApp:
        def __init__(self, name):
            self.name = name
            self.deactivate = SimpleNamespace(aio=self._deactivate)

        async def _deactivate(self):
            deactivated.append(self.name)

    table = {
        "nb-my-analysis-edit": _RunningApp("nb-my-analysis-edit"),
        "nb-my-analysis-run": _RunningApp("nb-my-analysis-run"),
    }

    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_fetch)
    monkeypatch.setattr(
        "app.admin_app.create_snapshot_notebook", fake_snapshot, raising=False
    )
    monkeypatch.setattr(
        "app.admin_app.delete_workspace_notebook", fake_delete, raising=False
    )
    _stub_fork_token(monkeypatch)
    _stub_app_get(monkeypatch, table, deleted=deleted_apps)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/snapshot",
        data={"slug": "my-analysis"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert sorted(deactivated) == ["nb-my-analysis-edit", "nb-my-analysis-run"]
    assert sorted(deleted_apps) == ["nb-my-analysis-edit", "nb-my-analysis-run"]


# ---------------------------------------------------------------------------
# /workspace/copy — copy a workflow or snapshot into the editable workspace
# ---------------------------------------------------------------------------


def test_copy_requires_session(secret_env, client):
    """Unauthenticated copy is rejected with 401."""
    resp = client.post(
        "/workspace/copy", data={"slug": "scrna-pipeline", "section": "workflows"}
    )
    assert resp.status_code == 401


def test_copy_requires_optin(secret_env, client):
    """Copy without workspace saving enabled is rejected with 403."""
    _auth(client)  # logged in, not opted in
    resp = client.post(
        "/workspace/copy", data={"slug": "scrna-pipeline", "section": "workflows"}
    )
    assert resp.status_code == 403


def test_copy_rejects_uncopyable_section(secret_env, client):
    """Copy only accepts workflows/snapshots sources — tutorials is rejected."""
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/copy", data={"slug": "assets", "section": "tutorials"}
    )
    assert resp.status_code == 400


def test_copy_unknown_workflow_is_404(secret_env, client, monkeypatch):
    """A workflow slug that isn't a registered workflow notebook returns 404."""
    _stub_fork_token(monkeypatch)
    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/copy",
        data={"slug": "ghost", "section": "workflows"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 404


def test_copy_workflow_writes_editable_workspace_notebook(
    secret_env, client, monkeypatch
):
    """Copying a workflow reads it from the fork's source tree and writes it
    into notebooks/workspace/ as an editable notebook keyed by its title.

    The new notebook carries the workflow's display title in its
    `[tool.stargazer]` header and gets the source's parsed resources; the write
    uses the fork-scoped installation token. The returned tile is a normal
    workspace tile (gear + launch forms).
    """
    src = (
        '# /// script\n# dependencies = ["marimo"]\n'
        '#\n# [tool.stargazer]\n# cpu = 2\n# memory = "4Gi"\n# ///\nimport marimo\n'
    )
    fetched: dict = {}
    written: dict = {}

    async def fake_repo_file(repo, token, path):
        fetched.update(repo=repo, token=token, path=path)
        return src

    async def fake_ws_fetch(_repo, _token, _filename):
        return None  # no collision on the target name

    async def fake_create(repo, token, filename, content, message=None):
        written.update(filename=filename, content=content, token=token)
        return {}

    monkeypatch.setattr("app.admin_app.get_repo_file", fake_repo_file)
    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_ws_fetch)
    monkeypatch.setattr("app.admin_app.create_workspace_notebook", fake_create)
    token = _stub_fork_token(monkeypatch)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/copy",
        data={"slug": "scrna-pipeline", "section": "workflows"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()

    # Read from the fork's repo tree (image-workdir prefix stripped).
    assert fetched["path"] == "src/stargazer/notebooks/workflows/scrna_pipeline.py"
    assert fetched["token"] == token
    # Title "scRNA-seq" drives the new slug; the write uses the install token.
    assert body["slug"] == "scrna-seq"
    assert written["filename"] == "scrna-seq.py"
    assert written["token"] == token
    # The copy is a real workspace tile (editable), not a frozen one.
    assert 'name="slug" value="scrna-seq"' in body["tile_html"]
    assert "tile-settings" in body["tile_html"]

    from app.notebook_meta import (
        NotebookResources,
        parse_notebook_name,
        parse_notebook_resources,
    )

    assert parse_notebook_resources(written["content"]) == NotebookResources(
        cpu=2, memory="4Gi"
    )
    assert parse_notebook_name(written["content"]) == "scRNA-seq"


def test_copy_conflict_when_notebook_exists(secret_env, client, monkeypatch):
    """Copying onto a name already in the workspace is rejected with 409.

    Copy reuses create's collision rule — the derived slug must be free, else
    the user renames the existing notebook and copies again.
    """
    src = '# /// script\n# dependencies = ["marimo"]\n# ///\nimport marimo\n'

    async def fake_repo_file(_repo, _token, _path):
        return src

    async def fake_ws_fetch(_repo, _token, _filename):
        return "# existing notebook\n"  # the derived slug is already taken

    monkeypatch.setattr("app.admin_app.get_repo_file", fake_repo_file)
    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_ws_fetch)
    _stub_fork_token(monkeypatch)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/copy",
        data={"slug": "scrna-pipeline", "section": "workflows"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 409


def test_copy_snapshot_writes_editable_workspace_notebook(
    secret_env, client, monkeypatch
):
    """Copying a snapshot reads it from snapshots/ and writes it editable.

    The snapshot's own `[tool.stargazer]` name/description/resources carry over
    onto the new workspace notebook.
    """
    src = (
        '# /// script\n# dependencies = ["marimo"]\n'
        "#\n# [tool.stargazer]\n"
        '# name = "My Saved Run"\n# cpu = 1\n# memory = "2Gi"\n'
        '# description = "A frozen analysis"\n# ///\nimport marimo\n'
    )
    fetched: dict = {}
    written: dict = {}

    async def fake_snapshot_fetch(repo, token, filename):
        fetched.update(repo=repo, token=token, filename=filename)
        return src

    async def fake_ws_fetch(_repo, _token, _filename):
        return None  # no collision on the target name

    async def fake_create(_repo, _token, filename, content, message=None):
        written.update(filename=filename, content=content)
        return {}

    monkeypatch.setattr("app.admin_app.get_snapshot_notebook", fake_snapshot_fetch)
    monkeypatch.setattr("app.admin_app.get_workspace_notebook", fake_ws_fetch)
    monkeypatch.setattr("app.admin_app.create_workspace_notebook", fake_create)
    _stub_fork_token(monkeypatch)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/copy",
        data={"slug": "my-analysis", "section": "snapshots"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert fetched["filename"] == "my-analysis.py"
    # The new slug comes from the snapshot's stored display name.
    assert body["slug"] == "my-saved-run"
    assert written["filename"] == "my-saved-run.py"

    from app.notebook_meta import (
        NotebookResources,
        parse_notebook_description,
        parse_notebook_name,
        parse_notebook_resources,
    )

    assert parse_notebook_name(written["content"]) == "My Saved Run"
    assert parse_notebook_description(written["content"]) == "A frozen analysis"
    assert parse_notebook_resources(written["content"]) == NotebookResources(
        cpu=1, memory="2Gi"
    )


def test_copy_missing_snapshot_source_is_404(secret_env, client, monkeypatch):
    """Copying a snapshot absent from the fork returns 404."""

    async def fake_snapshot_fetch(_repo, _token, _filename):
        return None

    monkeypatch.setattr("app.admin_app.get_snapshot_notebook", fake_snapshot_fetch)
    _stub_fork_token(monkeypatch)

    _auth(client, fork_full_name="octocat/stargazer", access_token="tok")
    resp = client.post(
        "/workspace/copy",
        data={"slug": "ghost", "section": "snapshots"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 404
