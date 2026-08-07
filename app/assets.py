"""
### Asset manager page and API routes.

APIRouter included by the admin app serving `/assets`: the registry schema
for the dynamic upload form, Pinata-backed listing, signed-URL minting for
direct browser→Pinata uploads (bytes never transit the admin pod), and
download redirects.

Auth model: the public network is truly public — schema, public listing,
and public downloads are anonymous, with the listing served from an
in-process TTL cache so the admin acts as a semi-static read-only mirror
rather than an open proxy to the Pinata API. Private listing fails closed
(`_owner == session user` only, stamped and filtered server-side), and
sign minting always requires a session.

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import asyncio
import dataclasses
import os
import time
from typing import get_type_hints

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.session import SessionData, session_from_request
from app.templates import templates
from stargazer.assets import ASSET_REGISTRY, build_asset
from stargazer.assets.asset import _BASE_FIELDS
from stargazer.utils.pinata import PinataClient

router = APIRouter()

# Pinata's plain multipart POST — the only thing a signed URL accepts from
# a fetch() upload — is capped at 100MB; larger files require the TUS
# resumable endpoint (ROADMAP: TUS support). Mirroring the cap into the
# signed URL turns an opaque mid-upload failure into a mint-time error the
# form can explain.
MAX_UPLOAD_BYTES = 100 * 1024**2

# The anonymous public tab reads this cache, refreshed lazily — stale
# entries cost at most one Pinata listing per TTL regardless of traffic.
PUBLIC_CACHE_TTL = 60.0

# Anonymous downloads redirect here instead of PINATA_GATEWAY, which may be
# a dedicated (bandwidth-metered) gateway — only session-holders spend it.
PUBLIC_FALLBACK_GATEWAY = "https://dweb.link"

# Module attributes resolved at call time so tests can swap in fakes.
_pinata_client: PinataClient | None = None
_public_cache: tuple[float, list[dict]] | None = None
# Single-flight guard for cache refreshes: concurrent anonymous requests on a
# cold/expired cache would otherwise each fan out their own Pinata listing.
_public_cache_lock = asyncio.Lock()


def _pinata() -> PinataClient:
    """The shared Pinata client, created lazily (keeps its gateway cache)."""
    global _pinata_client
    if _pinata_client is None:
        _pinata_client = PinataClient()
    return _pinata_client


def _session(request: Request) -> SessionData | None:
    """Session from the request cookie, or None (anonymous)."""
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        return None
    return session_from_request(request, secret)


def _require_session(request: Request) -> SessionData:
    """Return the session or raise 401 — for routes that require auth."""
    session = _session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return session


def _require_pinata() -> None:
    """Raise 503 when no PINATA_JWT is present — the page has no TinyDB mode."""
    if not os.environ.get("PINATA_JWT"):
        raise HTTPException(status_code=503, detail="Pinata not configured")


async def _public_records() -> list[dict]:
    """The cached full public listing, refreshed when older than the TTL.

    Refresh is single-flight: the lock is only contended on a cold/expired
    cache, and the double-check inside it means concurrent misses share one
    Pinata listing instead of each issuing their own.
    """
    global _public_cache

    def _fresh() -> bool:
        """Whether the cache exists and is within its TTL."""
        return (
            _public_cache is not None
            and time.monotonic() - _public_cache[0] <= PUBLIC_CACHE_TTL
        )

    if not _fresh():
        async with _public_cache_lock:
            if not _fresh():
                records = await _pinata().query({}, network="public")
                _public_cache = (time.monotonic(), records)
    return _public_cache[1]


@router.get("/assets")
async def assets_page(request: Request):
    """Render the asset manager. Anonymous visitors get the public tab."""
    session = _session(request)
    return templates.TemplateResponse(
        request,
        "assets.html",
        {
            "title": "Assets",
            "username": session.github_username if session else "",
            "pinata_configured": bool(os.environ.get("PINATA_JWT")),
        },
    )


@router.get("/assets/schema")
async def assets_schema() -> dict:
    """Registry schema for the dynamic upload form.

    Anonymous-readable: dataclass field names are not sensitive and the
    public tab's type filter wants them too.
    """
    schema: dict = {}
    for key, cls in sorted(ASSET_REGISTRY.items()):
        hints = get_type_hints(cls)
        schema[key] = [
            {
                "name": f.name,
                "type": getattr(hints.get(f.name), "__name__", "str"),
                "default": (
                    f.default if f.default is not dataclasses.MISSING else None
                ),
            }
            for f in dataclasses.fields(cls)
            if f.name not in _BASE_FIELDS
        ]
    return schema


@router.get("/assets/list")
async def assets_list(request: Request):
    """List assets on one network, filtered by keyvalue query params.

    Public: anonymous, served from the TTL cache with filters applied
    in-process. Private: session required; `_owner` is forced to the
    session user server-side (fail closed — unowned and other-owned
    records are never returned, whatever the query string says).
    """
    _require_pinata()
    params = dict(request.query_params)
    network = params.pop("network", "private")

    if network == "public":
        records = await _public_records()
        return [
            r
            for r in records
            if all(r["keyvalues"].get(k) == v for k, v in params.items())
        ]

    session = _require_session(request)
    params["_owner"] = session.github_username
    return await _pinata().query(params, network="private")


@router.post("/assets/sign")
async def assets_sign(request: Request):
    """Validate metadata and mint a signed upload URL (session required).

    `build_asset()` is the same choke point the MCP server uses; the
    session user is stamped as `_owner` after validation, so the minted
    URL carries exactly the validated + stamped keyvalues and the browser
    supplies bytes only.
    """
    _require_pinata()
    session = _require_session(request)

    body = await request.json()
    filename = body.get("filename", "")
    network = body.get("network", "private")
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    if network not in ("private", "public"):
        raise HTTPException(
            status_code=400, detail="network must be 'private' or 'public'"
        )
    try:
        asset = build_asset(body.get("keyvalues") or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    keyvalues = asset.to_keyvalues()
    keyvalues["_owner"] = session.github_username
    url = await _pinata().create_signed_upload_url(
        filename=filename,
        keyvalues=keyvalues,
        network=network,
        max_file_size=MAX_UPLOAD_BYTES,
    )
    return {"url": url, "keyvalues": keyvalues}


@router.post("/assets/update")
async def assets_update(request: Request):
    """Update (merge) metadata on a record the session user owns.

    Fail-closed ownership: the record's current `_owner` must match the
    session user (checked server-side, never trusted from the request), so
    nobody can rewrite another user's — or an unowned — record from the page
    even though the Pinata JWT is shared. Validation reuses `build_asset()`,
    `_owner` is re-stamped after it, and Pinata merges the patch onto the
    existing keyvalues (the CID and bytes are untouched, so provenance edges
    survive). SDK/MCP edits stay unenforced by design (shared JWT).
    """
    _require_pinata()
    session = _require_session(request)

    body = await request.json()
    cid = body.get("cid", "")
    network = body.get("network", "private")
    if not cid:
        raise HTTPException(status_code=400, detail="cid is required")
    if network not in ("private", "public"):
        raise HTTPException(
            status_code=400, detail="network must be 'private' or 'public'"
        )

    # Fail-closed: only the owner of record may edit it. Read fresh (not the
    # public TTL cache) so a mutation never decides off stale ownership.
    records = await _pinata().query({}, network=network)
    current = next((r for r in records if r["cid"] == cid), None)
    if current is None or current["keyvalues"].get("_owner") != session.github_username:
        raise HTTPException(status_code=403, detail="you can only edit assets you own")

    try:
        build_asset(body.get("keyvalues") or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    patch = dict(body.get("keyvalues") or {})
    patch["_owner"] = session.github_username
    return await _pinata().update_metadata(cid, patch, network=network)


@router.get("/assets/download/{cid}")
async def assets_download(request: Request, cid: str):
    """Redirect to the file bytes — they never transit the admin pod.

    Public files live on world-readable IPFS, so the redirect is anonymous,
    but split-gateway: only session-holders go through PINATA_GATEWAY
    (possibly dedicated and bandwidth-metered); anonymous visitors get the
    free public gateway. Private files need a session and get a short-lived
    signed URL.
    """
    network = request.query_params.get("network", "private")
    if network == "public":
        if _session(request) is not None:
            gateway = os.environ.get("PINATA_GATEWAY", PUBLIC_FALLBACK_GATEWAY)
        else:
            gateway = PUBLIC_FALLBACK_GATEWAY
        return RedirectResponse(f"{gateway}/ipfs/{cid}", status_code=302)

    _require_session(request)
    _require_pinata()
    url = await _pinata()._get_signed_url(cid)
    return RedirectResponse(url, status_code=302)
