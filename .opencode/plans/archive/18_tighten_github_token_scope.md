# Tighten GitHub Token Scope (OAuth App + GitHub App hybrid)

Security hardening for the credential the admin app holds on behalf of each user. Today the app requests a broad, long-lived OAuth token and hands it to the notebook runtime; this plan narrows it to a fork-scoped, short-lived credential and keeps it out of the pod where user code runs.

## Problem (current state)

Two independent weaknesses, the second worse than the first:

1. **Scope is too broad.** `app/oauth.py:30` requests `read:user public_repo`. `public_repo` grants **write to every public repo the user owns**, not just the fork — classic OAuth has no per-repo granularity — and the token does not expire.
2. **Exposure: the broad token reaches code the user controls.**
   - `app/per_notebook.py:205` injects it as `GITHUB_TOKEN` into the notebook pod `env_vars` → any marimo cell can read `os.environ["GITHUB_TOKEN"]`.
   - `app/launch-notebook.sh` embeds it in the clone URL → it persists in `/workspace/.git/config` (readable via `git remote -v` / `open()` in a cell).
   - `app/session.py` signs but does **not** encrypt the cookie → the token is base64-decodable client-side.

Net: a single malicious or careless notebook can exfiltrate a non-expiring token that rewrites all of the user's public repos.

## Goal

- The only credential that ever touches a notebook pod is **fork-scoped and short-lived (~1h, auto-expiring)**.
- The broad OAuth token is used **once** (login + initial fork) and then discarded — never stored in the session, never sent to a pod.
- The one high-value long-lived secret becomes the **GitHub App private key**, held only by the admin app (the trust anchor), used to mint per-fork installation tokens on demand.

## Token model (target)

| Step | Credential | Scope | Lifetime | Where it lives |
|---|---|---|---|---|
| Login + initial fork | OAuth user token (`read:user public_repo`) | all public repos | request-lifetime only | admin process memory; **discarded after fork** |
| List / get / create notebook (admin-side GitHub reads/writes) | GitHub App **installation token** | the fork only | ~1h, minted on demand | admin process memory |
| Clone / push in the notebook pod | GitHub App **installation token** via `GIT_ASKPASS` | the fork only | ~1h | pod env at boot, never written to `.git/config` |

The admin mints installation tokens from the GitHub App private key + the user's installation id (installation id is **not** a secret — it can sit in the session).

## Architecture

```
  Login (OAuth App)                     Ongoing ops (GitHub App)
  ─────────────────                     ────────────────────────
  user authorizes                       admin holds App private key
  read:user public_repo                          │
        │                                JWT ─► installation token
        ▼                                (scoped to fork, ~1h)
  fork upstream  ──────────────────────►         │
  (one-time, broad token)                        ├─► admin: list/get/create via API
  DISCARD token                                  │
                                                 └─► pod: GIT_ASKPASS clone/push
                                                     (token never in .git/config,
                                                      expires in ~1h)
```

---

## Phase 0 — Register the GitHub App (manual, external)

- [x] Create a GitHub App (org-owned). Permissions: **`contents: write`**, **`metadata: read`** — nothing else.
- [x] Generate and download a private key (PEM). This becomes `GITHUB_APP_PRIVATE_KEY`.
- [x] Record `GITHUB_APP_ID` and (if user-to-server login is used later) the App's client id/secret.
- [x] Set the install flow: "Only select repositories" so users can scope the install to their fork.
- [x] Decide the install trigger UX: redirect to install at **Enable click** (Phase 3a). Set the App's Setup URL to `{LANDING_BASE_URL}/auth/app-install-callback`.

