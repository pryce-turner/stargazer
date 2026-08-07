# Storage Client Refactor Plan

## Overview

Split `PinataClient` into two focused implementations behind a common interface. The current class overloads local filesystem storage and remote Pinata IPFS storage into a single class with `if self.local_only` branches in every method. This refactor separates concerns while keeping `IpFile` unchanged.

## Current State

`PinataClient` in `src/stargazer/utils/pinata.py` handles both modes:
- **Local mode** (`STARGAZER_LOCAL_ONLY=true`): copies files to `~/.stargazer/local/`, indexes metadata in TinyDB, queries TinyDB
- **Remote mode** (default): uploads to Pinata API, queries Pinata API, downloads via IPFS gateways (public or signed private URLs)

Every method (`upload_file`, `download_file`, `query_files`, `delete_file`) branches on `self.local_only`. The class also manages gateway discovery, signed URL generation, and TinyDB lifecycle — concerns that belong to only one mode each.

A module-level `default_client = PinataClient()` singleton is imported by all 4 type modules and 3 task modules.

### Direct Consumers of `default_client`

| File | Methods Used |
|------|-------------|
| `src/stargazer/assets/reference.py` | `upload_file`, `download_file`, `local_dir` |
| `src/stargazer/assets/alignment.py` | `upload_file`, `download_file`, `local_dir` |
| `src/stargazer/assets/reads.py` | `upload_file`, `download_file`, `local_dir` |
| `src/stargazer/assets/variants.py` | `upload_file`, `download_file`, `local_dir` |
| `src/stargazer/tasks/general/hydrate.py` | `query_files` |
| `src/stargazer/tasks/gatk/apply_bqsr.py` | `download_file` |
| `src/stargazer/tasks/gatk/base_recalibrator.py` | `query_files`, `download_file`, `upload_file` |

## Target State

```
src/stargazer/utils/
├── storage.py          # StorageClient protocol + factory
├── local_storage.py    # LocalStorageClient (TinyDB + filesystem)
├── pinata.py           # PinataClient (Pinata API + IPFS gateways, slimmed)
├── ipfile.py           # IpFile dataclass (extracted, unchanged)
├── query.py            # Unchanged
└── subprocess.py       # Unchanged
```

- `IpFile` moves to its own module
- `StorageClient` is a `Protocol` with 4 async methods: `upload_file`, `download_file`, `query_files`, `delete_file`, plus `local_dir` property
- `LocalStorageClient` contains TinyDB logic, no network code, no JWT
- `PinataClient` contains Pinata API logic, no TinyDB
- A factory function `get_client()` returns the right implementation based on env vars
- `default_client` remains as the module-level singleton, created by the factory

## Implementation Plan

### Phase 1: Extract IpFile

Move `IpFile` to `src/stargazer/utils/ipfile.py`.

1. Create `src/stargazer/utils/ipfile.py` with the `IpFile` dataclass (unchanged)
2. In `pinata.py`, remove the `IpFile` class definition entirely
3. Update all imports across the codebase to use `from stargazer.utils.ipfile import IpFile`
4. Run tests to confirm nothing breaks

### Phase 2: Define StorageClient Protocol

Create `src/stargazer/utils/storage.py` with a `Protocol` class.

```python
from typing import Protocol, Optional
from pathlib import Path


class StorageClient(Protocol):
    local_dir: Path

    async def upload_file(
        self,
        path: Path,
        keyvalues: Optional[dict[str, str]] = None,
        public: Optional[bool] = None,
    ) -> IpFile: ...

    async def download_file(
        self,
        ipfile: IpFile,
        dest: Optional[Path] = None,
    ) -> IpFile: ...

    async def query_files(
        self,
        keyvalues: dict[str, str],
        public: Optional[bool] = None,
    ) -> list[IpFile]: ...

    async def delete_file(self, ipfile: IpFile) -> None: ...
```

No consumers change yet. This phase is additive only.

### Phase 3: Extract LocalStorageClient

Create `src/stargazer/utils/local_storage.py`:

1. Move the `local_only=True` branches from each `PinataClient` method into `LocalStorageClient`
2. Move TinyDB initialization, `_ipfile_from_db_record`, and local CID handling
3. `LocalStorageClient.__init__` takes only `local_dir: Optional[Path]` and `public: Optional[bool]`
4. No JWT, no gateway, no `aiohttp`

