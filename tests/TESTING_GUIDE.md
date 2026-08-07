# Testing Flyte v2 Tasks with Pytest

## Summary

This guide explains how to test Flyte v2 tasks with pytest, based on the working implementation in this project.

## The Problem

When testing Flyte v2 tasks, you may encounter:
```
ValueError: Raw data path has not been set in the context.
```

This happens when using Flyte IO operations like `Dir.from_local()` without proper context initialization.

## The Solution

### 1. Global Flyte Initialization (`tests/conftest.py`)

Create a session-scoped fixture that initializes Flyte once for all tests:

```python
import pytest
import flyte


@pytest.fixture(scope="session", autouse=True)
def init_flyte_context():
    """Initialize Flyte context for all tests."""
    flyte.init_from_config()
    yield
```

This fixture:
- Runs once per test session (`scope="session"`)
- Runs automatically for all tests (`autouse=True`)
- Initializes Flyte from `.flyte/config.yaml`

### 2. Simple Tasks (scratch/test_hello_world.py)

For tasks with simple parameters (strings, ints, etc.), use `local_flyte.run()`:

```python
import pytest
import flyte
from hello_world import greet


@pytest.fixture(scope="module")
def local_flyte():
    """Return a local run context for executing tasks."""
    return flyte.with_runcontext(mode="local")


def test_hello_world_basic(local_flyte):
    """Test basic hello world functionality."""
    result = local_flyte.run(greet, name="World")
    result.wait()

    # Access output using _outputs attribute
    output = result._outputs
    assert output == "Hello, World!"
```

**Key points:**
- Use `local_flyte.run(task, **kwargs)` to execute tasks
- Call `.wait()` to wait for completion
- Access results via `._outputs` attribute

### 3. Tasks with Custom Types (tests/test_samtools.py)

For tasks with custom types, call tasks directly and use simple Path objects:

```python
import pytest
from pathlib import Path
from stargazer.assets import Reference
from stargazer.tasks.samtools import samtools_faidx


@pytest.mark.asyncio
async def test_samtools_faidx():
    """Test samtools faidx creates .fai index file."""
    # Create a Reference with a local Path (no Flyte Dir needed!)
    ref = Reference(
        ref_name="GRCh38_chr21.fasta",
        dir=tmpdir_path,  # Just use Path directly
    )

    # Call task directly (not via local_flyte.run)
    result = await samtools_faidx(ref)

    # Result is the actual return value, not a Run object
    assert isinstance(result, Reference)
    assert Path(result.dir) == tmpdir_path
```

**Key points:**
- Use `@pytest.mark.asyncio` for async tasks
- Create custom types with simple Path objects - no `Dir.from_local()` needed!
- Call tasks directly with `await task(**kwargs)` to bypass serialization
- Result is the actual return value, not a Run object

## Why Two Different Patterns?

### Pattern 1: `local_flyte.run()` (for simple types)
- **Pros:** Tests the full Flyte execution pipeline including serialization
- **Cons:** Requires Flyte TypeTransformers for custom types
- **Use when:** Testing tasks with primitive types (str, int, bool, etc.)

### Pattern 2: Direct `await task()` call (for complex types)
- **Pros:** Works with any types, simpler for unit tests
- **Cons:** Bypasses Flyte's serialization layer
- **Use when:** Testing tasks with custom types that don't have TypeTransformers

## Common Issues

### Issue: "TypeError: Error converting custom type"
**Solution:** Call task directly instead of using `local_flyte.run()`:
```python
# Instead of:
# result = local_flyte.run(task, arg=custom_obj)

# Do:
result = await task(arg=custom_obj)
```

### Tip: Design custom types for easy testing
**Best practice:** Design custom types to accept `Dir | Path` and default to Path:
```python
from pathlib import Path
from flyte.io import Dir
import tempfile
from dataclasses import dataclass, field


@dataclass
class CustomType:
    name: str
    dir: Dir | Path = field(default_factory=lambda: Path(tempfile.mkdtemp()))

    def get_path(self) -> Path:
        """Handle both Dir and Path."""
        if isinstance(self.dir, Dir):
            return Path(self.dir.download_sync())
        return Path(self.dir)
```

This makes testing trivial - just pass a Path!

## File Structure

```
project/
├── .flyte/
│   └── config.yaml           # Flyte configuration
├── tests/
│   ├── conftest.py           # Global Flyte initialization
│   └── test_samtools.py      # Complex type tests (direct calls)
└── scratch/
    ├── hello_world.py        # Simple task example
    └── test_hello_world.py   # Simple type tests (local_flyte.run)
```

## Best Practices

1. **Always call `flyte.init_from_config()`** in a session-scoped fixture
2. **For simple types:** Use `local_flyte.run()` to test full execution pipeline
3. **For custom types:** Call tasks directly to avoid serialization issues
4. **Design custom types** to accept `Dir | Path` with Path as default
5. **Use `@pytest.mark.asyncio`** for async tasks
6. **Access simple task results** via `result._outputs`
7. **Access direct call results** as the return value itself
8. **Keep tests simple** - use Path instead of Dir when possible
