"""Tests for the admin-side GitHub App client (`app.installation_tokens`).

Phase 1 of the token-scope hardening plan: a self-contained client that
mints short-lived, fork-scoped installation tokens from the GitHub App
private key. No live behavior depends on it yet, so everything here is
exercised against a generated RSA key and an `httpx.MockTransport`-backed
shared client — no network, no real GitHub App.

The fake transport is installed straight into `app.http_client._client`
(the shared pooled client every GitHub call rides), returning canned JSON
per (method, url) so we can assert the request shape (URL, JWT bearer,
body) and the response parsing.
"""

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import http_client, installation_tokens

# ---------------------------------------------------------------------------
# RSA key + fake transport fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair():
    """Generate an RSA keypair; return (private_pem_str, public_key)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return private_pem, key.public_key()


@pytest.fixture
def app_env(monkeypatch, rsa_keypair):
    """Set GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY in the environment."""
    private_pem, _public = rsa_keypair
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", private_pem)
    return private_pem


@pytest.fixture
def fake_http(monkeypatch):
    """Install a MockTransport-backed client as the shared `app.http_client`.

    Returns a `configure(routes)` callable; `routes` maps `(method, url)` to a
    canned JSON payload. The last request lands in the returned `recorder`
    dict (`method`/`url`/`headers`/`json`), and per-route hit counts accumulate
    in `recorder["calls"]` so cache behavior can be asserted without a second
    fake layer.
    """
    recorder: dict = {}

    def configure(routes: dict):
        def handler(request: httpx.Request) -> httpx.Response:
            key = (request.method, str(request.url))
            recorder["method"] = request.method
            recorder["url"] = str(request.url)
            recorder["headers"] = request.headers  # case-insensitive mapping
            recorder["json"] = json.loads(request.content) if request.content else None
            recorder.setdefault("calls", {})
            recorder["calls"][key] = recorder["calls"].get(key, 0) + 1
            return httpx.Response(200, json=routes[key])

        monkeypatch.setattr(
            http_client,
            "_client",
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        return recorder

    return configure


# ---------------------------------------------------------------------------
# _app_jwt — claim shape + signature
# ---------------------------------------------------------------------------


def test_app_jwt_has_required_claims(app_env, rsa_keypair):
    """The JWT carries iss=app id, iat in the past, exp <=10min ahead."""
    _private, public = rsa_keypair
    token = installation_tokens._app_jwt()
    claims = jwt.decode(token, public, algorithms=["RS256"])

    now = int(time.time())
    assert str(claims["iss"]) == "123456"
    assert claims["iat"] <= now
    assert now < claims["exp"] <= now + 600


def test_app_jwt_signed_rs256(app_env, rsa_keypair):
    """The JWT is RS256 and verifies against the app's public key."""
    _private, public = rsa_keypair
    token = installation_tokens._app_jwt()
    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    # A wrong key must fail verification.
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, other.public_key(), algorithms=["RS256"])


def test_app_jwt_missing_config_raises(monkeypatch):
    """Absent GITHUB_APP_ID / private key is a clear error, not a crash."""
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError):
        installation_tokens._app_jwt()


# ---------------------------------------------------------------------------
# get_installation_id
# ---------------------------------------------------------------------------


async def test_get_installation_id_calls_user_endpoint(app_env, fake_http):
    """Looks up GET /users/{owner}/installation with the app JWT, returns id."""
    installation_tokens._installation_id_cache.clear()
    url = "https://api.github.com/users/octocat/installation"
    recorder = fake_http({("GET", url): {"id": 42, "account": {"login": "octocat"}}})

    inst_id = await installation_tokens.get_installation_id("octocat")

    assert inst_id == 42
    assert recorder["url"] == url
    assert recorder["headers"]["Authorization"].startswith("Bearer ")


async def test_get_installation_id_caches_per_owner(app_env, fake_http):
    """A second lookup for the same owner is served from cache (no GET)."""
    installation_tokens._installation_id_cache.clear()
    url = "https://api.github.com/users/octocat/installation"
    recorder = fake_http({("GET", url): {"id": 42}})

    assert await installation_tokens.get_installation_id("octocat") == 42
    assert await installation_tokens.get_installation_id("octocat") == 42
    assert recorder["calls"][("GET", url)] == 1


