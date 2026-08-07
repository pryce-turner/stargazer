"""
### Asset base dataclass for Stargazer.

spec: [docs/architecture/types.md](../architecture/types.md)
"""

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Self, get_type_hints

_BASE_FIELDS = frozenset(("cid", "path", "keyvalues"))


@dataclass
class Asset:
    """Base class for all typed file assets in Stargazer.

    Attributes:
        cid: Content identifier (CID) for the stored file
        path: Local filesystem path (set after download or upload)
        keyvalues: Free-form metadata for *bare* Asset instances — the
            catchall for records whose asset key has no registered class
            in this process. Serialized verbatim (the ``asset`` key lives
            inside the dict). Typed subclasses ignore this field entirely;
            they serialize their declared fields instead.

    Subclasses declare typed fields as normal dataclass attributes:

        @dataclass
        class Alignment(Asset):
            _asset_key: ClassVar[str] = "alignment"
            sample_id: str = ""
            duplicates_marked: bool = False

    Fields are plain Python attributes. ``to_keyvalues()`` serializes them to
    ``dict[str, str]`` at storage boundaries; ``from_keyvalues()`` reconstructs
    from storage. ``str`` fields pass through directly; all other types use
    ``json.dumps`` / ``json.loads``.
    """

    _registry: ClassVar[dict[str, type["Asset"]]] = {}
    _asset_key: ClassVar[str] = ""

    cid: str = ""
    path: Path | None = None
    keyvalues: dict[str, str] = field(default_factory=dict)

    def __init_subclass__(cls, **kwargs):
        """Register subclass in the asset registry."""
        super().__init_subclass__(**kwargs)
        ak = cls.__dict__.get("_asset_key", "")
        if ak:
            Asset._registry[ak] = cls

    def __setattr__(self, name: str, value: Any) -> None:
        """Enforce declared fields on typed subclasses; pass through on base Asset."""
        if self._asset_key and not name.startswith("_") and name not in _BASE_FIELDS:
            allowed = {f.name for f in dataclasses.fields(type(self))} - _BASE_FIELDS
            if name not in allowed:
                raise AttributeError(
                    f"{type(self).__name__} has no field '{name}'. "
                    f"Allowed: {sorted(allowed)}"
                )
        super().__setattr__(name, value)

    def to_keyvalues(self) -> dict[str, str]:
        """Serialize to storage format.

        str fields pass through as-is; all other types are serialized with
        json.dumps. Bare Asset instances return a copy of their keyvalues
        dict verbatim — a copy so callers (e.g. ``_owner`` stamping at the
        storage layer) can mutate the result without touching the Asset.
        """
        if not self._asset_key:
            return dict(self.keyvalues)
        hints = get_type_hints(type(self))
        result: dict[str, str] = {"asset": self._asset_key}
        for f in dataclasses.fields(self):
            if f.name in _BASE_FIELDS:
                continue
            val = getattr(self, f.name)
            result[f.name] = val if hints.get(f.name) is str else json.dumps(val)
        return result

    @classmethod
    def from_keyvalues(
        cls, kv: dict[str, str], cid: str = "", path: Path | None = None
    ) -> "Asset":
        """Reconstruct from a storage keyvalues dict.

        str fields are assigned directly; all other types are deserialized
        with json.loads (raising ``json.JSONDecodeError`` on values that
        don't parse — callers that must tolerate malformed records catch it,
        see ``specialize()``). Bare Asset receives the keyvalues verbatim.
        """
        if not cls._asset_key:
            return cls(cid=cid, path=path, keyvalues=dict(kv))
        hints = get_type_hints(cls)
        kwargs = {}
        for f in dataclasses.fields(cls):
            if f.name in _BASE_FIELDS:
                continue
            if f.name in kv:
                kwargs[f.name] = (
                    kv[f.name] if hints.get(f.name) is str else json.loads(kv[f.name])
                )
        return cls(cid=cid, path=path, **kwargs)

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        return {
            "cid": self.cid,
            "path": str(self.path) if self.path else None,
            "keyvalues": self.to_keyvalues(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Reconstruct from a serialized dict."""
        return cls.from_keyvalues(
            data.get("keyvalues", {}),
            cid=data.get("cid", ""),
            path=Path(data["path"]) if data.get("path") else None,
        )

    async def fetch(self) -> None:
        """Download this asset and all its companions from storage.

        Downloads the asset itself, then queries storage for any assets linked
        via ``{_asset_key}_cid`` to auto-download companions (e.g. indices,
        mate reads).
        """
        import stargazer.utils.local_storage as _storage

        await _storage.default_client.download(self)

        if self._asset_key and self.cid:
            companions = await assemble(**{f"{self._asset_key}_cid": self.cid})
            for a in companions:
                await _storage.default_client.download(a)

    async def update(self, path: Path, **kwargs) -> None:
        """Upload file and set cid. Shared by all asset types."""
        from stargazer.utils.local_storage import default_client

        for key, value in kwargs.items():
            if value is not None:
                setattr(self, key, value)
        self.path = path
        await default_client.upload(self)


async def assemble(**filters: Any) -> list["Asset"]:
    """Query storage by keyvalue filters and return specialized assets.

    The ``asset`` filter key accepts a string or list of strings to narrow
    by asset type. Other filters are passed through as keyvalue matchers.

    Args:
        **filters: Keyvalue filters. Values may be scalars or lists
                   (cartesian product).

    Returns:
        Flat list of specialized Asset subclass instances.

    Examples:
        assets = await assemble(build="GRCh38", asset="reference")
        ref = next(a for a in assets if isinstance(a, Reference))

        assets = await assemble(sample_id="NA12878", asset=["r1", "r2"])
        r1 = next(a for a in assets if isinstance(a, R1))
    """
    import stargazer.utils.local_storage as _storage
    from stargazer.assets import specialize
    from stargazer.utils.query import generate_query_combinations

    query_combinations = generate_query_combinations(base_query={}, filters=filters)

    seen: dict[str, dict] = {}
    for query in query_combinations:
        for record in await _storage.default_client.query(query):
            seen[record["cid"]] = record

    return [specialize(r) for r in seen.values()]
