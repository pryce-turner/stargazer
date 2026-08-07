# Stargazer

**General Guidelines**
- Always call `ToolSearch` to fetch the schema of any deferred MCP tool before invoking it.
- When I say task, I am referring to a Flyte V2 task, not a raw python function
- Tasks are collected into workflows which are just regular tasks calling other tasks, sync or async
- This project uses UV so the appropriate commands are `uv add` and `uv pip install -e .`
- If something is changed that you didn't change, it's not a typo, it's a manual change. I do still write code occassionally..
- Don't use the "if TYPE_CHECKING:" pattern anywhere, Flyte will always check types
- Do not make any git commits unless explicitly requested
- The README is a document written exclusively BY HUMANS FOR HUMANS. Never modify the README. Notify if it is out of spec only.

**Positioning**
- The marimo notebook is Stargazer's primary user surface for both experimentation (`marimo edit`) and reproducible production (`marimo run`) — it's the most approachable entry point, so default new feature designs to the notebook surface (marimo, `mo.ui`) over CLI or other entry points. The SDK (`src/stargazer/tasks/`, `src/stargazer/workflows/`) is a first-class user surface too: authoring workflows in an IDE by importing SDK tasks directly is a fully supported use case, not a maintainer-only path.
- Two top-level packages: `src/stargazer/` is the bioinformatics SDK (tasks, workflows, assets, TaskEnvironment configs). `app/` at repo root is the deployment / web tier (FastAPI apps, `flyte.app.AppEnvironment` definitions, OAuth/session helpers, HTML templates, deploy entrypoints). When asked to add a deploy entrypoint, FastAPI route, AppEnvironment for a hosted service, OAuth integration, or any other non-SDK runtime code, it goes under `app/`.

**Dev Process**
- You will implement features piece by piece in a sequential fashion
- Handle a single case well at first instead of trying to anticipate every way the app will be used
- Do not add complexity until it is needed, which may be never
- Simple tests will be written before implementation and you will pause to ensure they're capturing the right behavior
- Implementation will be tightly scoped so it can be understood
- Tests will run until they pass
- All necessary CLI tools e.g. parabricks, bwa etc, are available in PATH. Use them to generate test assets as needed and alert the user if they are not available.
- When adding a task that wraps a new CLI tool, check the `TaskEnvironment` it is decorated against in `src/stargazer/config.py` and confirm the tool is layered onto that env's `flyte.Image` (via `with_apt_packages`, `with_commands`, or the bioconda block in `_BIOCONDA_INSTALL`). If it is missing, add it and notify the user.
- When defining a new `TaskEnvironment` in `src/stargazer/config.py`, always call `.with_uv_project(PROJECT_ROOT / "pyproject.toml")` on its image so the stargazer package and its pip deps end up installed, and set explicit `resources=` (e.g. `flyte.Resources(memory=("2Gi", "6Gi"))`). The devbox node has a hard ~7.5 GiB memory budget — see `.opencode/reference/devbox_workarounds.md`.
- **CRITICAL** Do not consider backwards compatibility unless explicitly requested!
- **Keep development minutiae out of user-facing surfaces.** App UI strings/pages, user guides, and the README describe behavior in product terms — a limit, what a control does, what an error means. Do **not** leak implementation detail into them: internal vendor/service names, env vars, workaround mechanisms, ticket/plan references, or "X limit, not ours" rationalizations. (E.g. an upload cap reads "100 MB", not "100 MB — a Pinata limit; larger needs TUS".) That rationale lives in code comments, `.opencode/` references, and plans — and architecture docs under `docs/architecture/` may name internals since they're the technical spec, not a product surface.
- Run `ruff --fix` after every set of changes to satisfy the pre-commit. This only holds because the ruff version is pinned in **two** places that must match: `rev:` in `.pre-commit-config.yaml` (pre-commit builds its own isolated env) and the `ruff` pin in `pyproject.toml`'s dev group. Bump them together — when they drifted across 0.14→0.16, ruff's *default* rule set went from 59 rules to 413 and the two gates silently enforced different things. Deliberate rule exceptions live in `[tool.ruff.lint]` in `pyproject.toml`, each with a comment saying why.
- Prefer bounded version ranges (`>=X,<Y`) over open-ended ones for anything whose API you import. Two breakages this project has actually hit came from unbounded pins walking across a major: `ruff` (rule-set change) and `mcp` (1.x→2.0 renamed `FastMCP`/`mcp.server.fastmcp` to `MCPServer`/`mcp.server`).

