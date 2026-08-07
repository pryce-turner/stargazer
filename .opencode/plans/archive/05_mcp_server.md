# MCP Server Implementation Plan

## Overview

Implement a Stargazer MCP server in Python using FastMCP that exposes all storage, task, and workflow capabilities as MCP tools, resources, and prompts. Supports stdio and Streamable HTTP transports.

## Prerequisites

- Storage client refactor (`storage_client_refactor.md`) — completed
- STARGAZER_MODE configuration (`stargazer_mode_config.md`) — completed

## Current State

- All bioinformatics tasks and workflows exist in Python under `src/stargazer/tasks/` and `src/stargazer/workflows/`
- Storage layer exists in `src/stargazer/utils/pinata.py` (will be refactored per prior plan)
- Type system exists in `src/stargazer/assets/`
- No MCP server, no CLI entry point

## Target State

```
src/stargazer/
├── server.py              # MCP server (FastMCP) — tools, resources, prompts
├── tasks/                 # Unchanged
├── workflows/             # Unchanged
├── types/                 # Unchanged
└── utils/                 # Refactored per storage plan
```

## Implementation Plan

### Phase 1: Skeleton + Storage Tools

1. Add `mcp[cli]` to project dependencies in `pyproject.toml`

2. Create `src/stargazer/server.py` with FastMCP instance:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stargazer")
```

3. Register storage tools:

```python
from stargazer.utils.storage import default_client


@mcp.tool()
async def query_files(keyvalues: dict[str, str]) -> list[dict]:
    """Query files by metadata. Returns matching files with their metadata."""
    files = await default_client.query_files(keyvalues)
    return [_serialize_ipfile(f) for f in files]


@mcp.tool()
async def upload_file(path: str, keyvalues: dict[str, str]) -> dict:
    """Upload a file with metadata keyvalues."""
    ipfile = await default_client.upload_file(Path(path), keyvalues=keyvalues)
    return _serialize_ipfile(ipfile)


@mcp.tool()
async def download_file(file_id: str) -> str:
    """Download a file to local cache. Returns the local path."""
    # Reconstruct IpFile from id, then download
    ...


@mcp.tool()
async def delete_file(file_id: str) -> str:
    """Delete a file by ID."""
    ...
```

4. Implement `_serialize_ipfile()` helper:

```python
def _serialize_ipfile(ipfile: IpFile) -> dict:
    return {
        "id": ipfile.id,
        "cid": ipfile.cid,
        "name": ipfile.name,
        "size": ipfile.size,
        "keyvalues": ipfile.keyvalues,
        "created_at": ipfile.created_at.isoformat(),
        "is_public": ipfile.is_public,
    }
```

5. Add CLI entry point in `pyproject.toml`:

```toml
[project.scripts]
stargazer = "stargazer.server:main"
```

6. Implement `main()`:

```python
def main():
    import sys

    transport = "stdio"
    if "--http" in sys.argv:
        transport = "streamable-http"
    mcp.run(transport=transport)
```

7. Test with MCP Inspector: `mcp dev src/stargazer/server.py`

### Phase 2: Task Tools

Register all bioinformatics tasks as MCP tools.

1. Implement serialization helpers for type dataclasses:

```python
def _serialize_type(obj) -> dict:
    """Serialize Reference, Alignment, Reads, or Variants to JSON dict."""
    ...


def _deserialize_args(task_fn, kwargs: dict) -> dict:
    """Deserialize JSON inputs to typed Python objects based on task signature."""
    ...
```

2. Create a registration helper:

```python
def register_task(mcp: FastMCP, task_fn, name: str, description: str):
    @mcp.tool(name=name, description=description)
    async def wrapper(**kwargs):
        typed_args = _deserialize_args(task_fn, kwargs)
        result = await task_fn(**typed_args)
        return _serialize_result(result)
