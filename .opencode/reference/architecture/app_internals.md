# App Tier — Internals (agent reference)

Deep, implementation-level detail for the `app/` deployment tier. This is the verbose companion to the human-facing [docs/architecture/app.md](../../../docs/architecture/app.md) — it carries the cross-cutting protocol detail (credential handshake, opt-in flow, pod lifecycle, sync mechanics) that spans four or five modules and so belongs to no single module docstring. When you change auth, forks, pod launch, or workspace sync, read this first and update it after.

Module-local behavior lives in each module's own docstring (`spec:`-linked back to the architecture doc); this file is only for the multi-module protocols.

## Workspace Opt-In (two-step handshake)

Forking a user's GitHub account is **opt-in**, not automatic. Login creates the user's Flyte project but writes nothing to GitHub; `SessionData.fork_owner` stays empty (`SessionData.workspace_enabled` is False). Tutorials and Workflows notebooks run from the image and need no fork.

Opt-in is **two steps**, and `SessionData.workspace_enabled` requires both — gated on `fork_full_name` **and** `app_installed`:

1. The Workspace section renders a disclaimer + an **Enable workspace saving** button. `POST /workspace/enable` forks the upstream repo, records the verified `fork_full_name`, then redirects the user to install the GitHub App on the fork.
2. The App's setup-URL returns to `/auth/app-install-callback`, which sets `app_installed` **and drops the OAuth token** — only *then* is saving on.

A user who forks but abandons the install is **not** shown as enabled (post-fork ops would fail). A returning user's install is re-confirmed at login via `installation_tokens.get_installation_id`. Only when enabled does the section list the user's notebooks and `/launch` permit workspace launches (clone-on-start + push-on-sync against the fork's `main`). The `public_repo` OAuth scope is requested at login, but no fork is created until this explicit action.

## Credential Model

Two GitHub identities, deliberately split so the broad credential is short-lived and never touches code the user controls:

| Step | Credential | Scope | Lifetime | Where it lives |
|---|---|---|---|---|
| Login + the one-time fork | **OAuth user token** (`read:user public_repo`) | all public repos | login → opt-in only | admin process + encrypted cookie, then **dropped** at install callback |
| Admin-side reads/writes (list/get/create/delete on the fork) | **GitHub App installation token** | the fork only | ~1h, minted on demand | admin process memory |
| Pod clone / push | **GitHub App installation token** via `GIT_ASKPASS` | the fork only | ~1h, minted at use | never persisted — fetched per operation |

The **GitHub App private key** is the trust anchor: held solely by the admin, it mints fork-scoped installation tokens (`app/installation_tokens.py`, `fork_token`) for every post-fork operation. The OAuth token forks once at `/workspace/enable`; the install setup-URL hits `/auth/app-install-callback`, which clears the token from the session — so an *enabled* session carries no GitHub credential. A returning user whose fork already exists never stores the OAuth token at all.

**Pods never receive a GitHub credential.** `per_notebook_env` injects only a signed *capability* (`SG_POD_TOKEN`, carrying the fork name, not a token). At clone (`launch-notebook.sh`) and push (`proxy.py`), the pod exchanges that capability at the admin's `POST /workspace/pod-token` for a fresh fork-scoped token, fed to git via `GIT_ASKPASS` against a token-free remote — so nothing lands in `.git/config` or `os.environ`. The worst a malicious cell can do is mint a fork-scoped, ~1h, revocable token (uninstalling the App cuts it off) — never the broad OAuth token. The session cookie is **encrypted** (Fernet, keyed off `SESSION_SECRET`; the proxy mirrors the derivation), so even its identity fields are opaque client-side.

## Creating Notebooks & Per-Notebook Resources

Once opted in, the Workspace section offers a **New notebook** create tile (name + blank|template seed only — resources and the tile blurb are set afterward). The seeds are real shipped notebooks — `notebooks/workspace/blank.py` and `template.py` — not generated source. `POST /workspace/create` slugifies the name, copies the chosen seed, injects **default** resources into its `[tool.stargazer]` header (`with_stargazer_resources`), writes it to the fork's `main` under `notebooks/workspace/`, and **returns the rendered tile HTML**. (Notebooks don't pin marimo in their PEP 723 headers — `marimo --sandbox` injects the image launcher's version into each kernel venv, so the two never skew without per-notebook bookkeeping.) Create is a pure "add a notebook" action: the browser drops that tile into the Workspace grid; it does not launch or navigate. Both seed slugs are reserved create names and filtered out of the dashboard's tile listing.

Each Workspace tile carries two corner controls:

- **Gear** → settings modal to edit **resources (cpu/memory)** and **description**. Save (`POST /workspace/settings`, workspace-only) rewrites the `[tool.stargazer]` header on the fork (`with_stargazer_resources(..., description=…)` via `update_workspace_notebook`, which carries the blob `sha`) and echoes the normalized values so the browser refreshes the tile blurb + the gear's `data-*` in place. Resource changes take effect at the **next** launch (resources bind at pod-spawn); the description updates immediately. The gear seeds its fields from `data-*` the dashboard renders by fetching each workspace notebook's header (in parallel, best-effort) at page load.
- **Trash** (`POST /workspace/delete`, workspace-only) removes `<slug>.py` from the fork's `main` (idempotent — a file already gone still succeeds; git history keeps it recoverable) and best-effort deactivates any running edit/run pod for that slug, so deleting can't orphan a pod with no tile left to Stop it.

Seed slugs are rejected by both. The browser confirms delete, then drops the tile on success.

Resources are honored **as-authored — no ceiling**. At `/launch`, the admin fetches a workspace notebook's source from the fork and `app.notebook_meta.parse_notebook_resources` reads `[tool.stargazer]` (cpu/memory) and passes it to `per_notebook_env(resources=…)`. Parsing is purely textual — the admin never executes notebook code. Image-baked tutorials/workflows notebooks carry no such block and keep the env's legacy `("2Gi", "6Gi")` default.

## Running State (stateful tiles)

Tile run-state is unified and authoritative. The dashboard's launch/stop handlers are **event-delegated**, so dynamically added tiles (a freshly created notebook) work with no re-binding. On load the page calls **`GET /launch/status`**, which queries Flyte (`App.get` per candidate `nb-{slug}-{mode}` in the user's project, in parallel) and returns the active ones with their endpoints; the page flips those tiles straight to **Open + Stop** instead of a fresh Edit/Run. Because it reads the control plane rather than in-memory state, it's correct across admin restarts. (`App.listall` can't be scoped to a per-user project, so per-candidate `App.get` is used.) The candidate-slug enumeration is shared with cleanup via `_candidate_slugs`.

