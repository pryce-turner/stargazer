# Typed Intermediates Refactor

## Context

Three clusters of GATK tasks still use raw `Path`, bare `ComponentFile`, or unregistered
`type=` keyvalues instead of proper typed subclasses. This plan replaces all three with
concrete `ComponentFile` subclasses that live in the registry and follow the same patterns
as `AlignmentFile`, `VariantsFile`, and `KnownSites`.

### What was already done (do not redo)

- `KnownSites` was added to `types/variants.py` and exported from `types/__init__.py`.
- `VQSRResource` dataclass was deleted; `variant_recalibrator` now accepts `resources: list[Variants]`.
- Workflows updated to import `KnownSites` directly.

### Three remaining problems

1. **BQSR Report** — `base_recalibrator` returns `ComponentFile` with ad-hoc
   `keyvalues={"type": "bqsr_report", ...}`, unregistered in the component registry.
   `apply_bqsr` and `analyze_covariates` consume raw `ComponentFile`/`Path`.

2. **VQSR Recalibration Model** — `variant_recalibrator` returns `tuple[Path, Path]`.
   `apply_vqsr` takes `recal_file: Path, tranches_file: Path`. No typed container exists.

3. **Duplicate Metrics side-effect** — `mark_duplicates` uploads a bare `ComponentFile`
   with `type="duplicate_metrics"` that is not returned, not consumed, and not registered.

---

## Design Decisions

### Decision 1: `BQSRReport` in `types/reports.py`

`BQSRReport` is not a biological entity — it is a GATK intermediate artifact. It has no
index file and does not compose with other BioTypes. A plain `ComponentFile` subclass
(no `BioType` wrapper) is sufficient, matching the pattern of `KnownSites`.

`_type_key = "bqsr_report"`, `_component_key = "report"`.

`analyze_covariates` currently returns `-> Path` (the PDF). That return value stays as
`Path` — it is a QC output consumed by humans, not downstream tasks. The inputs change
from `Path` to `BQSRReport` so callers pass typed objects instead of raw paths.

### Decision 2: `RecalibrationModel` BioType wrapping two ComponentFiles

Two `ComponentFile` subclasses in a new `types/vqsr.py` module, composed into a
`RecalibrationModel` BioType.

**Why a BioType wrapper?** The recal file and tranches file are always produced together
and always consumed together by `apply_vqsr`. A BioType allows `marshal_input` /
`marshal_output` to round-trip the pair as one serializable unit through Flyte. Returning
`tuple[Path, Path]` is opaque to the registry and MCP transport; a typed
`RecalibrationModel` is introspectable, serializable, and passable as a single typed input.

`RecalFile`: `_type_key = "vqsr"`, `_component_key = "recal"`, `_field_defaults = {"sample_id": "", "mode": ""}`
`TranchesFile`: `_type_key = "vqsr"`, `_component_key = "tranches"`, `_field_defaults = {"sample_id": "", "mode": ""}`

`RecalibrationModel(BioType)`: `sample_id: str`, `mode: str`, `recal: RecalFile | None = None`, `tranches: TranchesFile | None = None`

### Decision 3: `DuplicateMetrics` in `types/reports.py`

`DuplicateMetrics` is uploaded as a side-effect in `mark_duplicates` but never returned
and never consumed as a typed input downstream. A typed subclass makes it registered and
discoverable. The return signature (`-> Alignment`) is unchanged.

`_type_key = "duplicate_metrics"`, `_component_key = "metrics"`, `_field_defaults = {"sample_id": ""}`.

### Decision 4: `apply_vqsr` drops its `mode` parameter

`mode` is now read from `model.mode` (set at creation time by `variant_recalibrator`).
Removing it from `apply_vqsr` eliminates a class of caller mismatch bugs where the mode
passed to `apply_vqsr` could differ from the mode used to build the model.

---

## New Files

### `src/stargazer/assets/reports.py`

