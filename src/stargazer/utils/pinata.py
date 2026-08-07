"""
### Pinata API v3 client for IPFS file storage.

Provides async interface for authenticated Pinata operations:
- Uploading files with keyvalue metadata
- Downloading private files via signed gateway URLs
- Querying files by keyvalue pairs
- Deleting files

Used as a remote backend by LocalStorageClient when PINATA_JWT is available.

spec: [docs/architecture/configuration.md](../architecture/configuration.md)
"""

import base64
import json
import os
import time
from pathlib import Path

import aiofiles
import aiohttp

import stargazer.config  # ensure env var defaults are set  # noqa: F401
from stargazer.assets.asset import Asset

# Pinata's plain multipart POST is capped at 100MB; larger files must use the
# resumable TUS endpoint. upload() switches paths at this threshold. Chunks
# must stay under Pinata's 50MB TUS limit; per-file ceiling is 10 GiB.
TUS_THRESHOLD_BYTES = 100 * 1024**2
TUS_CHUNK_BYTES = 48 * 1024**2


def _tus_metadata(filename: str, network: str, keyvalues: dict[str, str]) -> str:
    """Encode TUS ``Upload-Metadata``: comma-joined ``key b64(value)`` pairs.

    Pinata reads ``filename``, ``network``, and ``keyvalues`` (stringified
    JSON) from this header on the creation POST.
    """
    fields = {
        "filename": filename,
        "network": network,
        "keyvalues": json.dumps(keyvalues),
    }
    return ",".join(
        f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in fields.items()
    )


def _stamp_owner(kv: dict[str, str]) -> dict[str, str]:
    """Inject ``_owner`` from STARGAZER_OWNER into upload keyvalues.

    Env wins: a rehydrated record must not carry a stale owner onto the
    re-upload of a derived artifact. With the env unset the dict passes
    through untouched, so manual attribution in scripts stays possible.
    """
    owner = os.environ.get("STARGAZER_OWNER")
    if owner:
        kv["_owner"] = owner
    return kv


