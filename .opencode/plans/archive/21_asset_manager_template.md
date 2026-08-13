# 21 — Asset Manager Template (graph + list + upload)

Piece 3 of [plan 20](./20_asset_manager_page.md), expanded. The backend
(Pieces 0–2) has landed: `/assets`, `/assets/schema`, `/assets/list`,
`/assets/sign`, `/assets/download/{cid}` all exist in `app/assets.py`, and
`GET /assets` already renders `assets.html` — **which does not exist yet**.
This plan builds that template and its client JS. **No new backend routes**
unless a gap surfaces during implementation (see "Backend: reuse only").

## Status / decisions

### 2026-06-11

- **Graph is a second rendering of `/assets/list`, not a new endpoint.**
  Relationship edges come from the `*_cid` keyvalues already on every record
  (typed assets serialize `reference_cid`, `r1_cid`, `mate_cid`,
  `alignment_cid`, `variants_cid`, `known_sites_cid`, `source_cid` as fields;
  bare/custom assets can carry their own `*_cid` rows). So the graph and the
  list consume the *same* fetched record set; the network tab (public /
  private) and any keyvalue filters apply to both. Switching Graph⇄List
  re-renders from memory — no refetch.
- **Dependency-free SVG, no graph library.** ~~Consistent with the starfield
  ethos…~~ **Reversed 2026-06-11 (Piece 3e).** The hand-rolled SVG force layout
  + wheel-zoom/drag-pan grew to ~250 lines reinventing solved problems, and the
  next asks (node drag, better layouts) were more of the same. Swapped to
  **Cytoscape.js**, vendored as `app/static/cytoscape.min.js` (pinned 3.30.2,
  ~370 KB, no build step — the existing `/static` mount serves it). Zoom, pan,
  node-drag, the `cose` force layout, tooltips, and selection are now the
  library's job; `GRAPH_STYLE` keeps the violet constellation palette. This is
  the one deliberate break from the "ship only hand-written JS" convention
  (the starfield stays hand-rolled). **The reactive background (`#sky` canvas +
  `#sky-glow`) is still not touched** — the graph is a separate Cytoscape
  canvas inside the `.dashboard` card.
- **Same box as the dashboard.** `assets.html` extends `base.html` and renders
  inside a single `.dashboard` glassy card (same `position:relative; z-index:1`
  surface the notebook dashboard uses), with the identical header (brand link
  to `/` + user menu). The dashboard gains an **Assets** nav link; the assets
  page's brand returns to `/`. No second card, no full-bleed layout — one box,
  matching the existing surface language.
- **Graph is the default view, with a Graph/List segmented toggle.** Resolves
  the "graph at the top" + "toggle between list and graph" requirement as a
  single asset-display region defaulting to Graph; the detail card sits below
  the region in both modes. Hover peeks metadata (transient); click pins it
  into the card with a download button (persistent + actionable).
- **Network tabs are the existing public/private split, owner-scoped on
  private.** Reuses plan 20's fail-closed model verbatim — Private shows only
  the session user's `_owner` records (forced server-side), Public shows the
  anonymous cached listing with an optional "Mine" filter. Anonymous visitors
  get the Public tab only (no upload, no Private tab; a sign-in link instead).

## Goal

A single `/assets` page, in the dashboard's box, that lets a user:

1. **See the asset graph** — assets as nodes, `*_cid` links as edges. Hover a
   node to peek its metadata; click it to pin a detail card (full metadata +
   download).
2. **Toggle to a list view** of the same records (sortable table: CID, name,
   owner, type, metadata, download + copy-CID).
3. **Switch public ⇄ private** (the two Pinata networks), private being
   `_owner`-scoped and fail-closed.
4. **Upload** a file under a registered asset type *or* a bare/custom asset
   with arbitrary metadata, choosing the target network.

## Design

### Page shell & layout (`app/templates/assets.html`)

- Extends `base.html`; body is one `<div class="dashboard">`. Header mirrors
  `dashboard.html`: `.dash-header` with the brand image linking `/` and the
  `.user-menu` avatar dropdown (rendered only when `username` is set —
  anonymous public visitors get a plain **Sign in** link in its place).
