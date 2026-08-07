# Local Filesystem Plan

## Overview

Extend Stargazer to support a fully local, standalone filesystem mode using TinyDB for metadata storage. This allows users to run Stargazer entirely offline without IPFS/Pinata.

## Current State

- `STARGAZER_CACHE` env var controls cache directory (default: `~/.stargazer/cache`)
- `STARGAZER_LOCAL_ONLY` enables local mode (copies files to cache instead of uploading)
- In local mode:
  - `upload_file()` copies to cache with fake CID `local_{filename}_{size}`
  - `query_files()` still calls Pinata API (doesn't work offline)
  - No persistent metadata storage

## Target State

- `STARGAZER_LOCAL` replaces `STARGAZER_CACHE` as the local directory path
- TinyDB database (`stargazer_local.json`) stores file metadata at root of local directory
- `upload_file()` writes metadata to TinyDB when in local mode
- `query_files()` reads from TinyDB when in local mode
- Full offline operation possible

## Implementation Plan

### Phase 1: Environment Variable Rename

1. **Update `PinataClient.__init__`** (`src/stargazer/utils/pinata.py`)
   - Change `STARGAZER_CACHE` to `STARGAZER_LOCAL`
   - Rename `cache_dir` property to `local_dir`
   - Update default path from `~/.stargazer/cache` to `~/.stargazer/local`

2. **Update all references** throughout codebase
   - `cache_dir` → `local_dir` in pinata.py
   - Any references in types, tasks, or workflows

### Phase 2: TinyDB Integration

3. **Add TinyDB dependency**
   - `uv add tinydb`

4. **Initialize TinyDB in PinataClient**
   ```python
   # In __init__
   self.local_db_path = self.local_dir / "stargazer_local.json"
   self._db: Optional[TinyDB] = None


   @property
   def db(self) -> TinyDB:
       if self._db is None:
           self._db = TinyDB(self.local_db_path)
       return self._db
   ```

### Phase 3: Update Upload Method

5. **Modify `upload_file()` local mode branch**
   - After copying file to local directory, insert metadata into TinyDB
   - Schema for TinyDB documents:
     ```json
     {
       "id": "local_filename_size",
       "cid": "local_filename_size",
       "name": "original_filename",
       "size": 12345,
       "keyvalues": {"type": "alignment", "component": "alignment", ...},
       "created_at": "2024-01-19T12:00:00+00:00",
       "is_public": false,
       "local_path": "/path/to/local/dir/local_filename_size"
     }
     ```

### Phase 4: Update Query Method

6. **Modify `query_files()` for local mode**
   - When `self.local_only` is True, query TinyDB instead of Pinata API
   - Filter by keyvalues using TinyDB Query
   - Return IpFile objects from database records

### Phase 5: Update Download Method

7. **Modify `download_file()` for local mode**
   - For local CIDs, lookup metadata in TinyDB
   - Set `local_path` from stored path or resolve from local_dir

### Phase 6: Testing & Documentation

8. **Unit tests**
   - Test TinyDB initialization
   - Test upload writes to DB
   - Test query reads from DB
   - Test offline workflow end-to-end

9. **Update docstrings and comments**
   - Document new env var
   - Document TinyDB schema

## File Changes

| File | Changes |
|------|---------|
| `src/stargazer/utils/pinata.py` | TinyDB integration, env var rename, method updates |
| `pyproject.toml` | Add tinydb dependency |
| `tests/` | New tests for local filesystem |

## Design Decisions

1. **CID generation**: Keep simple `local_{filename}_{size}` scheme (fast, no need for deduplication)

2. **Migration**: Clean break - only support `STARGAZER_LOCAL`, no backwards compatibility alias

3. **TinyDB structure**: Single `files` table for all file types (queried by keyvalues)

4. **Path storage**: Store **relative paths** from `local_dir` for portability

5. **Delete behavior**: **Hard delete** - remove both file from disk and metadata from TinyDB

## TinyDB Schema

```json
{
  "id": "local_filename_size",
  "cid": "local_filename_size",
  "name": "original_filename.bam",
  "size": 12345,
  "keyvalues": {
    "type": "alignment",
    "component": "alignment",
    "sample_id": "NA12878"
  },
  "created_at": "2024-01-19T12:00:00+00:00",
  "is_public": false,
  "rel_path": "local_filename_size"
}
```

Note: `rel_path` is relative to `local_dir`. Absolute path reconstructed as `local_dir / rel_path`.