## OpenCode Agent Definitions

The `.opencode/agent/` directory contains specialized agent definitions for [OpenCode](https://github.com/sst/opencode), an AI coding assistant. These markdown files define role-specific personas that can be invoked as subagents, each with tailored instructions, temperature settings, and tool permissions.

### Available Agents

| Agent | File | Purpose |
|-------|------|---------|
| **Architecture** | `architecture.md` | Designs feature plans in `.opencode/plans/` and maintains docs in `docs/` |
| **Task** | `task.md` | Implements individual Flyte v2 tasks for bioinformatics tools |
| **Test** | `test.md` | Writes unit and integration tests following TDD approach |
| **Workflow** | `workflow.md` | Composes Flyte v2 tasks into end-to-end pipelines |
| **Code Review** | `code-review.md` | Strict code reviewer that audits for edge cases, UX issues, and data provenance |
| **Technical Writer** | `technical-writer.md` | Writes and edits user-facing docs — assumes competent readers, never gatekeeps surfaces by role |

### Agent File Format

Each agent file uses YAML frontmatter to configure behavior:
```yaml
---
description: Brief description of the agent's role
mode: subagent
temperature: 0.2  # Lower = more deterministic
tools:
  write: true
  edit: true
  bash: true
---
```

The markdown body contains detailed instructions including:
- Role definition and core principles
- Implementation templates and patterns
- Project-specific rules (imports, async patterns, types)
- Checklists and communication guidelines

### When to Use

- **architecture agent**: When designing a new feature or updating system specs
- **task agent**: When implementing a new bioinformatics tool wrapper
- **test agent**: When writing tests for tasks or workflows
- **workflow agent**: When composing tasks into pipelines
- **code-review agent**: Before merging code, to catch issues early
- **technical-writer agent**: When writing or revising prose in `docs/` — tone, framing, and doc conventions

## Docstring Spec References

Every module in `src/` has two conventions in its module-level docstring:

1. The first line is a `###` heading so it renders prominently in the generated API docs.
2. A `spec:` line at the bottom is a markdown link to the relevant architecture doc:

```
spec: [docs/architecture/types.md](../architecture/types.md)
```

**Rationale:** This serves two purposes:
1. **Diff scanning** — when reviewing recent PRs or commits, an LLM can immediately see which spec doc is affected by any changed module and check whether the docs need updating.
2. **Low-overhead lookup** — when making changes to a specific module, the relevant high-level architecture is one link away without any search.

The `spec:` line is **module-level only** — class and function docstrings do not carry it.

100% docstring coverage is enforced by the `docstr-coverage` pre-commit hook.

## Specs, Plans and Reference Materials

- **`.opencode/reference/flyte_v2_docs.md`** - Official Flyte v2 documentation
- **`.opencode/reference/sdk_examples_concise.md`** - Flyte SDK v2 examples
- **`.opencode/reference/devbox_workarounds.md`** - Known devbox-specific quirks and workarounds (signed-URL host, App-pod secrets webhook + the full deploy-secret table, in-cluster init, serving domain off `.localhost` + CoreDNS wildcard, node memory budget, code-bundle non-Python assets). Check first when deploying/debugging against the local devbox cluster. The cluster-side subset is automated by [`cli/devbox-setup.sh`](./cli/devbox-setup.sh) — run it after recreating the container, and keep script and doc in sync. Append any new devbox quirk you diagnose; mark ones fixed upstream rather than deleting them, so a regression is recognizable.
- **`.opencode/reference/tool_refs/`** - Bioinformatics tool documentation, use as the source of truth for tool parameters and behavior
- **`.opencode/reference/architecture/`** - Deep, agent-facing internals for Stargazer's *own* subsystems (vs. `tool_refs/` and the Flyte docs, which are external). Verbose by design — the cross-cutting, multi-module implementation detail that's too granular for the human-facing `docs/architecture/` doc but valuable context when working on that subsystem. Each file is the companion to a `docs/architecture/*.md` doc, which links to it. Keep them in sync when you change the subsystem. (e.g. `app_internals.md` ↔ `docs/architecture/app.md`.)
- **`docs/`** - Project documentation (architecture, guides, reference)
  - **Critical**: Docs must be updated as the project evolves to stay in sync with the current state
  - No code in architecture docs - these are high-level references supported by docstrings in the actual functions
  - Guides are the only docs that contain code examples
  - **Every doc must be reachable from the `nav` in `zensical.toml`.** The nav is hand-maintained, so a new `docs/**/*.md` is invisible (built but unlinked) until you add it. Whenever you add, rename, move, or delete a doc, update `nav` to match, then verify nothing is orphaned: every file under `find docs -name '*.md'` must appear in `zensical.toml`'s `nav` (mkdocstrings-target files like `reference/api.md` included). A new architecture/notebook subpackage also needs an `__init__.py` with the `###` heading + `spec:` line, or the docs build fails to collect it.
- **`.opencode/plans/ROADMAP.md`** - The single priority-ordered list of upcoming work (next feature at the top) and a Complete section. This is where known gaps, deferred work, and "open issues" belong — **not** scattered across `docs/`. When you defer something or surface a production gap while working, add it here rather than leaving an "Open Issues" section in an architecture doc. Individual `NN_*.md` plans link up from their roadmap entry; mark items ✅ and move them to Complete as they ship.
- **`.opencode/plans/`** - Step by step instructions for building new features and fixing bugs
  - Only place outside src where code snippets are allowed
  - Keep track of progress and check off completed work as you go
  - **Prefix every new plan file with the next sequential two-digit integer** so the landing order is visible at a glance and sorts correctly in `ls`: `15_initial_thing.md`, `16_next_thing.md`, `17_followup.md`. Pick the next number by looking at the highest existing prefix across both the top level AND `archive/` (archive is numbered chronologically, top-level continues from where it left off). Do not renumber `future/` until those plans are activated.

## Project Structure

### Directory Organization

The project follows this structure:
- `app/` - Deployment / web tier (FastAPI apps, `flyte.app.AppEnvironment` definitions, OAuth + session helpers, HTML templates, deploy entrypoints). Installed alongside the main package via `[tool.setuptools.packages.find] where = ["src", "."]`. Uvicorn import path is `app:asgi_app`; deploy entry is `app:main`.
- `src/stargazer/` - Main package
  - `__init__.py` - Package root, re-exports key symbols
  - `config.py` - Centralized configuration, env var defaults, TaskEnvironment definitions (`gatk_env`, `scrna_env`), logger setup
  - `server.py` - MCP server implementation
  - `marshal.py` - Serialization helpers for MCP transport
  - `registry.py` - Task/workflow registry for MCP discovery
  - `tasks/` - Flyte task definitions, organized by domain subdirectory
    - `gatk/` - GATK tool tasks (haplotype_caller, mark_duplicates, sort_sam, etc.)
    - `general/` - General bioinformatics tasks (bwa, bwa_mem2, samtools)
    - `scrna/` - Single-cell RNA-seq tasks (cluster, normalize, qc_filter, etc.)
  - `workflows/` - Flyte workflow definitions (one module per pipeline)
  - `types/` - Asset dataclasses (all inherit from `Asset` base class)
  - `utils/` - Utility functions (subprocess, pinata, local_storage, query)
  - `bundles/` - Predefined workflow input bundles (YAML configs)
- `tests/` - Test directory
  - `conftest.py` - Pytest configuration (Flyte init, Pinata JWT injection, fixture paths)
  - `fixtures/` - Test fixtures organized by domain (`gatk/`, `general/`, `scrna/`)
  - `unit/` - Unit tests
  - `tasks/` - Task-level tests mirroring `src/stargazer/tasks/` structure
  - `helpers.py` - Shared test helper functions
- `docs/` - Project documentation
  - `architecture/` - System design and contracts
  - `workflows/` - Workflow-specific documentation (e.g., scRNA-seq)
  - `guides/` - Step-by-step walkthroughs with code examples
  - `reference/` - API reference (catalog of tasks and types)
- `.opencode/reference/` - Agent-facing reference materials (Flyte docs, tool refs)
- `scratch/` - Scratch materials

### Types Directory (`src/stargazer/assets/`)

**Purpose:** Define all input and output dataclasses used across tasks and workflows.

**Guidelines:**
- Create separate modules for different domains (e.g., `reference.py`, `alignment.py`, `variants.py`, `reads.py`, `scrna.py`)
- All types inherit from the `Asset` base class (`asset.py`) which provides `cid`, `path`, `to_keyvalues()`, `from_keyvalues()`, and registry mechanics
- Use descriptive dataclass names (e.g., `Alignment`, `Variants`, `Reference`)
- Group related types in the same module
- Use Python dataclasses with type annotations

### Tasks Directory (`src/stargazer/tasks/`)

**Purpose:** Define individual Flyte tasks that perform specific operations.

**Guidelines:**
- **Modular Organization:** Tasks are organized into domain subdirectories (`gatk/`, `general/`, `scrna/`) with one file per tool (e.g., `gatk/haplotype_caller.py`, `general/bwa.py`)
- **Naming Convention:** Use descriptive, action-oriented names
  - Task files: `{tool}.py` or `{function}.py` within domain subdirectories
  - Task functions: `{action}_{tool}` (e.g., `bwa_mem`, `haplotype_caller`, `sort_sam`)
- **One Task Per Function:** Each task should do one thing well
- **Use Structured I/O:** Leverage dataclasses from `types/` for inputs/outputs
- **Resource Specification:** Define appropriate resource requests (CPU, memory, GPU)

### Workflows Directory (`src/stargazer/workflows/`)

**Purpose:** Compose tasks into end-to-end workflows.

**Guidelines:**
- **Modular Organization:** Separate workflows by analysis type or pipeline (e.g., `germline_variant_calling.py`, `somatic_variant_calling.py`)
- **Naming Convention:** Use descriptive, pipeline-oriented names
  - Workflow files: `{analysis_type}.py` or `{pipeline_name}.py`
  - Workflow functions: `{pipeline_description}` (e.g., `germline_variant_calling_pipeline`)
- **Clear Composition:** Show task dependencies clearly
- **Use Structured I/O:** Leverage dataclasses for workflow inputs/outputs

### Core Concepts

- Main SDK import: `import flyte`
- Task environments are defined in `config.py`: `gatk_env` (GATK/BWA/samtools) and `scrna_env` (scanpy-based scRNA)
- Decorate tasks with `@gatk_env.task` or `@scrna_env.task` (not a generic `pb_env`)
- Async tasks are preferred for I/O operations
- In v2, there is no separate `@workflow` decorator - workflows are tasks that call other tasks
- Use `asyncio.gather` for parallel execution
- All types inherit from `Asset` base class, which provides `cid`/`path` fields, `to_keyvalues()`/`from_keyvalues()` serialization, and an auto-registry via `_asset_key`

## Style and Conventions

- **Paths:** Use `pathlib.Path` for all filesystem operations (e.g., `joinpath`). Use `resolve()` for absolute paths. Only convert to `str` immediately before a subprocess call.
- **Formatting:** Use `ruff` for formatting and correctness checking
- **Imports:** Use the `stargazer` package name, not relative imports across packages. Module level imports should be at the top of the file!!
- **Documentation:** Include docstrings explaining purpose and behavior
- **Resource Awareness:** Specify appropriate resource requests for bioinformatics workloads
