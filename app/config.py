"""
### App-tier configuration — one home for the web tier's env-derived settings.

Mirrors `stargazer.config` (the SDK tier) for the deployment / web tier. Every
**non-secret** setting the app reads from the environment is resolved here once,
at import, with its default visible in one place — so the rest of `app/` reads
`config.SECURE_COOKIES` instead of sprinkling `os.environ.get(...)` with ad-hoc
defaults across modules.

What does **not** live here:

- **Secrets** (`SESSION_SECRET`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`)
  — they have no committable default and are assembled into the App `env_vars`
  spec in `admin_app` (see the secret-baking block there).
- **The per-notebook proxy** (`app/proxy.py`) — it's baked into the notebook
  image as a *standalone* module with no `app` package on its path, so it can't
  import this; it re-reads the few env vars it needs (mirroring the values here).

Values are read at import: in a deployed pod the environment is baked before the
process starts, so import-time == runtime. Tests that need a specific value
`monkeypatch.setattr` the constant here rather than poking the environment.

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import os

_TRUTHY = ("1", "true", "yes", "on")


def _flag(name: str) -> bool:
    """Parse an env var as a boolean flag (`1/true/yes/on`, case-insensitive)."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


# Auth cookies get the `Secure` attribute (HTTPS-only) only under production TLS.
# Default **off**: devbox serves over plain HTTP, where a Secure cookie is never
# sent, so defaulting on would make local login silently impossible. Export
# `STARGAZER_SECURE_COOKIES=1` in production. The proxy mirrors this (via the
# value baked into each notebook pod's env) so all cookie writers agree.
SECURE_COOKIES: bool = _flag("STARGAZER_SECURE_COOKIES")

# The GitHub App's public URL handle (e.g. `stargazer-workspaces`), used to build
# the install-redirect URL at `/workspace/enable`. Unset → installs aren't wired
# up, so enable lands straight on the dashboard. Non-secret.
GITHUB_APP_SLUG: str | None = os.environ.get("GITHUB_APP_SLUG") or None

# The admin app's public base URL (e.g. `https://admin.stargazer.bio`). Used to
# build OAuth callback URIs and the per-notebook `admin_url`. Unset → fall back
# to the request's own base_url (so local `uvicorn --reload` needs no config).
LANDING_BASE_URL: str | None = os.environ.get("LANDING_BASE_URL") or None

# Default Flyte project/domain the admin pod targets for code-bundle uploads
# during per-user `serve.aio(...)` calls.
FLYTE_PROJECT: str = os.environ.get("FLYTE_PROJECT", "flytesnacks")
FLYTE_DOMAIN: str = os.environ.get("FLYTE_DOMAIN", "development")
