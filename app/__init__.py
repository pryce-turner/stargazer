"""
### Stargazer deployment infrastructure.

Two-app architecture:

- `app.admin_app.app_env` — shared FastAPI service. Hosts the
  unauthenticated landing + GitHub OAuth, runs per-user provisioning
  (Flyte project + GitHub fork), renders the post-login dashboard tile
  grid, and brokers Edit/Run clicks into per-notebook apps via
  `app.per_notebook.per_notebook_env(...)`.
- `app.per_notebook.per_notebook_env(...)` — factory for per-notebook
  AppEnvironments, spawned by the admin app's `/launch` handler. The
  image is `notebook-app` (uv + marimo + system tools + cookie-
  validating reverse proxy); the proxy serves `/__sg__/workspace/list`
  off the pod's local `/workspace` directory (cloned from the user's
  fork on startup) and `/__sg__/workspace/sync` pushes edits back to
  the fork. Persistence is the GitHub fork itself; per-notebook pods
  are working copies. No PVC — Flyte v2 doesn't yet support pod
  templates on AppEnvironments.

Plus supporting modules: `oauth`, `github`, `installation_tokens`,
`http_client` (the shared pooled HTTP client every outbound admin call
rides), `notebooks`, `notebook_meta`, `assets`, `proxy`, `session`,
`templates`, `provision`, `init`. Lives outside `src/stargazer` because
it is deployment glue, not part of the bioinformatics SDK.

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""