- Reuse existing CSS tokens/classes from `base.html` (`--accent`, `--green`,
  `.btn*`, `.notice`, `.section-desc`, `.tile`, inputs). New asset-specific CSS
  goes in the `{% block extra_css %}` hook (already provided by `base.html`).
- Page regions, top to bottom inside the card:
  1. **Header** (brand + user menu / sign-in).
  2. **Controls bar** — network tabs (Public | Private), a Graph/List
     segmented toggle, the keyvalue/type filter inputs, and (Public only) the
     "Mine" checkbox. One row, wraps on narrow viewports.
  3. **Asset display region** — the SVG graph *or* the list table (toggle
     swaps which is shown; the other is `hidden`, not destroyed, so toggling
     is instant and selection is preserved).
  4. **Detail card** — empty/placeholder until a node/row is clicked.
  5. **Upload panel** — schema-driven form (collapsible `<details>` so it
     doesn't dominate the page; open by default for authed users, absent for
     anonymous).
- **`pinata_configured` false** → render only a `.notice` ("Pinata not
  configured") in place of regions 2–5, matching plan 20's "no half-working
  TinyDB mode" rule. The route already passes this flag.

### Shared data model (client JS)

One fetch drives everything: `GET /assets/list?network=<tab>&<filters>`
returns `[{cid, name, keyvalues, ...}]`. Cache the array in a module-level
`state.records`. Both renderers read from it; changing the network tab or
filters refetches, changing Graph/List does not.

- `assetType(rec)` = `rec.keyvalues.asset || "(bare)"`.
- `owner(rec)` = `rec.keyvalues._owner || "shared"`.
- **Edge extraction:** for each record, every keyvalue key matching
  `/_cid$/` with a non-empty value is a relationship. The edge points from
  `rec.cid` to the record whose `cid === value`; the **edge label** is the key
  with `_cid` stripped (`reference_cid` → "reference", `mate_cid` → "mate").
  Guard: ignore a bare `"cid"` key (exact match, no underscore prefix) — only
  `*_cid` suffixes count.
- **Dangling edges:** a `*_cid` value with no matching node in the current set
  (cross-network reference, owner-scoped-out, or simply not loaded) is **not
  drawn** in the graph — it would be an edge to nowhere. Instead the detail
  card lists *all* of a node's `*_cid` links, resolving in-view targets to
  their name and showing the rest as a short CID + "not in this view." This
  keeps the graph clean while never hiding provenance.

### Graph view (SVG, dependency-free)

- Render an inline `<svg>` sized to the display region. Two layers: a `<g>` of
  edge `<line>`s under a `<g>` of node groups, each node a `<circle>` (violet,
  radius scaled subtly by degree) + a short truncated-CID/`name` `<text>`
  label below it.
- **Layout:** a small force simulation in plain JS (charge repulsion +
  edge-spring attraction + center gravity), run for a fixed number of
  iterations on render then drawn statically — no animation loop competing
  with the background field. For very small graphs (≤ ~2 nodes) or fully
  disconnected sets, fall back to a deterministic circular/grid layout so a
  lone asset doesn't drift to a corner. Cap simulated nodes (e.g. 150) and
  show a "showing N of M — narrow with filters" hint past the cap, so a large
  account can't hang the page (mirrors the list's own practical limit).
- **Hover → peek:** `pointerenter` on a node raises a lightweight floating
  tooltip (absolutely-positioned div, not SVG `<title>`, so it's styled to
  match the card) listing the asset type, owner, and key/value metadata.
  `pointerleave` hides it. Connected edges + neighbor nodes get a subtle
  highlight (the same violet the cursor uses in the background), reinforcing
  the constellation language. Honor `prefers-reduced-motion` for any
  transition.
- **Click → pin:** sets `state.selected = cid`, marks the node selected
  (filled/ringed), and renders the **detail card** below. Clicking empty SVG
  space clears the selection.
- **Edges** use the brand violet at low opacity; on neighbor-highlight they
  brighten. Edge labels are optional/omitted at rest (too noisy) and shown in
  the detail card instead.

### List view (table)

Per plan 20's browse panel: a results table with columns **CID** (truncated +
copy-CID button), **name**, **owner**, **type**, **metadata** (compact
key/value summary), and a per-row **Download** link. Clicking a row pins the
same detail card the graph uses (shared `selectAsset(cid)`). The list and
graph are interchangeable views of `state.records`; nothing list-only.

