"""
### GitHub repo operations beyond OAuth.

Helpers for the per-user fork that backs the Workspace section: forking the
upstream `stargazer` repo into the authenticated user's account, and listing,
reading, creating, and deleting notebook files under `notebooks/workspace/`
on the fork's `main`. Snapshotting *moves* a notebook into `notebooks/snapshots/`
(`create_snapshot_notebook` + `delete_workspace_notebook`). Fork and delete are
idempotent. Every write is guarded against ever targeting the upstream source
(`is_genuine_fork`, plus an upstream-name check before create/delete).

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import base64
import os

import aiohttp


GITHUB_API_BASE = "https://api.github.com"
WORKSPACE_CONTENTS_PATH = "src/stargazer/notebooks/workspace"
SNAPSHOTS_CONTENTS_PATH = "src/stargazer/notebooks/snapshots"

# User notebooks live and persist on the fork's default branch. Edits are
# confined to WORKSPACE_CONTENTS_PATH (the proxy only `git add`s that dir),
# so the fork's `main` never collides with upstream's shipped files — no side
# branch needed. See docs/architecture/app.md.
WORKSPACE_BRANCH = "main"


def upstream_repo() -> tuple[str, str]:
    """Resolve the canonical upstream `(owner, name)` for forking.

    Read from `STARGAZER_UPSTREAM_REPO` (format `owner/name`); defaults
    to the public-org canonical path.
    """
    spec = os.environ.get("STARGAZER_UPSTREAM_REPO", "StargazerBio/stargazer")
    owner, _, name = spec.partition("/")
    if not owner or not name:
        raise ValueError(
            f"STARGAZER_UPSTREAM_REPO must look like 'owner/name', got {spec!r}"
        )
    return owner, name


def upstream_full_name() -> str:
    """Return the upstream repo as an `owner/name` string."""
    owner, name = upstream_repo()
    return f"{owner}/{name}"


def canonical_fork_name(username: str) -> str:
    """The deterministic fork location: `{username}/{upstream_name}`.

    Stargazer treats this as the *only* valid fork. GitHub names a fork after
    its upstream, so this is where both `find_existing_fork` (detection) and
    `/workspace/enable` (creation) look — keeping them in lockstep. If the name
    is already taken on the user's account, Workspace saving is simply
    unavailable; we don't fall back to a renamed fork (`stargazer-1`).
    """
    _, name = upstream_repo()
    return f"{username}/{name}"


def is_genuine_fork(repo: dict) -> bool:
    """True if `repo` is a real fork we may safely write to.

    Guards the transfer-redirect and self-fork cases: a repo whose path
    redirects to (or *is*) the upstream source comes back as the source —
    not a fork the user owns. We require GitHub's `fork` flag and a
    `full_name` distinct from the upstream. Writing to anything else risks
    clobbering the shared upstream (see docs/architecture/app.md).
    """
    full = (repo.get("full_name") or "").lower()
    return (
        repo.get("fork") is True
        and "/" in full
        and full != upstream_full_name().lower()
    )


async def fork_upstream(access_token: str) -> dict:
    """Idempotently fork the upstream stargazer repo into the token holder's account.

    GitHub's `POST /repos/{owner}/{repo}/forks` returns the existing fork
    if one already exists, so this is safe to call on every login.

    Returns the fork payload (dict with at least `owner.login`, `name`,
    `clone_url`). Raises `aiohttp.ClientResponseError` on transport or
    auth failure.
    """
    owner, name = upstream_repo()
    url = f"{GITHUB_API_BASE}/repos/{owner}/{name}/forks"
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        resp.raise_for_status()
        return await resp.json()


async def find_existing_fork(access_token: str, username: str) -> dict | None:
    """Return the user's fork of the upstream repo if it exists, else None.

    Detection-only — no fork is created (that stays the opt-in
    `/workspace/enable` action). Restores Workspace saving for a returning user:
    the fork persists on GitHub, but the session cookie is minted fresh each
    login.

    A fork's location is deterministic (`canonical_fork_name`), so this is a
    single lookup — fetch that repo and keep it only if it's a genuine fork of
    our upstream. We don't chase collision-renamed forks; `enable` refuses to
    create one off the canonical name, so detection and creation stay in
    lockstep. Any non-200 / network error yields None (saving stays off).
    """
    _, name = upstream_repo()
    url = f"{GITHUB_API_BASE}/repos/{username}/{name}"
    async with aiohttp.ClientSession() as session:
        resp = await session.get(url, headers=_auth_headers(access_token))
        if resp.status != 200:
            return None
        repo = await resp.json()
    if not is_genuine_fork(repo):
        return None
    # Confirm it's a fork of *our* upstream, not some other repo of the same
    # name the user happens to have forked.
    upstream = upstream_full_name().lower()
    forked_from = {
        ((repo.get(key) or {}).get("full_name") or "").lower()
        for key in ("parent", "source")
    }
    return repo if upstream in forked_from else None


async def list_workspace(fork_full_name: str, access_token: str) -> list[str]:
    """List `.py` files under `notebooks/workspace/` in the user's fork.

    Cold-case fallback used by the admin dashboard when no per-notebook
    pod is up for the user. `fork_full_name` is the verified `owner/repo`
    of the fork. Reads from the fork's `WORKSPACE_BRANCH` (where the proxy's
    `/__sg__/workspace/sync` pushes). Returns an empty list if the directory
    doesn't exist yet.
    """
    url = f"{GITHUB_API_BASE}/repos/{fork_full_name}/contents/{WORKSPACE_CONTENTS_PATH}"
    async with aiohttp.ClientSession() as session:
        resp = await session.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"ref": WORKSPACE_BRANCH},
        )
        if resp.status == 404:
            return []
        resp.raise_for_status()
        data = await resp.json()
    return sorted(
        item["name"]
        for item in data
        if item.get("type") == "file"
        and item.get("name", "").endswith(".py")
        and not item["name"].startswith("_")
    )


async def list_snapshots(fork_full_name: str, access_token: str) -> list[str]:
    """List `.py` files under `notebooks/snapshots/` in the user's fork.

    Snapshots are frozen records that live only on the fork's `main` — no
    per-notebook pod ever serves them — so unlike `list_workspace` there's no
    live-pod path; the dashboard reads them straight from GitHub. Returns an
    empty list if the directory doesn't exist yet. Mirrors `list_workspace`'s
    filtering (`.py`, non-`_`-prefixed) so the `.gitkeep` placeholder is skipped.
    """
    url = f"{GITHUB_API_BASE}/repos/{fork_full_name}/contents/{SNAPSHOTS_CONTENTS_PATH}"
    async with aiohttp.ClientSession() as session:
        resp = await session.get(
            url,
            headers=_auth_headers(access_token),
            params={"ref": WORKSPACE_BRANCH},
        )
        if resp.status == 404:
            return []
        resp.raise_for_status()
        data = await resp.json()
    return sorted(
        item["name"]
        for item in data
        if item.get("type") == "file"
        and item.get("name", "").endswith(".py")
        and not item["name"].startswith("_")
    )


async def _get_file(fork_full_name: str, access_token: str, path: str) -> str | None:
    """Fetch a file's decoded UTF-8 source from the fork's `WORKSPACE_BRANCH`.

    Shared by `get_workspace_notebook` and `get_snapshot_notebook`. Returns
    None if the file is absent (404); other transport errors propagate so
    callers can fall back to defaults.
    """
    url = f"{GITHUB_API_BASE}/repos/{fork_full_name}/contents/{path}"
    async with aiohttp.ClientSession() as session:
        resp = await session.get(
            url, headers=_auth_headers(access_token), params={"ref": WORKSPACE_BRANCH}
        )
        if resp.status == 404:
            return None
        resp.raise_for_status()
        data = await resp.json()
        return base64.b64decode(data["content"]).decode("utf-8")


async def get_workspace_notebook(
    fork_full_name: str, access_token: str, filename: str
) -> str | None:
    """Fetch a workspace notebook's source from the fork's `WORKSPACE_BRANCH`.

    `fork_full_name` is the verified `owner/repo` of the fork. Returns the
    decoded UTF-8 source, or None if the file is absent. Used by `/launch` to
    read a notebook's `[tool.stargazer]` resource block before serving the
    pod, and by `/workspace/create` for collision + seed lookups.
    """
    return await _get_file(
        fork_full_name, access_token, f"{WORKSPACE_CONTENTS_PATH}/{filename}"
    )


async def get_snapshot_notebook(
    fork_full_name: str, access_token: str, filename: str
) -> str | None:
    """Fetch a frozen snapshot's source from the fork's `WORKSPACE_BRANCH`.

    Companion to `get_workspace_notebook` for `notebooks/snapshots/`. Used by
    `/launch` to read a snapshot's `[tool.stargazer]` resources before serving
    its read-only run pod. Returns the decoded source, or None if absent.
    """
    return await _get_file(
        fork_full_name, access_token, f"{SNAPSHOTS_CONTENTS_PATH}/{filename}"
    )


def _auth_headers(access_token: str) -> dict:
    """Standard authenticated GitHub API headers."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _ensure_ok(resp: aiohttp.ClientResponse, action: str) -> None:
    """Raise a descriptive error if `resp` failed, surfacing GitHub's reason.

    `aiohttp`'s `raise_for_status` only carries the bare status line, which
    hides the actual cause (e.g. missing scope, or `Resource not accessible`).
    Pull GitHub's JSON `message` and the granted token scopes so launch /
    create failures point at the real problem.
    """
    if resp.status < 400:
        return
    try:
        detail = (await resp.json()).get("message", "")
    except Exception:
        detail = (await resp.text())[:200]
    scopes = resp.headers.get("X-OAuth-Scopes", "")
    raise RuntimeError(
        f"{action}: GitHub {resp.status} ({detail}); token scopes=[{scopes}]"
    )


