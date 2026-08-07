# Typed File Components: Central Schema for Stargazer Types

## Context

Metadata for stored files is defined implicitly in 4+ places: `update_*()` method bodies, `@property` accessors, fixture data, and inline keyvalue dicts in tasks. There is no central definition of what metadata a given file component carries. This makes drift easy and validation impossible.

We're replacing this with typed `ComponentFile` subclasses. Metadata is defined once as typed properties backed by the component's `keyvalues` dict. `StorageClient` works exclusively with `ComponentFile`. `IpFile` is deleted.

## Phase 1: ComponentFile Base Class

**New file: `src/stargazer/assets/base.py`**

```python
@dataclass
class ComponentFile:
    cid: str = ""
    path: Path | None = None
    keyvalues: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "cid": self.cid,
            "path": str(self.path) if self.path else None,
            "keyvalues": self.keyvalues,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls(
            cid=data.get("cid", ""),
            path=Path(data["path"]) if data.get("path") else None,
            keyvalues=data.get("keyvalues", {}),
        )
```

**StorageClient protocol changes (in `utils/storage.py`):**
- `upload(component: ComponentFile) -> None` — reads `component.path` and `component.keyvalues`, uploads, sets `component.cid`
- `download(component: ComponentFile, dest: Path | None = None) -> None` — downloads, sets `component.path`
- `query(keyvalues: dict) -> list[ComponentFile]` — returns base `ComponentFile` instances
- `delete(component: ComponentFile) -> None`

**`PinataClient` + `LocalStorageClient` changes:**
- Replace `IpFile` with `ComponentFile` throughout
- `upload` mutates the component's `cid` in-place rather than returning a new object
- `download` mutates `component.path` in-place (same as current `local_path`)
- `query` returns `list[ComponentFile]` with `cid` and `keyvalues` populated

Delete `utils/ipfile.py`.

**Tests first: `tests/unit/test_component_file.py`**
- `to_dict()`/`from_dict()` round-trip with and without `path`
- Base `ComponentFile` can be used for untyped raw files

## Phase 2: Derived Component Definitions

Each derived class:
- Sets `type` and `component` in keyvalues via `__post_init__` (using `setdefault` so existing keyvalues survive `from_dict`)
- Exposes metadata as typed `@property` getters/setters that read/write `self.keyvalues`
- Defines `update()` with explicit typed keyword-only arguments — enforces valid metadata at the call site

```python
# Example
@dataclass
class AlignmentFile(ComponentFile):
    def __post_init__(self):
        self.keyvalues.setdefault("type", "alignment")
        self.keyvalues.setdefault("component", "alignment")

    @property
    def sample_id(self) -> str:
        return self.keyvalues.get("sample_id", "")

    @sample_id.setter
    def sample_id(self, value: str) -> None:
        self.keyvalues["sample_id"] = value

    @property
    def duplicates_marked(self) -> bool:
        return self.keyvalues.get("duplicates_marked") == "true"

    @duplicates_marked.setter
    def duplicates_marked(self, value: bool) -> None:
        self.keyvalues["duplicates_marked"] = "true" if value else "false"

    async def update(
        self,
        path: Path,
        *,
        sample_id: str | None = None,
        format: str | None = None,
        sorted: str | None = None,
        duplicates_marked: bool | None = None,
        bqsr_applied: bool | None = None,
        tool: str | None = None,
    ) -> None:
        if sample_id is not None:
            self.keyvalues["sample_id"] = sample_id
        if format is not None:
            self.keyvalues["format"] = format
        if sorted is not None:
            self.keyvalues["sorted"] = sorted
        if duplicates_marked is not None:
            self.keyvalues["duplicates_marked"] = (
                "true" if duplicates_marked else "false"
            )
        if bqsr_applied is not None:
            self.keyvalues["bqsr_applied"] = "true" if bqsr_applied else "false"
        if tool is not None:
            self.keyvalues["tool"] = tool
        self.path = path
        await default_client.upload(self)
```

`from_dict` is inherited from base — `keyvalues` is restored, properties work immediately.

### `src/stargazer/assets/reference.py`

| Class | type | component | Properties |
|-------|------|-----------|------------|
| `ReferenceFile` | reference | fasta | `build: str` |
| `ReferenceIndex` | reference | faidx | `build: str`, `tool: str \| None` |
| `SequenceDict` | reference | sequence_dictionary | `build: str`, `tool: str \| None` |
| `AlignerIndex` | reference | aligner_index | `build: str`, `aligner: str` |

### `src/stargazer/assets/alignment.py`

| Class | type | component | Properties |
|-------|------|-----------|------------|
| `AlignmentFile` | alignment | alignment | `sample_id: str`, `format: str \| None`, `sorted: str \| None`, `duplicates_marked: bool`, `bqsr_applied: bool`, `tool: str \| None` |
| `AlignmentIndex` | alignment | index | `sample_id: str` |

### `src/stargazer/assets/reads.py`

