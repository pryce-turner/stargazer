"""Tests for the per-notebook proxy (`app.proxy`).

The proxy is baked into the notebook image as a standalone module; here we
import it directly and exercise `_fetch_pod_git_token` (which exchanges the
`SG_POD_TOKEN` capability for a fresh fork-scoped token at push time — the
GitHub/admin round-trip is faked via `httpx.post`) plus the forwarding path
itself: cookie gating, terminal-overlay injection into HTML, streaming
passthrough for everything else, and raw query-string preservation. The
upstream marimo server is faked with an `httpx.MockTransport` installed as
the proxy's shared upstream client.
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import proxy
from app.session import SessionData, create_session_cookie

# ---------------------------------------------------------------------------
# Encrypted session cookie — the proxy must validate what the admin issues
# ---------------------------------------------------------------------------


def test_proxy_validates_admin_issued_cookie(monkeypatch):
    """The proxy's Fernet derivation matches app.session, so it accepts the cookie."""
    secret = "shared-session-secret"
    monkeypatch.setenv("SESSION_SECRET", secret)
    cookie = create_session_cookie(
        SessionData("octocat", 1, fork_full_name="octocat/stargazer"), secret
    )
    assert proxy._cookie_is_valid(cookie) is True


def test_proxy_rejects_tampered_or_foreign_cookie(monkeypatch):
    """A wrong-secret, tampered, absent, or unencrypted cookie is rejected."""
    secret = "shared-session-secret"
    cookie = create_session_cookie(SessionData("octocat", 1), "a-different-secret")
    monkeypatch.setenv("SESSION_SECRET", secret)
    assert proxy._cookie_is_valid(cookie) is False
    assert proxy._cookie_is_valid(None) is False
    assert proxy._cookie_is_valid("not-a-real-cookie") is False


def test_proxy_denies_when_secret_missing(monkeypatch):
    """No SESSION_SECRET in the pod env → deny everything."""
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert proxy._cookie_is_valid("anything") is False


def test_proxy_cookie_check_fails_closed_on_unexpected_error(monkeypatch):
    """An unexpected exception in the auth gate denies (401), never 500s.

    This is a security gate: any failure mode must fall through to False. A
    narrow `except` tuple would let a surprise exception escape the middleware
    and surface as a 500 from the notebook pod instead of a clean deny.
    """
    monkeypatch.setenv("SESSION_SECRET", "shared-session-secret")

    def boom(_secret):
        raise MemoryError("not a ValueError/TypeError/InvalidToken")

    monkeypatch.setattr(proxy, "_fernet", boom)
    assert proxy._cookie_is_valid("anything") is False


def test_proxy_cookie_secure_mirrors_config(monkeypatch):
    """The proxy's Secure read matches `app.config.SECURE_COOKIES` (off by default)."""
    monkeypatch.delenv("STARGAZER_SECURE_COOKIES", raising=False)
    assert proxy._cookie_secure() is False
    monkeypatch.setenv("STARGAZER_SECURE_COOKIES", "1")
    assert proxy._cookie_secure() is True


def _stub_httpx_post(monkeypatch, *, status: int, text: str):
    """Patch proxy.httpx.post to return a canned response, recording the call."""
    recorder: dict = {}

    def fake_post(url, headers=None, timeout=None):
        recorder.update(url=url, headers=headers or {})
        return SimpleNamespace(status_code=status, text=text)

    monkeypatch.setattr(proxy.httpx, "post", fake_post)
    return recorder


def test_fetch_token_missing_capability_returns_none(monkeypatch):
    """With no SG_POD_TOKEN/admin URL there's nothing to exchange — None."""
    monkeypatch.delenv("SG_POD_TOKEN", raising=False)
    monkeypatch.delenv("STARGAZER_ADMIN_URL", raising=False)
    assert proxy._fetch_pod_git_token() is None


def test_fetch_token_posts_capability_and_returns_token(monkeypatch):
    """The capability is sent as a bearer; the plain-text token is returned."""
    monkeypatch.setenv("SG_POD_TOKEN", "signed-cap")
    monkeypatch.setenv("STARGAZER_ADMIN_URL", "http://admin/")
    recorder = _stub_httpx_post(monkeypatch, status=200, text="ghs_pod\n")

    token = proxy._fetch_pod_git_token()

    assert token == "ghs_pod"
    assert recorder["url"] == "http://admin/workspace/pod-token"
    assert recorder["headers"]["Authorization"] == "Bearer signed-cap"


def test_fetch_token_non_200_returns_none(monkeypatch):
    """A 401/502 from the admin yields None (caller turns it into a failure)."""
    monkeypatch.setenv("SG_POD_TOKEN", "signed-cap")
    monkeypatch.setenv("STARGAZER_ADMIN_URL", "http://admin")
    _stub_httpx_post(monkeypatch, status=502, text="")
    assert proxy._fetch_pod_git_token() is None


# ---------------------------------------------------------------------------
# Dropdown terminal — secret scrub + HTML overlay injection
# ---------------------------------------------------------------------------


def test_shell_env_scrubs_named_secrets(monkeypatch):
    """The big risk: a shell that could read SESSION_SECRET could forge cookies."""
    monkeypatch.setenv("SESSION_SECRET", "shared-cookie-key")
    monkeypatch.setenv("SG_POD_TOKEN", "fork-capability")
    monkeypatch.setenv("PINATA_JWT", "pinata-secret")
    monkeypatch.setenv("PINATA_GATEWAY", "https://dweb.link")  # not a secret

    env = proxy._shell_env()

    assert "SESSION_SECRET" not in env
    assert "SG_POD_TOKEN" not in env
    assert "PINATA_JWT" not in env
    assert env["PINATA_GATEWAY"] == "https://dweb.link"