**One modality at a time.** A notebook runs in either `edit` *or* `run` mode, never both — there's no use case for two live pods of the same notebook, and forbidding it caps pod count at one per notebook. When a tile is launched (or hydrated as already-running), the dashboard hides **both** the launched mode's own Edit/Run button *and* the other mode's, leaving only that pod's controls: **Open: Edit Mode** / **Open: Run Mode**, **Stop**, and **Save** for workspace tiles. **Stop** restores both buttons. This is a client-side affordance: it removes the second-pod path from the UI rather than enforcing mutual exclusion in the control plane.

A running **Workspace** tile also gets a **Save** button (`POST /workspace/save`): the admin resolves that one pod's `App.endpoint` and calls its `/__sg__/workspace/sync` (server-to-server, with the session cookie). It's per-notebook on purpose — each pod owns its own `/workspace` clone, so syncing one can't clobber another's edits; a global commit would race those copies. The call uses the app's public endpoint regardless of where the admin runs (in-cluster pod, local `uvicorn`, prod) — devbox is configured so that hostname resolves inside the cluster too (see [devbox_workarounds.md](../devbox_workarounds.md)).

A global **Clean up stopped apps** control (`POST /workspace/cleanup`) deletes (`App.delete`) the deployment records for every *deactivated* `nb-{slug}-{mode}` candidate — Stop deactivates an app but leaves the record; this removes them. Active/idle apps are left alone.

## Working Branch & Sync

User notebooks live and persist on the fork's **`main`** — there is no side branch. The launch script clones `main`, the proxy's sync commits and pushes back to `main`, and the dashboard lists from `main`. Sync runs on two triggers:

1. The explicit **Save** button.
2. **Pod shutdown** — because `/workspace` is ephemeral, the proxy registers a FastAPI `lifespan` hook that flushes pending edits when Knative scales the pod to zero. The signal path is load-bearing: Flyte's `fserve` wrapper is PID 1 and, on the Knative SIGTERM, forwards the signal to its **one direct child only** (`Popen(cmd, shell=True)` + `send_signal`). So uvicorn must be that direct child — the AppEnvironment args prepend `exec` (so the `sh -c` wrapper replaces itself with the launch script rather than lingering; Debian's `/bin/sh` does not reliably exec-collapse a bare `sh -c "script"`), and the launch script then `exec`s uvicorn into that slot. An intermediate shell anywhere in the chain swallows SIGTERM and the flush is silently skipped, losing unpushed edits.

   Receiving SIGTERM is necessary but not sufficient: uvicorn drains open connections and in-flight background tasks **before** running the `lifespan` shutdown (the flush). The proxy holds a long-lived task to the local marimo backend that never drains on its own, so with uvicorn's default `timeout_graceful_shutdown=None` the drain blocks forever and the flush never fires — verified empirically (the pod sat at "Waiting for background tasks to complete" indefinitely, even after the browser tab closed). The launch script therefore passes `--timeout-graceful-shutdown 15`, which bounds the drain, cancels the lingering task, and then still runs `lifespan.shutdown` (uvicorn only skips it on a second/force SIGTERM). 15s sits well under the pod's 300s termination grace period, leaving room for the git push.

Conflicts with upstream are avoided not by branch isolation but by **path discipline**: the proxy only ever `git add`s `src/stargazer/notebooks/workspace/`, and users create new-named notebooks rather than editing shipped files, so the fork's `main` and upstream touch disjoint paths. The one shared file, `template.py`, is copied (never edited in place) by the create flow. The fork is also not auto-synced from upstream, and the SDK that notebooks import comes from the per-notebook image (`/stargazer`), not the fork checkout — so a drifting `main` doesn't affect execution.

## Snapshots (freeze mechanics)