| Class | type | component | Properties |
|-------|------|-----------|------------|
| `R1File` | reads | r1 | `sample_id: str`, `sequencing_platform: str \| None` |
| `R2File` | reads | r2 | `sample_id: str`, `sequencing_platform: str \| None` |

Two classes (not one with variable component) so `__post_init__` can hardcode the component value.

### `src/stargazer/assets/variants.py`

| Class | type | component | Properties |
|-------|------|-----------|------------|
| `VariantsFile` | variants | vcf | `sample_id: str`, `caller: str \| None`, `variant_type: str \| None`, `build: str \| None`, `sample_count: int \| None`, `source_samples: list[str] \| None` |
| `VariantsIndex` | variants | index | `sample_id: str` |

**Tests: one test per class verifying property get/set round-trips through keyvalues, `type`/`component` are set on construction, and `update()` only modifies specified keys.**

## Phase 3: Refactor Container Types

Each container becomes thin composition. Remove all `update_*()` methods and property proxies. Keep `fetch()`, `to_dict()`, `from_dict()`.

`fetch()` calls `default_client.download(component)` for each non-None component.

```python
@dataclass
class Reference:
    build: str
    fasta: ReferenceFile | None = None
    faidx: ReferenceIndex | None = None
    sequence_dictionary: SequenceDict | None = None
    aligner_index: list[AlignerIndex] = field(default_factory=list)


@dataclass
class Alignment:
    sample_id: str
    alignment: AlignmentFile | None = None
    index: AlignmentIndex | None = None


@dataclass
class Reads:
    sample_id: str
    r1: R1File | None = None
    r2: R2File | None = None
    read_group: dict[str, str] | None = None

    @property
    def is_paired(self) -> bool:
        return self.r1 is not None and self.r2 is not None


@dataclass
class Variants:
    sample_id: str
    vcf: VariantsFile | None = None
    index: VariantsIndex | None = None
```

Caller access changes (proxy properties removed, go through the component):
- `alignment.is_sorted` -> `alignment.alignment.sorted == "coordinate"`
- `alignment.has_duplicates_marked` -> `alignment.alignment.duplicates_marked`
- `alignment.has_bqsr_applied` -> `alignment.alignment.bqsr_applied`
- `variants.caller` -> `variants.vcf.caller`
- `variants.is_gvcf` -> `variants.vcf.variant_type == "gvcf"`
- `variants.source_samples` -> `variants.vcf.source_samples or [variants.sample_id]`
- `*.path` replaces all old `*.ipfile.local_path` / `*.get_*_path()` patterns

**Update `src/stargazer/assets/__init__.py`** — export all component classes.

## Phase 4: Update Tasks

Every task that called `container.update_*()` calls `component.update(path, **metadata)` instead.

### Pattern (sort_sam as reference example)

Before:
```python
sorted_alignment = Alignment(sample_id=alignment.sample_id)
await sorted_alignment.update_alignment(
    output_bam,
    format="bam",
    is_sorted=(sort_order == "coordinate"),
    duplicates_marked=alignment.has_duplicates_marked,
    bqsr_applied=alignment.has_bqsr_applied,
    tool="gatk_sort_sam",
)
await sorted_alignment.update_index(bam_index)
```

After:
```python
bam = AlignmentFile()
await bam.update(
    output_bam,
    sample_id=alignment.sample_id,
    format="bam",
    sorted="coordinate" if sort_order == "coordinate" else sort_order,
    duplicates_marked=alignment.alignment.duplicates_marked,
    bqsr_applied=alignment.alignment.bqsr_applied,
    tool="gatk_sort_sam",
)
idx = AlignmentIndex()
await idx.update(bam_index, sample_id=alignment.sample_id)
sorted_alignment = Alignment(sample_id=alignment.sample_id, alignment=bam, index=idx)
```

### Tasks to update (working — mechanical migration)

| Task file | Changes |
|-----------|---------|
| `tasks/general/samtools.py` | `ref.update_faidx()` -> `ReferenceIndex().update(path, ...)` |
| `tasks/gatk/create_sequence_dictionary.py` | `ref.update_sequence_dictionary()` -> `SequenceDict().update(path, ...)` |
| `tasks/general/bwa.py` (`bwa_index`) | `ref.update_aligner_index()` -> `AlignerIndex().update(path, ...)` |
| `tasks/general/bwa.py` (`bwa_mem`) | `alignment.update_alignment()` -> `AlignmentFile().update(path, ...)` |
| `tasks/gatk/sort_sam.py` | As shown above |
| `tasks/gatk/mark_duplicates.py` | Same pattern; metrics file stays as raw `default_client.upload()` |
| `tasks/gatk/merge_bam_alignment.py` | Same pattern |
| `tasks/gatk/apply_bqsr.py` | Same pattern |
| `tasks/gatk/base_recalibrator.py` | Raw `default_client.upload()` stays (returns base `ComponentFile`) |
| `tasks/gatk/combine_gvcfs.py` | `variants.update_vcf()` -> `VariantsFile().update(path, ...)` |
| `tasks/gatk/genotype_gvcf.py` | Same; also update `gvcf.source_samples` -> `gvcf.vcf.source_samples` |