```python
"""
Report and metrics types for Stargazer.

Defines ComponentFile subclasses for GATK intermediate report files.
"""

from dataclasses import dataclass
from typing import ClassVar

from stargazer.assets.component import ComponentFile


@dataclass
class BQSRReport(ComponentFile):
    """BQSR recalibration table produced by GATK BaseRecalibrator."""

    _type_key: ClassVar[str] = "bqsr_report"
    _component_key: ClassVar[str] = "report"
    _field_defaults = {"sample_id": ""}


@dataclass
class DuplicateMetrics(ComponentFile):
    """Duplicate metrics text file produced by GATK MarkDuplicates."""

    _type_key: ClassVar[str] = "duplicate_metrics"
    _component_key: ClassVar[str] = "metrics"
    _field_defaults = {"sample_id": ""}
```

### `src/stargazer/assets/vqsr.py`

Check current contents first — it may already define some of these classes. Adopt what is
there rather than overwriting. If creating from scratch:

```python
"""
VQSR recalibration model types for Stargazer.

Defines ComponentFile subclasses and the RecalibrationModel BioType container
for GATK VariantRecalibrator outputs.
"""

from dataclasses import dataclass
from typing import ClassVar

from stargazer.assets.component import ComponentFile
from stargazer.assets.biotype import BioType


@dataclass
class RecalFile(ComponentFile):
    """VQSR recalibration file (.recal) from GATK VariantRecalibrator."""

    _type_key: ClassVar[str] = "vqsr"
    _component_key: ClassVar[str] = "recal"
    _field_defaults = {"sample_id": "", "mode": ""}


@dataclass
class TranchesFile(ComponentFile):
    """VQSR tranches file (.tranches) from GATK VariantRecalibrator."""

    _type_key: ClassVar[str] = "vqsr"
    _component_key: ClassVar[str] = "tranches"
    _field_defaults = {"sample_id": "", "mode": ""}


@dataclass
class RecalibrationModel(BioType):
    """
    VQSR recalibration model produced by VariantRecalibrator.

    Always produced and consumed as a pair: the recal file (scored variants)
    and the tranches file (sensitivity thresholds). Mode is stored on the model
    so apply_vqsr does not need a separate mode parameter.

    Attributes:
        sample_id: Sample or cohort identifier
        mode: Recalibration mode — "SNP", "INDEL", or "BOTH"
        recal: Recalibration file (.recal)
        tranches: Tranches file (.tranches)
    """

    sample_id: str
    mode: str
    recal: RecalFile | None = None
    tranches: TranchesFile | None = None
```

---

## `types/__init__.py` changes

Add imports after the variants import:
```python
from stargazer.assets.reports import BQSRReport, DuplicateMetrics
from stargazer.assets.vqsr import RecalibrationModel, RecalFile, TranchesFile
```

Add to `__all__`:
```python
# Reports / intermediates
("BQSRReport",)
("DuplicateMetrics",)
# VQSR model
("RecalibrationModel",)
("RecalFile",)
("TranchesFile",)
```

---

## Task Signature Changes

### `base_recalibrator` (`tasks/gatk/base_recalibrator.py`)

Before:
```python
from stargazer.assets.component import ComponentFile
async def base_recalibrator(...) -> ComponentFile:
    recal_comp = ComponentFile(
        path=output_recal,
        keyvalues={"type": "bqsr_report", "sample_id": ..., "tool": "..."},
    )
    await _storage.default_client.upload(recal_comp)
    return recal_comp
```

After:
```python
from stargazer.assets.reports import BQSRReport
async def base_recalibrator(...) -> BQSRReport:
    recal_comp = BQSRReport()
    await recal_comp.update(output_recal, sample_id=alignment.sample_id, tool="gatk_base_recalibrator")
    return recal_comp
```

Remove `from stargazer.assets.component import ComponentFile` if it becomes unused.

### `apply_bqsr` (`tasks/gatk/apply_bqsr.py`)