### Phase 4: Slim PinataClient

Remove all `if self.local_only` branches and TinyDB code from `PinataClient`:

1. Remove `local_only` parameter and related init logic
2. Remove TinyDB property and imports
3. Remove local CID handling from `download_file`
4. Each method now has a single code path (Pinata API)
5. `PinataClient.__init__` takes `jwt`, `gateway`, `local_dir`, `public`

### Phase 5: Factory and Default Client

Add to `src/stargazer/utils/storage.py`:

```python
def get_client() -> StorageClient:
    local_only_env = os.environ.get("STARGAZER_LOCAL_ONLY", "").lower()
    if local_only_env in ("1", "true", "yes"):
        return LocalStorageClient()
    return PinataClient()


default_client: StorageClient = get_client()
```

Update `src/stargazer/utils/__init__.py` to export from `storage.py`.

### Phase 6: Update Consumers

Update imports across consumers. The actual call sites don't change — the method signatures are identical.

1. Types (`reference.py`, `alignment.py`, `reads.py`, `variants.py`): change `from stargazer.utils.pinata import default_client, IpFile` to `from stargazer.utils.storage import default_client, IpFile`
2. Tasks (`hydrate.py`, `apply_bqsr.py`, `base_recalibrator.py`): same import update
3. Top-level `src/stargazer/__init__.py`: update exports

### Phase 7: Update Tests

1. Update `tests/fixtures/build_local_db.py` to use `LocalStorageClient` directly
2. Test files that set `local_only` on `default_client` should now get a `LocalStorageClient` by default (since tests run with `STARGAZER_LOCAL_ONLY=true`)
3. Run full test suite

### Phase 8: Update Specs

Update `.opencode/specs/filesystem_spec.md` to reflect the new storage abstraction (protocol, two implementations, factory). No code in the spec — just describe the contract and the two implementations.

## File Changes

| File | Change |
|------|--------|
| `src/stargazer/utils/ipfile.py` | **New** — extracted IpFile dataclass |
| `src/stargazer/utils/storage.py` | **New** — StorageClient protocol, factory, default_client |
| `src/stargazer/utils/local_storage.py` | **New** — LocalStorageClient |
| `src/stargazer/utils/pinata.py` | **Modified** — remove IpFile, remove local_only branches, remove TinyDB |
| `src/stargazer/utils/__init__.py` | **Modified** — update exports |
| `src/stargazer/__init__.py` | **Modified** — update exports |
| `src/stargazer/assets/reference.py` | **Modified** — import path |
| `src/stargazer/assets/alignment.py` | **Modified** — import path |
| `src/stargazer/assets/reads.py` | **Modified** — import path |
| `src/stargazer/assets/variants.py` | **Modified** — import path |
| `src/stargazer/tasks/general/hydrate.py` | **Modified** — import path |
| `src/stargazer/tasks/gatk/apply_bqsr.py` | **Modified** — import path |
| `src/stargazer/tasks/gatk/base_recalibrator.py` | **Modified** — import path |
| `tests/fixtures/build_local_db.py` | **Modified** — use LocalStorageClient |
| `.opencode/specs/filesystem_spec.md` | **Modified** — document new storage abstraction |

## Design Decisions

1. **Protocol over ABC**: `StorageClient` is a `typing.Protocol`, not an abstract base class. This avoids forcing inheritance and keeps the implementations decoupled. Duck typing is sufficient since both classes are internal.

2. **IpFile stays unchanged**: It's a data container, not a behavior class. It doesn't need to know which storage backend produced it. Extracting it to its own module is purely organizational.

3. **Factory over DI**: A simple `get_client()` factory based on env vars matches the existing pattern (env-var-driven configuration). No need for a DI container or registry.

4. **local_dir on both implementations**: Both clients need a local directory — `LocalStorageClient` for primary storage, `PinataClient` for download caching. The property stays on the protocol.

5. **Execution is not a storage concern**: Local vs remote workflow execution is handled by the Flyte SDK's own run context (`flyte run --local` vs remote). This refactor only addresses storage.