class PinataClient:
    """Async client for Pinata API v3.

    Handles authenticated operations against the Pinata API: uploads,
    private downloads via signed URLs, metadata queries, and deletions.

    This is a pure remote transport — caching is handled by LocalStorageClient.

    PINATA_VISIBILITY controls upload network and query/download behavior:
    - "private": uploads as private, downloads via signed URLs, queries /files/private
    - "public": uploads as public, downloads via public gateway (handled by
      LocalStorageClient), queries /files/public

    If JWT is unset, only public downloads are possible (via LocalStorageClient's
    public gateway fallback).

    Usage:
        client = PinataClient()
        comp = Asset(path=Path("data.bam"), keyvalues={"type": "alignment"})
        await client.upload(comp)  # sets comp.cid
        files = await client.query({"type": "alignment", "sample": "NA12878"})
        await client.delete(comp)
    """

    API_BASE = "https://api.pinata.cloud/v3"
    UPLOAD_BASE = "https://uploads.pinata.cloud/v3"

    def __init__(
        self,
        jwt: str | None = None,
        visibility: str | None = None,
    ):
        """Initialize Pinata client.

        Args:
            jwt: Pinata JWT token (defaults to PINATA_JWT from config)
            visibility: "public" or "private" (defaults to PINATA_VISIBILITY from config)
        """
        self._jwt = jwt or os.environ.get("PINATA_JWT") or None
        self.visibility = visibility or os.environ["PINATA_VISIBILITY"]

    @property
    def jwt(self) -> str:
        """Get JWT token, raising error if not set."""
        if not self._jwt:
            raise ValueError(
                "PINATA_JWT not set. Provide jwt= argument or "
                "set PINATA_JWT environment variable."
            )
        return self._jwt

    def _headers(self) -> dict:
        """Get authorization headers."""
        return {"Authorization": f"Bearer {self.jwt}"}

    async def _get_gateway_domain(self) -> str:
        """Fetch the dedicated gateway domain from Pinata API."""
        if not hasattr(self, "_gateway_domain") or self._gateway_domain is None:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"{self.API_BASE}/ipfs/gateways", headers=self._headers()
                ) as response,
            ):
                response.raise_for_status()
                data = await response.json()
                rows = data["data"]["rows"]
                if not rows:
                    raise ValueError(
                        "No gateway configured in Pinata account. "
                        "Create one at https://app.pinata.cloud/gateway"
                    )
                domain = rows[0]["domain"]
                self._gateway_domain = f"https://{domain}.mypinata.cloud"
        return self._gateway_domain

    async def _get_signed_url(self, cid: str, expires: int = 300) -> str:
        """Get a signed download URL for a private file."""
        gateway = await self._get_gateway_domain()
        payload = {
            "url": f"{gateway}/files/{cid}",
            "expires": expires,
            "date": int(time.time()),
            "method": "GET",
        }
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{self.API_BASE}/files/sign",
                headers=self._headers(),
                json=payload,
            ) as response,
        ):
            response.raise_for_status()
            data = await response.json()
            return data["data"]

    async def create_signed_upload_url(
        self,
        filename: str,
        keyvalues: dict[str, str],
        network: str,
        expires: int = 300,
        max_file_size: int | None = None,
    ) -> str:
        """Mint a signed upload URL for a direct browser→Pinata upload.

        All metadata is fixed at mint time — Pinata bakes ``filename``,
        ``keyvalues``, and the size cap into the URL, so the uploader
        supplies bytes only and can never attach unvalidated metadata.

        Args:
            filename: Name the uploaded file will carry (downloads resolve
                their on-disk name from it)
            keyvalues: Validated, already-stamped metadata to bake in
            network: "private" or "public"
            expires: URL lifetime in seconds after minting
            max_file_size: Upload size cap in bytes, if any

        Returns:
            The signed upload URL
        """
        payload: dict = {
            "date": int(time.time()),
            "expires": expires,
            "filename": filename,
            "keyvalues": keyvalues,
            "network": network,
        }
        if max_file_size is not None:
            payload["max_file_size"] = max_file_size

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{self.UPLOAD_BASE}/files/sign",
                headers=self._headers(),
                json=payload,
            ) as response,
        ):
            response.raise_for_status()
            data = await response.json()
            return data["data"]

    async def upload(self, component: Asset) -> None:
        """Upload a file to IPFS via Pinata. Sets component.cid.

        Files up to ``TUS_THRESHOLD_BYTES`` go via the plain multipart POST;
        larger files use the resumable TUS endpoint (chunked, no resume yet).

        Args:
            component: Asset with path and keyvalues set
        """
        path = component.path
        if path is None:
            raise ValueError("component.path must be set before uploading")

        kv = _stamp_owner(component.to_keyvalues())
        if path.stat().st_size > TUS_THRESHOLD_BYTES:
            await self._upload_tus(component, kv)
        else:
            await self._upload_plain(component, kv)

    async def _upload_plain(self, component: Asset, kv: dict[str, str]) -> None:
        """Plain multipart POST upload (≤ TUS_THRESHOLD_BYTES)."""
        path = component.path
        url = f"{self.UPLOAD_BASE}/files"

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("file", open(path, "rb"), filename=path.name)
            data.add_field("name", path.name)
            data.add_field("network", self.visibility)
            if kv:
                data.add_field("keyvalues", json.dumps(kv))

            async with session.post(
                url, headers=self._headers(), data=data
            ) as response:
                response.raise_for_status()
                result = await response.json()
                data_obj = result.get("data", result)
                component.cid = data_obj["cid"]

    async def _upload_tus(self, component: Asset, kv: dict[str, str]) -> None:
        """Resumable TUS upload for large files (chunked, no resume yet).

        Creates an upload, streams the file in ``TUS_CHUNK_BYTES`` chunks via
        ``PATCH``, and reads the resulting CID from the ``Upload-Cid`` header
        on the completing response.
        """
        path = component.path
        metadata = _tus_metadata(path.name, self.visibility, kv)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.UPLOAD_BASE}/files",
                headers={
                    **self._headers(),
                    "Tus-Resumable": "1.0.0",
                    "Content-Type": "application/offset+octet-stream",
                    "Upload-Length": str(path.stat().st_size),
                    "Upload-Metadata": metadata,
                },
            ) as response:
                response.raise_for_status()
                location = response.headers["Location"]

            offset = 0
            cid = None
            async with aiofiles.open(path, "rb") as f:
                while True:
                    chunk = await f.read(TUS_CHUNK_BYTES)
                    if not chunk:
                        break
                    async with session.patch(
                        location,
                        headers={
                            **self._headers(),
                            "Tus-Resumable": "1.0.0",
                            "Upload-Offset": str(offset),
                            "Content-Type": "application/offset+octet-stream",
                        },
                        data=chunk,
                    ) as response:
                        response.raise_for_status()
                        offset = int(response.headers["Upload-Offset"])
                        cid = response.headers.get("Upload-Cid", cid)

        if not cid:
            raise ValueError("TUS upload completed but returned no Upload-Cid")
        component.cid = cid

    async def download_to(self, cid: str, dest: Path) -> None:
        """Download a file to dest. Uses signed URL for private, raises for public.

        Public downloads are handled by LocalStorageClient's public gateway
        fallback, so this method is only called for private visibility.

        Args:
            cid: Content identifier
            dest: Destination path to write to
        """
        if self.visibility == "public":
            raise ValueError(
                "Public downloads should use the public IPFS gateway, "
                "not signed URLs. This is a bug — LocalStorageClient "
                "should have handled this download."
            )

        download_url = await self._get_signed_url(cid)

        async with (
            aiohttp.ClientSession() as session,
            session.get(download_url) as response,
        ):
            response.raise_for_status()

            dest.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(dest, "wb") as f:
                async for chunk in response.content.iter_chunked(8192):
                    await f.write(chunk)

    async def query(
        self, keyvalues: dict[str, str], network: str | None = None
    ) -> list[dict]:
        """Query files by keyvalue metadata from Pinata API.

        By default checks both private and public Pinata endpoints and
        merges results by CID so files are found regardless of which
        network they were uploaded to; pass ``network`` to query a single
        endpoint (the asset-manager page lists per-tab).

        Args:
            keyvalues: Metadata key-value pairs to filter by
            network: "private" or "public" to query one endpoint only

        Returns:
            List of matching file records with cid, name, keyvalues, and
            the network each record was found on
        """
        seen: dict[str, dict] = {}
        networks = (network,) if network else ("private", "public")
        for visibility in networks:
            url = f"{self.API_BASE}/files/{visibility}"
            params: dict = {"pageLimit": 1000, "order": "DESC"}
            if keyvalues:
                for key, value in keyvalues.items():
                    params[f"keyvalues[{key}]"] = value

            async with aiohttp.ClientSession() as session:
                while True:
                    async with session.get(
                        url, headers=self._headers(), params=params
                    ) as response:
                        response.raise_for_status()
                        data = json.loads(await response.text())

                        for f in data.get("data", {}).get("files", []):
                            if f["cid"] not in seen:
                                seen[f["cid"]] = {
                                    "cid": f["cid"],
                                    "name": f.get("name", ""),
                                    "keyvalues": f.get("keyvalues", {}),
                                    "network": visibility,
                                }

                        next_token = data.get("data", {}).get("next_page_token")
                        if not next_token:
                            break
                        params["pageToken"] = next_token

        return list(seen.values())

    async def delete(self, component: Asset) -> None:
        """Delete a file from Pinata by querying for its internal ID first.

        Args:
            component: Asset with cid set
        """
        url = f"{self.API_BASE}/files/{self.visibility}"
        params = {"cid": component.cid}

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=self._headers(), params=params
            ) as response:
                response.raise_for_status()
                data = await response.json()
                files = data.get("data", {}).get("files", [])
                if not files:
                    return
                file_id = files[0]["id"]

            async with session.delete(
                f"{self.API_BASE}/files/{self.visibility}/{file_id}",
                headers=self._headers(),
            ) as response:
                response.raise_for_status()

    async def update_metadata(
        self,
        cid: str,
        keyvalues: dict[str, str],
        network: str | None = None,
    ) -> dict:
        """Merge keyvalues onto an existing file's metadata (Pinata PUT).

        Pinata's update is a **merge/upsert** (verified empirically): the
        supplied keys are added or overwritten, keys omitted from the patch
        are preserved — there is no key *removal*. The file bytes and CID are
        untouched (the CID is content-addressed; keyvalues live in the
        account index, not the file), so existing ``*_cid`` provenance edges
        pointing at this record stay valid. Looks up the internal file id by
        CID first (Pinata keys updates off the UUID, not the CID), then PUTs.
        ``_owner`` is restamped from ``STARGAZER_OWNER`` (env wins, unset
        passes through) so a re-attributed edit stays consistent with upload.

        Args:
            cid: Content identifier of the file to update
            keyvalues: Metadata patch to merge (partial — only these keys
                change). Reserved ``_*`` keys should be left out; ``_owner``
                is stamped here.
            network: "private" or "public"; defaults to the client visibility

        Returns:
            The updated record: ``{cid, name, keyvalues, network}``

        Raises:
            ValueError: when no file exists on ``network`` for ``cid``
        """
        network = network or self.visibility
        kv = _stamp_owner(dict(keyvalues))
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.API_BASE}/files/{network}",
                headers=self._headers(),
                params={"cid": cid},
            ) as response:
                response.raise_for_status()
                files = (await response.json()).get("data", {}).get("files", [])
            if not files:
                raise ValueError(f"No file on {network} network for cid {cid}")
            file_id = files[0]["id"]

            async with session.put(
                f"{self.API_BASE}/files/{network}/{file_id}",
                headers=self._headers(),
                json={"keyvalues": kv},
            ) as response:
                response.raise_for_status()
                data = (await response.json()).get("data", {})

        return {
            "cid": data.get("cid", cid),
            "name": data.get("name", ""),
            "keyvalues": data.get("keyvalues", kv),
            "network": network,
        }
