# Flyte v2 SDK Examples Reference

A concise reference showing the standard patterns and full range of capabilities in Flyte v2. Derived from 220 example files with duplicates removed.

## Table of Contents

1. [Core Concepts](#1-core-concepts)
2. [Task Environments & Resources](#2-task-environments--resources)
3. [Images & Dependencies](#3-images--dependencies)
4. [Type System](#4-type-system)
5. [File & Directory I/O](#5-file--directory-io)
6. [Concurrency Patterns](#6-concurrency-patterns)
7. [Caching](#7-caching)
8. [Error Handling](#8-error-handling)
9. [Task Overrides](#9-task-overrides)
10. [Reusable Tasks (Actors)](#10-reusable-tasks-actors)
11. [Traces & Groups](#11-traces--groups)
12. [Reports](#12-reports)
13. [Triggers (Schedules)](#13-triggers-schedules)
14. [Secrets](#14-secrets)
15. [Plugins](#15-plugins)
16. [Apps & Services](#16-apps--services)
17. [Higher-Order Patterns](#17-higher-order-patterns)
18. [Project Structure Patterns](#18-project-structure-patterns)
19. [Migration from v1](#19-migration-from-v1)

---

## 1. Core Concepts

### Minimal Task Definition

```python
import flyte

env = flyte.TaskEnvironment(name="hello")


@env.task
async def say_hello(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    flyte.init_from_config()
    run = flyte.run(say_hello, name="world")
    print(run.url)
```

### Sync vs Async Tasks

```python
# Async task (preferred for I/O operations)
@env.task
async def async_task(x: int) -> int:
    await asyncio.sleep(1)
    return x * 2


# Sync task (for CPU-bound operations)
@env.task
def sync_task(x: int) -> int:
    return x * 2
```

### Task Nesting (v2 Key Feature)

Tasks can call other tasks directly—no special constructs needed:

```python
@env.task
async def square(x: int) -> int:
    return x**2


@env.task
async def workflow(n: int) -> list[int]:
    results = []
    for i in range(n):
        results.append(await square(i))
    return results
```

### Running Tasks

```python
# Initialize connection
flyte.init_from_config()
# Or with explicit config:
flyte.init(
    endpoint="dns:///localhost:8090",
    insecure=True,
    org="myorg",
    project="myproject",
    domain="development",
)

# Run remotely (default)
run = flyte.run(my_task, arg1=value1)

# Run locally
run = flyte.with_runcontext(mode="local").run(my_task, arg1=value1)

# Run with options
run = flyte.with_runcontext(
    mode="remote",
    log_level=logging.DEBUG,
    env_vars={"KEY": "VALUE"},
    labels={"team": "ml"},
    annotations={"ann": "value"},
    overwrite_cache=True,
    interruptible=False,
).run(my_task)

# Wait and get outputs
run.wait()
result = run.outputs()
```

---

## 2. Task Environments & Resources

### Basic Environment

```python
env = flyte.TaskEnvironment(
    name="my_env",
    resources=flyte.Resources(cpu=1, memory="1Gi"),
)
```

### Full Resource Specification

```python
env = flyte.TaskEnvironment(
    name="gpu_env",
    resources=flyte.Resources(
        cpu="2",  # Or cpu=(1, 4) for min/max
        memory="4Gi",  # Or memory=("1Gi", "8Gi")
        gpu="A100 80G:1",  # GPU type and count
        disk="10Gi",  # Ephemeral storage
        shm="auto",  # Shared memory (auto or explicit)
    ),
)
```

### GPU/Accelerator Types

```python
# NVIDIA GPUs
flyte.Resources(gpu="T4:1")
flyte.Resources(gpu="A100 80G:8")
flyte.Resources(gpu="L4:1")

# AMD GPUs
flyte.Resources(gpu="MI350X:1")

# TPUs
flyte.Resources(gpu=flyte.TPU("V5P", "2x2x1"))

# AWS Trainium
flyte.Resources(gpu="Trn1:1")

# Intel Gaudi
flyte.Resources(gpu="Gaudi1:1")
```

### Environment Dependencies

```python
# Environments can depend on other environments
worker_env = flyte.TaskEnvironment(name="worker", image=worker_image)
driver_env = flyte.TaskEnvironment(
    name="driver",
    image=driver_image,
    depends_on=[worker_env],  # Ensures worker image is built first
)
```

### Clone Environment with Modifications

```python
base_env = flyte.TaskEnvironment(name="base", resources=flyte.Resources(cpu=1))

# Clone and modify
high_mem_env = base_env.clone_with(
    name="high_mem",
    resources=flyte.Resources(cpu=1, memory="8Gi"),
)
```

---

## 3. Images & Dependencies

### From Debian Base (Recommended)

```python
# Simple
image = flyte.Image.from_debian_base()

# With Python version
image = flyte.Image.from_debian_base(python_version=(3, 12))

# With packages
image = (
    flyte.Image.from_debian_base()
    .with_pip_packages("pandas", "numpy", "scikit-learn")
    .with_apt_packages("curl", "git")
)

# With registry
image = flyte.Image.from_debian_base(
    python_version=(3, 12),
    registry="ghcr.io/myorg",
    name="myimage",
)
```

### From UV Script (Inline Dependencies)

```python
# /// script
# requires-python = "==3.12"
# dependencies = [
#    "pandas",
#    "numpy",
# ]
# ///

import flyte

env = flyte.TaskEnvironment(
    name="uv_script",
    image=flyte.Image.from_uv_script(__file__, name="myimage"),
)
```

### From UV Project (pyproject.toml)

```python
import pathlib

env = flyte.TaskEnvironment(
    name="uv_project",
    image=flyte.Image.from_debian_base().with_uv_project(
        pyproject_file=pathlib.Path("pyproject.toml"),
    ),
)
```

### From Existing Base Image

```python
# Clone and customize an existing image
image = (
    flyte.Image.from_base("apache/spark-py:v3.4.0")
    .clone(name="spark", python_version=(3, 10), registry="ghcr.io/flyteorg")
    .with_pip_packages("flyteplugins-spark")
)
```

### Advanced Image Configuration

```python
image = (
    flyte.Image.from_debian_base()
    .with_pip_packages("mypackage", pre=True)  # Install pre-release
    .with_apt_packages("build-essential", "libssl-dev")
    .with_commands(["pip install -e /app"])  # Custom commands
    .with_env_vars({"MY_VAR": "value"})
    .with_source_folder(pathlib.Path("./src"), copy_contents_only=True)
    .with_workdir("/app")
)
```

### Private Registry / PyPI

```python
# Private base image with pull secret
env = flyte.TaskEnvironment(
    name="private",
    image=flyte.Image.from_base("private.registry.io/myimage:tag"),
    image_pull_secret="my-registry-secret",
)

# Private PyPI
image = flyte.Image.from_debian_base().with_pip_packages(
    "my-private-package",
    extra_index_url="https://pypi.mycompany.com/simple",
)
```

---

## 4. Type System

### Simple Types

```python
@env.task
async def simple_types(
    i: int,
    f: float,
    s: str,
    b: bool,
    default_val: int = 42,
) -> tuple[int, float, str, bool]:
    return i, f, s, b
```

### Collections

```python
from typing import List, Dict, Optional


@env.task
async def collections(
    int_list: List[int],
    str_dict: Dict[str, int],
    optional_val: Optional[str] = None,
) -> List[int]:
    return int_list
```

### Dataclasses

```python
from dataclasses import dataclass
from typing import List


@dataclass
class Request:
    feature_a: float
    feature_b: float


@dataclass
class BatchRequest:
    requests: List[Request]


@env.task
async def process(req: Request) -> float:
    return req.feature_a + req.feature_b


@env.task
async def batch_process(batch: BatchRequest) -> List[float]:
    return [await process(r) for r in batch.requests]
```

### Pydantic Models

```python
from pydantic import BaseModel


class PredictionResult(BaseModel):
    score: float
    label: str

    class Config:
        arbitrary_types_allowed = True  # Required for File/Dir types


@env.task
async def predict(data: str) -> PredictionResult:
    return PredictionResult(score=0.95, label="positive")
```

### Enums

```python
from enum import Enum


class Status(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@env.task
async def check_status(s: Status) -> Status:
    return Status.COMPLETED
```

### Union Types

```python
from typing import Union


@env.task
async def flexible_input(data: Union[str, int, List[int]]) -> str:
    return str(data)
```

### DataFrames

```python
import pandas as pd
from typing import Annotated


@env.task
async def create_df() -> pd.DataFrame:
    return pd.DataFrame({"a": [1, 2], "b": [3, 4]})


# With format hint
@env.task
async def create_csv_df() -> Annotated[flyte.io.DataFrame, "csv"]:
    return flyte.io.DataFrame.from_df(pd.DataFrame({"a": [1, 2]}))
```

---

## 5. File & Directory I/O

### File Operations (Async)

```python
from flyte.io import File


@env.task
async def create_file() -> File:
    # Create local file
    with open("output.txt", "w") as f:
        f.write("Hello, World!")

    # Upload to remote storage
    return await File.from_local("output.txt")


@env.task
async def read_file(f: File) -> str:
    # Stream read
    async with f.open("rb") as fh:
        content = await fh.read()
        return content.decode("utf-8")


@env.task
async def download_file(f: File) -> str:
    # Download to local path
    local_path = await f.download()
    return local_path


@env.task
async def stream_write() -> File:
    # Stream write directly to remote
    f = File.new_remote()
    async with f.open("wb") as fh:
        await fh.write(b"streaming content")
    return f
```

### File Operations (Sync)

```python
@env.task
def sync_file_ops() -> File:
    f = File.new_remote()
    with f.open_sync("wb") as fh:
        fh.write(b"content")
    return f


@env.task
def sync_download(f: File) -> str:
    return f.download_sync()
```

### Directory Operations

```python
from flyte.io import Dir


@env.task
async def create_dir() -> Dir:
    import tempfile, os

    tmpdir = tempfile.mkdtemp()

    # Create files in directory
    with open(os.path.join(tmpdir, "file1.txt"), "w") as f:
        f.write("content1")
    with open(os.path.join(tmpdir, "file2.txt"), "w") as f:
        f.write("content2")

    # Upload directory
    return await Dir.from_local(tmpdir)


@env.task
async def read_dir(d: Dir) -> list[str]:
    contents = []
    async for file in d.walk(recursive=True):
        async with file.open("rb") as fh:
            contents.append((await fh.read()).decode())
    return contents


@env.task
async def list_files(d: Dir) -> list[str]:
    files = await d.list_files()
    return [f.name for f in files]


@env.task
async def get_specific_file(d: Dir, name: str) -> File:
    return await d.get_file(name)
```

### Reference Existing Remote Files

```python
@env.task
async def use_existing() -> str:
    # Reference existing remote file
    f = File.from_existing_remote("s3://bucket/path/to/file.txt")
    
    # Check existence
    exists = await f.exists()
    
    if exists:
        async with f.open("rb") as fh:
            return (await fh.read()).decode()
    return ""
```

---

## 6. Concurrency Patterns

### Parallel Execution with asyncio.gather

```python
import asyncio


@env.task
async def process_item(x: int) -> int:
    return x * 2


@env.task
async def parallel_processing(items: list[int]) -> list[int]:
    # Create coroutines
    coros = [process_item(x) for x in items]

    # Execute in parallel
    return await asyncio.gather(*coros)
```

### Using asyncio.create_task

```python
@env.task
async def with_tasks(n: int) -> list[int]:
    tasks = []
    for i in range(n):
        # Create task immediately starts execution
        task = asyncio.create_task(process_item(i))
        tasks.append(task)
    
    return await asyncio.gather(*tasks)
```

### flyte.map (Parallel Map)

```python
@env.task
def process_single(x: int) -> int:
    return x**2


@env.task
def parallel_map(items: list[int]) -> list[int]:
    # flyte.map runs items in parallel
    return list(flyte.map(process_single, items))
```

### Fan-out / Fan-in Pattern

```python
@env.task
async def fan_out_fan_in(n: int) -> int:
    # Fan-out: parallel execution
    tasks = [asyncio.create_task(square(i)) for i in range(n)]
    results = await asyncio.gather(*tasks)

    # Fan-in: aggregate results
    return sum(results)
```

### Controlled Concurrency

```python
import asyncio


@env.task
async def rate_limited(items: list[int], max_concurrent: int = 10) -> list[int]:
    semaphore = asyncio.Semaphore(max_concurrent)

    async def limited_process(x):
        async with semaphore:
            return await process_item(x)

    return await asyncio.gather(*[limited_process(x) for x in items])
```

---

## 7. Caching

### Auto Caching (Content-Based)

```python
from flyte import Cache

env = flyte.TaskEnvironment(name="cached", cache="auto")


# Or per-task
@env.task(cache=Cache(behavior="auto"))
async def auto_cached(data: str) -> str:
    # Re-runs only when code or inputs change
    return f"processed: {data}"
```

### Manual Version Override

```python
@env.task(cache=Cache(behavior="override", version_override="v2"))
async def versioned_task(x: int) -> int:
    # Re-runs only when version changes
    return x * 2
```

### Ignored Inputs

```python
@env.task(
    cache=Cache(
        behavior="override",
        version_override="v1",
        ignored_inputs="timestamp",  # Won't affect cache key
    )
)
async def cached_with_ignored(data: str, timestamp: str) -> str:
    return data
```

### Disable Caching

```python
@env.task(cache="disable")
async def never_cached(x: int) -> int:
    return x
```

### Overwrite Cache at Runtime

```python
run = flyte.with_runcontext(overwrite_cache=True).run(my_task, x=1)
```

---

## 8. Error Handling

### Catching Task Errors

```python
import flyte.errors


@env.task
async def may_fail(x: int) -> int:
    if x < 0:
        raise ValueError("x must be positive")
    return x


@env.task
async def with_error_handling(x: int) -> int:
    try:
        return await may_fail(x)
    except flyte.errors.RuntimeUserError as e:
        if e.code == "ValueError":
            print(f"Caught ValueError: {e}")
            return await may_fail(abs(x))
        raise
```

### OOM Error Handling

```python
@env.task
async def oom_recovery() -> int:
    try:
        await memory_intensive_task()
    except flyte.errors.OOMError as e:
        print(f"OOM error: {e}")
        # Retry with more memory
        return await memory_intensive_task.override(
            resources=flyte.Resources(memory="8Gi")
        )()
```

### Cancel Running Tasks

```python
@env.task
async def with_cancellation(n: int):
    tasks = [asyncio.create_task(long_running(i)) for i in range(n)]
    
    try:
        await some_operation()
    except Exception:
        # Cancel all pending tasks
        for t in tasks:
            t.cancel()
        raise
```

---

## 9. Task Overrides

Override task properties at call time:

```python
@env.task
async def flexible_task(x: int) -> int:
    return x * 2


@env.task
async def driver() -> int:
    # Override resources
    result = await flexible_task.override(
        resources=flyte.Resources(cpu=4, memory="8Gi")
    )(x=10)

    # Override with short name (for UI)
    result = await flexible_task.override(short_name="custom_name")(x=20)

    # Override multiple properties
    result = await flexible_task.override(
        resources=flyte.Resources(gpu="T4:1"),
        cache="disable",
        timeout=3600,
    )(x=30)

    return result
```

### Override Plugin Config

```python
from copy import deepcopy


@task_env.task
async def dynamic_spark(executor_count: int) -> float:
    updated_config = deepcopy(spark_config)
    updated_config.spark_conf["spark.executor.instances"] = str(executor_count)

    return await spark_task.override(plugin_config=updated_config)()
```

---

## 10. Reusable Tasks (Actors)

Reusable tasks keep containers warm for multiple invocations:

```python
env = flyte.TaskEnvironment(
    name="reusable",
    resources=flyte.Resources(cpu=1, memory="500Mi"),
    image=flyte.Image.from_debian_base().with_pip_packages("unionai-reuse"),
    reusable=flyte.ReusePolicy(
        replicas=2,  # Number of warm replicas (or tuple for min/max)
        idle_ttl=300,  # Seconds to keep idle before scaling down
        concurrency=10,  # Max concurrent requests per replica
        scaledown_ttl=60,  # Grace period before termination
    ),
)


@env.task
async def fast_task(x: int) -> int:
    # Reuses warm container - no cold start
    return x * 2


# Non-reusable driver that uses reusable workers
driver_env = env.clone_with(name="driver", reusable=None, depends_on=[env])


@driver_env.task
async def orchestrate(n: int) -> list[int]:
    return await asyncio.gather(*[fast_task(i) for i in range(n)])
```

### In-Memory Caching with Actors

```python
from async_lru import alru_cache


@alru_cache(maxsize=100)
async def expensive_computation(key: str) -> dict:
    # Cached in actor memory across invocations
    return await load_expensive_data(key)


@env.task  # reusable env
async def cached_task(key: str) -> dict:
    return await expensive_computation(key)
```

---

## 11. Traces & Groups

### Traces (Side Effects)

Traces record function calls without creating separate tasks:

```python
@flyte.trace
async def log_metric(name: str, value: float):
    print(f"{name}: {value}")


@flyte.trace
async def compute_step(x: int) -> int:
    return x**2


@env.task
async def with_traces(n: int) -> int:
    total = 0
    for i in range(n):
        result = await compute_step(i)
        await log_metric("step_result", result)
        total += result
    return total
```

### Groups (Logical Organization)

Groups organize traces in the UI:

```python
@env.task
async def organized_workflow(batches: list[list[int]]) -> list[int]:
    all_results = []
    
    for i, batch in enumerate(batches):
        with flyte.group(f"batch-{i}"):
            results = await asyncio.gather(*[compute_step(x) for x in batch])
            all_results.extend(results)
    
    return all_results
```

---

## 12. Reports

Generate HTML reports viewable in the Flyte UI:

```python
import flyte.report

env = flyte.TaskEnvironment(name="reports")


@env.task(report=True)
async def generate_report():
    # Replace entire report content
    await flyte.report.replace.aio("<h1>Analysis Complete</h1>")

    # Add to report
    await flyte.report.log.aio("<p>Processing started...</p>")

    # Use tabs for organization
    tab1 = flyte.report.get_tab("Summary")
    tab1.log("<h2>Summary</h2><p>Results overview</p>")

    tab2 = flyte.report.get_tab("Details")
    tab2.log("<h2>Detailed Results</h2>")

    # Flush to make visible
    await flyte.report.flush.aio()

    # Add interactive visualizations
    html = """
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <div id="chart"></div>
    <script>
        Plotly.newPlot('chart', [{x: [1,2,3], y: [4,5,6], type: 'scatter'}]);
    </script>
    """
    await flyte.report.replace.aio(html)
    await flyte.report.flush.aio()
```

---

## 13. Triggers (Schedules)

### Built-in Triggers

```python
from datetime import datetime


@env.task(triggers=flyte.Trigger.hourly())
def hourly_task(trigger_time: datetime) -> str:
    return f"Executed at {trigger_time}"


@env.task(triggers=flyte.Trigger.minutely("start_time"))
def minutely_task(start_time: datetime) -> str:
    return f"Executed at {start_time}"
```

### Custom Cron Triggers

```python
custom_trigger = flyte.Trigger(
    "daily_midnight",
    flyte.Cron("0 0 * * *", timezone="America/New_York"),
    inputs={"trigger_time": flyte.TriggerTime, "x": 1},
)


@env.task(triggers=custom_trigger)
def custom_scheduled(trigger_time: datetime, x: int) -> str:
    return f"Executed at {trigger_time} with x={x}"
```

### Multiple Triggers

```python
nyc_trigger = flyte.Trigger(
    "nyc",
    flyte.Cron("0 9 * * *", timezone="America/New_York"),
    inputs={"start_time": flyte.TriggerTime},
)

sf_trigger = flyte.Trigger(
    "sf",
    flyte.Cron("0 9 * * *", timezone="America/Los_Angeles"),
    inputs={"start_time": flyte.TriggerTime},
)


@env.task(triggers=(nyc_trigger, sf_trigger))
def multi_timezone(start_time: datetime) -> str:
    return f"Executed at {start_time}"


# Deploy triggers
if __name__ == "__main__":
    flyte.init_from_config()
    flyte.deploy(env)
```

---

## 14. Secrets

### Environment Variable Secrets

```python
env = flyte.TaskEnvironment(
    name="with_secrets",
    secrets=flyte.Secret(key="MY_API_KEY", as_env_var="API_KEY"),
)


@env.task
async def use_secret() -> str:
    import os

    api_key = os.environ["API_KEY"]
    return f"Using key: {api_key[:4]}..."
```

### File-Mounted Secrets

```python
import pathlib

SECRET_PATH = "/etc/flyte/secrets"
GROUP = "arn:aws:secretsmanager:us-east-2:123456789:secret"
KEY = "MY_SECRET"

env = flyte.TaskEnvironment(
    name="file_secrets",
    secrets=flyte.Secret(
        group=GROUP,
        key=KEY,
        mount=pathlib.Path(SECRET_PATH),
    ),
)


@env.task
def read_secret() -> str:
    return pathlib.Path(f"{SECRET_PATH}/{GROUP}/{KEY.lower()}").read_text()
```

---

## 15. Plugins

### Spark

```python
from flyteplugins.spark.task import Spark

spark_config = Spark(
    spark_conf={
        "spark.driver.memory": "2g",
        "spark.executor.memory": "2g",
        "spark.executor.instances": "3",
    },
)

image = (
    flyte.Image.from_base("apache/spark-py:v3.4.0")
    .clone(name="spark", python_version=(3, 10))
    .with_pip_packages("flyteplugins-spark")
)

spark_env = flyte.TaskEnvironment(
    name="spark",
    plugin_config=spark_config,
    image=image,
    resources=flyte.Resources(cpu=2, memory="4Gi"),
)


@spark_env.task
async def spark_job() -> int:
    spark = flyte.ctx().data["spark_session"]
    df = spark.range(100)
    return df.count()
```

### Ray

```python
from flyteplugins.ray.task import RayJobConfig, HeadNodeConfig, WorkerNodeConfig

ray_config = RayJobConfig(
    head_node_config=HeadNodeConfig(),
    worker_node_config=[WorkerNodeConfig(group_name="workers", replicas=2)],
    enable_autoscaling=False,
)

image = flyte.Image.from_debian_base().with_pip_packages(
    "ray[default]==2.46.0", "flyteplugins-ray"
)

ray_env = flyte.TaskEnvironment(
    name="ray",
    plugin_config=ray_config,
    image=image,
    resources=flyte.Resources(cpu=4, memory="4Gi"),
)


@ray_env.task
async def ray_job(n: int) -> list[int]:
    import ray

    @ray.remote
    def square(x):
        return x**2

    futures = [square.remote(i) for i in range(n)]
    return ray.get(futures)
```

### Dask

```python
from flyteplugins.dask import Dask, Scheduler, WorkerGroup

dask_config = Dask(
    scheduler=Scheduler(),
    workers=WorkerGroup(number_of_workers=4),
)

image = flyte.Image.from_debian_base().with_pip_packages("flyteplugins-dask")

dask_env = flyte.TaskEnvironment(
    name="dask",
    plugin_config=dask_config,
    image=image,
)


@dask_env.task
async def dask_job(n: int) -> list[int]:
    from distributed import Client

    client = Client()
    futures = client.map(lambda x: x + 1, range(n))
    return client.gather(futures)
```

### PyTorch Distributed

```python
from flyteplugins.pytorch.task import Elastic

torch_config = Elastic(
    nproc_per_node=1,
    nnodes=2,
)

image = flyte.Image.from_debian_base().with_pip_packages("flyteplugins-pytorch")

torch_env = flyte.TaskEnvironment(
    name="torch",
    plugin_config=torch_config,
    image=image,
    resources=flyte.Resources(cpu=2, memory="4Gi"),
)


@torch_env.task
def distributed_train(epochs: int) -> float:
    import torch.distributed as dist

    dist.init_process_group("gloo")
    # Training code...
    return final_loss
```

---

## 16. Apps & Services

### Basic App (Streamlit)

```python
import flyte.app

image = flyte.Image.from_debian_base().with_pip_packages("streamlit")

app_env = flyte.app.AppEnvironment(
    name="streamlit-app",
    image=image,
    command="streamlit hello --server.port 8080",
    resources=flyte.Resources(cpu=1, memory="1Gi"),
)

if __name__ == "__main__":
    flyte.init_from_config()
    deployments = flyte.deploy(app_env)
    print(deployments[0])
```

### FastAPI App

```python
from fastapi import FastAPI
from flyte.app.extras import FastAPIAppEnvironment

app = FastAPI(title="My API")

image = flyte.Image.from_debian_base().with_pip_packages("fastapi", "uvicorn")

app_env = FastAPIAppEnvironment(
    name="api",
    app=app,
    image=image,
    resources=flyte.Resources(cpu=1, memory="512Mi"),
    requires_auth=False,
)


@app.get("/predict")
async def predict(x: int) -> dict:
    return {"result": x * 2}


if __name__ == "__main__":
    flyte.init_from_config()
    flyte.deploy(app_env)
```

### Task Calling App

```python
# App environment
app_env = FastAPIAppEnvironment(name="service", app=app, image=image)

# Task environment that depends on app
task_env = flyte.TaskEnvironment(
    name="caller",
    image=image,
    depends_on=[app_env],
)


@task_env.task
async def call_service(x: int) -> int:
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{app_env.endpoint}/predict", params={"x": x})
        return response.json()["result"]
```

---

## 17. Higher-Order Patterns

### OOM Retrier

```python
async def retry_with_memory(
    task_fn, *args, initial_memory: str = "250Mi", max_memory: str = "4Gi", **kwargs
):
    """Retry task with increasing memory on OOM."""
    memories = ["250Mi", "500Mi", "1Gi", "2Gi", "4Gi"]

    for mem in memories:
        if parse_memory(mem) > parse_memory(max_memory):
            break
        try:
            return await task_fn.override(resources=flyte.Resources(memory=mem))(
                *args, **kwargs
            )
        except flyte.errors.OOMError:
            continue

    raise RuntimeError("Exhausted memory retries")
```

### Circuit Breaker

```python
async def circuit_breaker(
    task_fn,
    items: list,
    max_failures: int = 3,
):
    """Execute tasks with failure threshold."""
    results = []
    failures = 0
    
    for item in items:
        if failures >= max_failures:
            raise RuntimeError(f"Circuit breaker opened after {failures} failures")
        try:
            result = await task_fn(item)
            results.append(result)
        except Exception:
            failures += 1
            results.append(None)
    
    return results
```

### Auto Batcher

```python
async def auto_batch(
    task_fn,
    items: list,
    batch_size: int = 10,
):
    """Process items in batches."""
    results = []

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        with flyte.group(f"batch-{i // batch_size}"):
            batch_results = await asyncio.gather(*[task_fn(item) for item in batch])
            results.extend(batch_results)

    return results
```

### UDF (User-Defined Functions) as Parameters

```python
import typing


@env.task
async def run_udf(x: int, udf: typing.Callable[[int], typing.Awaitable[int]]) -> int:
    return await udf(x)


@env.task
async def add_one(x: int) -> int:
    return x + 1


@env.task
async def main():
    # Pass task as UDF
    result = await run_udf(10, add_one)

    # Or inline function
    async def multiply(x: int) -> int:
        return x * 2

    result = await run_udf(10, multiply)
```

---

## 18. Project Structure Patterns

### UV Script (Single File)

```python
# /// script
# requires-python = "==3.12"
# dependencies = ["pandas", "numpy"]
# ///

import flyte

env = flyte.TaskEnvironment(
    name="uv_script",
    image=flyte.Image.from_uv_script(__file__),
)


@env.task
async def main():
    import pandas as pd

    return pd.DataFrame({"a": [1, 2, 3]})
```

### UV Project (pyproject.toml)

```
my_project/
├── pyproject.toml
├── main.py
└── src/
    └── utils.py
```

```python
# main.py
import pathlib
import flyte

env = flyte.TaskEnvironment(
    name="project",
    image=flyte.Image.from_debian_base().with_uv_project(
        pyproject_file=pathlib.Path("pyproject.toml"),
    ),
)
```

### UV Workspace (Multiple Packages)

```
workspace/
├── pyproject.toml          # Workspace root
├── packages/
│   ├── pkg_a/
│   │   ├── pyproject.toml
│   │   └── src/pkg_a/
│   └── pkg_b/
│       ├── pyproject.toml
│       └── src/pkg_b/
└── src/main/
    └── main.py
```

```toml
# workspace pyproject.toml
[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
pkg-a = { workspace = true }
pkg-b = { workspace = true }

[dependency-groups]
main = ["pkg-a", "pkg-b"]
```

```python
# main.py
env = flyte.TaskEnvironment(
    name="workspace",
    image=flyte.Image.from_debian_base().with_uv_project(
        pyproject_file=pathlib.Path("pyproject.toml"),
        extra_args="--only-group main",
    ),
)
```

### Full Build (Production)

```python
# Disable fast deploy, embed code in image
env = flyte.TaskEnvironment(
    name="prod",
    image=flyte.Image.from_debian_base().with_source_folder(
        pathlib.Path(__file__).parent,
        copy_contents_only=True,
    ),
)

if __name__ == "__main__":
    flyte.init_from_config(root_dir=pathlib.Path(__file__).parent)

    # Full build mode
    run = flyte.with_runcontext(
        copy_style="none",  # Disable fast deploy
        version="v1.0.0",  # Explicit version
    ).run(main)
```

---

## 19. Migration from v1

### Key Differences

| v1 | v2 |
|----|----|
| `@task` decorator | `@env.task` decorator |
| `@workflow` decorator | Regular async function with `@env.task` |
| `@dynamic` for dynamic DAGs | Native Python control flow |
| Promise-based outputs | Direct Python values |
| Separate task/workflow concepts | Unified task model |
| Static DAG compilation | Dynamic execution |

### v1 Workflow → v2 Task

```python
# v1 Style
@task
def add(a: int, b: int) -> int:
    return a + b


@workflow
def math_workflow(x: int) -> int:
    step1 = add(a=x, b=1)
    step2 = add(a=step1, b=2)
    return step2


# v2 Style
@env.task
async def add(a: int, b: int) -> int:
    return a + b


@env.task
async def math_workflow(x: int) -> int:
    step1 = await add(a=x, b=1)
    step2 = await add(a=step1, b=2)
    return step2
```

### v1 Dynamic → v2 Native

```python
# v1 Style
@dynamic
def process_all(items: List[str]) -> List[str]:
    results = []
    for item in items:
        results.append(process_item(item=item))
    return results


# v2 Style
@env.task
async def process_all(items: list[str]) -> list[str]:
    return await asyncio.gather(*[process_item(item) for item in items])
```

---

## Quick Reference

### Initialization

```python
flyte.init_from_config()  # From ~/.flyte/config.yaml
flyte.init_from_config("path/to/config")  # Explicit path
flyte.init(endpoint="...", project="...")  # Programmatic
```

### Running Tasks

```python
run = flyte.run(task, arg=value)  # Remote execution
run = flyte.with_runcontext(mode="local").run(task)  # Local
run.wait()  # Wait for completion
result = run.outputs()  # Get outputs
print(run.url)  # Get UI URL
```

### Task Definition

```python
@env.task                                   # Basic
@env.task(cache="auto")                     # With caching
@env.task(timeout=3600)                     # With timeout
@env.task(report=True)                      # With report
@env.task(triggers=flyte.Trigger.hourly())  # With schedule
```

### Resource Specification

```python
flyte.Resources(
    cpu="2",  # Or (min, max) tuple
    memory="4Gi",
    gpu="T4:1",
    disk="10Gi",
)
```

### File/Dir I/O

```python
File.from_local(path)           # Upload local file
File.new_remote()               # Create new remote file
File.from_existing_remote(uri)  # Reference existing
await f.download()              # Download
async with f.open("rb") as fh:  # Stream read
```

### Error Handling

```python
try:
    await task()
except flyte.errors.OOMError:
    # Handle OOM
except flyte.errors.RuntimeUserError as e:
    if e.code == "ValueError":
        # Handle specific error
```