### Detail card

Driven by `state.selected`. Shows, for the selected record:

- Asset type + owner badge, full CID (with copy), name.
- All metadata key/values (system `_*` keys rendered dimmed/labeled as
  system-stamped, e.g. `_owner`).
- **Linked assets:** every `*_cid` relationship — in-view targets as a
  clickable chip that re-selects that node (and, in graph mode, recenters the
  highlight); out-of-view targets as a short CID labeled "not in this view."
- **Download button** → `GET /assets/download/{cid}?network=<tab>` (302 to
  gateway / signed URL; bytes bypass the pod, per existing route).
- v1 has **no edit/delete** (plan 20 out-of-scope) — the card must not imply
  otherwise; the MCP tools remain the fix-a-typo escape hatch.

### Upload panel (schema-driven; typed + custom)

Implements plan 20's upload design — restate only what the template must do:

- Fetch `GET /assets/schema` once; populate the asset-type `<select>` with the
  registered keys **plus a "custom / bare asset" option**.
- **Typed mode:** render inputs from the schema (`str`→text, `int`→number,
  `bool`→checkbox); JSON-encode non-`str` values before submit to match the
  `from_keyvalues()` contract.
- **Custom mode (visually distinct):** a required asset-key text input + a
  free-form key/value row editor. Reserve `asset`, `cid`, `name`, and all
  `_`-prefixed keys as row keys; show the inline "unregistered asset key, will
  be stored as generic" notice; hint that non-text values should be
  JSON-encoded (`true`, `42`) so the record type-promotes once a class is
  registered. The two modes must *look* like different contracts (the
  strictness asymmetry is intentional, not arbitrary).
- **Network radio (default Private).** Public's label must say *anyone on the
  internet* can browse it. Size note: **100MB cap — a Pinata limit, not ours**
  (mint-time `MAX_UPLOAD_BYTES` fails oversize early).