```

3. Register individual tasks:
   - `hydrate` — direct registration (already JSON-friendly inputs)
   - `bwa_index`, `bwa_mem`, `samtools_faidx`
   - `sort_sam`, `mark_duplicates`, `base_recalibrator`, `apply_bqsr`
   - `create_sequence_dictionary`
   - `joint_call_gvcfs`, `combine_gvcfs`, `genomics_db_import`
   - `variant_recalibrator`, `apply_vqsr`

4. Register composite workflows:
   - `prepare_reference`, `preprocess_sample`, `preprocess_cohort`
   - `germline_single_sample`, `germline_cohort`, `germline_cohort_with_vqsr`

5. Test each tool with MCP Inspector

### Phase 3: Resources

1. Register resources:

```python
@mcp.resource("stargazer://references")
async def list_references() -> str:
    """List available reference genomes with their components."""
    refs = await default_client.query_files({"type": "reference"})
    # Group by build, summarize available components
    ...


@mcp.resource("stargazer://samples")
async def list_samples() -> str:
    """List available samples and their data types."""
    ...


@mcp.resource("stargazer://workflows")
async def list_workflows() -> str:
    """List available workflows with parameter descriptions."""
    ...


@mcp.resource("stargazer://runs")
async def list_runs() -> str:
    """List recent workflow runs with status."""
    ...


@mcp.resource("stargazer://config")
async def show_config() -> str:
    """Show current Stargazer mode and configuration."""
    ...
```

2. Test resources with MCP Inspector

### Phase 4: Prompts

1. Register prompts:

```python
@mcp.prompt()
def align_reads(sample_id: str, ref_build: str) -> str:
    """Generate instructions for aligning reads to a reference genome."""
    return (
        f"Align reads for sample {sample_id} against reference build {ref_build}. "
        f"First hydrate the reads and reference, then run bwa_mem. "
        f"Return the resulting alignment."
    )


@mcp.prompt()
def preprocess_sample(sample_id: str, ref_build: str, known_sites: str = "") -> str:
    """Generate instructions for full sample preprocessing."""
    ...


@mcp.prompt()
def call_variants(sample_id: str, ref_build: str) -> str:
    """Generate instructions for germline variant calling."""
    ...


@mcp.prompt()
def joint_genotype(sample_ids: str, ref_build: str, cohort_id: str = "cohort") -> str:
    """Generate instructions for joint genotyping across a cohort."""
    ...
```

2. Test prompts with MCP Inspector

### Phase 5: Mode-Aware Tool Registration

1. Read `STARGAZER_MODE` at server startup
2. Conditionally register tools based on mode:
   - Local mode without JWT: storage tools use LocalStorageClient, all tasks available
   - Local mode with JWT: storage tools use PinataClient, all tasks available
   - Cloud mode: all tools available, execution targets Union
3. Tools that cannot function in the current mode are not registered

### Phase 6: Testing

1. **Unit tests** (`tests/unit/test_server.py`):
   - Each storage tool returns expected JSON for known inputs
   - Serialization round-trips for all types (IpFile, Reference, Alignment, Reads, Variants)
   - Mode-aware tool registration (local mode hides cloud-only tools)

2. **Integration tests** (`tests/integration/test_mcp_server.py`):
   - Spawn MCP server over stdio, send JSON-RPC messages, verify responses
   - Tool chaining: upload file → query files → hydrate → verify type reconstruction

## File Changes

| File | Change |
|------|--------|
| `src/stargazer/server.py` | **New** — MCP server with all tools, resources, prompts |
| `pyproject.toml` | **Modified** — add `mcp[cli]` dependency, `stargazer` script entry point |
| `tests/unit/test_server.py` | **New** — unit tests for MCP tools and serialization |
| `tests/integration/test_mcp_server.py` | **New** — integration tests over stdio |

## Design Decisions

1. **FastMCP over raw SDK**: Uses Python type hints and docstrings to auto-generate tool schemas. Eliminates hand-written JSON Schema.

2. **Single server, two transports**: One `server.py` that runs in either stdio or HTTP mode via a runtime flag. Not two separate implementations.

3. **Flyte tasks as MCP tools via wrapper**: A thin wrapper deserializes JSON inputs to Python dataclasses, calls the existing async task function, and serializes the result back to JSON. The bioinformatics code doesn't change.

4. **Tool registration reflects mode**: In local mode without Pinata JWT, cloud-only tools are not registered. The LLM only sees tools it can actually execute.

5. **Resources are queries, not static**: Resources like `stargazer://references` query the storage client at read time, returning current state rather than cached data.
