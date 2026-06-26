#!/usr/bin/env bash
# Launch script baked into the notebook-app image at
# /usr/local/bin/launch-notebook.sh. Invoked as the per-notebook
# AppEnvironment's args:
#
#     /usr/local/bin/launch-notebook.sh <mode> <notebook_path>
#
# Three things happen on startup:
#
#   1. If the container-local /workspace is empty (no /workspace/.git),
#      clone the user's stargazer fork (default branch, `main`) into it as a
#      cone-mode SPARSE checkout of `src/stargazer` only — the work surface is
#      `src/stargazer/notebooks/workspace`, and tests/docs/app/etc. are never
#      materialized in the pod. Sparse checkout is a working-tree filter, so
#      out-of-cone files stay in the index (SKIP_WORKTREE) and commits/pushes
#      preserve them on the fork; the pod just doesn't carry them on disk. The
#      session then cd's into the workspace notebooks dir so marimo and the
#      proxy's dropdown terminal both work directly there. User notebooks live
#      and persist on `main`; storage is ephemeral; the fork is the source of
#      truth.
#   2. Start marimo on 127.0.0.1:8081 in sandbox mode (notebook's PEP 723
#      header drives the venv).
#   3. Start the cookie-validating reverse proxy on 0.0.0.0:8080 (the
#      public port). The proxy also serves /__sg__/workspace/list and
#      /__sg__/workspace/sync straight off the local /workspace directory.
#
# Pending workspace edits are committed and pushed before Knative idles the
# pod by the proxy's FastAPI shutdown hook, which fires on SIGTERM. For that to
# work the proxy (uvicorn) must be the process Flyte's `fserve` wrapper signals.
# `fserve` is PID 1; it runs this script via `Popen(cmd, shell=True)` and on
# SIGTERM forwards the signal to that ONE direct child only (it does not signal
# the whole tree). So uvicorn must end up as `fserve`'s direct child:
#   - The AppEnvironment args prepend `exec` (see app/per_notebook.py), so the
#     `sh -c` wrapper `fserve` spawns replaces itself with this script instead
#     of lingering as an intermediate shell. (Debian's /bin/sh does not always
#     exec-collapse a bare `sh -c "script"`; the explicit `exec` guarantees it.)
#   - This script then `exec`s uvicorn below, so uvicorn inherits that same
#     direct-child slot and receives the forwarded SIGTERM, running the flush.
# An intermediate shell anywhere in this chain swallows SIGTERM and the flush is
# silently skipped — losing unpushed edits at scale-to-zero.
set -euo pipefail

MODE="$1"
NOTEBOOK_PATH="$2"

WORKSPACE_DIR="/workspace"
# The only fork subtree the pod materializes / works in.
WORKSPACE_REL="src/stargazer/notebooks/workspace"
SPARSE_CONE="src/stargazer"

if [ ! -d "${WORKSPACE_DIR}/.git" ]; then
  if [ -z "${FORK_FULL_NAME:-}" ] || [ -z "${SG_POD_TOKEN:-}" ] || [ -z "${STARGAZER_ADMIN_URL:-}" ]; then
    echo "warning: FORK_FULL_NAME/SG_POD_TOKEN/STARGAZER_ADMIN_URL missing; skipping workspace clone" >&2
  else
    # No GitHub credential is baked into this pod. Exchange the signed
    # capability (SG_POD_TOKEN) for a fresh, fork-scoped, ~1h token from the
    # admin, and feed it to git via GIT_ASKPASS so it never lands in argv or
    # .git/config. FORK_FULL_NAME is the verified `owner/repo` (may be `…-1` on
    # a name collision), so clone it directly.
    SG_GIT_TOKEN=$(curl -sf -X POST \
      -H "Authorization: Bearer ${SG_POD_TOKEN}" \
      "${STARGAZER_ADMIN_URL%/}/workspace/pod-token" || true)
    if [ -z "${SG_GIT_TOKEN}" ]; then
      echo "warning: could not fetch fork token from admin; skipping workspace clone" >&2
    else
      ASKPASS=$(mktemp)
      printf '#!/bin/sh\necho "$SG_GIT_TOKEN"\n' > "${ASKPASS}"
      chmod +x "${ASKPASS}"
      echo "Sparse-cloning ${FORK_FULL_NAME} (${SPARSE_CONE}) into ${WORKSPACE_DIR}..."
      # Username in the URL is the literal `x-access-token` (not a secret); the
      # token comes only from GIT_ASKPASS, so the stored remote stays token-free.
      # `--sparse` checks out only the repo root initially; the depth-1 clone
      # still fetches all blobs for that one commit, so the follow-up
      # `sparse-checkout set` needs no network (hence no ASKPASS on it).
      SG_GIT_TOKEN="${SG_GIT_TOKEN}" GIT_ASKPASS="${ASKPASS}" GIT_TERMINAL_PROMPT=0 \
        git clone --depth 1 --sparse \
        "https://x-access-token@github.com/${FORK_FULL_NAME}.git" "${WORKSPACE_DIR}"
      rm -f "${ASKPASS}"
      git -C "${WORKSPACE_DIR}" sparse-checkout set "${SPARSE_CONE}"
      git -C "${WORKSPACE_DIR}" config user.email "${FORK_OWNER}@users.noreply.github.com"
      git -C "${WORKSPACE_DIR}" config user.name "${FORK_OWNER}"
    fi
  fi
fi

# Work directly in the workspace notebooks dir. marimo (backgrounded) and the
# proxy (exec'd) both inherit this cwd, so the dropdown terminal — and any
# `claude` launched in it — open here, inside the sparse fork checkout. Guarded
# with `|| true` so non-workspace launches (clone skipped, dir absent) stay in
# the default cwd rather than aborting under `set -e`.
cd "${WORKSPACE_DIR}/${WORKSPACE_REL}" 2>/dev/null || true

marimo "${MODE}" --sandbox "${NOTEBOOK_PATH}" \
  --port 8081 --host 127.0.0.1 --headless --no-token &

# `--timeout-graceful-shutdown` is required for the shutdown flush to run, not
# just a tuning knob. On SIGTERM uvicorn drains open connections and in-flight
# background tasks BEFORE firing the FastAPI `lifespan` shutdown (the commit+push
# of workspace edits). The proxy holds a long-lived task to the local marimo
# backend that does not drain on its own, so with the uvicorn default
# (`timeout_graceful_shutdown=None`) the drain waits forever and the flush never
# runs — verified empirically: the pod sat at "Waiting for background tasks to
# complete" indefinitely, even after the browser tab closed. A bounded timeout
# makes uvicorn cancel the lingering task and then still run `lifespan.shutdown`
# (it only skips the flush on a *second* SIGTERM / force-quit). 15s leaves ample
# room for the git push under the pod's 300s termination grace period.
exec uvicorn sg_proxy:asgi_app --host 0.0.0.0 --port 8080 \
  --timeout-graceful-shutdown 15