Before:
```python
from stargazer.assets.component import ComponentFile


async def apply_bqsr(alignment, ref, recal_report: ComponentFile) -> Alignment:
    await _storage.default_client.download(recal_report)
```

After:
```python
from stargazer.assets.reports import BQSRReport


async def apply_bqsr(alignment, ref, recal_report: BQSRReport) -> Alignment:
    await _storage.default_client.download(recal_report)
```

No logic changes — `recal_report.path` works identically on `BQSRReport`.

### `analyze_covariates` (`tasks/gatk/analyze_covariates.py`)

Before:
```python
from pathlib import Path


async def analyze_covariates(
    before_report: Path, after_report: Path | None = None
) -> Path:
    if not before_report.exists():
        ...
    output_dir = before_report.parent
    plots_file = output_dir / f"{before_report.stem}_plots.pdf"
    cmd = ["gatk", "AnalyzeCovariates", "-before", str(before_report), ...]
```

After:
```python
import stargazer.utils.storage as _storage
from stargazer.assets.reports import BQSRReport


async def analyze_covariates(
    before_report: BQSRReport,
    after_report: BQSRReport | None = None,
) -> Path:
    await _storage.default_client.download(before_report)
    if after_report is not None:
        await _storage.default_client.download(after_report)

    if not before_report.path or not before_report.path.exists():
        raise FileNotFoundError(f"Before report not found: {before_report.path}")
    if after_report is not None and (
        not after_report.path or not after_report.path.exists()
    ):
        raise FileNotFoundError(f"After report not found: {after_report.path}")

    output_dir = before_report.path.parent
    plots_file = output_dir / f"{before_report.path.stem}_plots.pdf"
    cmd = [
        "gatk",
        "AnalyzeCovariates",
        "-before",
        str(before_report.path),
        "-plots",
        str(plots_file),
    ]
    if after_report is not None:
        cmd.extend(["-after", str(after_report.path)])
    ...
```

`-> Path` return is kept — the PDF is a human QC artifact with no downstream consumers.

### `variant_recalibrator` (`tasks/gatk/variant_recalibrator.py`)

Before:
```python
from pathlib import Path
async def variant_recalibrator(...) -> tuple[Path, Path]:
    ...
    return recal_file, tranches_file
```

After:
```python
from stargazer.assets.vqsr import RecalibrationModel, RecalFile, TranchesFile
async def variant_recalibrator(...) -> RecalibrationModel:
    ...
    recal = RecalFile()
    await recal.update(recal_file, sample_id=vcf.sample_id, mode=mode.lower())
    tranches = TranchesFile()
    await tranches.update(tranches_file, sample_id=vcf.sample_id, mode=mode.lower())
    return RecalibrationModel(
        sample_id=vcf.sample_id,
        mode=mode.lower(),
        recal=recal,
        tranches=tranches,
    )
```

Remove `from pathlib import Path` — output paths are local temporaries, no longer returned.

### `apply_vqsr` (`tasks/gatk/apply_vqsr.py`)

Before:
```python
from pathlib import Path


async def apply_vqsr(
    vcf: Variants,
    recal_file: Path,
    tranches_file: Path,
    ref: Reference | None = None,
    mode: str = "SNP",
    truth_sensitivity_filter_level: float = 99.0,
) -> Variants:
    if not recal_file.exists():
        ...
    if not tranches_file.exists():
        ...
    ...
    cmd = [
        ...,
        "--recal-file",
        str(recal_file),
        "--tranches-file",
        str(tranches_file),
        "--mode",
        mode,
        ...,
    ]
```

After:
```python
from stargazer.assets.vqsr import RecalibrationModel


async def apply_vqsr(
    vcf: Variants,
    model: RecalibrationModel,
    ref: Reference | None = None,
    truth_sensitivity_filter_level: float = 99.0,
) -> Variants:
    await model.fetch()
    if not model.recal or not model.recal.path:
        raise ValueError("RecalibrationModel has no recal file")
    if not model.tranches or not model.tranches.path:
        raise ValueError("RecalibrationModel has no tranches file")
    mode = model.mode.upper()
    ...
    cmd = [
        ...,
        "--recal-file",
        str(model.recal.path),
        "--tranches-file",
        str(model.tranches.path),
        "--mode",
        mode,
        ...,
    ]
```

