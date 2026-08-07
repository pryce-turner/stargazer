"""
### Notebook registry consumed by the dashboard app.

Curated, hand-maintained tuple of every notebook that ships in the
`notebook-app` image. Workspace notebooks are NOT listed here —
they're discovered at render time from the per-notebook pod's local
clone of the user's fork (or the GitHub Contents API as a fallback).

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

from dataclasses import dataclass
from typing import Literal

# Workspace-section paths live under /workspace/<src/...>/workspace at runtime;
# image-shipped notebooks live at /stargazer/<src/...> after the Docker COPY.
# Snapshots live in a sibling dir on the fork (same sparse-clone cone), served
# read-only in run mode.
IMAGE_WORKDIR = "/stargazer"
WORKSPACE_NOTEBOOK_DIR = "/workspace/src/stargazer/notebooks/workspace"
SNAPSHOT_NOTEBOOK_DIR = "/workspace/src/stargazer/notebooks/snapshots"


# Seed notebooks shipped in every fork at `notebooks/workspace/{slug}.py`.
# `/workspace/create` copies one of these under the user's chosen name. They
# are NOT rendered as dashboard tiles (only user-created notebooks are): the
# template is linked from the Workspace description, and both slugs are
# reserved create names + filtered out of the tile listing.
TEMPLATE_SLUG = "template"
BLANK_SLUG = "blank"
SEED_SLUGS = frozenset({TEMPLATE_SLUG, BLANK_SLUG})


@dataclass(frozen=True)
class Notebook:
    """A single tile on the dashboard.

    `path_in_image` is the absolute path to the `.py` file inside the
    `note` image. `section` drives which dashboard column the tile
    renders under and (with `slug`) keys the per-notebook AppEnvironment
    Knative name.
    """

    slug: str
    title: str
    description: str
    section: Literal["tutorials", "workflows"]
    path_in_image: str


# Tutorials are ordered as a reading sequence: assets → tasks → workflows →
# execution. Workflows (full pipelines) render in their own dashboard section.
NOTEBOOKS: tuple[Notebook, ...] = (
    Notebook(
        slug="assets",
        title="1. Assets",
        description="Content-addressed I/O primitives.",
        section="tutorials",
        path_in_image=f"{IMAGE_WORKDIR}/src/stargazer/notebooks/tutorials/assets.py",
    ),
    Notebook(
        slug="tasks",
        title="2. Tasks",
        description="Define a single task with typed asset I/O.",
        section="tutorials",
        path_in_image=f"{IMAGE_WORKDIR}/src/stargazer/notebooks/tutorials/tasks.py",
    ),
    Notebook(
        slug="workflows",
        title="3. Workflows",
        description="Compose tasks into a workflow with asyncio.gather fan-out.",
        section="tutorials",
        path_in_image=f"{IMAGE_WORKDIR}/src/stargazer/notebooks/tutorials/workflows.py",
    ),
    Notebook(
        slug="execution",
        title="4. Execution",
        description="Run a real workflow locally, then remote — no code changes.",
        section="tutorials",
        path_in_image=f"{IMAGE_WORKDIR}/src/stargazer/notebooks/tutorials/execution.py",
    ),
    Notebook(
        slug="scrna-pipeline",
        title="scRNA-seq",
        description="Multi-sample fan-out, clustering, side-by-side UMAPs.",
        section="workflows",
        path_in_image=f"{IMAGE_WORKDIR}/src/stargazer/notebooks/workflows/scrna_pipeline.py",
    ),
)


def by_slug(slug: str) -> Notebook | None:
    """Return the notebook with the given slug, or None if absent."""
    for n in NOTEBOOKS:
        if n.slug == slug:
            return n
    return None


def by_section(section: str) -> tuple[Notebook, ...]:
    """Return all notebooks belonging to one dashboard section."""
    return tuple(n for n in NOTEBOOKS if n.section == section)