async def _create_file(
    fork_full_name: str,
    access_token: str,
    path: str,
    content: str,
    message: str,
    action: str,
) -> dict:
    """Create a new file at `path` on the fork's `WORKSPACE_BRANCH`.

    Shared by `create_workspace_notebook` and `create_snapshot_notebook`. As a
    final safety net, refuses to write if `fork_full_name` resolves to the
    upstream source — a mis-set fork must never clobber the shared repo. Writes
    to the literal `/repos/{full_name}` path WITHOUT following redirects, so a
    transfer redirect surfaces as an error rather than silently retargeting
    upstream. Assumes the caller has checked the file does not exist (no `sha`
    is sent, so GitHub rejects an overwrite). Returns the Contents API payload.
    """
    if fork_full_name.lower() == upstream_full_name().lower():
        raise RuntimeError(
            f"refusing to write to the upstream source repo {fork_full_name!r}"
        )
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": WORKSPACE_BRANCH,
    }
    url = f"{GITHUB_API_BASE}/repos/{fork_full_name}/contents/{path}"
    async with aiohttp.ClientSession() as session:
        resp = await session.put(
            url,
            headers=_auth_headers(access_token),
            json=payload,
            allow_redirects=False,
        )
        if 300 <= resp.status < 400:
            raise RuntimeError(
                f"{action}: {fork_full_name!r} redirected "
                f"(HTTP {resp.status}) — not a writable fork path"
            )
        await _ensure_ok(resp, action)
        return await resp.json()


