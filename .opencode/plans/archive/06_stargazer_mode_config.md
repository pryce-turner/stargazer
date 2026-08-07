# STARGAZER_MODE Configuration Plan

## Overview

Replace the current multi-env-var configuration (`STARGAZER_LOCAL_ONLY`, `STARGAZER_PUBLIC`, `PINATA_JWT`) with a single `STARGAZER_MODE` setting that determines both execution and storage context. Storage backend is derived from mode + available credentials, eliminating invalid combinations by construction.

## Current State

Configuration is spread across multiple independent env vars with no enforcement of valid combinations:

| Variable | Purpose |
|----------|---------|
| `STARGAZER_LOCAL_ONLY` | Toggle between local filesystem and Pinata storage |
| `STARGAZER_PUBLIC` | Toggle between public and private IPFS uploads |
| `STARGAZER_LOCAL` | Path to local storage directory |
| `PINATA_JWT` | Pinata API authentication |
| `PINATA_GATEWAY` | IPFS gateway URL |

Problems:
- `STARGAZER_LOCAL_ONLY` is a double-negative name (describes what you're *not* doing)
- Nothing prevents setting `STARGAZER_LOCAL_ONLY=false` without providing `PINATA_JWT`
- `STARGAZER_PUBLIC` is orthogonal to the mode but only meaningful when using Pinata
- Tests manually toggle `default_client.local_only` as a mutable flag, coupling test setup to implementation details

### Files referencing current env vars

| File | Variables used |
|------|---------------|
| `src/stargazer/utils/pinata.py` | `STARGAZER_LOCAL_ONLY`, `STARGAZER_LOCAL`, `STARGAZER_PUBLIC`, `PINATA_JWT`, `PINATA_GATEWAY` |
| `tests/conftest.py` | `default_client.local_only` (mutated directly) |
| `tests/utils/test_pinata.py` | `default_client.local_only` (mutated directly) |
| `tests/types/test_reference.py` | `STARGAZER_LOCAL_ONLY` env var, `default_client.local_only`, `PinataClient(local_only=...)` |

## Target State

One env var controls the mode. Storage backend is derived:

```
STARGAZER_MODE=local                        → local exec, local storage
STARGAZER_MODE=local + PINATA_JWT=xxx       → local exec, pinata storage
STARGAZER_MODE=cloud                        → union exec, cloud-hosted storage (PINATA_JWT ignored)
```

| Variable | Purpose | Required |
|----------|---------|----------|
| `STARGAZER_MODE` | `local` (default) or `cloud` | No (defaults to `local`) |
| `PINATA_JWT` | Pinata API authentication | No — only used in local mode to enable Pinata storage. Ignored in cloud mode. |
| `PINATA_GATEWAY` | IPFS gateway URL | Only when using Pinata |
| `STARGAZER_LOCAL` | Path to local storage directory | No (defaults to `~/.stargazer/local`) |

Removed:
- `STARGAZER_LOCAL_ONLY` — replaced by `STARGAZER_MODE`
- `STARGAZER_PUBLIC` — Pinata visibility becomes a per-upload parameter, not a global setting

## Implementation Plan

**Prerequisite**: This plan assumes the storage client refactor (see `storage_client_refactor.md`) is completed first. That plan splits `PinataClient` into `LocalStorageClient` and `PinataClient` behind a `StorageClient` protocol, and introduces a `get_client()` factory in `storage.py`.

### Phase 1: Define StargazerMode

Add mode resolution to `src/stargazer/utils/storage.py`:

```python
from enum import Enum


class StargazerMode(Enum):
    LOCAL = "local"
    CLOUD = "cloud"


def resolve_mode() -> StargazerMode:
    mode_str = os.environ.get("STARGAZER_MODE", "local").lower()
    try:
        return StargazerMode(mode_str)
    except ValueError:
        raise ValueError(
            f"Invalid STARGAZER_MODE: '{mode_str}'. Must be 'local' or 'cloud'."
        )
```

### Phase 2: Update get_client Factory

Replace the `STARGAZER_LOCAL_ONLY` check with mode + credential-based resolution:

```python
def get_client() -> StorageClient:
    mode = resolve_mode()
    pinata_jwt = os.environ.get("PINATA_JWT")

    if mode == StargazerMode.CLOUD:
        # In cloud mode, storage is hosted — PINATA_JWT is ignored.
        return CloudStorageClient()

    # Local mode: upgrade to Pinata if JWT is available
    if pinata_jwt:
        return PinataClient()

    return LocalStorageClient()
```

### Phase 3: Remove STARGAZER_LOCAL_ONLY from PinataClient

After the storage client refactor, `PinataClient` no longer has `local_only` logic. This phase removes any remaining references:

1. Remove `local_only` parameter from `PinataClient.__init__`
2. Remove `STARGAZER_LOCAL_ONLY` reads from `PinataClient`
3. Remove the env var from the module-level docstring/comments in `pinata.py`

### Phase 4: Remove STARGAZER_PUBLIC as a Global Setting

The `public` flag becomes a per-call concern, not a client-level default:

1. Remove `public` from `PinataClient.__init__` and `LocalStorageClient.__init__`
2. Remove `STARGAZER_PUBLIC` env var reads
3. The `public` parameter on `upload_file()` remains — callers set it explicitly when needed
4. Default behavior: private uploads (the safe default)

### Phase 5: Update Tests

1. **`tests/conftest.py`**: The `setup_local_only_mode` fixture no longer mutates `default_client.local_only`. Instead, it sets `STARGAZER_MODE=local` and ensures no `PINATA_JWT` is set, then re-initializes `default_client` via the factory.

```python
@pytest.fixture(autouse=True)
def setup_local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("STARGAZER_MODE", "local")
    monkeypatch.delenv("PINATA_JWT", raising=False)
    # Re-initialize default_client as LocalStorageClient
    ...
```

2. **`tests/utils/test_pinata.py`**: Remove all `default_client.local_only = True/False` mutations. Tests that need Pinata should mock the API or skip if no JWT. Tests that need local storage get it by default.

3. **`tests/types/test_reference.py`**:
   - Remove `test_pinata_client_local_only_env_var` — replace with a test for `STARGAZER_MODE` resolution
   - Remove all `default_client.local_only` mutations
   - Tests that toggle `local_only` should instead test with a fresh `LocalStorageClient` or `PinataClient` instance

### Phase 6: Update Specs and Documentation

1. Update `.opencode/specs/filesystem_spec.md` to reference `STARGAZER_MODE` instead of `STARGAZER_LOCAL_ONLY`
2. Update any references in `AGENTS.md` or other docs

## File Changes

| File | Change |
|------|--------|
| `src/stargazer/utils/storage.py` | **Modified** — add `StargazerMode` enum, `resolve_mode()`, update `get_client()` |
| `src/stargazer/utils/cloud_storage.py` | **New** — `CloudStorageClient` implementing `StorageClient` for cloud-hosted storage |
| `src/stargazer/utils/pinata.py` | **Modified** — remove `local_only`, `public` init params, remove env var reads |
| `src/stargazer/utils/local_storage.py` | **Modified** — remove `public` init param |
| `tests/conftest.py` | **Modified** — use `STARGAZER_MODE` env var, stop mutating `local_only` |
| `tests/utils/test_pinata.py` | **Modified** — stop mutating `local_only`, use proper client instances |
| `tests/types/test_reference.py` | **Modified** — replace `local_only` env var tests with mode tests |
| `.opencode/specs/filesystem_spec.md` | **Modified** — document `STARGAZER_MODE` |

## Design Decisions

1. **Single mode, not two flags**: `STARGAZER_MODE` replaces `STARGAZER_LOCAL_ONLY`. One knob is easier to reason about. The storage backend is derived from mode + credentials, not set independently.

2. **Credential-based upgrade in local mode**: If `PINATA_JWT` is set in local mode, storage silently upgrades to Pinata. This avoids a third mode value — the user's intent is clear from providing the credential.

3. **Cloud mode always uses cloud-hosted storage**: `STARGAZER_MODE=cloud` uses a cloud-hosted storage backend with no credentials required — storage is managed by the cloud platform. `PINATA_JWT` is ignored in cloud mode to keep the behavior deterministic and avoid ambiguity.

4. **Public/private is per-upload, not global**: `STARGAZER_PUBLIC` as a global toggle was a blunt instrument. Some files should be public (reference genomes), others private (patient data). Making this a per-call parameter on `upload_file()` is more correct.

5. **Execution mode is Flyte's concern**: `STARGAZER_MODE=cloud` implies Union execution, but Stargazer doesn't configure this — the Flyte SDK handles it based on whether a Union client is configured. The mode setting is about storage and overall intent, not execution orchestration.

6. **Tests use monkeypatch, not mutation**: Instead of mutating `default_client.local_only` at runtime, tests use `monkeypatch` to set env vars and re-initialize the client. This is more realistic and doesn't leak state between tests.
