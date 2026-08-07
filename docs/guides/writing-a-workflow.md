# Writing a Workflow

This guide walks through composing tasks into an end-to-end workflow.

## 1. Create the Workflow Module

Add a file in `src/stargazer/workflows/` named after the analysis type:

```python
import asyncio
import flyte

from stargazer.assets.asset import assemble
from stargazer.assets.reference import Reference
from stargazer.assets.reads import R1, R2
from stargazer.tasks.bwa import align_with_bwa

pipeline_env = flyte.TaskEnvironment(name="my_pipeline")


@pipeline_env.task
async def my_pipeline(build: str, sample_id: str) -> Variants:
    """One-line description of the pipeline."""
    assets = await assemble(
        build=build, sample_id=sample_id, asset=["reference", "r1", "r2"]
    )

    ref = next(a for a in assets if isinstance(a, Reference))
    r1 = next(a for a in assets if isinstance(a, R1))
    r2 = next(a for a in assets if isinstance(a, R2))

    alignment = await align_with_bwa(ref=ref, r1=r1, r2=r2)
    # ... chain more tasks ...
    return result
```

## 2. Key Rules

- **Accept scalar parameters only** — workflows handle their own assembly via `assemble()`, this keeps the workflow interface flexible for the MCP server while allowing you to create a strong contract with respect to the types *between* tasks
- **Filter with `isinstance`** — not string matching on asset keys
- **Use `asyncio.gather`** for independent tasks that can run in parallel
- **Workflows are tasks** — in Flyte v2, there's no separate decorator

## 3. Register in the MCP Server

Workflows are discovered like tasks. Ensure your module is imported in `src/stargazer/workflows/__init__.py`.

## 4. Test

Write integration tests that verify the full pipeline produces expected outputs. Mock storage but let task composition run.