`mode: str` parameter is removed — read from `model.mode` instead.

### `mark_duplicates` (`tasks/gatk/mark_duplicates.py`)

Before:
```python
from stargazer.assets.component import ComponentFile

if metrics_file.exists():
    metrics_comp = ComponentFile(
        path=metrics_file,
        keyvalues={"type": "duplicate_metrics", "sample_id": ..., "tool": "..."},
    )
    await _storage.default_client.upload(metrics_comp)
```

After:
```python
from stargazer.assets.reports import DuplicateMetrics

if metrics_file.exists():
    metrics_comp = DuplicateMetrics()
    await metrics_comp.update(
        metrics_file, sample_id=alignment.sample_id, tool="gatk_mark_duplicates"
    )
```

Return signature (`-> Alignment`) is unchanged.

---

## Workflow Changes

### `germline_short_variant_discovery.py`

The VQSR section currently unpacks a tuple:
```python
snp_recal, snp_tranches = await variant_recalibrator(...)
snp_filtered_vcf = await apply_vqsr(vcf=..., recal_file=snp_recal, tranches_file=snp_tranches, mode="SNP", ...)
indel_recal, indel_tranches = await variant_recalibrator(...)
final_filtered_vcf = await apply_vqsr(vcf=..., recal_file=indel_recal, tranches_file=indel_tranches, mode="INDEL", ...)
```

After:
```python
snp_model = await variant_recalibrator(...)
snp_filtered_vcf = await apply_vqsr(vcf=..., model=snp_model, ...)
indel_model = await variant_recalibrator(...)
final_filtered_vcf = await apply_vqsr(vcf=..., model=indel_model, ...)
```

Add `RecalibrationModel` to the type imports. Remove `Path` from imports if unused.

---

## `build_local_db.py` Changes

The `bqsr_report` entry is missing `"component"`. Add it:

```python
"NA12829_TP53_bqsr.table": {
    "type": "bqsr_report",
    "component": "report",
    "sample_id": "NA12829",
},
```

The `duplicate_metrics` entry is also missing `"component"`. Add it:

```python
"NA12829_TP53_markdup_metrics.txt": {
    "type": "duplicate_metrics",
    "component": "metrics",
    "sample_id": "NA12829",
    "stage": "markdup",
},
```

After both changes, regenerate the fixture DB:
```bash
uv run python tests/fixtures/build_local_db.py
```

No fixture files exist for VQSR outputs (`.recal`, `.tranches`) — these are live pipeline
intermediates. No new fixture entries needed.

---

## Test Changes

### Phase 1: `tests/unit/test_reports.py` (new)

Write before implementing. Verify:
- `BQSRReport()` sets `keyvalues["type"] == "bqsr_report"` and `keyvalues["component"] == "report"`
- `DuplicateMetrics()` sets `keyvalues["type"] == "duplicate_metrics"` and `keyvalues["component"] == "metrics"`
- Both appear in `COMPONENT_REGISTRY` under their `(type_key, component_key)` tuples
- `sample_id` reads/writes through `keyvalues`
- `to_dict()` / `from_dict()` round-trip

### Phase 1: `tests/unit/test_vqsr_types.py` (new)

Write before implementing. Verify:
- `RecalFile()` / `TranchesFile()` set correct type and component keyvalues
- `RecalibrationModel.to_dict()` / `from_dict()` round-trip preserves `mode`, `sample_id`, and component dicts
- Both component types appear in `COMPONENT_REGISTRY`

### Phase 2: update `tests/tasks/gatk/test_base_recalibrator.py`

