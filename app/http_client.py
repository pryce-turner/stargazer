"""
### Shared outbound HTTP client for the admin process.

One lazily-created `httpx.AsyncClient` serves every outbound call the admin
app makes — GitHub API traffic (`app.github`, `app.oauth`,
`app.installation_tokens`) and admin→pod calls (workspace listing probes,
save syncs) — so connections to `api.github.com` and the notebook endpoints
are pooled and reused instead of paying a fresh TLS handshake per call.

Redirect-following is **off** (httpx's default): the GitHub write helpers
depend on *seeing* a 3xx (a transfer redirect must surface as an error, never
silently retarget the upstream repo), and reads treat non-200 explicitly.
Callers override the 15s default timeout per request where a tighter or looser
bound fits (`client().get(url, timeout=2.0)`).

The admin app's lifespan calls `aclose()` on shutdown. Tests install a fake by
setting `_client` directly (e.g. an `httpx.MockTransport`-backed client) and
resetting it to None afterward.

The per-notebook proxy (`app/proxy.py`) cannot import this module — it is
baked into the notebook image standalone — and keeps its own module-local
shared client instead.

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import httpx

DEFAULT_TIMEOUT = 15.0

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    """The process-wide shared async HTTP client, created on first use."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    return _client


async def aclose() -> None:
    """Close the shared client (admin lifespan shutdown). Safe to re-call."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
