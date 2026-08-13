# 22 — App-Tier Performance & Modernization Audit

A full pass over the `app/` deployment tier (admin dashboard, asset manager,
per-notebook proxy, launch/status brokering) to bring it in line with modern
FastAPI/httpx best practices while keeping the Flyte app model — one shared
admin `AppEnvironment` + per-notebook `AppEnvironment`s — exactly as it is.
Goal: **lightweight and performant**. No behavior changes visible to users
except lower latency.

## Audit Findings

### A. HTTP client hygiene (the big one)

Every outbound HTTP call in the app tier constructs a fresh client/session:

1. **`app/proxy.py` builds an `httpx.AsyncClient` per proxied request.** Every
   marimo asset, API call, and readiness probe pays client construction plus a
   fresh TCP connection — no keep-alive to the loopback marimo server. This is
   the hottest path in the whole tier (every byte of every notebook page).
2. **The proxy buffers entire request/response bodies** (`await request.body()`
   / `resp.content`). Marimo ships multi-MB static bundles; run-mode pages can
   be large. Only `text/html` needs buffering (for the terminal-overlay splice);
   everything else can stream.
3. **The proxy collapses duplicate query params** (`dict(request.query_params)`)
   instead of passing the raw query string through.
4. **`app/admin_app.py` builds an `httpx.AsyncClient` per call** in
   `_list_workspace_from_pods` (one per endpoint probed!) and `/workspace/save`.
5. **`app/github.py`, `app/oauth.py`, `app/installation_tokens.py` open a new
   `aiohttp.ClientSession` per API call** — a dashboard render can cost 2+N
   TLS handshakes to `api.github.com`. The app tier also mixes two HTTP stacks
   (aiohttp + httpx) for no reason.

**Decision:** one shared `httpx.AsyncClient` for the admin process
(`app/http_client.py`, lazy singleton, closed in the admin lifespan), used by
the GitHub modules and the admin→pod calls; aiohttp leaves the app tier
entirely (it stays an SDK dep — `stargazer.utils.pinata` is out of scope
here). The proxy — a standalone module that can't import `app` — gets its own
module-local shared client with keep-alive to marimo, plus streaming
passthrough for non-HTML responses.

### B. Notebook launch/status brokering

6. **`/launch/status` enumerates candidates the expensive way**: two GitHub
   API listings (workspace + snapshots) plus 2×N `App.get` probes, where N =
   every registry, workspace, and snapshot notebook — on every dashboard load.
   `list_project_apps` (already used by `/workspace/cleanup`) returns every
   deployed `nb-*` app in one RPC. **Decision:** discover via the single list,
   then re-`App.get` only the discovered names (list payloads may not carry
   full conditions — keep the authoritative re-fetch), parse `slug`/`mode`
   back out of the `nb-{slug}-{mode}` name. `_candidate_slugs` and the GitHub
   round-trips drop out of the hot path entirely.
7. **The landing route resolves workspace and snapshot listings sequentially**
   → `asyncio.gather`.
8. **`_list_workspace_from_pods` probes known pod endpoints sequentially**
   (2s timeout each) → probe in parallel, first success wins.

### C. Middleware

9. **No response compression.** The dashboard HTML is ~40KB+ and the asset
   listings are JSON-heavy. **Decision:** `GZipMiddleware` (minimum_size=1KB)
   on the admin app. The proxy does NOT get it — it must pass marimo's own
   encoding through untouched (streamed raw).

### D. Asset manager

10. **`_public_records` has no single-flight guard** — concurrent cache misses
    fan out duplicate Pinata listings. **Decision:** an `asyncio.Lock` around
    the refresh (double-checked).
11. `Optional[X]` → modern `X | None` unions.
12. `/assets/update` fetches the full listing to find one CID — a
    `PinataClient` API-shape constraint (no by-CID lookup); noted, not changed
    here (SDK tier).

### E. Small correctness/cleanliness

13. Proxy `except (InvalidToken, Exception)` → `except Exception`.
14. Dashboard JS `document.write` (deprecated) → DOM construction for the
    "starting notebook" placeholder tab.
15. `memory_to_gib` falls back to a hardcoded `2` that would silently diverge
    from `DEFAULT_RESOURCES` — derive it instead.

### F. Explicitly NOT changed (informed choices)

- **The Flyte app model itself** — admin `app_env` + `per_notebook_env`
  factory + per-user Flyte projects stays as designed.
- **Session auth via `_get_session` per route** (vs FastAPI `Depends`): routes
  have heterogeneous unauthenticated behavior (redirect vs JSON 401 vs
  `wants_json` branching), and the JS contract is `{"error": ...}` not
  FastAPI's `{"detail": ...}`. A dependency refactor would be churn without a
  performance or clarity win. Kept.
- **`/launch` holding the serve watch** — already degrades gracefully
  (endpoint recovery on watch error) and the browser polls readiness anyway.
- **`PinataClient` per-call aiohttp sessions** — SDK tier, shared with task
  pods; a separate pass.
- **Response caching of the dashboard** — the page is per-user and cheap once
  the GitHub calls are pooled; not worth cache-invalidation complexity.

## Implementation Steps

- [x] 1. `app/http_client.py` — lazy shared `httpx.AsyncClient` (`client()`,
  `aclose()`), 15s default timeout, redirects off (GitHub write guards depend
  on seeing 3xx). Admin lifespan closes it on shutdown.
- [x] 2. Migrate `app/oauth.py`, `app/github.py`, `app/installation_tokens.py`
  from per-call aiohttp sessions to the shared httpx client. Reads keep the
  404→None/[] contract; writes keep the explicit no-redirect + upstream-name
  guards; `_ensure_ok` keeps surfacing GitHub's JSON `message`.
- [x] 3. `tests/unit/test_installation_tokens.py` — replace the aiohttp fake
  session with an `httpx.MockTransport`-backed client installed into
  `app.http_client`.
- [x] 4. `app/admin_app.py` — shared client for pod list/sync calls; parallel
  pod probing; landing gathers workspace+snapshot listings; `/launch/status`
  via `list_project_apps`; drop `_candidate_slugs`; add GZip middleware.
- [x] 5. `app/proxy.py` — module-local shared client (keep-alive to marimo),
  streamed non-HTML responses (`BackgroundTask(resp.aclose)`), raw query
  passthrough, exception-clause fix; client closed in lifespan after the
  shutdown sync.
- [x] 6. `tests/unit/test_proxy.py` — new proxied-path tests (HTML injection,
  non-HTML streaming passthrough, duplicate-query preservation) via
  MockTransport.
- [x] 7. `app/assets.py` — cache single-flight lock; union syntax.
- [x] 8. `app/notebook_meta.py` — derive the GiB fallback.
- [x] 9. `app/templates/dashboard.html` — drop `document.write`.
- [x] 10. Update `tests/unit/test_app.py` (save/status/candidates), module
  docstrings, `.opencode/reference/architecture/app_internals.md`, pyproject
  `landing` extra (drop aiohttp), ROADMAP Complete entry.
- [x] 11. `ruff check --fix` + `ruff format`; full unit suite green.
