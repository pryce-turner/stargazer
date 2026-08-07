# Stargazer Roadmap

Upcoming work is ordered — the **next feature is at the top**. Move items into Complete (with a ✅) as they ship.

## Upcoming

1. **Admin app with embedded dashboard.** Single shared FastAPI deployment: OAuth, fork, provisioning, dashboard tile UI, `/launch` broker. Detailed plan: [`16_admin_app_with_dashboard.md`](./16_admin_app_with_dashboard.md).
2. **Per-notebook apps + marimo `--sandbox` inline deps.** Edit/Run click spawns a per-notebook app from a shared static `note` image; Python deps inlined via PEP 723; container-local fork clone on launch; cookie-validating proxy serves workspace `list`/`sync` locally. Detailed plan: [`17_per_notebook_apps.md`](./17_per_notebook_apps.md).
3. **Identity-gated production auth.** Per-notebook envs currently set `requires_auth=False` — a devbox concession where the proxy's session-cookie check is the only gate. Production needs auth gated by the user's identity. (Was an Open Issue in `docs/architecture/app.md`.)
4. **Async OAuth provisioning.** `provision_user()` runs inline in the OAuth callback, so a slow provision can outlive the browser's redirect window. Move to background provisioning + status polling. (Was an Open Issue in `docs/architecture/app.md`.)
5. **In-notebook local-vs-remote toggle UI.** Formalize the dispatch choice as a reusable `mo.ui` element (radio / segmented control) so individual cells don't need to hardcode `flyte.with_runcontext(mode="local").run` vs `flyte.run`.
6. **Marimo AI features investigation.** Determine what marimo's native AI surface offers (`mo.ai.chat` / similar), whether tool-calling is supported, and how to wire the registry catalog in.
7. **Publish `stargazer` to PyPI.** Once the package is published, notebook PEP 723 headers can pin a version (`stargazer == X.Y.Z`) instead of `[tool.uv.sources] stargazer = { path = "/stargazer", editable = true }`. Unlocks fully reproducible community notebooks without baking the source path.
8. **Upload public assets for quickstart workflow to Pinata.**
9. **Update README with CLI quickstart and bump to alpha status.**
10. **Interactive workflow for generating a DB from existing data in `STARGAZER_LOCAL`.**
11. **Condensed context files for production use (separate from dev).**
12. **Recurring docs-sync job** so architecture docs never go stale against the code.
13. **Agentic PR process** for end-to-end automated review/merge of trusted contributors.
14. **More robust logging.**
    - Per-task tags so logs can be demultiplexed.
    - One logfile per workflow execution.
    - Stop flushing to stdout/err to keep context windows clean.
    - Env vars for log level and a bool to include actual tool-call output.
15. **Data-aware caching.** Flyte's input-hash caching is solid but breaks down for keyword/metadata-based workflows — need a higher-level cache keyed on semantic inputs.
16. **`stargazer promote-task` CLI.** The mechanical step of task promotion — extract the cell function via `ast` (marimo files are valid Python), drop it into the target `src/stargazer/tasks/` module with decorator and types intact, generate a skeleton test, open a PR via the server-side GitHub flow. Waiting for real usage patterns to inform the exact UX. (Was a Roadmap note in `docs/architecture/notebook.md`.)
17. **In-notebook MCP integration.** marimo does not yet support custom MCP server configuration; when that ships upstream, the stargazer MCP server becomes a one-line config addition to the chat panel, giving the in-notebook assistant direct access to `list_tasks`, `run_task`, `query_files`, etc. (Was a Future note in `docs/architecture/notebook.md`.)
18. **Bit-for-bit snapshot reproducibility.** Snapshots currently freeze the notebook *source* only; add image-digest pinning and a CID input/output manifest so a snapshot re-run is bit-for-bit. (Was a Deferred note in `docs/architecture/app.md`.)
19. **Cohesive `marimo.toml` integration.** A root `marimo.toml` exists with `[ai] rules` carrying stargazer authoring conventions, but it's an ad-hoc artifact — no story for how it's baked into the notebook image, kept in sync with the conventions in AGENTS.md/docs, or extended (completions, future MCP wiring, per-notebook overrides). Design one deliberate marimo-config surface and remove the duplication. Subsumes the marimo-AI angle of items 6 and 17.

20. **TUS resumable uploads — browser half remaining.** Pinata's plain
    multipart POST is hard-capped at 100MB; larger files need the TUS
    resumable endpoint (per-file ceiling then 10 GiB, chunks <50MB).
    - ✅ **SDK/task outputs (2026-06-10):** `PinataClient.upload()` now
      size-branches — ≤100MB plain POST, larger streams via chunked TUS
      (`_upload_tus`, CID read from the `Upload-Cid` header on the final
      PATCH). Chunked-first: no resume yet. Verified by
      `test_tus_upload_multichunk_roundtrip` (pinata-marked).
    - ⬜ **Browser/assets page:** wire `tus-js-client` into `assets.html`
      (Piece 3 territory) so the page lifts past `MAX_UPLOAD_BYTES` (100MB).
      Confirmed empirically that **signed upload URLs speak full TUS** —
      anonymous TUS creation against a signed URL returns 201 with a signed,
      resumable Location URL whose mint-time keyvalues/filename/network/size
      cap ride in signature-protected query params, so the
      no-unvalidated-metadata property carries over. Note: the resumable
      session inherits the signed URL's `expires`, so mint generously for
      big files.
    - ⬜ **Resume** (`HEAD`-then-continue-from-offset) for both halves — the
      real payoff of TUS (survive a dropped multi-GB upload); deferred until
      a flaky large upload demands it.

## Complete

- ✅ App-tier performance & modernization audit (2026-07-06): one pooled HTTP client per process (aiohttp out of the app tier), streaming notebook proxy, `/launch/status` via a single project deployment list, gzip on the admin, single-flight public-asset cache. [`22_app_tier_performance_audit.md`](./22_app_tier_performance_audit.md)
- ✅ scRNA preprocessing tutorial rebuild (Asset → Task → Workflow → local → remote). [`archive/15_scrna_tutorial_rebuild.md`](./archive/15_scrna_tutorial_rebuild.md)
- ✅ Integrate marimo as the notebook experience (basic plumbing — per-user provisioning, in-pod execution, tutorial scaffold).
- ✅ Create Stargazer org.
- ✅ Set up GitHub Pages.
- ✅ Exhaustively link docs to code for agent traversal.
