"""
### Cookie-validating reverse proxy in front of marimo, with workspace endpoints.

Standalone ASGI app baked into the `notebook-app` image. Listens on
the per-notebook pod's public port (8080), validates the same signed
session cookie the admin app issues (HMAC-keyed by `SESSION_SECRET`),
then forwards HTTP + websocket traffic to marimo on `127.0.0.1:8081`.

It also injects a Quake-style dropdown terminal into marimo's HTML: every
`text/html` response gets an xterm.js overlay (loaded from CDN) spliced in
before `</body>`, toggled with Ctrl+` and wired to the `/__sg__/term`
websocket below. This makes the terminal app chrome present on every notebook
page rather than a per-notebook cell.

Five reserved paths the proxy handles itself instead of forwarding:

- `GET  /__sg__/dashboard` — 302 to the admin app's URL (read from
  `STARGAZER_ADMIN_URL`). Lets notebooks link back to the dashboard
  with a stable relative path instead of plumbing the admin URL into
  each notebook's Python.
- `GET  /__sg__/ready` — unauthenticated readiness probe; returns 200
  once local marimo answers, 503 while it's still cold-starting. Polled
  by the admin app's `/launch` handler so the dashboard spinner only
  resolves once the pod is actually serving.
- `GET  /__sg__/workspace/list` — directory listing of the user's
  workspace from the pod-local `/workspace` clone of their fork. Admin
  app queries this on dashboard render so workspace state is read
  straight off disk.
- `POST /__sg__/workspace/sync` — `git add` + `git commit` + `git push`
  to the fork's `main`. No GitHub credential is baked into the pod: the push
  exchanges the signed `SG_POD_TOKEN` capability for a fresh, fork-scoped, ~1h
  token from the admin's `/workspace/pod-token` endpoint and feeds it to git
  via `GIT_ASKPASS`, so it never lands in argv or `.git/config`. Only the
  `notebooks/workspace/` dir is staged, so the fork's `main` never collides
  with upstream's shipped files. Called by the launch script's SIGTERM hook on
  idle-down, and exposed to the admin app as a "save" affordance.
- `WS   /__sg__/term` — cookie-gated PTY websocket. Spawns a login `bash`
  via `pty.fork()` and bridges it to the injected xterm.js overlay. The
  child's environment is scrubbed of auth-critical secrets (`SESSION_SECRET`,
  `SG_POD_TOKEN`, anything ending `_SECRET`/`_TOKEN`/`_JWT`/`_KEY`/`_PASSWORD`)
  so an interactive shell in the pod can't read the shared cookie key and
  forge sessions for other users. The shell still has full run of the pod's
  own ephemeral `/workspace`.

Self-contained on purpose: the notebook image installs only `fastapi`,
`uvicorn`, `itsdangerous`, `httpx`, `websockets`, `cryptography` at system
level and COPYs this file in as `/usr/local/lib/sg_proxy.py` (alongside
`terminal_overlay.html`, the injected dropdown-terminal markup it reads at
import) — no stargazer or app-package install needed, and the top-level module
name avoids colliding with Flyte's loaded_modules code bundle which
ships an `app/` package into the pod's `/home/flyte` cwd at deploy
time.

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import asyncio
import base64
import fcntl
import hashlib
import json
import os
import pty
import signal
import struct
import subprocess
import tempfile
import termios
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import httpx
import websockets
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse


SESSION_COOKIE = "sg_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days, matches app.session
LAUNCH_QUERY_PARAM = "sg_launch"
MARIMO_HOST = "127.0.0.1"
MARIMO_HTTP_PORT = 8081
WORKSPACE_ROOT = Path("/workspace")
WORKSPACE_NOTEBOOK_DIR = WORKSPACE_ROOT / "src/stargazer/notebooks/workspace"
WORKSPACE_REL = "src/stargazer/notebooks/workspace"

# Env keys never handed to the interactive shell. SESSION_SECRET is the big one:
# it's shared across pods and keys cookie validation, so a shell that could
# `echo` it could forge sessions for any user. The suffix list catches future
# secret-shaped vars without an explicit entry per name.
_TERM_SECRET_KEYS = {"SESSION_SECRET", "SG_POD_TOKEN", "PINATA_JWT"}
_TERM_SECRET_SUFFIXES = ("_SECRET", "_TOKEN", "_JWT", "_KEY", "_PASSWORD")

# Quake-style dropdown terminal markup, spliced into every marimo HTML page
# before </body>. Kept as a static asset (terminal_overlay.html) so it gets real
# HTML/CSS/JS tooling instead of living as a Python string. Read once at import.
# In the notebook image it's baked next to this module (see per_notebook.py), so
# resolving it relative to __file__ works both in-repo and in-pod. xterm.js + the
# fit addon load from CDN (browser-side, no pod dep); the overlay talks to the
# /__sg__/term PTY websocket. Self-contained — no marimo plugin needed.
_TERM_INJECTION = (Path(__file__).parent / "terminal_overlay.html").read_bytes()


def _fetch_pod_git_token() -> str | None:
    """Exchange the pod capability for a fresh fork-scoped git token.

    Calls the admin's `/workspace/pod-token` with the `SG_POD_TOKEN` capability
    baked into the pod env. Returns the bare token string, or None if the
    capability/admin URL is missing or the admin declines — the caller turns
    None into a push failure. The token is fetched at use (never persisted), so
    it's always fresh even past the ~1h installation-token expiry.
    """
    capability = os.environ.get("SG_POD_TOKEN")
    admin_url = os.environ.get("STARGAZER_ADMIN_URL")
    if not capability or not admin_url:
        return None
    try:
        resp = httpx.post(
            f"{admin_url.rstrip('/')}/workspace/pod-token",
            headers={"Authorization": f"Bearer {capability}"},
            timeout=10.0,
        )
    except Exception:
        return None
    if resp.status_code != 200 or not resp.text.strip():
        return None
    return resp.text.strip()


def _sync_workspace() -> tuple[dict, int]:
    """git add + commit + push the workspace dir to the user's fork.

    Returns `(payload, http_status)`. `{"status": "clean"}` means there were
    no on-disk changes to push. Shared by the `/__sg__/workspace/sync` route
    and the shutdown hook so manual Save and scale-to-zero use one code path.
    The push fetches a fresh fork-scoped token (`_fetch_pod_git_token`) and
    supplies it via `GIT_ASKPASS`, so no credential is stored in `.git/config`.
    """
    if not (WORKSPACE_ROOT / ".git").exists():
        return {"error": "workspace not initialized"}, 409

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(WORKSPACE_ROOT), *args], capture_output=True, text=True
        )

    add = git("add", WORKSPACE_REL)
    if add.returncode != 0:
        return {"error": "git add failed", "stderr": add.stderr}, 500
    status = git("status", "--porcelain", WORKSPACE_REL)
    if not status.stdout.strip():
        return {"status": "clean"}, 200
    # Name the touched notebook(s) in the message — the porcelain paths are the
    # only authoritative signal here (the pod isn't told its own slug). For
    # renames ("R old -> new") the last token is the new path.
    saved = sorted(
        {
            Path(line.split()[-1]).stem
            for line in status.stdout.splitlines()
            if line.strip()
        }
    )
    commit = git("commit", "-m", f"workspace: save {', '.join(saved)}")
    if commit.returncode != 0:
        return {"error": "git commit failed", "stderr": commit.stderr}, 500
    push, err = _git_push()
    if not push:
        return {"error": "git push failed", "stderr": err}, 500
    return {"status": "pushed"}, 200


def _git_push() -> tuple[bool, str]:
    """Push `HEAD:main` to origin using a freshly-minted token via GIT_ASKPASS.

    Returns `(ok, stderr)`. The remote URL is token-free
    (`https://x-access-token@github.com/...`), so git asks GIT_ASKPASS for the
    password; we point that at a throwaway script echoing the just-fetched
    token. The token lives only in the push subprocess's env and a temp file we
    delete immediately — never in argv or `.git/config`.
    """
    token = _fetch_pod_git_token()
    if not token:
        return False, "could not obtain a fork-scoped push token from the admin"
    askpass = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False)
    try:
        askpass.write('#!/bin/sh\necho "$SG_GIT_TOKEN"\n')
        askpass.close()
        os.chmod(askpass.name, 0o700)
        env = {
            **os.environ,
            "SG_GIT_TOKEN": token,
            "GIT_ASKPASS": askpass.name,
            "GIT_TERMINAL_PROMPT": "0",
        }
        push = subprocess.run(
            ["git", "-C", str(WORKSPACE_ROOT), "push", "origin", "HEAD:main"],
            capture_output=True,
            text=True,
            env=env,
        )
        return push.returncode == 0, push.stderr
    finally:
        os.unlink(askpass.name)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """On shutdown (Knative SIGTERM at scale-to-zero), flush pending edits.

    `/workspace` is ephemeral, so anything not pushed before the pod idles is
    lost. Flyte's `fserve` wrapper is PID 1 and forwards the Knative SIGTERM to
    its one direct child; the launch script's `exec` chain (args prepend `exec`,
    then the script `exec`s uvicorn) makes uvicorn that direct child, so it
    receives SIGTERM and runs this hook. See app/launch-notebook.sh — an
    intermediate shell anywhere in that chain would swallow the signal and skip
    this flush.
    """
    yield
    try:
        payload, _code = await asyncio.to_thread(_sync_workspace)
        print(f"[sg] workspace shutdown sync: {payload}")
    except Exception as exc:  # never block shutdown
        print(f"[sg] workspace shutdown sync failed: {exc}")


asgi_app = FastAPI(
    title="stargazer-notebook-proxy", docs_url=None, redoc_url=None, lifespan=lifespan
)


def _fernet(secret: str) -> Fernet:
    """Derive the session-cookie cipher from `SESSION_SECRET`.

    Mirrors `app.session._fernet` exactly (sha256 → urlsafe-base64 key) so this
    standalone proxy can decrypt the admin-issued cookie without importing the
    app package. Keep the two derivations in lockstep.
    """
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _cookie_secure() -> bool:
    """Whether the session cookie gets `Secure` — mirrors `app.config.SECURE_COOKIES`.

    Off on devbox/http, on under production TLS. This standalone proxy can't
    import `app.config`, so it re-reads `STARGAZER_SECURE_COOKIES` (which the
    admin bakes into this pod's env at launch from that single source of truth);
    keep this read in lockstep so both sides set the cookie identically.
    """
    return os.environ.get("STARGAZER_SECURE_COOKIES", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _cookie_is_valid(cookie_value: str | None) -> bool:
    """Verify the encrypted session cookie against `SESSION_SECRET`.

    Returns False (denying access) if the secret env var is missing, the
    cookie is absent, or it does not decrypt/authenticate within the max age
    window. Fernet authenticates on decrypt, so a forged cookie fails here.
    """
    secret = os.environ.get("SESSION_SECRET")
    if not secret or not cookie_value:
        return False
    try:
        _fernet(secret).decrypt(cookie_value.encode("ascii"), ttl=SESSION_MAX_AGE)
        return True
    except (InvalidToken, Exception):
        return False


@asgi_app.middleware("http")
async def redeem_launch_token(request: Request, call_next):
    """Convert an `?sg_launch=<token>` URL into a host-only session cookie.

    The admin app at `admin-…<parent>` and this per-notebook host at
    `nb-…<parent>` are sibling subdomains that intentionally use host-only
    cookies (no shared `Domain=` parent), so a notebook can't read the admin's
    cookie. So `/launch` redirects here with the signed session value as a
    one-shot query param; we set our own host-only cookie and bounce the
    browser to the clean URL.
    """
    token = request.query_params.get(LAUNCH_QUERY_PARAM)
    if not token or not _cookie_is_valid(token):
        return await call_next(request)
    remaining = {
        k: v for k, v in request.query_params.multi_items() if k != LAUNCH_QUERY_PARAM
    }
    clean = request.url.path + (f"?{urlencode(remaining)}" if remaining else "")
    response = RedirectResponse(clean, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=_cookie_secure(),
        max_age=SESSION_MAX_AGE,
        samesite="lax",
    )
    return response


# ---------------------------------------------------------------------------
# Reserved /__sg__/* endpoints (handled locally, NOT forwarded to marimo).
# Declared before the catch-all proxy routes so FastAPI matches them first.
# ---------------------------------------------------------------------------


@asgi_app.get("/__sg__/dashboard")
async def dashboard_redirect() -> Response:
    """Redirect back to the admin dashboard.

    Admin and per-notebook live on sibling subdomains, so a notebook
    can't just link to `/`. The admin pod stamps its own URL into the
    per-notebook's `STARGAZER_ADMIN_URL` env var at launch time; this
    route just 302s the browser there. No cookie check — the dashboard
    is the place users go to *re*-authenticate.
    """
    target = os.environ.get("STARGAZER_ADMIN_URL") or "/"
    return RedirectResponse(target, status_code=302)


@asgi_app.get("/__sg__/ready")
async def ready() -> Response:
    """Probe local marimo on 127.0.0.1:8081; 200 if reachable, else 503.

    Polled cross-origin from the dashboard JS so the spinner can resolve
    once the pod is actually serving. The probe runs from the browser (which
    already has a route to the notebook URL) rather than the admin pod, so
    readiness is judged from the same vantage point the user will open from.
    Hence the `Access-Control-Allow-Origin: *` header — readiness leaks no
    data so wildcard is fine. Unauthenticated by design.
    """
    headers = {"Access-Control-Allow-Origin": "*"}
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://{MARIMO_HOST}:{MARIMO_HTTP_PORT}/")
        if resp.status_code < 500:
            return Response("ready", status_code=200, headers=headers)
    except Exception:
        pass
    return Response("not ready", status_code=503, headers=headers)


@asgi_app.get("/__sg__/workspace/list")
async def workspace_list(request: Request) -> Response:
    """Return `.py` filenames in the locally-mounted workspace dir."""
    if not _cookie_is_valid(request.cookies.get(SESSION_COOKIE)):
        return Response("Unauthorized", status_code=401)
    if not WORKSPACE_NOTEBOOK_DIR.exists():
        return JSONResponse({"files": []})
    files = sorted(
        p.name
        for p in WORKSPACE_NOTEBOOK_DIR.glob("*.py")
        if not p.name.startswith("_")
    )
    return JSONResponse({"files": files})


@asgi_app.post("/__sg__/workspace/sync")
async def workspace_sync(request: Request) -> Response:
    """Commit + push pending workspace edits to the user's fork (manual Save).

    Invoked by the admin app's `/workspace/save` with the user's session
    cookie. Idle/shutdown flushes go through the proxy's `lifespan` hook
    instead, which calls `_sync_workspace` directly (no HTTP round-trip).
    """
    if not _cookie_is_valid(request.cookies.get(SESSION_COOKIE)):
        return Response("Unauthorized", status_code=401)
    payload, code = await asyncio.to_thread(_sync_workspace)
    return JSONResponse(payload, status_code=code)


def _shell_env() -> dict[str, str]:
    """Pod env with auth-critical secrets stripped, for the interactive shell.

    Drops every key in `_TERM_SECRET_KEYS` plus anything ending in a
    secret-shaped suffix, so a shell opened in the pod can't read the shared
    `SESSION_SECRET` (and thus can't forge other users' session cookies) or the
    fork capability. Sets a sane `TERM` so curses apps render.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _TERM_SECRET_KEYS and not k.endswith(_TERM_SECRET_SUFFIXES)
    }
    env.setdefault("TERM", "xterm-256color")
    return env


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    """Apply an xterm.js fit-addon size to the PTY via TIOCSWINSZ.

    Keeps the shell's notion of the terminal geometry in sync with the browser
    pane so line wrapping and full-screen TUIs (less, vim) lay out correctly.
    """
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@asgi_app.websocket("/__sg__/term")
async def term_proxy(websocket: WebSocket) -> None:
    """Bridge the injected xterm.js overlay to a login bash via a PTY.

    Cookie-gated like the marimo proxy. Forks a `bash -l` on a pseudo-terminal
    with a secret-scrubbed environment (`_shell_env`) and pumps bytes both ways:
    PTY output is read off the master fd (registered with the event loop) and
    sent as binary frames; the client sends JSON text frames — `{"type":
    "input", ...}` for keystrokes and `{"type": "resize", ...}` for geometry.
    The child is killed and reaped when either side closes.
    """
    if not _cookie_is_valid(websocket.cookies.get(SESSION_COOKIE)):
        await websocket.close(code=1008)
        return
    await websocket.accept()

    pid, master_fd = pty.fork()
    if pid == 0:  # child — becomes the shell
        try:
            os.execvpe("/bin/bash", ["/bin/bash", "-l"], _shell_env())
        except Exception:
            pass
        os._exit(1)  # exec failed

    loop = asyncio.get_running_loop()
    os.set_blocking(master_fd, False)
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _on_readable() -> None:
        """Drain the PTY master fd into the outbound queue (b'' on EOF/error)."""
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            data = b""
        queue.put_nowait(data)

    loop.add_reader(master_fd, _on_readable)

    async def pump_out() -> None:
        """Forward queued PTY output to the browser until EOF."""
        while True:
            data = await queue.get()
            if not data:
                return
            await websocket.send_bytes(data)

    async def pump_in() -> None:
        """Apply client input/resize JSON frames to the PTY until disconnect."""
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            raw = msg.get("text")
            if raw is None:
                continue
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if payload.get("type") == "input":
                os.write(master_fd, payload.get("data", "").encode())
            elif payload.get("type") == "resize":
                _set_winsize(
                    master_fd,
                    int(payload.get("rows", 24)),
                    int(payload.get("cols", 80)),
                )

    out_task = asyncio.create_task(pump_out())
    in_task = asyncio.create_task(pump_in())
    try:
        _done, pending = await asyncio.wait(
            {out_task, in_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    finally:
        loop.remove_reader(master_fd)
        os.close(master_fd)
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Catch-all HTTP + websocket proxy → marimo on 127.0.0.1:8081
# ---------------------------------------------------------------------------


@asgi_app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def http_proxy(request: Request, path: str) -> Response:
    """Forward any HTTP method to marimo on localhost:8081 after cookie check."""
    if not _cookie_is_valid(request.cookies.get(SESSION_COOKIE)):
        return Response("Unauthorized", status_code=401)

    upstream = f"http://{MARIMO_HOST}:{MARIMO_HTTP_PORT}/{path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.request(
            request.method,
            upstream,
            params=dict(request.query_params),
            headers=headers,
            content=body,
        )
    # Splice the dropdown-terminal overlay into marimo's HTML shell. Done here
    # (not as a marimo plugin) so the terminal is app chrome on every page.
    content = resp.content
    if "text/html" in resp.headers.get("content-type", "").lower():
        content = content.replace(b"</body>", _TERM_INJECTION + b"</body>", 1)

    # Strip hop-by-hop headers httpx may carry across, plus content-length —
    # the injection changes the body size, so let Starlette recompute it.
    forbidden = {
        "content-encoding",
        "transfer-encoding",
        "connection",
        "content-length",
    }
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in forbidden}
    return Response(content=content, status_code=resp.status_code, headers=out_headers)


@asgi_app.websocket("/{path:path}")
async def ws_proxy(websocket: WebSocket, path: str) -> None:
    """Bridge a client websocket to marimo's websocket after cookie check."""
    if not _cookie_is_valid(websocket.cookies.get(SESSION_COOKIE)):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    upstream_url = f"ws://{MARIMO_HOST}:{MARIMO_HTTP_PORT}/{path}"
    if websocket.url.query:
        upstream_url = f"{upstream_url}?{websocket.url.query}"

    async with websockets.connect(upstream_url) as upstream:

        async def client_to_upstream() -> None:
            """Pump frames from the browser to marimo."""
            try:
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if (data := msg.get("text")) is not None:
                        await upstream.send(data)
                    elif (data := msg.get("bytes")) is not None:
                        await upstream.send(data)
            except Exception:
                return

        async def upstream_to_client() -> None:
            """Pump frames from marimo back to the browser."""
            try:
                async for msg in upstream:
                    if isinstance(msg, bytes):
                        await websocket.send_bytes(msg)
                    else:
                        await websocket.send_text(msg)
            except Exception:
                return

        await asyncio.gather(client_to_upstream(), upstream_to_client())
