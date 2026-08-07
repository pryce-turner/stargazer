"""
### Resource bundle loader for Stargazer.

Discovers YAML bundle definitions from this package directory and provides
hydration into local storage. Bundles are curated sets of files identified
by CID with associated keyvalue metadata.

**With JWT (remote mode):** Files are already registered in Pinata with a
``bundle`` keyvalue. The tool downloads bytes by CID via the standard path.
No TinyDB writes occur.

**Without JWT (local mode):** The manifest's keyvalues are seeded into TinyDB
so ``assemble()`` can find them. Bytes are fetched from the public IPFS gateway.

spec: [docs/architecture/configuration.md](../architecture/configuration.md)
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml
from tinydb import Query

logger = logging.getLogger(__name__)

_BUNDLE_DIR = Path(__file__).parent


def list_bundles() -> list[dict]:
    """Return metadata for all discovered bundle YAML files.

    Returns:
        List of dicts with 'name', 'description', and 'file_count' keys.
    """
    bundles = []
    for p in sorted(_BUNDLE_DIR.glob("*.yaml")):
        with p.open() as f:
            data = yaml.safe_load(f)
        bundles.append(
            {
                "name": data["name"],
                "description": data.get("description", ""),
                "file_count": len(data.get("files", [])),
            }
        )
    return bundles


async def fetch_bundle(bundle_name: str) -> list[dict]:
    """Fetch a resource bundle by downloading files by CID.

    Bundle files are always public; downloads use the public IPFS gateway
    unconditionally so the fetch works with or without a JWT. TinyDB is
    seeded so ``assemble()`` can discover assets via local queries.

    Args:
        bundle_name: Name of the bundle (matches the 'name' field in a YAML file).

    Returns:
        List of dicts with 'cid', 'keyvalues', and 'path' for each fetched file.

    Raises:
        ValueError: If the bundle name is not found.
    """
    from stargazer.assets.asset import Asset
    from stargazer.utils.local_storage import default_client

    manifest = _load_manifest(bundle_name)
    client = default_client
    results = []

    for entry in manifest["files"]:
        cid = entry["cid"]
        manifest_kv = entry["keyvalues"]
        name = entry.get("name", "")

        if not client.remote:
            _upsert_local(cid, manifest_kv, name, client)

        comp = Asset(
            cid=cid,
            path=client.local_dir / name if name else None,
        )
        cached = await client.download(comp)

        results.append(
            {
                "cid": cid,
                "name": name,
                "keyvalues": manifest_kv,
                "path": str(comp.path),
                "cached": cached,
            }
        )

    return results


def _load_manifest(bundle_name: str) -> dict:
    """Load a bundle YAML file by name.

    Args:
        bundle_name: The 'name' field to match in YAML files.

    Returns:
        Parsed YAML dict.

    Raises:
        ValueError: If no matching bundle is found.
    """
    for p in _BUNDLE_DIR.glob("*.yaml"):
        with p.open() as f:
            data = yaml.safe_load(f)
        if data.get("name") == bundle_name:
            return data

    available = [_read_name(p) for p in _BUNDLE_DIR.glob("*.yaml")]
    raise ValueError(f"Bundle {bundle_name!r} not found. Available: {available}")


def _read_name(path: Path) -> str:
    """Read just the name field from a bundle YAML."""
    with path.open() as f:
        data = yaml.safe_load(f)
    return data.get("name", path.stem)


def _upsert_local(cid: str, keyvalues: dict[str, str], name: str, client) -> None:
    """Upsert a CID + keyvalues record into TinyDB.

    Only called in local mode (no JWT) to seed metadata for assemble().

    Args:
        cid: Content identifier.
        keyvalues: Metadata to store.
        name: Human-readable filename for the asset.
        client: LocalStorageClient instance.
    """
    now = datetime.now(UTC)
    File = Query()
    client.db.upsert(
        {
            "cid": cid,
            "name": name,
            "keyvalues": keyvalues,
            "created_at": now.isoformat(),
            "rel_path": name,
        },
        File.cid == cid,
    )