async def create_workspace_notebook(
    fork_full_name: str,
    access_token: str,
    filename: str,
    content: str,
    message: str | None = None,
) -> dict:
    """Create a new notebook file under `notebooks/workspace/` on the fork.

    Thin wrapper over `_create_file` targeting `WORKSPACE_CONTENTS_PATH`; see
    that helper for the upstream-source and redirect guards. Returns the
    Contents API payload.
    """
    return await _create_file(
        fork_full_name,
        access_token,
        f"{WORKSPACE_CONTENTS_PATH}/{filename}",
        content,
        message or f"workspace: create {filename}",
        "write notebook",
    )


async def create_snapshot_notebook(
    fork_full_name: str,
    access_token: str,
    filename: str,
    content: str,
    message: str | None = None,
) -> dict:
    """Create a frozen notebook under `notebooks/snapshots/` on the fork.

    The write half of a snapshot *move*: the admin reads a workspace notebook's
    source and re-creates it verbatim here, then deletes the workspace original
    (`delete_workspace_notebook`). Thin wrapper over `_create_file` targeting
    `SNAPSHOTS_CONTENTS_PATH` — same upstream-source and redirect guards.
    Returns the Contents API payload.
    """
    return await _create_file(
        fork_full_name,
        access_token,
        f"{SNAPSHOTS_CONTENTS_PATH}/{filename}",
        content,
        message or f"snapshot: freeze {filename}",
        "write snapshot",
    )


