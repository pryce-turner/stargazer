# 20 — Asset Manager Dashboard Page

## Status / decisions

### 2026-06-10

- **Bare `Asset` is the catchall.** Users may upload files under *any* asset
  key with arbitrary metadata — only the `asset` key itself is required.
  Registered keys validate strictly against the typed class as before;
  unregistered keys are stored verbatim as bare `Asset` records. SDK tasks
  stay strict about the typed objects they consume; users authoring their own
  tasks define their own `Asset` subclass in their own code, which
  self-registers on import (`__init_subclass__`), so `assemble()` returns
  typed instances in any process that imports it — including for records
  uploaded before the class existed. Processes without the class (e.g. the
  admin app) see bare Assets, which is all the browse table needs. The
  "registry-driven everything" design below is updated accordingly.
- Known trade-off: a typo'd built-in key (`alignmnet`) lands as a generic
  record instead of erroring. The page shows an inline "unregistered asset
  key, will be stored as generic" notice; the MCP path returns the same hint
  rather than rejecting.
- **Ownership: server-stamped `_owner` keyvalue** (attribution, not
  enforcement — the Pinata JWT is shared). Stamped from the OAuth session at
  sign time on the page and from a `STARGAZER_OWNER` env var on SDK/MCP
  uploads; never typed by the user. Underscore-prefixed keys become the
  reserved system-key namespace. Details in the Design section below; this
  supersedes the "per-user ownership tagging" out-of-scope deferral.
- **Hosted visibility model: Public / Private browse tabs** (the two Pinata
  networks). Public = every public-network asset in the account, owner
  column + optional "Mine" filter. Private = **only** records with
  `_owner == session user` — **fail closed**: an unstamped private record
  is shown to nobody, so someone else's asset can never leak into another
  user's private view by missing its label. (Unowned private records remain
  reachable via SDK/MCP.)
  `_owner` is stamped on public uploads too: it's publisher attribution
  (in Pinata account keyvalues, not the public file bytes; with anonymous
  public browsing — next bullet — these usernames are world-visible,
  accepted as the GitHub-attribution norm). Private filtering happens
  server-side in `/assets/list`, but is page-level only — the shared JWT
  means SDK/MCP can still query anything.