# ---------------------------------------------------------------------------
# mint_installation_token — scoping + parsing + cache
# ---------------------------------------------------------------------------


async def test_mint_token_scopes_to_repo_names(app_env, fake_http):
    """POSTs to the access_tokens endpoint with repositories, parses token."""
    installation_tokens._token_cache.clear()
    url = "https://api.github.com/app/installations/42/access_tokens"
    recorder = fake_http(
        {("POST", url): {"token": "ghs_abc", "expires_at": "2099-01-01T00:00:00Z"}}
    )

    token, expires_at = await installation_tokens.mint_installation_token(
        42, repositories=["stargazer"]
    )

    assert token == "ghs_abc"
    assert expires_at == "2099-01-01T00:00:00Z"
    assert recorder["json"] == {"repositories": ["stargazer"]}
    assert recorder["headers"]["Authorization"].startswith("Bearer ")


async def test_mint_token_caches_until_near_expiry(app_env, fake_http):
    """A cached, unexpired token is reused without a second GitHub call."""
    installation_tokens._token_cache.clear()
    url = "https://api.github.com/app/installations/42/access_tokens"
    far_future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    recorder = fake_http(
        {("POST", url): {"token": "ghs_cached", "expires_at": far_future}}
    )

    t1, _ = await installation_tokens.mint_installation_token(
        42, repositories=["stargazer"]
    )
    t2, _ = await installation_tokens.mint_installation_token(
        42, repositories=["stargazer"]
    )

    assert t1 == t2 == "ghs_cached"
    assert recorder["calls"][("POST", url)] == 1  # second call served from cache


async def test_mint_token_refreshes_when_expired(app_env, fake_http):
    """A token at/near expiry is re-minted rather than served stale."""
    installation_tokens._token_cache.clear()
    url = "https://api.github.com/app/installations/42/access_tokens"
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 10))
    recorder = fake_http({("POST", url): {"token": "ghs_new", "expires_at": past}})

    await installation_tokens.mint_installation_token(42, repositories=["stargazer"])
    await installation_tokens.mint_installation_token(42, repositories=["stargazer"])

    assert recorder["calls"][("POST", url)] == 2  # expired entry forces a refresh
    assert recorder["url"] == url


# ---------------------------------------------------------------------------
# fork_token — installation lookup + mint, scoped to one repo
# ---------------------------------------------------------------------------


async def test_fork_token_resolves_install_and_mints_scoped(app_env, fake_http):
    """fork_token splits owner/name, looks up the install, mints a scoped token."""
    installation_tokens._installation_id_cache.clear()
    installation_tokens._token_cache.clear()
    inst_url = "https://api.github.com/users/octocat/installation"
    mint_url = "https://api.github.com/app/installations/42/access_tokens"
    recorder = fake_http(
        {
            ("GET", inst_url): {"id": 42},
            ("POST", mint_url): {
                "token": "ghs_fork",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        }
    )

    token = await installation_tokens.fork_token("octocat/stargazer")

    assert token == "ghs_fork"
    # The final recorded call is the mint, scoped to just the fork's name.
    assert recorder["url"] == mint_url
    assert recorder["json"] == {"repositories": ["stargazer"]}


# ---------------------------------------------------------------------------
# get_installation_id — a renamed owner (301) must not read as "not installed"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_installation_id_follows_owner_rename_redirect(app_env, monkeypatch):
    """GitHub 301s this path after an owner rename — follow it.

    The shared client has redirects off (the write guards depend on seeing a
    3xx), and httpx's `raise_for_status` treats a 3xx as an error. Without an
    explicit follow, a renamed account raises here, the caller's `except`
    reads it as "app not installed", and Workspace saving silently turns off.
    """
    installation_tokens._installation_id_cache.clear()
    old = "https://api.github.com/users/octocat/installation"
    new = "https://api.github.com/users/octocat-renamed/installation"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == old:
            return httpx.Response(301, headers={"location": new})
        return httpx.Response(200, json={"id": 99})

    monkeypatch.setattr(
        http_client,
        "_client",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await installation_tokens.get_installation_id("octocat") == 99