Change the assertion:
```python
# Before
from stargazer.assets.component import ComponentFile

assert isinstance(recal_report, ComponentFile)
assert recal_report.keyvalues.get("type") == "bqsr_report"

# After
from stargazer.assets.reports import BQSRReport

assert isinstance(recal_report, BQSRReport)
assert recal_report.keyvalues.get("type") == "bqsr_report"
assert recal_report.keyvalues.get("component") == "report"
```

### Phase 2: update `tests/tasks/gatk/test_apply_bqsr.py`

Change the `recal_file` fixture construction:
```python
# Before
from stargazer.assets.component import ComponentFile
recal_file = ComponentFile(path=FIXTURES_DIR / "NA12829_TP53_bqsr.table", keyvalues={"type": "bqsr_report", ...})

# After
from stargazer.assets.reports import BQSRReport
recal_file = BQSRReport(path=FIXTURES_DIR / "NA12829_TP53_bqsr.table", keyvalues={"sample_id": ..., "tool": "..."})
```

### Phase 4: `tests/tasks/gatk/test_variant_recalibrator.py` (new)

Minimal set:
- `test_variant_recalibrator_rejects_gvcf` — `ValueError` when input is a GVCF
- `test_variant_recalibrator_rejects_empty_resources` — `ValueError` on empty resources
- `test_variant_recalibrator_task_is_callable` — `callable(variant_recalibrator)`
- `test_variant_recalibrator_creates_model(fixtures_db)` — integration with `gatk` skip
  guard; asserts `isinstance(result, RecalibrationModel)` with `recal` and `tranches` set

### Phase 4: `tests/tasks/gatk/test_apply_vqsr.py` (new)

Minimal set:
- `test_apply_vqsr_rejects_gvcf` — `ValueError`
- `test_apply_vqsr_task_is_callable`
- `test_apply_vqsr_filters_vcf(fixtures_db)` — integration with `gatk` skip guard; note
  that the TP53 fixture VCF likely won't satisfy VQSR's minimum variant count — mark this
  test with `pytest.mark.skip` or add an explicit `pytest.skip` if GATK exits non-zero

---

## Implementation Phases

Each phase must leave all existing tests passing before the next phase starts.

### Phase 1 — New type modules (no task changes yet)
- [x] Check existing `src/stargazer/assets/vqsr.py` — adopt or replace
- [x] Create `src/stargazer/assets/reports.py` with `BQSRReport`, `DuplicateMetrics`
  - NOTE: `BQSRReport._type_key="alignment"`, `_component_key="bqsr_report"` (not standalone)
  - NOTE: `DuplicateMetrics._type_key="alignment"`, `_component_key="markdup_metrics"`
  - NOTE: `Alignment` BioType gains `bqsr_report: BQSRReport | None = None` field
- [x] Write `tests/unit/test_reports.py` — run and pass
- [ ] Write `tests/unit/test_vqsr_types.py` — run and pass
- [x] Update `src/stargazer/assets/__init__.py` — add imports and `__all__` entries
- [x] `uv run pytest tests/unit/ -v` — all pass

### Phase 2 — BQSR task updates
- [x] Update `base_recalibrator` — return `BQSRReport`
- [x] Update `apply_bqsr` — accept `BQSRReport`
- [x] Update `analyze_covariates` — accept `BQSRReport`; download before use
- [x] Update `test_base_recalibrator.py` — assert `BQSRReport` instance and component key
- [x] Update `test_apply_bqsr.py` — construct `BQSRReport` in test setup
- [x] Update `build_local_db.py` — set `"type": "alignment"`, `"component": "bqsr_report"` on bqsr entry
- [x] `uv run python tests/fixtures/build_local_db.py`
- [x] `uv run pytest tests/tasks/gatk/test_base_recalibrator.py tests/tasks/gatk/test_apply_bqsr.py -v`