Conceptual framing (snapshot vs. workflow, the publication path) is in the architecture doc and [Notebooks → Promotion Paths](../../../docs/architecture/notebook.md#promotion-paths). The mechanics:

**Freezing is a move.** `POST /workspace/snapshot` takes a notebook *out* of the editable Workspace surface: it re-creates the notebook's current `main` source verbatim under `notebooks/snapshots/<slug>.py` (`create_snapshot_notebook`), then deletes the workspace original (`delete_workspace_notebook`). The snapshots write happens first, so a failed move leaves the notebook editable rather than lost. The source of truth is the fork's `main` — its last *saved* state — so Save before snapshotting to capture live pod edits. Like delete, snapshot tears down any running pod for the slug (`_teardown_notebook_pods`), since once moved there's no Workspace tile left to Stop it.

Each Workspace tile carries a **📸 snapshot** button (between the gear and trash) that calls this route, then drops the workspace tile and inserts the returned tile into the Snapshots grid. The Snapshots section lists the fork's `notebooks/snapshots/` (`list_snapshots` → `_resolve_snapshot_files`, GitHub-only for the listing — no pod *lists* snapshots). A snapshot tile carries **a single Run button** — no Edit, gear, or trash, since a frozen record isn't edited or re-configured. Launching one goes through the same `/launch` path as workspace notebooks: `section=snapshots` is fork-backed (so it requires opt-in) and **restricted to run mode** (`marimo run`, read-only); the file is read from `SNAPSHOT_NOTEBOOK_DIR` (the snapshots dir in the pod's sparse clone, which already covers `src/stargazer/`) and its `[tool.stargazer]` resources are honored like any workspace launch. Snapshot slugs join `_candidate_slugs`, so a running snapshot hydrates to Open/Stop on reload and `/workspace/cleanup` reaps its stopped pod.

**Public vs. own — both flow through the fork.** Snapshots are not user-specific the way Workspace notebooks are. They live in the repo's `notebooks/snapshots/` and travel with the fork like any other repo content: when a user enables saving, the fork is a full copy of upstream, so it already carries every **public, merged** snapshot — just like the shipped tutorials. The user's own 📸 freezes land in that same dir, and syncing the fork with `main` pulls in newly-merged public ones. So `list_snapshots(fork)` returns both, run launches serve both from the fork clone, and there's no separate upstream-listing or upstream-clone path. (One consequence: freezing a notebook whose slug matches an existing public snapshot is refused by the no-overwrite create — pick a distinct name.) The trade-off is that snapshots are visible only once a fork exists (opt-in), unlike image-baked tutorials which everyone sees.

**Deferred:** image-digest pinning and an inputs/outputs (CID) manifest. This cut freezes the notebook *source*, which is auditable; bit-for-bit re-run is a later phase. The publication path — a user PRs a fork snapshot into upstream, where it merges and then reaches every other fork on sync — is the GitHub-native flow, not a Stargazer route.

## Admin Routes (full table)

All on `app/admin_app.py` (`app_env`). Lifespan runs `init()` at startup. Post-fork GitHub ops go through `app/installation_tokens.py` (installation tokens), not the OAuth token.

| Route | Purpose |
|---|---|
| `/` | Landing / dashboard |
| `/auth/login`, `/auth/callback`, `/auth/logout` | GitHub OAuth login flow |
| `/workspace/enable` | Opt-in: forks upstream, records verified `fork_full_name`, redirects to GitHub App install |
| `/auth/app-install-callback` | Finishes opt-in (sets `app_installed`), drops the OAuth token |
| `/workspace/create` | Writes a new notebook to the fork's `main`, returns the rendered tile |
| `/workspace/settings` | Rewrites a workspace notebook's `[tool.stargazer]` header (resources + description) |
| `/workspace/delete` | Removes a workspace notebook (idempotent); deactivates any running pod for it |
| `/workspace/snapshot` | *Moves* a workspace notebook into `notebooks/snapshots/` (frozen); tears down its pods |
| `/launch` | Serves a per-notebook env (workspace/snapshot launches require opt-in) |
| `/launch/status` | Reports active per-notebook apps so the dashboard hydrates running tiles to Open/Stop |
| `/stop` | Deactivates a per-notebook app by name |
| `/workspace/save` | Syncs one running pod's workspace to the fork |
| `/workspace/cleanup` | Deletes deactivated per-notebook app records |
| `/workspace/pod-token` | Mints a fork-scoped git token for a pod that presents its capability |
| `/health` | Health probe |

Asset-manager routes are a separate router (`app/assets.py`, `include_router`ed onto the same app):

| Route | Auth | Purpose |
|---|---|---|
| `GET /assets` | none | Render `assets.html` (anonymous → public tab only) |
| `GET /assets/schema` | none | `{asset_key: [{name,type,default}]}` from `ASSET_REGISTRY` (minus `_BASE_FIELDS`) for the dynamic form |
| `GET /assets/list?<kv>&network=` | public: none / private: session | Public served from a TTL cache (filters in-memory); private forces `_owner == session user` server-side (fail closed) |
| `POST /assets/sign` | session | `build_asset()` validate → stamp `_owner` → mint Pinata signed upload URL (filename + keyvalues + `MAX_UPLOAD_BYTES` baked in) → `{url, keyvalues}` |
| `POST /assets/update` | session + **owner** | Fail-closed ownership (record's `_owner` must equal session user, read fresh) → `build_asset()` validate patch → re-stamp `_owner` → `PinataClient.update_metadata()` merge → updated record |
| `GET /assets/download/{cid}?network=` | public: none / private: session | 302 redirect; split-gateway (anon public → `PUBLIC_FALLBACK_GATEWAY`, session → `PINATA_GATEWAY`); private → signed URL |

## Asset Manager (mechanics)

`PINATA_JWT` rides into the admin pod via `_RUNTIME_SECRETS` (same env-baking as the OAuth secrets — App-pod `secrets=` is dropped by this Flyte build). Without it the routes 503 and the page shows "not configured"; there is no TinyDB fallback for this surface.

- **Owner stamping.** `/assets/sign` stamps `session.github_username` as `_owner` *after* `build_asset()` validation, so it rides the signature-protected signed URL — unforgeable from the browser. Workspace/SDK uploads stamp from `STARGAZER_OWNER` instead: the launcher injects it into per-notebook pods (`env.env_vars["STARGAZER_OWNER"]` next to `FLYTE_PROJECT`), and `config._stargazer_env_vars()` forwards it into task pods at submission so pipeline outputs are owned too. Stamping lives in `PinataClient.upload()` (`_stamp_owner`, env wins over any stale value) and in the sign route; `build_asset()` rejects user-supplied `_*` keys so the namespace stays clean.
- **Metadata edit (`update_metadata`).** A mis-tagged record is fixed in place rather than delete-and-re-uploaded. `PinataClient.update_metadata(cid, keyvalues, network)` looks up the file's internal UUID by CID, then `PUT /v3/files/{network}/{id}` with the patch — Pinata **merges** (verified empirically: supplied keys added/overwritten, omitted keys preserved, no key removal), and the bytes/CID are untouched so `*_cid` provenance edges survive. `_stamp_owner` runs here too (env wins; no-op in the admin pod, where the route sets `_owner` explicitly). `LocalStorageClient.update_metadata` mirrors the merge into TinyDB when there's no remote. Two surfaces drive it: the MCP `update_file` tool (validate via `build_asset` → delegate, shared-JWT so unenforced by design) and the `POST /assets/update` route (which additionally **fail-closes on ownership** — the record's current `_owner` must match the session user, read fresh from Pinata not the public TTL cache, so a shared-JWT session still can't rewrite another user's or an unowned record from the page). Editing is the headline reason the bare `cid` is *not* treated as a relationship key in the graph — a content-addressed id never changes under a metadata edit.
- **Public TTL cache.** `_public_cache` (module global, `PUBLIC_CACHE_TTL` = 60s) holds one unfiltered public-network listing; the public tab filters it in-memory. So anonymous public browsing costs ≤1 Pinata listing call per TTL regardless of traffic, and the admin acts as a semi-static mirror. Swap to a background refresher if the first-request-after-expiry latency ever matters.
- **Client swap for tests.** `_pinata_client` / `_public_cache` are module attributes resolved at call time, so route tests monkeypatch a fake Pinata client and reset the cache (`tests/unit/test_assets_routes.py`, `TestClient` without lifespan).
- **Errors** are FastAPI-standard `HTTPException` → `{"detail": ...}` (401 auth, 400 validation, 503 not-configured), via the `_require_session` / `_require_pinata` guards.

### Template (`app/templates/assets.html`)

The page extends `base.html` and renders inside one `.dashboard` card — the same glassy surface as the notebook dashboard — with the identical brand+avatar header (an `Assets`↔`Dashboard` cross-link in each menu). It reuses base's tokens/buttons; asset-specific CSS rides the `{% block extra_css %}` hook. **The reactive `#sky` starfield is untouched** — the asset graph is a separate foreground canvas inside the card.

The graph is rendered by **Cytoscape.js**, vendored as `app/static/cytoscape.min.js` (pinned 3.30.2, ~370 KB) and `<script src>`-loaded only on this page. This is the one deliberate exception to the app's "ship only hand-written JS" convention (base.html's starfield is still hand-rolled): a graph library earns its keep here because zoom/pan/node-drag/force-layout are exactly its core competency, and the project has no bundler, so a single vendored min.js (no build step, served by the existing `/static` mount) was the lowest-friction way in. To update: re-fetch the pinned version from jsdelivr into the same path. The rest of the page JS is still dependency-free vanilla, gated behind `{% if pinata_configured %}`. A `state` object (network, view, filters, `records`, `selected`, `editing`, `schema`) is the single source of truth; the graph and list are two renderings of the **same** `/assets/list` payload, so toggling Graph⇄List re-renders from memory while changing the network tab or filters refetches.

- **Graph edges from `*_cid`.** `relKeys()` selects every keyvalue ending in `_cid` with a non-empty value (bare `cid` excluded); `buildGraph()` draws an edge from the record to the asset whose `cid` matches the value, labeled with the key minus `_cid` (`reference_cid` → "reference"). The typed `*_cid` fields (`reference_cid`, `r1_cid`, `mate_cid`, `alignment_cid`, `variants_cid`, `known_sites_cid`, `source_cid`) ride in `keyvalues` from `to_keyvalues()`, so no backend change was needed; custom/bare assets carrying their own `*_cid` rows link too.
- **Dangling edges** (a `*_cid` value with no matching node in the current set — cross-network, owner-scoped-out, or unloaded) are **not drawn**; instead the detail card lists *all* of a node's links, resolving in-view targets to a clickable chip and showing the rest read-only as "not in this view," so provenance is never hidden.
- **Layout** is Cytoscape's built-in `cose` force layout (`GRAPH_LAYOUT`, non-animated, fit-to-view), run on `graphElements(records)` — nodes sized by degree, edges carrying a target arrow for provenance direction. Style lives in the `GRAPH_STYLE` array (canvas-rendered, so hex/rgba not CSS vars; it mirrors the violet/accent palette). Nodes are capped at `NODE_CAP` (150) with a "showing N of M" hint past the cap. The `cy` instance is created once and reused (`cy.elements().remove()` + `add` + `resize` + re-layout on each render).
- **Interaction** is mostly Cytoscape-native: **scroll zooms**, **drag pans the background**, **drag moves a node** (free with the library). `bindGraphEvents()` adds the app behaviors: `tap` on a node → `selectAsset()`, `tap` on empty canvas → clear; `mouseover`/`mouseout` drive the styled `graph-tip` tooltip (positioned via `node.renderedPosition()`) and a neighbor/edge `.hot` highlight; a native container `dblclick` calls `cy.fit()` to reset. Selection is driven by a `.selected` class we toggle (cy runs `autounselectify`), so it stays in sync whether you click a graph node or a list row (`selectAsset()` is shared). Clicking pins the **detail card** (full metadata, system `_*` keys dimmed, linked-asset chips, a Download button → `/assets/download/{cid}?network=`, and **Edit metadata** on owned records).
- **Upload** is schema-driven from `/assets/schema`: a typed mode (inputs per field, non-`str` values JSON-encoded to match `from_keyvalues()`) and a visually-distinct custom/bare mode (dashed framing, generic notice, free-form k/v rows reserving `asset`/`cid`/`name`/`_*`). On submit it mints (`POST /assets/sign`), PUTs the file straight to the single-use signed URL (re-mint-and-retry once on failure), then **optimistically inserts** the returned record into `state.records` so it appears before Pinata's list catches up. Network radio defaults to Private; the 100 MB cap is enforced at mint via `MAX_UPLOAD_BYTES` (TUS for larger is ROADMAP item 20).

## Runtime Init

The same `init()` (`app/init.py`) works locally and in-cluster:

| Context | Signal | Call |
|---------|--------|------|
| In-cluster app pod | `_U_EP_OVERRIDE` set | `flyte.init_in_cluster()` |
| API-key context | `FLYTE_API_KEY` set | `flyte.init_from_api_key(...)` |
| Local dev / deployer | neither set | `flyte.init_from_config()` |

FastAPI's lifespan calls `init()` once at startup so subsequent SDK calls have a configured client.

## Images (build & publish)

The admin app and the per-notebook pods share a strict split:

- `app_env.image` is Flyte-built via `with_uv_project` — the admin is small Python with no heavy deps, and the Flyte builder is the natural fit.
- Per-notebook envs use the **`notebook-app`** image, defined programmatically as `notebook_app_img_recipe` in `app/per_notebook.py` (proxy, launch script, bioconda CLIs, Claude Code, SDK source at `/stargazer`). The admin pod references it by a stable tag via `Image.from_base(NOTEBOOK_IMAGE_URI)` so it never tries to (re)build the image itself — the admin pod has no Docker daemon or project source layout. (The `note` target in the project `Dockerfile` — `stargazer-note` — is for local `docker run` exploration only and is *not* the hosted image.)

The admin deploy entrypoint (`python -m app.admin_app` / `stargazer-app`) runs `flyte.build` on the recipe and retags the result as `notebook-app:latest` in `STARGAZER_REGISTRY` before calling `flyte.serve(app_env)`, so the image is always pullable when a user's `/launch` click first spawns a per-notebook pod. The `:latest` tag is load-bearing — it flips Kubernetes to `imagePullPolicy: Always` so pods pick up new proxy/launch code after a redeploy.

## Known Gaps

Production-hardening gaps live in the roadmap, not here: **identity-gated production auth** (per-notebook envs currently run `requires_auth=False`) and **async OAuth provisioning**. See [`.opencode/plans/ROADMAP.md`](../../plans/ROADMAP.md).
