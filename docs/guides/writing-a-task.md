# Writing a Task

This guide walks through adding a new bioinformatics task to Stargazer.

## 1. Define the Asset Type (if needed)

If your task produces a new kind of output, add an asset subclass in `src/stargazer/assets/`:

```python
class MyOutput(Asset):
    _asset_key: ClassVar[str] = "my_output"
    sample_id: str
```

The `_asset_key` must be unique. The class auto-registers via `__init_subclass__`.

## 2. Create the Task Module

Add a file in `src/stargazer/tasks/` named after the tool (e.g., `my_tool.py`):

```python
import asyncio
import flyte

from stargazer.assets.reference import Reference
from stargazer.assets.alignment import Alignment

tool_env = flyte.TaskEnvironment(name="my_tool")


@tool_env.task
async def run_my_tool(ref: Reference, aln: Alignment) -> MyOutput:
    """One-line description of what this task does."""
    await asyncio.gather(ref.fetch(), aln.fetch())

    output_path = Path("/tmp/output.ext")
    # ... run tool subprocess ...

    result = MyOutput()
    await result.update(output_path, sample_id=aln.sample_id)
    return result
```

## 3. Key Rules

- **One task, one operation** — don't combine multiple tool calls unless there's a good reason, e.g. piping between tools where the intermediate would have little long-term value for re-analysis
- **Always `fetch()` inputs** before accessing their paths
- **Always `update()` outputs** to register them in storage
- **Use `asyncio.gather`** to fetch multiple inputs in parallel
- **Specify resources** via `TaskEnvironment` for CPU/memory/GPU-intensive tools
- **Use `pathlib.Path`** for all filesystem operations; convert to `str` only for subprocess calls

## 4. Register in the MCP Server

Tasks are automatically discovered if they're importable from the `stargazer.tasks` package. Ensure your module is imported in `src/stargazer/tasks/__init__.py`.

## 5. Test

Write a test in `tests/unit/` that mocks storage and verifies the task produces the expected output type with correct keyvalues.