### Phase 3 — DuplicateMetrics side-effect
- [x] Update `mark_duplicates` — use `DuplicateMetrics()` instead of bare `ComponentFile`
- [x] `build_local_db.py` — `markdup_metrics` entry already had correct `"type": "alignment"`, `"component": "markdup_metrics"`
- [x] `uv run pytest tests/tasks/gatk/test_mark_duplicates.py -v`

### Phase 4 — VQSR task updates
- [ ] Write `tests/tasks/gatk/test_variant_recalibrator.py` (tests first)
- [ ] Write `tests/tasks/gatk/test_apply_vqsr.py` (tests first)
- [ ] Update `variant_recalibrator` — return `RecalibrationModel`
- [ ] Update `apply_vqsr` — accept `model: RecalibrationModel`, drop `mode` param
- [ ] Update `germline_short_variant_discovery.py` — replace tuple unpacking with model
- [ ] `uv run pytest tests/tasks/gatk/test_variant_recalibrator.py tests/tasks/gatk/test_apply_vqsr.py -v`

### Phase 5 — Full suite + spec update
- [ ] `uv run pytest tests/ -v`
- [ ] Update `specs/filesystem.md` — add `reports.py` and `vqsr.py` type tables

---

## Before/After Signature Summary

| Task | Before | After |
|------|--------|-------|
| `base_recalibrator` | `-> ComponentFile` | `-> BQSRReport` |
| `apply_bqsr` | `recal_report: ComponentFile` | `recal_report: BQSRReport` |
| `analyze_covariates` | `before_report: Path, after_report: Path \| None` | `before_report: BQSRReport, after_report: BQSRReport \| None` |
| `variant_recalibrator` | `-> tuple[Path, Path]` | `-> RecalibrationModel` |
| `apply_vqsr` | `recal_file: Path, tranches_file: Path, mode: str` | `model: RecalibrationModel` (mode dropped) |
| `mark_duplicates` | bare `ComponentFile` side-effect | `DuplicateMetrics` side-effect (return unchanged) |

---

## Files Modified

| File | Action |
|------|--------|
| `src/stargazer/assets/reports.py` | **New** — `BQSRReport`, `DuplicateMetrics` |
| `src/stargazer/assets/vqsr.py` | **New or adopt** — `RecalFile`, `TranchesFile`, `RecalibrationModel` |
| `src/stargazer/assets/__init__.py` | Add imports + `__all__` entries for all new types |
| `src/stargazer/tasks/gatk/base_recalibrator.py` | Return `BQSRReport` |
| `src/stargazer/tasks/gatk/apply_bqsr.py` | Accept `BQSRReport` |
| `src/stargazer/tasks/gatk/analyze_covariates.py` | Accept `BQSRReport`; download before use |
| `src/stargazer/tasks/gatk/variant_recalibrator.py` | Return `RecalibrationModel` |
| `src/stargazer/tasks/gatk/apply_vqsr.py` | Accept `model: RecalibrationModel`; drop `mode` param |
| `src/stargazer/tasks/gatk/mark_duplicates.py` | Use `DuplicateMetrics` for side-effect upload |
| `src/stargazer/workflows/germline_short_variant_discovery.py` | Replace tuple unpacking with model |
| `tests/unit/test_reports.py` | **New** |
| `tests/unit/test_vqsr_types.py` | **New** |
| `tests/tasks/gatk/test_base_recalibrator.py` | Assert `BQSRReport` instance and component key |
| `tests/tasks/gatk/test_apply_bqsr.py` | Construct `BQSRReport` in test setup |
| `tests/tasks/gatk/test_variant_recalibrator.py` | **New** |
| `tests/tasks/gatk/test_apply_vqsr.py` | **New** |
| `tests/fixtures/build_local_db.py` | Add `component` key to bqsr_report and duplicate_metrics entries |
| `tests/fixtures/stargazer_local.json` | Regenerated by `build_local_db.py` |
| `.opencode/specs/filesystem.md` | Add `reports.py` and `vqsr.py` type tables |
