"""
### Static parsing of a notebook's `[tool.stargazer]` resource block.

The admin app sets a per-notebook pod's resources at `/launch` time —
before the pod exists — so the resource spec has to be readable from the
notebook *source*, not from running code. Notebooks already carry a PEP
723 script header (`# /// script … # ///`) for their deps; this module
reads an optional `[tool.stargazer]` table from that same header:

    # /// script
    # dependencies = ["stargazer"]
    #
    # [tool.stargazer]
    # cpu = 2
    # memory = "4Gi"
    # ///

Parsing is purely textual (regex + `tomllib`) — no notebook code is ever
executed. Anything missing or malformed falls back to `DEFAULT_RESOURCES`
rather than failing the launch. There is no ceiling: whatever a notebook
declares is honored as-is, so you rightsize per-notebook for the target
cluster.

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import re
import tomllib
from dataclasses import dataclass

# PEP 723 script-metadata block: `# /// script` … `# ///`, every line in
# between prefixed with `#`. Mirrors the reference regex in the PEP.
_PEP723_BLOCK = re.compile(
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s?)+)^# ///$"
)


@dataclass(frozen=True)
class NotebookResources:
    """Normalized resource request parsed from a notebook header.

    Kept separate from `flyte.Resources` so this module stays pure and
    framework-free; `app.per_notebook` converts it at launch time.
    `memory` is a Kubernetes-style quantity string (e.g. `"4Gi"`).
    """

    cpu: int
    memory: str


# Fallback only — used when a notebook declares no `[tool.stargazer]` block.
# No ceiling is applied anywhere; whatever a notebook authors is honored, so
# you rightsize per-notebook for the target cluster.
DEFAULT_RESOURCES = NotebookResources(cpu=1, memory="2Gi")


def _coerce_cpu(value: object) -> int:
    """Coerce `value` to an int cpu count, or the default if it isn't one."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_RESOURCES.cpu


def _coerce_memory(value: object) -> str:
    """Coerce a memory input to a Kubernetes quantity string.

    GiB is the fixed unit: a bare integer (the create form's number input, or
    `memory = 8` authored in a header) becomes `"<n>Gi"`. An already-suffixed
    quantity (`"4Gi"`, `"512Mi"`) passes through unchanged; empty/None falls
    back to `DEFAULT_RESOURCES.memory`.
    """
    if value is None:
        return DEFAULT_RESOURCES.memory
    text = str(value).strip()
    if not text:
        return DEFAULT_RESOURCES.memory
    return f"{text}Gi" if text.isdigit() else text


def _extract_stargazer_table(source: str) -> dict:
    """Return the `[tool.stargazer]` table from the PEP 723 header, or {}.

    Never raises: a missing block, non-`script` block, malformed TOML, or
    absent table all yield an empty dict so callers fall back to defaults.
    """
    match = _PEP723_BLOCK.search(source)
    if match is None or match.group("type") != "script":
        return {}
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in match.group("content").splitlines(keepends=True)
    )
    try:
        meta = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return {}
    table = meta.get("tool", {}).get("stargazer", {})
    return table if isinstance(table, dict) else {}


def parse_notebook_description(source: str) -> str:
    """Return the `[tool.stargazer]` `description` string, or "" if absent.

    Companion to `parse_notebook_resources`: the workspace tile's blurb is
    authored in the same header table and edited via the settings modal. Always
    returns a str — a missing/non-string value yields "".
    """
    value = _extract_stargazer_table(source).get("description", "")
    return value if isinstance(value, str) else ""


def parse_notebook_name(source: str) -> str:
    """Return the `[tool.stargazer]` `name` string, or "" if absent.

    The display name preserves the title exactly as the user typed it at
    creation (the filename is a lowercased, dash-joined derivation, so the
    original casing/spacing can't be recovered from it). The workspace tile
    falls back to deriving a title from the filename when this is "".
    """
    value = _extract_stargazer_table(source).get("name", "")
    return value if isinstance(value, str) else ""


def memory_to_gib(memory: str) -> int:
    """Read a `NotebookResources.memory` quantity back to a GiB integer.

    The settings modal and create form both speak whole GiB, while the header
    stores a Kubernetes quantity (`"4Gi"`). Recovers the integer for the form:
    `"4Gi"` → 4, a bare digit string → itself, anything else → the default
    (derived from `DEFAULT_RESOURCES` so the two can't silently diverge).
    """
    text = memory.strip()
    if text.endswith("Gi") and text[:-2].isdigit():
        return int(text[:-2])
    if text.isdigit():
        return int(text)
    return int(DEFAULT_RESOURCES.memory.removesuffix("Gi"))


