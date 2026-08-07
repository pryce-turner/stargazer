"""Pre-build script: generate the catalog and API reference pages.

Writes standalone pages under docs/reference/ from the task, workflow,
and asset registries. Run this before `zensical build`.

Usage:
    uv run python docs/gen_catalog.py
"""

import dataclasses
from pathlib import Path

from stargazer.assets import ASSET_REGISTRY
from stargazer.assets.asset import _BASE_FIELDS
from stargazer.bundles import list_bundles
from stargazer.registry import TaskRegistry

DOCS = Path(__file__).parent
registry = TaskRegistry()


def _task_table(category: str) -> str:
    items = registry.to_catalog(category=category)
    if not items:
        return "*No entries registered.*"
    rows = [
        "| Name | Description | Parameters |",
        "|------|-------------|------------|",
    ]
    for item in items:
        params = ", ".join(f"`{p['name']}` (`{p['type']}`)" for p in item["params"])
        rows.append(f"| `{item['name']}` | {item['description']} | {params} |")
    return "\n".join(rows)


def _asset_table() -> str:
    if not ASSET_REGISTRY:
        return "*No asset types registered.*"
    rows = [
        "| Asset Key | Class | Module | Fields |",
        "|-----------|-------|--------|--------|",
    ]
    for asset_key, cls in sorted(ASSET_REGISTRY.items()):
        module = cls.__module__.split(".")[-1]
        fields = sorted(
            f.name for f in dataclasses.fields(cls) if f.name not in _BASE_FIELDS
        )
        field_str = ", ".join(f"`{f}`" for f in fields) if fields else "—"
        rows.append(
            f"| `{asset_key}` | `{cls.__name__}` | `types/{module}.py` | {field_str} |"
        )
    return "\n".join(rows)


def _bundle_table() -> str:
    bundles = list_bundles()
    if not bundles:
        return "*No bundles defined.*"
    rows = [
        "| Name | Description | Files |",
        "|------|-------------|-------|",
    ]
    for b in bundles:
        rows.append(f"| `{b['name']}` | {b['description']} | {b['file_count']} |")
    return "\n".join(rows)


def _api_directives() -> str:
    src = DOCS.parent / "src"
    sections: dict[str, list[str]] = {}
    for path in sorted(src.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        module = ".".join(path.relative_to(src).with_suffix("").parts)
        parts = path.relative_to(src / "stargazer").parts
        if len(parts) > 1:
            section = parts[0].replace("_", " ").title()
        else:
            section = path.stem.replace("_", " ").title()
        sections.setdefault(section, []).append(f"::: {module}")
    lines = []
    for section, directives in sections.items():
        lines += [f"## {section}", ""] + directives + [""]
    return "\n".join(lines)


def build() -> None:
    ref_dir = DOCS / "reference"
    ref_dir.mkdir(exist_ok=True)

    # Catalog page
    catalog_lines = [
        "# Catalog",
        "",
        "## Tasks",
        "",
        _task_table("task"),
        "",
        "## Workflows",
        "",
        _task_table("workflow"),
        "",
        "## Asset Types",
        "",
        _asset_table(),
        "",
        "## Bundles",
        "",
        _bundle_table(),
        "",
    ]
    catalog_path = ref_dir / "catalog.md"
    catalog_path.write_text("\n".join(catalog_lines))
    print(f"Generated {catalog_path}")

    # API reference page
    api_lines = [
        "# API Reference",
        "",
        _api_directives(),
    ]
    api_path = ref_dir / "api.md"
    api_path.write_text("\n".join(api_lines))
    print(f"Generated {api_path}")


if __name__ == "__main__":
    build()