async def update_workspace_notebook(
    fork_full_name: str,
    access_token: str,
    filename: str,
    content: str,
    message: str | None = None,
) -> dict:
    """Overwrite an existing notebook file on the fork's `WORKSPACE_BRANCH`.

    Like `create_workspace_notebook` but for a file that already exists: the
    Contents API needs the current blob `sha` to replace it, so this fetches it
    first (404 → raises, the caller should have verified existence). Same
    upstream-source and redirect guards as create. Returns the Contents payload.
    Used by `/workspace/settings` to persist edited resources/description.
    """
    if fork_full_name.lower() == upstream_full_name().lower():
        raise RuntimeError(
            f"refusing to write to the upstream source repo {fork_full_name!r}"
        )
    path = f"{WORKSPACE_CONTENTS_PATH}/{filename}"
    url = f"{GITHUB_API_BASE}/repos/{fork_full_name}/contents/{path}"
    async with aiohttp.ClientSession() as session:
        head = await session.get(
            url, headers=_auth_headers(access_token), params={"ref": WORKSPACE_BRANCH}
        )
        await _ensure_ok(head, "update notebook (lookup)")
        sha = (await head.json())["sha"]

        payload = {
            "message": message or f"workspace: update {filename}",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": WORKSPACE_BRANCH,
        }
        resp = await session.put(
            url,
            headers=_auth_headers(access_token),
            json=payload,
            allow_redirects=False,
        )
        if 300 <= resp.status < 400:
            raise RuntimeError(
                f"update notebook: {fork_full_name!r} redirected "
                f"(HTTP {resp.status}) — not a writable fork path"
            )
        await _ensure_ok(resp, "update notebook")
        return await resp.json()


async def _delete_file(
    fork_full_name: str,
    access_token: str,
    path: str,
    message: str,
    action: str,
) -> bool:
    """Delete the file at `path` from the fork's `WORKSPACE_BRANCH`. Idempotent.

    Shared by `delete_workspace_notebook` and `delete_snapshot_notebook`. The
    Contents API requires the file's current blob `sha`, so this fetches it
    first; if the file is already absent, returns False without erroring. As
    with `_create_file`, refuses to touch the upstream source and never follows
    redirects, so a transfer redirect surfaces as an error. Returns True if a
    file was deleted.
    """
    if fork_full_name.lower() == upstream_full_name().lower():
        raise RuntimeError(
            f"refusing to write to the upstream source repo {fork_full_name!r}"
        )
    url = f"{GITHUB_API_BASE}/repos/{fork_full_name}/contents/{path}"
    async with aiohttp.ClientSession() as session:
        head = await session.get(
            url, headers=_auth_headers(access_token), params={"ref": WORKSPACE_BRANCH}
        )
        if head.status == 404:
            return False
        await _ensure_ok(head, f"{action} (lookup)")
        sha = (await head.json())["sha"]

        payload = {
            "message": message,
            "sha": sha,
            "branch": WORKSPACE_BRANCH,
        }
        resp = await session.delete(
            url,
            headers=_auth_headers(access_token),
            json=payload,
            allow_redirects=False,
        )
        if 300 <= resp.status < 400:
            raise RuntimeError(
                f"{action}: {fork_full_name!r} redirected "
                f"(HTTP {resp.status}) — not a writable fork path"
            )
        await _ensure_ok(resp, action)
        return True


async def delete_workspace_notebook(
    fork_full_name: str,
    access_token: str,
    filename: str,
    message: str | None = None,
) -> bool:
    """Delete a notebook under `notebooks/workspace/` on the fork. Idempotent.

    Thin wrapper over `_delete_file` targeting `WORKSPACE_CONTENTS_PATH`; see
    that helper for the upstream-source and redirect guards. Returns True if a
    file was deleted, False if it was already absent.
    """
    return await _delete_file(
        fork_full_name,
        access_token,
        f"{WORKSPACE_CONTENTS_PATH}/{filename}",
        message or f"workspace: delete {filename}",
        "delete notebook",
    )


async def delete_snapshot_notebook(
    fork_full_name: str,
    access_token: str,
    filename: str,
    message: str | None = None,
) -> bool:
    """Delete a frozen notebook under `notebooks/snapshots/` on the fork. Idempotent.

    Thin wrapper over `_delete_file` targeting `SNAPSHOTS_CONTENTS_PATH`; see
    that helper for the upstream-source and redirect guards. Returns True if a
    file was deleted, False if it was already absent.
    """
    return await _delete_file(
        fork_full_name,
        access_token,
        f"{SNAPSHOTS_CONTENTS_PATH}/{filename}",
        message or f"snapshot: delete {filename}",
        "delete snapshot",
    )