def parse_notebook_resources(source: str) -> NotebookResources:
    """Parse `[tool.stargazer]` resources from notebook source.

    Reads `cpu` and `memory` from the PEP 723 header's `[tool.stargazer]`
    table (no ceiling — you rightsize per-notebook), filling any missing field
    from `DEFAULT_RESOURCES`. `memory` is normalized via `_coerce_memory`, so a
    bare integer is read as GiB and a suffixed quantity passes through. Always
    returns a value — never raises.
    """
    table = _extract_stargazer_table(source)
    cpu = _coerce_cpu(table["cpu"]) if "cpu" in table else DEFAULT_RESOURCES.cpu
    memory = (
        _coerce_memory(table["memory"])
        if "memory" in table
        else DEFAULT_RESOURCES.memory
    )
    return NotebookResources(cpu=cpu, memory=memory)


def resources_from_inputs(cpu: object, memory: object) -> NotebookResources:
    """Build resources from separate cpu/memory inputs (e.g. a create form).

    Values are honored as-authored — no ceiling. `cpu` is coerced to an int
    (falling back to the default if it isn't one). `memory` is a GiB count: a
    bare integer becomes `"<n>Gi"`, an already-suffixed quantity passes
    through, and empty falls back to the default (see `_coerce_memory`).
    """
    return NotebookResources(
        cpu=_coerce_cpu(cpu),
        memory=_coerce_memory(memory),
    )


def _toml_str(value: str) -> str:
    """Encode `value` as a single-line TOML basic string (quoted, escaped).

    The description is free user text written into the commented header, so it
    must round-trip through `tomllib`. Newlines/runs of whitespace collapse to a
    single space, the result is length-capped, and `\\`/`"` are escaped.
    """
    text = " ".join(value.split())[:200]
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _stargazer_table_lines(
    resources: NotebookResources, description: str | None, name: str | None = None
) -> list[str]:
    """Render the commented `[tool.stargazer]` TOML lines for a header."""
    lines = ["#", "# [tool.stargazer]"]
    if name:
        lines.append(f"# name = {_toml_str(name)}")
    lines += [
        f"# cpu = {resources.cpu}",
        f'# memory = "{resources.memory}"',
    ]
    if description:
        lines.append(f"# description = {_toml_str(description)}")
    return lines


def _strip_stargazer_table(body_lines: list[str]) -> list[str]:
    """Drop an existing commented `[tool.stargazer]` table from header lines.

    Skips from the `# [tool.stargazer]` line up to the next `# [..]` table
    header (or end of block), so re-injecting can't duplicate the table.
    """
    out: list[str] = []
    skipping = False
    for line in body_lines:
        inner = line.lstrip("#").strip()
        if skipping:
            if inner.startswith("[") and inner != "[tool.stargazer]":
                skipping = False
                out.append(line)
            continue
        if inner == "[tool.stargazer]":
            skipping = True
            continue
        out.append(line)
    return out


def with_stargazer_resources(
    source: str,
    resources: NotebookResources,
    description: str | None = None,
    name: str | None = None,
) -> str:
    """Return `source` with its `[tool.stargazer]` block set to `resources`.

    Rewrites the whole table, so `description` (the workspace tile blurb) and
    `name` (the display title) are written when truthy and dropped when
    None/empty — callers that edit settings pass the current values so they're
    preserved. Replaces an existing table or appends one before the header's
    closing `# ///`. Returns `source` unchanged if there's no PEP 723 script
    block to inject into.
    """
    match = _PEP723_BLOCK.search(source)
    if match is None or match.group("type") != "script":
        return source
    lines = match.group(0).splitlines()
    open_line, close_line, body = lines[0], lines[-1], lines[1:-1]
    body = _strip_stargazer_table(body)
    # Trim a trailing blank comment so we don't accumulate blank `#` lines.
    while body and body[-1].lstrip("#").strip() == "":
        body.pop()
    new_block = "\n".join(
        [
            open_line,
            *body,
            *_stargazer_table_lines(resources, description, name),
            close_line,
        ]
    )
    return source[: match.start()] + new_block + source[match.end() :]
