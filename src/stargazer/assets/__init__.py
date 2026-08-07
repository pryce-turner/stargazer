"""
### Stargazer types for bioinformatics workflows.

spec: [docs/architecture/types.md](../architecture/types.md)
"""

import dataclasses
from pathlib import Path

from stargazer.assets.alignment import (
    Alignment,
    AlignmentIndex,
    BQSRReport,
    DuplicateMetrics,
)
from stargazer.assets.asset import _BASE_FIELDS, Asset, assemble
from stargazer.assets.reads import R1, R2
from stargazer.assets.reference import (
    AlignerIndex,
    Reference,
    ReferenceIndex,
    SequenceDict,
)
from stargazer.assets.scrna import AnnData
from stargazer.assets.variants import (
    KnownSites,
    KnownSitesIndex,
    Variants,
    VariantsIndex,
    VQSRModel,
)
from stargazer.config import logger

# Auto-populated via Asset.__init_subclass__
ASSET_REGISTRY: dict[str, type[Asset]] = Asset._registry


def specialize(record: dict) -> Asset:
    """Construct a typed Asset subclass from a raw storage record.

    Looks up the 'asset' key in the record's keyvalues against the registry
    and returns the appropriate subclass instance. Falls back to a bare
    Asset carrying the keyvalues verbatim when no type is registered in
    this process, or when the record's values don't parse against the
    registered class (strict at upload, graceful at query — one malformed
    legacy record must not crash a whole ``assemble()``).

    Pinata query records carry the file's original ``name`` but no local
    ``path``; we resolve that name against the storage client's local_dir so
    the eventual download lands on disk with the correct extension (tools
    like GATK refuse CID-named inputs).
    """
    from stargazer.utils.local_storage import get_client

    kv = record.get("keyvalues", {})
    cid = record.get("cid", "")
    path = record.get("path")
    if path is None and record.get("name"):
        path = get_client().local_dir / record["name"]
    cls = ASSET_REGISTRY.get(kv.get("asset", ""))
    if cls is None:
        return Asset(cid=cid, path=path, keyvalues=dict(kv))
    try:
        return cls.from_keyvalues(kv, cid=cid, path=path)
    except ValueError:
        logger.warning(
            f"Record {cid} claims asset={kv.get('asset')!r} but its values "
            f"don't parse as {cls.__name__}; returning it as a bare Asset"
        )
        return Asset(cid=cid, path=path, keyvalues=dict(kv))


def build_asset(keyvalues: dict[str, str], path: Path | None = None) -> Asset:
    """Construct an Asset from keyvalues, typed when the key is registered.

    The single validation choke point shared by the MCP server and the
    asset-manager page: registered asset keys validate strictly against
    their dataclass schema; unregistered keys are first-class and construct
    a bare Asset carrying the keyvalues verbatim (the catchall — shape
    enforcement begins only once a class registers that key).

    Raises:
        ValueError: when ``keyvalues["asset"]`` is missing; when any key is
            underscore-prefixed (reserved system namespace — keys like
            ``_owner`` are stamped automatically at the storage layer, so
            callers must drop them); or when the asset key is registered and
            keyvalues contain undeclared fields or values that don't
            json-parse (strict at upload, vs. ``specialize()`` which
            degrades gracefully at query time).
    """
    asset_key = keyvalues.get("asset")
    if not asset_key:
        raise ValueError(
            "keyvalues must include 'asset'. Registered keys: "
            f"{sorted(ASSET_REGISTRY)}; unregistered keys are stored as "
            "generic assets."
        )
    reserved = sorted(k for k in keyvalues if k.startswith("_"))
    if reserved:
        raise ValueError(
            f"Reserved system keys {reserved} are stamped automatically — "
            "remove them from keyvalues."
        )
    cls = ASSET_REGISTRY.get(asset_key)
    if cls is None:
        return Asset(path=path, keyvalues=dict(keyvalues))
    declared = {f.name for f in dataclasses.fields(cls)} - _BASE_FIELDS
    unknown = sorted(set(keyvalues) - declared - {"asset"})
    if unknown:
        raise ValueError(
            f"Unknown keys for {asset_key!r}: {unknown}. Allowed: {sorted(declared)}"
        )
    return cls.from_keyvalues(keyvalues, path=path)


__all__ = [
    # Registry + helpers
    "ASSET_REGISTRY",
    # Read assets
    "R1",
    "R2",
    "AlignerIndex",
    # Alignment assets
    "Alignment",
    "AlignmentIndex",
    # scRNA-seq assets
    "AnnData",
    # Base
    "Asset",
    "BQSRReport",
    "DuplicateMetrics",
    "KnownSites",
    "KnownSitesIndex",
    # Reference assets
    "Reference",
    "ReferenceIndex",
    "SequenceDict",
    "VQSRModel",
    # Variants assets
    "Variants",
    "VariantsIndex",
    # Query
    "assemble",
    "build_asset",
    "specialize",
]