- **Two-phase upload:** `POST /assets/sign {filename, keyvalues, network}` →
  receive `{url, keyvalues}` → browser PUTs the file to `url` → on the upload
  response (carries the new record's `cid`/`name`/`keyvalues`), **optimistically
  insert the new record into `state.records`** and re-render both views (a new
  graph node + new edges if its `*_cid` values resolve; a new list row). The
  optimistic record persists until a later `/assets/list` reflects it (Pinata
  list can lag a fresh upload). Single-use signed URLs → on PUT failure,
  re-mint and retry (mint is cheap/idempotent).
- Validation 400s (missing `asset`; undeclared field on a registered key;
  user-supplied `_*` key) surface inline with the allowed-keys hint from the
  response body. Unregistered key is *not* an error — show the generic notice.

### States

- **Anonymous (no session):** Public tab only, no Private tab, no upload
  panel; a "Sign in to upload or view your private assets" link (to the OAuth
  entry) in the header where the avatar would be. The page route already
  passes `username == ""` for this case.
- **Empty network:** graph and list both show a friendly empty state ("No
  assets on this network yet" / for Private, hint that unowned/pipeline
  records are reachable via SDK/MCP — the fail-closed cost from plan 20).
- **Fetch/list error:** inline error with a retry, not a blank region.

### Backend: reuse only

No new routes are expected. The graph derives entirely from `/assets/list`'s
existing `{cid, name, keyvalues}` payload. **Confirm during implementation**
that `PinataClient.query()` returns the `*_cid` keyvalues intact on both
networks (typed assets emit them via `to_keyvalues()`; the private query path
should pass keyvalues through unmodified). If any needed field is missing from
the list payload, that's a one-line additive fix in `app/assets.py`, noted
here — not a redesign.

## Tests

- **Page-route test** (deferred from plan 20 Piece 2): `GET /assets` renders
  `assets.html` 200 for both an authed session and anonymous (public-only)
  request; asserts the upload panel/Private tab are present for the session
  and absent for anonymous; asserts the "not configured" notice when
  `PINATA_JWT` is unset. Lives in `tests/unit/test_assets_routes.py` (the fake
  `app.assets._pinata_client` harness is already there).
- Client JS is vanilla and untested by the Python suite (consistent with
  `dashboard.html`); cover it via the **manual verify** below instead. Keep
  pure helpers (edge extraction, label stripping) small and obvious.

## Manual verify

Against the devbox admin deploy (or a local run with `PINATA_JWT` set):

- Graph renders nodes for listed assets; a typed asset with a `reference_cid`
  pointing to an in-view reference draws an edge labeled "reference"; a
  `*_cid` pointing out of view draws no edge but appears in the detail card.
- Hover peeks metadata; click pins the card; Download redirects to bytes.
- Graph⇄List toggle preserves selection and doesn't refetch; Public⇄Private
  refetches and re-renders both.
- Private tab shows only own `_owner` records; anonymous load shows Public
  only with no upload panel.
- Upload (typed and custom) → optimistic node/row appears immediately; an
  oversize file fails at mint with the 100MB explanation.

## Docs

- `docs/architecture/app.md` — extend the **Asset Manager** section with the
  frontend surface: graph-as-second-rendering, the constellation reuse, the
  Graph/List toggle, and that the reactive background is untouched.
- `.opencode/reference/architecture/app_internals.md` — add graph mechanics
  (edge extraction from `*_cid`, dangling-edge handling, layout, optimistic
  insert) to the Asset Manager section.
- No new doc *files* → no `zensical.toml` nav change. If that holds, say so in
  the commit so the nav-sync check is satisfied by inspection.
- Refresh the `assets.html`-adjacent docstrings only where code changes
  (`app/assets.py` page route if its render context grows).
- Mark plan 20 **Piece 3** ✅ and move plan 20 to the ROADMAP Complete section
  once this ships.

## Pieces

- [x] **Piece 3a — page shell + list + network tabs + detail card + upload.**
      ✅ 2026-06-11. `assets.html` in the dashboard box, controls bar (network
      tabs + filters + Mine), list table, shared detail card, schema-driven
      typed/custom upload with signed-URL POST + optimistic insert, all states
      (anonymous public-only, empty, error, not-configured). Page-route tests
      (`TestPage`).
- [x] **Piece 3b — graph view.** ✅ 2026-06-11. Dependency-free SVG renderer,
      static force `layout()`, `*_cid` edge extraction with dangling handling,
      hover tooltip + neighbor highlight, click-to-pin sharing `selectAsset`
      with the list, Graph/List toggle, `NODE_CAP` with overflow hint.
- [x] **Piece 3c — dashboard nav link + docs.** ✅ 2026-06-11. **Assets** link
      added to `dashboard.html`'s avatar menu; `app.md` + `app_internals.md`
      frontend sections written; plan 20 Piece 3 marked done. No new doc
      *files*, so no `zensical.toml` nav change.
- [x] **Piece 3d — metadata edit (follow-up).** ✅ 2026-06-11. Empirically
      confirmed Pinata's `PUT /v3/files/{network}/{id}` **merges** keyvalues
      (CID untouched), then wired it through all four layers:
      `PinataClient.update_metadata` + `LocalStorageClient.update_metadata`
      (SDK), MCP `update_file` tool, `POST /assets/update` route (fail-closed
      ownership), and an owner-only **Edit metadata** affordance on the detail
      card. Tests: `TestUpdate` (8, `test_assets_routes.py`) +
      `test_update_metadata_merges` (pinata-marked, run live). Live-verified
      the route end-to-end (403 non-owner, 200 owner+merge, 400 invalid) and
      the MCP tool registration/validation. Docs: mcp-server (arch+guide),
      app.md, app_internals, types.md.
- [x] **Piece 3e — Cytoscape.js graph (follow-up).** ✅ 2026-06-11. Replaced
      the hand-rolled SVG force layout + zoom/pan (~250 lines) with vendored
      **Cytoscape.js** (`app/static/cytoscape.min.js`, pinned 3.30.2). New JS:
      `graphElements()`, `GRAPH_STYLE`/`GRAPH_LAYOUT` (cose), `bindGraphEvents()`,
      `showGraphTip()`; `selectAsset()` graph branch now drives a `.selected`
      class on the `cy` instance. Gains node-drag + a robust layout for free.
      Headless-Chromium verified: render (3 nodes/2 edges), zoom, pan,
      node-drag (43 px), tap-select, dblclick-reset. Reverses the Piece 3b
      "dependency-free SVG" decision (see Status). The 31 route tests still
      pass; docs updated (app.md, app_internals, this plan).