### Tasks to rewrite (already broken — reference nonexistent methods)

| Task file | Broken references |
|-----------|------------------|
| `tasks/gatk/variant_recalibrator.py` | `vcf.vcf_name`, `vcf.get_vcf_path()`, `ref.get_ref_path()` |
| `tasks/gatk/apply_vqsr.py` | `vcf.vcf_name`, `vcf.get_vcf_path()`, `ref.get_ref_path()`, `vcf.files`, `variants.add_files()` |
| `tasks/gatk/genomics_db_import.py` | `gvcf.vcf_name`, `gvcf.get_vcf_path()` |

Rewrite to use the component pattern. `vcf.get_vcf_path()` -> `vcf.vcf.path`, `ref.get_ref_path()` -> `ref.fasta.path`.

## Phase 5: Marshal and Serialization

### `marshal.py`
- Swap `IpFile` for `ComponentFile` (and subclasses) in `_FROM_DICT_TYPES`
- `marshal_output` already checks `hasattr(value, "to_dict")` — works automatically

### Serialization tests
- `to_dict()` on containers now nests component dicts (`{"cid": ..., "keyvalues": {...}}`) instead of IpFile dicts
- Typed metadata fields are inside `keyvalues`, not at the top level of the component dict
- `from_dict` on derived classes correctly restores properties via keyvalues

## Implementation Order

1. `base.py` + `tests/unit/test_component_file.py` — foundation
2. Migrate `StorageClient`, `PinataClient`, `LocalStorageClient` to use `ComponentFile`; delete `utils/ipfile.py`
3. Derived component classes in all 4 type modules
4. Container refactoring — strip to composition
5. Task updates — one at a time, run tests after each
6. Marshal + serialization test cleanup

## Files Modified

| File | Action |
|------|--------|
| `src/stargazer/utils/ipfile.py` | **Delete** |
| `src/stargazer/assets/base.py` | **New** — `ComponentFile` base |
| `src/stargazer/utils/storage.py` | Replace `IpFile` with `ComponentFile` |
| `src/stargazer/utils/pinata.py` | Replace `IpFile` with `ComponentFile` |
| `src/stargazer/utils/local_storage.py` | Replace `IpFile` with `ComponentFile` |
| `src/stargazer/assets/reference.py` | Add `ReferenceFile`, `ReferenceIndex`, `SequenceDict`, `AlignerIndex`; strip `Reference` |
| `src/stargazer/assets/alignment.py` | Add `AlignmentFile`, `AlignmentIndex`; strip `Alignment` |
| `src/stargazer/assets/reads.py` | Add `R1File`, `R2File`; strip `Reads` |
| `src/stargazer/assets/variants.py` | Add `VariantsFile`, `VariantsIndex`; strip `Variants` |
| `src/stargazer/assets/__init__.py` | Export new classes |
| `src/stargazer/tasks/general/samtools.py` | Use `ReferenceIndex().update()` |
| `src/stargazer/tasks/general/bwa.py` | Use `AlignerIndex().update()`, `AlignmentFile().update()` |
| `src/stargazer/tasks/gatk/create_sequence_dictionary.py` | Use `SequenceDict().update()` |
| `src/stargazer/tasks/gatk/sort_sam.py` | Use `AlignmentFile().update()`, `AlignmentIndex().update()` |
| `src/stargazer/tasks/gatk/mark_duplicates.py` | Use `AlignmentFile().update()`, `AlignmentIndex().update()` |
| `src/stargazer/tasks/gatk/merge_bam_alignment.py` | Use `AlignmentFile().update()`, `AlignmentIndex().update()` |
| `src/stargazer/tasks/gatk/apply_bqsr.py` | Use `AlignmentFile().update()` |
| `src/stargazer/tasks/gatk/combine_gvcfs.py` | Use `VariantsFile().update()` |
| `src/stargazer/tasks/gatk/genotype_gvcf.py` | Use `VariantsFile().update()` |
| `src/stargazer/tasks/gatk/variant_recalibrator.py` | Rewrite (broken) |
| `src/stargazer/tasks/gatk/apply_vqsr.py` | Rewrite (broken) |
| `src/stargazer/tasks/gatk/genomics_db_import.py` | Rewrite (broken) |
| `src/stargazer/marshal.py` | Swap `IpFile` for `ComponentFile` in `_FROM_DICT_TYPES` |
| `tests/unit/test_component_file.py` | **New** — base class tests |
| `tests/types/test_*.py` | Update for new component types; remove all `IpFile` usage |
| `tests/unit/test_marshal.py` | Update for new serialization shape |

## Verification

```bash
# After each phase:
cd /home/coder/stargazer && uv run pytest tests/unit/ tests/types/ -v

# Full suite after all phases:
cd /home/coder/stargazer && uv run pytest tests/ -v
```