**Verify against current GitHub docs** (APIs move; do not hardcode from memory):
- exact permission required to **create a fork** via a token — stays on the OAuth side (`public_repo`); the GitHub App does not fork.
- the **add-repo-to-installation** API + which token type it needs — `PUT /user/installations/{id}/repositories/{repo_id}` (user-to-server token); Phase 3.
- [x] whether an installation token can be restricted to a single repo within a multi-repo install — yes, via `repositories` (names) or `repository_ids` on the mint call. We scope by **name** (the fork's short name) so no numeric repo-id capture is needed.

## Phase 1 — Admin-side GitHub App client (no behavior change yet)

- [x] Add `app/installation_tokens.py`:
  - [x] `_app_jwt()` — sign a short JWT with `GITHUB_APP_PRIVATE_KEY` + `GITHUB_APP_ID` (PyJWT). 9-min RS256 JWT, 60s backdated `iat`.
  - [x] `get_installation_id(owner)` — look up the user's installation (`GET /users/{owner}/installation` with the app JWT).
  - [x] `mint_installation_token(installation_id, repo_ids=[...])` — `POST /app/installations/{id}/access_tokens` scoped to the fork; returns `(token, expires_at)`.
  - [x] A tiny in-memory cache keyed by `(installation id, repo_ids)` (tokens are ~1h; refresh within a 5-min margin of expiry).
- [x] Add the App key + id to admin config: `pyjwt[crypto]` + `aiohttp` added to the `landing` extra; `GITHUB_APP_ID` / `GITHUB_APP_PRIVATE_KEY` baked into the admin App `env_vars` **when set** (kept optional so pre-Phase-0 deploys still work). They ride the same `env_vars` baking as the OAuth secrets — see [`.opencode/reference/devbox_workarounds.md`](../reference/devbox_workarounds.md) (`secrets=[...]` is dropped on App pods).
- [x] Unit-test JWT signing and token-response parsing with a mocked GitHub. (`tests/unit/test_installation_tokens.py` — JWT claims/alg, installation lookup, repo-scoped mint, cache hit + expiry refresh.)

## Phase 2 — Route ongoing GitHub ops through installation tokens

Replace the user OAuth token in every **post-fork** call with a freshly-minted installation token.

- [x] `app/github.py` — `list_workspace`, `get_workspace_notebook`, `create_workspace_notebook`, `delete_workspace_notebook`, `_auth_headers`: already token-agnostic (take a token string), so no change needed — the *caller* now passes an installation token via `installation_tokens.fork_token(session.fork_full_name)`.
- [x] `app/admin_app.py` — every post-fork admin-side op now mints a fork-scoped installation token via `installation_tokens.fork_token(session.fork_full_name)` instead of `session.access_token`: `_resolve_workspace_files` (dashboard list), `/workspace/create` (collision + seed reads + write), `/launch` (workspace resource fetch), `/workspace/delete`.
- [x] Keep `fork_upstream` (enable) and `find_existing_fork` (login) on the OAuth token (Phase 3 owns the fork/install step).
- [x] Tests: handlers call the GitHub-App path; create + delete assert the *minted* token (never `session.access_token`) reaches the GitHub op; `_resolve_workspace_files` asserts the same for the listing fallback. (`tests/unit/test_app.py`, `tests/unit/test_installation_tokens.py`.)

> Note: the **pod's** clone/push still uses `session.access_token` (injected as `GITHUB_TOKEN`) — that's the Phase 3 exposure fix, deliberately untouched here.

## Phase 3 — Drop the OAuth token from the session and the pod (the exposure fix)

This is the core trust win. The OAuth token becomes request-scoped; pods get a short-lived fork-only token.

### 3a. OAuth token is ephemeral — DONE (chosen UX: install at Enable click; token dropped after)

Decision (vs. the plan's "remove the field"): keep the opt-in **Enable** model, so the OAuth token must survive login→Enable. It's therefore kept *only* in that window and cleared once opt-in finishes — an *enabled* session carries no OAuth token. `installation_id` is **not** stored: `installation_tokens.fork_token` resolves the install by owner on demand (cached), so the session needs no new field.

- [x] `app/admin_app.py` callback: store `access_token` **only when there's no fork yet** (`"" if fork_full_name else access_token`) — a returning, already-enabled user gets a token-free cookie immediately.
- [x] `app/session.py`: `workspace_enabled` now keys on `fork_full_name` alone (not the token), so dropping the token doesn't turn saving off. `access_token` field kept (default `""`) for the login→Enable window only.
- [x] Install choreography (one extra consent): `/workspace/enable` records the fork, then redirects to `_app_install_url()` (`https://github.com/apps/{GITHUB_APP_SLUG}/installations/new`); the user picks their fork on GitHub's "Only select repositories" page. The App's Setup URL → new `GET /auth/app-install-callback`, which **clears the OAuth token** and lands on the dashboard. No `PUT add-repo` / user-to-server token needed (the user hand-picks the fork — the plan's PUT was an optional nicety). `GITHUB_APP_SLUG` baked into the admin env when set; unset = skip install redirect (clone/push no-ops until installed).
- [x] Tests: `workspace_enabled` keys on fork; callback keeps token only for first-timers / drops it for returning forks; enable redirects to the install URL; install-callback clears the token but keeps saving on.

> Phase 0 follow-up: set the App's **Setup URL** to `{LANDING_BASE_URL}/auth/app-install-callback` and **Redirect on update** so post-install lands back in Stargazer. Export `GITHUB_APP_SLUG` (the App's URL handle) alongside the ID/key.

### 3b. Pod never gets the broad token — DONE (chosen design: callback-fetch at boot/sync)

The pod is injected a **signed capability** (`SG_POD_TOKEN`, `SESSION_SECRET`-signed via a distinct salt, carrying only the fork name — *not* a GitHub credential), never a token. It exchanges that capability at the admin's mint endpoint for a fresh, fork-scoped, ~1h token at the moment of clone/push, fed to git via `GIT_ASKPASS`.

- [x] `app/per_notebook.py`: dropped `GITHUB_TOKEN` from `env_vars`; `per_notebook_env` now takes `pod_capability` and injects it as `SG_POD_TOKEN` alongside `FORK_FULL_NAME` / `FORK_OWNER` / `STARGAZER_ADMIN_URL`.
- [x] `app/admin_app.py`: new `POST /workspace/pod-token` mint endpoint — verifies the capability (`read_pod_capability`), then `installation_tokens.fork_token(fork_full_name)`; returns the token as plain text. `/launch` now passes `pod_capability=sign_pod_capability(fork, SESSION_SECRET)`.
- [x] `app/session.py`: `sign_pod_capability` / `read_pod_capability` (distinct salt so a session cookie can't be replayed as a capability).
- [x] `app/launch-notebook.sh`: clone via `GIT_ASKPASS` against a **token-free** remote (`https://x-access-token@github.com/…`); token fetched from the admin with the capability, used in a throwaway askpass script, never in argv / `.git/config`.
- [x] `app/proxy.py` `_sync_workspace`: `_fetch_pod_git_token` re-fetches a fresh token at push time (handles ~1h expiry on long-lived pods); `_git_push` pushes via `GIT_ASKPASS`.
- [x] Tests: `test_per_notebook` (no `GITHUB_TOKEN`, has `SG_POD_TOKEN`), `test_app` (capability sign/verify + salt isolation, `/workspace/pod-token` 401/200/502), `test_proxy` (capability→token exchange).

> Residual (accepted): a notebook cell can read `SG_POD_TOKEN` and call the mint endpoint to obtain a **fork-scoped, ~1h, revocable** token — strictly weaker than today's broad non-expiring token, and meets the acceptance bar ("any token reachable from a pod is fork-scoped and ~1h"). `os.environ` exposes no GitHub credential.

> **Stronger variant (optional, "token never in pod at all"):** the pod sends its workspace diff to the admin, and the admin commits+pushes via the GitHub Contents API. Bigger change; defer unless required.

## Phase 4 — Hardening — DONE

- [x] Encrypt the session cookie: `app/session.py` now uses **Fernet** (AES-CBC + HMAC) keyed off `SESSION_SECRET` via `_fernet` (sha256 → urlsafe-b64 key); the per-notebook proxy mirrors the derivation so it still validates cookies without importing the app package. `cryptography` added to the `landing` extra and the notebook image. Contents (github id/username, pre-opt-in OAuth token) are now opaque client-side.
- [x] Confirm the GitHub App private key is the high-value long-lived secret held solely by the admin — documented in the Credential Model table. (Caveat: the **OAuth App client secret** also remains long-lived, but it only enables login + the consented one-time fork, not direct repo access; noted in `docs/architecture/app.md`.)
- [x] Scrub tokens from pod `.git/config` and argv: clone/push use `GIT_ASKPASS` against a token-free remote (`https://x-access-token@github.com/…`); no `GITHUB_TOKEN` in pod `env_vars`. Confirmed by code (3b) + `test_per_notebook` (`SG_POD_TOKEN` present, `GITHUB_TOKEN` absent).
- [x] Updated `docs/architecture/app.md`: new **Credential Model** section (token table + login/fork-vs-ongoing split + pod capability flow) and refreshed module rows.
- [x] README check: it's high-level vision/architecture only (chat frontend, Flyte, IPFS) — documents nothing about the web tier/OAuth/forking, so the security work is **not out of spec**. No flag needed; README untouched.

## Acceptance

- [x] A notebook cell running `os.environ.get("GITHUB_TOKEN")` and reading `.git/config` yields no usable long-lived credential — no `GITHUB_TOKEN` env, token-free remote.
- [x] Any token reachable from a pod is fork-scoped and expires within ~1h (installation token, scoped by fork name). *Live waiting-out-expiry verification pending a real deploy.*
- [x] An *enabled* session cookie carries no GitHub token (dropped at install callback; returning users never store it). The cookie is also encrypted. *(Residual: the OAuth token is in the encrypted cookie during the brief login→opt-in window.)*
- [x] Revoking the GitHub App install on the fork immediately cuts off all access — `fork_token` 404s, no lingering OAuth token in an enabled session. *Live verification pending a real deploy.*

## Open questions / decisions

- **One app or two?** RESOLVED for now: keep the OAuth App for login+fork and the GitHub App for everything else. A later simplification (GitHub App does user-to-server login too, dropping the OAuth App + its long-lived client secret) is a possible follow-up.
- **Pod token delivery** (3b): RESOLVED → **callback-fetch at boot/sync** (token minted at use via `POST /workspace/pod-token`).
- **Install trigger UX** (3a): RESOLVED → redirect to install at **Enable click**; setup URL `/auth/app-install-callback`.
- **Devbox secret injection** for the App private key rides on the existing `env_vars` baking gap; revisit if/when Union supports App-pod `secrets=[...]`.

## Deploy checklist (carry-over for the next real deploy)

- [ ] GitHub App settings: Setup URL = `{LANDING_BASE_URL}/auth/app-install-callback`, "Redirect on update" on.
- [ ] Export `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_SLUG` before `python -m app.admin_app`.
- [ ] Rebuild + push the notebook image (cookie format + clone path changed): `_build_and_push_notebook_image` runs in the deploy entrypoint.
- [ ] Live-verify the four acceptance rows that need a running pod (expiry, revoke).
