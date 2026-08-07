# Flyte v2 Documentation

> **Note:** These are the Flyte 2.0 beta docs. This documentation for open-source Flyte is maintained by Union.ai.

Flyte is a free and open source platform that provides a full suite of powerful features for orchestrating AI workflows. Flyte empowers AI development teams to rapidly ship high-quality code to production by offering optimized performance, unparalleled resource efficiency, and a delightful workflow authoring experience. You deploy and manage Flyte yourself, on your own cloud infrastructure.

---

## Table of Contents

1. [Flyte 2 Overview](#flyte-2-overview)
2. [Getting Started](#getting-started)
3. [Local Setup](#local-setup)
4. [Task Configuration](#task-configuration)
5. [Container Images](#container-images)
6. [Retries and Timeouts](#retries-and-timeouts)
7. [Task Programming](#task-programming)
   - [DataFrames](#dataframes)
   - [Dataclasses and Structures](#dataclasses-and-structures)
8. [Tutorials](#tutorials)
   - [Deep Research](#deep-research)
   - [Hyperparameter Optimization](#hyperparameter-optimization)
   - [Automatic Prompt Engineering](#automatic-prompt-engineering)

---

## Flyte 2 Overview

Flyte 2 represents a fundamental shift in how workflows are written and executed in Flyte. Ready to get started? Go to the [Getting Started](#getting-started) guide to install Flyte 2 and run your first task.

### Pure Python Execution

Write workflows in pure Python, enabling a more natural development experience and removing the constraints of a domain-specific language (DSL).

**Synchronous Example:**

```python
import flyte

env = flyte.TaskEnvironment("hello_world")


@env.task
def hello_world(name: str) -> str:
    return f"Hello, {name}!"


@env.task
def main(name: str) -> str:
    for i in range(10):
        hello_world(name)
    return "Done"


if __name__ == "__main__":
    flyte.init()
    flyte.run(main, name="World")
```

**Asynchronous Example:**

```python
import asyncio
import flyte

env = flyte.TaskEnvironment("hello_world")


@env.task
async def hello_world(name: str) -> str:
    return f"Hello, {name}!"


@env.task
async def main(name: str) -> str:
    results = []
    for i in range(10):
        results.append(hello_world(name))
    await asyncio.gather(*results)
    return "Done"


if __name__ == "__main__":
    flyte.init()
    flyte.run(main, name="World")
```

Workflows can be constructed at runtime, allowing for more flexible and adaptive behavior. Flyte 2 also supports:

- Python's asynchronous programming model to express parallelism
- Python's native error handling with `try-except` to override configurations like resource requests
- Predefined static workflows when compile-time safety is critical

### Simplified API

The new API is more intuitive, with fewer abstractions to learn and a focus on simplicity.

| Use case | Flyte 1 | Flyte 2 |
|----------|---------|---------|
| Environment management | N/A | `TaskEnvironment` |
| Perform basic computation | `@task` | `@env.task` |
| Combine tasks into a workflow | `@workflow` | `@env.task` |
| Create dynamic workflows | `@dynamic` | `@env.task` |
| Fanout parallelism | `flytekit.map` | Python for loop with `asyncio.gather` |
| Conditional execution | `flytekit.conditional` | Python `if-elif-else` |
| Catching workflow failures | `@workflow(on_failure=...)` | Python `try-except` |

There is no `@workflow` decorator. Instead, "workflows" are authored through a pattern of tasks calling tasks. Tasks are defined within environments, which encapsulate the context and resources needed for execution.

### Fine-grained Reproducibility and Recoverability

Flyte tasks support caching via `@env.task(cache=...)`, but tracing with `@flyte.trace` augments task-level caching even further, enabling reproducibility and recovery at the sub-task function level.

```python
@flyte.trace
async def call_llm(prompt: str) -> str:
    return ...


@env.task
def finalize_output(output: str) -> str:
    return ...


@env.task(cache=flyte.Cache(behavior="auto"))
async def main(prompt: str) -> str:
    output = await call_llm(prompt)
    output = await finalize_output(output)
    return output
```

Here the `call_llm` function is called in the same container as `main` and serves as an automated checkpoint with full observability in the UI. If the task run fails, the workflow can recover and replay from where it left off.

### Improved Remote Functionality

Flyte 2 provides full management of the workflow lifecycle through a standardized API via the CLI and the Python SDK.

| Use case | CLI | Python SDK |
|----------|-----|------------|
| Run a task | `flyte run ...` | `flyte.run(...)` |
| Deploy a task | `flyte deploy ...` | `flyte.deploy(...)` |

You can also fetch and run remote (previously deployed) tasks within a running workflow:

```python
import flyte.remote

env = flyte.TaskEnvironment(name="root")

# Get remote tasks that were previously deployed
torch_task = flyte.remote.Task.get("torch_env.torch_task", auto_version="latest")
spark_task = flyte.remote.Task.get("spark_env.spark_task", auto_version="latest")


@env.task
def main() -> flyte.File:
    dataset = await spark_task(value)
    model = await torch_task(dataset)
    return model
```

### Native Notebook Support

Author and run workflows and fetch workflow metadata (I/O and logs) directly from Jupyter notebooks.

### Enhanced UI

New UI with a streamlined and user-friendly experience for authoring and managing workflows. This UI improves the visualization of workflow execution and monitoring, simplifying access to logs, metadata, and other important information.

---

## Getting Started

This section gives you a quick introduction to writing and running workflows on Union and Flyte 2.

### Prerequisites

#### Install uv

First, install the [uv package manager](https://docs.astral.sh/uv/getting-started/installation/).

You will need to use the `uv` package manager to run the examples in this guide. In particular, we leverage `uv`'s ability to embed dependencies directly in scripts.

#### Install Python 3.10 or later

Flyte 2 requires Python 3.10 or later. Install the most recent version of Python (>= 3.10) compatible with your codebase and pin it:

```bash
uv python install 3.13
uv python pin 3.13 --global
```

#### Create a Python Virtual Environment

In your working directory, create a Python virtual environment and activate it:

```bash
uv venv
source .venv/bin/activate
```

#### Install the flyte Package

Install the latest `flyte` package in the virtual environment (we are currently in beta, so you will have to enable prerelease installation):

```bash
uv pip install --no-cache --prerelease=allow --upgrade flyte
```

#### Create a config.yaml

Create a configuration file that points to your Flyte instance using the `flyte create config` command:

```bash
flyte create config \
  --endpoint my-org.my-company.com \
  --builder local \
  --domain development \
  --project my-project
```

Replace `my-org.my-company.com` with the actual URL of your Flyte backend instance and `my-project` with an actual project that exists on your Flyte backend instance.

By default, this creates a `./.flyte/config.yaml` file in your current working directory. Run `flyte get config` to see the current configuration file being used by the `flyte` CLI.

### Hello World Example

Create a file called `hello.py` with the following content:

```python
# /// script
# requires-python = "==3.13"
# dependencies = [
#     "flyte==2.0.0b31",
# ]
# main = "main"
# params = "x_list=[1,2,3,4,5,6,7,8,9,10]"
# ///

import flyte

# A TaskEnvironment provides a way of grouping the configuration used by tasks.
env = flyte.TaskEnvironment(name="hello_world")


# Use a TaskEnvironment to define tasks, which are regular Python functions.
@env.task
def fn(x: int) -> int:  # Type annotations are recommended.
    slope, intercept = 2, 5
    return slope * x + intercept


# Tasks can call other tasks.
# Each task defined with a given TaskEnvironment will run in its own separate container,
# but the containers will all be configured identically.
@env.task
def main(x_list: list[int] = list(range(10))) -> float:
    x_len = len(x_list)
    if x_len < 10:
        raise ValueError(
            f"x_list doesn't have a larger enough sample size, found: {x_len}"
        )

    # flyte.map is like Python map, but runs in parallel.
    y_list = list(flyte.map(fn, x_list))
    y_mean = sum(y_list) / len(y_list)
    return y_mean


# Running this script locally will perform a flyte.run,
# which will deploy your task code to your remote Union/Flyte instance.
if __name__ == "__main__":
    # Initialize Flyte from a config file.
    flyte.init_from_config()

    # Run your tasks remotely inline and pass parameter data.
    r = flyte.run(main, x_list=list(range(10)))

    # Print various attributes of the run.
    print(r.name)
    print(r.url)

    # Stream the logs from the remote run to the terminal.
    r.wait()
```

### Understanding the Code

In the code above:

1. Import the `flyte` package
2. Define a `TaskEnvironment` to group the configuration used by tasks
3. Define two tasks using the `@env.task` decorator:
   - Tasks are regular Python functions, but each runs in its own container
   - When deployed to your Union/Flyte instance, each task execution will run in its own separate container
   - Both tasks use the same `env` (the same `TaskEnvironment`) so, while each runs in its own container, those containers will be configured identically

### Running the Code

Make sure that your `config.yaml` file is in the same directory as your `hello.py` script. Now, run the script with:

```bash
uv run --prerelease allow hello.py
```

The main guard section in the script performs a `flyte.init_from_config` to set up the connection with your Union/Flyte instance and a `flyte.run` to send your task code to that instance and execute it there.

### Viewing the Results

In your terminal, you should see output like this:

```
cg9s54pksbjsdxlz2gmc
https://my-instance.example.com/v2/runs/project/my-project/domain/development/cg9s54pksbjsdxlz2gmc
Run 'a0' completed successfully.
```

Click the link to go to your Union instance and see the run in the UI.

---

## Local Setup

This section explains the options for configuring the `flyte` CLI and SDK to connect to your Union/Flyte instance.

### Setting Up a Configuration File

The `flyte create config` command creates a configuration file:

```bash
flyte create config \
  --endpoint my-org.my-company.com \
  --project my-project \
  --domain development \
  --builder remote
```

This creates a `./flyte/config.yaml` file with the following content:

```yaml
admin:
  endpoint: dns:///my-org.my-company.com
image:
  builder: remote
task:
  domain: development
  org: my-org
  project: my-project
```

#### Configuration Sections

**admin section:** Contains the connection details for your Union/Flyte instance.
- `admin.endpoint` is the URL (always with `dns:///` prefix) of your Union/Flyte instance
- `admin.insecure` indicates whether to use an insecure connection (without TLS)

**image section:** Contains the configuration for building Docker images for your tasks.
- `image.builder` specifies the image builder to use:
  - For Union instances, usually set to `remote` (images built on Union's infrastructure using ImageBuilder)
  - For Flyte OSS instances, must be set to `local` (images built locally; requires Docker)

**task section:** Contains the configuration for running tasks.
- `task.domain` specifies the domain (e.g., `development`, `staging`, `production`)
- `task.org` specifies the organization
- `task.project` specifies the project (must already exist on your instance)

### Using the Configuration File

#### Explicitly Specify a Configuration File

When using the `flyte` CLI:

```bash
flyte --config my-config.yaml run hello.py main
# or using the -c shorthand:
flyte -c my-config.yaml run hello.py main
```

When using the Flyte SDK programmatically:

```python
flyte.init_from_config("my-config.yaml")
run = flyte.run(main)
```

#### Use the Configuration File Implicitly

You can use the `flyte` CLI without an explicit `--config`:

```bash
flyte run hello.py main
```

Or initialize the SDK without specifying a configuration file:

```python
flyte.init_from_config()
```

The SDK searches in the following order:
1. `./config.yaml`
2. `./.flyte/config.yaml`
3. `UCTL_CONFIG` environment variable
4. `FLYTECTL_CONFIG` environment variable
5. `~/.union/config.yaml`
6. `~/.flyte/config.yaml`

### Checking Your Configuration

Check your current configuration:

```bash
flyte get config
```

### Inline Configuration

#### With flyte CLI

```bash
flyte \
  --endpoint my-org.my-company.com \
  --org my-org \
  run \
  --domain development \
  --project my-project \
  hello.py \
  main
```

#### With flyte SDK

```python
flyte.init(
    endpoint="dns:///my-org.my-company.com",
    org="my-org",
    project="my-project",
    domain="development",
)
```

---

## Task Configuration

You can run any Python function as a task in Flyte just by decorating it with `@env.task`. This allows you to run your Python code in a distributed manner, with each function running in its own container.

The simplest possible case:

```python
env = flyte.TaskEnvironment(name="my_env")


@env.task
async def my_task(name: str) -> str:
    return f"Hello {name}!"
```

Notice how the `TaskEnvironment` is assigned to the variable `env` and then that variable is used in the `@env.task`. This connects the `TaskEnvironment` to the task definition.

### Task Configuration Levels

Task configuration is done at three levels. From most general to most specific:

1. **TaskEnvironment level:** Setting parameters when defining the `TaskEnvironment` object
2. **@env.task decorator level:** Setting parameters in the decorator when defining a task function
3. **Task invocation level:** Using the `task.override()` method when invoking task execution

For shared parameters, the more specific level overrides the more general one.

### Example

```python
# Level 1: TaskEnvironment - Base configuration
env_2 = flyte.TaskEnvironment(
    name="data_processing_env",
    image=flyte.Image.from_debian_base(),
    resources=flyte.Resources(cpu=1, memory="512Mi"),
    env_vars={"MY_VAR": "value"},
    cache="disable",
    depends_on=[another_env],
    description="Data processing task environment",
)


# Level 2: Decorator - Override some environment settings
@env_2.task(
    short_name="process",
    cache="auto",
    report=True,
    max_inline_io_bytes=100 * 1024,
    retries=3,
    timeout=60,
    docs="This task processes data and generates a report.",
)
async def process_data(data_path: str) -> str:
    return f"Processed {data_path}"


@env_2.task
async def invoke_process_data() -> str:
    result = await process_data.override(
        resources=flyte.Resources(cpu=4, memory="2Gi"),
        env_vars={"MY_VAR": "new_value"},
        cache="auto",
        max_inline_io_bytes=100 * 1024,
        retries=3,
        timeout=60,
    )("input.csv")
    return result
```

### Parameter Overview

| Parameter | TaskEnvironment | @env.task | override |
|-----------|-----------------|-----------|----------|
| `name` | ✅ Yes (required) | ❌ No | ❌ No |
| `short_name` | ❌ No | ✅ Yes | ✅ Yes |
| `image` | ✅ Yes | ❌ No | ❌ No |
| `resources` | ✅ Yes | ❌ No | ✅ Yes (if not reusable) |
| `env_vars` | ✅ Yes | ❌ No | ✅ Yes (if not reusable) |
| `secrets` | ✅ Yes | ✅ Yes | ✅ Yes (if not reusable) |
| `cache` | ✅ Yes | ✅ Yes | ✅ Yes |
| `pod_template` | ✅ Yes | ✅ Yes | ❌ No |
| `reusable` | ✅ Yes | ❌ No | ✅ Yes |
| `depends_on` | ✅ Yes | ❌ No | ❌ No |
| `description` | ✅ Yes | ❌ No | ❌ No |
| `plugin_config` | ✅ Yes | ❌ No | ❌ No |
| `report` | ❌ No | ✅ Yes | ❌ No |
| `max_inline_io_bytes` | ❌ No | ✅ Yes | ✅ Yes |
| `retries` | ❌ No | ✅ Yes | ✅ Yes |
| `timeout` | ❌ No | ✅ Yes | ✅ Yes |
| `docs` | ❌ No | ✅ Yes | ❌ No |

### Task Configuration Parameters

#### name
- **Type:** `str` (required)
- Defines the name of the `TaskEnvironment`. Used in conjunction with the name of each `@env.task` function to define the fully-qualified name of the task.

#### short_name
- **Type:** `str`
- Defines the short name of the task or action. By default, the short name is the name of the task function.

#### image
- **Type:** `Union[str, Image, Literal['auto']]`
- Specifies the Docker image to use for the task container. Can be a URL reference, an `Image` object, or `auto`.
- Only settable at the `TaskEnvironment` level.

#### resources
- **Type:** `Optional[Resources]`
- Specifies the compute resources (CPU, Memory) required by the task environment.

#### env_vars
- **Type:** `Optional[Dict[str, str]]`
- A dictionary of environment variables to be made available in the task container.

#### secrets
- **Type:** `Optional[SecretRequest]`
- The secrets to be made available in the task container.

#### cache
- **Type:** `Union[CacheRequest]` where `CacheRequest` is `Literal["auto", "override", "disable", "enabled"] | Cache`
- Specifies the caching policy to be used for this task.

#### pod_template
- **Type:** `Optional[Union[str, kubernetes.client.V1PodTemplate]]`
- A pod template that defines the Kubernetes pod configuration for the task.

#### depends_on
- **Type:** `List[Environment]`
- A list of `Environment` objects that this `TaskEnvironment` depends on.

#### description
- **Type:** `Optional[str]`
- A description of the task environment.

#### report
- **Type:** `bool`
- Whether to generate the HTML report for the task.

#### max_inline_io_bytes
- **Type:** `int`
- Maximum allowed size (in bytes) for all inputs and outputs passed directly to the task. Default is 100 MiB.

#### retries
- **Type:** `Union[int, RetryStrategy]`
- The number of retries for the task, or a `RetryStrategy` object.

#### timeout
- **Type:** `Union[timedelta, int]`
- The timeout for the task (as a `timedelta` object or seconds).

#### docs
- **Type:** `Optional[Documentation]`
- Documentation for the task.

---

## Container Images

The `image` parameter of the `TaskEnvironment` specifies a container image. Every task defined using that `TaskEnvironment` will run in a container based on that image.

If a `TaskEnvironment` does not specify an `image`, it will use the default Flyte image (`ghcr.io/flyteorg/flyte:py{python-version}-v{flyte_version}`).

### Specifying Your Own Image Directly

You can directly reference an image by URL:

```python
env = flyte.TaskEnvironment(name="my_task_env", image="docker.io/myorg/myimage:mytag")
```

### Specifying Your Own Image with the flyte.Image Object

The `flyte.Image` object provides a fluent interface for building container images.

**Start with one of the `from_` methods:**
- `Image.from_base()`: Start from a specified Dockerfile
- `Image.from_debian_base()`: Start from the Flyte default image
- `Image.from_uv_script()`: Start from a uv script

**Then layer additional components using `with_` methods:**
- `Image.with_apt_packages()`: Add Debian packages
- `Image.with_commands()`: Add commands to run
- `Image.with_dockerignore()`: Specify a .dockerignore file
- `Image.with_env_vars()`: Set environment variables
- `Image.with_pip_packages()`: Add Python packages
- `Image.with_requirements()`: Specify a requirements.txt file
- `Image.with_source_file()`: Specify a source file
- `Image.with_source_folder()`: Specify a source folder
- `Image.with_uv_project()`: Use uv script metadata
- `Image.with_poetry_project()`: Create a new image with pyproject.toml
- `Image.with_workdir()`: Specify the working directory

### Example: Image.from_debian_base

```python
import flyte
import numpy as np

env = flyte.TaskEnvironment(
    name="my_env",
    image=(
        flyte.Image.from_debian_base(name="my-image", python_version=(3, 13))
        .with_apt_packages("libopenblas-dev")
        .with_pip_packages("numpy")
        .with_env_vars({"OMP_NUM_THREADS": "4"})
    ),
)


@env.task
def main(x_list: list[int]) -> float:
    arr = np.array(x_list)
    return float(np.mean(arr))


if __name__ == "__main__":
    flyte.init_from_config()
    r = flyte.run(main, x_list=list(range(10)))
    print(r.name)
    print(r.url)
    r.wait()
```

> **Note:** Images built with `flyte.Image.from_debian_base()` do not include CA certificates by default. Add `"ca-certificates"` using `.with_apt_packages()` to avoid TLS validation errors.

### Example: Image Based on uv Script Metadata

```python
# /// script
# requires-python = "==3.13"
# dependencies = [
#     "flyte==2.0.0b31",
#     "numpy"
# ]
# main = "main"
# params = "x_list=[1,2,3,4,5,6,7,8,9,10]"
# ///

import flyte
import numpy as np

env = flyte.TaskEnvironment(
    name="my_env", image=flyte.Image.from_uv_script(__file__, name="my-image")
)


@env.task
def main(x_list: list[int]) -> float:
    arr = np.array(x_list)
    return float(np.mean(arr))
```

### Image Building

There are two ways to build images:

1. **Local building (Flyte OSS):** Image is built locally and pushed to your specified container registry. Requires Docker.
2. **Remote ImageBuilder (Union):** Image is built on Union's infrastructure using ImageBuilder.

Configure the `image.builder` property in `config.yaml`:
- For Flyte OSS: set to `local`
- For Union: set to `remote` (uses ImageBuilder) or `local`

#### Local Image Building

When `image.builder` is `local`, `flyte.run()`:
1. Builds the Docker image locally
2. Pushes the image to your specified container registry
3. Deploys your code to the backend
4. Kicks off the execution

**Requirements:**
- Docker must be running on your local machine
- You must have run `docker login` to your registry
- Your Union/Flyte installation must have read access to that registry

#### Remote ImageBuilder

When `image.builder` is `remote` (Union only), `flyte.run()`:
1. Builds the Docker image on your Union instance with ImageBuilder
2. Pushes the image to the internal registry (or specified external registry)
3. Deploys your code to the backend
4. Kicks off the execution

No local Docker setup required.

### Install Private PyPI Packages

To install from a private PyPI index, mount a secret to the image layer:

```python
private_package = "git+https://[email protected]/pingsutw/flytex.git@2e20a2acebfc3877d84af643fdd768edea41d533"

image = (
    Image.from_debian_base()
    .with_apt_packages("git")
    .with_pip_packages(private_package, pre=True, secret_mounts=Secret("GITHUB_PAT"))
)
```

---

## Retries and Timeouts

Flyte provides robust error handling through configurable retry strategies and timeout controls.

### Retries

The `retries` parameter controls how many times a failed task should be retried before giving up. A "retry" is any attempt after the initial attempt (`retries=3` means up to 4 total attempts).

```python
import random
from datetime import timedelta
import flyte

env = flyte.TaskEnvironment(name="my-env")


@env.task(retries=3)
async def retry() -> str:
    if random.random() < 0.7:  # 70% failure rate
        raise Exception("Task failed!")
    return "Success!"


@env.task
async def main() -> list[str]:
    results = []
    try:
        results.append(await retry())
    except Exception as e:
        results.append(f"Failed: {e}")

    try:
        results.append(await retry.override(retries=5)())
    except Exception as e:
        results.append(f"Failed: {e}")

    return results


if __name__ == "__main__":
    flyte.init_from_config()
    r = flyte.run(main)
    print(r.name)
    print(r.url)
    r.wait()
```

### Timeouts

The `timeout` parameter sets limits on how long a task can run.

```python
import random
from datetime import timedelta
import asyncio
import flyte
from flyte import Timeout

env = flyte.TaskEnvironment(name="my-env")


# Using seconds as an integer
@env.task(timeout=60)
async def timeout_seconds() -> str:
    await asyncio.sleep(random.randint(0, 120))
    return "timeout_seconds completed"


# Using a timedelta object
@env.task(timeout=timedelta(minutes=1))
async def timeout_timedelta() -> str:
    await asyncio.sleep(random.randint(0, 120))
    return "timeout_timedelta completed"


# Using the Timeout class for separate max_runtime and max_queued_time
@env.task(
    timeout=Timeout(
        max_runtime=timedelta(minutes=1), max_queued_time=timedelta(minutes=1)
    )
)
async def timeout_advanced() -> str:
    await asyncio.sleep(random.randint(0, 120))
    return "timeout_advanced completed"


# Combining retries and timeouts
@env.task(
    retries=3,
    timeout=Timeout(
        max_runtime=timedelta(minutes=1), max_queued_time=timedelta(minutes=1)
    ),
)
async def timeout_with_retry() -> str:
    await asyncio.sleep(random.randint(0, 120))
    return "timeout_with_retry completed"
```

---

## Task Programming

### DataFrames

By default, return values in Python are materialized—meaning the actual data is downloaded and loaded into memory. To avoid downloading large datasets into memory, Flyte V2 exposes `flyte.io.DataFrame`: a thin, uniform wrapper type for DataFrame-style objects that allows you to pass a reference to the data, rather than the fully materialized contents.

The `flyte.io.DataFrame` type provides serialization support for common engines like `pandas`, `polars`, `pyarrow`, `dask`, etc.

#### Setting Up the Environment and Sample Data

```python
from typing import Annotated
import numpy as np
import pandas as pd
import flyte
import flyte.io

env = flyte.TaskEnvironment(
    "dataframe_usage",
    image=flyte.Image.from_debian_base().with_pip_packages(
        "pandas", "pyarrow", "numpy"
    ),
    resources=flyte.Resources(cpu="1", memory="2Gi"),
)

BASIC_EMPLOYEE_DATA = {
    "employee_id": range(1001, 1009),
    "name": ["Alice", "Bob", "Charlie", "Diana", "Ethan", "Fiona", "George", "Hannah"],
    "department": [
        "HR",
        "Engineering",
        "Engineering",
        "Marketing",
        "Finance",
        "Finance",
        "HR",
        "Engineering",
    ],
    "hire_date": pd.to_datetime(
        [
            "2018-01-15",
            "2019-03-22",
            "2020-07-10",
            "2017-11-01",
            "2021-06-05",
            "2018-09-13",
            "2022-01-07",
            "2020-12-30",
        ]
    ),
}
```

#### Create a Raw DataFrame

```python
@env.task
async def create_raw_dataframe() -> pd.DataFrame:
    return pd.DataFrame(BASIC_EMPLOYEE_DATA)
```

Flyte supports auto-serialization for:
- `pandas.DataFrame`
- `pyarrow.Table`
- `dask.dataframe.DataFrame`
- `polars.DataFrame`
- `flyte.io.DataFrame`

#### Create a flyte.io.DataFrame

```python
@env.task
async def create_flyte_dataframe() -> Annotated[flyte.io.DataFrame, "parquet"]:
    pd_df = pd.DataFrame(ADDL_EMPLOYEE_DATA)
    fdf = flyte.io.DataFrame.from_df(pd_df)
    return fdf
```

The `flyte.io.DataFrame` class creates a thin wrapper around objects of any standard DataFrame type. You can be explicit about the storage format using an `Annotated` type.

#### Automatically Convert Between Types

```python
@env.task
async def join_data(
    raw_dataframe: pd.DataFrame, flyte_dataframe: pd.DataFrame
) -> flyte.io.DataFrame:
    joined_df = raw_dataframe.merge(flyte_dataframe, on="employee_id", how="inner")
    return flyte.io.DataFrame.from_df(joined_df)
```

Flyte automatically converts the `flyte.io.DataFrame` to a Pandas DataFrame (since declared as the input type) before passing it to the task.

#### Downloading DataFrames

```python
@env.task
async def download_data(joined_df: flyte.io.DataFrame):
    downloaded = await joined_df.open(pd.DataFrame).all()
    print("Downloaded Data:\n", downloaded)
```

The `open()` call delegates to the DataFrame handler for the stored format and converts to the requested in-memory type.

### Dataclasses and Structures

Dataclasses and Pydantic models are fully supported in Flyte as materialized data types. Unlike offloaded types like DataFrames, Files and Dirs, dataclass and Pydantic model data is fully serialized, stored, and deserialized between tasks.

```python
import asyncio
from dataclasses import dataclass
from typing import List
from pydantic import BaseModel
import flyte

env = flyte.TaskEnvironment(name="ex-mixed-structures")


@dataclass
class InferenceRequest:
    feature_a: float
    feature_b: float


@dataclass
class BatchRequest:
    requests: List[InferenceRequest]
    batch_id: str = "default"


class PredictionSummary(BaseModel):
    predictions: List[float]
    average: float
    count: int
    batch_id: str


@env.task
async def predict_one(request: InferenceRequest) -> float:
    """A dummy linear model: prediction = 2 * feature_a + 3 * feature_b + bias(=1.0)"""
    return 2.0 * request.feature_a + 3.0 * request.feature_b + 1.0


@env.task
async def process_batch(batch: BatchRequest) -> PredictionSummary:
    """Processes a batch of inference requests and returns summary statistics."""
    tasks = [predict_one(request=req) for req in batch.requests]
    predictions = await asyncio.gather(*tasks)

    average = sum(predictions) / len(predictions) if predictions else 0.0
    return PredictionSummary(
        predictions=predictions,
        average=average,
        count=len(predictions),
        batch_id=batch.batch_id,
    )


@env.task
async def summarize_results(summary: PredictionSummary) -> str:
    """Creates a text summary from the prediction results."""
    return (
        f"Batch {summary.batch_id}: "
        f"Processed {summary.count} predictions, "
        f"average value: {summary.average:.2f}"
    )


@env.task
async def main() -> str:
    batch = BatchRequest(
        requests=[
            InferenceRequest(feature_a=1.0, feature_b=2.0),
            InferenceRequest(feature_a=3.0, feature_b=4.0),
            InferenceRequest(feature_a=5.0, feature_b=6.0),
        ],
        batch_id="demo_batch_001",
    )
    summary = await process_batch(batch)
    result = await summarize_results(summary)
    return result


if __name__ == "__main__":
    flyte.init_from_config()
    r = flyte.run(main)
    print(r.name)
    print(r.url)
    r.wait()
```

---

## Tutorials

### Deep Research

This example demonstrates how to build an agentic workflow for deep research—a multi-step reasoning system that mirrors how a human researcher explores, analyzes, and synthesizes information from the web.

Deep research refers to the iterative process of thoroughly investigating a topic: identifying relevant sources, evaluating their usefulness, refining the research direction, and ultimately producing a well-structured summary or report.

The agent uses:
- **Tavily** to search for and retrieve high-quality online resources
- **LiteLLM** to route LLM calls that perform reasoning, evaluation, and synthesis

The agent executes a multi-step trajectory:
1. Parallel search across multiple queries
2. Evaluation of retrieved results
3. Adaptive iteration: If results are insufficient, it formulates new research queries and repeats
4. Synthesis: After a fixed number of iterations, it produces a comprehensive research report

**Why Flyte is well-suited for this:**
- Structured composition of dynamic reasoning steps
- Built-in parallelism for faster search and evaluation
- Traceability and observability into each step and iteration
- Scalability for long-running or compute-intensive workloads

#### Setting Up the Environment

```python
import asyncio
import json
from pathlib import Path
import flyte
import yaml
from flyte.io._file import File

env = flyte.TaskEnvironment(
    name="deep-researcher",
    secrets=[
        flyte.Secret(key="together_api_key", as_env_var="TOGETHER_API_KEY"),
        flyte.Secret(key="tavily_api_key", as_env_var="TAVILY_API_KEY"),
    ],
    image=flyte.Image.from_uv_script(__file__, name="deep-research-agent", pre=True)
    .with_apt_packages("pandoc", "texlive-xetex")
    .with_source_file(Path("prompts.yaml"), "/root"),
    resources=flyte.Resources(cpu=1),
)
```

#### Using flyte.trace for Observability

```python
@flyte.trace
async def asingle_shot_llm_call(
    model: str,
    system_prompt: str,
    message: str,
    response_format=None,
    max_completion_tokens=None,
):
    stream = await acompletion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ],
        temperature=0.0,
        response_format=response_format,
        max_tokens=max_completion_tokens,
        timeout=600,
        stream=True,
    )
    async for chunk in stream:
        content = chunk.choices[0].delta.get("content", "")
        if content:
            yield content
```

`flyte.trace` tracks intermediate steps within a task, like LLM calls or specific function executions. This lightweight decorator adds observability with minimal overhead.

#### Run the Deep Research Agent

```bash
flyte create secret TOGETHER_API_KEY <>
flyte create secret TAVILY_API_KEY <>
uv run --prerelease=allow agent.py
```

### Hyperparameter Optimization

Hyperparameter Optimization (HPO) is a critical step in the ML lifecycle. This example combines Flyte with Optuna to optimize a RandomForestClassifier on the Iris dataset.

**Benefits of using Flyte for HPO:**
- No need to manage a separate centralized database for state tracking
- Full observability in the UI with complete lineage and metadata for each trial
- Each objective is seeded for reproducibility
- If the main optimization task crashes, Flyte can resume from the last successful trial
- Trial functions can be strongly typed

#### Define the Task Environment

```python
import flyte

driver = flyte.TaskEnvironment(
    name="driver",
    resources=flyte.Resources(cpu=1, memory="250Mi"),
    image=flyte.Image.from_uv_script(__file__, name="optimizer"),
    cache="auto",
)
```

#### Define the Objective Function

```python
@driver.task
async def objective(params: dict[str, Union[int, float]]) -> float:
    data = load_iris()
    X, y = shuffle(data.data, data.target, random_state=42)
    
    clf = RandomForestClassifier(
        n_estimators=params["n_estimators"],
        max_depth=params["max_depth"],
        min_samples_split=params["min_samples_split"],
        random_state=42,
        n_jobs=-1,
    )
    
    score = cross_val_score(clf, X, y, cv=3, scoring="accuracy").mean()
    return score.item()
```

#### Run the Experiment

```python
if __name__ == "__main__":
    flyte.init_from_config()
    run = flyte.run(optimize, 100, 10)
    print(run.url)
    run.wait()
```

### Automatic Prompt Engineering

When building with LLMs and agents, the first prompt almost never works. Flyte turns prompt engineering into a systematic process.

**With Flyte, you can:**
- Generate candidate prompts automatically
- Run evaluations in parallel
- Track results in real time with built-in observability
- Recover from failures without losing progress
- Trace the lineage of every experiment for reproducibility

#### Set Up the Environment

```python
import flyte
import flyte.report
import pandas as pd
from flyte.io._file import File

env = flyte.TaskEnvironment(
    name="auto-prompt-engineering",
    image=flyte.Image.from_uv_script(
        __file__, name="auto-prompt-engineering", pre=True
    ),
    secrets=[flyte.Secret(key="openai_api_key", as_env_var="OPENAI_API_KEY")],
    resources=flyte.Resources(cpu=1),
)
```

#### Define Model Configuration

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class ModelConfig:
    model_name: str
    hosted_model_uri: Optional[str] = None
    temperature: float = 0.0
    max_tokens: Optional[int] = 1000
    timeout: int = 600
    prompt: str = ""
```

#### Evaluate Prompts

```python
@env.task(report=True)
async def evaluate_prompt(
    df: pd.DataFrame,
    target_model_config: ModelConfig,
    review_model_config: ModelConfig,
    concurrency: int,
) -> float:
    semaphore = asyncio.Semaphore(concurrency)
    counter = {"correct": 0, "processed": 0}
    counter_lock = asyncio.Lock()

    # Write initial HTML structure for live reporting
    await flyte.report.log.aio(
        CSS
        + """
    <h2 style="margin-top:0;">Model Evaluation Results</h2>
    <h3>Live Accuracy</h3>
    ...
    """,
        do_flush=True,
    )

    # Launch tasks concurrently
    tasks = [run_grouped_task(...) for i, row in enumerate(df.itertuples(index=True))]
    await asyncio.gather(*tasks)

    async with counter_lock:
        return (
            (counter["correct"] / counter["processed"]) if counter["processed"] else 0.0
        )
```

#### Run It

```python
if __name__ == "__main__":
    flyte.init_from_config()
    run = flyte.run(auto_prompt_engineering)
    print(run.url)
    run.wait()
```

```bash
uv run --prerelease=allow optimizer.py
```

---

## Additional Resources

- [GitHub Examples](https://github.com/unionai/unionai-examples)
- [Union.ai Documentation](https://www.union.ai/docs/v2/flyte/)
- [Flyte OSS Documentation](https://docs.flyte.org/)

---

*This documentation was compiled from the official Flyte v2 documentation at union.ai/docs/v2/flyte/*