def test_shell_env_scrubs_secret_shaped_suffixes(monkeypatch):
    """Future secret-shaped vars are dropped by suffix without an explicit entry."""
    monkeypatch.setenv("SOME_API_TOKEN", "x")
    monkeypatch.setenv("DB_PASSWORD", "x")
    monkeypatch.setenv("SIGNING_KEY", "x")
    monkeypatch.setenv("WEBHOOK_SECRET", "x")
    monkeypatch.setenv("THIRDPARTY_JWT", "x")
    monkeypatch.setenv("HARMLESS_VALUE", "keep")

    env = proxy._shell_env()

    for leaked in (
        "SOME_API_TOKEN",
        "DB_PASSWORD",
        "SIGNING_KEY",
        "WEBHOOK_SECRET",
        "THIRDPARTY_JWT",
    ):
        assert leaked not in env
    assert env["HARMLESS_VALUE"] == "keep"
    assert env["TERM"] == "xterm-256color"


def test_term_injection_targets_body_close():
    """The overlay splices before </body> and wires the /__sg__/term websocket."""
    html = b"<html><body><div id='app'></div></body></html>"
    injected = html.replace(b"</body>", proxy._TERM_INJECTION + b"</body>", 1)

    assert b"/__sg__/term" in injected
    assert b"sg-term-overlay" in injected
    # Injection lands inside the body, before its close tag.
    assert injected.index(b"sg-term-overlay") < injected.index(b"</body>")


# ---------------------------------------------------------------------------
# Forwarding path — cookie gate, HTML injection, streaming, query passthrough
# ---------------------------------------------------------------------------


@pytest.fixture
def upstream(monkeypatch):
    """Fake the marimo upstream behind the proxy's shared client.

    Installs an `httpx.MockTransport`-backed client as `proxy._upstream` and
    returns a `configure(handler)` callable plus a recorder capturing the last
    upstream request (`method`/`url`/`raw query`).
    """
    recorder: dict = {}

    def configure(respond):
        def handler(request: httpx.Request) -> httpx.Response:
            recorder["method"] = request.method
            recorder["url"] = str(request.url)
            recorder["query"] = request.url.query.decode()
            return respond(request)

        monkeypatch.setattr(
            proxy,
            "_upstream",
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        return recorder

    return configure


@pytest.fixture
def authed_client(monkeypatch):
    """A TestClient (no lifespan) holding a valid admin-issued session cookie."""
    secret = "shared-session-secret"
    monkeypatch.setenv("SESSION_SECRET", secret)
    client = TestClient(proxy.asgi_app)
    cookie = create_session_cookie(SessionData("octocat", 1), secret)
    client.cookies.set(proxy.SESSION_COOKIE, cookie)
    return client


def test_proxy_401s_without_cookie(monkeypatch, upstream):
    """No session cookie → 401, and the request never reaches marimo."""
    monkeypatch.setenv("SESSION_SECRET", "shared-session-secret")
    recorder = upstream(lambda req: httpx.Response(200, text="never"))
    client = TestClient(proxy.asgi_app)

    resp = client.get("/anything")

    assert resp.status_code == 401
    assert "url" not in recorder  # upstream untouched


def test_proxy_injects_terminal_into_html(authed_client, upstream):
    """HTML responses get the terminal overlay spliced in before </body>."""
    upstream(
        lambda req: httpx.Response(
            200,
            content=b"<html><body><div id='app'></div></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )

    resp = authed_client.get("/")

    assert resp.status_code == 200
    assert b"sg-term-overlay" in resp.content
    assert resp.content.index(b"sg-term-overlay") < resp.content.index(b"</body>")


def test_proxy_streams_non_html_untouched(authed_client, upstream):
    """Non-HTML bodies pass through byte-for-byte with their content type."""
    payload = bytes(range(256)) * 64  # binary, overlay must not touch it
    upstream(
        lambda req: httpx.Response(
            200,
            content=payload,
            headers={"content-type": "application/octet-stream"},
        )
    )

    resp = authed_client.get("/assets/bundle.bin")

    assert resp.status_code == 200
    assert resp.content == payload
    assert b"sg-term-overlay" not in resp.content


def test_proxy_preserves_raw_query_string(authed_client, upstream):
    """Duplicate query params reach marimo intact (no dict collapse)."""
    recorder = upstream(lambda req: httpx.Response(200, text="ok"))

    resp = authed_client.get("/api/kernel?file=a.py&tag=x&tag=y")

    assert resp.status_code == 200
    assert recorder["query"] == "file=a.py&tag=x&tag=y"
    assert recorder["url"].endswith("/api/kernel?file=a.py&tag=x&tag=y")


def test_proxy_forwards_method_and_body(authed_client, upstream):
    """POST bodies stream through to marimo with the method intact."""

    def respond(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"echo": req.content.decode()})

    recorder = upstream(respond)

    resp = authed_client.post("/api/run", content=b'{"cell": 1}')

    assert resp.status_code == 200
    assert recorder["method"] == "POST"
    assert resp.json() == {"echo": '{"cell": 1}'}