- **Public browsing is anonymous.** Schema, public listing, and public
  downloads need no session — public bytes are world-readable IPFS anyway,
  so gating the index was security theater. The admin serves the public
  listing from an in-process TTL cache (`app/assets.py::PUBLIC_CACHE_TTL`,
  60s, refreshed lazily) with filters applied in-memory: a semi-static
  read-only mirror, not an open per-request proxy to the Pinata API. Rate
  limiting of the anonymous surface is deferred until it's needed — and
  note that surface includes **gateway bandwidth**: `PINATA_GATEWAY`
  reaches the admin pod via `STARGAZER_ENV_VARS` with no per-user
  branching, so when the hosted deploy sets a dedicated (metered)
  `*.mypinata.cloud` gateway, anonymous download redirects spend account
  bandwidth just like authed ones. **Split-gateway policy implemented**
  (2026-06-10): anonymous public downloads redirect to
  `PUBLIC_FALLBACK_GATEWAY` (`dweb.link`); only session-holders go through
  `PINATA_GATEWAY`. Real rate limiting stays deferred.
  **UI obligation (piece 3):** the private/public upload toggle must say
  public = *anyone on the internet* can browse it (not "anyone on the
  deployment"), and anonymous visitors get a public-tab-only page with a
  sign-in link.

### 2026-06-09

- **Page is on hold** — not built yet. When built, it will **always** query
  from and write to Pinata (never the local TinyDB path), and uploads will use
  **Pinata signed upload URLs**: the admin validates metadata + mints a signed
  URL, the browser PUTs bytes straight to Pinata, so the bulk data plane never
  transits the admin pod. *(2026-06-10: the body below has been rewritten to
  this signed-URL design — the original multipart-through-the-pod sketch is
  gone.)* Pinata's sign endpoint bakes `filename`, `keyvalues`, `group_id`,
  `max_file_size`, and `allow_mime_types` into the URL at mint time, so all
  metadata is fixed server-side at validation; the browser only supplies bytes.
- **Landed independently (storage layer):** remote uploads now stage the file
  into `local_dir` via `LocalStorageClient._stage_in_cache` (hardlink, copy2
  cross-fs fallback), so the download cache hits on warm-pod reuse / local
  in-process runs without a Pinata round-trip. Caching is now a property of
  `upload()` rather than each task writing under `local_dir`.
  Tests: `tests/unit/test_storage_cache.py`.
- **Landed independently (cleanup):** removed the dead standalone
  `genomics_db_import` task (superseded by `joint_call_gvcfs`, which keeps the
  GenomicsDB workspace pod-local in a tmpdir). Dropped its module, the
  `tasks/__init__.py` export, the registry test entry, and the `catalog.md` /
  `api.md` doc rows. The `tool_refs/gatk/genomicsdbimport.md` CLI reference
  stays (still used by `joint_call_gvcfs`).


## Goal

A first-class dashboard surface for uploading files into the asset/metadata
system and browsing what's there. Users navigate from the dashboard to
`/assets`, pick a file, choose an asset type, fill in that type's metadata
fields, and upload. A query/browse table shows existing assets filtered by
metadata.

**v1 scope is upload + browse + download.** No page-level delete yet (the MCP
`delete_file` tool remains the escape hatch). **Metadata edit landed**
(2026-06-11, plan 21 follow-up): Pinata's `PUT` merges keyvalues in place
without touching bytes/CID, so a mis-tagged record is fixed via the detail
card's **Edit metadata** (owner-only, fail-closed) or the MCP `update_file`
tool — no more delete-and-re-upload, and `*_cid` edges survive.

This is a web-tier feature: routes + template live in `app/`, with two small
SDK additions (`stargazer.assets`): the bare-Asset keyvalues round-trip
(Piece 0) and a shared `build_asset()` validator so the page and the MCP
server validate uploads identically.

## Design

### Registry-driven typed assets, bare Asset catchall

`ASSET_REGISTRY` is the single source of truth for *typed* assets. The page
never hardcodes asset types or fields:

- `GET /assets/schema` returns, per asset key, the declared fields from
  `dataclasses.fields()` + `get_type_hints()`: `{name, type, default}` where
  `type` is one of `str | int | bool | list | ...` (hint's `__name__`).
- The upload form is rendered dynamically from that schema (text inputs for
  `str`, number for `int`, checkbox for `bool`).
- Non-`str` values are JSON-encoded by the JS before submit, matching the
  `from_keyvalues()` contract (`"true"`, `"12"`), so the server is a thin
  pass-through.

Unregistered asset keys are first-class, not an error: the asset-type
`<select>` includes a "custom" option that swaps the schema-driven fields for
a required asset-key text input plus a free-form key/value-row editor. The
server stores those keyvalues verbatim as a bare `Asset`.

### Groundwork: bare Asset keyvalues round-trip (`asset.py`)

Bare `Asset` currently *cannot* carry metadata: `to_keyvalues()` returns `{}`
when `_asset_key` is empty, base `from_keyvalues()` ignores the kv dict, and
the `specialize()` fallback drops `kv` entirely — even though the class
docstring already promises a `keyvalues` attribute "for base Asset instances
only". Fix before anything else:

- Add a `keyvalues: dict[str, str]` field to base `Asset`; `to_keyvalues()` /
  `from_keyvalues()` pass it through verbatim on bare instances (typed
  subclasses unchanged).
- `specialize()` fallback preserves the record's keyvalues instead of
  returning an empty `Asset(cid=cid, path=path)`.
- **Parse strictness is asymmetric: strict at upload, graceful at query.**
  At the API boundary, `build_asset()` raises on malformed values for
  registered keys (non-`str` fields must `json.loads`-parse) → 400, so bad
  values never enter storage through validated paths. At query time,
  `specialize()` catches parse errors from `from_keyvalues()`
  (`json.JSONDecodeError`) and falls back to a bare Asset carrying the
  keyvalues verbatim. Rationale: free-form records predate their class —
  once a user registers a class, one legacy record whose values don't parse
  (`True` instead of `true`) must degrade to generic, not crash the whole
  `assemble()`. Falling back also beats silently coercing to field defaults,
  which would fabricate values.

### Routes (new module `app/assets.py`, APIRouter included by admin_app)

| Route | Auth | Behavior |
|-------|------|----------|
| `GET /assets` | none | Render `assets.html`; anonymous visitors get the public tab only |
| `GET /assets/schema` | none | Registry schema for the dynamic form (field names aren't sensitive; the public type filter wants them too) |
| `GET /assets/list?<kv filters>&network=` | public: none / private: session → else 401 | Public: full listing from the TTL cache, filters applied in-memory. Private: Pinata query with `_owner == session user` forced **server-side** (fail closed: unowned and other-owned excluded, whatever the query string says) |
| `POST /assets/sign` | session → else 401 JSON | JSON `{filename, keyvalues, network}` (must include `asset`; `network` defaults to private). Validate via `build_asset()`, stamp `_owner` from the session, mint a Pinata signed upload URL for that network with `filename` + the final keyvalues baked in (plus `MAX_UPLOAD_BYTES`), return `{url, keyvalues}` |
| `GET /assets/download/{cid}?network=` | public: none / private: session → else 401 | 302 redirect — public to the IPFS gateway, private to a Pinata signed download URL; bytes bypass the pod both ways |

Upload data plane (two-phase, browser → Pinata):

1. Page POSTs `{filename, keyvalues}` to `/assets/sign`. The server validates
   and mints; because metadata is fixed at mint time, the browser can never
   attach keyvalues that weren't validated — only the bytes bypass the pod.
2. Browser uploads the file directly to the signed URL; Pinata's upload
   response carries the new file record (cid).
3. Page renders the new row optimistically from that response — Pinata's
   list API can lag a fresh upload, so a bare "refresh after upload" would
   make the file appear missing. The browse table merges the optimistic row
   until `/assets/list` returns it.

Failure handling: signed URLs are short-lived; an abandoned mint stores
nothing (the metadata only exists inside the unused URL, which expires). On
upload failure the page re-mints and retries — mint is cheap and idempotent
from the user's perspective.

Validation errors (missing `asset` key; undeclared field on a *registered*
key) → 400 JSON with the allowed keys, mirroring the MCP `upload_file`
behavior. Unregistered keys are not an error — the response notes the asset
will be stored as generic.

### Shared validator: `stargazer.assets.build_asset()`

```python
def build_asset(keyvalues: dict[str, str], path: Path | None = None) -> Asset:
    """Construct an Asset from keyvalues, typed when the key is registered.

    Raises ValueError when keyvalues["asset"] is missing, or when the key is
    registered and keyvalues contain fields the asset class does not declare.
    Unregistered keys construct a bare Asset carrying the keyvalues verbatim.
    """
```

`app/assets.py` and `src/stargazer/server.py::upload_file` both call it —
the inline validation currently in `server.py` moves here (and loosens:
unregistered keys no longer raise).

### Ownership: server-stamped `_owner`

**`_owner` is strictly optional — never a gate.** Solo/local users running
against their own Pinata account must work with zero extra env vars: uploads
never require `STARGAZER_OWNER` (unset → the key is simply absent), and no
SDK/MCP query path filters by owner unless explicitly asked. Only the hosted
browse page defaults to a "Mine" filter. Do not make this required at any
boundary — the field only earns its keep on the shared-JWT deployment, where
the launcher sets the env var automatically.

**Hosted invariant: every hosted write path stamps.** On the shared
deployment, all three asset-producing surfaces carry an owner — page
uploads (session stamp at sign time), workspace MCP/SDK uploads
(launcher-injected `STARGAZER_OWNER`), and pipeline outputs (env forwarded
into task pods at run submission). An unowned record on the hosted
deployment is therefore legacy data or a stamping bug, never an expected
state. This is what makes the fail-closed private tab the right shape: it
quarantines stamping failures instead of leaking them, and the remedy is
always to re-attribute the record (MCP delete/re-upload), never to weaken
the filter.

Uploads on owner-aware deployments carry an `_owner` keyvalue, stamped
server-side — users never type it:

- **Page:** `/assets/sign` stamps `session.github_username` into the
  keyvalues *after* `build_asset()` validation, so it's baked into the
  signed URL and unforgeable from the browser.
- **SDK/MCP:** `PinataClient.upload()` stamps from the `STARGAZER_OWNER` env
  var when set (the workspace launcher knows the github username and should
  inject it into workspace pods). Unset → record stored without `_owner`.
  - **Precedence: env wins.** When `STARGAZER_OWNER` is set the client
    overwrites any `_owner` already in the keyvalues — a bare Asset
    rehydrated from a query must not carry the previous owner onto a
    re-upload of a derived artifact. Unset → keyvalues pass through
    untouched (explicit manual attribution stays possible in scripts).
  - `_owner` is never a dataclass field — typed assets can't emit it from
    `to_keyvalues()`; the client stamp is the only typed-path source. A
    hand-built bare `Asset(keyvalues={"_owner": ...})` uploaded directly via
    the SDK bypasses `build_asset()` and is not blocked — consistent with
    attribution-not-enforcement (SDK users hold the shared JWT anyway).
  - **Task-pod gap:** Flyte task pods don't inherit the workspace pod's env,
    so workflow-produced assets will stamp as "shared" unless
    `STARGAZER_OWNER` is propagated into the run (e.g. forwarded at
    submission via TaskEnvironment env vars). Because the private tab fails
    closed, unowned private outputs are invisible on the page — closing
    this gap is part of the feature, not a follow-up.

Conventions and limits:

- **Underscore-prefixed keys are the reserved system namespace.**
  `build_asset()` rejects user-supplied `_*` keys (→ 400) for typed and
  custom assets alike, and dataclass fields must not start with `_`.
  (`asset` predates the convention and stays as is.)
- Records without `_owner` (legacy uploads, env unset) render as **shared**
  in the browse table. "Mine" is just an `_owner=<username>` filter through
  the existing keyvalue query machinery — works from `assemble()` too.
- **Stamping is network-independent: public uploads carry `_owner` too.**
  On the public network it's publisher attribution (byline + "Mine" filter
  among public assets). Dropping it for public would be the only
  irreversible branch — keep one rule. Keyvalues are account metadata, not
  file content: usernames are never written into the public IPFS bytes.
- **Private tab fails closed: `_owner == session user` only.** A private
  record without `_owner` appears for nobody on the page — a mislabeled or
  unstamped asset can never leak into another user's private view. The
  deliberate cost: unowned private records (legacy uploads, pipeline
  outputs until the task-pod gap closes) are invisible on the page and
  reachable only via SDK/MCP. The filter is enforced server-side in
  `/assets/list`, never in the browser.
- **Attribution, not authorization:** the Pinata JWT is shared, so anyone
  with SDK/MCP access can still read or delete anything. Enforcement would
  need per-user credentials or server-mediated mutation — out of scope.

Alternatives considered: Pinata Groups per user (native partitioning, but
adds group lifecycle management, group-aware SDK uploads, and multi-call
"all assets" views) and upload-name prefixes (lossy, and pollutes the
filename that downloads resolve against). The keyvalue rides the existing
query path everywhere, so it wins.

### Storage client

The page always talks to Pinata (decision above): `/assets/list` queries
Pinata and `/assets/sign` mints Pinata URLs; the local TinyDB path is never
used by these routes. The Pinata client is resolved via a module attribute at
call time so tests can swap in a fake. Without `PINATA_JWT` in the pod env,
`/assets` renders an explicit "Pinata not configured" state rather than
half-working against TinyDB.

**Deploy note:** the admin AppEnvironment does not currently carry
`PINATA_JWT`. App-pod `secrets=[...]` is dropped by this Flyte build (see
devbox workarounds), so bake it the same way as the OAuth secrets: add
`PINATA_JWT` to the optional runtime-secret names in `admin_app.py` so it's
injected into `env_vars` when present in the deployer's shell.

### Frontend (`app/templates/assets.html`)

Same conventions as `dashboard.html`: extends `base.html`, vanilla JS,
`fetch` + JSON, header with brand link back to `/`. Dashboard header gains
an "Assets" nav link.

- Upload panel: file input, asset-type `<select>` (from schema, plus a
  "custom" option), dynamic field inputs → sign → direct upload → show
  resulting CID + keyvalues.
  - **Typed and custom modes must be visually distinct** (e.g. the custom
    editor gets its own styling and the generic notice), or the strictness
    asymmetry reads as arbitrary: `asset=alignment` + an extra field is a
    400, while `asset=alignmnet` + anything succeeds as generic. The two
    modes are different contracts and should look like it.
  - Custom mode: required asset-key text input + free-form key/value rows.
    The editor reserves `asset`, `cid`, `name`, and all `_`-prefixed keys
    as row keys, shows the
    inline "unregistered asset key, will be stored as generic" notice, and
    hints that non-text values should be JSON-encoded (`true`, `42`) so the
    record can type-promote when a class is later registered.
  - Network choice: **private / public radio, default private**, passed to
    `/assets/sign`. Public means **anyone on the internet** can browse it
    (the public listing is anonymous) — the form must say exactly that
    next to the radio.
  - A short note sets size expectations: uploads are capped at **100MB —
    a Pinata limit, not ours** (plain multipart POST; larger requires the
    TUS resumable endpoint, ROADMAP item 20, which also covers the SDK's
    identical ceiling). `MAX_UPLOAD_BYTES` mirrors the cap into the signed
    URL so oversized files fail at mint time with an explanation, not
    mid-upload.
- Browse panel: **Public / Private tabs** (the two Pinata networks).
  Private shows only the session user's own records (`_owner` match,
  filtered server-side, fail closed — unowned records don't appear).
  Public shows everything on the public network with an owner column and
  an optional **Mine** filter. Both tabs:
  asset-type filter + free-form key/value filter, results table (CID, name,
  owner, metadata, per-row download link + copy-CID button).
  The asset-type filter dropdown is populated from the registry **plus the
  distinct custom keys present in the list results**, so custom assets are
  discoverable without remembering exact keys. New uploads appear as an
  optimistic row until `/assets/list` reflects them.

## Out of scope (later pieces, only if needed)

- Delete-by-CID with confirm (page-level). The MCP `delete_file` tool is the
  escape hatch. (Metadata edit has since landed — see the scope note above.)
- Ownership *enforcement* — `_owner` (Design above) is attribution +
  default filtering only; the shared Pinata JWT means anyone with SDK/MCP
  access can read or delete anything. Per-user credentials or
  server-mediated mutation is a later decision. Custom asset keys also
  remain a shared namespace across users (two users' `asset=sample_sheet`
  conventions can collide in queries); the default "Mine" browse filter
  reduces the confusion but doesn't partition `assemble()` from the SDK.
- Companion linking UI (e.g. upload R1+R2 as a pair, wiring `mate_cid`).

## Backend pre-implementation notes (2026-06-10)

Resolved by inspection:

- **Sign-route network param confirmed.** Pinata's sign request accepts
  `network` (`"public"`/`"private"`), so `/assets/sign` maps it straight
  through.
- **Stamping location is safe — exactly one metadata record.** In remote
  mode `LocalStorageClient.upload()` delegates metadata entirely to
  `PinataClient.upload()` (it only stages bytes into the cache, no TinyDB
  upsert), so stamping inside `PinataClient.upload()` — injecting `_owner`
  into the dict returned by `to_keyvalues()`, never onto the Asset object —
  cannot diverge. Typed assets couldn't hold `_owner` as an attribute
  anyway.
- **Env propagation mechanism.** Flyte v2 supports `env_vars` on
  `TaskEnvironment` and on `task.override()` (not for reusable envs). Since
  TaskEnvironment specs serialize from the submitting process, `config.py`
  can conditionally include `STARGAZER_OWNER` in each env's `env_vars` when
  it's set in the submitter's shell — workspace-submitted runs then stamp
  pipeline outputs with no per-run plumbing. Verify the serialization
  timing assumption when implementing Piece 1.

Confirm during implementation (none block starting):

- [x] Signed upload URL semantics — answered empirically by
      `test_create_signed_upload_url_end_to_end` (pinata-marked, ran
      against the real API 2026-06-10): the upload response carries the
      full record (`cid`, `name`, `keyvalues`) — everything the optimistic
      browse row needs; mint-time keyvalues (incl. `_owner`) verifiably
      reach the stored record; and **signed URLs are single-use** (second
      upload → HTTP 409), so the page's re-mint-on-retry isn't optional.
- [x] `PinataClient.query()` gained a `network` parameter (single-endpoint
      query; records now carry the network they were found on).
- [x] Download route: reuses the existing `_get_signed_url` (already the
      private-download path); public redirects to the IPFS gateway.
- [x] Piece 0 details: `keyvalues` uses `field(default_factory=dict)`; the
      `asset` key lives *inside* the dict (verbatim round-trip).
- [x] `build_asset()` reserved-key error message should tell callers to
      drop system keys (queried records legitimately contain `_owner`;
      re-uploads get restamped, so the fix is always "remove it").

## Pieces

- [x] **Piece 0 — bare-Asset keyvalues round-trip.** ✅ 2026-06-10.
      `keyvalues: dict[str, str]` field (`default_factory=dict`) on base
      `Asset`; bare `to_keyvalues()` returns a **copy** verbatim (so
      `_owner` stamping can't contaminate the object); bare
      `from_keyvalues()` restores it; `keyvalues` joined `_BASE_FIELDS` so
      typed subclasses skip it in both directions. `specialize()` preserves
      keyvalues on the unregistered fallback and catches `ValueError` from
      `from_keyvalues()` (malformed record → bare Asset + log warning, not
      a crashed `assemble()`). Tests: `TestBareAssetKeyvalues` in
      `tests/unit/test_asset.py`, fallback cases in
      `tests/unit/test_specialize.py`. Note: the tutorials register a real
      `sample_sheet` class on import — tests needing an unregistered key
      must use one that nothing registers.
- [x] **Piece 1 — shared validator + owner stamping.** ✅ 2026-06-10.
      `build_asset()` in `stargazer/assets/__init__.py` (strict for
      registered keys, bare Asset for unregistered, rejects `_`-prefixed
      user keys with a "stamped automatically — remove them" message);
      `server.py::upload_file` refactored onto it and returns a `note` key
      when storing a generic asset. `pinata.py::_stamp_owner()` (called in
      `PinataClient.upload()` on the `to_keyvalues()` copy) stamps `_owner`
      from `STARGAZER_OWNER` — env wins, unset passes through.
      `config.py::_stargazer_env_vars()` forwards `STARGAZER_OWNER` into
      `STARGAZER_ENV_VARS` (both TaskEnvironments) when the submitting
      process carries it, closing the task-pod gap. Tests:
      `tests/unit/test_assets_page.py` (15).
- [x] **Piece 2 — API routes.** ✅ 2026-06-10. `app/assets.py` router
      (page, schema, list, sign, download) included by `admin_app.py`;
      `PINATA_JWT` joined `_RUNTIME_SECRETS` (optional bake); workspace
      launcher injects `STARGAZER_OWNER=<github_username>` next to
      `FLYTE_PROJECT`. Public browsing is anonymous, served from the
      lazy TTL cache; private list forces `_owner` server-side; sign
      stamps the session user after `build_asset()` validation; download
      302s (gateway / signed URL). `PinataClient` gained
      `create_signed_upload_url()` and per-network `query()`. Tests:
      `tests/unit/test_assets_routes.py` (19) — fake Pinata client via
      `app.assets._pinata_client`, fail-closed and cache behavior covered.
      The page route renders `assets.html`, which lands in Piece 3 (no
      page-route test until then).
- [x] **Piece 3 — template.** ✅ 2026-06-11. Built in its own plan:
      [`21_asset_manager_template.md`](./21_asset_manager_template.md).
      `app/templates/assets.html` (in the dashboard box, untouched reactive
      background), dependency-free graph + list views with a Graph/List
      toggle, public/private tabs, hover-peek + click-to-pin detail card with
      download, schema-driven typed/custom upload via signed URLs with
      optimistic insert; dashboard avatar menu gained an **Assets** link.
      Page-route tests in `tests/unit/test_assets_routes.py` (`TestPage`, 3).
      Docs: `app.md` + `app_internals.md` frontend sections.
- [x] **Docs (backend).** ✅ 2026-06-10. `docs/architecture/app.md` gained
      an Asset Manager section (ownership model, public/private tabs,
      anonymous public browsing); `app_internals.md` route table + asset
      mechanics; `configuration.md` covers signed-URL uploads, TUS, and the
      per-network query. `types.md` Asset/specialize sections corrected for
      the bare-Asset catchall + `build_asset()` (and the stale
      keyvalue-coercion section, drift predating this plan, fixed in
      passing). Module docstrings written with the code. Frontend docs ride
      with plan 21.
