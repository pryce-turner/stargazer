"""Tests for parsing a notebook's `[tool.stargazer]` resource block.

These define the contract for `app.notebook_meta`: statically read the PEP
723 script header from notebook source, pull `[tool.stargazer]` cpu/memory
verbatim (no ceiling — you rightsize per-notebook), and fall back to defaults
whenever the block is missing or malformed. No notebook code is executed —
purely a text parse.
"""

from app.notebook_meta import (
    DEFAULT_RESOURCES,
    NotebookResources,
    memory_to_gib,
    parse_notebook_description,
    parse_notebook_resources,
    resources_from_inputs,
    with_stargazer_resources,
)


def _nb(header: str = "", body: str = "import marimo\n") -> str:
    """Wrap a PEP 723 `header` (TOML lines) into a script-metadata block."""
    if not header:
        return body
    commented = "\n".join(f"# {line}" if line else "#" for line in header.splitlines())
    return f"# /// script\n{commented}\n# ///\n{body}"


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------


def test_no_script_header_returns_default():
    """Source without any PEP 723 block yields the default resources."""
    assert parse_notebook_resources("import marimo\n") == DEFAULT_RESOURCES


def test_header_without_stargazer_table_returns_default():
    """A PEP 723 block lacking [tool.stargazer] yields the default."""
    src = _nb('dependencies = ["marimo", "stargazer"]')
    assert parse_notebook_resources(src) == DEFAULT_RESOURCES


def test_malformed_toml_returns_default():
    """A broken header never raises — it falls back to the default."""
    src = _nb("[tool.stargazer\ncpu = = 2")
    assert parse_notebook_resources(src) == DEFAULT_RESOURCES


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_reads_cpu_and_memory():
    """cpu + memory from [tool.stargazer] are parsed verbatim."""
    src = _nb('dependencies = ["marimo"]\n\n[tool.stargazer]\ncpu = 2\nmemory = "4Gi"')
    res = parse_notebook_resources(src)
    assert res == NotebookResources(cpu=2, memory="4Gi")


def test_partial_block_fills_only_given_field():
    """Only `memory` given → cpu falls back to the default cpu."""
    src = _nb('[tool.stargazer]\nmemory = "3Gi"')
    res = parse_notebook_resources(src)
    assert res.memory == "3Gi"
    assert res.cpu == DEFAULT_RESOURCES.cpu


# ---------------------------------------------------------------------------
# No ceiling — values pass through as authored
# ---------------------------------------------------------------------------


def test_large_values_pass_through_unclamped():
    """cpu/memory beyond the old devbox limits are honored verbatim."""
    src = _nb('[tool.stargazer]\ncpu = 64\nmemory = "64Gi"')
    assert parse_notebook_resources(src) == NotebookResources(cpu=64, memory="64Gi")


def test_memory_units_pass_through():
    """Any Kubernetes-style memory quantity is taken as-is."""
    assert (
        parse_notebook_resources(_nb('[tool.stargazer]\nmemory = "512Mi"')).memory
        == "512Mi"
    )
    assert (
        parse_notebook_resources(_nb('[tool.stargazer]\nmemory = "2Gi"')).memory
        == "2Gi"
    )


# ---------------------------------------------------------------------------
# resources_from_inputs — create-form inputs, honored as-authored
# ---------------------------------------------------------------------------


def test_resources_from_inputs_passes_values_through():
    """Inputs are honored as-authored, with no clamping."""
    assert resources_from_inputs(8, "16Gi") == NotebookResources(cpu=8, memory="16Gi")


def test_resources_from_inputs_coerces_bad_cpu_to_default():
    """A non-integer cpu falls back to the default cpu; memory passes through."""
    res = resources_from_inputs("abc", "3Gi")
    assert res.cpu == DEFAULT_RESOURCES.cpu
    assert res.memory == "3Gi"


def test_resources_from_inputs_empty_memory_uses_default():
    """Empty memory falls back to the default memory."""
    assert resources_from_inputs(2, "").memory == DEFAULT_RESOURCES.memory


def test_resources_from_inputs_bare_int_memory_is_gib():
    """The create form's GiB number input (a bare int) becomes a Gi quantity."""
    assert resources_from_inputs("4", "8") == NotebookResources(cpu=4, memory="8Gi")


def test_parse_bare_int_memory_read_as_gib():
    """A header authoring `memory = 16` (bare int) is read as 16 GiB."""
    assert parse_notebook_resources(
        _nb("[tool.stargazer]\ncpu = 2\nmemory = 16")
    ) == NotebookResources(cpu=2, memory="16Gi")


# ---------------------------------------------------------------------------
# with_stargazer_resources — inject/replace the block in existing source
# ---------------------------------------------------------------------------


def test_inject_resources_into_header_without_block():
    """A header lacking [tool.stargazer] gains one that round-trips."""
    src = _nb('dependencies = ["marimo", "stargazer"]')
    out = with_stargazer_resources(src, NotebookResources(cpu=3, memory="2Gi"))
    assert parse_notebook_resources(out) == NotebookResources(cpu=3, memory="2Gi")
    # Existing header content is preserved.
    assert "stargazer" in out


def test_inject_resources_replaces_existing_block():
    """An existing [tool.stargazer] block is overwritten, not duplicated."""
    src = _nb('[tool.stargazer]\ncpu = 1\nmemory = "1Gi"')
    out = with_stargazer_resources(src, NotebookResources(cpu=4, memory="5Gi"))
    assert parse_notebook_resources(out) == NotebookResources(cpu=4, memory="5Gi")
    assert out.count("[tool.stargazer]") == 1


# ---------------------------------------------------------------------------
# description — written into the same table, edited via the settings modal
# ---------------------------------------------------------------------------


def test_description_absent_parses_empty():
    """No description in the header yields the empty string, not None."""
    assert parse_notebook_description(_nb("cpu = 2", body="x\n")) == ""
    assert parse_notebook_description("import marimo\n") == ""


def test_description_round_trips_with_resources():
    """Writing resources + description round-trips both back out."""
    src = _nb('dependencies = ["marimo", "stargazer"]')
    out = with_stargazer_resources(
        src, NotebookResources(cpu=2, memory="4Gi"), description="My analysis"
    )
    assert parse_notebook_resources(out) == NotebookResources(cpu=2, memory="4Gi")
    assert parse_notebook_description(out) == "My analysis"


def test_description_escapes_quotes_and_collapses_whitespace():
    """Quotes/backslashes survive the TOML round-trip; newlines collapse."""
    src = _nb('dependencies = ["marimo"]')
    out = with_stargazer_resources(
        src,
        NotebookResources(cpu=1, memory="2Gi"),
        description='Calls "X" \\ over\nmulti   lines',
    )
    assert parse_notebook_description(out) == 'Calls "X" \\ over multi lines'


def test_empty_description_writes_no_line():
    """A falsy description leaves the table description-free (create path)."""
    src = _nb('dependencies = ["marimo"]')
    out = with_stargazer_resources(src, NotebookResources(cpu=1, memory="2Gi"))
    assert "description" not in out
    assert parse_notebook_description(out) == ""


def test_memory_to_gib_reads_quantities_back():
    """GiB quantities and bare ints recover; other units fall back to 2."""
    assert memory_to_gib("4Gi") == 4
    assert memory_to_gib("16") == 16
    assert memory_to_gib("512Mi") == 2
    assert memory_to_gib(DEFAULT_RESOURCES.memory) == 2
