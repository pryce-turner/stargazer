"""
### Admin-side GitHub App client — mints fork-scoped installation tokens.

The trust anchor for the hardened credential model (see plan 18). The admin
app holds one long-lived secret, the **GitHub App private key**, and uses it
to mint short-lived (~1h), repository-scoped *installation tokens* on demand.
Those tokens — never the broad user OAuth token — are what ongoing GitHub
operations and notebook pods get to touch.

Primitives:

- `_app_jwt()` signs a ~9-minute RS256 JWT (`iss` = app id) from the private
  key. This authenticates *as the app* to the `GET /users/{owner}/installation`
  and `POST /app/installations/{id}/access_tokens` endpoints.
- `get_installation_id(owner)` resolves a user's installation of the app
  (cached per owner).
- `mint_installation_token(installation_id, repositories)` exchanges the app
  JWT for an installation token scoped to specific repositories *by name*,
  cached in-process until shortly before it expires.
- `fork_token(fork_full_name)` ties the two together: the single entry point
  the admin app calls for every post-fork GitHub op (`list`/`get`/`create`/
  `delete`, plus the pod's token mint) in place of the broad user OAuth token.

The fork step itself (`fork_upstream`) and login-time fork detection
(`find_existing_fork`) stay on the OAuth token — they predate the install.

All GitHub calls ride the shared pooled client (`app.http_client`).

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import os
import time
from datetime import UTC, datetime

import jwt

from app import http_client

GITHUB_API_BASE = "https://api.github.com"

# A GitHub App JWT may live at most 10 minutes; we use 9 to leave headroom for
# the 60s backdated `iat` (clock-drift guard) and any transport latency.
_JWT_LIFETIME_SECONDS = 9 * 60
_JWT_BACKDATE_SECONDS = 60

# Re-mint an installation token once it's within this margin of expiry, so a
# token handed to a caller has comfortably more than a moment of validity left.
_TOKEN_REFRESH_MARGIN_SECONDS = 300

# In-process cache: (installation_id, repo_names) -> (token, expiry_epoch).
# Volatile across admin restarts, which is fine — tokens are cheap to re-mint.
_token_cache: dict[tuple[int, tuple[str, ...]], tuple[str, float]] = {}

# In-process cache: owner -> installation_id. Needed so a token-cache hit
# doesn't still cost a `GET /users/{owner}/installation` to form the cache key.
# A user who uninstalls + reinstalls gets a new id; an admin restart clears
# this, and `get_installation_id` is cheap, so staleness self-heals.
_installation_id_cache: dict[str, int] = {}


def _app_jwt() -> str:
    """Sign a short-lived RS256 JWT authenticating as the GitHub App.

    Reads `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY` (a PEM string) from the
    environment. The `iat` claim is backdated 60s to tolerate clock skew and
    `exp` is set 9 minutes out (GitHub caps app JWTs at 10). Raises
    `RuntimeError` if either env var is missing.
    """
    app_id = os.environ.get("GITHUB_APP_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    if not app_id or not private_key:
        raise RuntimeError(
            "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY must be set to mint "
            "GitHub App tokens"
        )
    now = int(time.time())
    claims = {
        "iat": now - _JWT_BACKDATE_SECONDS,
        "exp": now + _JWT_LIFETIME_SECONDS,
        "iss": app_id,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


def _app_headers() -> dict:
    """Headers authenticating as the app (JWT bearer) for app-level endpoints."""
    return {
        "Authorization": f"Bearer {_app_jwt()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_installation_id(owner: str) -> int:
    """Resolve `owner`'s installation id of this GitHub App (cached per owner).

    `GET /users/{owner}/installation`, authenticated as the app. The
    installation id is *not* a secret — callers may persist it in the session.
    Cached in `_installation_id_cache` so repeat mints don't re-look-up. Raises
    `httpx.HTTPStatusError` if the user hasn't installed the app (404) or
    on transport failure.
    """
    cached = _installation_id_cache.get(owner)
    if cached is not None:
        return cached
    url = f"{GITHUB_API_BASE}/users/{owner}/installation"
    # Follow redirects here: GitHub 301s this path when the owner's login has
    # been renamed, and httpx's `raise_for_status` treats a 3xx as an error.
    # Without this a renamed account reads as "app not installed", silently
    # turning Workspace saving off. No write happens here, so there is no
    # redirect-retarget risk (writes keep redirects off — see `app.github`).
    resp = await http_client.client().get(
        url, headers=_app_headers(), follow_redirects=True
    )
    resp.raise_for_status()
    data = resp.json()
    _installation_id_cache[owner] = data["id"]
    return data["id"]


def _expiry_to_epoch(expires_at: str) -> float:
    """Parse a GitHub ISO-8601 `expires_at` (`...Z`) into a UTC epoch float."""
    dt = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return dt.timestamp()


async def mint_installation_token(
    installation_id: int, repositories: list[str]
) -> tuple[str, str]:
    """Mint a token scoped to `repositories` (names), returning `(token, expires_at)`.

    `POST /app/installations/{id}/access_tokens` with a `repositories` body
    (short repo names, e.g. `["stargazer"]`), authenticated by the app JWT, so
    the resulting ~1h token can reach only the listed repositories (the user's
    fork) — never every repo the installation covers. Scoping by name avoids
    capturing the fork's numeric id (we always have its name). Results are
    cached per `(installation_id, repositories)` until within
    `_TOKEN_REFRESH_MARGIN_SECONDS` of expiry; a cached, still-fresh token is
    returned without a GitHub round-trip. `expires_at` is GitHub's ISO-8601
    string, surfaced so callers can reason about lifetime.
    """
    key = (installation_id, tuple(sorted(repositories)))
    cached = _token_cache.get(key)
    if cached is not None:
        token, expiry_epoch = cached
        if expiry_epoch - time.time() > _TOKEN_REFRESH_MARGIN_SECONDS:
            return token, _epoch_to_iso(expiry_epoch)

    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    resp = await http_client.client().post(
        url, headers=_app_headers(), json={"repositories": list(repositories)}
    )
    resp.raise_for_status()
    data = resp.json()

    token, expires_at = data["token"], data["expires_at"]
    _token_cache[key] = (token, _expiry_to_epoch(expires_at))
    return token, expires_at


async def fork_token(fork_full_name: str) -> str:
    """Mint a fork-scoped installation token for a fork's `owner/name`.

    The one entry point the admin app calls for post-fork GitHub ops (the
    admin-side reads/writes and the pod's `/workspace/pod-token` mint): takes the
    fork's `full_name` — GitHub's own `owner/repo` convention — splits it once,
    resolves the owner's installation (cached), then mints a token scoped to just
    that one repository. Returns the bare token string (callers don't need the
    expiry). Raises if the user hasn't installed the GitHub App on the fork.
    """
    owner, _, repo_name = fork_full_name.partition("/")
    installation_id = await get_installation_id(owner)
    token, _expires_at = await mint_installation_token(installation_id, [repo_name])
    return token


def _epoch_to_iso(epoch: float) -> str:
    """Render a UTC epoch float back as a GitHub-style ISO-8601 `...Z` string."""
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
